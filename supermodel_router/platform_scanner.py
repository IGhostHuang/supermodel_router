"""L2 new-platform community scanner.

Scans public sources (GitHub README, later reddit/HN) for candidate
free-tier LLM API platforms, verifies OpenAI-compatible /v1/models
endpoints, and persists candidates to platform_candidates.json.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class NewPlatformCandidate:
    name: str
    url: str
    source: str  # "github" | "reddit" | "hn"
    found_at: float
    free_signals: List[str] = field(default_factory=list)
    verified: bool = False
    api_base: str = ""
    models_endpoint: str = "/v1/models"
    model_count: int = 0


class PlatformScanner:
    GITHUB_REPO = "cheahjs/free-llm-api-resources"
    GITHUB_README_URL = (
        f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/README.md"
    )
    KEYWORDS = [
        "free LLM API",
        "free AI API",
        "free inference",
        "free tier",
        "no cost",
        "$0",
        "free credits",
        "openai compatible",
    ]
    MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
    FILTER_RE = re.compile(r"free|api|llm|ai", re.IGNORECASE)
    ENDPOINTS = ["/v1/models", "/api/v1/models", "/models"]
    HTTP_TIMEOUT = 5.0

    def __init__(self, state_dir: Optional[Path] = None, known_urls: Optional[set] = None):
        base = Path(__file__).resolve().parent
        self.state_path: Path = (state_dir or base) / "platform_candidates.json"
        self._known_urls: set = set(known_urls or [])
        self._candidates: Dict[str, NewPlatformCandidate] = {}
        self._load_state()

    # ---------- state ----------
    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            for item in raw.get("candidates", []):
                c = NewPlatformCandidate(**item)
                self._candidates[c.url] = c
        except Exception as e:
            logger.warning("platform_scanner: failed to load state: %s", e)

    def _save_state(self) -> None:
        tmp = self.state_path.with_suffix(".json.tmp")
        payload = {
            "saved_at": time.time(),
            "candidates": [asdict(c) for c in self._candidates.values()],
        }
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    # ---------- github ----------
    async def scan_github_readme(self, gh_token: str = "") -> List[NewPlatformCandidate]:
        headers = {"User-Agent": "supermodel-router/platform-scanner"}
        if gh_token:
            headers["Authorization"] = f"Bearer {gh_token}"
        found: List[NewPlatformCandidate] = []
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as sess:
                async with sess.get(self.GITHUB_README_URL) as resp:
                    if resp.status != 200:
                        logger.warning("github readme fetch %s -> %s", self.GITHUB_README_URL, resp.status)
                        return found
                    text = await resp.text()
        except Exception as e:
            logger.warning("scan_github_readme error: %s", e)
            return found

        now = time.time()
        for m in self.MD_LINK_RE.finditer(text):
            name, url = m.group(1).strip(), m.group(2).strip()
            if not url.startswith(("http://", "https://")):
                continue
            if url in self._known_urls or url in self._candidates:
                continue
            if not (self.FILTER_RE.search(name) or self.FILTER_RE.search(url)):
                continue
            signals = [kw for kw in self.KEYWORDS if kw.lower() in name.lower()]
            cand = NewPlatformCandidate(
                name=name, url=url, source="github", found_at=now, free_signals=signals
            )
            self._candidates[url] = cand
            found.append(cand)
        return found

    # ---------- verify ----------
    async def verify_candidate(self, candidate: NewPlatformCandidate) -> bool:
        base = candidate.url.rstrip("/")
        timeout = aiohttp.ClientTimeout(total=self.HTTP_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                for ep in self.ENDPOINTS:
                    probe = base + ep
                    try:
                        async with sess.get(probe) as resp:
                            if resp.status != 200:
                                continue
                            try:
                                data = await resp.json(content_type=None)
                            except Exception:
                                data = None
                            candidate.verified = True
                            candidate.api_base = base
                            candidate.models_endpoint = ep
                            if isinstance(data, dict) and isinstance(data.get("data"), list):
                                candidate.model_count = len(data["data"])
                            return True
                    except Exception:
                        continue
        except Exception as e:
            logger.debug("verify_candidate %s error: %s", candidate.url, e)
        return False

    # ---------- top-level ----------
    async def scan_all(self, gh_token: str = "") -> Dict[str, int]:
        gh_found = await self.scan_github_readme(gh_token=gh_token)
        stats = {
            "github_new": len(gh_found),
            "total_candidates": len(self._candidates),
            "verified": sum(1 for c in self._candidates.values() if c.verified),
        }
        self._save_state()
        return stats

    def get_candidates(self, verified_only: bool = False) -> List[NewPlatformCandidate]:
        vals = list(self._candidates.values())
        if verified_only:
            vals = [c for c in vals if c.verified]
        return vals


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    s = PlatformScanner()
    stats = asyncio.run(s.scan_all())
    print(json.dumps(stats, indent=2))
