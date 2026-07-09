"""
supermodel_router/app.py — FastAPI 主服务 v3 (模态感知路由)
"""
import json
import time
import logging
import asyncio
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List, cast

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .config import config
from .models import ModelRegistry
from .engine import RouteEngine, proxy_chat_request, proxy_images_generations
from .detector import (
    detect_chat_input_modality,
    detect_chat_output_modality,
    detect_streaming,
    match_modality_for_request,
    detect_image_gen_params,
)
from .classifier import (
    TEXT_ONLY, MULTIMODAL, IMAGE_GEN, VIDEO_GEN, AUDIO_GEN,
    get_modality_display,
)
from .context_bridge import ContextBridge  # v3.4.0
from .loop_engine import LoopEngine, get_loop_engine  # v3.22.0
from .memory_bus import MemoryBus  # v3.22.0
from .scheduler import TaskScheduler  # v3.22.0

LOG = logging.getLogger("app")


def _refresh_async(registry, *, tag: str = "refresh"):
    """后台线程跑 refresh_all,fire-and-forget。

    POST provider 时不让 httpx.ConnectError 阻塞响应。
    daemon=True → SMR 退出时 thread 自动终止,不卡进程。
    try/except → daemon thread 异常默认被吞,这里显式记 LOG。
    """
    def _runner():
        try:
            t0 = time.time()
            registry.refresh_all()
            LOG.info("[%s] async refresh done in %.2fs", tag, time.time() - t0)
        except Exception:
            LOG.exception("[%s] async refresh failed", tag)

    th = threading.Thread(target=_runner, daemon=True, name=f"smr-{tag}")
    th.start()
    LOG.info("[%s] async refresh kicked off", tag)

# ---- 全局对象 ----
registry: ModelRegistry = None
engine: RouteEngine = None
model_manager: Any = None
# v3.4.0: 上下文桥接 + 过期标记引擎
context_bridge: ContextBridge | None = None
# v3.22.0: 周天循环系统
memory_bus: MemoryBus = None
loop_engine: LoopEngine = None
scheduler: TaskScheduler = None
_start_time: float = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global registry, engine, _start_time, memory_bus, loop_engine, scheduler, context_bridge, model_manager
    _start_time = time.time()

    registry = ModelRegistry(config)
    registry.build()
    registry.refresh_all()
    # v3.10.0 (Phase I): refresh 后 merge metadata 到 ModelInfo
    from .model_metadata import get_model_metadata_store
    _mms = get_model_metadata_store()
    if _mms is not None:
        all_models = registry.get_models()  # List[ModelInfo]
        merged = _mms.merge_bulk(all_models)
        LOG.info("Phase I: merged metadata into %d models (BUG-005 fallback active)", merged)
    engine = RouteEngine(config, registry)

    # v3.4.0: ContextBridge — 上下文桥接 + 过期标记 (老大 22:00 拍)
    bridge_cfg = config.data.get("context_bridge", {}) or {}
    context_bridge = ContextBridge(bridge_cfg)
    LOG.info("ContextBridge v%s initialized: enabled=%s threshold=%ds max_history=%d",
             context_bridge.version, context_bridge.enabled,
             context_bridge.stale_threshold_s, context_bridge.max_history)

    # v3.3: ModelManager — 模型管理 (Discovery + Notifier + Lists + AutoRules)
    from .model_manager import ModelManager
    model_manager = ModelManager(config, registry)
    # v3.3: model_manager 注册到 refresh 完成回调 (覆盖 periodic_refresh + admin API 全部 refresh_all 调用)
    registry.register_refresh_callback(model_manager.on_refresh)

    # v3.9.0: ModelGroupManager 同步 registry model 列表 (跨 provider 解析)
    # manager 必须在首次 _sync_mgm() 前初始化；否则 startup refresh 已完成但
    # known_models 仍为空，/v1/admin/model-groups 会返回 stale/错误 model_count。
    from .model_groups import init_model_group_manager, get_model_group_manager
    state_dir = config.data.get("model_management", {}).get("state_dir", ".")
    init_model_group_manager(state_dir=state_dir)
    LOG.info("ModelGroupManager v3.9.0 initialized (state_dir=%s)", state_dir)

    def _sync_mgm():
        try:
            mgm = get_model_group_manager()
            if mgm is None:
                return
            provider_models: Dict[str, List[str]] = {}
            for pname, ps in registry._providers.items():
                provider_models[pname] = [m.id for m in ps.models]
            mgm.set_known_models(provider_models)
            LOG.info("ModelGroupManager: synced %d providers / %d total models",
                     len(provider_models), sum(len(v) for v in provider_models.values()))
        except Exception:
            LOG.exception("ModelGroupManager sync failed")
    registry.register_refresh_callback(_sync_mgm)
    # startup refresh 已经完成；manager 初始化后必须立即同步一次。
    _sync_mgm()

    config.start_watcher()
    config.on_change(lambda data: model_manager.on_config_reload())

    # 定期刷新模型列表 (10min)
    refresh_interval = 600
    async def _periodic_refresh():
        while True:
            await asyncio.sleep(refresh_interval)
            try:
                registry.refresh_all()
                # v3.10.0 (Phase I): periodic refresh 后也 merge metadata
                from .model_metadata import get_model_metadata_store
                _mms = get_model_metadata_store()
                if _mms is not None:
                    _mms.merge_bulk(registry.get_models())
            except Exception:
                LOG.exception("periodic refresh failed")
    asyncio.create_task(_periodic_refresh())

    # v4: 定期 model penalty 衰减 (复测恢复分数)
    decay_interval = config.routing.get("recovery_interval", 300)
    async def _periodic_penalty_decay():
        while True:
            await asyncio.sleep(decay_interval)
            try:
                updated = engine.decay_model_penalty()
                if updated:
                    LOG.info("periodic penalty decay: %d models updated", updated)
            except Exception:
                LOG.exception("periodic penalty decay failed")
    asyncio.create_task(_periodic_penalty_decay())

    # v4.1: 启动时加载本地版本元数据 (v3.1)
    try:
        from .version import load_version_meta
        version_meta = load_version_meta()
        LOG.info("SMR v%s (%s) started: %d models across %d providers",
                 version_meta["version"], version_meta["build_date"],
                 len(registry.get_model_ids()), len(registry._providers))
    except Exception:
        LOG.info("Model Router v3 started: %d models across %d providers",
                 len(registry.get_model_ids()), len(registry._providers))

    # v3.2.0: 把 registry/engine/_start_time 注入到子路由模块 (on_event deprecated)
    # v3.4.0: 同时注入 context_bridge
    # v3.22.0: 注入 memory_bus / loop_engine / scheduler
    openai_init(registry, engine, context_bridge)
    admin_api_init(registry, engine, model_manager, _start_time, context_bridge,
                   memory_bus=memory_bus, loop_engine=loop_engine, scheduler=scheduler,
                   fastapi_app=app, app_state_dir=state_dir)
    # v3.7.0: 对外 API 多 key 管理 (per-tenant)
    from .public_api import init_public_key_manager
    init_public_key_manager(state_dir=state_dir)

    # v3.10.0 (Phase I): Model Metadata Store (quality/speed/reasoning/tags 元数据)
    from .model_metadata import init_model_metadata_store, get_model_metadata_store
    init_model_metadata_store(state_dir=state_dir)
    LOG.info("ModelMetadataStore v3.10.0 initialized (state_dir=%s)", state_dir)

    # v3.15.0: ModelHealthManager — 健康度管理 + 背景恢复检测 (老大 2026-06-24 钦定)
    from .model_health import init_model_health_manager, get_model_health_manager
    mh_cfg = config.data.get("model_health", {}) or {}
    init_model_health_manager(state_dir=state_dir, config=mh_cfg)
    mhm = get_model_health_manager()

    # 注入 engine (pick_chain 路由前 filter, record_* 联动)
    engine.model_health = mhm

    # probe 函数: 用 HEAD /models 验证 provider 端点可达 (不消耗 token 配额)
    async def _probe_model(path: str) -> bool:
        """轻量 probe: HEAD base_url/v1/models (不真发 key)

        返回: True = provider 端点活着 (200/401/403/404 都算 OK)
              False = 端点不可达 / 网络错误 / 超时
        """
        try:
            provider_name = path.split("/", 1)[0] if "/" in path else path
            ps = registry._providers.get(provider_name)
            if not ps:
                return False
            base_url = getattr(ps, "base_url", None) or ""
            if not base_url:
                return False
            timeout = float(mhm.cfg["probe_timeout_seconds"])
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.get(f"{base_url.rstrip('/')}/models", timeout=timeout)
                # 200 = OK, 401/403 = provider 活着但 key 失效 (仍算 OK, 路由时再换 key), 5xx/network = fail
                return r.status_code < 500
        except Exception as e:
            LOG.debug("probe_model %s failed: %s", path, e)
            return False

    mhm.set_probe_func(_probe_model)

    # v3.16.0: provider 自动禁用 callback (后台每 10min 扫 1 次, 全 SKIP 持续 ≥ 7 天 → 禁用)
    def _provider_disable_scan():
        if not mhm:
            return []
        try:
            candidates = mhm.check_provider_disable_candidates(registry)
        except Exception as e:
            LOG.warning("check_provider_disable_candidates failed: %s", e)
            return []
        disabled = []
        for c in candidates:
            try:
                if config.disable_provider(c["provider"], c["reason"]):
                    disabled.append(c["provider"])
                    LOG.warning("model_health: provider auto-disabled: %s (reason=%s, duration=%.1fd)",
                                c["provider"], c["reason"][:80], c["duration_seconds"] / 86400)
            except Exception as e:
                LOG.warning("disable_provider(%s) failed: %s", c["provider"], e)
        return disabled

    mhm.set_provider_disable_callback(_provider_disable_scan)

    # 启动 background checker (loop 启动后才能 set_event)
    loop = asyncio.get_running_loop()
    mhm.start_background_checker(loop)
    LOG.info("ModelHealthManager v3.15.0 initialized (state_dir=%s, skip_threshold=%d, probe_interval=%ds)",
             state_dir, mhm.cfg["consecutive_fails_skip"], mhm.cfg["probe_interval_seconds"])

    # ── v3.23.0: L1 Free Resource (老大钦定重点 6/27) ─────────────────────
    from .free_models import init_free_model_registry, get_free_model_registry
    free_reg = init_free_model_registry(
        providers=config.providers,
        state_dir=state_dir,
    )
    # 初次扫描 (从已加载的 registry)
    try:
        provider_models = {}
        for pname, ps in registry._providers.items():
            provider_models[pname] = [m.id for m in ps.models]
        free_reg.refresh(models_by_provider=provider_models)
        LOG.info("FreeModelRegistry: 初次识别 %d free models", free_reg.count())
    except Exception as e:
        LOG.warning("FreeModelRegistry 初次扫描失败: %s", e)
    # 注入到 engine
    engine.free_registry = free_reg

    # ── v3.22.0: 周天循环系统 ──────────────────────────────────────────
    memory_bus = MemoryBus(state_dir=state_dir)
    LOG.info("MemoryBus v3.22.0 initialized (state_dir=%s)", state_dir)

    loop_engine = get_loop_engine(
        memory_bus=memory_bus,
        health_manager=mhm,
        state_dir=state_dir,
        tick_interval=config.data.get("loop_engine", {}).get("tick_interval", 300),
    )
    loop_engine.start()  # sync — uses create_task internally
    LOG.info("LoopEngine v3.22.0 started (interval=%ds)", loop_engine.tick_interval)

    # ── v3.22.0: Scheduler (优雅降级 + 并行聚合 + 中间模型) ────────────
    from .maker_checker import MakerCheckerEngine
    maker_checker = MakerCheckerEngine(
        model_health=mhm,
        strategy=config.data.get("maker_checker", {}).get("strategy", "flat"),
        memory_bus=memory_bus,
    )

    # adapter: engine.proxy_chat_request(route, body) → simple (provider, model_id, messages) proxy
    async def _engine_proxy(provider: str, model_id: str, messages: list, **kwargs) -> dict:
        from .engine import proxy_chat_request, RouteResult
        # build a minimal RouteResult for the engine
        ps = registry._providers.get(provider)
        base_url = ps.base_url if ps else ""
        api_key = (ps.api_keys[0] if ps and ps.api_keys else "") if ps else ""
        route = RouteResult(
            provider_name=provider, model_id=model_id, base_url=base_url, api_key=api_key,
            full_model_path=f"{provider}/{model_id}",
        )
        body = {"messages": messages, **kwargs}
        raw = await proxy_chat_request(route, body, stream=False)
        # normalize to expected {text, latency_ms, ...} shape
        text = ""
        if isinstance(raw, dict):
            choices = raw.get("choices", [])
            if choices and isinstance(choices[0], dict):
                text = choices[0].get("message", {}).get("content", "")
        return {"text": text, "raw": raw}

    scheduler = TaskScheduler(
        engine_proxy=_engine_proxy,
        maker_checker=maker_checker,
        memory_bus=memory_bus,
    )
    LOG.info("TaskScheduler v3.22.0 initialized (strategy=%s)", maker_checker.maker.strategy)

    # ── hot-reload: config 变动通知 LoopEngine ─────────────────────────
    config.on_change(lambda data: LOG.info("config hot-reload: loop/scheduler config may need update"))

    yield
    # 关闭周天循环
    try:
        loop_engine.stop()
    except Exception:
        LOG.exception("loop_engine stop failed")
    # 关闭 background checker
    try:
        mhm.stop_background_checker()
    except Exception:
        pass
    config.stop_watcher()


# v3.1 — 实际版本从 version.py 读
try:
    from .version import VERSION as SMR_VERSION
    from .version import BUILD_DATE as SMR_BUILD_DATE
    SMR_APP_TITLE = f"SuperModel Router v{SMR_VERSION}"
except Exception:
    SMR_VERSION = "3.28.0"
    SMR_BUILD_DATE = "2026-07-04"
    SMR_APP_TITLE = "SuperModel Router v3.28.0"

app = FastAPI(title=SMR_APP_TITLE, version=SMR_VERSION, lifespan=lifespan)


# ============================================================
# v3.7.1 (P0 BUG-001 fix): Public API 用量追踪 middleware
# 拦截所有响应, 自动从 request.state.public_key_meta 读取 public_key 元数据
# 根据 HTTP status 判断 success/fail, 自动调 pkm.record_usage()
# 流式响应也能追踪 (status 200 = success)
# ============================================================
@app.middleware("http")
async def public_usage_middleware(request: Request, call_next):
    response = await call_next(request)
    try:
        from .public_api import public_key_manager, PublicKeyManager as _PKM
        from typing import cast as _cast
        pkm = _cast(_PKM, public_key_manager)
        meta = getattr(request.state, 'public_key_meta', None)
        if pkm is not None and meta is not None:
            success = 200 <= response.status_code < 400
            # 流式响应无法直接读 body 计 tokens, 暂时按 status 计数
            tokens = 0
            # v3.9.0 (Phase G): 读 request.state.requested_model (per-tenant key 鉴权时存进去的), 缺省 None
            model_name = getattr(request.state, "requested_model", None)
            pkm.record_usage(meta["key_hash"], success=success, tokens=tokens, model_name=model_name)
    except Exception as e:
        LOG.warning("public_usage_middleware: %s", e)
    return response



# ---- v3.2.0: 拆分后装载 3 个子路由 ----
from .openai_routes import router as openai_router, init as openai_init
from .admin_ui import router as admin_ui_router
from .admin_api import router as admin_api_router, init as admin_api_init


app.include_router(openai_router)
app.include_router(admin_ui_router)
app.include_router(admin_api_router)

# ---- v3.8.1: mount /design 端点, 暴露 SMR-design.html 设计文档 ----
# 让老大和 reviewer 通过 http://localhost:6473/design 看完整功能设计
# build-time 由 scripts/sync_design_to_admin.py 同步 docs/SMR-design.html → /app/docs/SMR-design.html
import os
from fastapi.responses import FileResponse
from pathlib import Path

_DESIGN_HTML_CANDIDATES = [
    Path("/app/docs/SMR-design.html"),                          # docker 部署 (build-time sync 目标)
    Path(__file__).parent / "static" / "SMR-design.html",       # dev 模式 (build-time sync 默认目标)
    Path(__file__).parent.parent / "docs" / "SMR-design.html",  # 仓库 docs/ 源文件 (兜底)
]


@app.get("/design", include_in_schema=False)
@app.get("/design/", include_in_schema=False)
async def smr_design_page():
    """v3.8.1: 暴露 docs/SMR-design.html 设计文档
    路径探测顺序: /app/docs/ → 仓库 docs/
    """
    for p in _DESIGN_HTML_CANDIDATES:
        if p.exists():
            return FileResponse(p, media_type="text/html",
                                headers={"Cache-Control": "public, max-age=3600"})
    return Response(
        content="<h1>Design doc not found</h1><p>Expected at: "
                + "<br>".join(str(p) for p in _DESIGN_HTML_CANDIDATES)
                + "</p>",
        status_code=404,
        media_type="text/html",
    )


@app.get("/v1/admin/design", include_in_schema=False)
async def smr_design_meta():
    """v3.8.1: 设计文档元数据 (供 admin UI 校验是否同步)"""
    for p in _DESIGN_HTML_CANDIDATES:
        if p.exists():
            return {
                "ok": True,
                "path": str(p),
                "size_bytes": p.stat().st_size,
                "mtime": p.stat().st_mtime,
            }
    return {"ok": False, "candidates": [str(p) for p in _DESIGN_HTML_CANDIDATES]}
