"""
supermodel_router/openai_routes.py — OpenAI 兼容 API 路由 (v3.2.0 拆分)

- /v1/chat/completions (含 stream + chain rotation v4)
- /v1/images/generations
- /v1/images/edits
- /v1/embeddings
- /v1/models
- /v1/models/{model_id:path}
"""
import json
import time
import asyncio
import logging
from typing import cast, AsyncGenerator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import config
from .engine import proxy_chat_request, proxy_images_generations
from .detector import (
    detect_chat_input_modality,
    detect_chat_output_modality,
    detect_streaming,
    match_modality_for_request,
    detect_image_gen_params,
)
from .classifier import TEXT_ONLY, IMAGE_GEN

LOG = logging.getLogger("openai_routes")
router = APIRouter()

registry = None
engine = None


def init(app_registry, app_engine):
    global registry, engine
    registry = app_registry
    engine = app_engine


# ============================================================
# OpenAI 兼容 API — 任意模态自动路由
# ============================================================

@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI-compatible chat completions — 自动检测输入/输出类型, 按模态路由"""
    body = await request.json()
    requested_model = body.get("model", "auto")
    stream = detect_streaming(body)

    # 鉴权
    api_key = config.server.get("api_key", "")
    if api_key:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != api_key:
            return JSONResponse(
                {"error": {"message": "Invalid API key", "type": "auth_error"}},
                status_code=401,
            )

    # ── v3: 自动检测输入/输出类型 ──
    input_mod = detect_chat_input_modality(body)
    output_mod = detect_chat_output_modality(body)
    preferred_modalities = match_modality_for_request(input_mod, output_mod)

    LOG.debug("request: input=%s output=%s → modalities=%s model=%s",
              input_mod, output_mod, preferred_modalities, requested_model)

    # 路由 (v4: pick_chain traverse — 失败时自动切下一个候选)
    max_retry = config.routing.get("max_retry", 2)
    backoff_ms = config.routing.get("retry_backoff_ms", [0, 500])
    last_error = None
    chain = engine.pick_chain(requested_model, preferred_modalities=preferred_modalities,
                              max_candidates=max(8, max_retry * 4))
    if not chain:
        return JSONResponse(
            {"error": {"message": "No available models", "type": "routing_error"}},
            status_code=503,
        )

    chain_idx = 0
    candidate = chain[0]
    route = candidate.materialize(registry)
    if not route:
        return JSONResponse(
            {"error": {"message": "No available models (materialize failed)", "type": "routing_error"}},
            status_code=503,
        )
    route = route  # type: ignore[assignment]  # LSP narrowing fix

    # v4: traverse 候选链 — 5xx/timeout/429 (短) 自动切下一个候选
    attempts = 0
    while True:
        attempts += 1
        if attempts > max_retry + len(chain):
            # 兜底: 链遍历完仍失败
            return JSONResponse(
                last_error or {"error": {"message": "All candidates exhausted"}},
                status_code=502,
            )

        t0 = time.time()
        try:
            if stream:
                # v4 修复 B6: stream 路径加 pre-flight check
                # 用 GET /models 探针 (已有 key rotation 逻辑) — 失败抛异常让 outer while 切链
                pf_url = f"{route.base_url.rstrip('/')}/models"
                pf_headers = {"Authorization": f"Bearer {route.api_key}"}
                async with httpx.AsyncClient(timeout=5.0) as pf_client:
                    pf_resp = await pf_client.get(pf_url, headers=pf_headers)
                if pf_resp.status_code != 200:
                    raise httpx.HTTPStatusError(
                        f"preflight HTTP {pf_resp.status_code}",
                        request=pf_resp.request, response=pf_resp,
                    )
                # pre-flight OK, 真正 stream
                agen = cast(AsyncGenerator, await proxy_chat_request(route, body, stream=True))
                async def _stream_generator(route=route, t0=t0, candidate=candidate,
                                            chain=chain, chain_idx=chain_idx):
                    try:
                        async for chunk in agen:
                            yield chunk
                        engine.record_success(route.provider_name, time.time() - t0)
                    except Exception as e:
                        engine.record_failure(route.provider_name, route.model_id, 0, str(e))
                        LOG.exception("stream error to %s", route.full_model_path)
                        yield f'data: {json.dumps({"error": str(e)})}\n\n'
                return StreamingResponse(
                    _stream_generator(),
                    media_type="text/event-stream",
                )
            else:
                result = await proxy_chat_request(route, body, stream=False, timeout=300)
                assert isinstance(result, dict), f"expected dict, got {type(result)}"
                latency = time.time() - t0
                if "error" in result:
                    http_code = result.get("error", {}).get("code", 0)
                    error_msg = result.get("error", {}).get("message", "")
                    engine.record_failure(route.provider_name, route.model_id, http_code, error_msg)
                    last_error = result
                    # v4: 切下一个候选 (key 或 model)
                    chain_idx += 1
                    if chain_idx >= len(chain):
                        return JSONResponse(
                            last_error or {"error": {"message": "All candidates exhausted"}},
                            status_code=502,
                        )
                    candidate = chain[chain_idx]
                    route = candidate.materialize(registry)
                    if not route:
                        continue
                    LOG.info("v4 rotate: → %s (key_idx=%d, score=%.1f, penalty=%.2f)",
                             route.full_model_path, candidate.key_index,
                             candidate.score, candidate.penalty)
                    if attempts <= max_retry:
                        ms = backoff_ms[min(attempts - 1, len(backoff_ms) - 1)]
                        if ms > 0:
                            await asyncio.sleep(ms / 1000)
                    continue
                engine.record_success(route.provider_name, latency)
                result["_router"] = {
                    "provider": route.provider_name,
                    "model": route.model_id,
                    "full_path": route.full_model_path,
                    "latency_ms": round(latency * 1000, 1),
                    "input_modality": input_mod,
                    "output_modality": output_mod,
                    "key_index": candidate.key_index,
                    "chain_position": chain_idx,
                    "chain_size": len(chain),
                }
                return JSONResponse(result)
        except httpx.TimeoutException:
            latency = time.time() - t0
            LOG.warning("timeout %s (attempt %d, %.1fs)",
                        route.full_model_path, attempts, latency)
            engine.record_failure(route.provider_name, route.model_id, 0, "timeout")
            last_error = {"error": {"message": "Upstream timeout", "type": "timeout"}}
        except Exception as e:
            LOG.exception("proxy error %s (attempt %d)", route.full_model_path, attempts)
            engine.record_failure(route.provider_name, route.model_id, 0, str(e))
            last_error = {"error": {"message": str(e), "type": "proxy_error"}}

        # 失败 — 切下一个候选
        chain_idx += 1
        if chain_idx >= len(chain):
            return JSONResponse(
                last_error or {"error": {"message": "All candidates exhausted"}},
                status_code=502,
            )
        candidate = chain[chain_idx]
        route = candidate.materialize(registry)
        if not route:
            continue
        LOG.info("v4 rotate (exception): → %s (key_idx=%d)",
                 route.full_model_path, candidate.key_index)
        if attempts <= max_retry:
            ms = backoff_ms[min(attempts - 1, len(backoff_ms) - 1)]
            if ms > 0:
                await asyncio.sleep(ms / 1000)


@router.post("/v1/images/generations")
async def images_generations(request: Request):
    """图像生成 — 自动路由到生图模型分组"""
    body = await request.json()
    params = detect_image_gen_params(body)
    prompt = params["prompt"]
    if not prompt:
        return JSONResponse({"error": {"message": "prompt is required", "type": "invalid_request"}},
                            status_code=400)

    route = engine.pick("auto", preferred_modalities=[IMAGE_GEN])
    if not route:
        return JSONResponse(
            {"error": {"message": "No image generation models available", "type": "routing_error"}},
            status_code=503,
        )

    t0 = time.time()
    try:
        result = await proxy_images_generations(route, body, timeout=120)
        latency = time.time() - t0
        if "error" not in result:
            engine.record_success(route.provider_name, latency)
            result["_router"] = {
                "provider": route.provider_name,
                "model": route.model_id,
                "full_path": route.full_model_path,
                "latency_ms": round(latency * 1000, 1),
            }
            return JSONResponse(result)
        engine.record_failure(route.provider_name, route.model_id)
        return JSONResponse(result, status_code=502)
    except Exception as e:
        engine.record_failure(route.provider_name, route.model_id)
        return JSONResponse({"error": {"message": str(e), "type": "proxy_error"}},
                            status_code=502)


@router.post("/v1/images/edits")
async def images_edits(request: Request):
    """图片编辑 — 路由到生图模型"""
    # 简化: 转发相同
    return await images_generations(request)


@router.post("/v1/embeddings")
async def embeddings(request: Request):
    """文本嵌入 — 路由到 embedding 模型 (没有则 fallback)"""
    body = await request.json()
    requested_model = body.get("model", "auto")

    route = engine.pick(requested_model, preferred_modalities=["embedding", TEXT_ONLY])
    if not route:
        return JSONResponse(
            {"error": {"message": "No embedding models available", "type": "routing_error"}},
            status_code=503,
        )

    headers = {
        "Authorization": f"Bearer {route.api_key}",
        "Content-Type": "application/json",
    }
    payload = {**body, "model": route.model_id}
    url = f"{route.base_url.rstrip('/')}/embeddings"

    t0 = time.time()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=headers, timeout=30)
            latency = time.time() - t0
            if resp.status_code == 200:
                engine.record_success(route.provider_name, latency)
                result = resp.json()
                result["_router"] = {
                    "provider": route.provider_name,
                    "model": route.model_id,
                    "latency_ms": round(latency * 1000, 1),
                }
                return JSONResponse(result)
            engine.record_failure(route.provider_name, route.model_id, resp.status_code, resp.text)
            return JSONResponse(
                {"error": {"message": resp.text[:500], "type": f"http_{resp.status_code}"}},
                status_code=502,
            )
    except Exception as e:
        engine.record_failure(route.provider_name, route.model_id)
        return JSONResponse({"error": {"message": str(e), "type": "proxy_error"}},
                            status_code=502)


# ── 模型列表 (带分类) ──

@router.get("/v1/models")
async def list_models(provider: str | None = None, modality: str | None = None):
    """模型列表, 支持 ?provider= 和 ?modality= 过滤"""
    models = registry.get_models(provider)
    if modality:
        models = [m for m in models if m.modality == modality]

    return JSONResponse({
        "object": "list",
        "data": [
            {
                "id": m.id,
                "object": m.object,
                "created": m.created,
                "owned_by": m.owned_by,
                "provider": m.provider,
                "modality": m.modality,
                "modality_display": m.modality_display,
                "capability_score": m.capability_score,
                **m.extra,
            }
            for m in models
        ],
    })


@router.get("/v1/models/{model_id:path}")
async def get_model(model_id: str):
    models = registry.get_models()
    for m in models:
        if m.id == model_id:
            return JSONResponse({
                "id": m.id,
                "object": m.object,
                "created": m.created,
                "owned_by": m.owned_by,
                "provider": m.provider,
                "modality": m.modality,
                "modality_display": m.modality_display,
                "capability_score": m.capability_score,
            })
    return JSONResponse({"error": "Model not found"}, status_code=404)

