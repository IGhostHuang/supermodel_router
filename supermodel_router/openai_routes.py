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
    smr_request_id = body.get("_smr_request_id") or str(uuid.uuid4())
    chain_id = body.get("_smr_chain_id") or smr_request_id
    request_start_time = time.time()

    # v0.5.6: bare "agent" or "agent:auto" should reach the agent branch.
    # Normalize: if requested_model == "agent", rewrite to "agent:auto" so the
    # agent dispatcher sees agent_mode="auto" and re-routes via fast/moa/hybrid.
    # (Otherwise SMR engine.pick_chain routes it to a generic provider and fails.)
    if isinstance(requested_model, str) and requested_model == "agent":
        requested_model = "agent:auto"
        LOG.info("agent_normalize: bare 'agent' -> 'agent:auto'")

    # Step 20: fusion dispatch. body.model starts with "fusion:<plan_id>" -> use FusionRouter
    if isinstance(requested_model, str) and requested_model.startswith("fusion:"):
        plan_id = requested_model.split(":", 1)[1].strip()
        try:
            from .fusion_router import get_fusion_router, run_plan_streaming
            from starlette.responses import JSONResponse as _JSONResp
            fr = get_fusion_router()
            if fr is None:
                raise RuntimeError("FusionRouter not initialized")
            if not fr.has_plan(plan_id):
                raise KeyError(f"unknown fusion plan '{plan_id}'. available: {fr.list_plans()}")
            msgs = body.get("messages") or []
            last_user = next((m["content"] for m in reversed(msgs) if m.get("role") == "user"), "")
            history_msgs = [m for m in msgs if m.get("role") in ("system", "user", "assistant")]
            history = history_msgs[:-1] if history_msgs else []

            # v4-stream: SSE streaming mode
            if stream:
                LOG.info("span_start=fusion_stream smr_request_id=%s plan=%s chars=%d",
                         smr_request_id, plan_id, len(last_user))
                return StreamingResponse(
                    run_plan_streaming(fr, plan_id, last_user, history, smr_request_id),
                    media_type="text/event-stream",
                )

            # non-streaming mode (original)
            LOG.info("span_start=fusion smr_request_id=%s plan=%s chars=%d",
                     smr_request_id, plan_id, len(last_user))
            import time as _time
            _t0 = _time.time()
            result = await fr.run_plan(plan_id, last_user, history)
            LOG.info("span_end=fusion smr_request_id=%s plan=%s elapsed=%.2fs trace_steps=%d",
                     smr_request_id, plan_id, result.elapsed_seconds, len(result.trace))
            return _JSONResp({
                "id": f"fusion-{smr_request_id[:8]}",
                "object": "chat.completion",
                "created": int(_time.time()),
                "model": requested_model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": result.answer},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": result.total_tokens_in,
                    "completion_tokens": result.total_tokens_out,
                    "total_tokens": result.total_tokens_in + result.total_tokens_out,
                },
                "fusion_trace": result.trace,
            })
        except KeyError as e:
            from starlette.responses import JSONResponse as _JSONResp
            return _JSONResp({"error": {"message": str(e), "type": "fusion_plan_error"}}, status_code=400)
        except Exception as e:
            from starlette.responses import JSONResponse as _JSONResp
            LOG.exception("fusion_dispatch_failed")
            return _JSONResp({"error": {"message": f"fusion failed: {e!r}", "type": "fusion_error"}}, status_code=500)


    # v0.5.0: agent dispatch. body.model starts with "agent:<plan_id>" -> use AgentLoop
    if isinstance(requested_model, str) and requested_model.startswith("agent:"):
        try:
            from starlette.responses import JSONResponse as _JSONResp2
            from .agent_loop import get_agent_loop
            # v0.5.5: bare "agent" -> "" so the auto-dispatcher fires
            # "agent:xxx" -> "xxx"  (manual mode selection)
            agent_mode = requested_model.split(":", 1)[1].strip()
            loop = get_agent_loop()
            if loop is None:
                raise RuntimeError("AgentLoop not initialized")
            msgs = body.get("messages") or []
            last_user = next((m["content"] for m in reversed(msgs) if m.get("role") == "user"), "")
            history_msgs = [m for m in msgs if m.get("role") in ("system", "user", "assistant")]
            history = history_msgs[:-1] if history_msgs else []

            LOG.info("span_start=agent smr_request_id=%s mode=%s chars=%d",
                     smr_request_id, agent_mode, len(last_user))
            import time as _time
            _t0 = _time.time()
            if agent_mode == "" or agent_mode == "auto":
                # v0.5.5: agent (no suffix) -> auto-dispatch based on query
                # Heuristic:
                #   - has tool verbs (读/写/执行/搜索/获取) -> hybrid (need tools)
                #   - short query (<30 chars) -> fast (single call)
                #   - long + complex keywords (分析/对比/写代码/总结/翻译) -> moa
                #   - default -> fast (fastest path)
                _tool_keywords = ("读", "写", "执行", "运行", "搜索", "查找",
                                  "获取", "删除", "创建", "发送", "抓取",
                                  "file_read", "file_write", "run_command",
                                  "http_get", "echo_message", "list_directory")
                _quality_keywords = ("分析", "对比", "写代码", "总结", "翻译",
                                     "设计", "plan", "analyze", "compare",
                                     "summary", "translate", "implement")
                _has_tool = any(kw in last_user for kw in _tool_keywords)
                _wants_quality = any(kw in last_user for kw in _quality_keywords)
                _short = len(last_user) < 30
                if _has_tool:
                    chosen = "hybrid"
                elif _short:
                    chosen = "fast"
                elif _wants_quality:
                    chosen = "moa"
                else:
                    chosen = "fast"
                LOG.info("agent auto-dispatch: query_len=%d has_tool=%s wants_quality=%s -> %s",
                         len(last_user), _has_tool, _wants_quality, chosen)
                agent_mode = chosen
                # Fall through to chosen branch by re-routing via if/elif chain

            if agent_mode == "moa":
                from .moa_selector import get_moa_selector, run_moa
                from .quality_gate import QualityGate
                sel = get_moa_selector()
                cfg = sel.select(last_user, history)
                qg = QualityGate({"min_score": 0.0})
                async def invoke(model, messages):
                    # Hit SMR's own /v1/chat/completions so engine.pick_chain
                    # handles routing/fallback properly with auth headers.
                    import logging
                    _log = logging.getLogger("moa_invoke")
                    _log.info("invoke: model=%s", model)
                    import httpx as _httpx
                    # Pick an available auth header (try Bearer with known keys)
                    auth = None
                    try:
                        from .config import CONFIG
                        srv_keys = CONFIG.get("server", {}).get("api_keys", [])
                        if srv_keys:
                            auth = f"Bearer {srv_keys[0]}"
                    except Exception:
                        pass
                    headers = {"Content-Type": "application/json"}
                    if auth:
                        headers["Authorization"] = auth
                    async with _httpx.AsyncClient(timeout=180) as client:
                        try:
                            r = await client.post(
                                "http://127.0.0.1:6473/v1/chat/completions",
                                headers=headers,
                                json={"model": model, "messages": messages,
                                      "max_tokens": 4000, "stream": False},
                            )
                            _log.info("invoke: status=%d", r.status_code)
                            try:
                                d = r.json()
                                if r.status_code >= 400:
                                    return {"error": d.get("error", {}).get("message", str(d)[:200])}
                                # Extract OpenAI-format response into MOA format
                                choice = (d.get("choices") or [{}])[0]
                                msg = choice.get("message", {})
                                content = msg.get("content") or ""
                                reasoning = msg.get("reasoning_content") or ""
                                return {
                                    "content": content,
                                    "reasoning_content": reasoning,
                                    "model": d.get("model", model),
                                    "usage": d.get("usage", {}),
                                }
                            except Exception:
                                return {"error": f"non_json: {r.text[:200]}"}
                        except Exception as e:
                            _log.exception("invoke: error")
                            return {"error": f"http_failed: {e!r}"}
                async def scorer(prompt, content, model):
                    res = await qg.assess(prompt, content, [])
                    return res.score
                moa_res = await run_moa(cfg, last_user, history, invoke, scorer)
                answer = moa_res.best_answer
                agent_trace = [{"mode": "moa", "complexity": moa_res.complexity,
                                "strategy": moa_res.strategy, "best_model": moa_res.best_model,
                                "best_score": moa_res.best_score,
                                "all_outputs": [(m, s) for m, _, s in moa_res.all_outputs],
                                "duration_ms": moa_res.duration_ms}]
            elif agent_mode == "hybrid":
                # agent:hybrid = MOA 多模型投票 + 完整 ReAct + 工具调用
                # 每一步 LLM 决策用 MOA (3 模型投票), 工具调用走 agent_loop
                from .moa_selector import get_moa_selector, run_moa
                from .quality_gate import QualityGate

                sel = get_moa_selector()
                qg = QualityGate({"min_score": 0.0})

                # Build hybrid moa_invoke — hits SMR engine.pick_chain
                # (auto-fallback to qwythos-9b if freellmapi exhausted).
                async def moa_invoke(model_full_id, messages):
                    import logging
                    _log = logging.getLogger("hybrid_invoke")
                    _log.info("hybrid invoke: model=%s", model_full_id)
                    import httpx as _httpx
                    headers = {"Content-Type": "application/json"}
                    try:
                        from .config import CONFIG
                        srv_keys = CONFIG.get("server", {}).get("api_keys", [])
                        if srv_keys:
                            headers["Authorization"] = f"Bearer {srv_keys[0]}"
                    except Exception:
                        pass
                    async with _httpx.AsyncClient(timeout=180) as client:
                        try:
                            r = await client.post(
                                "http://127.0.0.1:6473/v1/chat/completions",
                                headers=headers,
                                json={"model": model_full_id,
                                      "messages": messages,
                                      "max_tokens": 4000, "stream": False},
                            )
                            d = r.json()
                            if r.status_code >= 400:
                                return {"error": d.get("error", {}).get("message", str(d)[:200])}
                            choice = (d.get("choices") or [{}])[0]
                            msg = choice.get("message", {})
                            content = msg.get("content") or ""
                            reasoning = msg.get("reasoning_content") or ""
                            return {
                                "content": content,
                                "reasoning_content": reasoning,
                                "model": d.get("model", model_full_id),
                                "usage": d.get("usage", {}),
                            }
                        except Exception as e:
                            return {"error": f"http_failed: {e!r}"}

                async def hybrid_scorer(prompt, content, model):
                    res = await qg.assess(prompt, content, [])
                    return res.score

                # ---- Phase 1: Plan via MOA (2 fast models propose plan, vote best) ----
                # v0.5.5: speed up — 2 fast-tier models instead of 3 (quality
                # preserved because both are top-of-fast pool, not degraded).
                plan_prompt = (
                    "你是一位顶尖的项目规划师。用户任务: " + last_user + "\n\n"
                    "请输出一个清晰的执行计划（最多 5 步），每步格式: "
                    "[编号] 动作: 描述\n\n"
                    "不要调用工具，纯文本计划即可。"
                )
                # v0.5.5: plan = single fast call (no MOA vote).
                # Saves 30-60s vs 2-model MOA which often retries on 429.
                plan_cfg = sel.select(last_user, history, force_n_models=1,
                                      explicit_preference="fast")
                LOG.info("hybrid phase=plan smr_request_id=%s n_models=1 complexity=%d",
                         smr_request_id, plan_cfg.complexity)
                plan_moa = await run_moa(plan_cfg, plan_prompt, history,
                                         moa_invoke, hybrid_scorer)
                plan_text = plan_moa.best_answer
                LOG.info("hybrid plan_best_model=%s score=%.3f duration=%dms",
                         plan_moa.best_model, plan_moa.best_score, plan_moa.duration_ms)

                # ---- Phase 2: Execute plan via agent_loop (tool use) ----
                enriched_msg = (
                    last_user + "\n\n"
                    "[MOA-generated plan, best_model=" + plan_moa.best_model +
                    " score=" + f"{plan_moa.best_score:.2f}]\n" + plan_text
                )
                result = await loop.run(enriched_msg, history)
                exec_answer = result.answer
                exec_trace = result.trace

                # ---- Phase 3: Final answer synthesis via MOA ----
                obs_lines = []
                for t in exec_trace[:8]:
                    if isinstance(t, dict):
                        desc = t.get("description", "")
                        action = t.get("action")
                        tr = t.get("tool_result")
                        obs = ""
                        if isinstance(tr, dict):
                            obs = (tr.get("output_preview") or "")[:200]
                        obs_lines.append(
                            f"- step: {desc}; action: {action}; result_preview: {obs}"
                        )
                synth_prompt = (
                    "用户任务: " + last_user + "\n\n"
                    "执行轨迹 (共 " + str(len(exec_trace)) + " 步):\n"
                    + "\n".join(obs_lines) +
                    "\n\n请综合上述执行结果,给用户一个完整、准确、简洁的回答。"
                    "如果某个步骤失败了,请说明并给出可执行的替代方案。"
                )
                synth_cfg = sel.select(synth_prompt, history, force_n_models=1,
                                         explicit_preference="fast")
                LOG.info("hybrid phase=synth smr_request_id=%s n_models=1 complexity=%d",
                         smr_request_id, synth_cfg.complexity)
                synth_moa = await run_moa(synth_cfg, synth_prompt, history,
                                          moa_invoke, hybrid_scorer)
                final_answer = synth_moa.best_answer or exec_answer

                # ---- Aggregate trace for transparency ----
                agent_trace = [
                    {"phase": "plan_moa",
                     "complexity": plan_moa.complexity,
                     "best_model": plan_moa.best_model,
                     "best_score": plan_moa.best_score,
                     "duration_ms": plan_moa.duration_ms,
                     "plan_text": plan_text,
                     "all_outputs": [(m, s) for m, _, s in plan_moa.all_outputs]},
                ] + exec_trace + [
                    {"phase": "synth_moa",
                     "complexity": synth_moa.complexity,
                     "best_model": synth_moa.best_model,
                     "best_score": synth_moa.best_score,
                     "duration_ms": synth_moa.duration_ms,
                     "all_outputs": [(m, s) for m, _, s in synth_moa.all_outputs]},
                ]
                answer = final_answer
                usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

            elif agent_mode == "fast":
                # v0.5.4: agent:fast = single call via SMR engine (uses _bare
                # model id "deepseek-v4-flash" which auto-falls-back to local
                # qwythos-9b when freellmapi is rate-limited).
                # Designed for connectivity tests: 5-15s response.
                import httpx as _httpx
                fast_messages = []
                for m in (body.get("messages") or []):
                    role = m.get("role")
                    c_text = m.get("content", "")
                    if role in ("user", "system", "assistant"):
                        fast_messages.append({"role": role, "content": c_text})
                if not fast_messages:
                    fast_messages = [{"role": "user", "content": last_user}]
                # Hit SMR's own endpoint (engine has local key configured)
                fast_url = "http://127.0.0.1:6473/v1/chat/completions"
                LOG.info("agent:fast smr_request_id=%s msgs=%d", smr_request_id, len(fast_messages))
                fast_payload = {
                    "model": "deepseek-v4-flash",  # auto-fallback to local qwythos-9b
                    "messages": fast_messages,
                    "max_tokens": 4000, "stream": False,
                }
                headers = {"Content-Type": "application/json"}
                # Local self-call: no auth needed (loopback)
                async with _httpx.AsyncClient(timeout=60) as client:
                    try:
                        r = await client.post(fast_url, json=fast_payload, headers=headers)
                        if r.status_code >= 400:
                            raise RuntimeError(f"smr {r.status_code}: {r.text[:200]}")
                        d = r.json()
                        c = (d.get("choices") or [{}])[0].get("message", {})
                        answer = (c.get("content") or c.get("reasoning_content") or "").strip()
                        if not answer:
                            raise RuntimeError("empty content")
                        usage = d.get("usage", {}) or {}
                        agent_trace = [{
                            "mode": "fast",
                            "model": d.get("model", "auto"),
                            "duration_ms": int((_time.time() - _t0) * 1000),
                            "usage": usage,
                            "elapsed_s": _time.time() - _t0,
                            "request_model": "deepseek-v4-flash",
                        }]
                    except Exception as e:
                        LOG.error("agent:fast failed: %s", e)
                        answer = ("[agent:fast fallback] " + str(e)[:200])
                        agent_trace = [{"mode": "fast", "error": str(e)[:200]}]

            else:
                # agent:auto -> full ReAct loop with tools
                result = await loop.run(last_user, history)
                answer = result.answer
                usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                agent_trace = result.trace

            LOG.info("span_end=agent smr_request_id=%s elapsed=%.2fs steps=%d",
                     smr_request_id, _time.time() - _t0, len(agent_trace))

            # v0.5.2: SSE streaming support for agent:* modes (ChatBox / OpenAI
            # clients default to stream=true). Wrap answer as one SSE chunk.
            import json as _json
            from starlette.responses import StreamingResponse as _SSE
            resp_body = {
                "id": f"agent-{smr_request_id[:8]}",
                "object": "chat.completion",
                "created": int(_time.time()),
                "model": requested_model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "agent_trace": agent_trace,
            }
            if stream:
                # SSE format: data: <json>\n\n + final [DONE]
                chunk = {
                    "id": resp_body["id"],
                    "object": "chat.completion.chunk",
                    "created": resp_body["created"],
                    "model": requested_model,
                    "choices": [{"index": 0, "delta": {"role": "assistant", "content": answer},
                                 "finish_reason": "stop"}],
                }
                async def _sse_gen():
                    yield f"data: {_json.dumps(chunk, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                return _SSE(_sse_gen(), media_type="text/event-stream")
            return _JSONResp2(resp_body)
        except Exception as e:
            from starlette.responses import JSONResponse as _JSONResp2
            LOG.exception("agent_dispatch_failed")
            return _JSONResp2({"error": {"message": f"agent failed: {e!r}", "type": "agent_error"}}, status_code=500)


    # v3.32.0 九字真言'行' span 追踪 (跨模块 grep 目标: span_start=api_entry / span_end=api_entry)
    LOG.info("span_start=api_entry smr_request_id=%s chain=%s model=%s stream=%s",
             smr_request_id, chain_id, str(requested_model)[:40], stream)

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
        LOG.info("span_end=api_entry smr_request_id=%s status=no_chain total_ms=%.0f chain_pos=0/0",
                 smr_request_id, (time.time() - request_start_time) * 1000)
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
        LOG.info("span_end=api_entry smr_request_id=%s status=no_route total_ms=%.0f chain_pos=0/%d",
                 smr_request_id, (time.time() - request_start_time) * 1000, len(chain))
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

    def _log_api_entry_end(status: str, chain_pos: int = 0, chain_len: int = 0) -> None:
        """v3.32.1 补丁: 统一 api_entry span_end 出口 (支持 6 return 分支)
        chain_pos/chain_len 默认 0 处理 no-chain 分支 (chain_idx 未定义时)"""
        LOG.info("span_end=api_entry smr_request_id=%s status=%s total_ms=%.0f chain_pos=%d/%d",
                 smr_request_id, status,
                 (time.time() - request_start_time) * 1000,
                 chain_pos, chain_len)

    while True:
        attempts += 1
        if attempts > max_retry + len(chain):
            # 兜底: 链遍历完仍失败
            _log_api_entry_end("exhausted")
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
                # v3.32.0 span: proxy 调用前
                LOG.info("span_start=proxy_call smr_request_id=%s path=%s attempt=%d",
                         smr_request_id, route.full_model_path, attempts)
                result = await proxy_chat_request(route, current_body, stream=False, timeout=300)
                assert isinstance(result, dict), f"expected dict, got {type(result)}"
                latency = time.time() - t0
                if "error" in result:
                    http_code = result.get("error", {}).get("code", 0)
                    error_msg = result.get("error", {}).get("message", "")
                    engine.record_failure(route.provider_name, route.model_id, http_code, error_msg)
                    # v3.32.0 span: proxy 失败结束
                    LOG.warning("span_end=proxy_call smr_request_id=%s path=%s status=error http=%s latency_ms=%.0f",
                                smr_request_id, route.full_model_path, http_code, latency * 1000)
                    last_error = result
                    # v3.4.0: 切下一个 candidate, 记录 switch + inject
                    advance = _advance_to_next_chain(
                        failure_status=f"http_{http_code}" if http_code else "proxy_error",
                        failure_code=http_code,
                        failure_msg=error_msg,
                    )
                    if advance is None:
                        _log_api_entry_end("exhausted", chain_idx + 1, len(chain))
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
                # v3.32.0 span: proxy 成功结束 + api_entry 全链路结束
                LOG.info("span_end=proxy_call smr_request_id=%s path=%s status=ok latency_ms=%.0f",
                         smr_request_id, route.full_model_path, latency * 1000)
                LOG.info("span_end=api_entry smr_request_id=%s status=ok total_ms=%.0f chain_pos=%d/%d",
                         smr_request_id, (time.time() - request_start_time) * 1000, chain_idx + 1, len(chain))
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
            # v3.32.0 span: proxy timeout 结束
            LOG.warning("span_end=proxy_call smr_request_id=%s path=%s status=timeout latency_ms=%.0f",
                        smr_request_id, route.full_model_path, latency * 1000)
            engine.record_failure(route.provider_name, route.model_id, 0, "timeout")
            last_error = {"error": {"message": "Upstream timeout", "type": "timeout"}}
            # v3.4.0: 切链 + 记录
            advance = _advance_to_next_chain(
                failure_status="timeout",
                failure_code=0,
                failure_msg=f"upstream timeout after {latency:.1f}s",
            )
            if advance is None:
                _log_api_entry_end("exhausted", chain_idx + 1, len(chain))
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
                _log_api_entry_end("exhausted", chain_idx + 1, len(chain))
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

    data_list = [
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
    ]

    # v4.3.5: Add fusion plans to model list (for TRAE Work / OpenAI client compatibility)
    try:
        from .fusion_router import get_fusion_router as _gfr
        _fr = _gfr()
        if _fr and _fr.plans:
            import time as _t
            _now = int(_t.time())
            for _pid, _pcfg in _fr.plans.items():
                _model_id = f"fusion:{_pid}"
                if any(d["id"] == _model_id for d in data_list):
                    continue
                data_list.append({
                    "id": _model_id,
                    "object": "model",
                    "created": _now,
                    "owned_by": "fusion",
                    "provider": "fusion",
                    "modality": "text-only",
                    "modality_display": "Fusion",
                    "capability_score": 90,
                    "description": _pcfg.get("name", _pid),
                })
    except Exception:
        pass

    # v0.5.3: Register agent:* modes so OpenAI clients (ChatBox etc.) can
    # validate them via GET /v1/models/{model_id} before sending chat requests.
    import time as _t2
    _now = int(_t2.time())
    _agent_modes = [
        ("agent", "🤖 自动选择（推荐）：根据 query 自动选 fast/moa/auto/hybrid"),
        ("agent:fast", "⚡ 单次 LLM 调用（5-15 秒，无工具）"),
        ("agent:moa", "MOA 多模型投票（无工具，12-60 秒）"),
        ("agent:auto", "ReAct 完整 agent + 工具调用（单 LLM 决策，30-60 秒）"),
        ("agent:hybrid", "MOA×2 + ReAct（最高质量，加速后 15-50 秒）"),
    ]
    for _mid, _desc in _agent_modes:
        if any(d["id"] == _mid for d in data_list):
            continue
        data_list.append({
            "id": _mid,
            "object": "model",
            "created": _now,
            "owned_by": "agent",
            "provider": "agent",
            "modality": "text-only",
            "modality_display": "🤖 Agent",
            "capability_score": 88,
            "description": _desc,
        })

    return JSONResponse({"object": "list", "data": data_list})


@router.get("/v1/models/{model_id:path}")
async def get_model(model_id: str):
    # v0.5.3: Handle agent:* mode lookups
    if model_id.startswith("agent:"):
        import time as _t3
        _now = int(_t3.time())
        _desc_map = {
            "agent": "🤖 自动选择（推荐）：根据 query 自动选 fast/moa/auto/hybrid",
            "agent:fast": "⚡ 单次 LLM 调用（5-15 秒，无工具）",
            "agent:moa": "MOA 多模型投票（无工具，12-60 秒）",
            "agent:auto": "ReAct 完整 agent + 工具调用（单 LLM 决策，30-60 秒）",
            "agent:hybrid": "MOA×2 + ReAct（最高质量，加速后 15-50 秒）",
        }
        _desc = _desc_map.get(model_id, f"Agent mode: {model_id}")
        return JSONResponse({
            "id": model_id,
            "object": "model",
            "created": _now,
            "owned_by": "agent",
            "provider": "agent",
            "modality": "text-only",
            "modality_display": "🤖 Agent",
            "capability_score": 88,
            "description": _desc,
        })
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
