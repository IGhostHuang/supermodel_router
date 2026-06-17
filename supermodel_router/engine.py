"""
supermodel_router/engine.py — 路由引擎 v3: 质量评分 + 模态路由 + 并发槽位 + 错误分类
"""
import re
import json
import time
import logging
import random
import os
import threading
from dataclasses import dataclass, field
from typing import AsyncGenerator

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
        )


# ── 错误分类 ──────────────────────────────────────────────

def classify_error(http_code: int, body_text: str = "") -> dict:
    body_lower = body_text.lower() if body_text else ""
    if http_code == 400:
        return {"retryable": True, "disable_model": False, "rate_limit": False, "retry_after": 0}
    elif http_code == 401:
        return {"retryable": False, "disable_model": True, "rate_limit": False, "retry_after": 0}
    elif http_code == 403:
        return {"retryable": True, "disable_model": False, "rate_limit": False, "retry_after": 0}
    elif http_code == 404:
        return {"retryable": False, "disable_model": True, "rate_limit": False, "retry_after": 0}
    elif http_code == 408:
        return {"retryable": True, "disable_model": False, "rate_limit": False, "retry_after": 0}
    elif http_code == 410:
        return {"retryable": False, "disable_model": True, "rate_limit": False, "retry_after": 0}
    elif http_code == 422:
        return {"retryable": True, "disable_model": False, "rate_limit": False, "retry_after": 0}
    elif http_code == 429:
        ra = 120
        if "today" in body_lower or "daily" in body_lower or "今日" in body_lower:
            ra = 3600
        if "monthly" in body_lower or "billing" in body_lower or "充值" in body_lower:
            ra = 86400
        return {"retryable": True, "disable_model": False, "rate_limit": True, "retry_after": ra}
    elif http_code >= 500:
        return {"retryable": True, "disable_model": False, "rate_limit": False, "retry_after": 0}
    return {"retryable": False, "disable_model": False, "rate_limit": False, "retry_after": 0}


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
        self._disabled_models: set[str] = set()

        # 模态轮询指针: modality → index
        self._modality_rr: dict[str, int] = {}

        # v4: 模型失败惩罚 (model_id 路径 → 0..0.9 penalty)
        # 失败时累加, 成功时清零, 定期 decay 恢复
        self._model_penalty: dict[str, float] = {}
        self._model_last_failure: dict[str, float] = {}

        self._stats_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), ".."
        )
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
                   max_candidates: int = 20) -> list[CandidateResult]:
        """
        v4 新轮询机制 (老大 09:48 拍):
        - 高分模型第一个 key 不通 → 换下一个 key
        - 同 provider 全部 key 该模型不通 → 换下一个 model
        - 同时降低该模型分数 (penalty), 避免下一次又从头轮询
        - 定期复测更新分数 (decay)

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

        scored.sort(key=lambda x: -x[0])

        chain: list[CandidateResult] = []
        seen_keys: set[tuple[str, str, int]] = set()
        for score, m, penalty, path in scored:
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
                ))
                if len(chain) >= max_candidates:
                    return chain

        return chain

    def _collect_candidate_models(self, requested_model: str,
                                   preferred_modalities: list[str] | None) -> list:
        """收集候选 model: auto / provider/model / 模糊匹配 / 全部"""
        if not requested_model or requested_model.strip() in ("auto", ""):
            if preferred_modalities and self._get_modality_auto_routing():
                return self.registry.get_models_by_modality(preferred_modalities[0])
            return self.registry.get_models()

        # 精确匹配 (provider/model)
        if "/" in requested_model:
            resolved = self.registry.resolve(requested_model)
            if resolved:
                pname, _, _ = resolved
                mid = self._strip_provider_prefix(requested_model)
                for m in self.registry.get_models():
                    if m.provider == pname and m.id == mid:
                        return [m]

        # 模糊匹配
        models = self.registry.get_models()
        matches = [m for m in models if requested_model.lower() in m.id.lower()]
        if matches:
            return matches

        # fallback: 按 modality
        if preferred_modalities and self._get_modality_auto_routing():
            return self.registry.get_models_by_modality(preferred_modalities[0])
        return models

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

    # ── v4: penalty 状态 + 周期复测 ─────────────────────

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
        "Authorization": f"Bearer {route.api_key}",
        "Content-Type": "application/json",
    }

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
    """发送 images/generations 请求到生图模型"""
    headers = {
        "Authorization": f"Bearer {route.api_key}",
        "Content-Type": "application/json",
    }

    payload = {**body, "model": route.model_id}
    base_url = route.base_url.rstrip("/")
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