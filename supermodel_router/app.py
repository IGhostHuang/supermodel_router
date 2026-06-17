"""
supermodel_router/app.py — FastAPI 主服务 v3 (模态感知路由)
"""
import json
import time
import logging
import asyncio
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, cast

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
_start_time: float = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global registry, engine, _start_time
    _start_time = time.time()

    registry = ModelRegistry(config)
    registry.build()
    registry.refresh_all()
    engine = RouteEngine(config, registry)

    # v3.4.0: ContextBridge — 上下文桥接 + 过期标记 (老大 22:00 拍)
    global context_bridge
    bridge_cfg = config.data.get("context_bridge", {}) or {}
    context_bridge = ContextBridge(bridge_cfg)
    LOG.info("ContextBridge v%s initialized: enabled=%s threshold=%ds max_history=%d",
             context_bridge.version, context_bridge.enabled,
             context_bridge.stale_threshold_s, context_bridge.max_history)

    # v3.3: ModelManager — 模型管理 (Discovery + Notifier + Lists + AutoRules)
    from .model_manager import ModelManager
    global model_manager
    model_manager = ModelManager(config, registry)
    # v3.3: model_manager 注册到 refresh 完成回调 (覆盖 periodic_refresh + admin API 全部 refresh_all 调用)
    registry.register_refresh_callback(model_manager.on_refresh)

    config.start_watcher()
    config.on_change(lambda data: model_manager.on_config_reload())

    # 定期刷新模型列表 (10min)
    refresh_interval = 600
    async def _periodic_refresh():
        while True:
            await asyncio.sleep(refresh_interval)
            try:
                registry.refresh_all()
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
    openai_init(registry, engine, context_bridge)
    admin_api_init(registry, engine, model_manager, _start_time, context_bridge)

    yield
    config.stop_watcher()


# v3.1 — 实际版本从 version.py 读
try:
    from .version import VERSION as SMR_VERSION
    from .version import BUILD_DATE as SMR_BUILD_DATE
    SMR_APP_TITLE = f"SuperModel Router v{SMR_VERSION}"
except Exception:
    SMR_VERSION = "3.4.0"
    SMR_BUILD_DATE = "2026-06-17"
    SMR_APP_TITLE = "SuperModel Router v3.4.0"

app = FastAPI(title=SMR_APP_TITLE, version=SMR_VERSION, lifespan=lifespan)



# ---- v3.2.0: 拆分后装载 3 个子路由 ----
from .openai_routes import router as openai_router, init as openai_init
from .admin_ui import router as admin_ui_router
from .admin_api import router as admin_api_router, init as admin_api_init


app.include_router(openai_router)
app.include_router(admin_ui_router)
app.include_router(admin_api_router)
