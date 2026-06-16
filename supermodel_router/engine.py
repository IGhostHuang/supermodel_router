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

    # ── 核心路由 ────────────────────────────────────────

    def pick(self, requested_model: str, preferred_modalities: list[str] | None = None) -> RouteResult | None:
        """
        v3: 支持按模态路由
        - requested_model = "auto" / "" → 按 preferred_modalities 路由 (自动)
        - requested_model = "provider/model" → 精确匹配
        - requested_model 模糊 → 模糊匹配
        """
        self.registry.check_recovery()

        if not requested_model or requested_model.strip() in ("auto", ""):
            if preferred_modalities and self._get_modality_auto_routing():
                return self._pick_by_modality(preferred_modalities)
            return self._pick_quality()

        resolved = self.registry.resolve(requested_model)
        if resolved:
            pname, base_url, key = resolved
            actual_model = self._strip_provider_prefix(requested_model)
            return self._build_result(pname, base_url, key, actual_model, requested_model)

        # 模糊匹配
        models = self.registry.get_models()
        candidates = [m for m in models if requested_model.lower() in m.id.lower()]
        if candidates:
            scored = [(self._score_for(m), m) for m in candidates]
            scored.sort(key=lambda x: -x[0])
            for score, m in scored:
                key = self.registry.pick_key_for(m.provider)
                if key and self._acquire_slot(m.provider):
                    return self._build_result(
                        m.provider, m.base_url, key, m.id,
                        f"{m.provider}/{m.id}", score=score,
                        modality=m.modality,
                    )

        LOG.warning("Model '%s' not found, modality/quality routing", requested_model)
        if preferred_modalities and self._get_modality_auto_routing():
            return self._pick_by_modality(preferred_modalities)
        return self._pick_quality()

    def _pick_by_modality(self, preferred_modalities: list[str]) -> RouteResult | None:
        """
        按模态分组自动路由:
          1. 从 preferred_modalities 中依次匹配有模型的模态
          2. 在每个模态组内, 按综合评分排序取 top K
          3. 在 top K 内轮询 (round-robin)
          4. 没找到再 fallback 到全部质量路由
        """
        for modality in preferred_modalities:
            models = self.registry.get_models_by_modality(modality)
            if not models:
                continue

            # 按综合评分 = 健康*0.6 + 能力*0.4 + 模态匹配加分排序
            scored = []
            for m in models:
                q = self._score_for(m)
                c = m.capability_score
                # 所属模态正好是目标 → 加分
                is_match = (m.modality == modality)
                combined = compute_combined_score(q, c, modality_match=is_match, modality_boost=5.0)
                key = self.registry.pick_key_for(m.provider)
                if key and self._acquire_slot(m.provider):
                    route_key = f"{m.provider}/{m.id}"
                    if route_key not in self._disabled_models:
                        scored.append((combined, m, key))

            if scored:
                # 按综合分降序
                scored.sort(key=lambda x: -x[0])

                # 取 top K
                top_k = self._get_modality_top_k()
                top = scored[:min(top_k, len(scored))]

                # 在 top K 内轮询
                with self._lock:
                    idx = self._modality_rr.get(modality, 0)
                    self._modality_rr[modality] = (idx + 1) % len(top)
                    _, m, key = top[idx % len(top)]

                return self._build_result(
                    m.provider, m.base_url, key, m.id,
                    f"{m.provider}/{m.id}",
                    score=_,
                    modality=m.modality,
                )

        # fallback: quality routing
        LOG.info("No models found in modalities %s, fallback to quality routing", preferred_modalities)
        return self._pick_quality()

    def _pick_quality(self) -> RouteResult | None:
        """按质量评分 + 并发槽位可用性选最优模型"""
        routes = self.registry.all_routes()
        if not routes:
            LOG.error("No available models!")
            return None

        scored = []
        for route in routes:
            parts = route.split("/", 1)
            if len(parts) != 2:
                continue
            pname, mid = parts
            ps_obj = self.registry._providers.get(pname)
            if not ps_obj or ps_obj.degraded:
                continue
            if route in self._disabled_models:
                continue
            if not self._acquire_slot(pname):
                continue
            key = self.registry.pick_key_for(pname)
            if not key:
                continue
            score = self._score_for_model_obj(pname, mid)
            scored.append((score, pname, mid, key, ps_obj.base_url))

        if scored:
            scored.sort(key=lambda x: -x[0])
            score, pname, mid, key, base_url = scored[0]
            return self._build_result(pname, base_url, key, mid, f"{pname}/{mid}", score=score)

        # 全部没槽位或被禁, 硬选第一个
        LOG.warning("All routes busy/disabled, forcing first available")
        with self._lock:
            for route in routes:
                parts = route.split("/", 1)
                pname = parts[0]
                mid = parts[1] if len(parts) > 1 else route
                ps_obj = self.registry._providers.get(pname)
                if ps_obj:
                    key = self.registry.pick_key_for(pname)
                    if key:
                        return self._build_result(pname, ps_obj.base_url, key, mid, route)
        return None

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

        self._persist_stats()

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

    def _load_stats(self):
        path = os.path.join(self._stats_dir, STATS_FILE)
        if not os.path.exists(path):
            return
        try:
            with open(path) as f:
                data = json.load(f)
            for name, vals in data.items():
                s = ProviderStats(**vals)
                self._stats[name] = s
                today = time.strftime("%Y-%m-%d")
                if s.daily_reset_date != today:
                    s.daily_calls = 0
                    s.daily_tokens = 0
                    s.daily_failures = 0
                    s.daily_total_latency = 0
                    s.daily_reset_date = today
            LOG.info("Loaded stats for %d providers", len(data))
        except Exception as e:
            LOG.warning("Failed to load stats: %s", e)


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
                return {
                    "error": {"message": body_text[:500], "type": f"http_{resp.status_code}", "code": resp.status_code},
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
                    body = await resp.aread()
                    yield f'data: {json.dumps({"error": body.decode()[:500]})}\n\n'
                    return
                async for line in resp.aiter_lines():
                    if line:
                        yield line
                        yield "\n"
        except httpx.TimeoutException:
            yield f'data: {json.dumps({"error": "Upstream timeout"})}\n\n'