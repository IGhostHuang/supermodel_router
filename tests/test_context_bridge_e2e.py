"""
SMR v3.4.0 端到端自验 — 上下文桥接 + 过期标记 (重写版)

每个场景独立 SMR 实例 + 独立 state, 避免 penalty 跨场景污染.
"""
import sys
import os
import time
import json
import threading
import subprocess
import asyncio
import signal
import glob
import httpx
from http.server import BaseHTTPRequestHandler, HTTPServer


# ── Mock upstream A: 401 失败 ──
class MockUpstreamA(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs): pass

    def do_GET(self):
        if self.path == "/v1/models":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "object": "list",
                "data": [{
                    "id": "mock-model-a",
                    "object": "model",
                    "created": 0,
                    "owned_by": "mock-a",
                }]
            }).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/v1/chat/completions":
            # 场景 3 测试用: sleep 3s 再返 401, 让 SMR 端 "切链耗时" > stale_threshold
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


# ── Mock upstream B: 200 成功 ──
class MockUpstreamB(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs): pass

    def do_GET(self):
        if self.path == "/v1/models":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "object": "list",
                "data": [{
                    "id": "mock-model-b",
                    "object": "model",
                    "created": 0,
                    "owned_by": "mock-b",
                }]
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
                 "model": "mock-model-b", "choices": [{"index": 0, "delta": {"content": "Mock"}, "finish_reason": None}]},
                {"id": "mock-4", "object": "chat.completion.chunk", "created": int(time.time()),
                 "model": "mock-model-b", "choices": [{"index": 0, "delta": {"content": " B"}, "finish_reason": None}]},
                {"id": "mock-5", "object": "chat.completion.chunk", "created": int(time.time()),
                 "model": "mock-model-b", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
            for c in chunks:
                self.wfile.write(f"data: {json.dumps(c, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
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


# ── Mock upstream 启动/杀 ──
def start_mock(handler_cls, port: int) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", port), handler_cls)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def kill_all_mocks():
    """杀 18001/18002 上的 mock server"""
    for port in (18001, 18002):
        try:
            s = HTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
            # 实际没启, 跳过
        except Exception:
            pass
    # 通过 /proc 扫
    for pid_dir in glob.glob("/proc/[0-9]*"):
        try:
            with open(f"{pid_dir}/cmdline", "rb") as f:
                cmd = f.read().decode(errors="replace")
            # daemon thread 的 mock server 没特征, 跳过
        except (ProcessLookupError, FileNotFoundError, PermissionError):
            continue


# ── SMR state 清理 ──
def clear_smr_state():
    """清掉 SMR 持久化 state 文件 (避免 penalty 跨场景污染)"""
    for f in ("penalty_state.json", "engine_stats.json", "model_rules_state.json"):
        p = f"/root/projects/supermodel_router/{f}"
        if os.path.exists(p):
            os.remove(p)


# ── 启 SMR (独立端口 + 独立 state) ──
def start_smr(port: int, stale_threshold_s: int = 1800, log_file: str = "/tmp/smr-e2e.log") -> subprocess.Popen:
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
  stale_threshold_seconds: {stale_threshold_s}
  max_history: 5
  sentinel_enabled: true
  version: "3.4.0"
"""
    cfg_path = f"/tmp/smr-scenario-config-{port}.yaml"
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
    try:
        proc._log_handle.close()
    except Exception:
        pass


def wait_for_smr(port: int, timeout: float = 10) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/v1/health", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


# ── 场景 1: 非流式切换 ──
async def scenario_1_nonstream(port: int) -> dict:
    print("\n[场景 1] 非流式切换 — A 401 → B 200 + switched_from metadata")
    async with httpx.AsyncClient() as client:
        r = await client.post(f"http://127.0.0.1:{port}/v1/chat/completions", json={
            "model": "auto",
            "messages": [{"role": "user", "content": "你好"}],
        }, timeout=15)
    print(f"  HTTP {r.status_code}")
    body = r.json()
    rm = body.get("_router", {})
    sf = rm.get("switched_from", [])
    print(f"  response model: {body.get('model')}")
    print(f"  chain_position: {rm.get('chain_position')}, chain_size: {rm.get('chain_size')}")
    print(f"  switched_from: {len(sf)} entries")
    for rec in sf:
        print(f"    {rec.get('from_full_path')} status={rec.get('response_status')} http={rec.get('http_code')}")
    print(f"  stale: {rm.get('stale')}, age_seconds: {rm.get('age_seconds')}")
    return {
        "status_code": r.status_code,
        "has_switched_from": bool(sf),
        "switched_from_count": len(sf),
        "stale": rm.get("stale"),
        "chain_position": rm.get("chain_position"),
    }


# ── 场景 2: 流式切换 ──
async def scenario_2_stream(port: int) -> dict:
    print("\n[场景 2] 流式切换 — A 流式 401 → B 流式 200 + SSE sentinel")
    chunks_received = []
    async with httpx.AsyncClient() as client:
        async with client.stream("POST", f"http://127.0.0.1:{port}/v1/chat/completions", json={
            "model": "auto",
            "stream": True,
            "messages": [{"role": "user", "content": "你好"}],
        }, timeout=15) as r:
            async for line in r.aiter_lines():
                if line.strip():
                    chunks_received.append(line)

    print(f"  收到 {len(chunks_received)} 行 SSE")
    for i, line in enumerate(chunks_received):
        print(f"    [{i}] {line[:160]}")
    # 找 sentinel
    sentinel_payload = None
    for line in chunks_received:
        if line.startswith("data:") and "_smr_bridge" in line:
            try:
                sentinel_payload = json.loads(line[5:].strip())
            except Exception as e:
                print(f"  WARN parse err: {e}")
            break
    b_chunks = [l for l in chunks_received if l.startswith("data:") and "_smr_bridge" not in l]
    print(f"  B 流式 chunks: {len(b_chunks)}")
    if sentinel_payload:
        meta = sentinel_payload.get("_smr_bridge", {})
        print(f"  sentinel.version: {meta.get('version')}")
        print(f"  sentinel.switched_from_count: {meta.get('switched_from_count')}")
        print(f"  sentinel.stale: {meta.get('stale')}")
        print(f"  sentinel.age_seconds: {meta.get('age_seconds')}")
    return {
        "sentinel_found": sentinel_payload is not None,
        "bridge_payload": sentinel_payload,
        "b_chunks_count": len(b_chunks),
    }


# ── 场景 3: 过期 (threshold=2s + sleep 3s) ──
async def scenario_3_stale(port: int) -> dict:
    print("\n[场景 3] 过期标记 — threshold=2s, mock_a 自身 sleep 3s 再 401 → B 200 → stale=True")
    print("  (mock_a 处理 3s 后才失败, SMR 切到 B 时, request 已耗时 3s > threshold=2s → stale)")
    async with httpx.AsyncClient() as client:
        r = await client.post(f"http://127.0.0.1:{port}/v1/chat/completions", json={
            "model": "auto",
            "messages": [{"role": "user", "content": "你好"}],
        }, timeout=20)
    body = r.json()
    rm = body.get("_router", {})
    sf = rm.get("switched_from", [])
    print(f"  HTTP {r.status_code}")
    print(f"  response model: {body.get('model')}")
    print(f"  switched_from: {len(sf)} entries")
    print(f"  stale: {rm.get('stale')}")
    print(f"  age_seconds: {rm.get('age_seconds')}")
    print(f"  stale_threshold_seconds: {rm.get('stale_threshold_seconds')}")
    for rec in sf:
        print(f"    {rec.get('from_full_path')} stale={rec.get('stale')}")
    return {
        "status_code": r.status_code,
        "stale": rm.get("stale"),
        "age_seconds": rm.get("age_seconds"),
        "switched_from_count": len(sf),
    }


# ── Main ──
async def main():
    # 杀残留 SMR + 起 mock
    clear_smr_state()
    # 杀 SMR 残留
    for pid_dir in glob.glob("/proc/[0-9]*"):
        try:
            with open(f"{pid_dir}/cmdline", "rb") as f:
                cmd = f.read().decode(errors="replace")
            if "run.py" in cmd and ("smr-scenario" in cmd or "smr-test" in cmd or "smr-trace" in cmd):
                pid = int(pid_dir.split("/")[-1])
                os.kill(pid, signal.SIGKILL)
                print(f"  killed stale SMR pid={pid}")
        except (ProcessLookupError, PermissionError, FileNotFoundError):
            continue

    print("[setup] 启动 Mock Upstream A (18001) + B (18002)")
    server_a = start_mock(MockUpstreamA, 18001)
    server_b = start_mock(MockUpstreamB, 18002)

    # 3 个独立 SMR 实例
    ports = [16473, 16474, 16475]
    logs = [f"/tmp/smr-scenario-{i+1}.log" for i in range(3)]

    print("[setup] 启动 3 个独立 SMR 实例 (3 场景互不污染)")
    procs = []
    for i, (p, log) in enumerate(zip(ports, logs)):
        stale = 1800 if i < 2 else 2  # 场景 3 用 threshold=2s
        proc = start_smr(p, stale_threshold_s=stale, log_file=log)
        procs.append(proc)
        if not wait_for_smr(p):
            print(f"❌ SMR #{i+1} 启动失败")
            for pr in procs: stop_smr(pr)
            return False
        print(f"  SMR #{i+1} ready on port {p} (stale_threshold={stale}s)")

    # 跑 3 场景 (独立 SMR, 互不污染)
    try:
        res1 = await scenario_1_nonstream(ports[0])
        res2 = await scenario_2_stream(ports[1])
        # 场景 3 sleep 在场景内做 (避免 e2e 跟 SMR 互相等)
        res3 = await scenario_3_stale(ports[2])

        # 验证
        print("\n" + "="*60)
        print("验证结果:")
        print("="*60)
        checks = [
            ("场景1 - HTTP 200", res1.get("status_code") == 200),
            ("场景1 - 有 switched_from metadata", res1.get("has_switched_from")),
            ("场景1 - switched_from 数量=1", res1.get("switched_from_count") == 1),
            ("场景1 - chain_position=1 (切到 B)", res1.get("chain_position") == 1),
            ("场景1 - stale=False (默认 30min threshold)", res1.get("stale") == False),
            ("场景2 - 流式收到 sentinel", res2.get("sentinel_found")),
            ("场景2 - sentinel 含 _smr_bridge", res2.get("bridge_payload", {}).get("_smr_bridge") is not None if res2.get("bridge_payload") else False),
            ("场景2 - sentinel 标记了 switched_from", res2.get("bridge_payload", {}).get("_smr_bridge", {}).get("switched_from_count", 0) >= 1 if res2.get("bridge_payload") else False),
            ("场景2 - B 流式至少 1 chunk", res2.get("b_chunks_count", 0) >= 1),
            ("场景3 - 过期 stale=True", res3.get("stale") == True),
            ("场景3 - age_seconds >= 2", (res3.get("age_seconds") or 0) >= 2),
            ("场景3 - switched_from 数量=1", res3.get("switched_from_count") == 1),
        ]
        pass_count = 0
        for name, ok in checks:
            mark = "✅" if ok else "❌"
            print(f"  {mark} {name}")
            if ok: pass_count += 1
        print(f"\n{pass_count}/{len(checks)} checks passed")
        return pass_count == len(checks)
    finally:
        for pr in procs:
            stop_smr(pr)
        server_a.shutdown()
        server_b.shutdown()


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
