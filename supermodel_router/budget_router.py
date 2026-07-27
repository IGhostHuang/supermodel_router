"""
budget_router.py — L2 Smart Budget-Aware Routing (v3.23.0)

老大 2026-06-27 钦定: 
  - 性价比最优
  - 不是简单 health + score
  - 给定 max_cost, 自动选 best within budget

核心:
  - 每模型 cost 估算 (input/output token × $/token)
  - QualityEstimator: 基于历史 quality_score + 任务难度预估
  - BudgetAwareRouter: 过滤超预算, 在预算内挑性价比 best
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)


@dataclass
class ModelCostEstimate:
    """单模型的 cost 估算"""
    provider: str
    model_id: str
    full_path: str
    cost_per_1k_input: float = 0.0       # USD per 1000 input tokens
    cost_per_1k_output: float = 0.0      # USD per 1000 output tokens
    is_free: bool = False
    tier: str = "unknown"
    quality_score: float = 50.0
    avg_latency_ms: float = 0.0
    
    def estimate_cost(self, est_input_tokens: int, est_output_tokens: int) -> float:
        if self.is_free:
            return 0.0
        return (
            (est_input_tokens / 1000) * self.cost_per_1k_input +
            (est_output_tokens / 1000) * self.cost_per_1k_output
        )
    
    def value_score(self, est_input_tokens: int, est_output_tokens: int) -> float:
        """性价比 = quality / cost (越高越优)"""
        cost = self.estimate_cost(est_input_tokens, est_output_tokens)
        if cost <= 0:
            return self.quality_score * 100  # free 模型 → 用 quality 直接打分
        return self.quality_score / max(cost, 0.000001)


# 默认价格表 (2026-06-27 现状, USD/1K tokens)
DEFAULT_PRICING: Dict[str, Dict[str, float]] = {
    # OpenRouter (按 :free 后缀 + 付费模型常见价)
    "openrouter/meta-llama/llama-4-maverick:free":      {"in": 0.0, "out": 0.0},
    "openrouter/qwen/qwen-2.5-coder-32b-instruct:free":{"in": 0.0, "out": 0.0},
    "openrouter/anthropic/claude-sonnet-4":             {"in": 0.003, "out": 0.015},
    "openrouter/openai/gpt-4o":                         {"in": 0.0025, "out": 0.01},
    
    # NVIDIA NIM (默认免费)
    "nvidia/meta/llama-3.1-8b-instruct":                {"in": 0.0, "out": 0.0},
    "nvidia/nvidia/llama-3.3-nemotron-super-49b-v1":   {"in": 0.0, "out": 0.0},
    
    # 魔塔免费 (全免费)
    "魔塔免费模型/Qwen/Qwen3-VL-8B-Instruct":          {"in": 0.0, "out": 0.0},
    
    # 火山 Ark
    "volc_ark/ark-code-latest":                         {"in": 0.0008, "out": 0.002},
    "volc_ark/GLM-5.2":                                 {"in": 0.0005, "out": 0.001},
    
    # Cloudflare Workers AI
    "cloudflare/@cf/meta/llama-3.1-8b-instruct":         {"in": 0.0, "out": 0.0},
}


class CostTable:
    """Cost 估算表 (含 free 标识)"""
    
    def __init__(self, free_paths: Optional[set] = None):
        self._pricing = {**DEFAULT_PRICING}
        self._free_paths = free_paths or set()
    
    def mark_free(self, full_path: str):
        self._free_paths.add(full_path)
    
    def estimate(self, provider: str, model_id: str, 
                 est_input_tokens: int = 1000,
                 est_output_tokens: int = 500) -> ModelCostEstimate:
        full = f"{provider}/{model_id}"
        pricing = self._pricing.get(full, {"in": 0.001, "out": 0.002})  # 默认保守估计
        is_free = full in self._free_paths or pricing.get("in", 0) == 0 and pricing.get("out", 0) == 0
        
        return ModelCostEstimate(
            provider=provider,
            model_id=model_id,
            full_path=full,
            cost_per_1k_input=pricing.get("in", 0.0),
            cost_per_1k_output=pricing.get("out", 0.0),
            is_free=is_free,
        )


class QualityEstimator:
    """质量预估 — 基于历史 quality_score + 任务难度
    
    难度评估:
      - prompt 长度 + 上下文复杂度 → 难度系数
      - 任务类型 (code > math > translation > chat)
    """
    
    # 任务类型权重 (1.0 = 标准, >1 = 难)
    KIND_DIFFICULTY = {
        "chat": 0.6,
        "completion": 0.7,
        "vision_qa": 0.9,
        "image_gen": 0.8,
        "audio_transcribe": 0.7,
        "audio_gen": 0.8,
        "multi_step": 1.3,
        "parallel_fusion": 1.0,
    }
    
    @staticmethod
    def estimate_required_quality(task_kind: str, prompt_len: int, has_images: bool) -> float:
        """估算任务需要的最低 quality"""
        difficulty = QualityEstimator.KIND_DIFFICULTY.get(task_kind, 1.0)
        # 越长越需要高质量 (避免 model 截断/遗忘)
        len_factor = min(2.0, 1.0 + prompt_len / 4000)
        image_factor = 1.2 if has_images else 1.0
        return 50.0 * difficulty * len_factor * image_factor


class BudgetAwareRouter:
    """Budget-aware 路由
    
    流程:
      1. 拿到所有 candidate + cost estimate
      2. 过滤超 budget
      3. 按 value_score (quality/cost) 排序
      4. 优先 free (如果 quality 达标)
    """
    
    def __init__(self, cost_table: CostTable, free_registry=None):
        self.cost_table = cost_table
        self.free_registry = free_registry
    
    def select_within_budget(
        self,
        candidates: List[Any],  # List of (provider, model_id, raw_score)
        max_cost: float,
        est_input_tokens: int = 1000,
        est_output_tokens: int = 500,
        min_quality: float = 50.0,
    ) -> List[Any]:
        """在 budget 内筛选 + 排序 candidates
        
        Returns: 排序后的 candidates (best first)
        """
        scored = []
        for cand in candidates:
            provider, model_id, raw_score = cand[0], cand[1], cand[2]
            cost_est = self.cost_table.estimate(
                provider, model_id, est_input_tokens, est_output_tokens
            )
            
            # 质量检查
            quality = self._get_quality(provider, model_id, raw_score)
            if quality < min_quality:
                continue
            
            # Budget 检查
            estimated_cost = cost_est.estimate_cost(est_input_tokens, est_output_tokens)
            if estimated_cost > max_cost and not cost_est.is_free:
                # 付费超预算 → 跳过
                LOG.debug("BudgetAwareRouter: skip %s (cost=%.5f > budget=%.5f)",
                          cost_est.full_path, estimated_cost, max_cost)
                continue
            
            # Value score
            value = cost_est.value_score(est_input_tokens, est_output_tokens)
            
            # 综合分 = raw * 0.4 + value * 0.6
            combined = raw_score * 0.4 + min(value, 100) * 0.6
            scored.append((combined, cand))
        
        scored.sort(key=lambda x: -x[0])
        return [cand for _, cand in scored]
    
    def _get_quality(self, provider: str, model_id: str, raw_score: float) -> float:
        """优先用 free_registry 累计的 quality"""
        if self.free_registry:
            full = f"{provider}/{model_id}"
            info = self.free_registry.get(full)
            if info and info.success_count > 5:
                return info.quality_score
        # fallback 用 raw_score (0-100)
        return raw_score if raw_score > 1.0 else raw_score * 100


# ─── Convenience ────────────────────────────────────────────────────

def auto_select(
    candidates: List[tuple],
    max_cost: float = 0.01,
    est_tokens: int = 1500,
    free_registry=None,
) -> Optional[tuple]:
    """快速选: 1 个 best candidate within budget"""
    ct = CostTable(free_paths=free_registry.get_all_paths() if free_registry else set())
    router = BudgetAwareRouter(ct, free_registry)
    filtered = router.select_within_budget(
        candidates, max_cost=max_cost,
        est_input_tokens=est_tokens, est_output_tokens=est_tokens // 2,
    )
    return filtered[0] if filtered else None


# ═══════════════════════════════════════════════════════════════════
# v4.0.0: 8 路由策略调度器 (RoutingStrategyDispatcher)
# 老大 §P0-1 钦定: priority / weighted / round-robin / cost-optimized
#                  lkgp / least-used / p2c / reset-aware
# 与现有 BudgetAwareRouter 并存, 不改旧路径 (backward compat)
# ═══════════════════════════════════════════════════════════════════

import random
import time
import json
import os
from pathlib import Path

STRATEGIES = [
    "priority",         # 按 score 全局降序 (等价于 flat)
    "weighted",         # 按 score 加权随机
    "round-robin",      # 严格轮询 (跨 candidate)
    "cost-optimized",   # 按成本升序 (免费在前, 付费按 $/1M)
    "lkgp",             # Last Known Good Provider (黏上次成功)
    "least-used",       # 选负载最低 (same_provider_active 最小)
    "p2c",              # Power-of-Two-Choices: 随机抽 2 挑负载小的
    "reset-aware",      # 配额重置窗口临近优先
]


class RoutingStrategyDispatcher:
    """
    v4 8 路由策略调度器

    输入: scored: List[(combined_score, ModelInfo, penalty, path)]
    输出: ordered: List[(combined_score, ModelInfo, penalty, path)]

    调用点: engine.py pick_chain() 在 group 级 _order_by_strategy 之后
    """

    def __init__(self, state_dir: Optional[Path] = None):
        # LKGP 持久化: state/lkgp.json { "model_id": "provider/model/ki", ... }
        self.state_dir = Path(state_dir) if state_dir else Path(os.environ.get("STATE_DIR", "/app/state"))
        self.lkgp_file = self.state_dir / "lkgp.json"
        self.rr_file = self.state_dir / "rr.json"
        self._lkgp: Dict[str, str] = {}
        self._rr_cursor: Dict[str, int] = {}   # key = requested_model, val = 上次选到第几个
        self._load_state()

    def _load_state(self):
        try:
            if self.lkgp_file.exists():
                self._lkgp = json.loads(self.lkgp_file.read_text())
        except Exception as e:
            LOG.warning("dispatcher: load lkgp failed: %s", e)
            self._lkgp = {}
        try:
            if self.rr_file.exists():
                self._rr_cursor = json.loads(self.rr_file.read_text())
        except Exception as e:
            LOG.warning("dispatcher: load rr failed: %s", e)
            self._rr_cursor = {}

    def _save_lkgp(self):
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self.lkgp_file.write_text(json.dumps(self._lkgp, ensure_ascii=False, indent=2))
        except Exception as e:
            LOG.warning("dispatcher: save lkgp failed: %s", e)

    def _save_rr(self):
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self.rr_file.write_text(json.dumps(self._rr_cursor, ensure_ascii=False, indent=2))
        except Exception as e:
            LOG.warning("dispatcher: save rr failed: %s", e)

    def record_success(self, requested_model: str, full_path: str):
        """LKGP: 记录该 requested_model 上次成功的 full_path"""
        self._lkgp[requested_model] = full_path
        self._save_lkgp()

    def apply_strategy(
        self,
        scored: List[tuple],           # [(score, m, penalty, path), ...]
        strategy: str = "priority",
        requested_model: str = "",
        cost_table: Optional[CostTable] = None,
        provider_active: Optional[Dict[str, int]] = None,
        provider_capacity: Optional[Dict[str, int]] = None,
        quota_reset_map: Optional[Dict[str, float]] = None,
    ) -> List[tuple]:
        """
        应用 model 级路由策略

        provider_active: {provider: 当前并发数}
        provider_capacity: {provider: 总并发槽}
        quota_reset_map: {full_path: seconds_until_reset}
        """
        if not scored:
            return scored
        if strategy not in STRATEGIES:
            LOG.warning("dispatcher: unknown strategy '%s', fallback to priority", strategy)
            strategy = "priority"

        if strategy == "priority":
            return sorted(scored, key=lambda x: -x[0])

        if strategy == "weighted":
            # 按 score 加权采样 (score 越高抽中概率越大), 输出为一次性抽样序列
            pool = list(scored)
            result = []
            while pool:
                weights = [max(0.001, s[0]) for s in pool]
                idx = random.choices(range(len(pool)), weights=weights, k=1)[0]
                result.append(pool.pop(idx))
            return result

        if strategy == "round-robin":
            # 严格轮询: 用 rr_cursor 记录上次选到第几个
            ordered_by_score = sorted(scored, key=lambda x: -x[0])
            n = len(ordered_by_score)
            key = requested_model or "__default__"
            start = self._rr_cursor.get(key, 0) % n
            rotated = ordered_by_score[start:] + ordered_by_score[:start]
            self._rr_cursor[key] = (start + 1) % n
            self._save_rr()
            return rotated

        if strategy == "cost-optimized":
            # 按成本升序 (免费在前)
            def cost_key(item):
                _score, m, _penalty, path = item
                if cost_table is not None:
                    est = cost_table.estimate(m.provider, m.id)
                    if est.is_free:
                        return 0.0
                    return est.cost_per_1k_input + est.cost_per_1k_output
                # fallback: 无 cost_table → 用 is_free 属性
                return 0.0 if getattr(m, 'is_free', False) else 1.0
            return sorted(scored, key=cost_key)

        if strategy == "lkgp":
            # Last Known Good Provider: 上次成功的 path 排第一, 其他按 score 降序
            key = requested_model or "__default__"
            last_good = self._lkgp.get(key)
            if not last_good:
                return sorted(scored, key=lambda x: -x[0])
            preferred = []
            others = []
            for item in scored:
                _score, _m, _penalty, path = item
                if path == last_good:
                    preferred.append(item)
                else:
                    others.append(item)
            others.sort(key=lambda x: -x[0])
            return preferred + others

        if strategy == "least-used":
            # 选负载最低: same_provider_active / capacity 比率最小
            active = provider_active or {}
            capacity = provider_capacity or {}
            def load_key(item):
                _score, m, _penalty, _path = item
                a = active.get(m.provider, 0)
                c = max(1, capacity.get(m.provider, 1))
                return (a / c, -_score)  # 负载升序, score 降序
            return sorted(scored, key=load_key)

        if strategy == "p2c":
            # Power-of-Two-Choices: 随机抽 2 挑负载小的作为第 1, 剩下 shuffle
            active = provider_active or {}
            capacity = provider_capacity or {}
            def load(m):
                a = active.get(m.provider, 0)
                c = max(1, capacity.get(m.provider, 1))
                return a / c
            pool = list(scored)
            result = []
            while len(pool) >= 2:
                # 随机抽 2 个
                a, b = random.sample(pool, 2)
                winner = a if load(a[1]) <= load(b[1]) else b
                result.append(winner)
                pool.remove(winner)
            result.extend(pool)  # 剩余 0 或 1 个
            return result

        if strategy == "reset-aware":
            # 配额重置窗口临近优先: quota_reset_in_seconds 越小越优先
            reset_map = quota_reset_map or {}
            def reset_key(item):
                _score, _m, _penalty, path = item
                reset_in = reset_map.get(path, 9999999.0)
                return (reset_in, -_score)  # 重置越快越优先, 平手时 score 降序
            return sorted(scored, key=reset_key)

        # 兜底
        return sorted(scored, key=lambda x: -x[0])


# 单例
_dispatcher: Optional[RoutingStrategyDispatcher] = None


def get_dispatcher(state_dir: Optional[Path] = None) -> RoutingStrategyDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = RoutingStrategyDispatcher(state_dir=state_dir)
    return _dispatcher