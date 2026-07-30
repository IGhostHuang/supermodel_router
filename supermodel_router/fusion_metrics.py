"""fusion_metrics.py — in-memory per-plan statistics for n1_fusion.

v4.3.1: add disk persistence to /app/state/fusion_metrics.json
  - load() on startup (called from main app)
  - save_async() debounced to 2s after each record
  - state dir configurable via env SMR_STATE_DIR
"""
import os
import time
import threading
import json
import logging
from collections import deque
from typing import Any, Dict, Optional, List

LOG = logging.getLogger("fusion_metrics")

LOG.info("fusion_metrics loaded (v4.3.1 with persistence)")


def _percentile(values, p):
    if not values:
        return 0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))
    return int(s[k])


def _state_dir() -> str:
    """State directory. Env override; default /app/state."""
    return os.environ.get("SMR_STATE_DIR") or os.path.join(
        os.environ.get("APP_HOME", "/app"), "state"
    )


_STATE_FILE = os.path.join(_state_dir(), "fusion_metrics.json")


class _PlanBucket:
    __slots__ = (
        "calls", "success", "fail", "total_tokens_in", "total_tokens_out",
        "fallback_uses", "fanout_used_fallback", "refiner_used_fallback",
        "stage_latencies", "last_calls",
    )

    def __init__(self):
        self.calls = 0
        self.success = 0
        self.fail = 0
        self.total_tokens_in = 0
        self.total_tokens_out = 0
        self.fallback_uses = 0
        self.fanout_used_fallback = 0
        self.refiner_used_fallback = 0
        self.stage_latencies = {
            "refine_task": [], "fanout": [],
            "refine_answers": [], "final_fuse": [],
        }
        self.last_calls = deque(maxlen=50)

    def record(self, *, success, tokens_in=0, tokens_out=0,
               fallback_uses=0, fanout_used_fallback=0, refiner_used_fallback=0,
               stage_latencies=None, trace_summary=None):
        self.calls += 1
        if success:
            self.success += 1
        else:
            self.fail += 1
        self.total_tokens_in += tokens_in
        self.total_tokens_out += tokens_out
        self.fallback_uses += fallback_uses
        self.fanout_used_fallback += fanout_used_fallback
        self.refiner_used_fallback += refiner_used_fallback
        if stage_latencies:
            for k, v in stage_latencies.items():
                if k in self.stage_latencies and v is not None:
                    self.stage_latencies[k].append(int(v))
        if trace_summary:
            self.last_calls.appendleft(trace_summary)

    def to_dict(self):
        return {
            "calls": self.calls,
            "success": self.success,
            "fail": self.fail,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "fallback_uses": self.fallback_uses,
            "fanout_used_fallback": self.fanout_used_fallback,
            "refiner_used_fallback": self.refiner_used_fallback,
            "stage_latencies": {k: list(v) for k, v in self.stage_latencies.items()},
            "last_calls": list(self.last_calls),
        }

    @classmethod
    def from_dict(cls, d):
        b = cls()
        b.calls = int(d.get("calls", 0))
        b.success = int(d.get("success", 0))
        b.fail = int(d.get("fail", 0))
        b.total_tokens_in = int(d.get("total_tokens_in", 0))
        b.total_tokens_out = int(d.get("total_tokens_out", 0))
        b.fallback_uses = int(d.get("fallback_uses", 0))
        b.fanout_used_fallback = int(d.get("fanout_used_fallback", 0))
        b.refiner_used_fallback = int(d.get("refiner_used_fallback", 0))
        for k, v in (d.get("stage_latencies") or {}).items():
            if k in b.stage_latencies and isinstance(v, list):
                b.stage_latencies[k] = [int(x) for x in v]
        lc = d.get("last_calls") or []
        b.last_calls = deque(lc, maxlen=50)
        return b

    def snapshot(self):
        sr = (self.success / self.calls) if self.calls else 0.0
        stg = {}
        for name, vals in self.stage_latencies.items():
            stg[name] = None if not vals else {
                "n": len(vals),
                "p50": _percentile(vals, 0.5),
                "p95": _percentile(vals, 0.95),
                "p99": _percentile(vals, 0.99),
            }
        return {
            "calls": self.calls, "success": self.success, "fail": self.fail,
            "success_rate": round(sr, 4),
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "fallback_uses": self.fallback_uses,
            "fanout_used_fallback": self.fanout_used_fallback,
            "refiner_used_fallback": self.refiner_used_fallback,
            "stage_latencies_ms": stg,
            "last_calls": list(self.last_calls),
        }


class FusionMetrics:
    def __init__(self, state_file: Optional[str] = None):
        self._lock = threading.Lock()
        self._plans = {}
        self._state_file = state_file or _STATE_FILE
        self._save_lock = threading.Lock()
        self._dirty = False
        self._last_save_ts = 0.0
        self._save_cooldown = 2.0  # debounce 2s
        # Try load
        self._load()

    def _load(self):
        try:
            if os.path.exists(self._state_file):
                with open(self._state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                plans_data = data.get("plans", {})
                with self._lock:
                    for pid, pdict in plans_data.items():
                        self._plans[pid] = _PlanBucket.from_dict(pdict)
                LOG.info("fusion_metrics loaded %d plan(s) from %s", len(plans_data), self._state_file)
        except Exception as e:
            LOG.warning("fusion_metrics load failed (will start fresh): %s", e)

    def _save_sync(self):
        """Sync save to disk. Caller must hold _lock."""
        try:
            os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
            data = {
                "version": 1,
                "saved_at": time.time(),
                "plans": {pid: b.to_dict() for pid, b in self._plans.items()},
            }
            tmp = self._state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self._state_file)
        except Exception as e:
            LOG.warning("fusion_metrics save failed: %s", e)

    def _save_async(self):
        """Schedule a save if cooldown elapsed."""
        now = time.time()
        with self._save_lock:
            if now - self._last_save_ts < self._save_cooldown:
                return  # debounced
            self._last_save_ts = now
        # Snapshot + write outside any lock contention
        with self._lock:
            self._save_sync()

    def _bucket(self, plan_id):
        b = self._plans.get(plan_id)
        if b is None:
            b = _PlanBucket()
            self._plans[plan_id] = b
        return b

    def record(self, plan_id, **kwargs):
        with self._lock:
            self._bucket(plan_id).record(**kwargs)
        # Persist async
        self._save_async()

    def snapshot(self):
        with self._lock:
            plans = {k: v.snapshot() for k, v in self._plans.items()}
            calls = sum(p["calls"] for p in plans.values())
            success = sum(p["success"] for p in plans.values())
            fail = sum(p["fail"] for p in plans.values())
            sr = (success / calls) if calls else 0.0
            return {
                "plans": plans,
                "global": {
                    "calls": calls, "success": success, "fail": fail,
                    "success_rate": round(sr, 4),
                },
            }

    def reset(self):
        with self._lock:
            self._plans.clear()
            self._save_sync()  # persist empty state immediately


fusion_metrics = FusionMetrics()