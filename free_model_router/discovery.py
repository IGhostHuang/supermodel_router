"""
free-model-router model discovery — auto-fetch available models from /v1/models

支持 4 种模式 (model_rules.mode):
  - all:      拉取所有模型, 全部视为免费
  - pattern:  拉取所有, 正则匹配过滤出免费模型
  - include:  拉取所有, 白名单匹配视为免费
  - exclude:  拉取所有, 黑名单排除后其余视为免费

Discovery backends:
  - HTTPDiscovery:    调用 provider 的 GET /v1/models 拉取列表
  - StaticDiscovery:  从本地 JSON/字典读取 (离线场景)
  - ManualDiscovery:  手动指定模型 ID 列表

支持定时刷新 + 内存缓存.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import httpx

from .filter import filter_models

LOG = logging.getLogger("fmr.discovery")


@dataclass
class DiscoveredModel:
    """从 provider 拉取的模型信息"""
    id: str
    name: str = ""
    owned_by: str = ""
    context_window: int = 0
    max_tokens: int = 0
    modalities: list[str] = field(default_factory=lambda: ["text"])
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name or self.id,
            "owned_by": self.owned_by,
            "context_window": self.context_window,
            "max_tokens": self.max_tokens,
            "modalities": self.modalities,
        }


@dataclass
class DiscoveryResult:
    """一次发现的结果"""
    provider_id: str
    fetched_at: float
    all_models: list[DiscoveredModel]      # provider 返回的全部模型
    free_models: list[DiscoveredModel]     # 经过 filter 后的免费模型
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "fetched_at": self.fetched_at,
            "model_count": len(self.all_models),
            "free_model_count": len(self.free_models),
            "all_models": [m.id for m in self.all_models],
            "free_models": [m.id for m in self.free_models],
            "error": self.error,
        }


# ── 抽象基类 ──


class DiscoveryStrategy(ABC):
    """所有 discovery 后端的基类"""

    @abstractmethod
    async def discover(self, provider_id: str, provider_cfg: dict) -> DiscoveryResult:
        """拉取 provider 的模型列表"""
        raise NotImplementedError


# ── HTTP 自动发现 ──


class HTTPDiscovery(DiscoveryStrategy):
    """调用 provider 的 GET /v1/models 拉取模型列表"""

    def __init__(self, timeout: float = 15.0):
        self._timeout = timeout

    async def discover(self, provider_id: str, provider_cfg: dict) -> DiscoveryResult:
        base_url = (provider_cfg.get("base_url") or "").rstrip("/")
        api_keys = [k for k in (provider_cfg.get("api_keys") or []) if k]
        if not base_url:
            return DiscoveryResult(provider_id, time.time(), [], [],
                                   error="base_url is empty")
        if not api_keys:
            return DiscoveryResult(provider_id, time.time(), [], [],
                                   error="no api_keys configured")

        url = f"{base_url}/models"
        headers = {"Authorization": f"Bearer {api_keys[0]}"}
        custom = provider_cfg.get("headers") or {}
        if isinstance(custom, dict):
            headers.update(custom)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code >= 400:
                    return DiscoveryResult(
                        provider_id, time.time(), [], [],
                        error=f"HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                data = resp.json()
        except httpx.TimeoutException:
            return DiscoveryResult(provider_id, time.time(), [], [],
                                   error="request timeout")
        except Exception as e:
            return DiscoveryResult(provider_id, time.time(), [], [],
                                   error=f"request failed: {e}")

        raw_models = self._parse_openai_models(data)
        free_models = self._apply_filter(raw_models, provider_cfg)
        return DiscoveryResult(
            provider_id=provider_id,
            fetched_at=time.time(),
            all_models=raw_models,
            free_models=free_models,
        )

    @staticmethod
    def _parse_openai_models(data: Any) -> list[DiscoveredModel]:
        """解析 OpenAI 兼容的 /v1/models 响应"""
        items = []
        if isinstance(data, dict) and "data" in data:
            items = data["data"] or []
        elif isinstance(data, list):
            items = data
        out: list[DiscoveredModel] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            mid = it.get("id") or it.get("name") or ""
            if not mid:
                continue
            out.append(DiscoveredModel(
                id=str(mid),
                name=str(it.get("name") or mid),
                owned_by=str(it.get("owned_by") or ""),
                context_window=int(it.get("context_window") or 0),
                max_tokens=int(it.get("max_tokens") or 0),
                modalities=list(it.get("modalities") or ["text"]),
                raw=it,
            ))
        return out

    @staticmethod
    def _apply_filter(models: list[DiscoveredModel], provider_cfg: dict) -> list[DiscoveredModel]:
        rules = provider_cfg.get("model_rules") or {}
        mode = rules.get("mode", "all")
        model_dicts = [{"id": m.id, **m.raw} for m in models]
        kept = filter_models(
            model_dicts,
            mode=mode,
            pattern=rules.get("pattern", ""),
            include=rules.get("include") or [],
            exclude=rules.get("exclude") or [],
        )
        kept_ids = {m["id"] for m in kept}
        return [m for m in models if m.id in kept_ids]


# ── 静态发现 ──


class StaticDiscovery(DiscoveryStrategy):
    """从本地 JSON 文件或字典读取模型列表"""

    def __init__(self, models_source: dict[str, list[str]] | str | Path | None = None):
        self._source = models_source
        self._cache: dict[str, list[str]] = {}
        if isinstance(models_source, (str, Path)):
            self._load_file(Path(models_source))
        elif isinstance(models_source, dict):
            self._cache = {k: list(v) for k, v in models_source.items()}

    def _load_file(self, path: Path) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._cache = {k: list(v) for k, v in data.items() if isinstance(v, list)}
        except Exception as e:
            LOG.error("Failed to load static models from %s: %s", path, e)

    def set_models(self, provider_id: str, model_ids: Iterable[str]) -> None:
        self._cache[provider_id] = list(model_ids)

    async def discover(self, provider_id: str, provider_cfg: dict) -> DiscoveryResult:
        ids = self._cache.get(provider_id) or provider_cfg.get("models") or []
        models = [DiscoveredModel(id=str(mid), name=str(mid)) for mid in ids]
        free = self._apply_filter(models, provider_cfg)
        return DiscoveryResult(
            provider_id=provider_id,
            fetched_at=time.time(),
            all_models=models,
            free_models=free,
        )

    @staticmethod
    def _apply_filter(models: list[DiscoveredModel], provider_cfg: dict) -> list[DiscoveredModel]:
        rules = provider_cfg.get("model_rules") or {}
        mode = rules.get("mode", "all")
        model_dicts = [{"id": m.id} for m in models]
        kept = filter_models(
            model_dicts,
            mode=mode,
            pattern=rules.get("pattern", ""),
            include=rules.get("include") or [],
            exclude=rules.get("exclude") or [],
        )
        kept_ids = {m["id"] for m in kept}
        return [m for m in models if m.id in kept_ids]


# ── 手动指定 ──


class ManualDiscovery(DiscoveryStrategy):
    """provider_cfg['models'] 直接列出的模型 ID"""

    async def discover(self, provider_id: str, provider_cfg: dict) -> DiscoveryResult:
        ids = provider_cfg.get("models") or []
        models = [DiscoveredModel(id=str(mid), name=str(mid)) for mid in ids]
        free = StaticDiscovery._apply_filter(models, provider_cfg)
        return DiscoveryResult(
            provider_id=provider_id,
            fetched_at=time.time(),
            all_models=models,
            free_models=free,
        )


# ── Manager: 协调多个 provider 的发现 + 缓存 ──


class DiscoveryManager:
    """管理所有 provider 的发现流程, 提供缓存 + 定时刷新"""

    def __init__(self, strategy: DiscoveryStrategy | None = None,
                 cache_ttl: float = 3600.0):
        self._strategy = strategy or HTTPDiscovery()
        self._cache: dict[str, DiscoveryResult] = {}
        self._ttl = cache_ttl
        self._lock = asyncio.Lock()
        self._refresh_task: asyncio.Task | None = None

    @property
    def strategy(self) -> DiscoveryStrategy:
        return self._strategy

    def set_strategy(self, strategy: DiscoveryStrategy) -> None:
        self._strategy = strategy

    async def discover_all(self, providers: dict[str, dict],
                           force: bool = False) -> dict[str, DiscoveryResult]:
        """发现所有 provider 的模型. force=True 跳过缓存."""
        results: dict[str, DiscoveryResult] = {}
        now = time.time()
        for pid, pcfg in providers.items():
            if not (pcfg.get("enabled", True)):
                continue
            cached = self._cache.get(pid)
            if not force and cached and (now - cached.fetched_at) < self._ttl:
                results[pid] = cached
                continue
            try:
                r = await self._strategy.discover(pid, pcfg)
            except Exception as e:
                LOG.exception("Discovery failed for %s", pid)
                r = DiscoveryResult(pid, now, [], [], error=str(e))
            self._cache[pid] = r
            results[pid] = r
            LOG.info("Discovery[%s]: %d free / %d total%s",
                     pid, len(r.free_models), len(r.all_models),
                     f" err={r.error}" if r.error else "")
        return results

    def get_cached(self, provider_id: str) -> DiscoveryResult | None:
        return self._cache.get(provider_id)

    def get_free_models(self, provider_id: str) -> list[str]:
        r = self._cache.get(provider_id)
        if not r:
            return []
        return [m.id for m in r.free_models]

    def get_all_models(self, provider_id: str) -> list[str]:
        r = self._cache.get(provider_id)
        if not r:
            return []
        return [m.id for m in r.all_models]

    def clear_cache(self, provider_id: str | None = None) -> None:
        if provider_id is None:
            self._cache.clear()
        else:
            self._cache.pop(provider_id, None)

    def start_periodic_refresh(self, providers_getter, interval: float) -> None:
        """启动后台定时刷新. providers_getter 是 callable 返回当前 providers 字典"""
        if self._refresh_task and not self._refresh_task.done():
            return

        async def _loop():
            while True:
                try:
                    await asyncio.sleep(interval)
                    providers = providers_getter() or {}
                    await self.discover_all(providers, force=True)
                except asyncio.CancelledError:
                    break
                except Exception:
                    LOG.exception("Periodic refresh failed")

        try:
            self._refresh_task = asyncio.create_task(_loop())
        except RuntimeError:
            # 没有 running event loop, 跳过
            LOG.debug("No event loop, periodic refresh not started")

    def stop_periodic_refresh(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            self._refresh_task = None
