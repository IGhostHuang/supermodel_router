"""
supermodel_router/scoring_engine.py — Auto-Combo 12 因子评分引擎 (v4.0.0)

老大 2026-07-27 钦定:
  12 因子权重 (总和 1.0):
    health             0.20  — model_health 健康度
    quota              0.15  — 剩余配额比率
    costInv            0.15  — 价格 (越低越好, inverse)
    latencyInv         0.12  — P95 延迟 (越低越好, inverse)
    taskFit            0.08  — 任务匹配度 (coding/chat/reasoning)
    stability          0.05  — 失败率/标准差
    tierPriority       0.05  — tier 加分
    tierAffinity       0.05  — 请求 tier 与 model tier 匹配度 (NEW)
    specificityMatch   0.05  — 模型专精度 (通用<专精) (NEW)
    contextAffinity    0.05  — 上下文窗口匹配
    connectionDensity  0.05  — 同 provider 负载分散
    resetWindowAffinity 0.00 — 配额重置窗口临近度 (预留, P1 激活)

集成点: engine.py pick_chain() 的排序逻辑替换为 scoring_engine.score()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

LOG = logging.getLogger("scoring_engine")

# ── 默认权重 (12 因子, 老大 §4.2 钦定) ────────────────────────
DEFAULT_WEIGHTS: Dict[str, float] = {
    "health_score": 0.20,
    "quota_remaining": 0.15,
    "cost_per_token": 0.15,
    "latency_p95": 0.12,
    "task_fit": 0.08,
    "stability": 0.05,
    "tier_priority": 0.05,
    "tier_affinity": 0.05,
    "specificity_match": 0.05,
    "context_affinity": 0.05,
    "connection_density": 0.05,
    "reset_window_affinity": 0.00,
}

# 权重总和校验 (1.0)
assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 0.001, \
    f"Weights must sum to 1.0, got {sum(DEFAULT_WEIGHTS.values())}"

# ── 任务类型维度 ──────────────────────────────────────────────
# 每个模型在特定任务上的适配分 (0-100)
# 来源: Arena ELO / 社区基准 / 实测, 初始默认值
TASK_FIT_DEFAULTS: Dict[str, Dict[str, float]] = {
    "coding": {
        "claude": 92, "gpt-4": 88, "gpt-4o": 85, "gemini": 82,
        "deepseek": 85, "qwen": 80, "yi": 72, "glm": 75,
        "llama": 70, "mistral": 72, "default": 60,
    },
    "chat": {
        "claude": 90, "gpt-4o": 92, "gemini": 88, "deepseek": 82,
        "qwen": 85, "yi": 80, "glm": 78, "llama": 75, "mistral": 75,
        "default": 65,
    },
    "reasoning": {
        "claude": 95, "gpt-4": 90, "deepseek": 92, "gemini": 85,
        "qwen": 82, "yi": 75, "glm": 78, "llama": 65, "mistral": 68,
        "default": 60,
    },
    "translation": {
        "claude": 88, "gpt-4o": 85, "deepseek": 90, "qwen": 88,
        "gemini": 82, "yi": 85, "glm": 85, "llama": 70, "mistral": 72,
        "default": 65,
    },
    "creative": {
        "claude": 92, "gpt-4o": 88, "gemini": 85, "deepseek": 80,
        "qwen": 78, "yi": 75, "glm": 72, "llama": 70, "mistral": 68,
        "default": 60,
    },
}

# ── 成本分映射 (USD / 1M tokens, 越低分越高) ──────────────────
# 0 = 免费/配额内 → 100 分; >$15 → 0 分
COST_SCORE_BASELINE = 0.0    # 免费 = 100
COST_SCORE_MAX = 15.0         # $15/1M = 0
COST_SCORE_RANGE = COST_SCORE_MAX - COST_SCORE_BASELINE


@dataclass
class ScoringContext:
    """评分上下文 — 传入所有所需数据"""
    # 模型信息
    model_id: str = ""
    provider: str = ""
    modality: str = "text_only"

    # 健康度 (来自 model_health.py)
    health_state: str = "healthy"          # healthy/degraded/skip/half_open
    consecutive_fails: int = 0
    rolling_success_rate: float = 100.0    # 0-100
    ewma_latency_ms: float = 0.0

    # 配额 (来自 rate_limiter / tenant usage)
    quota_used: int = 0
    quota_total: int = 0                   # 0 = 无限制

    # 成本 (来自 pricing.json)
    cost_per_1m_input: float = 0.0
    cost_per_1m_output: float = 0.0
    is_free: bool = True

    # 延迟统计 (来自 engine_stats)
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_std_ms: float = 0.0

    # 能力分 (来自 classifier)
    capability_score: float = 50.0
    tier_bonus: float = 0.0
    context_window: int = 8192

    # 请求特征
    request_tokens: int = 0                # 请求的 token 数
    task_type: str = "chat"               # coding/chat/reasoning/translation/creative
    request_tier: str = "standard"        # v4: standard/premium/budget  (tier_affinity)
    request_specialty: str = ""           # v4: 请求专精领域 (specificity_match)

    # 同 provider 负载 (用于负载分散)
    same_provider_active: int = 0          # 同 provider 正在处理的并发数
    same_provider_capacity: int = 1        # provider 总并发槽

    # v4 新增: 配额重置窗口 (reset_window_affinity)
    quota_reset_in_seconds: float = 0.0    # 距离下次重置的秒数


@dataclass
class FactorResult:
    """单个因子的评分明细"""
    name: str
    weight: float
    raw_score: float        # 0-100
    weighted: float         # raw * weight
    detail: str = ""


@dataclass
class ScoringResult:
    """完整评分结果"""
    total_score: float = 0.0
    factors: List[FactorResult] = field(default_factory=list)
    summary: str = ""


class AutoComboScorer:
    """Auto-Combo 12 因子评分器 (v4.0.0, 老大 §4.2 钦定权重)"""

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        self.weights = dict(weights or DEFAULT_WEIGHTS)
        self._validate_weights()

    def _validate_weights(self):
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.001:
            LOG.warning("Weights sum to %.3f (expected 1.0), normalizing", total)
            factor = 1.0 / total
            self.weights = {k: v * factor for k, v in self.weights.items()}

    # ── 12 个因子计算函数 ────────────────────────────────────

    def _health_score(self, ctx: ScoringContext) -> float:
        """健康度: SKIP=0, DEGRADED=40, HALF_OPEN=55, HEALTHY=rolling_success_rate"""
        if ctx.health_state == "skip":
            return 0.0
        if ctx.health_state == "degraded":
            return 40.0
        if ctx.health_state == "half_open":
            return 55.0
        return ctx.rolling_success_rate

    def _quota_score(self, ctx: ScoringContext) -> float:
        """配额剩余: 无限制=100, 用完=0"""
        if ctx.quota_total <= 0:
            return 100.0
        if ctx.quota_used >= ctx.quota_total:
            return 0.0
        remaining_ratio = 1.0 - (ctx.quota_used / ctx.quota_total)
        return remaining_ratio * 100.0

    def _cost_score(self, ctx: ScoringContext) -> float:
        """成本分 (inverse): 免费=100, $15+/1M=0"""
        if ctx.is_free or (ctx.cost_per_1m_input + ctx.cost_per_1m_output) == 0:
            return 100.0
        avg_cost = (ctx.cost_per_1m_input + ctx.cost_per_1m_output) / 2
        if avg_cost >= COST_SCORE_MAX:
            return 0.0
        return max(0.0, 100.0 * (1.0 - avg_cost / COST_SCORE_MAX))

    def _latency_score(self, ctx: ScoringContext) -> float:
        """延迟分 (inverse): P95 < 500ms=100, > 10s=0"""
        if ctx.latency_p95_ms <= 0:
            return 70.0
        if ctx.latency_p95_ms < 500:
            return 100.0
        if ctx.latency_p95_ms > 10000:
            return 0.0
        return max(0.0, 100.0 - (ctx.latency_p95_ms - 500) / 95.0)

    def _task_fit_score(self, ctx: ScoringContext) -> float:
        """任务匹配: 根据 model_id 匹配 task_type 的已知基准分"""
        task_baselines = TASK_FIT_DEFAULTS.get(ctx.task_type, TASK_FIT_DEFAULTS.get("chat", {}))
        model_lower = ctx.model_id.lower()
        provider_lower = ctx.provider.lower()
        for key, score in task_baselines.items():
            if key in provider_lower or key in model_lower:
                return score
        return task_baselines.get("default", 60.0)

    def _stability_score(self, ctx: ScoringContext) -> float:
        """稳定性: 连续失败 0=100, ≥3=0; 标准差低=高"""
        score = 100.0
        if ctx.consecutive_fails >= 3:
            score -= 60.0
        elif ctx.consecutive_fails == 2:
            score -= 30.0
        elif ctx.consecutive_fails == 1:
            score -= 10.0
        if ctx.latency_std_ms > 5000:
            score -= 20.0
        elif ctx.latency_std_ms > 2000:
            score -= 10.0
        return max(0.0, score)

    def _tier_score(self, ctx: ScoringContext) -> float:
        """Tier 优先级: capability_score + tier_bonus 归一化到 0-100"""
        raw = ctx.capability_score + ctx.tier_bonus
        return max(0.0, min(100.0, raw))

    def _tier_affinity_score(self, ctx: ScoringContext) -> float:
        """v4: 请求 tier 与 model tier 匹配度
        - 相同 tier → 100
        - premium 请求用 standard 模型 → 70 (仍可用)
        - premium 请求用 budget 模型 → 30 (降级)
        - budget 请求用 premium 模型 → 60 (浪费但可用)
        """
        req_tier = ctx.request_tier.lower()
        # 根据 capability_score 推断 model tier
        cap = ctx.capability_score
        if cap >= 80:
            model_tier = "premium"
        elif cap >= 60:
            model_tier = "standard"
        else:
            model_tier = "budget"

        if req_tier == model_tier:
            return 100.0
        if req_tier == "premium" and model_tier == "standard":
            return 70.0
        if req_tier == "premium" and model_tier == "budget":
            return 30.0
        if req_tier == "budget" and model_tier == "premium":
            return 60.0
        # fallback: standard 请求用 premium 或 budget
        if model_tier == "premium":
            return 80.0
        if model_tier == "budget":
            return 60.0
        return 80.0

    def _specificity_match_score(self, ctx: ScoringContext) -> float:
        """v4: 模型专精度匹配
        - 请求有专精领域 + 模型名匹配该领域 → 100 (coder 模型跑 coding 任务)
        - 请求无专精领域 + 通用模型 → 80
        - 请求有专精领域 + 通用模型 → 70 (能用但不专)
        - 请求有专精领域 + 模型专精其他领域 → 30 (错配)
        """
        specialty = ctx.request_specialty.lower().strip()
        if not specialty:
            return 80.0  # 无专精要求 → 通用匹配

        model_lower = ctx.model_id.lower()
        provider_lower = ctx.provider.lower()

        # 检查模型名/provider 是否包含专精关键词
        keywords = {
            "coding": ["coder", "code", "deepseek", "qwen-coder"],
            "math": ["math", "reasoning", "deepseek"],
            "creative": ["creative", "claude", "sonnet"],
            "chat": ["chat", "gpt", "instruct"],
        }
        matches = keywords.get(specialty, [])
        if matches:
            for kw in matches:
                if kw in model_lower or kw in provider_lower:
                    return 100.0  # 精确匹配
            return 70.0  # 有专精但模型不匹配
        return 80.0

    def _context_affinity_score(self, ctx: ScoringContext) -> float:
        """上下文亲和: request_tokens 接近 context_window → 低分, 刚好 → 高分"""
        if ctx.context_window <= 0 or ctx.request_tokens <= 0:
            return 70.0
        ratio = ctx.request_tokens / ctx.context_window
        if ratio > 0.95:
            return 10.0
        if ratio > 0.7:
            return 50.0
        if ratio < 0.05:
            return 60.0
        return 100.0

    def _density_score(self, ctx: ScoringContext) -> float:
        """连接密度: 同 provider 空闲越多分越高"""
        if ctx.same_provider_capacity <= 0:
            return 50.0
        active_ratio = ctx.same_provider_active / ctx.same_provider_capacity
        if active_ratio >= 1.0:
            return 10.0
        return 100.0 * (1.0 - active_ratio)

    def _reset_window_affinity_score(self, ctx: ScoringContext) -> float:
        """v4: 配额重置窗口临近度 (P1 激活, 当前占位 50.0)"""
        return 50.0

    # ── 主评分函数 ────────────────────────────────────────────

    def score(self, ctx: ScoringContext) -> ScoringResult:
        """计算综合评分 0-100 (12 因子)"""
        factor_computers = [
            ("health_score", self._health_score),
            ("quota_remaining", self._quota_score),
            ("cost_per_token", self._cost_score),
            ("latency_p95", self._latency_score),
            ("task_fit", self._task_fit_score),
            ("stability", self._stability_score),
            ("tier_priority", self._tier_score),
            ("tier_affinity", self._tier_affinity_score),
            ("specificity_match", self._specificity_match_score),
            ("context_affinity", self._context_affinity_score),
            ("connection_density", self._density_score),
            ("reset_window_affinity", self._reset_window_affinity_score),
        ]

        factors = []
        total = 0.0

        for name, func in factor_computers:
            raw = max(0.0, min(100.0, func(ctx)))
            weight = self.weights.get(name, 0.0)
            weighted = raw * weight
            total += weighted
            factors.append(FactorResult(
                name=name,
                weight=weight,
                raw_score=round(raw, 1),
                weighted=round(weighted, 2),
            ))

        return ScoringResult(
            total_score=round(total, 2),
            factors=factors,
            summary=self._summarize(factors, total),
        )

    def _summarize(self, factors: List[FactorResult], total: float) -> str:
        """生成简短摘要"""
        sorted_factors = sorted(factors, key=lambda f: f.raw_score)
        weak = sorted_factors[:2]
        weak_desc = ", ".join(f"{f.name}={f.raw_score:.0f}" for f in weak)
        tier = "A" if total >= 80 else "B" if total >= 60 else "C" if total >= 40 else "D"
        return f"[{tier}] {total:.1f} | weak: {weak_desc}"

    # ── 批量评分 ──────────────────────────────────────────────

    def score_candidates(
        self, candidates: List[ScoringContext]
    ) -> List[tuple[ScoringContext, ScoringResult]]:
        """批量评分 + 按总分降序排列"""
        results = [(c, self.score(c)) for c in candidates]
        results.sort(key=lambda x: x[1].total_score, reverse=True)
        return results

    def best_candidate(
        self, candidates: List[ScoringContext]
    ) -> Optional[tuple[ScoringContext, ScoringResult]]:
        """返回最高分候选项"""
        scored = self.score_candidates(candidates)
        return scored[0] if scored else None


# ── 便捷工厂函数 ──────────────────────────────────────────────

_scorer_cache: Dict[str, AutoComboScorer] = {}


def get_scorer(weights: Optional[Dict[str, float]] = None) -> AutoComboScorer:
    """获取全局评分器单例 (带缓存)"""
    key = str(sorted(weights.items())) if weights else "default"
    if key not in _scorer_cache:
        _scorer_cache[key] = AutoComboScorer(weights)
    return _scorer_cache[key]


def build_context(
    *,
    model_id: str = "",
    provider: str = "",
    modality: str = "text_only",
    health_state: str = "healthy",
    rolling_success_rate: float = 100.0,
    consecutive_fails: int = 0,
    ewma_latency_ms: float = 0.0,
    quota_used: int = 0,
    quota_total: int = 0,
    is_free: bool = True,
    cost_per_1m_input: float = 0.0,
    cost_per_1m_output: float = 0.0,
    capability_score: float = 50.0,
    tier_bonus: float = 0.0,
    context_window: int = 8192,
    latency_p95_ms: float = 0.0,
    latency_p50_ms: float = 0.0,
    latency_std_ms: float = 0.0,
    request_tokens: int = 0,
    task_type: str = "chat",
    request_tier: str = "standard",
    request_specialty: str = "",
    same_provider_active: int = 0,
    same_provider_capacity: int = 1,
    quota_reset_in_seconds: float = 0.0,
) -> ScoringContext:
    """快捷构建 ScoringContext"""
    return ScoringContext(
        model_id=model_id,
        provider=provider,
        modality=modality,
        health_state=health_state,
        rolling_success_rate=rolling_success_rate,
        consecutive_fails=consecutive_fails,
        ewma_latency_ms=ewma_latency_ms,
        quota_used=quota_used,
        quota_total=quota_total,
        is_free=is_free,
        cost_per_1m_input=cost_per_1m_input,
        cost_per_1m_output=cost_per_1m_output,
        capability_score=capability_score,
        tier_bonus=tier_bonus,
        context_window=context_window,
        latency_p95_ms=latency_p95_ms,
        latency_p50_ms=latency_p50_ms,
        latency_std_ms=latency_std_ms,
        request_tokens=request_tokens,
        task_type=task_type,
        request_tier=request_tier,
        request_specialty=request_specialty,
        same_provider_active=same_provider_active,
        same_provider_capacity=same_provider_capacity,
        quota_reset_in_seconds=quota_reset_in_seconds,
    )


# ── 引擎集成适配器 ───────────────────────────────────────────
# 将 engine.py 现有的数据转换为 ScoringContext + 调用 scorer

def compute_auto_combo_score(
    model_id: str,
    provider: str,
    *,
    capability_score: float = 50.0,
    tier_bonus: float = 0.0,
    context_window: int = 8192,
    # 来自 model_health
    health_state: str = "healthy",
    rolling_success_rate: float = 100.0,
    consecutive_fails: int = 0,
    ewma_latency_ms: float = 0.0,
    # 来自 pricing
    is_free: bool = True,
    cost_per_1m_input: float = 0.0,
    cost_per_1m_output: float = 0.0,
    # 来自 engine_stats
    latency_p95_ms: float = 0.0,
    latency_p50_ms: float = 0.0,
    latency_std_ms: float = 0.0,
    # 请求特征
    request_tokens: int = 0,
    task_type: str = "chat",
    request_tier: str = "standard",
    request_specialty: str = "",
    # 配额
    quota_used: int = 0,
    quota_total: int = 0,
    # 负载分散
    same_provider_active: int = 0,
    same_provider_capacity: int = 1,
    # 权重覆盖
    weights: Optional[Dict[str, float]] = None,
    # v4 新增
    quota_reset_in_seconds: float = 0.0,
) -> float:
    """
    v4: 12 因子评分 (老大 §4.2 钦定权重)
    对标 engine.py 现有 compute_combined_score() 的直接替换。
    返回 0-100 综合分。
    """
    scorer = get_scorer(weights)
    ctx = build_context(
        model_id=model_id,
        provider=provider,
        capability_score=capability_score,
        tier_bonus=tier_bonus,
        context_window=context_window,
        health_state=health_state,
        rolling_success_rate=rolling_success_rate,
        consecutive_fails=consecutive_fails,
        ewma_latency_ms=ewma_latency_ms,
        is_free=is_free,
        cost_per_1m_input=cost_per_1m_input,
        cost_per_1m_output=cost_per_1m_output,
        latency_p95_ms=latency_p95_ms,
        latency_p50_ms=latency_p50_ms,
        latency_std_ms=latency_std_ms,
        request_tokens=request_tokens,
        task_type=task_type,
        request_tier=request_tier,
        request_specialty=request_specialty,
        quota_used=quota_used,
        quota_total=quota_total,
        same_provider_active=same_provider_active,
        same_provider_capacity=same_provider_capacity,
        quota_reset_in_seconds=quota_reset_in_seconds,
    )
    result = scorer.score(ctx)
    return result.total_score