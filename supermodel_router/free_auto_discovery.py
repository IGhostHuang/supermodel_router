"""
free_auto_discovery.py -- in-process scheduler that drives L1/L2/L3 discovery.

Born from the user's directive that "daily free-model auto-discovery must live
inside the project, not as a foreign cron job". Everything runs as part of
the SMR application lifecycle:

  * startup   -> run_once() once after app boot (best-effort)
  * daily     -> background asyncio task that fires every FREE_DISCOVERY_INTERVAL_S
  * admin API -> ad-hoc trigger via /admin/discovery/run
  * shutdown  -> loop cancels cleanly

Output artifacts (all under state_dir/):
  * discovery/l1_models.json
  * discovery/l2_platforms.json
  * discovery/validated_models.json  (L3 verified -> file-backed cache)
  * discovery/discovery_state.json   (last_run, next_run, history)

We deliberately keep this in-process (no Docker-only crond, no host cron) so
the discovery cadence tracks the app's heartbeat. Engine status and routing
weights are re-evaluated automatically because FreeModelRegistry is the same
object that engine.free_registry holds.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .model_discovery import ModelDiscovery, DiscoveredModel
from .platform_scanner import PlatformScanner
from .discovery_pipeline import DiscoveryPipeline
from .free_models import get_free_model_registry

LOG = logging.getLogger(__name__)


@dataclass
class DiscoveryRunResult:
    started_at: float
    finished_at: float = 0.0
    l1_count: int = 0
    l2_count: int = 0
    l3_verified: int = 0
    l3_new_in_registry: int = 0
    error: Optional[str] = None
    success: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": round(self.finished_at - self.started_at, 3)
            if self.finished_at
            else 0,
            "l1_count": self.l1_count,
            "l2_count": self.l2_count,
            "l3_verified": self.l3_verified,
            "l3_new_in_registry": self.l3_new_in_registry,
            "error": self.error,
            "success": self.success,
        }


class FreeAutoDiscovery:
    """In-process orchestrator for L1 + L2 + L3 free-model discovery.

    Lifetime:
        discovery = FreeAutoDiscovery(state_dir, interval_seconds=86400)
        await discovery.start()      # schedules background loop
        await discovery.run_once()   # synchronous run, used at boot
        await discovery.stop()
    """

    DEFAULT_INTERVAL = 86400  # daily
    DISCOVERY_SUBDIR = "discovery"

    def __init__(self, state_dir: str, interval_seconds: int = DEFAULT_INTERVAL):
        self.state_dir = Path(state_dir)
        self.discovery_dir = self.state_dir / self.DISCOVERY_SUBDIR
        self.discovery_dir.mkdir(parents=True, exist_ok=True)
        self.interval_seconds = max(60, int(interval_seconds))  # floor 1min

        self._pipeline = DiscoveryPipeline(state_dir=str(self.state_dir))
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._history: List[DiscoveryRunResult] = []
        self._state_file = self.discovery_dir / "discovery_state.json"

        # restore history if present
        if self._state_file.exists():
            try:
                blob = json.loads(self._state_file.read_text())
                self._history = [
                    DiscoveryRunResult(**{k: v for k, v in item.items() if k in DiscoveryRunResult.__dataclass_fields__})
                    for item in blob.get("history", [])[:20]
                ]
            except Exception as e:  # pragma: no cover
                LOG.warning("FreeAutoDiscovery: state restore failed: %s", e)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._task and not self._task.done():
            LOG.warning("FreeAutoDiscovery: already started")
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="free-auto-discovery")
        LOG.info(
            "FreeAutoDiscovery: scheduled, interval=%ds, state_dir=%s",
            self.interval_seconds,
            self.discovery_dir,
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.TimeoutError:
                self._task.cancel()
            except Exception:
                pass
        self._persist_history()
        LOG.info("FreeAutoDiscovery: stopped")

    async def _loop(self) -> None:
        """Background loop that runs discovery on a fixed cadence."""
        # Wait one full interval before first scheduled run; run_once() is
        # called separately at boot by app.py so users see a populated set
        # of free models on first request.
        try:
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.interval_seconds
                    )
                except asyncio.TimeoutError:
                    # tick -> run again
                    try:
                        await self.run_once()
                    except Exception as e:  # pragma: no cover
                        LOG.error("FreeAutoDiscovery scheduled run failed: %s", e)
        finally:
            self._persist_history()

    # ------------------------------------------------------------------
    # public run API
    # ------------------------------------------------------------------
    async def run_once(self, *, force_full_scan: bool = False) -> DiscoveryRunResult:
        """Execute one full L1 + L2 + L3 cycle.

        Returns a DiscoveryRunResult; result is also persisted to history.
        New L3-verified free models are merged into the in-process
        FreeModelRegistry (same object engine.free_registry points at), so
        routing weights pick them up immediately without restart.
        """
        result = DiscoveryRunResult(started_at=time.time())
        try:
            LOG.info("FreeAutoDiscovery: run_once starting (force_full_scan=%s)", force_full_scan)

            # L1: poll known platforms (model_discovery)
            models: List[DiscoveredModel] = await self._pipeline.run_l1(force_full=force_full_scan)
            result.l1_count = len(models)
            LOG.info("FreeAutoDiscovery: L1 returned %d candidate models", result.l1_count)

            # L2: scan communities (platform_scanner) for new platforms
            new_platforms = await self._pipeline.run_l2()
            result.l2_count = len(new_platforms)
            LOG.info("FreeAutoDiscovery: L2 surfaced %d new platforms", result.l2_count)

            # L3: verify + integrate
            integrated = await self._pipeline.run_l3(models, new_platforms)
            result.l3_verified = len(integrated)
            LOG.info("FreeAutoDiscovery: L3 verified %d models", result.l3_verified)

            # Merge into runtime registry
            registry = get_free_model_registry()
            new_in_reg = 0
            if registry is not None:
                before = registry.count()
                for entry in integrated:
                    # entry is a dict {provider, model_id, tier, ...}
                    full_path = f"{entry['provider']}/{entry['model_id']}"
                    if registry.get(full_path) is None:
                        # cheap register via public API (uses same schema as initial scan)
                        registry._register_runtime_discovered(entry)
                        new_in_reg += 1
                after = registry.count()
                new_in_reg = max(0, after - before)
                if new_in_reg:
                    registry.save_state()
            result.l3_new_in_registry = new_in_reg

            result.success = True
            result.finished_at = time.time()
            LOG.info(
                "FreeAutoDiscovery: run_once finished, l1=%d l2=%d l3_verified=%d new_in_registry=%d duration=%.2fs",
                result.l1_count,
                result.l2_count,
                result.l3_verified,
                result.l3_new_in_registry,
                result.finished_at - result.started_at,
            )
        except Exception as e:
            LOG.exception("FreeAutoDiscovery: run_once failed")
            result.error = repr(e)
            result.finished_at = time.time()

        self._history.append(result)
        if len(self._history) > 20:
            self._history = self._history[-20:]
        self._persist_history()
        return result

    # ------------------------------------------------------------------
    # observability
    # ------------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        last = self._history[-1] if self._history else None
        return {
            "interval_seconds": self.interval_seconds,
            "state_dir": str(self.discovery_dir),
            "running": bool(self._task and not self._task.done()),
            "last_run": last.to_dict() if last else None,
            "history_size": len(self._history),
            "next_run_in_seconds": self.interval_seconds if last else 0,
        }

    def get_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self._history[-limit:]]

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------
    def _persist_history(self) -> None:
        try:
            self._state_file.write_text(
                json.dumps(
                    {"history": [r.to_dict() for r in self._history]},
                    indent=2,
                )
            )
        except Exception as e:  # pragma: no cover
            LOG.warning("FreeAutoDiscovery: persist history failed: %s", e)


# ----------------------------------------------------------------------
# module-level singleton helpers (mirror free_models.py pattern)
# ----------------------------------------------------------------------
_auto: Optional[FreeAutoDiscovery] = None


def init_free_auto_discovery(
    state_dir: str, interval_seconds: int = FreeAutoDiscovery.DEFAULT_INTERVAL
) -> FreeAutoDiscovery:
    global _auto
    if _auto is None:
        _auto = FreeAutoDiscovery(state_dir=state_dir, interval_seconds=interval_seconds)
        LOG.info(
            "FreeAutoDiscovery: singleton initialized (interval=%ds)", interval_seconds
        )
    return _auto


def get_free_auto_discovery() -> Optional[FreeAutoDiscovery]:
    return _auto