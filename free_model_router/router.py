"""
free-model-router routing engine

职责:
- 选择下一个 provider (策略: random / round-robin / least-loaded)
- 从 provider 中选模型 (主 → 备选 → 任意免费)
- 转发请求到上游 (支持 stream / non-stream)
- 错误处理: 401/404/410 禁用模型, 429 限流等待, 5xx 重试
- 多 key 轮询 + 自动 failover
- 请求统计

对标 JS 版 proxy.js + route-engine.js.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import httpx

from .provider import (
    Provider, ProviderManager, STATUS_HEALTHY, STATUS_DEGRADED, STATUS_UNAVAILABLE,
)

LOG = logging.getLogger("fmr.router")

REQUEST_TIMEOUT = 60.0
FIRST_TOKEN_TIMEOUT = 10.0


# ── 路由结果 ──


class RouteTarget:
    """一次路由选择的产物: 用哪个 provider, 哪个 key, 哪个 model"""
    def __init__(self, provider: Provider, model: str, api_key: str,
                 base_url: str, headers: dict[str, str]):
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.headers = headers
        self.using_fallback = False
        self.is_exploration = False  # 预留: 探索模式

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider.id,
            "model": self.model,
            "base_url": self.base_url,
            "using_fallback": self.using_fallback,
        }


# ── Router ──


class Router:
    """
    主路由引擎.

    用法:
        router = Router(provider_manager)
        target = await router.select_target(skip={"bad-provider"})
        if not target:
            raise NoProviderError()
        result = await router.forward_chat(target, body, stream=False)
    """

    def __init__(self, manager: ProviderManager,
                 strategy: str = "random",
                 max_retry: int = 2):
        self.manager = manager
        self.strategy = strategy
        self.max_retry = max_retry
        self._rr_index = 0
        self._lock = asyncio.Lock()
        # 请求计数 (简单实现)
        self._request_count = 0
        self._success_count = 0
        self._failure_count = 0

    # ── Provider 选择 ──

    async def select_target(self, skip_providers: set[str] | None = None,
                            request_model: str | None = None) -> RouteTarget | None:
        """从所有 active provider 中选一个 + 选 key + 选 model"""
        skip = skip_providers or set()
        candidates = [p for p in self.manager.active_providers() if p.id not in skip]
        if not candidates:
            return None

        # 打乱/排序
        if self.strategy == "round-robin":
            async with self._lock:
                ordered = candidates[self._rr_index % len(candidates):] + \
                          candidates[:self._rr_index % len(candidates)]
                self._rr_index = (self._rr_index + 1) % len(candidates)
                candidates = ordered
        elif self.strategy == "random":
            random.shuffle(candidates)
        elif self.strategy == "least-loaded":
            candidates.sort(key=lambda p: (p.slot_used, p.consecutive_failures))

        for p in candidates:
            if not await p.acquire_slot():
                continue
            model = p.select_model()
            if not model:
                await p.release_slot()
                continue
            api_key = await p.pick_key()
            if not api_key:
                await p.release_slot()
                continue
            headers = {"Authorization": f"Bearer {api_key}"}
            headers.update(p.extra_headers)
            return RouteTarget(
                provider=p,
                model=model,
                api_key=api_key,
                base_url=p.base_url,
                headers=headers,
            )

        # 没有任何 provider 拿到槽位
        return None

    # ── 错误分类 ──

    @staticmethod
    def classify_http_error(status_code: int, body: str = "") -> dict[str, bool]:
        """判断 HTTP 错误是否可重试 / 是否需要禁用模型"""
        body_low = (body or "").lower()
        if status_code == 400:
            return {"retryable": True, "disable_model": False}
        if status_code in (401, 404, 410):
            return {"retryable": False, "disable_model": True}
        if status_code == 403:
            return {"retryable": True, "disable_model": False}
        if status_code in (408, 422, 429):
            return {"retryable": True, "disable_model": False}
        if status_code >= 500:
            return {"retryable": True, "disable_model": False}
        return {"retryable": False, "disable_model": False}

    @staticmethod
    def parse_rate_limit(headers: httpx.Headers) -> dict[str, Any]:
        info: dict[str, Any] = {}
        for k in ("retry-after", "x-ratelimit-reset", "x-ratelimit-retry-after"):
            v = headers.get(k)
            if v:
                try:
                    info["retry_after"] = float(v)
                    break
                except ValueError:
                    pass
        for k in ("x-ratelimit-remaining-requests", "x-ratelimit-remaining-tokens",
                  "modelscope-ratelimit-requests-remaining",
                  "modelscope-ratelimit-model-requests-remaining"):
            v = headers.get(k)
            if v is not None:
                try:
                    info.setdefault("rate_headers", {})[k] = int(v)
                except ValueError:
                    pass
        return info

    # ── 转发 ──

    async def forward_chat(self, target: RouteTarget, body: dict,
                           stream: bool = False) -> dict[str, Any]:
        """
        转发 chat completions 请求.

        Returns:
            {"success": bool, "status_code": int, "error": str, ...}
        """
        if not target:
            return {"success": False, "status_code": 503,
                    "error": "no provider available", "retryable": True}
        url = f"{target.base_url}/chat/completions"
        payload = {**body, "model": target.model}
        try:
            payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as e:
            return {"success": False, "status_code": 400,
                    "error": f"serialize failed: {e}", "retryable": False}

        headers = {**target.headers, "Content-Type": "application/json"}
        start = time.time()
        try:
            timeout = httpx.Timeout(self._stream_timeout() if stream else REQUEST_TIMEOUT)
            async with httpx.AsyncClient(timeout=timeout) as client:
                if stream:
                    return await self._forward_stream(client, target, url, headers,
                                                      payload_bytes, start)
                return await self._forward_blocking(client, target, url, headers,
                                                    payload_bytes, start)
        except Exception as e:
            LOG.exception("Forward error to %s", target.provider.id)
            return {"success": False, "status_code": 502,
                    "error": str(e), "retryable": True}

    def _stream_timeout(self) -> float:
        return max(REQUEST_TIMEOUT, FIRST_TOKEN_TIMEOUT + 30.0)

    async def _forward_blocking(self, client: httpx.AsyncClient,
                                target: RouteTarget, url: str,
                                headers: dict, body: bytes,
                                start: float) -> dict[str, Any]:
        try:
            resp = await client.post(url, headers=headers, content=body)
        except httpx.TimeoutException:
            return {"success": False, "status_code": 504,
                    "error": "upstream timeout", "retryable": True,
                    "latency": (time.time() - start) * 1000}
        latency = (time.time() - start) * 1000
        rl = self.parse_rate_limit(resp.headers)
        text = resp.text
        if 200 <= resp.status_code < 300:
            try:
                data = resp.json()
            except Exception:
                return {"success": False, "status_code": 502,
                        "error": "non-JSON response", "retryable": True,
                        "latency": latency}
            tokens = self._extract_tokens(data)
            return {"success": True, "status_code": resp.status_code,
                    "data": data, "latency": latency, "tokens": tokens,
                    "rate_limit": rl or None}
        cls = self.classify_http_error(resp.status_code, text)
        return {
            "success": False, "status_code": resp.status_code,
            "error": self._truncate(text), "latency": latency,
            "retryable": cls["retryable"],
            "disable_model": cls["disable_model"],
            "rate_limit": rl or None,
        }

    async def _forward_stream(self, client: httpx.AsyncClient,
                              target: RouteTarget, url: str,
                              headers: dict, body: bytes,
                              start: float) -> dict[str, Any]:
        """流式响应: 透传, 但要捕获首字节超时和错误"""
        try:
            req = client.build_request("POST", url, headers=headers, content=body)
            resp = await client.send(req, stream=True)
        except httpx.TimeoutException:
            return {"success": False, "status_code": 504,
                    "error": "upstream timeout", "retryable": True,
                    "latency": (time.time() - start) * 1000}
        latency_first = (time.time() - start) * 1000
        rl = self.parse_rate_limit(resp.headers)
        if 200 <= resp.status_code < 300:
            return {
                "success": True, "status_code": resp.status_code,
                "stream": resp, "latency_first": latency_first,
                "rate_limit": rl or None,
            }
        # 错误响应
        try:
            text = await resp.aread()
            text = text.decode("utf-8", errors="replace")
        except Exception:
            text = ""
        await resp.aclose()
        cls = self.classify_http_error(resp.status_code, text)
        return {
            "success": False, "status_code": resp.status_code,
            "error": self._truncate(text), "latency": latency_first,
            "retryable": cls["retryable"],
            "disable_model": cls["disable_model"],
            "rate_limit": rl or None,
        }

    @staticmethod
    def _truncate(s: str, n: int = 300) -> str:
        if not s:
            return ""
        return s if len(s) <= n else s[:n] + "..."

    @staticmethod
    def _extract_tokens(data: Any) -> int:
        if not isinstance(data, dict):
            return 0
        usage = data.get("usage") or {}
        if not usage:
            return 0
        return (int(usage.get("total_tokens") or 0)
                or int((usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0))
                or int((usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)))

    # ── 顶层处理入口 ──

    async def handle_chat(self, body: dict) -> dict[str, Any]:
        """
        处理 /v1/chat/completions 请求: 选择 → 转发 → 失败重试.
        Returns: {"success": bool, "data": ..., "provider_id": ..., "model": ...}
        """
        self._request_count += 1
        is_stream = bool(body.get("stream"))
        skip: set[str] = set()
        attempts: list[dict] = []
        last_result: dict | None = None

        for attempt in range(self.max_retry + 1):
            target = await self.select_target(skip_providers=skip)
            if not target:
                if attempts:
                    self._failure_count += 1
                    return {
                        "success": False,
                        "error": "all providers unavailable",
                        "status_code": 503,
                        "attempts": attempts,
                    }
                self._failure_count += 1
                return {
                    "success": False,
                    "error": "no providers configured or all unavailable",
                    "status_code": 503,
                    "attempts": attempts,
                }
            try:
                result = await self.forward_chat(target, body, stream=is_stream)
            finally:
                await target.provider.release_slot()
            attempts.append({
                "provider_id": target.provider.id,
                "model": target.model,
                "status_code": result.get("status_code"),
                "error": result.get("error"),
            })
            last_result = result
            if result.get("success"):
                # 记录成功
                target.provider.record_key_success(target.api_key)
                target.provider.report_success(
                    target.model,
                    latency_ms=result.get("latency") or result.get("latency_first") or 0,
                )
                self._success_count += 1
                result["provider_id"] = target.provider.id
                result["model_id"] = target.model
                return result
            # 失败处理
            target.provider.record_key_failure(
                target.api_key,
                http_code=result.get("status_code", 0),
                retry_after=(result.get("rate_limit") or {}).get("retry_after", 0.0),
            )
            target.provider.report_failure(
                target.model,
                error=result.get("error", ""),
                http_code=result.get("status_code", 0),
            )
            # 限流 → 标记 + 跳过该 provider
            if result.get("status_code") == 429 and (result.get("rate_limit") or {}).get("retry_after"):
                retry_after = (result["rate_limit"] or {}).get("retry_after", 60)
                target.provider.mark_rate_limited(target.model, retry_after)
            # 禁用模型
            if result.get("disable_model"):
                target.provider.disable_model(target.model, reason=result.get("error", ""))
            # 不可重试 → 直接返回
            if not result.get("retryable"):
                self._failure_count += 1
                return result
            # 跳过这个 provider
            skip.add(target.provider.id)
            if attempt < self.max_retry:
                await asyncio.sleep(0.2 * (attempt + 1))

        self._failure_count += 1
        return last_result or {
            "success": False, "error": "max retry exceeded",
            "status_code": 502, "attempts": attempts,
        }

    # ── 统计 ──

    def stats(self) -> dict[str, Any]:
        return {
            "request_count": self._request_count,
            "success_count": self._success_count,
            "failure_count": self._failure_count,
            "success_rate": (
                self._success_count / self._request_count
                if self._request_count else 0.0
            ),
            "strategy": self.strategy,
        }
