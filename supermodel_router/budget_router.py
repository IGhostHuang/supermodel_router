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