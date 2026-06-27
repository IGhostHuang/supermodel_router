"""
supermodel_router/memory_bus.py — 跨请求经验复用 + 路由记忆 (v3.21.0 周天循环)

核心: 路由决策不再从零开始, 每次请求的经验写入 memory_bus, 下次 tick/请求自动复用

3 层记忆:
  L1 热记忆 (RAM): 最近 1000 条路由结果, 0ms 读写
  L2 温记忆 (state/): JSON 持久化, 重启不丢
  L3 冷记忆 (vault): 长期模式, admin 可查

记忆条目 (RouteMemory):
  - provider/model + 输入模态 + 输出模态 + 任务类型 → 成功/失败/延迟/token消耗
  - 自动聚合: 成功率 EWMA / 延迟 EWMA / 最佳 provider-model 对
  - 失败模式: 记录失败原因分类 (timeout/429/500/content_filter/…)
  - 周天循环: 每天 UTC 00:00 rotate, 7 天滚动窗口
"""

import json
import time
import logging
import threading
from collections import deque
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

LOG = logging.getLogger("memory_bus")

# ── 常量 ──────────────────────────────────────────
MEMORY_FILE = "loop_memory.json"
HOT_WINDOW = 1000          # L1 热记忆窗口
RECENT_WINDOW = 100        # L2 温记忆聚合窗口
PATTERN_ROTATE_DAYS = 7    # L3 冷记忆滚动天数
EWMA_ALPHA = 0.3           # 新值权重


@dataclass
class RouteMemory:
    """单次路由经验"""
    provider: str
    model_id: str
    input_modality: str       # text / image / audio / video / multimodal
    output_modality: str      # text / image / audio / video
    task_type: str            # generation / reasoning / coding / creative /…
    success: bool
    latency_ms: float
    tokens_in: int = 0
    tokens_out: int = 0
    fail_reason: str = ""     # timeout / 429 / 500 / content_filter / abort / other
    timestamp: float = 0.0

    def path(self) -> str:
        return f"{self.provider}/{self.model_id}"

    def key(self) -> str:
        """聚合 key: path+modality+task"""
        return f"{self.path()}|{self.input_modality}->{self.output_modality}|{self.task_type}"


@dataclass
class AggregatedPattern:
    """聚合路由模式 (L2 温记忆)"""
    key: str                       # RouteMemory.key()
    provider: str
    model_id: str
    success_count: int = 0
    fail_count: int = 0
    total_calls: int = 0
    ewma_latency_ms: float = 0.0
    ewma_success_rate: float = 0.0
    last_success_at: float = 0.0
    last_fail_at: float = 0.0
    last_fail_reason: str = ""
    avg_tokens_in: float = 0.0
    avg_tokens_out: float = 0.0
    day_stamp: str = ""           # UTC YYYY-MM-DD, 用于 rotate


class MemoryBus:
    """
    跨请求经验复用总线

    写: record(result) → L1 热 + L2 聚合更新
    读: query(modality, task) → L1 最近 + L2 模式 → 推荐 provider/model 排序
    tick: 每 5min 由 Loop Engine 调用, 做 L2→L3 持久化 + rotate
    """

    def __init__(self, state_dir: str = "."):
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)

        # L1 热记忆 (RAM ring buffer)
        self._hot: deque[RouteMemory] = deque(maxlen=HOT_WINDOW)
        # L2 温记忆 (聚合模式)
        self._warm: Dict[str, AggregatedPattern] = {}
        # L3 冷记忆 (7天滚动)
        self._cold: Dict[str, dict] = {}

        # 线程安全
        self._lock = threading.Lock()

        # 启动时加载
        self._load()

    # ── 写: record ──────────────────────────────────

    def record(self, mem: RouteMemory) -> None:
        """记录一次路由经验 → L1 + L2"""
        if mem.timestamp == 0.0:
            mem.timestamp = time.time()

        with self._lock:
            # L1: 入 ring buffer
            self._hot.append(mem)

            # L2: 聚合更新
            key = mem.key()
            if key not in self._warm:
                self._warm[key] = AggregatedPattern(
                    key=key,
                    provider=mem.provider,
                    model_id=mem.model_id,
                )
            p = self._warm[key]
            p.total_calls += 1
            if mem.success:
                p.success_count += 1
                p.last_success_at = mem.timestamp
            else:
                p.fail_count += 1
                p.last_fail_at = mem.timestamp
                p.last_fail_reason = mem.fail_reason

            # EWMA latency
            if p.ewma_latency_ms == 0.0:
                p.ewma_latency_ms = mem.latency_ms
            else:
                p.ewma_latency_ms = (
                    EWMA_ALPHA * mem.latency_ms
                    + (1 - EWMA_ALPHA) * p.ewma_latency_ms
                )

            # EWMA success rate
            instant = 1.0 if mem.success else 0.0
            p.ewma_success_rate = (
                EWMA_ALPHA * instant
                + (1 - EWMA_ALPHA) * p.ewma_success_rate
            )

            # avg tokens
            if mem.tokens_in > 0:
                n = p.total_calls
                p.avg_tokens_in = p.avg_tokens_in * (n - 1) / n + mem.tokens_in / n
                p.avg_tokens_out = p.avg_tokens_out * (n - 1) / n + mem.tokens_out / n

    # ── 读: query ──────────────────────────────────

    def query(self, input_modality: str, output_modality: str,
              task_type: str = "", top_k: int = 5) -> List[Tuple[str, float]]:
        """
        查询最佳 provider/model 排序

        Returns: [(path, score), ...] score 越高越好
        """
        with self._lock:
            results: Dict[str, float] = {}

            for key, p in self._warm.items():
                # 过滤: 只匹配 modality+task
                parts = key.split("|")
                if len(parts) < 2:
                    continue
                modality_pair = parts[1]  # "input->output"
                if f"{input_modality}->{output_modality}" != modality_pair:
                    continue
                if task_type and len(parts) >= 3 and parts[2] != task_type:
                    continue

                # 评分: success_rate 70% + latency 30%
                score = p.ewma_success_rate * 0.7
                if p.ewma_latency_ms > 0:
                    # latency 越低越好, 归一化到 0-1
                    latency_score = max(0.0, 1.0 - p.ewma_latency_ms / 30000.0)
                    score += latency_score * 0.3

                # 最近失败降权
                if p.last_fail_at > p.last_success_at:
                    age_hours = (time.time() - p.last_fail_at) / 3600.0
                    if age_hours < 1.0:
                        score *= 0.5  # 1h 内失败降半

                path = f"{p.provider}/{p.model_id}"
                if path in results:
                    results[path] = max(results[path], score)
                else:
                    results[path] = score

            # 排序
            ranked = sorted(results.items(), key=lambda x: -x[1])[:top_k]
            return ranked

    # ── 读: get_pattern ──────────────────────────────

    def get_pattern(self, provider: str, model_id: str) -> Optional[AggregatedPattern]:
        """查单个 model 聚合模式"""
        prefix = f"{provider}/{model_id}|"
        with self._lock:
            for key, p in self._warm.items():
                if key.startswith(prefix):
                    return p
        return None

    # ── 读: stats ──────────────────────────────────

    def stats(self) -> dict:
        """总线统计 (admin 用)"""
        with self._lock:
            return {
                "l1_hot_count": len(self._hot),
                "l2_warm_patterns": len(self._warm),
                "l3_cold_patterns": len(self._cold),
                "total_calls": sum(p.total_calls for p in self._warm.values()),
                "top_providers": self._top_providers(5),
            }

    def _top_providers(self, k: int) -> List[dict]:
        """按调用次数 top k provider"""
        provider_calls: Dict[str, int] = {}
        for p in self._warm.values():
            provider_calls[p.provider] = provider_calls.get(p.provider, 0) + p.total_calls
        top = sorted(provider_calls.items(), key=lambda x: -x[1])[:k]
        return [{"provider": name, "calls": cnt} for name, cnt in top]

    # ── tick: rotate + persist ──────────────────────

    def tick(self) -> dict:
        """
        由 Loop Engine 每 5min 调用:
        1. L2→L3 rotate (7天)
        2. L2 持久化到 state/loop_memory.json
        """
        import datetime
        today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        rotated = 0

        with self._lock:
            # rotate: 7+ 天前的 L2 → L3 冷记忆
            cutoff = time.time() - PATTERN_ROTATE_DAYS * 86400
            expired_keys = []
            for key, p in self._warm.items():
                if p.last_success_at < cutoff and p.last_fail_at < cutoff:
                    self._cold[key] = asdict(p)
                    expired_keys.append(key)
            for k in expired_keys:
                del self._warm[k]
                rotated += 1

            # 持久化
            self._save()

        LOG.info("memory_bus tick: rotated=%d warm=%d cold=%d",
                 rotated, len(self._warm), len(self._cold))
        return {"rotated": rotated, "warm": len(self._warm), "cold": len(self._cold)}

    # ── 持久化 ──────────────────────────────────

    def _save(self):
        """L2 + L3 持久化 (caller 持 _lock)"""
        data = {
            "warm": {k: asdict(v) for k, v in self._warm.items()},
            "cold": self._cold,
            "saved_at": time.time(),
        }
        fp = self._state_dir / MEMORY_FILE
        try:
            fp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception:
            LOG.exception("memory_bus save failed")

    def _load(self):
        """启动时加载 L2 + L3"""
        fp = self._state_dir / MEMORY_FILE
        if not fp.exists():
            return
        try:
            data = json.loads(fp.read_text())
            for k, v in data.get("warm", {}).items():
                self._warm[k] = AggregatedPattern(**v)
            self._cold = data.get("cold", {})
            LOG.info("memory_bus loaded: warm=%d cold=%d",
                     len(self._warm), len(self._cold))
        except Exception:
            LOG.exception("memory_bus load failed")

    # ── recent (L1 直查) ──────────────────────────

    def recent(self, limit: int = 20) -> List[dict]:
        """最近 N 条路由经验 (admin 用)"""
        with self._lock:
            items = list(self._hot)[-limit:]
            return [asdict(m) for m in reversed(items)]
