"""
supermodel_router/openai_routes.py — OpenAI 兼容 API 路由 (v3.2.0 拆分)

- /v1/chat/completions (含 stream + chain rotation v4 + context bridge v3.5.0)
- /v1/images/generations
- /v1/images/edits
- /v1/embeddings
- /v1/models
- /v1/models/{model_id:path}

v3.4.0 新增 (2026-06-17):
- 切换模型时, 通过 ContextBridge 注入 system message 让新模型接续对话
- 流式响应: 切到新 candidate 时, 发 SSE sentinel `data: {"_smr_bridge": {...}}` 标记
- 非流式: response._router 加 switched_from + stale + age_seconds
- 整个请求超过 stale_threshold_seconds 才标 stale (默认 30min)

v3.5.0 新增 (2026-06-17 22:25 老大拍):
- smr_request_id 嵌入: 每个请求生成/透传唯一 ID, 嵌到 response._router.smr_request_id
  + chain_id 跨 candidate 一致. mainbot 收 response 时校验错配 → 丢弃
- 切链 race condition 防御 (stream 模式): 切到下一 candidate 时, 显式
  await current_agen.aclose() 关上游 httpx 连接, 防止旧模型的迟缓 reply
  晚到错配新请求 (或飞书侧)
- 主动盘点: body._smr_context_review=true → 调 SMR /v1/admin/context_review
  拿 SwitchRecord 聚合 (v3.5.0)
"""
import json
import time
import asyncio  # v3.28: ModelScope 异步生图轮询用
import logging
import uuid
from typing import Any, cast, AsyncGenerator
from starlette.datastructures import UploadFile  # v3.28: multipart img upload type check

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
from .context_bridge import ContextBridge, SwitchRecord

LOG = logging.getLogger("openai_routes")
router = APIRouter()

registry: Any = None
engine: Any = None
# v3.4.0: 全局 ContextBridge 单例 (app.py 启动时 init 注入)
context_bridge: ContextBridge | None = None


def init(app_registry, app_engine, app_bridge: ContextBridge | None = None):
    global registry, engine, context_bridge
    registry = app_registry
    engine = app_engine
    context_bridge = app_bridge or ContextBridge()


# ============================================================
# OpenAI 兼容 API — 任意模态自动路由
# ============================================================

@router.post("/v1/public/chat/completions")
async def public_chat_completions(request: Request):
    """对外公开 API 端点 — 强制使用 PublicKeyManager 多 key 鉴权

    v3.7.0 落地: 老大拍"中转 router 不对外就丧失核心功能"
    v3.7.1: 简化实现 — 直接复用 chat_completions, 仅在鉴权阶段拒绝非 public key

    与 /v1/chat/completions 区别:
    - 只接受 public key (smr-pub-*), config.server.api_key 单 key 模式不允许
    - 所有请求都进 PublicKeyManager 用量统计
    """
    from .public_api import public_key_manager, PublicKeyManager
    from typing import cast as _cast
    pkm = _cast(PublicKeyManager, public_key_manager)
    auth_header = request.headers.get("Authorization", "")
    bearer = auth_header[7:] if auth_header.startswith("Bearer ") else ""
    public_meta = pkm.authenticate(bearer) if pkm is not None else None

    if public_meta is None:
        return JSONResponse(
            {"error": {"message": "Invalid or missing public API key",
                      "type": "auth_error",
                      "hint": "Get a key from /v1/admin/public-keys (admin only)"}},
            status_code=401,
        )

    # 复用 chat_completions 完整逻辑
    return await chat_completions(request)

@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI-compatible chat completions — 自动检测输入/输出类型, 按模态路由

    v3.4.0: 切换模型时, ContextBridge 注入 system message 同步上下文 + 任务
    v3.5.0: smr_request_id 嵌入 + 切链 abort + 主动盘点
    """
    body = await request.json()
    requested_model = body.get("model", "auto")
    stream = detect_streaming(body)

    # ── v3.5.0: smr_request_id + chain_id (防 race condition 错配) ──
    # 透传优先: mainbot 发的请求已经有 _smr_request_id, 用它的; 否则生成
    # chain_id 默认 = smr_request_id (单一请求一个 chain, 跨 candidate 不变)
    smr_request_id = body.get("_smr_request_id") or str(uuid.uuid4())
    chain_id = body.get("_smr_chain_id") or smr_request_id
    request_start_time = time.time()

    # 鉴权
    # v3.7.0: 多 key 体系 — 先查 public_key_manager (per-tenant), 退到 config.server.api_key (单 key)
    from .public_api import public_key_manager, PublicKeyManager
    from typing import cast as _cast
    pkm = _cast(PublicKeyManager, public_key_manager)
    auth_header = request.headers.get("Authorization", "")
    bearer = auth_header[7:] if auth_header.startswith("Bearer ") else ""
    public_meta = pkm.authenticate(bearer) if pkm is not None else None
    if public_meta is not None:
        # v3.7.0: per-tenant key 鉴权通过
        if not pkm.check_rate_limit(public_meta):
            return JSONResponse(
                {"error": {"message": "Rate limit exceeded", "type": "rate_limit_error",
                          "rpm_limit": public_meta.get("rate_limit_rpm", 0)}},
                status_code=429,
            )
        requested_model = body.get("model", "")
        if not pkm.check_model_filter(public_meta, requested_model):
            return JSONResponse(
                {"error": {"message": f"Model '{requested_model}' not allowed for this key",
                          "type": "model_filter_error"}},
                status_code=403,
            )
        # 标记到 request.state (后续 record_usage 用)
        request.state.public_key_meta = public_meta
        # v3.9.0 (Phase G): 把用户请求的 model 也存进 state, 中间件按 model 分组统计用量
        request.state.requested_model = requested_model
    else:
        # 退到老的单 key 模式
        api_key = config.server.get("api_key", "")
        if api_key:
            if not auth_header.startswith("Bearer ") or bearer != api_key:
                return JSONResponse(
                    {"error": {"message": "Invalid API key", "type": "auth_error"}},
                    status_code=401,
                )

    # ── v3: 自动检测输入/输出类型 ──
    input_mod = detect_chat_input_modality(body)
    output_mod = detect_chat_output_modality(body)
    preferred_modalities = match_modality_for_request(input_mod, output_mod)

    LOG.debug("request: input=%s output=%s → modalities=%s model=%s smr_req_id=%s",
              input_mod, output_mod, preferred_modalities, requested_model, smr_request_id[:8])

    # 路由 (v4: pick_chain traverse — 失败时自动切下一个候选)
    max_retry = config.routing.get("max_retry", 2)
    backoff_ms = config.routing.get("retry_backoff_ms", [0, 500])
    last_error = None
    # v3.9.0 (Phase H): 4 策略轮询 (默认 round-robin-group)
    from .model_groups import get_model_group_manager
    mgm = get_model_group_manager()
    groups = mgm.get_path_to_group_mapping() if mgm else {}
    chain = engine.pick_chain(requested_model, preferred_modalities=preferred_modalities,
                              max_candidates=max(8, max_retry * 4),
                              strategy=config.group_strategy(),
                              groups=groups,
                              group_weights=config.group_weights())
    if not chain:
        return JSONResponse(
            {"error": {"message": "No available models", "type": "routing_error"}},
            status_code=503,
        )

    # v3.4.0: per-request 切换历史 + 请求起始时间
    # v3.5.0: 也注册到 context_bridge (per-request 跟踪 + 主动盘点)
    switch_history: list[SwitchRecord] = []
    current_body = body  # 可能被 ContextBridge 注入改写
    if context_bridge:
        context_bridge.register_request(smr_request_id, {
            "chain_id": chain_id,
            "requested_model": requested_model,
            "stream": stream,
            "request_start_time": request_start_time,
        })

    chain_idx = 0
    candidate = chain[0]
    route = candidate.materialize(registry)
    if not route:
        return JSONResponse(
            {"error": {"message": "No available models (materialize failed)", "type": "routing_error"}},
            status_code=503,
        )
    route = route  # type: ignore[assignment]  # LSP narrowing fix

    def _advance_to_next_chain(failure_status: str, failure_code: int,
                                failure_msg: str, partial_text: str = "") -> tuple | None:
        """切到下一个 candidate + 记录 SwitchRecord + inject body
        返回 (新 candidate, 新 route, 新 body) 或 None (链耗尽)

        v3.5.0: 同步到 context_bridge.per-request tracking
        """
        nonlocal chain_idx, route, candidate, current_body, switch_history
        if chain_idx + 1 >= len(chain):
            return None
        # 记录这次失败
        rec = SwitchRecord(
            from_provider=route.provider_name,
            from_model=route.model_id,
            from_full_path=route.full_model_path,
            partial_text=partial_text,
            switch_time=time.time(),
            request_start_time=request_start_time,
            response_status=failure_status,
            http_code=failure_code,
            error_message=failure_msg[:500],
            attempt_index=chain_idx,
        )
        switch_history.append(rec)
        if context_bridge:
            context_bridge.record_switch(rec)  # 全局 stats
            context_bridge.append_switch_to_request(smr_request_id, rec)  # v3.5.0 per-request
        # 切链
        chain_idx += 1
        candidate = chain[chain_idx]
        new_route = candidate.materialize(registry)
        if not new_route:
            return None
        route = new_route  # type: ignore[assignment]
        # v3.4.0: inject 上下文到下一 candidate 的 body
        if context_bridge and context_bridge.enabled and switch_history:
            current_body = context_bridge.inject_into_body(current_body, switch_history)
        # v3.8.0: 按下一 candidate 的 context_window 压缩 body
        if (context_bridge and context_bridge.compress_on_switch
                and candidate.context_window > 0):
            before_tokens = context_bridge.estimate_tokens(current_body)
            compressed = context_bridge.compress_for_target(
                current_body, candidate.context_window, before_tokens
            )
            if compressed is not current_body:  # 真发生了压缩
                current_body = compressed
                LOG.info("v3.8.0 compress on switch: → %s (target=%d, before=%d, after=%d, meta=%s)",
                         route.full_model_path, candidate.context_window,
                         before_tokens, context_bridge.estimate_tokens(current_body),
                         current_body.get("_smr_compress", {}))
        LOG.info("v4 rotate (bridge): → %s (key_idx=%d, attempt=%d/%d, history=%d, smr_req_id=%s)",
                 route.full_model_path, candidate.key_index,
                 chain_idx + 1, len(chain), len(switch_history), smr_request_id[:8])
        return (candidate, route, current_body)

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
                # v3.4.0: 流式支持链切换
                # v3.5.0: 切到下一 candidate 时, 显式 abort 上游 httpx (race condition 防御)
                # 切到下一 candidate 时, 累积 partial_text + 发 sentinel
                async def _stream_generator():
                    nonlocal chain_idx, route, candidate, switch_history, current_body
                    accumulated_text = ""
                    current_agen: AsyncGenerator | None = None  # v3.5.0: 跟踪当前上游 gen
                    while True:
                        # v3.5.0: 切链时 abort 旧 gen (关上游 httpx 连接)
                        if current_agen is not None and context_bridge and context_bridge.abort_on_switch:
                            try:
                                await current_agen.aclose()
                                context_bridge.record_abort()
                                LOG.info("v3.5.0 aborted upstream httpx on switch: smr_req_id=%s", smr_request_id[:8])
                            except Exception as ae:
                                LOG.warning("aclose() failed (best-effort): %s", ae)
                        agen = cast(AsyncGenerator, await proxy_chat_request(route, current_body, stream=True))
                        current_agen = agen  # v3.5.0: 跟踪
                        try:
                            # v3.4.0: 如果是切到的新 candidate, 在第一个 chunk 前发 sentinel
                            is_continuation = chain_idx > 0 and bool(switch_history)
                            sent_sentinel = False
                            async for chunk in agen:
                                if is_continuation and not sent_sentinel:
                                    if context_bridge:
                                        sentinel = context_bridge.build_sse_sentinel(switch_history)
                                        if sentinel:
                                            yield sentinel
                                            context_bridge.record_sentinel_sent()
                                sent_sentinel = True
                                # 累积 chunk text (粗略提取 delta content)
                                if chunk.startswith("data: ") and chunk.endswith("\n\n"):
                                    payload = chunk[6:].strip()
                                    if payload and payload != "[DONE]":
                                        try:
                                            obj = json.loads(payload)
                                            delta = obj.get("choices", [{}])[0].get("delta", {})
                                            content = delta.get("content", "")
                                            if content:
                                                accumulated_text += content
                                        except Exception:
                                            pass
                                yield chunk
                            # 流成功结束
                            engine.record_success(route.provider_name, time.time() - t0)
                            # v3.5.0: 流式 response 末尾发 _router meta chunk
                            # 注: OpenAI 流协议无 _router 字段, 我们用 chunk 形式发出
                            if context_bridge:
                                bridge_meta = context_bridge.build_switched_from_metadata(switch_history)
                                if bridge_meta:
                                    router_chunk = {
                                        "id": f"smr-finalize-{smr_request_id[:8]}",
                                        "object": "chat.completion.chunk",
                                        "created": int(time.time()),
                                        "model": route.model_id,
                                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                                        "_smr_router": {
                                            "smr_request_id": smr_request_id,  # v3.5.0
                                            "chain_id": chain_id,  # v3.5.0
                                            "provider": route.provider_name,
                                            "model": route.model_id,
                                            "full_path": route.full_model_path,
                                            "latency_ms": round((time.time() - request_start_time) * 1000, 1),
                                            "chain_position": chain_idx,
                                            "chain_size": len(chain),
                                            "request_age_seconds": int(time.time() - request_start_time),
                                            **bridge_meta,
                                        },
                                    }
                                    yield f"data: {json.dumps(router_chunk, ensure_ascii=False)}\n\n"
                            return
                        except Exception as e:
                            LOG.warning("stream error to %s: %s (chain_idx=%d, history=%d)",
                                        route.full_model_path, e, chain_idx, len(switch_history))
                            engine.record_failure(route.provider_name, route.model_id, 0, str(e))
                            # v3.4.0: 切到下一 candidate
                            advance = _advance_to_next_chain(
                                failure_status="stream_error",
                                failure_code=0,
                                failure_msg=f"stream interrupted: {e}",
                                partial_text=accumulated_text,
                            )
                            if advance is None:
                                # 链耗尽
                                yield f'data: {json.dumps({"error": str(e)})}\n\n'
                                return
                            # 继续 while True 循环, 切到下一个 candidate 续流
                            continue
                return StreamingResponse(
                    _stream_generator(),
                    media_type="text/event-stream",
                )
            else:
                result = await proxy_chat_request(route, current_body, stream=False, timeout=300)
                assert isinstance(result, dict), f"expected dict, got {type(result)}"
                latency = time.time() - t0
                if "error" in result:
                    http_code = result.get("error", {}).get("code", 0)
                    error_msg = result.get("error", {}).get("message", "")
                    engine.record_failure(route.provider_name, route.model_id, http_code, error_msg)
                    last_error = result
                    # v3.4.0: 切下一个 candidate, 记录 switch + inject
                    advance = _advance_to_next_chain(
                        failure_status=f"http_{http_code}" if http_code else "proxy_error",
                        failure_code=http_code,
                        failure_msg=error_msg,
                    )
                    if advance is None:
                        return JSONResponse(
                            last_error or {"error": {"message": "All candidates exhausted"}},
                            status_code=502,
                        )
                    # 等待 backoff
                    if attempts <= max_retry:
                        ms = backoff_ms[min(attempts - 1, len(backoff_ms) - 1)]
                        if ms > 0:
                            await asyncio.sleep(ms / 1000)
                    continue
                # 成功
                engine.record_success(route.provider_name, latency)
                router_meta = {
                    "smr_request_id": smr_request_id,  # v3.5.0: 错配检测
                    "chain_id": chain_id,  # v3.5.0: 跨 candidate 一致
                    "provider": route.provider_name,
                    "model": route.model_id,
                    "full_path": route.full_model_path,
                    "latency_ms": round(latency * 1000, 1),
                    "input_modality": input_mod,
                    "output_modality": output_mod,
                    "key_index": candidate.key_index,
                    "chain_position": chain_idx,
                    "chain_size": len(chain),
                    "request_age_seconds": int(time.time() - request_start_time),
                }
                # v3.4.0: 切换历史 + 过期标记
                if context_bridge and switch_history:
                    bridge_meta = context_bridge.build_switched_from_metadata(switch_history)
                    router_meta.update(bridge_meta)
                result["_router"] = router_meta
                return JSONResponse(result)
        except httpx.TimeoutException:
            latency = time.time() - t0
            LOG.warning("timeout %s (attempt %d, %.1fs)",
                        route.full_model_path, attempts, latency)
            engine.record_failure(route.provider_name, route.model_id, 0, "timeout")
            last_error = {"error": {"message": "Upstream timeout", "type": "timeout"}}
            # v3.4.0: 切链 + 记录
            advance = _advance_to_next_chain(
                failure_status="timeout",
                failure_code=0,
                failure_msg=f"upstream timeout after {latency:.1f}s",
            )
            if advance is None:
                return JSONResponse(
                    last_error or {"error": {"message": "All candidates exhausted"}},
                    status_code=502,
                )
        except Exception as e:
            LOG.exception("proxy error %s (attempt %d)", route.full_model_path, attempts)
            engine.record_failure(route.provider_name, route.model_id, 0, str(e))
            last_error = {"error": {"message": str(e), "type": "proxy_error"}}
            # v3.4.0: 切链 + 记录
            advance = _advance_to_next_chain(
                failure_status="exception",
                failure_code=0,
                failure_msg=str(e),
            )
            if advance is None:
                return JSONResponse(
                    last_error or {"error": {"message": "All candidates exhausted"}},
                    status_code=502,
                )

        # 失败 — 等待 backoff
        if attempts <= max_retry:
            ms = backoff_ms[min(attempts - 1, len(backoff_ms) - 1)]
            if ms > 0:
                await asyncio.sleep(ms / 1000)


@router.post("/v1/images/generations")
async def images_generations(request: Request):
    """图像生成 — 自动路由到生图模型分组

    v3.28: 加 image 字段支持 (img2img / 图生图)
    - body.image = str URL or {"url": "..."} 或 {"base64": "..."}
    - 检测到 image → 视为 img2img, 传给 provider 用 image_url 字段
    - 没 image → 走 text2img 流程
    """
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
    else:
        # multipart/form-data: prompt 在表单, image 文件在文件字段
        form = await request.form()
        body = {}
        for k, v in form.items():
            if k == "image" and isinstance(v, UploadFile):
                body[k] = await v.read()  # UploadFile → bytes
            else:
                body[k] = v
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

    # v3.28: img2img 时, 把 image 字段转成 image_url (ModelScope Qwen-Image-Edit 标准格式)
    # 注意: ModelScope 不接受 chat messages 格式, 必须是 image_url 字段
    if "image" in body and body["image"] is not None:
        img_val = body["image"]
        if isinstance(img_val, dict):
            img_url_or_b64 = img_val.get("url") or img_val.get("base64") or img_val.get("b64_json")
        elif isinstance(img_val, (bytes, bytearray)):
            # multipart 上传 → base64 data URI
            import base64
            img_url_or_b64 = f"data:image/png;base64,{base64.b64encode(img_val).decode()}"
        else:
            img_url_or_b64 = img_val
        if img_url_or_b64:
            body["image_url"] = img_url_or_b64
            body.pop("image", None)
            # chat messages 模式不要 (ModelScope 不支持)

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
    """图片编辑 — 路由到生图模型 (multipart image upload)
    v3.28: 真接 multipart/form-data + forward to engine"""
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
