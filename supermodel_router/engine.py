"""
supermodel_router/engine.py — 路由引擎 v3: 质量评分 + 模态路由 + 并发槽位 + 错误分类
"""
import re
import json
import time
import asyncio  # v3.28: ModelScope 异步生图轮询用
import datetime
import logging
import random
import os
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from .config import Config
from .models import ModelRegistry
from .classifier import TEXT_ONLY, MULTIMODAL, IMAGE_GEN, VIDEO_GEN, AUDIO_GEN

LOG = logging.getLogger("engine")

# ── 常量 ──────────────────────────────────────────────────
STATS_FILE = "engine_stats.json"
PENALTY_FILE = "penalty_state.json"  # v3.2.0: 独立存储 penalty
EWMA_ALPHA = 0.5
DEFAULT_RECOVERY_INTERVAL = 300  # 5min 自动恢复
FIRST_TOKEN_TIMEOUT_DEFAULT = 15  # 15s
DEFAULT_MAX_SLOTS = 3


# ── 数据类 ────────────────────────────────────────────────

@dataclass
class ProviderStats:
    total_calls: int = 0
    success_calls: int = 0
    fail_calls: int = 0
    total_latency: float = 0.0
    total_first_token_latency: float = 0.0
    first_token_count: int = 0
    ewma_latency: float = 0.0
    ewma_first_token: float = 0.0
    last_call_time: float = 0.0
    last_success_time: float = 0.0
    daily_calls: int = 0
    daily_tokens: int = 0
    daily_failures: int = 0
    daily_total_latency: float = 0.0
    daily_reset_date: str = ""


@dataclass
class RouteResult:
    provider_name: str
    base_url: str
    api_key: str
    model_id: str
    full_model_path: str
    score: float = 0.0
    latency_ms: float = 0.0
    modality: str = TEXT_ONLY
    # v3.8.0: 上下文窗口 (供切链时压缩用, 0=未知)
    context_window: int = 0


@dataclass
class CandidateResult:
    """候选路由 (v4): (provider, model_id, key_index, score, modality)

    pick_chain 返回有序候选链, 同 model 全部 key 在前, 然后下一 model。
    app.py 在 5xx/timeout 时 traverse 链自动切下一个。
    """
    provider: str
    model_id: str
    key_index: int  # 0..len(api_keys)-1
    score: float  # 综合分 (含 penalty 折扣)
    capability_score: float
    modality: str = TEXT_ONLY
    penalty: float = 0.0
    # v3.8.0: 上下文窗口 (供切链时压缩用, 0=未知)
    context_window: int = 0

    @property
    def full_path(self) -> str:
        return f"{self.provider}/{self.model_id}"

    def materialize(self, registry) -> RouteResult | None:
        """用 registry 当前 base_url + api_key 实例化"""
        ps = registry._providers.get(self.provider)
        if not ps:
            return None
        if self.key_index >= len(ps.api_keys):
            return None
        api_key = ps.api_keys[self.key_index]
        return RouteResult(
            provider_name=self.provider,
            base_url=ps.base_url,
            api_key=api_key,
            model_id=self.model_id,
            full_model_path=self.full_path,
            score=self.score,
            modality=self.modality,
            context_window=self.context_window,
        )


# ── 错误分类 ──────────────────────────────────────────────

def classify_error(http_code: int, body_text: str = "") -> dict:
    body_lower = body_text.lower() if body_text else ""
    if http_code == 400:
        return {"retryable": True, "disable_model": False, "rate_limit": False, "retry_after": 0, "quota_exhausted": False, "quota_type": ""}
    elif http_code == 401:
        return {"retryable": False, "disable_model": True, "rate_limit": False, "retry_after": 0, "quota_exhausted": False, "quota_type": ""}
    elif http_code == 403:
        return {"retryable": True, "disable_model": False, "rate_limit": False, "retry_after": 0, "quota_exhausted": False, "quota_type": ""}
    elif http_code == 404:
        return {"retryable": False, "disable_model": True, "rate_limit": False, "retry_after": 0, "quota_exhausted": False, "quota_type": ""}
    elif http_code == 408:
        return {"retryable": True, "disable_model": False, "rate_limit": False, "retry_after": 0, "quota_exhausted": False, "quota_type": ""}
    elif http_code == 410:
        return {"retryable": False, "disable_model": True, "rate_limit": False, "retry_after": 0, "quota_exhausted": False, "quota_type": ""}
    elif http_code == 422:
        return {"retryable": True, "disable_model": False, "rate_limit": False, "retry_after": 0, "quota_exhausted": False, "quota_type": ""}
    elif http_code == 429:
        ra = 120
        quota_type = ""
        quota_exhausted = False
        # 日限额 → skip 24h (跟 model_health.quota_durations['daily']=86400 对齐, R65 伊芙发现)
        if "today" in body_lower or "daily" in body_lower or "今日" in body_lower or "日额度" in body_lower:
            ra = 86400
            quota_type = "daily"
            quota_exhausted = True
        # 周限额 → skip 7 天
        if "weekly" in body_lower or "week" in body_lower or "周" in body_lower:
            ra = 604800
            quota_type = "weekly"
            quota_exhausted = True
        # 月限额 → skip 30 天
        if "monthly" in body_lower or "billing" in body_lower or "充值" in body_lower or "月额度" in body_lower or "month" in body_lower:
            ra = 2592000
            quota_type = "monthly"
            quota_exhausted = True
        # 套餐限额 (TokenPlan) → skip 24h
        if "tokenplan" in body_lower or "套餐" in body_lower or "plan" in body_lower:
            ra = 86400
            quota_type = "token_plan"
            quota_exhausted = True
        # 账户余额不足 → skip 24h
        if "balance" in body_lower or "余额" in body_lower or "insufficient" in body_lower:
            ra = 86400
            quota_type = "balance"
            quota_exhausted = True
        return {"retryable": True, "disable_model": False, "rate_limit": True, "retry_after": ra, "quota_exhausted": quota_exhausted, "quota_type": quota_type}
    elif http_code >= 500:
        return {"retryable": True, "disable_model": False, "rate_limit": False, "retry_after": 0, "quota_exhausted": False, "quota_type": ""}
    return {"retryable": False, "disable_model": False, "rate_limit": False, "retry_after": 0, "quota_exhausted": False, "quota_type": ""}


# ── 评分 ──────────────────────────────────────────────────

def compute_quality_score(stats: dict) -> float:
    """
    0-100 分. 因子: 成功率 40%, EWMA 延迟 30%, 调用量 20%, 新鲜度 10%
    """
    if not stats:
        return 50.0
    success_rate = stats.get("success_rate", 0.5)
    latency = stats.get("ewma_latency_ms", 5000)
    call_count = stats.get("success_calls", 0)
    last_success_ago = stats.get("last_success_ago", 9999)

    sr_score = success_rate * 40
    lat_score = max(0, 30 - (latency - 500) / 9500 * 30) if latency > 500 else 30
    call_score = min(20, call_count / 10 * 20)
    freshness = 10 if last_success_ago < 60 else (5 if last_success_ago < 300 else 0)
    return sr_score + lat_score + call_score + freshness


def compute_combined_score(quality_score: float, capability_score: float,
                           modality_match: bool = False, modality_boost: float = 0.0) -> float:
    """
    综合评分 = 健康*0.6 + 能力*0.4 + 模态匹配加分
    同一模态组内, 健康度高的排前面
    """
    base = quality_score * 0.6 + capability_score * 0.4
    if modality_match:
        base += 15.0 + modality_boost
    return base


# ── 路由引擎 ──────────────────────────────────────────────

class RouteEngine:
    def __init__(self, cfg: Config, registry: ModelRegistry):
        self.cfg = cfg
        self.registry = registry
        self._rr_index = 0
        self._lock = threading.RLock()
        self._stats: dict[str, ProviderStats] = {}

        # 并发槽位: provider_name → {"max": N, "used": N}
        self._slots: dict[str, dict] = {}

        # 模型级错误计数
        self._model_fails: dict[str, int] = {}
        # v3.15.0: model health manager (由 app.py 在启动时注入, None = 不启用)
        self.model_health: Optional[Any] = None
        self._disabled_models: set[str] = set()

        # 模态轮询指针: modality → index
        self._modality_rr: dict[str, int] = {}

        # v4: 模型失败惩罚 (model_id 路径 → 0..0.9 penalty)
        # 失败时累加, 成功时清零, 定期 decay 恢复
        self._model_penalty: dict[str, float] = {}
        self._model_last_failure: dict[str, float] = {}

        # v3.23.0: FreeModelRegistry (由 app.py 在启动时注入, None = 不启用)
        # 老大 2026-06-27 钦定重点: free 模型识别 + 优先路由
        self.free_registry: Optional[Any] = None

        # v3.6: state_dir 走 config (集中管理)
        from .config import Config
        state_dir = "."
        if isinstance(cfg, Config):
            mm = (cfg.data.get("model_management") or {})
            state_dir = mm.get("state_dir", ".")
            try:
                os.makedirs(state_dir, exist_ok=True)
            except Exception:
                LOG.warning("engine: state_dir '%s' not creatable, fallback to '.'", state_dir)
                state_dir = "."
        self._stats_dir = state_dir
        self._load_stats()

    # ── 配置辅助 ────────────────────────────────────────

    def _get_routing_cfg(self, key: str, default=None):
        return self.cfg.routing.get(key, default)

    def _get_concurrent_slots(self, provider: str) -> int:
        ps = self.registry._providers.get(provider)
        if ps and hasattr(ps, 'max_concurrent') and ps.max_concurrent:
            return ps.max_concurrent
        return self.cfg.providers.get(provider, {}).get("max_concurrent_slots", DEFAULT_MAX_SLOTS)

    def _get_modality_auto_routing(self) -> bool:
        m = self.cfg.data.get("modality_auto_routing", {})
        return m.get("enabled", True)

    def _get_modality_top_k(self) -> int:
        routing = self.cfg.data.get("modality_auto_routing", {})
        return max(1, routing.get("top_k", 5))

    def _get_modality_routing_table(self) -> dict:
        routing = self.cfg.data.get("modality_auto_routing", {})
        return routing.get("routing_table", {})

    def _get_modality_fallback_order(self) -> list[str]:
        routing = self.cfg.data.get("modality_auto_routing", {})
        fallback = routing.get("fallback_order", ["multimodal", "text-only"])
        return fallback

    def _get_quality_weights(self) -> dict:
        return self.cfg.routing.get("quality_weights", {"success_rate": 0.6, "latency": 0.4})

    # ── 核心路由 (v4: pick_chain) ─────────────────────

    def pick(self, requested_model: str, preferred_modalities: list[str] | None = None) -> RouteResult | None:
        """
        v4: pick() = pick_chain()[0].materialize()
        兼容旧 API, 但实际走候选链逻辑。
        """
        self.registry.check_recovery()
        chain = self.pick_chain(requested_model, preferred_modalities, max_candidates=1)
        if not chain:
            return None
        return chain[0].materialize(self.registry)

    def pick_chain(self, requested_model: str, preferred_modalities: list[str] | None = None,
                   max_candidates: int = 20,
                   strategy: str = "flat",
                   groups: Optional[Dict[str, str]] = None,
                   group_weights: Optional[Dict[str, float]] = None) -> list[CandidateResult]:
        """
        v4: 新轮询机制 (老大 09:48 拍):
        - 高分模型第一个 key 不通 → 换下一个 key
        - 同 provider 全部 key 该模型不通 → 换下一个 model
        - 同时降低该模型分数 (penalty), 避免下一次又从头轮询
        - 定期复测更新分数 (decay)

        v3.9.0 (Phase H): 加 strategy 参数, 4 种轮询策略
        - flat: 老 v4 行为, 按综合分 (capability + quality - penalty + modality) 全局降序
        - round-robin-group: 按 group 分桶, 每个 bucket 单独排序, 然后桶间轮询
        - group-failover: 按 group 优先级, group A 全失败才 group B
        - group-weighted: 按 group_weights 加权随机决定 group 顺序 (高 weight 先抽)

        返回候选链: [(m1, key0), (m1, key1), (m2, key0), ...]
        按综合分 (capability + quality + modality match - penalty) 降序。
        """
        self.registry.check_recovery()

        candidates_models = self._collect_candidate_models(
            requested_model, preferred_modalities
        )
        if not candidates_models:
            LOG.warning("No candidate models for request: %s", requested_model)
            return []

        scored = []
        for m in candidates_models:
            base_capability = m.capability_score or 0.0
            base_quality = self._score_for(m)  # 0-100, 含历史成功率
            path = f"{m.provider}/{m.id}"
            penalty = self._model_penalty.get(path, 0.0)

            modality_boost = 0.0
            if preferred_modalities and m.modality in preferred_modalities:
                modality_boost = 15.0

            combined = (base_capability * 0.5 + base_quality * 0.5) * (1.0 - penalty)
            combined += modality_boost
            scored.append((combined, m, penalty, path))

        # v3.20.0 (SMR 周天循环): 应用当前星期的权重配置
        scored = self._apply_weekly_weights(scored)

        # v3.23.0 (L1 Free Resource): free 模型优先加成 (老大钦定重点)
        # 注入 free_registry 后, 提升 free 模型优先级 (auto_skip dead free)
        if self.free_registry is not None:
            boosted = []
            for combined, m, penalty, path in scored:
                boost = self.free_registry.get_priority_boost(path)
                if boost <= -1.0:
                    # dead free model → 跳过
                    continue
                boosted.append((combined + boost * 5, m, penalty, path))  # 放大 5x 让 free 显著优先
            scored = boosted

        # v3.9.0 (Phase H): 按 strategy 决定排序方式
        ordered = self._order_by_strategy(scored, strategy=strategy,
                                          groups=groups or {},
                                          group_weights=group_weights or {})

        # v3.15.0: 健康度过滤 — 跳过 SKIP 模型, DEGRADED 降权
        if self.model_health is not None:
            ordered = self._filter_by_health(ordered)

        chain: list[CandidateResult] = []  # type: ignore[name-defined]  # noqa
        seen_keys: set[tuple[str, str, int]] = set()
        for score, m, penalty, path in ordered:
            ps = self.registry._providers.get(m.provider)
            if not ps or not ps.api_keys:
                continue
            for ki in range(len(ps.api_keys)):
                key = (m.provider, m.id, ki)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                chain.append(CandidateResult(
                    provider=m.provider,
                    model_id=m.id,
                    key_index=ki,
                    score=score,
                    capability_score=m.capability_score or 0.0,
                    modality=m.modality,
                    penalty=penalty,
                    context_window=m.context_window,  # v3.8.0: 透传 ModelInfo.context_window
                ))
                if len(chain) >= max_candidates:
                    return chain

        return chain

    def _order_by_strategy(self, scored, strategy: str,
                           groups: Dict[str, str],
                           group_weights: Dict[str, float]) -> list:
        """v3.9.0 (Phase H): 按 strategy 排序 scored 列表

        scored: List[(combined_score, ModelInfo, penalty, path)]
        groups: Dict[path, group_name]  (model → group mapping)
        group_weights: Dict[group_name, weight] (用于 weighted 策略)
        """
        if strategy == "flat" or not groups:
            # 老 v4 行为: 全局按 combined 降序
            return sorted(scored, key=lambda x: -x[0])

        # 按 group 分桶
        UNGROUPED = "__ungrouped__"
        buckets: Dict[str, list] = defaultdict(list)
        for s in scored:
            path = s[3]
            gname = groups.get(path, UNGROUPED)
            buckets[gname].append(s)

        # bucket 内按 score 降序
        for b in buckets.values():
            b.sort(key=lambda x: -x[0])

        if strategy == "round-robin-group":
            # 桶间轮询: round 1 每个桶 1 个, round 2 每个桶再 1 个, ...
            ordered = []
            while any(buckets.values()):
                added = False
                # 按 group name 稳定排序 (确定性, 调试友好)
                for gname in sorted(buckets.keys()):
                    if buckets[gname]:
                        ordered.append(buckets[gname].pop(0))
                        added = True
                if not added:
                    break
            return ordered

        if strategy == "group-failover":
            # 按 group 优先级 (字母序, ungrouped 最后), 整桶一次性返回
            ordered = []
            # 优先有名字的 group (字母序), 然后 ungrouped
            named_groups = sorted(g for g in buckets.keys() if g != UNGROUPED)
            for gname in named_groups + ([UNGROUPED] if UNGROUPED in buckets else []):
                ordered.extend(buckets[gname])
            return ordered

        if strategy == "group-weighted":
            # 加权采样: 按 group_weights 概率抽 group, 抽完一轮重抽
            # weight 缺省 = 1.0
            ordered = []
            group_names = list(buckets.keys())
            if not group_names:
                return []
            weights = [group_weights.get(g, 1.0) for g in group_names]
            while any(buckets.values()):
                available = [(i, g) for i, g in enumerate(group_names) if buckets[g]]
                if not available:
                    break
                avail_idx = [i for i, _ in available]
                avail_weights = [weights[i] for i in avail_idx]
                chosen_idx = random.choices(avail_idx, weights=avail_weights, k=1)[0]
                chosen_gname = group_names[chosen_idx]
                ordered.append(buckets[chosen_gname].pop(0))
            return ordered

        # 未知 strategy → 退到 flat (安全降级)
        LOG.warning("Unknown strategy '%s', falling back to 'flat'", strategy)
        return sorted(scored, key=lambda x: -x[0])

    def _collect_candidate_models(self, requested_model: str,
                                   preferred_modalities: list[str] | None) -> list:
        """收集候选 model: alias / auto / provider/model / 模糊匹配 / 全部

        v3.17.0: 新增 alias 机制 (老大原话 "API 调用统一的模型名, SMR 内部路由决定实际模型")
        """
        # 0) v3.17.0: alias 解析 (优先级最高 — 'model-router' / 'cheap' / 'fast' 等)
        alias_cfg = self._resolve_alias(requested_model)
        if alias_cfg:
            models = self._apply_alias_strategy(alias_cfg, preferred_modalities)
            LOG.info("engine: alias '%s' resolved to %d models (strategy=%s)",
                     requested_model, len(models), alias_cfg.get("strategy"))
            return models

        # 1) auto / 空 model
        if not requested_model or requested_model.strip() in ("auto", ""):
            if preferred_modalities and self._get_modality_auto_routing():
                return self.registry.get_models_by_modality(preferred_modalities[0])
            return self.registry.get_models()

        # 2) 精确匹配 (provider/model)
        if "/" in requested_model:
            resolved = self.registry.resolve(requested_model)
            if resolved:
                pname, _, _ = resolved
                mid = self._strip_provider_prefix(requested_model)
                for m in self.registry.get_models():
                    if m.provider == pname and m.id == mid:
                        return [m]

        # 3) 模糊匹配
        models = self.registry.get_models()
        matches = [m for m in models if requested_model.lower() in m.id.lower()]
        if matches:
            return matches

        # 4) fallback: 按 modality
        if preferred_modalities and self._get_modality_auto_routing():
            return self.registry.get_models_by_modality(preferred_modalities[0])
        return models

    def _resolve_alias(self, requested_model: str) -> dict | None:
        """v3.17.0: alias 解析 — 把统一 model 名 ('model-router' 等) 转成 routing 配置"""
        if not requested_model:
            return None
        aliases = self.cfg.get_model_aliases()
        return aliases.get(requested_model)

    def _apply_alias_strategy(self, alias_cfg: dict,
                                preferred_modalities: list[str] | None) -> list:
        """v3.17.0: 按 alias.strategy 过滤 + 排序 model 列表

        返回排序后的 model 列表 (best 排第一, SMR pick_chain 按此链切链)
        """
        models = self.registry.get_models()
        # modality 过滤
        if preferred_modalities:
            models = [m for m in models if m.modality in preferred_modalities]
        elif alias_cfg.get("modality"):
            models = [m for m in models if m.modality == alias_cfg["modality"]]
        # capability 过滤
        min_cap = alias_cfg.get("min_capability_score", 0)
        if min_cap > 0:
            models = [m for m in models if (m.capability_score or 0) >= min_cap]
        # exclude providers
        excl = alias_cfg.get("exclude_providers", []) or []
        if excl:
            models = [m for m in models if m.provider not in excl]
        # 排除 disabled provider (跟 registry._providers.enabled 同步)
        models = [m for m in models if self._is_provider_enabled(m.provider)]
        # 按 strategy 排序
        strategy = alias_cfg.get("strategy", "best_quality")
        if strategy == "best_quality":
            # 综合: quality_score 优先, 然后 capability_score, 排除 latency 高的
            prefer_low_latency = alias_cfg.get("prefer_low_latency", True)
            models.sort(key=lambda m: (
                -(m.quality_score or 0),                  # 1. quality_score 高
                -(m.capability_score or 0),                # 2. capability 高
                -(m.speed_score or 0) if prefer_low_latency else 0,  # 3. 速度快
                m.id                                       # 4. id 稳定排序
            ))
        elif strategy == "free_only":
            # 只 free 模型 (openrouter :free 后缀 / extra.pricing == "0" / provider 默认 free) + 按 quality 排序
            models = [m for m in self._filter_free_models(models)]
            models.sort(key=lambda m: -(m.quality_score or 0))
        elif strategy == "lowest_latency":
            # 按 ewma_latency 升序 (从 engine stats 取 — ProviderStats 是 dataclass, 用 getattr)
            stats = self._get_all_stats()
            def _lat(m):
                ps = stats.get(m.provider)
                if ps is None:
                    return 99999.0
                return float(getattr(ps, "ewma_latency", 99999.0) or 99999.0)
            models.sort(key=_lat)
            max_lat = alias_cfg.get("max_latency_ms", 99999.0) * 1000
            models = [m for m in models if _lat(m) <= max_lat or _lat(m) >= 99999.0]  # 未调用过的 (99999) 保留
        elif strategy == "modality_auto":
            pass
        elif strategy == "random":
            import random as _r
            _r.shuffle(models)
        return models

    def _is_provider_enabled(self, provider_name: str) -> bool:
        """v3.17.0: 检查 provider 是否 enabled (跟 registry._providers 同步)"""
        ps = self.registry._providers.get(provider_name)
        return bool(getattr(ps, "enabled", True)) if ps else False

    def _filter_free_models(self, models: list) -> list:
        """v3.17.0: 过滤 free 模型 (openrouter :free 后缀 / pricing="0" / provider 标记)"""
        FREE_PROVIDERS = {"openrouter", "nvidia", "modelscope"}  # 已知默认 free 的 provider
        FREE_SUFFIXES = (":free", "/free")
        out = []
        for m in models:
            mid = m.id.lower()
            # openrouter 格式: "xxx:free"
            if any(mid.endswith(s) for s in FREE_SUFFIXES):
                out.append(m)
                continue
            # extra.pricing 字段 ("0" = free, openrouter 标准)
            pricing = (m.extra or {}).get("pricing", {}) if hasattr(m, "extra") else {}
            if isinstance(pricing, dict):
                prompt_p = str(pricing.get("prompt", "1"))
                completion_p = str(pricing.get("completion", "1"))
                if prompt_p == "0" and completion_p == "0":
                    out.append(m)
                    continue
            # provider 默认 free (e.g. NVIDIA, 魔塔免费模型)
            if m.provider in FREE_PROVIDERS and ":free" not in mid:
                # 大部分 NVIDIA 模型免费 (通过 NVIDIA NIM API), 但有些收费
                # 用 tags 辅助判断
                if "free" in (m.tags or []) or "preview" in (m.tags or []):
                    out.append(m)
        return out

    def _get_all_stats(self) -> dict:
        """返所有 provider stats (alias strategy=lowest_latency 用)"""
        return dict(self._stats)

    def _build_result(self, pname, base_url, key, model_id, full_path,
                      score=0.0, modality=TEXT_ONLY):
        stats = self._get_stats(pname)
        return RouteResult(
            provider_name=pname,
            base_url=base_url,
            api_key=key,
            model_id=model_id,
            full_model_path=full_path,
            score=score,
            latency_ms=stats.ewma_latency * 1000 if stats.ewma_latency > 0 else 0,
            modality=modality,
        )

    def _score_for(self, model) -> float:
        return self._score_for_model_obj(model.provider, model.id)

    def _score_for_model_obj(self, provider: str, model_id: str) -> float:
        stats = self._get_stats(provider)
        if stats.total_calls == 0:
            return 50.0
        success_rate = stats.success_calls / stats.total_calls if stats.total_calls > 0 else 0.5
        ewma_ms = stats.ewma_latency * 1000
        last_success_ago = time.time() - stats.last_success_time if stats.last_success_time > 0 else 9999
        score_stats = {
            "success_rate": success_rate,
            "ewma_latency_ms": ewma_ms,
            "success_calls": stats.success_calls,
            "last_success_ago": last_success_ago,
        }
        return compute_quality_score(score_stats)

    # ── 并发槽位 ────────────────────────────────────────

    def _acquire_slot(self, provider: str) -> bool:
        with self._lock:
            slots = self._slots.get(provider)
            if slots is None:
                slots = {"max": self._get_concurrent_slots(provider), "used": 0}
                self._slots[provider] = slots
            if slots["used"] < slots["max"]:
                slots["used"] += 1
                return True
            return False

    def release_slot(self, provider: str):
        with self._lock:
            slots = self._slots.get(provider)
            if slots and slots["used"] > 0:
                slots["used"] -= 1

    def _strip_provider_prefix(self, model: str) -> str:
        if "/" in model:
            return model.split("/", 1)[1]
        return model

    # ── 模型禁用/恢复 ────────────────────────────────────

    def disable_model(self, route_key: str):
        self._disabled_models.add(route_key)
        LOG.warning("Model permanently disabled: %s", route_key)

    def is_model_disabled(self, route_key: str) -> bool:
        return route_key in self._disabled_models

    # ── 记录成功/失败 ────────────────────────────────────

    def record_success(self, provider: str, latency: float, first_token_latency: float = 0):
        self.registry.mark_ok(provider)
        stats = self._get_stats(provider)
        stats.total_calls += 1
        stats.success_calls += 1
        stats.total_latency += latency
        stats.last_call_time = time.time()
        stats.last_success_time = time.time()
        stats.daily_calls += 1
        stats.daily_total_latency += latency

        if stats.ewma_latency == 0:
            stats.ewma_latency = latency
        else:
            stats.ewma_latency = EWMA_ALPHA * latency + (1 - EWMA_ALPHA) * stats.ewma_latency

        if first_token_latency > 0:
            stats.total_first_token_latency += first_token_latency
            stats.first_token_count += 1
            if stats.ewma_first_token == 0:
                stats.ewma_first_token = first_token_latency
            else:
                stats.ewma_first_token = (
                    EWMA_ALPHA * first_token_latency
                    + (1 - EWMA_ALPHA) * stats.ewma_first_token
                )

        # v4: 清零该 provider 上所有 model penalty (成功了 → 模型恢复)
        prefix = provider + "/"
        cleared = [k for k in self._model_penalty if k.startswith(prefix)]
        for k in cleared:
            old = self._model_penalty.pop(k)
            if old > 0.01:
                LOG.info("model penalty cleared (success): %s (was %.2f)", k, old)

        self._persist_stats()

        # v3.15.0: 联动 model_health (per-model 维度, 比 provider 维度更细)
        if self.model_health is not None:
            try:
                self.model_health.record_success(f"{provider}", latency_ms=latency * 1000)
            except Exception as e:
                LOG.warning("model_health.record_success failed: %s", e)

    def record_failure(self, provider: str, model_id: str = "", http_code: int = 0, body_text: str = ""):
        self.registry.mark_fail(provider)
        stats = self._get_stats(provider)
        stats.total_calls += 1
        stats.fail_calls += 1
        stats.last_call_time = time.time()
        stats.daily_calls += 1
        stats.daily_failures += 1

        if http_code > 0:
            cls = classify_error(http_code, body_text)
            if cls["disable_model"] and model_id:
                route_key = f"{provider}/{model_id}"
                self.disable_model(route_key)

        # v4: model penalty 累加 (避免下次又从头轮询高分模型)
        if model_id:
            path = f"{provider}/{model_id}"
            old_penalty = self._model_penalty.get(path, 0.0)
            if http_code in (401, 403):
                inc = 0.3  # 鉴权失败 — 严重
            elif http_code == 429:
                inc = 0.2  # 限流 — 中等
            elif http_code >= 500:
                inc = 0.15  # 服务端错误 — 较轻
            elif http_code > 0:
                inc = 0.1  # 其他 4xx — 轻
            else:
                inc = 0.1  # 网络/超时
            self._model_penalty[path] = min(0.9, old_penalty + inc)
            self._model_last_failure[path] = time.time()
            LOG.warning("model penalty +%.2f → %.2f: %s (http=%d)",
                        inc, self._model_penalty[path], path, http_code)

        self._persist_stats()

        # v3.15.0: 联动 model_health
        if self.model_health is not None and model_id:
            try:
                err_summary = body_text[:80] if body_text else f"http={http_code}"
                # v3.18.0: 配额耗尽 → 传 quota_type 给 model_health
                quota_exhausted = False
                quota_type = ""
                if http_code > 0:
                    cls = classify_error(http_code, body_text)
                    quota_exhausted = cls.get("quota_exhausted", False)
                    quota_type = cls.get("quota_type", "")
                self.model_health.record_failure(
                    f"{provider}/{model_id}",
                    latency_ms=0.0,
                    error=err_summary,
                    quota_exhausted=quota_exhausted,
                    quota_type=quota_type,
                )
            except Exception as e:
                LOG.warning("model_health.record_failure failed: %s", e)

    # ── v4: penalty 状态 + 周期复测 ─────────────────────

    # v3.15.0: model health filter helper
    def _filter_by_health(self, ordered: list) -> list:
        """按 model_health 过滤候选：SKIP 跳过，DEGRADED 降权

        ordered: List[(combined_score, ModelInfo, penalty, path)]
        返回：过滤 + 降权后的列表
        """
        if self.model_health is None:
            return ordered
        filtered = []
        skipped = []
        for score, m, penalty, path in ordered:
            if self.model_health.should_skip(path):
                skipped.append(path)
                continue
            mult = self.model_health.get_penalty_multiplier(path)
            if mult < 1.0:
                # DEGRADED: 降权 score + 累加 penalty
                score = score * mult
                penalty = min(0.9, penalty + (1 - mult) * 0.5)
            filtered.append((score, m, penalty, path))
        if skipped:
            LOG.info("engine: model_health filtered %d unhealthy: %s", len(skipped), skipped[:5])
        return filtered

    # v3.20.0 (周天循环): 应用每周权重配置
    def _apply_weekly_weights(self, scored: list) -> list:
        """根据当前星期几应用周天权重配置

        scored: List[(combined_score, ModelInfo, penalty, path)]
        返回：应用权重后的 scored 列表

        配置位置：config.yaml → routing.weekly_schedule.<weekday>["provider/model"] = weight
        weekday: 0=Monday .. 6=Sunday (datetime.utcnow().weekday())
        """
        schedule = self.cfg.routing.get("weekly_schedule", {})
        if not schedule:
            return scored  # 无配置 → 保持原始分数

        # 使用 UTC 时间 (与 cron tick 一致)
        today = datetime.datetime.utcnow().weekday()
        day_cfg = schedule.get(str(today), {}) or schedule.get(today, {})
        if not day_cfg:
            return scored  # 当天无配置 → 保持原始分数

        adjusted = []
        for combined, m, penalty, path in scored:
            weight = float(day_cfg.get(path, 1.0))
            if weight != 1.0:
                LOG.debug("weekly_weight: %s weekday=%d weight=%.2f (base=%.1f → adj=%.1f)",
                          path, today, weight, combined, combined * weight)
            adjusted_score = combined * weight
            adjusted.append((adjusted_score, m, penalty, path))
        return adjusted

    def get_model_penalty(self) -> dict:
        """返回所有 model penalty 状态 (admin 用)"""
        return {
            "penalties": dict(self._model_penalty),
            "last_failures": dict(self._model_last_failure),
            "recovery_interval_seconds": self.cfg.routing.get("recovery_interval", 300),
            "decay_step": self.cfg.routing.get("penalty_decay_step", 0.1),
        }

    def reset_model_penalty(self, model_path: str | None = None) -> dict:
        """清零 penalty (admin 手动复测 / 强制恢复)
        model_path=None → 清空所有
        """
        if model_path is None:
            cleared = len(self._model_penalty)
            self._model_penalty.clear()
            self._model_last_failure.clear()
            LOG.info("all model penalties cleared (count=%d)", cleared)
            return {"ok": True, "cleared": cleared}
        if model_path in self._model_penalty:
            old = self._model_penalty.pop(model_path)
            self._model_last_failure.pop(model_path, None)
            LOG.info("model penalty reset: %s (was %.2f)", model_path, old)
            return {"ok": True, "cleared": 1, "previous": old}
        return {"ok": True, "cleared": 0}

    def decay_model_penalty(self) -> int:
        """v4 周期复测: 减少所有 penalty, 触发"复测更新分数"
        - 距 last_failure > recovery_interval 的 model: penalty - decay_step
        - penalty 降到 0 → 移除
        返回更新的 model 数
        """
        interval = self.cfg.routing.get("recovery_interval", 300)
        decay_step = self.cfg.routing.get("penalty_decay_step", 0.1)
        now = time.time()
        updated = 0
        with self._lock:
            for path in list(self._model_penalty.keys()):
                last_fail = self._model_last_failure.get(path, 0)
                if now - last_fail < interval:
                    continue
                old = self._model_penalty[path]
                new = max(0.0, old - decay_step)
                if new <= 0.001:
                    del self._model_penalty[path]
                    self._model_last_failure.pop(path, None)
                    LOG.info("model penalty fully recovered (decay): %s", path)
                else:
                    self._model_penalty[path] = new
                    LOG.info("model penalty decay: %s %.2f → %.2f", path, old, new)
                updated += 1
        if updated:
            self._persist_stats()
        return updated

    # ── 模型失败/成功 (v4: penalty) ─────────────────────

    def _get_stats(self, provider: str) -> ProviderStats:
        if provider not in self._stats:
            self._stats[provider] = ProviderStats()
            today = time.strftime("%Y-%m-%d")
            self._stats[provider].daily_reset_date = today
        return self._stats[provider]

    def get_stats(self) -> dict:
        result = {}
        for name, s in self._stats.items():
            avg_lat = (s.total_latency / s.success_calls * 1000) if s.success_calls > 0 else 0
            avg_ft = (s.total_first_token_latency / s.first_token_count * 1000) if s.first_token_count > 0 else 0
            result[name] = {
                "total_calls": s.total_calls,
                "success_calls": s.success_calls,
                "fail_calls": s.fail_calls,
                "avg_latency_ms": round(avg_lat, 1),
                "avg_first_token_ms": round(avg_ft, 1),
                "ewma_latency_ms": round(s.ewma_latency * 1000, 1),
                "ewma_first_token_ms": round(s.ewma_first_token * 1000, 1),
                "daily_calls": s.daily_calls,
                "daily_tokens": s.daily_tokens,
                "daily_failures": s.daily_failures,
                "quality_score": round(self._score_for_model_obj(name, ""), 1),
            }
        return result

    # ── 统计持久化 ───────────────────────────────────────

    def _persist_stats(self):
        try:
            data = {}
            for name, s in self._stats.items():
                data[name] = {
                    "total_calls": s.total_calls,
                    "success_calls": s.success_calls,
                    "fail_calls": s.fail_calls,
                    "total_latency": s.total_latency,
                    "total_first_token_latency": s.total_first_token_latency,
                    "first_token_count": s.first_token_count,
                    "ewma_latency": s.ewma_latency,
                    "ewma_first_token": s.ewma_first_token,
                    "last_call_time": s.last_call_time,
                    "last_success_time": s.last_success_time,
                    "daily_calls": s.daily_calls,
                    "daily_tokens": s.daily_tokens,
                    "daily_failures": s.daily_failures,
                    "daily_total_latency": s.daily_total_latency,
                    "daily_reset_date": s.daily_reset_date,
                }
            path = os.path.join(self._stats_dir, STATS_FILE)
            with open(path, "w") as f:
                json.dump(data, f)
        except Exception:
            pass
        # v3.2.0: 独立持久化 penalty state (跟 engine_stats.json 分离, 避免大文件频繁重写)
        try:
            penalty_path = os.path.join(self._stats_dir, PENALTY_FILE)
            with open(penalty_path, "w") as f:
                json.dump({
                    "penalties": dict(self._model_penalty),
                    "last_failures": dict(self._model_last_failure),
                }, f)
        except Exception:
            pass

    def _load_stats(self):
        # v3.2.0: 读 ProviderStats (含 daily_reset_date 跨日重置)
        path = os.path.join(self._stats_dir, STATS_FILE)
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                today = time.strftime("%Y-%m-%d")
                for name, vals in data.items():
                    s = ProviderStats(**vals)
                    if s.daily_reset_date != today:
                        s.daily_calls = 0
                        s.daily_tokens = 0
                        s.daily_failures = 0
                        s.daily_total_latency = 0
                        s.daily_reset_date = today
                    self._stats[name] = s
                LOG.info("Loaded stats for %d providers", len(data))
            except Exception as e:
                LOG.warning("Failed to load stats: %s", e)
        # v3.2.0: 读 penalty state (失败 → 保留空 dict, 不影响启动)
        penalty_path = os.path.join(self._stats_dir, PENALTY_FILE)
        if os.path.exists(penalty_path):
            try:
                with open(penalty_path) as f:
                    data = json.load(f)
                self._model_penalty.update(data.get("penalties", {}))
                self._model_last_failure.update(data.get("last_failures", {}))
                LOG.info("Loaded penalty state: %d entries", len(self._model_penalty))
            except Exception:
                LOG.exception("penalty state load failed")


# ── v3: 模态感知的 proxy 转发 ──────────────────────────────

async def proxy_chat_request(
    route: RouteResult,
    body: dict,
    stream: bool = False,
    timeout: float = 300,
) -> dict | AsyncGenerator:
    """
    发送 chat/completions 请求, 自动处理不同模态的特殊请求体构造
    """
    headers = {
        "Content-Type": "application/json",
    }
    if route.provider_name == "volc_ark":
        headers["api-key"] = route.api_key
    else:
        headers["Authorization"] = f"Bearer {route.api_key}"

    payload = {**body, "model": route.model_id}

    base_url = route.base_url.rstrip("/")
    url = f"{base_url}/chat/completions"

    if stream:
        return _proxy_stream(url, headers, payload, timeout)
    else:
        return await _proxy_normal(url, headers, payload, timeout, route)


async def proxy_images_generations(
    route: RouteResult,
    body: dict,
    timeout: float = 120,
) -> dict:
    """发送 images/generations 请求到生图模型

    v3.28: 加 ModelScope 异步模式分支 (api-inference.modelscope.cn 必须 X-ModelScope-Async-Mode)
    """
    headers = {
        "Content-Type": "application/json",
    }
    if route.provider_name == "volc_ark":
        headers["api-key"] = route.api_key
    else:
        headers["Authorization"] = f"Bearer {route.api_key}"

    payload = {**body, "model": route.model_id}
    base_url = route.base_url.rstrip("/")

    # v3.28: ModelScope 必须异步 (同步模式 API 直接 400)
    if "modelscope.cn" in base_url:
        return await _proxy_modelscope_async(base_url, headers, payload, route, timeout=timeout)

    # v3.28: HuggingFace inference API — 同步返回 image bytes, 转 OpenAI 格式
    if "huggingface.co" in base_url:
        return await _proxy_huggingface(base_url, headers, payload, route, timeout=timeout)

    # v3.28: DashScope 阿里云百炼 (OpenAI 兼容模式) — sync 直接可用
    if "dashscope.aliyuncs.com" in base_url:
        return await _proxy_dashscope(base_url, headers, payload, route, timeout=timeout)

    url = f"{base_url}/images/generations"

    async with httpx.AsyncClient() as client:
        t0 = time.time()
        try:
            resp = await client.post(url, json=payload, headers=headers, timeout=timeout)
            elapsed = time.time() - t0
            if resp.status_code != 200:
                return {
                    "error": {"message": resp.text[:500], "type": f"http_{resp.status_code}"},
                    "_router": {"provider": route.provider_name, "model": route.model_id,
                                "latency_ms": round(elapsed * 1000, 1)},
                }
            result = resp.json()
            result["_router"] = {
                "provider": route.provider_name,
                "model": route.model_id,
                "latency_ms": round(elapsed * 1000, 1),
            }
            return result
        except Exception as e:
            return {"error": {"message": str(e), "type": "proxy_error"}}


async def _proxy_dashscope(
    base_url: str,
    headers: dict,
    payload: dict,
    route: RouteResult,
    timeout: float = 180,
) -> dict:
    """v3.28: DashScope 阿里云百炼 图像生成 (OpenAI 兼容模式)

    端点: {base_url}/images/generations  (跟 OpenAI 一致)
    流程:
    1. POST /images/generations 同步
       - 文生图: {"model": "wanx-v1", "prompt": "..."}
       - 图生图: {"model": "qwen-image-edit", "prompt": "...", "image_url": "..."}
    2. 响应: OpenAI 风格 JSON {"created": ts, "data": [{"url": "..."}]}
    """
    url = f"{base_url.rstrip('/')}/images/generations"

    # DashScope OpenAI 兼容模式: 用 Bearer auth
    auth_headers = {
        "Authorization": headers.get("Authorization", ""),
        "Content-Type": "application/json",
    }

    # payload 标准化: SMR 已有 prompt + image_url 字段, DashScope 直接支持
    # 但 image_url 字段是 DashScope 内部 API 命名, OpenAI 兼容模式可能不同
    # 兼容两种情况: 标准 image_url (qwen-image-edit) 或 messages 风格
    if "image_url" in payload and payload["image_url"]:
        # 保持 image_url 字段, qwen-image-edit 直接支持
        pass

    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=auth_headers, timeout=timeout)
        elapsed = time.time() - t0

        if resp.status_code != 200:
            return {
                "error": {"message": resp.text[:500],
                          "type": f"http_{resp.status_code}"},
                "_router": {"provider": route.provider_name,
                            "model": route.model_id,
                            "latency_ms": round(elapsed * 1000, 1)},
            }

        result = resp.json()
        result["_router"] = {
            "provider": route.provider_name,
            "model": route.model_id,
            "latency_ms": round(elapsed * 1000, 1),
        }
        return result
    except Exception as e:
        return {"error": {"message": str(e), "type": "dashscope_proxy_error"},
                "_router": {"provider": route.provider_name, "model": route.model_id,
                            "latency_ms": round((time.time() - t0) * 1000, 1)}}


async def _proxy_huggingface(
    base_url: str,
    headers: dict,
    payload: dict,
    route: RouteResult,
    timeout: float = 180,
) -> dict:
    """v3.28: HuggingFace Inference API 图像生成/编辑

    流程:
    1. POST /models/{model_id} 同步
       - text2img: {"inputs": "prompt", "parameters": {...}}
       - img2img: {"inputs": base64_image, "parameters": {"prompt": "..."}}
    2. 响应: image/png bytes (NOT JSON)
    3. 转 OpenAI 格式 + base64 内嵌 (无 external URL)
    """
    model_id = route.model_id
    url = f"{base_url.rstrip('/')}/{model_id}"

    # 转换 payload: SMR image_url → HF inputs + parameters
    # prompt / image_url 字段标准化
    hf_inputs = payload.get("prompt", "")
    hf_params = {}
    if "image_url" in payload and payload["image_url"]:
        # img2img 模式: inputs = base64 string, parameters.prompt = instruction
        img_url = payload["image_url"]
        # 剥 data URI 前缀
        if img_url.startswith("data:image/") and ";base64," in img_url:
            img_b64 = img_url.split(";base64,", 1)[1]
        else:
            img_b64 = img_url  # 假设纯 base64
        # instruct-pix2pix: inputs = base64 string, parameters.prompt = instruction
        # SDXL img2img: inputs = base64 string, parameters = {prompt, strength, ...}
        # 但 HF API 文档: image-to-image 实际上 inputs 字段就是 image bytes, parameters.prompt 是文本
        hf_inputs = img_b64
        if hf_inputs.startswith("data:image"):
            # 有些模型接受 data URI, instruct-pix2pix 是这种
            pass
        hf_params["prompt"] = payload.get("prompt", "")
        # copy 其他 parameters
        for k in ("negative_prompt", "guidance_scale", "num_inference_steps", "strength"):
            if k in payload:
                hf_params[k] = payload[k]
    elif "messages" in payload:
        # chat-style fallback (e.g. 内嵌消息)
        msgs = payload["messages"]
        for m in msgs:
            if m.get("role") == "user":
                content = m.get("content", "")
                if isinstance(content, str):
                    hf_inputs = content
                elif isinstance(content, list):
                    # 多模态: 找 text 和 image_url
                    for part in content:
                        if part.get("type") == "text":
                            hf_inputs = part.get("text", "")
                        elif part.get("type") == "image_url":
                            url_v = part.get("image_url", {}).get("url", "")
                            if url_v.startswith("data:image/"):
                                hf_inputs = url_v.split(";base64,", 1)[1] if ";base64," in url_v else url_v
                                hf_params["prompt"] = payload.get("prompt", "") or hf_inputs
                                break
                break

    # 构造 request body
    if hf_params:
        body = {"inputs": hf_inputs, "parameters": hf_params}
    else:
        body = {"inputs": hf_inputs}

    # 同步调用 HF API
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                json=body,
                headers={"Authorization": headers.get("Authorization", ""),
                         "Content-Type": "application/json"},
                timeout=timeout,
            )
        elapsed = time.time() - t0

        if resp.status_code != 200:
            return {
                "error": {"message": resp.text[:500],
                          "type": f"http_{resp.status_code}"},
                "_router": {"provider": route.provider_name,
                            "model": route.model_id,
                            "latency_ms": round(elapsed * 1000, 1)},
            }

        # 检查 Content-Type
        ct = resp.headers.get("Content-Type", "")
        if "image/" in ct:
            # 真返回 image bytes — 转 base64 内嵌
            import base64
            b64 = base64.b64encode(resp.content).decode()
            data_uri = f"data:{ct};base64,{b64}"
            return {
                "created": int(time.time()),
                "data": [{"url": None, "b64_json": data_uri}],
                "_router": {
                    "provider": route.provider_name,
                    "model": route.model_id,
                    "latency_ms": round(elapsed * 1000, 1),
                },
            }

        # JSON 响应 (可能含 errors)
        try:
            result = resp.json()
        except Exception:
            return {
                "error": {"message": f"HF returned non-image non-JSON: {ct}, body[:300]={resp.text[:300]}"},
                "_router": {"provider": route.provider_name, "model": route.model_id,
                            "latency_ms": round(elapsed * 1000, 1)},
            }

        # 可能是 error JSON
        if isinstance(result, dict) and "error" in result:
            return {
                "error": result["error"] if isinstance(result["error"], dict)
                        else {"message": str(result["error"])[:300],
                              "type": "huggingface_error"},
                "_router": {"provider": route.provider_name, "model": route.model_id,
                            "latency_ms": round(elapsed * 1000, 1)},
            }

        # 数组形式 (HF 老 API)
        if isinstance(result, list) and result:
            return {
                "created": int(time.time()),
                "data": [{"url": None,
                          "b64_json": item.get("generated_text", None) if isinstance(item, dict) else None}
                         for item in result],
                "_router": {"provider": route.provider_name, "model": route.model_id,
                            "latency_ms": round(elapsed * 1000, 1)},
            }

        return {
            "created": int(time.time()),
            "data": [{"url": None, "b64_json": json.dumps(result)[:300]}],
            "_router": {"provider": route.provider_name, "model": route.model_id,
                        "latency_ms": round(elapsed * 1000, 1)},
        }
    except Exception as e:
        return {"error": {"message": str(e), "type": "huggingface_proxy_error"},
                "_router": {"provider": route.provider_name, "model": route.model_id,
                            "latency_ms": round((time.time() - t0) * 1000, 1)}}


async def _proxy_modelscope_async(
    base_url: str,
    headers: dict,
    payload: dict,
    route: RouteResult,
    timeout: float = 120,
    poll_interval: float = 1.5,
    max_polls: int = 80,  # 80 * 1.5s = 120s 上限
) -> dict:
    """v3.28: ModelScope 异步生图 (image-gen 必须异步)

    流程:
    1. POST /v1/images/generations + X-ModelScope-Async-Mode: true
       → 返回 task_id
    2. GET /v1/tasks/{task_id} 轮询 status
       → SUCCEED → 拿 image_urls
       → FAILED → 报失败
    3. 转成 OpenAI 格式 {"created": ts, "data": [{"url": "..."}]}
    """
    submit_url = f"{base_url}/images/generations"
    submit_headers = {**headers, "X-ModelScope-Async-Mode": "true"}

    t0 = time.time()
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            # Step 1: 提交异步任务
            sub_resp = await client.post(submit_url, json=payload, headers=submit_headers, timeout=30)
            if sub_resp.status_code != 200:
                return {
                    "error": {"message": f"submit failed: {sub_resp.text[:300]}",
                              "type": f"http_{sub_resp.status_code}"},
                    "_router": {"provider": route.provider_name, "model": route.model_id,
                                "latency_ms": round((time.time() - t0) * 1000, 1)},
                }
            sub_data = sub_resp.json()
            task_id = sub_data.get("task_id") or sub_data.get("taskId") or sub_data.get("id")
            if not task_id:
                return {
                    "error": {"message": f"no task_id in submit response: {json.dumps(sub_data)[:300]}",
                              "type": "modelscope_no_task_id"},
                    "_router": {"provider": route.provider_name, "model": route.model_id,
                                "latency_ms": round((time.time() - t0) * 1000, 1)},
                }

            # Step 2: 轮询状态
            task_url = f"{base_url}/tasks/{task_id}"
            task_headers = {**headers}  # Bearer token 走 headers
            for i in range(max_polls):
                if time.time() - t0 > timeout:
                    return {
                        "error": {"message": f"task {task_id} timeout after {timeout}s",
                                  "type": "modelscope_timeout"},
                        "_router": {"provider": route.provider_name, "model": route.model_id,
                                    "task_id": task_id,
                                    "latency_ms": round((time.time() - t0) * 1000, 1)},
                    }
                await asyncio.sleep(poll_interval)
                poll_resp = await client.get(task_url, headers=task_headers, timeout=15)
                if poll_resp.status_code != 200:
                    continue  # 网络抖动, 继续轮询
                task_data = poll_resp.json()
                status = (task_data.get("task_status")
                          or task_data.get("status")
                          or task_data.get("state")
                          or "").upper()

                if status in ("SUCCEED", "SUCCESS", "COMPLETED", "FINISHED"):
                    # Step 3: 拿 image_urls 转 OpenAI 格式
                    image_urls = (task_data.get("output_images")
                                  or task_data.get("output", {}).get("images")
                                  or task_data.get("images")
                                  or task_data.get("results")
                                  or [])
                    # 也可能 image_urls 是 dict list with 'url' key
                    if image_urls and isinstance(image_urls[0], dict):
                        image_urls = [x.get("url") or x.get("image_url") or x.get("image") for x in image_urls]
                    b64s = (task_data.get("output_images_base64")
                            or task_data.get("output", {}).get("images_base64")
                            or [])
                    data_list = []
                    for u in image_urls:
                        data_list.append({"url": u, "b64_json": None})
                    for b in b64s:
                        data_list.append({"url": None, "b64_json": b})
                    if not data_list:
                        return {
                            "error": {"message": f"task SUCCEED but no images: {json.dumps(task_data)[:300]}",
                                      "type": "modelscope_no_images"},
                            "_router": {"provider": route.provider_name, "model": route.model_id,
                                        "task_id": task_id,
                                        "latency_ms": round((time.time() - t0) * 1000, 1)},
                        }
                    return {
                        "created": int(time.time()),
                        "data": data_list,
                        "_router": {
                            "provider": route.provider_name,
                            "model": route.model_id,
                            "task_id": task_id,
                            "async": True,
                            "polls": i + 1,
                            "latency_ms": round((time.time() - t0) * 1000, 1),
                        },
                    }
                if status in ("FAILED", "FAILURE", "ERROR"):
                    return {
                        "error": {"message": task_data.get("message") or task_data.get("error") or f"task FAILED: {json.dumps(task_data)[:300]}",
                                  "type": "modelscope_task_failed"},
                        "_router": {"provider": route.provider_name, "model": route.model_id,
                                    "task_id": task_id,
                                    "latency_ms": round((time.time() - t0) * 1000, 1)},
                    }
                # PENDING / RUNNING / PROCESSING → 继续轮询

            return {
                "error": {"message": f"task {task_id} exhausted {max_polls} polls",
                          "type": "modelscope_polls_exhausted"},
                "_router": {"provider": route.provider_name, "model": route.model_id,
                            "task_id": task_id,
                            "latency_ms": round((time.time() - t0) * 1000, 1)},
            }
        except Exception as e:
            return {"error": {"message": str(e), "type": "modelscope_proxy_error"},
                    "_router": {"provider": route.provider_name, "model": route.model_id,
                                "latency_ms": round((time.time() - t0) * 1000, 1)}}


async def _proxy_normal(url, headers, payload, timeout, route) -> dict:
    async with httpx.AsyncClient() as client:
        t0 = time.time()
        try:
            resp = await client.post(url, json=payload, headers=headers, timeout=timeout)
            elapsed = time.time() - t0
            body_text = resp.text

            if resp.status_code != 200:
                # v4 修复: parse JSON error 或提取 status reason, 避免 raw HTTP header 污染
                ctype = resp.headers.get("content-type", "")
                error_msg = f"HTTP {resp.status_code} {resp.reason_phrase}"
                # 优先尝试 resp.json() (标准 OpenAI 格式)
                try:
                    err_body = resp.json()
                    if isinstance(err_body, dict):
                        err_obj = err_body.get("error")
                        if isinstance(err_obj, dict):
                            error_msg = err_obj.get("message", error_msg)
                        elif err_obj is not None:
                            error_msg = str(err_obj)[:200]
                except Exception:
                    # 失败: raw body 含 status line + headers (e.g. proxy 注入)
                    # regex 提取最后一个 {"error": {...}} 或纯 body 部分
                    m = re.search(rb'\{"error":\s*\{[^}]*(?:"[^"]*"\s*:\s*"[^"]*"\s*[,}])*', body_text.encode())
                    if m:
                        try:
                            err_obj = json.loads(m.group(0) + b"}")
                            if isinstance(err_obj.get("error"), dict):
                                error_msg = err_obj["error"].get("message", error_msg)
                        except Exception:
                            pass
                return {
                    "error": {"message": error_msg, "type": f"http_{resp.status_code}", "code": resp.status_code},
                    "_router": {"provider": route.provider_name, "model": route.model_id,
                                "full_path": route.full_model_path, "latency_ms": round(elapsed * 1000, 1),
                                "http_status": resp.status_code},
                }

            result = resp.json()
            result["_router"] = {
                "provider": route.provider_name,
                "model": route.model_id,
                "full_path": route.full_model_path,
                "latency_ms": round(elapsed * 1000, 1),
                "http_status": 200,
            }
            return result
        except httpx.TimeoutException:
            return {
                "error": {"message": "Upstream timeout", "type": "timeout"},
                "_router": {"provider": route.provider_name, "model": route.model_id,
                            "latency_ms": round((time.time() - t0) * 1000, 1)},
            }


async def _proxy_stream(url, headers, payload, timeout) -> AsyncGenerator[str, None]:
    async with httpx.AsyncClient() as client:
        try:
            async with client.stream("POST", url, json=payload, headers=headers, timeout=timeout) as resp:
                if resp.status_code != 200:
                    # v4 修复: 4xx/5xx 抛异常, 让 app.py chain rotation 接住切下一个候选
                    body = await resp.aread()
                    err_msg = f"HTTP {resp.status_code}: {body.decode(errors='replace')[:300]}"
                    raise httpx.HTTPStatusError(err_msg, request=resp.request, response=resp)
                async for line in resp.aiter_lines():
                    if line:
                        yield line
                        yield "\n"
        except httpx.TimeoutException:
            # timeout 也抛, 让 app.py chain rotation 切链
            raise httpx.TimeoutException("Upstream stream timeout")