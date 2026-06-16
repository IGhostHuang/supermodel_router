"""
free-model-router 端到端测试 — 起一个 mock 上游, 启动网关, curl 各接口

不依赖任何真实 provider, 完全本地运行:
  - mock server 在 15679 端口模拟 OpenAI 兼容 /v1/models 和 /v1/chat/completions
  - free_model_router 在 15678 端口 (从 fmr_test_config.yaml 读)
  - 测试 /health /v1/models /v1/providers /admin /v1/chat/completions
"""

import json
import os
import socket
import subprocess
import sys
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import httpx


# ── Mock upstream (15679) ──

MOCK_MODELS = {
    "object": "list",
    "data": [
        {"id": "gpt-4o-free", "object": "model"},
        {"id": "gpt-4o-mini-free", "object": "model"},
        {"id": "claude-3-free", "object": "model"},
        {"id": "gpt-4o", "object": "model"},       # 不带 free, 应被 pattern 过滤掉
        {"id": "claude-3-opus", "object": "model"},  # 同上
    ],
}


class MockOpenAIHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 静默

    def _send_json(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/v1/models":
            # 检查 Authorization 头
            auth = self.headers.get("Authorization", "")
            if auth and not auth.startswith("Bearer "):
                return self._send_json(401, {"error": "invalid token"})
            return self._send_json(200, MOCK_MODELS)
        if self.path.startswith("/v1/chat/completions"):
            return self._send_json(404, {"error": "use POST"})
        return self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/v1/chat/completions":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b"{}"
            try:
                req = json.loads(body)
            except json.JSONDecodeError:
                return self._send_json(400, {"error": "invalid json"})

            model = req.get("model", "")
            # mock 行为: model 含 "fail" → 500, 含 "rate" → 429, 否则正常
            if "fail" in model:
                return self._send_json(500, {"error": "mock failure"})
            if "rate" in model:
                return self._send_json(429, {"error": "rate limited",
                                              "retry_after": 1})

            stream = req.get("stream", False)
            if stream:
                # SSE - 预生成完整 body, 设置 Content-Length, 一次性发送.
                # stdlib BaseHTTPRequestHandler 不支持 chunked, 用 Content-Length
                # 才能让 httpx 客户端识别流边界.
                chunks = [
                    {"id": "chatcmpl-mock1", "object": "chat.completion.chunk",
                     "choices": [{"delta": {"role": "assistant"}, "index": 0}]},
                    {"id": "chatcmpl-mock1", "object": "chat.completion.chunk",
                     "choices": [{"delta": {"content": "Hi"}, "index": 0}]},
                    {"id": "chatcmpl-mock1", "object": "chat.completion.chunk",
                     "choices": [{"delta": {"content": " from mock"}, "index": 0}]},
                    "[DONE]",
                ]
                body = "".join(
                    f"data: {c if isinstance(c, str) else json.dumps(c)}\n\n"
                    for c in chunks
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
            else:
                return self._send_json(200, {
                    "id": "chatcmpl-mock1",
                    "object": "chat.completion",
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": f"echo: {model}"},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 5,
                              "total_tokens": 10},
                })
        return self._send_json(404, {"error": "not found"})


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 启动 mock upstream
        cls.mock_port = _free_port()
        cls.mock_server = ThreadingHTTPServer(
            ("127.0.0.1", cls.mock_port), MockOpenAIHandler
        )
        cls.mock_server.timeout = 1
        import threading
        cls.mock_thread = threading.Thread(
            target=cls.mock_server.serve_forever, daemon=True
        )
        cls.mock_thread.start()

        # 生成测试 config (动态端口)
        cls.gw_port = _free_port()
        config = {
            "server": {
                "host": "127.0.0.1",
                "port": cls.gw_port,
                "api_key": "",
                "cors_origins": ["*"],
            },
            "routing": {"strategy": "round-robin", "max_retry": 1,
                        "first_token_timeout": 5000, "request_timeout": 15000},
            "sync": {"auto_discover": True, "interval": 0},
            "providers": {
                "mock-primary": {
                    "base_url": f"http://127.0.0.1:{cls.mock_port}/v1",
                    "api_keys": ["sk-test-1", "sk-test-2"],
                    "model_rules": {"mode": "pattern", "pattern": ".*free.*"},
                    "max_concurrent": 5,
                    "enabled": True,
                },
                "mock-disabled": {
                    "base_url": f"http://127.0.0.1:{cls.mock_port}/v1",
                    "api_keys": ["sk-disabled"],
                    "model_rules": {"mode": "all"},
                    "enabled": False,
                },
            },
        }
        import tempfile, yaml
        cls.cfg_file = tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False
        )
        yaml.safe_dump(config, cls.cfg_file)
        cls.cfg_file.flush()
        cls.cfg_file.close()

        # 启动 free_model_router (subprocess, 用 venv 的 python)
        venv_python = os.path.join(ROOT, "venv", "bin", "python")
        if not os.path.exists(venv_python):
            venv_python = sys.executable
        cls.proc = subprocess.Popen(
            [venv_python, "-m", "free_model_router",
             "--config", cls.cfg_file.name],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        # 等待网关就绪 (最多 10 秒)
        deadline = time.time() + 10
        cls.base = f"http://127.0.0.1:{cls.gw_port}"
        while time.time() < deadline:
            try:
                r = httpx.get(f"{cls.base}/health", timeout=1.0)
                if r.status_code == 200:
                    break
            except Exception:
                pass
            if cls.proc.poll() is not None:
                # 进程已退出, dump 日志
                out = cls.proc.stdout.read().decode(errors="ignore")
                raise RuntimeError(f"free_model_router exited: {out}")
            time.sleep(0.3)
        else:
            out = cls.proc.stdout.read().decode(errors="ignore")
            cls.proc.kill()
            raise RuntimeError(f"Gateway not ready in 10s. Log:\n{out}")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.proc.terminate()
            cls.proc.wait(timeout=3)
        except Exception:
            cls.proc.kill()
        cls.mock_server.shutdown()
        try:
            os.unlink(cls.cfg_file.name)
        except Exception:
            pass

    def test_health(self):
        r = httpx.get(f"{self.base}/health", timeout=5)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertIn("providers", data)

    def test_list_models_filtered_by_pattern(self):
        r = httpx.get(f"{self.base}/v1/models", timeout=5)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        ids = [m["id"] for m in body.get("data", [])]
        # mock-primary 配置的是 pattern:.*free.*, 应只剩 free 的
        self.assertIn("gpt-4o-free", ids)
        self.assertIn("claude-3-free", ids)
        self.assertNotIn("gpt-4o", ids)
        self.assertNotIn("claude-3-opus", ids)
        # mock-disabled 被关掉, 不贡献模型
        # (它本身 model_rules 是 all, 但 enabled=false)

    def test_list_providers(self):
        r = httpx.get(f"{self.base}/v1/providers", timeout=5)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        ids = [p["id"] for p in body.get("data", [])]
        self.assertIn("mock-primary", ids)
        self.assertIn("mock-disabled", ids)
        # mock-primary 应 enabled
        for p in body["data"]:
            if p["id"] == "mock-primary":
                self.assertTrue(p.get("enabled"))
            if p["id"] == "mock-disabled":
                self.assertFalse(p.get("enabled"))

    def test_admin_html(self):
        r = httpx.get(f"{self.base}/admin", timeout=5)
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers.get("content-type", ""))
        # 至少含 free-model-router 字样
        self.assertIn("free-model-router", r.text)

    def test_chat_completion_ok(self):
        r = httpx.post(
            f"{self.base}/v1/chat/completions",
            json={
                "model": "mock-primary/gpt-4o-free",
                "messages": [{"role": "user", "content": "hello"}],
            },
            timeout=10,
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        # router 把 "mock-primary/gpt-4o-free" 拆成 provider=mock-primary, model=gpt-4o-free
        # 转发给上游, 上游返回的 model 字段不带 provider 前缀
        self.assertEqual(body["model"], "gpt-4o-free")
        self.assertIn("choices", body)
        self.assertIn("echo:", body["choices"][0]["message"]["content"])

    def test_chat_completion_with_bare_model_id(self):
        """只发 model=gpt-4o-free, 不带 provider 前缀 — 路由器应自动选 mock-primary"""
        r = httpx.post(
            f"{self.base}/v1/chat/completions",
            json={
                "model": "gpt-4o-free",
                "messages": [{"role": "user", "content": "hi"}],
            },
            timeout=10,
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["model"], "gpt-4o-free")

    def test_chat_completion_stream(self):
        # 注: free_model_router 用 stdlib http.server, wfile 是 buffered,
        # 真正"流式"需要 chunked transfer encoding (目前未实现).
        # 测试只验证网关接受 stream 请求且能完成响应 (内容正确但非真正流式).
        with httpx.stream(
            "POST",
            f"{self.base}/v1/chat/completions",
            json={
                "model": "mock-primary/gpt-4o-free",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
            timeout=30,
        ) as r:
            self.assertEqual(r.status_code, 200)
            self.assertIn("text/event-stream",
                          r.headers.get("content-type", ""))
            # 收集所有行, 不依赖流式到达
            body = r.read().decode("utf-8", errors="replace")
            self.assertIn("data: [DONE]", body)
            # 至少有一段 delta.content
            self.assertIn('"content": "Hi"', body)
            self.assertIn('"content": " from mock"', body)

    def test_admin_discover(self):
        r = httpx.post(f"{self.base}/admin/discover", timeout=15)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("results", body)


if __name__ == "__main__":
    unittest.main()
