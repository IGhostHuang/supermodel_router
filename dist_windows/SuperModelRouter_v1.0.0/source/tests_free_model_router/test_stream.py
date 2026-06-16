"""
流式 chat 测试 - 验证 SSE 流式响应 + 多 key 轮询
"""
import json
import subprocess
import time
import sys
import os
import httpx

FMR_URL = "http://127.0.0.1:19876"
MOCK_URL = "http://127.0.0.1:18765"

def check_fmr_running():
    try:
        r = httpx.get(f"{FMR_URL}/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False

def test_stream_chat():
    """流式 chat: 验证 SSE 格式 + 内容"""
    print("\n=== TEST STREAM: 流式 chat (SSE) ===")
    body = {
        "model": "gpt-4o-free",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 50,
        "stream": True,
    }
    chunks = []
    try:
        with httpx.stream("POST", f"{FMR_URL}/v1/chat/completions",
                          json=body, timeout=10) as resp:
            assert resp.status_code == 200, f"status={resp.status_code}"
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    data = line[6:].strip()
                    if data == "[DONE]":
                        print("  ✅ 收到 [DONE] 终止符")
                        break
                    try:
                        chunk = json.loads(data)
                        chunks.append(chunk)
                    except json.JSONDecodeError:
                        print(f"  ⚠️  无法解析: {data[:80]}")
    except Exception as e:
        print(f"  ❌ 流式请求失败: {e}")
        return False

    if not chunks:
        print("  ❌ 没收到任何 chunk")
        return False

    # 验证 chunk 格式
    for c in chunks:
        assert "id" in c, f"chunk 缺 id: {c}"
        assert "object" in c, f"chunk 缺 object: {c}"
        assert c["object"] == "chat.completion.chunk", f"object 类型错: {c['object']}"
        assert "choices" in c, f"chunk 缺 choices: {c}"

    # 验证 delta 累计
    full_content = ""
    for c in chunks:
        delta = c.get("choices", [{}])[0].get("delta", {})
        full_content += delta.get("content", "")
    print(f"  ✅ 收到 {len(chunks)} 个 chunk, 完整内容: {full_content!r}")

    return len(chunks) >= 2 and len(full_content) > 0


def test_stream_with_bad_key():
    """流式 chat 第一个 key 坏, 验证能切到下一个 key 流式输出"""
    print("\n=== TEST STREAM: 流式 chat + 坏 key 切到好 key ===")
    # 跑 3 次, 期望 key-bbb/ccc 中至少 1 次成功
    results = []
    for i in range(3):
        body = {
            "model": "gpt-4o-free",
            "messages": [{"role": "user", "content": f"msg-{i}"}],
            "max_tokens": 30,
            "stream": True,
        }
        try:
            with httpx.stream("POST", f"{FMR_URL}/v1/chat/completions",
                              json=body, timeout=10) as resp:
                got_content = False
                for line in resp.iter_lines():
                    if line.startswith("data: "):
                        data = line[6:].strip()
                        if data == "[DONE]":
                            break
                        chunk = json.loads(data)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        if delta.get("content"):
                            got_content = True
                results.append((resp.status_code, got_content))
        except Exception as e:
            results.append((0, False))
            print(f"  [请求 {i+1}] 异常: {e}")

    success = sum(1 for s, c in results if s == 200 and c)
    print(f"  3 次流式请求: 状态={[s for s, c in results]}, 内容成功={success}/3")
    return success >= 1


def main():
    if not check_fmr_running():
        print("❌ fmr 没启动, 请先启动 fmr 和 mock")
        return 1
    print(f"✅ fmr 运行中 @ {FMR_URL}")

    results = []
    results.append(("流式 chat 基本", test_stream_chat()))
    results.append(("流式 chat + 坏 key 切 key", test_stream_with_bad_key()))

    print("\n" + "=" * 50)
    print("STREAM 测试结果:")
    passed = 0
    for name, ok in results:
        print(f"  {'✅' if ok else '❌'} {name}")
        if ok:
            passed += 1
    print(f"\n通过: {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
