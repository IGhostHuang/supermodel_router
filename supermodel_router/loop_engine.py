"""
supermodel_router/loop_engine.py — 周天循环 Loop Engine (v3.21.0)

功能:
  1. 每 5 分钟 tick 执行:
     - MemoryBus.tick() (L2\u2192L3 rotate + persist)
     - ModelHealthManager.decay_model_penalty()
     - ProviderQuotaManager (future) reset daily counters
  2. 自发信息日志 (tick success/failure)
  3. 在 FastAPI lifespan 启动, 使用 asyncio.create_task
  4. 将 tick 结果写入 hot-cache/loop_engine_tick_<timestamp>.json (debug)

说明:
  - 当 immediate shutdown (调用 app.shutdown) 时, 自动取消 task, 不影同下一次 tick.
  - 目前具体实现不依赖 ProviderQuotaManager, 只调用 ModelHealthManager.decay_model_penalty().
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from .model_health import ModelHealthManager
from .memory_bus import MemoryBus

LOG = logging.getLogger("loop_engine")

DEFAULT_TICK_INTERVAL = 300  # 5 min seconds

class LoopEngine:
    """Loop Engine runs periodic background tasks for SMR.

    - memory_bus: MemoryBus instance (for route experience)
    - health_manager: ModelHealthManager instance
    - tick_interval: seconds (default 5 min)
    """

    def __init__(self,
                 memory_bus: MemoryBus,
                 health_manager: ModelHealthManager,
                 state_dir: str = ".",
                 tick_interval: int = DEFAULT_TICK_INTERVAL):
        self.memory_bus = memory_bus
        self.health_manager = health_manager
        self.state_dir = Path(state_dir)
        self.tick_interval = max(10, tick_interval)  # min 10s safety
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    async def _tick(self) -> dict:
        """One tick: rotate memory, decay penalties, persist status.
        Returns dict for logging / debug file.
        """
        start = time.time()
        LOG.info("loop_engine tick start")
        mem_res = self.memory_bus.tick()
        self.health_manager.decay_model_penalty()
        # future: provider quota daily reset (not implemented yet)
        elapsed = time.time() - start
        LOG.info("loop_engine tick finished in %.2fs", elapsed)
        return {
            "timestamp": time.time(),
            "duration_s": elapsed,
            "memory": mem_res,
            # placeholder for future health stats
        }

    async def _run(self):
        LOG.info("loop_engine background task started (interval=%ds)", self.tick_interval)
        while not self._stop.is_set():
            try:
                data = await self._tick()
                # write debug json file
                ts = int(data["timestamp"])
                fp = self.state_dir / f"loop_engine_tick_{ts}.json"
                try:
                    fp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
                except Exception:
                    LOG.exception("loop_engine: failed to write debug tick file %s", fp)
            except Exception:
                LOG.exception("loop_engine tick error")
            # wait next interval (respect stop)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick_interval)
            except asyncio.TimeoutError:
                continue
        LOG.info("loop_engine background task stopped")

    def start(self) -> None:
        if self._task is not None:
            LOG.warning("loop_engine already started")
            return
        self._task = asyncio.create_task(self._run(), name="loop_engine")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        await self._task
        self._task = None
        self._stop.clear()

    # for manual trigger (e.g. admin API) -------------------------------------------------
    async def trigger(self) -> dict:
        return await self._tick()

# Helper to get or create singleton LoopEngine (used in app.py)
_engine_instance: Optional[LoopEngine] = None

def get_loop_engine(memory_bus: MemoryBus, health_manager: ModelHealthManager,
                    state_dir: str = ".", tick_interval: int = DEFAULT_TICK_INTERVAL) -> LoopEngine:
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = LoopEngine(memory_bus, health_manager, state_dir, tick_interval)
    return _engine_instance
