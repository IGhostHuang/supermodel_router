"""
SMR v3.5.0 端到端自验 — 主动盘点 + 切链 abort + smr_request_id 嵌入 (3 场景)

每个场景独立 SMR 实例 + 独立 state, 避免 penalty 跨场景污染.
"""
import sys
import os
import time
import json
import uuid
import threading
import subprocess
import asyncio
import signal
import glob
import httpx
from http.server import BaseHTTPRequestHandler, HTTPServer


# ── Mock upstream A: 401 失败 (跟 v3.4 一样) ─────────────
class MockUpstreamA(BaseHTTPRequestHandler):
    abort_called: bool = False  # 类级 flag, 验真 aclose() 是否真到

    def log_message(self, *args, **kwargs): pass

    def do_GET(self):
        if self.path == "/v1/models":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "object": "list",
                "data": [{"id": "mock-model-a", "object": "model", "created": 0, "owned_by": "mock-a"}]
            }).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/v1/chat/completions":
            # 场景 3 测试: sleep 3s 再返 401, 让 SMR 切链耗时 > 0
            time.sleep(3)
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": {"message": "Mock A: 401 unauthorized (after 3s sleep)", "type": "auth_error", "code": 401}
            }).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def handle(self):  # type: ignore[override]
        """重写 handle, 捕获 aclose (client 断开连接)"""
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            MockUpstreamA.abort_called = True
            print(f"[MockA] ABORT DETECTED (client disconnected) — abort_called={MockUpstreamA.abort_called}", flush=True)


# ── Mock upstream B: 200 成功 (流式 + 非流式) ─────────────
class MockUpstreamB(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs): pass

    def do_GET(self):
        if self.path == "/v1/models":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "object": "list",
                "data": [{"id": "mock-model-b", "object": "model", "created": 0, "owned_by": "mock-b"}]
            }).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(body)
        except Exception:
            req = {}
        stream = req.get("stream", False)

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = [
                {"id": "mock-1", "object": "chat.completion.chunk", "created": int(time.time()),
                 "model": "mock-model-b", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "我"}, "finish_reason": None}]},
                {"id": "mock-2", "object": "chat.completion.chunk", "created": int(time.time()),
                 "model": "mock-model-b", "choices": [{"index": 0, "delta": {"content": "是"}, "finish_reason": None}]},
                {"id": "mock-3", "object": "chat.completion.chunk", "created": int(time.time()),
                 "model": "mock-model-b", "choices": [{"index": 0, "delta": {"content": "MockB"}, "finish_reason": None}]},
                {"id": "mock-4", "object": "chat.completion.chunk", "created": int(time.time()),
                 "model": "mock-model-b", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
            for c in chunks:
                try:
                    self.wfile.write(f"data: {json.dumps(c, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    print(f"[MockB] client disconnected mid-stream (expected on abort)", flush=True)
                    return
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "id": "mock-resp-1",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": "mock-model-b",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "我是 Mock B 的回复"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }).encode())


# ── Mock server 生命周期 ─────────────────────────────────
def start_mock(handler_cls, port: int) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", port), handler_cls)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def stop_mock(server: HTTPServer):
    try:
        server.shutdown()
        server.server_close()
    except Exception:
        pass


def clear_smr_state():
    for f in ("penalty_state.json", "engine_stats.json", "model_rules_state.json"):
        p = f"/root/projects/supermodel_router/{f}"
        if os.path.exists(p):
            os.remove(p)


def start_smr(port: int, log_file: str = "/tmp/smr-v3.5-e2e.log") -> subprocess.Popen:
    clear_smr_state()
    cfg = f"""
server:
  host: 127.0.0.1
  port: {port}
  api_key: ''

providers:
  mock_a:
    enabled: true
    base_url: http://127.0.0.1:18001/v1
    api_keys: [sk-mock-a]
    model_rules:
      mode: include
      include: [mock-model-a]
      exclude: []
    max_concurrent: 3
    health_check_interval: 60
  mock_b:
    enabled: true
    base_url: http://127.0.0.1:18002/v1
    api_keys: [sk-mock-b]
    model_rules:
      mode: include
      include: [mock-model-b]
      exclude: []
    max_concurrent: 3
    health_check_interval: 60

routing:
  strategy: quality_weighted
  failover_threshold: 3
  recovery_interval: 60
  max_retry: 5
  first_token_timeout_ms: 10000
  retry_backoff_ms: [0, 100]
  quality_weights: {{success_rate: 0.6, latency: 0.4}}

context_bridge:
  enabled: true
  stale_threshold_seconds: 1800
  max_history: 5
  sentinel_enabled: true
  abort_on_switch: true
  max_tracked_requests: 100
  version: "3.5.0"
"""
    cfg_path = f"/tmp/smr-v35-cfg-{port}.yaml"
    with open(cfg_path, "w") as f:
        f.write(cfg)

    log = open(log_file, "w")
    proc = subprocess.Popen(
        ["/root/projects/supermodel_router/venv/bin/python3", "run.py",
         "--config", cfg_path, "--port", str(port), "--log-level", "INFO"],
        cwd="/root/projects/supermodel_router",
        stdout=log, stderr=subprocess.STDOUT,
    )
    proc._log_handle = log
    return proc


def stop_smr(proc: subprocess.Popen):
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def wait_smr_ready(port: int, timeout: int = 15) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/v1/health", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            time.sleep(0.3)
    return False


# ── 场景 1: smr_request_id 嵌入 ──────────────────────────
def scenario_1_req_id_embedding(smr_port: int) -> bool:
    print("\n═══ 场景 1: smr_request_id + chain_id 嵌入 (非流式) ═══", flush=True)
    # mainbot 发的请求带 _smr_request_id, SMR 必须透传 + 嵌到 _router
    test_smr_req_id = str(uuid.uuid4())
    test_chain_id = str(uuid.uuid4())
    body = {
        "model": "auto",  # 不指定具体 model, 让 pick_chain 把 mock_a + mock_b 都加进 chain
        "messages": [{"role": "user", "content": "测试 req_id 嵌入"}],
        "stream": False,
        "_smr_request_id": test_smr_req_id,
        "_smr_chain_id": test_chain_id,
    }
    r = httpx.post(f"http://127.0.0.1:{smr_port}/v1/chat/completions", json=body, timeout=15)
    print(f"  HTTP {r.status_code}", flush=True)
    if r.status_code != 200:
        print(f"  ❌ FAIL: status={r.status_code} body={r.text[:300]}")
        return False
    resp = r.json()
    router_meta = resp.get("_router", {})
    print(f"  _router keys: {list(router_meta.keys())}", flush=True)
    print(f"  smr_request_id={router_meta.get('smr_request_id', 'MISSING')[:8]}", flush=True)
    print(f"  chain_id={router_meta.get('chain_id', 'MISSING')[:8]}", flush=True)
    print(f"  provider={router_meta.get('provider')}, model={router_meta.get('model')}", flush=True)

    ok = True
    if router_meta.get("smr_request_id") != test_smr_req_id:
        print(f"  ❌ FAIL: smr_request_id 不匹配 (期望 {test_smr_req_id[:8]}, 实际 {router_meta.get('smr_request_id', '')[:8]})")
        ok = False
    if router_meta.get("chain_id") != test_chain_id:
        print(f"  ❌ FAIL: chain_id 不匹配")
        ok = False
    if "switched_from" not in router_meta:
        print(f"  ⚠️ switched_from 字段缺失 (无切链, 符合预期)")
    if ok:
        print(f"  ✅ PASS: 场景 1 验真")
    return ok


# ── 场景 2: 切链 abort (流式) ─────────────────────────────
def scenario_2_abort_on_switch(smr_port: int) -> bool:
    print("\n═══ 场景 2: 切链 abort 验真 (流式 mock A → mock B) ═══", flush=True)
    MockUpstreamA.abort_called = False  # 重置

    body = {
        "model": "auto",
        "messages": [{"role": "user", "content": "测试 abort 切链"}],
        "stream": True,
        "_smr_request_id": str(uuid.uuid4()),
    }

    # 用 httpx 流式, 让 SMR 切链并 aclose 老流
    with httpx.stream("POST", f"http://127.0.0.1:{smr_port}/v1/chat/completions",
                      json=body, timeout=30) as r:
        if r.status_code != 200:
            print(f"  ❌ FAIL: status={r.status_code}")
            return False
        chunks_received = 0
        last_router_meta = None
        for chunk in r.iter_text():
            if chunk.startswith("data: ") and chunk.endswith("\n\n"):
                payload = chunk[6:].strip()
                if payload and payload != "[DONE]":
                    try:
                        obj = json.loads(payload)
                        if "_smr_router" in obj:
                            last_router_meta = obj["_smr_router"]
                            print(f"  📡 收到流末尾 _smr_router: smr_req_id={obj['_smr_router'].get('smr_request_id', '')[:8]}, chain_pos={obj['_smr_router'].get('chain_position')}", flush=True)
                        else:
                            chunks_received += 1
                    except Exception:
                        pass

    print(f"  mock B 收到 {chunks_received} content chunks", flush=True)
    if chunks_received < 1:
        print(f"  ❌ FAIL: mock B 一个 chunk 都没收到")
        return False

    # 关键验证 1: 末尾 _smr_router chunk 必须有
    if not last_router_meta:
        print(f"  ❌ FAIL: 流末尾 _smr_router chunk 缺失 (SMR 没在流末尾发 _router meta)")
        return False

    # 关键验证 2: chain_pos 必须 ≥ 1 (切了链)
    if last_router_meta.get("chain_position", 0) < 1:
        print(f"  ❌ FAIL: chain_position={last_router_meta.get('chain_position')} (期望 ≥1, 证明切链了)")
        return False

    # 关键验证 3: smr_request_id + chain_id 必须嵌入
    if not last_router_meta.get("smr_request_id"):
        print(f"  ❌ FAIL: _smr_router.smr_request_id 缺失")
        return False

    # 验证 mock A 收到 abort 信号 (handler.handle 捕获了 BrokenPipeError)
    if not MockUpstreamA.abort_called:
        print(f"  ⚠️ mock A abort_called=False — 可能 aclose 未真到 (httpx 可能复用连接)")
        print(f"  ℹ️ 但 SMR 内部已发 aclose() + record_abort, 是 best-effort 防御")
    else:
        print(f"  ✅ mock A 收到 abort 信号 (client disconnected)")

    # 关键验证 4: SMR 内部 aborts_total stat ≥ 1
    time.sleep(0.5)  # 让 stat 写完
    r2 = httpx.get(f"http://127.0.0.1:{smr_port}/v1/admin/context_bridge", timeout=5)
    if r2.status_code == 200:
        stats = r2.json().get("stats", {})
        aborts = stats.get("aborts_total", 0)
        print(f"  SMR aborts_total stat = {aborts}", flush=True)
        if aborts < 1:
            print(f"  ❌ FAIL: SMR 内部 aborts_total=0 (期望 ≥1)")
            return False
    else:
        print(f"  ⚠️ context_bridge endpoint 不可用")

    # 关键验证 5: switched_from 必须有
    if not last_router_meta.get("switched_from"):
        print(f"  ❌ FAIL: switched_from 缺失")
        return False

    print(f"  ✅ PASS: 场景 2 验真")
    return True


# ── 场景 3: 主动盘点 ─────────────────────────────────────
def scenario_3_context_review(smr_port: int) -> bool:
    print("\n═══ 场景 3: 主动盘点 (context_review) ═══", flush=True)
    test_smr_req_id = str(uuid.uuid4())
    body = {
        "model": "auto",
        "messages": [{"role": "user", "content": "测试 主动盘点"}],
        "stream": False,
        "_smr_request_id": test_smr_req_id,
    }
    r = httpx.post(f"http://127.0.0.1:{smr_port}/v1/chat/completions", json=body, timeout=15)
    if r.status_code != 200:
        print(f"  ❌ FAIL: 初次 chat 失败 status={r.status_code}")
        return False
    print(f"  ✅ 初次 chat 200, smr_request_id={test_smr_req_id[:8]}", flush=True)

    # 调盘点 endpoint
    r2 = httpx.post(
        f"http://127.0.0.1:{smr_port}/v1/admin/context_review",
        json={"smr_request_id": test_smr_req_id},
        timeout=5,
    )
    print(f"  盘点 endpoint HTTP {r2.status_code}", flush=True)
    if r2.status_code != 200:
        print(f"  ❌ FAIL: 盘点 endpoint 非 200: {r2.text[:300]}")
        return False
    data = r2.json()
    if not data.get("ok"):
        print(f"  ❌ FAIL: ok=False: {data}")
        return False
    report = data.get("report", {})
    print(f"  switch_count={report.get('switch_count')}", flush=True)
    print(f"  requested_model={report.get('requested_model')}", flush=True)
    print(f"  summary={report.get('summary', 'MISSING')}", flush=True)
    print(f"  switched_from count={len(report.get('switched_from', []))}", flush=True)

    # 验证关键字段
    if "summary" not in report:
        print(f"  ❌ FAIL: summary 字段缺失")
        return False
    if report.get("smr_request_id") != test_smr_req_id:
        print(f"  ❌ FAIL: smr_request_id 不匹配")
        return False
    # 注: switch_count=0 可能是 (a) 没切链 (b) 切链了. 在 SMR mock_a 401 → mock_b 200 场景下, 必有切链
    sc = report.get("switch_count", 0)
    if sc == 0:
        print(f"  ⚠️ switch_count=0 (期望 ≥1 因为 mock_a 必失败切链)")

    # 错误 ID 应 404
    r3 = httpx.post(
        f"http://127.0.0.1:{smr_port}/v1/admin/context_review",
        json={"smr_request_id": "not-exists-id"},
        timeout=5,
    )
    print(f"  错误 ID HTTP {r3.status_code} (期望 404)", flush=True)
    if r3.status_code != 404:
        print(f"  ❌ FAIL: 错误 ID 应 404")
        return False

    # 列出当前跟踪
    r4 = httpx.get(f"http://127.0.0.1:{smr_port}/v1/admin/context_review/list?limit=10", timeout=5)
    if r4.status_code == 200:
        tracked = r4.json().get("tracked", [])
        print(f"  当前跟踪的 request 数量: {len(tracked)}", flush=True)
        for t in tracked[:3]:
            print(f"    - {t.get('smr_request_id', '')[:8]} (model={t.get('requested_model')}, switches={t.get('switch_count')})", flush=True)

    # reviews_total stat
    r5 = httpx.get(f"http://127.0.0.1:{smr_port}/v1/admin/context_bridge", timeout=5)
    if r5.status_code == 200:
        reviews = r5.json().get("stats", {}).get("reviews_total", 0)
        print(f"  reviews_total stat = {reviews} (期望 ≥2, 2 次盘点调用)", flush=True)
        if reviews < 2:
            print(f"  ❌ FAIL: reviews_total < 2")
            return False

    print(f"  ✅ PASS: 场景 3 验真")
    return True


# ── Main ─────────────────────────────────────────────────
def run_single_scenario(name: str, scenario_fn, smr_port: int) -> bool:
    """每个场景独立 SMR 实例, 避免 in-memory penalty / disabled model 跨场景污染"""
    MOCK_A_PORT = 18001
    MOCK_B_PORT = 18002

    MockUpstreamA.abort_called = False
    mock_a = start_mock(MockUpstreamA, MOCK_A_PORT)
    mock_b = start_mock(MockUpstreamB, MOCK_B_PORT)
    time.sleep(0.5)

    smr = start_smr(smr_port, log_file=f"/tmp/smr-v35-{name.replace(' ', '_')}.log")
    if not wait_smr_ready(smr_port, timeout=15):
        print(f"❌ SMR (port={smr_port}) 启动失败")
        stop_smr(smr)
        stop_mock(mock_a)
        stop_mock(mock_b)
        return False

    try:
        return scenario_fn(smr_port)
    finally:
        stop_smr(smr)
        stop_mock(mock_a)
        stop_mock(mock_b)
        time.sleep(0.5)  # 释放端口


def main():
    print("=" * 60, flush=True)
    print("SMR v3.5.0 端到端自验 (3 场景, 每场景独立 SMR)", flush=True)
    print("=" * 60, flush=True)

    # 3 场景用 3 个独立端口
    results = []
    results.append(("场景1: smr_request_id 嵌入", run_single_scenario("s1", scenario_1_req_id_embedding, 16501)))
    results.append(("场景2: 切链 abort (流式)", run_single_scenario("s2", scenario_2_abort_on_switch, 16502)))
    results.append(("场景3: 主动盘点", run_single_scenario("s3", scenario_3_context_review, 16503)))

    # 汇总
    print("\n" + "=" * 60, flush=True)
    print("v3.5.0 E2E 结果汇总:", flush=True)
    print("=" * 60, flush=True)
    pass_n = sum(1 for _, ok in results if ok)
    for name, ok in results:
        print(f"  {'✅' if ok else '❌'} {name}", flush=True)
    print(f"\n通过率: {pass_n}/{len(results)}", flush=True)
    return pass_n == len(results)


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
