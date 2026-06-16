"""
free-model-router provider — 多 Key 轮询 + 健康跟踪

每个 Provider 封装:
- 多个 API key 的轮询 / 故障转移
- 健康状态 (healthy / degraded / unavailable)
- 连续失败计数 + 自动降级 / 恢复
- 槽位并发控制 (max_concurrent)
- 限流状态 (RateLimit state)

对标 JS 版 route-engine.js 的核心功能.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

LOG = logging.getLogger("fmr.provider")


# ── 状态枚举 (用字符串而非 Enum, 方便 JSON 序列化) ──

STATUS_HEALTHY = "healthy"
STATUS_DEGRADED = "degraded"
STATUS_UNAVAILABLE = "unavailable"


# ── Key 状态 ──


@dataclass
class KeyStats:
    """单个 API key 的使用统计"""
    key_masked: str
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    consecutive_failures: int = 0
    last_used: float = 0.0
    last_success: float = 0.0
    last_failure: float = 0.0
    in_cooldown: bool = False
    cooldown_until: float = 0.0

    def to_dict(self) -> dict:
        return {
            "key_masked": self.key_masked,
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "consecutive_failures": self.consecutive_failures,
            "last_used": self.last_used,
            "last_success": self.last_success,
            "last_failure": self.last_failure,
            "in_cooldown": self.in_cooldown,
        }


# ── Rate Limit 状态 ──


@dataclass
class RateLimitInfo:
    provider_id: str
    model_id: str
    rate_limited_at: float
    retry_after: float  # 秒

    def is_active(self, now: float | None = None) -> bool:
        now = now or time.time()
        return (now - self.rate_limited_at) < self.retry_after

    def remaining_seconds(self, now: float | None = None) -> float:
        now = now or time.time()
        return max(0.0, self.retry_after - (now - self.rate_limited_at))


# ── Provider ──


class Provider:
    """
    单个上游 provider 的封装.

    配置示例:
    {
      "base_url": "https://openrouter.ai/api/v1",
      "api_keys": ["sk-1", "sk-2", "sk-3"],
      "model_rules": {"mode": "pattern", "pattern": ".*free.*"},
      "max_concurrent": 3,
      "enabled": true,
    }
    """

    def __init__(self, provider_id: str, config: dict[str, Any],
                 health_check_fn=None):
        self.id = provider_id
        self.base_url = (config.get("base_url") or "").rstrip("/")
        self.api_keys: list[str] = [k for k in (config.get("api_keys") or []) if k]
        self.extra_headers: dict[str, str] = dict(config.get("headers") or {})
        self.max_concurrent = int(config.get("max_concurrent") or 3)
        self.enabled = bool(config.get("enabled", True))
        self.timeout = float(config.get("timeout") or 30.0)
        self.model_rules = dict(config.get("model_rules") or {})
        self.primary_model: str = config.get("primary_model") or ""
        self.fallback_models: list[str] = list(config.get("fallback_models") or [])
        self.disabled_models: list[str] = list(config.get("disabled_models") or [])
        self._free_models_cache: list[str] = list(config.get("models") or [])
        self._health_check_fn = health_check_fn

        # 运行态
        self._key_index = 0
        self._key_lock = asyncio.Lock()
        self._key_stats: list[KeyStats] = [
            KeyStats(key_masked=self._mask_key(k)) for k in self.api_keys
        ]
        self.status: str = STATUS_HEALTHY if self.enabled else STATUS_UNAVAILABLE
        self.consecutive_failures: int = 0
        self._slot_used: int = 0
        self._slot_lock = asyncio.Lock()
        self._rate_limits: dict[str, RateLimitInfo] = {}  # key: f"{model_id}"

    # ── Key 管理 ──

    @staticmethod
    def _mask_key(k: str) -> str:
        if len(k) <= 12:
            return k[:4] + "***" + k[-2:]
        return k[:6] + "..." + k[-4:]

    @property
    def key_count(self) -> int:
        return len(self.api_keys)

    @property
    def has_keys(self) -> bool:
        return len(self.api_keys) > 0

    async def pick_key(self) -> str | None:
        """轮询选一个可用 key, 跳过 cooldown 中的"""
        return await self.pick_key_excluding(exclude=set())

    async def pick_key_excluding(self, exclude: set[str] | None = None) -> str | None:
        """轮询选一个可用 key, 跳过 cooldown 中的 + 排除指定 keys

        Args:
            exclude: 已经试过且失败的 key 集合 (用于 401 后切 key)
        Returns:
            选中的 key, None 表示所有 key 都不可用
        """
        if not self.api_keys:
            return None
        exclude = exclude or set()
        async with self._key_lock:
            now = time.time()
            n = len(self.api_keys)
            # 优先选: 不在 cooldown 且不在 exclude 中
            for offset in range(n):
                idx = (self._key_index + offset) % n
                ks = self._key_stats[idx]
                if ks.in_cooldown and now < ks.cooldown_until:
                    continue
                if self.api_keys[idx] in exclude:
                    continue
                self._key_index = (idx + 1) % n
                return self.api_keys[idx]
            # 全部在 cooldown 或都在 exclude: 看是否有可强制恢复的 (cooldown 已过)
            for offset in range(n):
                idx = (self._key_index + offset) % n
                ks = self._key_stats[idx]
                if ks.in_cooldown and now < ks.cooldown_until:
                    continue  # 真在 cooldown
                if self.api_keys[idx] in exclude:
                    continue
                self._key_index = (idx + 1) % n
                return self.api_keys[idx]
            # 真没可用 key, 选最快恢复的 (给上层知道 provider 已无力)
            idx = min(range(n), key=lambda i: self._key_stats[i].cooldown_until)
            self._key_index = (idx + 1) % n
            return self.api_keys[idx]

    def has_available_key(self, exclude: set[str] | None = None) -> bool:
        """检查 provider 是否还有 (排除指定 keys 后) 可用 key"""
        exclude = exclude or set()
        now = time.time()
        for k, ks in zip(self.api_keys, self._key_stats):
            if k in exclude:
                continue
            if not ks.in_cooldown or now >= ks.cooldown_until:
                return True
        return False

    def get_key_index(self, key: str) -> int:
        for i, k in enumerate(self.api_keys):
            if k == key:
                return i
        return -1

    def record_key_success(self, key: str) -> None:
        i = self.get_key_index(key)
        if i < 0:
            return
        ks = self._key_stats[i]
        ks.total_requests += 1
        ks.successful_requests += 1
        ks.consecutive_failures = 0
        ks.last_used = time.time()
        ks.last_success = ks.last_used
        ks.in_cooldown = False

    def record_key_failure(self, key: str, http_code: int = 0,
                           retry_after: float = 0.0) -> None:
        i = self.get_key_index(key)
        if i < 0:
            return
        ks = self._key_stats[i]
        ks.total_requests += 1
        ks.failed_requests += 1
        ks.consecutive_failures += 1
        ks.last_used = time.time()
        ks.last_failure = ks.last_used
        # 401/403/429 → key 临时不可用
        if http_code in (401, 403, 429, 529):
            cooldown = retry_after if retry_after > 0 else 60.0
            if http_code == 429:
                cooldown = max(cooldown, 60.0)
            elif http_code in (401, 403):
                cooldown = max(cooldown, 300.0)
            ks.in_cooldown = True
            ks.cooldown_until = time.time() + cooldown
            LOG.warning("Provider %s key %s cooldown %ds (HTTP %d)",
                        self.id, ks.key_masked, int(cooldown), http_code)

    # ── 槽位管理 ──

    async def acquire_slot(self) -> bool:
        async with self._slot_lock:
            if self._slot_used >= self.max_concurrent:
                return False
            self._slot_used += 1
            return True

    async def release_slot(self) -> None:
        async with self._slot_lock:
            if self._slot_used > 0:
                self._slot_used -= 1

    @property
    def slot_used(self) -> int:
        return self._slot_used

    # ── 健康状态 ──

    def report_failure(self, model_id: str = "", error: str = "",
                       http_code: int = 0) -> None:
        self.consecutive_failures += 1
        threshold = 3
        if self.consecutive_failures >= threshold and self.fallback_models:
            if self.status != STATUS_DEGRADED:
                LOG.warning("Provider %s → degraded after %d failures",
                            self.id, self.consecutive_failures)
                self.status = STATUS_DEGRADED
        elif self.consecutive_failures >= threshold * 2:
            if self.status != STATUS_UNAVAILABLE:
                LOG.error("Provider %s → unavailable after %d failures",
                          self.id, self.consecutive_failures)
                self.status = STATUS_UNAVAILABLE

    def report_success(self, model_id: str = "", latency_ms: float = 0) -> None:
        if self.consecutive_failures > 0:
            self.consecutive_failures = max(0, self.consecutive_failures - 1)
        if self.status != STATUS_HEALTHY and self.consecutive_failures == 0:
            LOG.info("Provider %s → healthy", self.id)
            self.status = STATUS_HEALTHY

    # ── Rate Limit ──

    def mark_rate_limited(self, model_id: str, retry_after: float) -> None:
        self._rate_limits[model_id] = RateLimitInfo(
            provider_id=self.id,
            model_id=model_id,
            rate_limited_at=time.time(),
            retry_after=retry_after,
        )
        LOG.info("Provider %s model %s rate-limited, retry-after %.0fs",
                 self.id, model_id, retry_after)
        # 触发降级
        if self.status == STATUS_HEALTHY and self.fallback_models:
            self.status = STATUS_DEGRADED

    def is_model_rate_limited(self, model_id: str) -> bool:
        info = self._rate_limits.get(model_id)
        if not info:
            return False
        if not info.is_active():
            del self._rate_limits[model_id]
            return False
        return True

    def cleanup_expired_rate_limits(self) -> None:
        for mid in list(self._rate_limits.keys()):
            if not self.is_model_rate_limited(mid):
                pass  # 已删除

    # ── 模型选择 ──

    def get_free_models(self) -> list[str]:
        return list(self._free_models_cache)

    def set_free_models(self, model_ids: list[str]) -> None:
        self._free_models_cache = list(model_ids)
        # 如果 primary_model 不在 free models 中, 重新选
        if self.primary_model and self.primary_model not in self._free_models_cache:
            if self._free_models_cache:
                self.primary_model = self._free_models_cache[0]

    def select_model(self, skip_rate_limited: bool = True) -> str | None:
        """从免费模型中选一个, 优先 primary, 跳过限流的和禁用的"""
        candidates = [m for m in self._free_models_cache
                      if m not in self.disabled_models]
        if skip_rate_limited:
            candidates = [m for m in candidates if not self.is_model_rate_limited(m)]
        if not candidates:
            return None
        # 优先 primary
        if self.primary_model in candidates:
            return self.primary_model
        # 然后 fallback
        for m in self.fallback_models:
            if m in candidates:
                return m
        # 最后随机/顺序选
        return candidates[0]

    def disable_model(self, model_id: str, reason: str = "") -> None:
        if model_id not in self.disabled_models:
            self.disabled_models.append(model_id)
            LOG.info("Provider %s disabled model %s (%s)",
                     self.id, model_id, reason or "no reason")
        # 如果是 primary, 切到下一个
        if self.primary_model == model_id:
            available = [m for m in self._free_models_cache
                         if m not in self.disabled_models]
            if available:
                self.primary_model = available[0]
                # 补充 fallback
                self.fallback_models = available[1:3]
                LOG.info("Provider %s primary → %s (auto)",
                         self.id, self.primary_model)
            else:
                self.primary_model = ""
                self.fallback_models = []
                self.status = STATUS_UNAVAILABLE
                LOG.error("Provider %s no models left, → unavailable", self.id)

    def enable_model(self, model_id: str) -> None:
        if model_id in self.disabled_models:
            self.disabled_models.remove(model_id)
            LOG.info("Provider %s re-enabled model %s", self.id, model_id)

    # ── 健康检查 ──

    async def health_check(self) -> dict[str, Any]:
        """对 provider 发一个轻量请求检测可达性"""
        if not self.has_keys:
            return {"ok": False, "error": "no api_keys", "latency": 0}
        if not self.base_url:
            return {"ok": False, "error": "no base_url", "latency": 0}
        key = self.api_keys[0]
        url = f"{self.base_url}/models"
        headers = {"Authorization": f"Bearer {key}"}
        headers.update(self.extra_headers)
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
            latency = (time.time() - start) * 1000
            if 200 <= resp.status_code < 400:
                return {"ok": True, "status_code": resp.status_code,
                        "latency_ms": round(latency, 1)}
            return {"ok": False, "status_code": resp.status_code,
                    "latency_ms": round(latency, 1),
                    "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"ok": False, "error": str(e), "latency": 0}

    # ── 状态输出 ──

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "base_url": self.base_url,
            "enabled": self.enabled,
            "has_keys": self.has_keys,
            "key_count": self.key_count,
            "status": self.status,
            "consecutive_failures": self.consecutive_failures,
            "max_concurrent": self.max_concurrent,
            "slot_used": self._slot_used,
            "primary_model": self.primary_model,
            "fallback_models": self.fallback_models,
            "disabled_models": self.disabled_models,
            "free_model_count": len(self._free_models_cache),
            "model_rules": self.model_rules,
            "keys": [ks.to_dict() for ks in self._key_stats],
            "rate_limited_models": [
                {"model": mid, "remaining_s": round(info.remaining_seconds(), 1)}
                for mid, info in self._rate_limits.items() if info.is_active()
            ],
        }


# ── Manager: 统一管理多个 provider ──


class ProviderManager:
    """管理所有 provider 实例, 路由 / 选 key 统一入口"""

    def __init__(self, providers_config: dict[str, dict] | None = None):
        self.providers: dict[str, Provider] = {}
        if providers_config:
            self.update(providers_config)

    def update(self, providers_config: dict[str, dict]) -> None:
        """根据最新 config 增删改 provider"""
        # 新建 / 更新
        for pid, pcfg in providers_config.items():
            if pid in self.providers:
                # 更新可热改字段
                p = self.providers[pid]
                p.enabled = bool(pcfg.get("enabled", True))
                p.max_concurrent = int(pcfg.get("max_concurrent") or 3)
                p.model_rules = dict(pcfg.get("model_rules") or {})
                if pcfg.get("primary_model"):
                    p.primary_model = pcfg["primary_model"]
                if "fallback_models" in pcfg:
                    p.fallback_models = list(pcfg["fallback_models"])
                if "disabled_models" in pcfg:
                    p.disabled_models = list(pcfg["disabled_models"])
            else:
                self.providers[pid] = Provider(pid, pcfg)
        # 删除已移除的
        for pid in list(self.providers.keys()):
            if pid not in providers_config:
                del self.providers[pid]

    def get(self, provider_id: str) -> Provider | None:
        return self.providers.get(provider_id)

    def list_providers(self) -> list[Provider]:
        return list(self.providers.values())

    def active_providers(self) -> list[Provider]:
        return [p for p in self.providers.values()
                if p.enabled and p.has_keys and p.status != STATUS_UNAVAILABLE]

    async def health_check_all(self) -> dict[str, dict]:
        results = {}
        providers = self.list_providers()
        if not providers:
            return results
        tasks = [p.health_check() for p in providers]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        for p, out in zip(providers, outcomes):
            if isinstance(out, Exception):
                results[p.id] = {"ok": False, "error": str(out)}
            else:
                results[p.id] = out
                if out.get("ok"):
                    p.report_success()
                else:
                    p.report_failure(error=out.get("error", "health check failed"))
        return results

    def to_dict(self) -> dict[str, Any]:
        return {
            "providers": {pid: p.to_dict() for pid, p in self.providers.items()},
            "total": len(self.providers),
            "active": len(self.active_providers()),
        }
