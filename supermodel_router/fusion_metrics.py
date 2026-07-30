"""fusion_metrics.py — in-memory per-plan statistics for n1_fusion."""
import time
import threading
from collections import deque
from typing import Any, Dict, Optional, List


def _percentile(values, p):
    if not values:
        return 0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))
    return int(s[k])


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
    def __init__(self):
        self._lock = threading.Lock()
        self._plans = {}

    def _bucket(self, plan_id):
        b = self._plans.get(plan_id)
        if b is None:
            b = _PlanBucket()
            self._plans[plan_id] = b
        return b

    def record(self, plan_id, **kwargs):
        with self._lock:
            self._bucket(plan_id).record(**kwargs)

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


fusion_metrics = FusionMetrics()
