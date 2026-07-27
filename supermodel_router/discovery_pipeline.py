"""L3 discovery pipeline: verify L1/L2 finds and integrate into SMR.

Non-destructive by default: validated models are persisted to
validated_models.json plus a sidecar discovery_auto_register.yaml
fragment. Main config.yaml is NOT mutated here.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

try:  # L1 module may not exist yet in earlier steps
    from supermodel_router.model_discovery import (  # type: ignore
        ModelDiscovery,
        DiscoveredModel,
    )
except Exception:  # pragma: no cover
    ModelDiscovery = None  # type: ignore
    DiscoveredModel = None  # type: ignore

try:
    from supermodel_router.platform_scanner import PlatformScanner  # noqa: F401
except Exception:  # pragma: no cover
    PlatformScanner = None  # type: ignore

logger = logging.getLogger(__name__)


class DiscoveryPipeline:
    HTTP_TIMEOUT = 10.0
    DEFAULT_STATE_DIR = "/app/state"

    def __init__(
        self,
        discovery: Any = None,
        scanner: Any = None,
        config: Optional[dict] = None,
        state_dir: str = DEFAULT_STATE_DIR,
    ):
        self.discovery = discovery
        self.scanner = scanner
        self.config = config or {}

        try:
            base = Path(state_dir)
            base.mkdir(parents=True, exist_ok=True)
        except Exception:
            base = Path(__file__).resolve().parent
        self.state_dir: Path = base
        self.state_path: Path = self.state_dir / "validated_models.json"
        self.register_fragment_path: Path = (
            self.state_dir / "discovery_auto_register.yaml"
        )

        self._validated: Dict[str, dict] = {}
        self._stats: Dict[str, int] = {
            "discovered": 0,
            "verified": 0,
            "registered": 0,
            "failed": 0,
        }
        self._load_state()

    # ---------- state ----------
    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._validated = raw.get("validated", {}) or {}
            saved_stats = raw.get("stats") or {}
            for k, v in saved_stats.items():
                if k in self._stats and isinstance(v, int):
                    self._stats[k] = v
        except Exception as e:
            logger.warning("discovery_pipeline: failed to load state: %s", e)

    def _save_state(self) -> None:
        tmp = self.state_path.with_suffix(".json.tmp")
        payload = {
            "saved_at": time.time(),
            "validated": self._validated,
            "stats": self._stats,
        }
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, self.state_path)

    # ---------- verify ----------
    async def verify_model(self, dm: Any, key: str = "") -> bool:
        model_id = getattr(dm, "model_id", None)
        api_base = getattr(dm, "api_base", "")
        if not model_id or not api_base:
            logger.warning(
                "verify_model: missing model_id or api_base (model_id=%r api_base=%r)",
                model_id,
                api_base,
            )
            return False

        url = api_base.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        body = {
            "model": model_id,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 5,
        }
        timeout = aiohttp.ClientTimeout(total=self.HTTP_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.post(url, json=body, headers=headers) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "verify_model %s -> HTTP %s", model_id, resp.status
                        )
                        return False
                    try:
                        data = await resp.json(content_type=None)
                    except Exception as e:
                        logger.warning(
                            "verify_model %s -> JSON error: %s", model_id, e
                        )
                        return False
                    choices = data.get("choices") if isinstance(data, dict) else None
                    if isinstance(choices, list) and len(choices) > 0:
                        return True
                    logger.warning(
                        "verify_model %s -> empty choices", model_id
                    )
                    return False
        except asyncio.TimeoutError:
            logger.warning("verify_model %s -> timeout", model_id)
        except Exception as e:
            logger.warning("verify_model %s -> error: %s", model_id, e)
        return False

    async def verify_batch(
        self,
        models: List[Any],
        concurrency: int = 5,
        key_map: Optional[Dict[str, str]] = None,
    ) -> Dict[str, bool]:
        if not models:
            return {}
        sem = asyncio.Semaphore(max(1, concurrency))
        key_map = key_map or {}

        async def _one(dm: Any):
            provider = getattr(dm, "provider", "")
            key = key_map.get(provider, "") if provider else ""
            async with sem:
                ok = await self.verify_model(dm, key=key)
            return dm, ok

        results = await asyncio.gather(
            *[_one(m) for m in models], return_exceptions=True
        )
        out: Dict[str, bool] = {}
        for r in results:
            if isinstance(r, Exception):
                self._stats["failed"] += 1
                continue
            dm, ok = r
            mid = getattr(dm, "model_id", None)
            if not mid:
                continue
            out[mid] = bool(ok)
            if ok:
                self._stats["verified"] += 1
            else:
                self._stats["failed"] += 1
        return out

    # ---------- integrate ----------
    async def integrate(self, config_key_env: Optional[Dict[str, str]] = None) -> int:
        """Run full pipeline: L1 probe → L2 scan → verify → non-destructive register."""
        config_key_env = config_key_env or {}
        key_map = {
            provider: os.environ.get(env_name, "")
            for provider, env_name in config_key_env.items()
        }

        l1_models: List[Any] = []
        if self.discovery is not None:
            try:
                probe = getattr(self.discovery, "probe_all", None)
                if callable(probe):
                    res = probe()
                    if asyncio.iscoroutine(res):
                        res = await res
                    if isinstance(res, list):
                        l1_models = res
            except Exception as e:
                logger.warning("integrate: discovery.probe_all failed: %s", e)

        if self.scanner is not None:
            try:
                scan = getattr(self.scanner, "scan_all", None)
                if callable(scan):
                    res = scan()
                    if asyncio.iscoroutine(res):
                        await res
            except Exception as e:
                logger.warning("integrate: scanner.scan_all failed: %s", e)

        self._stats["discovered"] = len(l1_models)

        valid_map = await self.verify_batch(l1_models, key_map=key_map)

        free_valid: List[Any] = []
        for dm in l1_models:
            mid = getattr(dm, "model_id", None)
            if mid and valid_map.get(mid) and getattr(dm, "is_free", False):
                free_valid.append(dm)

        new_registered = self._register_to_smr(free_valid)
        self._save_state()
        self._write_register_fragment()
        return new_registered

    def _register_to_smr(self, models: List[Any]) -> int:
        new_count = 0
        for dm in models:
            mid = getattr(dm, "model_id", None)
            if not mid:
                continue
            provider = getattr(dm, "provider", "") or ""
            key = f"{provider}:{mid}" if provider else mid
            if key in self._validated:
                continue
            self._validated[key] = {
                "model_id": mid,
                "provider": provider,
                "api_base": getattr(dm, "api_base", ""),
                "is_free": bool(getattr(dm, "is_free", False)),
                "registered_at": time.time(),
            }
            new_count += 1
        self._stats["registered"] += new_count
        return new_count

    def _write_register_fragment(self) -> None:
        try:
            lines = ["# auto-generated by DiscoveryPipeline; non-destructive sidecar",
                     "discovery_auto_register:"]
            for key, entry in sorted(self._validated.items()):
                lines.append(f"  - key: {key}")
                lines.append(f"    model_id: {entry.get('model_id','')}")
                lines.append(f"    provider: {entry.get('provider','')}")
                lines.append(f"    api_base: {entry.get('api_base','')}")
                lines.append(f"    is_free: {str(bool(entry.get('is_free'))).lower()}")
            tmp = self.register_fragment_path.with_suffix(".yaml.tmp")
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(tmp, self.register_fragment_path)
        except Exception as e:
            logger.warning("write_register_fragment failed: %s", e)

    # ---------- stats ----------
    def get_stats(self) -> Dict[str, int]:
        return dict(self._stats)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    p = DiscoveryPipeline()
    print(json.dumps(p.get_stats(), indent=2))
