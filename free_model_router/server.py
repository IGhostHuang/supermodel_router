"""
free-model-router HTTP server

基于 Python 标准库 http.server (无外部依赖), 提供 OpenAI 兼容接口:

  POST /v1/chat/completions   chat completions (支持流式)
  GET  /v1/models             列出所有已发现模型
  GET  /v1/providers          列出所有 provider 状态
  GET  /health                健康检查
  GET  /admin                 简单管理面板
  POST /admin/discover        强制重新发现
  POST /admin/health-check    触发健康检查

支持:
- API key 鉴权 (Bearer token, 可选)
- CORS
- 流式 SSE 透传
- 错误返回 OpenAI 格式
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

LOG = logging.getLogger("fmr.server")


def _format_sse(data: Any) -> bytes:
    """OpenAI 风格 SSE 帧"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


def _format_sse_done() -> bytes:
    return b"data: [DONE]\n\n"


def _openai_error(message: str, err_type: str = "api_error",
                  code: int = 500) -> dict:
    return {
        "error": {
            "message": message,
            "type": err_type,
            "code": code,
        }
    }


# ── HTTP Handler ──


class FreeModelRouterHandler(BaseHTTPRequestHandler):
    """
    HTTP 请求处理器. server 字段由外部 Server 实例注入:

      handler.server.app = server_instance
    """

    # 抑制默认 access log (我们自己打)
    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass

    @property
    def app(self):  # type: ignore[override]
        return self.server.app  # type: ignore[attr-defined]

    # ── 通用响应辅助 ──

    def _send_json(self, status: int, body: dict, extra_headers: dict | None = None) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, x-api-key")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _send_sse_headers(self, status: int = 200,
                          extra_headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Accel-Buffering", "no")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()

    def _check_auth(self) -> bool:
        """检查 API key (如果配置了)"""
        expected = self.app.config.get("server", {}).get("api_key", "")
        if not expected:
            return True
        provided = ""
        x_api = self.headers.get("x-api-key")
        if x_api:
            provided = x_api.strip()
        elif self.headers.get("authorization"):
            auth = self.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                provided = auth[7:].strip()
        if provided != expected:
            self._send_json(401, _openai_error(
                "Missing or invalid API key", "authentication_error", 401))
            return False
        return True

    # ── 路由分发 ──

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, x-api-key")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/health":
                self._handle_health()
            elif path == "/v1/models":
                self._handle_models_list()
            elif path == "/v1/providers":
                self._handle_providers_list()
            elif path == "/admin" or path == "/admin/":
                self._handle_admin_dashboard()
            elif path == "/v1/stats":
                self._handle_stats()
            else:
                self._send_json(404, _openai_error("Not found", "not_found_error", 404))
        except Exception as e:
            LOG.exception("GET %s failed", path)
            self._send_json(500, _openai_error(f"Internal error: {e}", "api_error", 500))

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/v1/chat/completions":
                if not self._check_auth():
                    return
                self._handle_chat_completions()
            elif path == "/admin/discover":
                if not self._check_auth():
                    return
                self._handle_admin_discover()
            elif path == "/admin/health-check":
                if not self._check_auth():
                    return
                self._handle_admin_health_check()
            elif path == "/admin/reload":
                if not self._check_auth():
                    return
                self._handle_admin_reload()
            else:
                self._send_json(404, _openai_error("Not found", "not_found_error", 404))
        except Exception as e:
            LOG.exception("POST %s failed", path)
            self._send_json(500, _openai_error(f"Internal error: {e}", "api_error", 500))

    # ── /health ──

    def _handle_health(self) -> None:
        mgr = self.app.provider_manager
        router = self.app.router
        self._send_json(200, {
            "status": "ok",
            "uptime_s": int(time.time() - self.app.started_at),
            "providers": len(mgr.providers),
            "active_providers": len(mgr.active_providers()),
            "router_stats": router.stats(),
        })

    # ── /v1/models ──

    def _handle_models_list(self) -> None:
        provider = self.headers.get("x-provider")  # 可选: 只看某个 provider
        all_ids: list[dict[str, Any]] = []
        mgr = self.app.provider_manager
        for p in mgr.list_providers():
            if not p.enabled:
                continue
            if provider and p.id != provider:
                continue
            for mid in p.get_free_models():
                if mid in p.disabled_models:
                    continue
                all_ids.append({
                    "id": mid,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": p.id,
                })
        self._send_json(200, {"object": "list", "data": all_ids})

    # ── /v1/providers ──

    def _handle_providers_list(self) -> None:
        self._send_json(200, {
            "object": "list",
            "data": [p.to_dict() for p in self.app.provider_manager.list_providers()],
        })

    # ── /v1/stats ──

    def _handle_stats(self) -> None:
        self._send_json(200, {
            "router": self.app.router.stats(),
            "providers": self.app.provider_manager.to_dict(),
        })

    # ── /v1/chat/completions ──

    def _handle_chat_completions(self) -> None:
        # 读 body
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, json.JSONDecodeError) as e:
            self._send_json(400, _openai_error(
                f"Invalid JSON: {e}", "invalid_request_error", 400))
            return
        if not isinstance(body, dict):
            self._send_json(400, _openai_error(
                "Body must be JSON object", "invalid_request_error", 400))
            return
        is_stream = bool(body.get("stream"))

        # ── v3 流式: 立即发 SSE headers + 调度后台 task 异步消费 upstream ──
        # 关键修复 (2026-06-22): upstream 是 httpx async stream, 必须 async for + aiter_bytes()
        # sync iter_bytes() 在 async stream 上 → 500 "sync iterator on async stream"
        # 同步线程 (BaseHTTPRequestHandler) 不能直接 async for, 通过 run_coroutine_threadsafe
        # 把消费任务交给 event loop, 在 loop 里 aiter_bytes() + 用 run_in_executor 写 wfile
        if is_stream:
            try:
                self._send_sse_headers(200)
            except Exception as e:
                LOG.exception("send sse headers failed: %s", e)
                return
            # 调度后台 task, 立即 return (handle_one_request 退出)
            asyncio.run_coroutine_threadsafe(
                self._stream_loop(body), self.app._loop
            )
            return

        # 非流式: 原 future.result 模式
        future = asyncio.run_coroutine_threadsafe(
            self.app.router.handle_chat(body), self.app._loop
        )
        try:
            result = future.result(timeout=120)
        except Exception as e:
            LOG.exception("Chat handle failed")
            self._send_json(500, _openai_error(
                f"Internal error: {e}", "api_error", 500))
            return
        if result.get("success"):
            self._send_json(200, result["data"])
        else:
            code = result.get("status_code") or 502
            err = result.get("error") or "Unknown error"
            err_type = "rate_limit_error" if code == 429 else "api_error"
            extra = {}
            if code == 429 and result.get("rate_limit", {}).get("retry_after"):
                extra["Retry-After"] = str(int(result["rate_limit"]["retry_after"]))
            self._send_json(code, _openai_error(err, err_type, code), extra)

    async def _stream_loop(self, body: dict) -> None:
        """
        在 event loop 中消费 chunks list (router 已 cache) + 同步写入 wfile.

        关键 (2026-06-22 v3 修复):
        1. router._forward_stream 在 async with httpx.AsyncClient 内已 aiter_bytes + cache
        2. result["chunks"] 是 list[bytes], 不是 httpx async stream (跨 async with 边界问题已规避)
        3. 同步线程 (BaseHTTPRequestHandler) 调 loop.run_in_executor 写 wfile
        4. 完成后 wfile.flush + close + close_connection=True
        """
        loop = asyncio.get_event_loop()
        try:
            result = await self.app.router.handle_chat(body)
            if not result.get("success"):
                err = result.get("error", "stream failed")
                code = result.get("status_code", 500)
                err_data = f"data: {json.dumps({'error': {'message': err, 'type': 'api_error', 'code': code}})}\n\n".encode()
                await loop.run_in_executor(None, self.wfile.write, err_data)
                await loop.run_in_executor(None, self.wfile.write, b"data: [DONE]\n\n")
                return

            chunks = result.get("chunks", [])
            for chunk in chunks:
                await loop.run_in_executor(None, self.wfile.write, chunk)
            # 上游通常会发 [DONE], 兜底再发
            try:
                await loop.run_in_executor(None, self.wfile.write, b"data: [DONE]\n\n")
            except Exception:
                pass
        except (BrokenPipeError, ConnectionResetError):
            LOG.debug("client disconnected during stream")
        except Exception as e:
            LOG.exception("stream loop error: %s", e)
            try:
                err_data = f"data: {json.dumps({'error': {'message': str(e), 'type': 'api_error', 'code': 500}})}\n\n".encode()
                await loop.run_in_executor(None, self.wfile.write, err_data)
            except Exception:
                pass
        finally:
            try:
                await loop.run_in_executor(None, self._finalize_stream)
            except Exception:
                pass

    def _finalize_stream(self) -> None:
        """同步 flush + close wfile, 强制关 connection"""
        try:
            self.wfile.flush()
        except Exception:
            pass
        try:
            self.wfile.close()
        except Exception:
            pass
        # 强制关 keep-alive, 避免下个请求用已关的 stream
        self.close_connection = True

    def _stream_response(self, result: dict) -> None:
        """
        透传 SSE 流 (同步版, 已废弃, 保留仅为兼容旧调用)
        真实流式走 _stream_loop
        """
        upstream = result.get("stream")
        if not upstream:
            err = result.get("error") or "stream failed"
            self._send_json(result.get("status_code") or 502,
                            _openai_error(err, "api_error",
                                          result.get("status_code") or 502))
            return
        # 旧版仅作 fallback, 不应被调用
        LOG.warning("_stream_response called (deprecated), use _stream_loop instead")
        try:
            self._send_sse_headers(200)
            for chunk in upstream.iter_bytes():
                self.wfile.write(chunk)
                self.wfile.flush()
            self.wfile.write(_format_sse_done())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            LOG.debug("Client disconnected during stream")
        finally:
            try:
                upstream.close()
            except Exception:
                pass

    # ── /admin/* ──

    def _handle_admin_dashboard(self) -> None:
        mgr = self.app.provider_manager
        providers = mgr.to_dict()
        router_stats = self.app.router.stats()
        html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>free-model-router admin</title>
<style>
  body {{ font-family: monospace; margin: 2em; background: #1a1a1a; color: #ddd; }}
  h1 {{ color: #6cf; }}
  table {{ border-collapse: collapse; width: 100%; }}
  td, th {{ border: 1px solid #444; padding: 6px 10px; text-align: left; }}
  th {{ background: #2a2a2a; }}
  .healthy {{ color: #6f6; }}
  .degraded {{ color: #fc6; }}
  .unavailable {{ color: #f66; }}
  pre {{ background: #222; padding: 1em; overflow: auto; }}
  button {{ background: #444; color: #fff; border: 1px solid #888; padding: 6px 12px;
           cursor: pointer; margin-right: 8px; }}
  button:hover {{ background: #555; }}
</style></head><body>
<h1>free-model-router admin</h1>
<p>Uptime: {int(time.time() - self.app.started_at)}s |
   Router: {router_stats['request_count']} req ({router_stats['success_count']} ok) |
   Active: {providers['active']}/{providers['total']} providers</p>
<div>
  <button onclick="location.href='/admin/discover'">Force Discover</button>
  <button onclick="location.href='/admin/health-check'">Health Check</button>
  <button onclick="location.href='/v1/providers'">View JSON</button>
  <button onclick="location.href='/v1/models'">View Models</button>
  <button onclick="location.reload()">Refresh</button>
</div>
<h2>Providers</h2>
<table>
<tr><th>ID</th><th>Status</th><th>Keys</th><th>Primary</th><th>Free Models</th><th>Slot</th><th>Failures</th></tr>
{''.join(self._render_provider_row(p) for p in providers['providers'].values())}
</table>
<h2>Raw State</h2>
<pre>{json.dumps({'router': router_stats, 'providers': providers}, indent=2, default=str)}</pre>
</body></html>"""
        payload = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    @staticmethod
    def _render_provider_row(p: dict) -> str:
        status_class = p["status"]
        free = p["free_model_count"]
        return (f"<tr><td>{p['id']}</td>"
                f"<td class='{status_class}'>{p['status']}</td>"
                f"<td>{p['key_count']}</td>"
                f"<td>{p['primary_model'] or '-'}</td>"
                f"<td>{free}</td>"
                f"<td>{p['slot_used']}/{p['max_concurrent']}</td>"
                f"<td>{p['consecutive_failures']}</td></tr>")

    def _handle_admin_discover(self) -> None:
        # 触发异步发现
        future = asyncio.run_coroutine_threadsafe(
            self.app.run_discovery(force=True), self.app._loop
        )
        try:
            results = future.result(timeout=60)
        except Exception as e:
            self._send_json(500, {"ok": False, "error": str(e)})
            return
        self._send_json(200, {
            "ok": True,
            "results": {pid: r.to_dict() for pid, r in results.items()},
        })

    def _handle_admin_health_check(self) -> None:
        future = asyncio.run_coroutine_threadsafe(
            self.app.provider_manager.health_check_all(), self.app._loop
        )
        try:
            results = future.result(timeout=60)
        except Exception as e:
            self._send_json(500, {"ok": False, "error": str(e)})
            return
        self._send_json(200, {"ok": True, "results": results})

    def _handle_admin_reload(self) -> None:
        try:
            self.app.reload_config()
            self._send_json(200, {"ok": True, "message": "config reloaded"})
        except Exception as e:
            self._send_json(500, {"ok": False, "error": str(e)})


# ── Server 包装 ──


class FreeModelRouterServer:
    """
    顶层服务器: 持有 provider_manager / router / discovery_manager,
    在独立线程中运行 asyncio event loop.
    """

    def __init__(self, config: dict[str, Any], provider_manager: Any,
                 router: Any, discovery_manager: Any,
                 host: str = "127.0.0.1", port: int = 5678):
        self.config = config
        self.provider_manager = provider_manager
        self.router = router
        self.discovery_manager = discovery_manager
        self.host = host
        self.port = port
        self.started_at = time.time()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = None
        self._httpd: ThreadingHTTPServer | None = None
        self._stop_event: asyncio.Event | None = None

    async def run_discovery(self, force: bool = False) -> dict:
        providers_cfg = self.config.get("providers") or {}
        return await self.discovery_manager.discover_all(providers_cfg, force=force)
        # 注意: 这里只拉取, 不会自动更新 provider 的 free_models 列表
        # 需调用者处理. Server 启动时和定时刷新会处理.

    def reload_config(self) -> None:
        """重新加载配置文件 (外部负责)"""
        # 由 main.py 的 config_watcher 处理
        # 这里只发个信号
        LOG.info("Config reload requested")

    def _serve(self):
        """运行 event loop + http server"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def _main():
            # 启动 discovery 后台任务
            providers_cfg_getter = lambda: self.config.get("providers") or {}
            sync_cfg = self.config.get("sync") or {}
            if sync_cfg.get("auto_discover", True):
                interval = float(sync_cfg.get("interval", 3600))
                self.discovery_manager.start_periodic_refresh(
                    providers_cfg_getter, interval
                )
            # 启动 HTTP server (在线程池中, 不阻塞 event loop)
            import threading
            self._httpd = ThreadingHTTPServer((self.host, self.port),
                                              FreeModelRouterHandler)
            self._httpd.app = self  # type: ignore[attr-defined]
            LOG.info("free-model-router listening on http://%s:%d",
                     self.host, self.port)
            http_thread = threading.Thread(target=self._httpd.serve_forever,
                                           daemon=True, name="fmr-httpserve")
            http_thread.start()
            # 保持 loop 运行
            self._stop_event = asyncio.Event()
            await self._stop_event.wait()
            self._httpd.shutdown()

        try:
            self._loop.run_until_complete(_main())
        except Exception:
            LOG.exception("Server crashed")
        finally:
            self.discovery_manager.stop_periodic_refresh()
            if self._loop and not self._loop.is_closed():
                self._loop.close()

    def start(self) -> None:
        import threading
        self._thread = threading.Thread(target=self._serve, daemon=True,
                                        name="fmr-http")
        self._thread.start()
        # 等待服务起来
        for _ in range(50):
            if self._httpd is not None:
                break
            time.sleep(0.1)
        if self._httpd is None:
            raise RuntimeError("Server failed to start")

    def stop(self) -> None:
        if self._stop_event is not None and self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._stop_event.set)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)
        if self._httpd:
            try:
                self._httpd.server_close()
            except Exception:
                pass
