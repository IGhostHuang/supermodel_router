"""L1 Model Discovery — probe known free-tier LLM API platforms.

Provides DiscoveredModel dataclass and ModelDiscovery class with a
registry of >=8 known platforms and an async probe_all() that returns
an aggregated list of discovered models. Network failures are soft:
unreachable platforms yield an empty list and are logged.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredModel:
    model_id: str
    provider: str
    api_base: str
    is_free: bool = False
    source: str = "models_endpoint"
    discovered_at: float = field(default_factory=time.time)
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ModelDiscovery:
    """Probe known free-tier OpenAI-compatible platforms for model lists."""

    DEFAULT_PLATFORMS: Dict[str, Dict[str, Any]] = {
        "openrouter": {
            "api_base": "https://openrouter.ai/api/v1",
            "models_endpoint": "/models",
            "free_default": False,
            "key_env": "OPENROUTER_API_KEY",
        },
        "groq": {
            "api_base": "https://api.groq.com/openai/v1",
            "models_endpoint": "/models",
            "free_default": True,
            "key_env": "GROQ_API_KEY",
        },
        "cerebras": {
            "api_base": "https://api.cerebras.ai/v1",
            "models_endpoint": "/models",
            "free_default": True,
            "key_env": "CEREBRAS_API_KEY",
        },
        "sambanova": {
            "api_base": "https://api.sambanova.ai/v1",
            "models_endpoint": "/models",
            "free_default": True,
            "key_env": "SAMBANOVA_API_KEY",
        },
        "together": {
            "api_base": "https://api.together.xyz/v1",
            "models_endpoint": "/models",
            "free_default": False,
            "key_env": "TOGETHER_API_KEY",
        },
        "mistral": {
            "api_base": "https://api.mistral.ai/v1",
            "models_endpoint": "/models",
            "free_default": False,
            "key_env": "MISTRAL_API_KEY",
        },
        "google_ai_studio": {
            "api_base": "https://generativelanguage.googleapis.com/v1beta/openai",
            "models_endpoint": "/models",
            "free_default": True,
            "key_env": "GOOGLE_AI_STUDIO_KEY",
        },
        "hyperbolic": {
            "api_base": "https://api.hyperbolic.xyz/v1",
            "models_endpoint": "/models",
            "free_default": False,
            "key_env": "HYPERBOLIC_API_KEY",
        },
        "deepinfra": {
            "api_base": "https://api.deepinfra.com/v1/openai",
            "models_endpoint": "/models",
            "free_default": False,
            "key_env": "DEEPINFRA_API_KEY",
        },
        "chutes": {
            "api_base": "https://llm.chutes.ai/v1",
            "models_endpoint": "/models",
            "free_default": True,
            "key_env": "CHUTES_API_KEY",
        },
    }

    HTTP_TIMEOUT = 8.0

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        overrides: Dict[str, Any] = {}
        try:
            overrides = (self.config.get("discovery") or {}).get("platforms") or {}
        except Exception:
            overrides = {}
        merged: Dict[str, Dict[str, Any]] = {
            k: dict(v) for k, v in self.DEFAULT_PLATFORMS.items()
        }
        if isinstance(overrides, dict):
            for k, v in overrides.items():
                if isinstance(v, dict):
                    base = dict(merged.get(k, {}))
                    base.update(v)
                    merged[k] = base
        self._platforms: Dict[str, Dict[str, Any]] = merged

    # ---------- helpers ----------
    def _looks_free(self, provider: str, raw: Dict[str, Any]) -> bool:
        pconf = self._platforms.get(provider, {})
        if pconf.get("free_default"):
            return True
        mid = str(raw.get("id", ""))
        if ":free" in mid.lower():
            return True
        pricing = raw.get("pricing") or {}
        if isinstance(pricing, dict):
            try:
                p = float(pricing.get("prompt", 0) or 0)
                c = float(pricing.get("completion", 0) or 0)
                if p == 0 and c == 0:
                    return True
            except Exception:
                pass
        return False

    async def _probe_one(
        self,
        session: aiohttp.ClientSession,
        provider: str,
        pconf: Dict[str, Any],
    ) -> List[DiscoveredModel]:
        api_base = str(pconf.get("api_base", "")).rstrip("/")
        endpoint = str(pconf.get("models_endpoint", "/models"))
        if not api_base:
            return []
        url = api_base + endpoint
        headers = {"Accept": "application/json"}
        key_env = pconf.get("key_env")
        if key_env:
            k = os.environ.get(key_env, "")
            if k:
                headers["Authorization"] = f"Bearer {k}"

        out: List[DiscoveredModel] = []
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    logger.info("probe %s -> HTTP %s", provider, resp.status)
                    return []
                try:
                    data = await resp.json(content_type=None)
                except Exception as e:
                    logger.info("probe %s -> JSON error: %s", provider, e)
                    return []
        except asyncio.TimeoutError:
            logger.info("probe %s -> timeout", provider)
            return []
        except Exception as e:
            logger.info("probe %s -> error: %s", provider, e)
            return []

        items: List[Any] = []
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            items = data["data"]
        elif isinstance(data, list):
            items = data
        for it in items:
            if not isinstance(it, dict):
                continue
            mid = str(it.get("id") or it.get("name") or "").strip()
            if not mid:
                continue
            out.append(
                DiscoveredModel(
                    model_id=mid,
                    provider=provider,
                    api_base=api_base,
                    is_free=self._looks_free(provider, it),
                    source="models_endpoint",
                    raw=it,
                )
            )
        return out

    # ---------- public ----------
    async def probe_all(self) -> List[DiscoveredModel]:
        timeout = aiohttp.ClientTimeout(total=self.HTTP_TIMEOUT)
        results: List[DiscoveredModel] = []
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                tasks = [
                    self._probe_one(session, prov, pconf)
                    for prov, pconf in self._platforms.items()
                ]
                gathered = await asyncio.gather(*tasks, return_exceptions=True)
                for r in gathered:
                    if isinstance(r, Exception):
                        continue
                    if isinstance(r, list):
                        results.extend(r)
        except Exception as e:
            logger.warning("probe_all error: %s", e)
        return results

    def platforms(self) -> List[str]:
        return sorted(self._platforms.keys())


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    md = ModelDiscovery(config={})
    print(f"platforms={len(md._platforms)}: {md.platforms()}")
