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

LOG = logging.getLogger("smr.router")

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
        """判断 HTTP 错误是否可重试 / 是否需要禁用模型

        关键修正:
        - 401/403: 鉴权失败, 是 key 问题不是 model 问题 → 切 key 重试, 不动 model
        - 404/410: 模型不存在 → 是 model 问题, 禁用 model
        - 429: 限流 → 仅标记, 切 key 试下一个
        - 5xx: 服务端问题, 切 key 试下一个; 真正多次失败才考虑 disable model
        """
        body_low = (body or "").lower()
        if status_code == 400:
            return {"retryable": True, "disable_model": False}
        # 鉴权类: 切 key 重试, 不 disable model
        if status_code in (401, 403):
            return {"retryable": True, "disable_model": False}
        # 模型不存在: 是 model 问题
        if status_code in (404, 410):
            return {"retryable": False, "disable_model": True}
        # 限流 / 超时 / 不可处理: 切 key 重试
        if status_code in (408, 422, 429):
            return {"retryable": True, "disable_model": False}
        # 5xx: 服务端问题, 切 key 重试 (不要立刻 disable model, 由 router 综合判断)
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
        """
        流式响应: 透传 SSE 字节流.

        关键修复 (2026-06-22): 必须在 async with httpx.AsyncClient 块内消费 stream.
        原版: return {stream: resp} → 调用方 async with 退出 → resp.stream 死 → ReadError
        新版: 在 async with 内 aiter_bytes() 读完 → cache 为 list[bytes] → return chunks
        副作用: 破坏流式实时性 (必须等上游发完所有 chunk 才开始写 client), 但保证正确性.
        优化方向: 改 _forward_stream 为 async generator, 保持 stream 跨 async with 边界.
        """
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
            # ── v3 修复: 在 async with 块内消费 stream + cache ──
            chunks: list[bytes] = []
            try:
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
            finally:
                try:
                    await resp.aclose()
                except Exception:
                    pass
            return {
                "success": True, "status_code": resp.status_code,
                "chunks": chunks,                # ← 改: stream → chunks (list of bytes)
                "latency_first": latency_first,
                "rate_limit": rl or None,
            }
        # 错误响应 (保持不变)
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

    # ── 错误状态码分类常量 ──

    # 鉴权/限流类错误: 是 key 问题, 应在同 provider 内切下一个 key
    KEY_RETRY_STATUSES = (401, 403, 429)
    # 模型不存在类: 是 model 问题, 应当 disable model
    MODEL_ERROR_STATUSES = (404, 410)

    async def _try_provider_with_keys(
        self, target: RouteTarget, body: dict, is_stream: bool,
        attempts: list[dict], tried_keys: set[str] | None = None,
    ) -> dict[str, Any]:
        """
        在一个 provider 内, 用多个 key 依次尝试同一个 (provider, model) 组合.

        行为:
        - 401/403/429 → mark key cooldown + 立即换下一个 key
        - 404/410 → mark model disabled (是 model 问题)
        - 5xx → 换下一个 key; 全部 key 都 5xx 才返回
        - 400/422 → 直接返回 (不可重试)
        - 成功 → 返回

        槽位约定: 调用方 (select_target) 已 acquire_slot, 整个方法内只占 1 个槽,
        切 key 时不重新 acquire, 退出时统一 release.
        """
        provider = target.provider
        model = target.model
        tried_keys = set(tried_keys or set())
        current_key = target.api_key
        current_target = target
        last_result: dict | None = None
        slot_released = False

        # 最多试 provider.key_count + 1 次 (给 cooldown 恢复的 key 一次机会)
        max_attempts = provider.key_count + 1
        try:
            for _ in range(max_attempts):
                tried_keys.add(current_key)
                attempts.append({
                    "provider_id": provider.id,
                    "model": model,
                    "key": current_key[:8] + "...",
                    "status_code": None,
                    "error": None,
                })
                try:
                    result = await self.forward_chat(current_target, body, stream=is_stream)
                except Exception as e:
                    LOG.exception("Forward unexpected error")
                    result = {"success": False, "status_code": 502,
                              "error": str(e), "retryable": True}
                attempts[-1]["status_code"] = result.get("status_code")
                attempts[-1]["error"] = (result.get("error") or "")[:200]
                last_result = result

                if result.get("success"):
                    provider.record_key_success(current_key)
                    provider.report_success(
                        model,
                        latency_ms=result.get("latency") or result.get("latency_first") or 0,
                    )
                    result["provider_id"] = provider.id
                    result["model_id"] = model
                    return result

                # ── 失败处理 ──
                status = result.get("status_code", 0) or 0
                provider.record_key_failure(
                    current_key,
                    http_code=status,
                    retry_after=(result.get("rate_limit") or {}).get("retry_after", 0.0),
                )
                provider.report_failure(model, error=result.get("error", ""),
                                        http_code=status)

                # 429 限流: 标记 (只影响 model 选择, 不一定切 provider)
                if status == 429 and (result.get("rate_limit") or {}).get("retry_after"):
                    retry_after = (result["rate_limit"] or {}).get("retry_after", 60)
                    provider.mark_rate_limited(model, retry_after)

                # 404/410: 模型确实不存在 → disable model
                if status in self.MODEL_ERROR_STATUSES:
                    provider.disable_model(model, reason=result.get("error", ""))
                    # model 已 disable, 本 provider 内换 key 也无意义
                    return result

                # 不可重试: 直接返回
                if not result.get("retryable"):
                    return result

                # 鉴权/限流/5xx: 切下一个 key
                if status in self.KEY_RETRY_STATUSES or status >= 500:
                    next_key = await provider.pick_key_excluding(exclude=tried_keys)
                    if not next_key or next_key == current_key:
                        # 没有别的 key 可试
                        return result
                    LOG.info("Provider %s key %s → trying next key %s (HTTP %d)",
                             provider.id, current_key[:8] + "...", next_key[:8] + "...",
                             status)
                    current_key = next_key
                    current_target = RouteTarget(
                        provider=provider,
                        model=model,
                        api_key=next_key,
                        base_url=provider.base_url,
                        headers={**provider.extra_headers,
                                 "Authorization": f"Bearer {next_key}"},
                    )
                    continue

                # 其它情况: 直接返回
                return result
        finally:
            # 统一释放槽位 (select_target 给的)
            await provider.release_slot()
            slot_released = True

        # 超 max_attempts: 用最后一次结果
        return last_result or {
            "success": False, "status_code": 502,
            "error": "exhausted key retries", "retryable": True,
        }

    async def handle_chat(self, body: dict) -> dict[str, Any]:
        """
        处理 /v1/chat/completions 请求: 选择 → 转发 → 失败重试.

        重试策略 (修正后):
        1. 选一个 provider + model + key
        2. 在同一 provider 内, 鉴权/限流/5xx → 立即切下一个 key 重试
        3. 同 provider 所有 key 都失败后, 才切下一个 provider
        4. 404/410 → disable model, 不再在同 provider 重试

        Returns: {"success": bool, "data": ..., "provider_id": ..., "model_id": ...}
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

            # 释放 select_target 里 acquire 的槽位, 由 _try_provider_with_keys 内部 acquire
            # 注: select_target 已 acquire, 这里我们让它带着, _try_provider_with_keys 会用第一个 target
            result = await self._try_provider_with_keys(
                target, body, is_stream, attempts,
            )
            last_result = result

            if result.get("success"):
                self._success_count += 1
                return result

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
