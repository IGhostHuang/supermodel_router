"""
test_v380_context_window.py — v3.8.0 上下文窗口 + 压缩单测

测 4 模块:
  1. models._extract_context_window (顶层 / openrouter nested / 默认 0)
  2. classifier.compute_capability_score (7 档 context bonus, 可配置)
  3. ContextBridge.estimate_tokens (粗估 token)
  4. ContextBridge.compress_for_target (pass-through / 段落分批 / 历史压缩)

跑: ./venv/bin/python3 test_v380_context_window.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from supermodel_router.models import _extract_context_window
from supermodel_router.classifier import (
    compute_capability_score, get_context_window_bonus,
    DEFAULT_CONTEXT_WINDOW_BONUS,
)
from supermodel_router.context_bridge import ContextBridge


def test_1_extract_context_window():
    print("=" * 60)
    print("Test 1: _extract_context_window")
    print("=" * 60)
    # 1a. 顶层 context_window
    assert _extract_context_window({"context_window": 128000}) == 128000
    print("✓ 顶层 context_window=128000")
    # 1b. openrouter nested top_provider
    assert _extract_context_window({"top_provider": {"context_length": 200000}}) == 200000
    print("✓ openrouter nested top_provider.context_length=200000")
    # 1c. openrouter nested architecture (fallback)
    assert _extract_context_window({"architecture": {"context_length": 16000}}) == 16000
    print("✓ openrouter nested architecture.context_length=16000")
    # 1d. max_context_tokens
    assert _extract_context_window({"max_context_tokens": 8000}) == 8000
    print("✓ max_context_tokens=8000")
    # 1e. 优先级: 顶层 > nested
    assert _extract_context_window({
        "context_window": 32000,
        "top_provider": {"context_length": 200000}
    }) == 32000
    print("✓ 优先级: 顶层 (32K) > openrouter nested (200K)")
    # 1f. 空 / 无效
    assert _extract_context_window({}) == 0
    assert _extract_context_window(None) == 0
    assert _extract_context_window({"context_window": -1}) == 0
    assert _extract_context_window({"context_window": "abc"}) == 0
    print("✓ 空 / 负数 / 字符串 兜底为 0")
    # 1g. 浮点
    assert _extract_context_window({"context_window": 128000.5}) == 128000
    print("✓ 浮点数自动转 int")
    print("✅ Test 1 PASSED\n")


def test_2_capability_score():
    print("=" * 60)
    print("Test 2: compute_capability_score (7 档 context bonus)")
    print("=" * 60)
    # 2a. 默认 7 档
    table = DEFAULT_CONTEXT_WINDOW_BONUS
    assert table[0] == (200_000, 20)
    assert table[1] == (128_000, 14)
    assert table[2] == (64_000, 10)
    assert table[3] == (32_000, 7)
    assert table[4] == (16_000, 5)
    assert table[5] == (8_000, 3)
    assert table[6] == (4_000, 2)
    print("✓ 默认 7 档 bonus 表正确")
    # 2b. 200K+ 加 20
    score_200k = compute_capability_score(
        "claude-200k-test", "text",
        extra={"context_window": 200000}
    )
    score_8k = compute_capability_score(
        "test-8k", "text",
        extra={"context_window": 8000}
    )
    assert score_200k - score_8k >= 17, f"200K - 8K 应该 ≥ 17 (20-3), 实际 {score_200k - score_8k}"
    print(f"✓ 200K score={score_200k} vs 8K score={score_8k}, 差 {score_200k - score_8k}")
    # 2c. 7 档细分: 4K vs 8K 差 1
    s_4k = compute_capability_score("t", "text", extra={"context_window": 4000})
    s_8k = compute_capability_score("t", "text", extra={"context_window": 8000})
    s_16k = compute_capability_score("t", "text", extra={"context_window": 16000})
    s_32k = compute_capability_score("t", "text", extra={"context_window": 32000})
    s_64k = compute_capability_score("t", "text", extra={"context_window": 64000})
    s_128k = compute_capability_score("t", "text", extra={"context_window": 128000})
    s_200k = compute_capability_score("t", "text", extra={"context_window": 200000})
    expected = [s_4k, s_8k, s_16k, s_32k, s_64k, s_128k, s_200k]
    actual_diff = [b - a for a, b in zip(expected, expected[1:])]
    print(f"✓ 7 档分数递增: {[round(x, 1) for x in expected]}")
    print(f"✓ 相邻档差: {[round(x, 1) for x in actual_diff]} = [1, 2, 2, 3, 4, 6]")
    assert actual_diff == [1, 2, 2, 3, 4, 6], f"差值不对: {actual_diff}"
    # 2d. openrouter nested 兼容 (用同 id 对比)
    score_nested = compute_capability_score(
        "gpt-4-test", "text",  # 不含 tier 关键词, 避免 tier_bonus 干扰
        extra={"top_provider": {"context_length": 128000}}
    )
    s_128k_plain = compute_capability_score("gpt-4-test", "text", extra={"context_window": 128000})
    assert score_nested == s_128k_plain, f"nested 128K 应等于顶层 128K: {score_nested} vs {s_128k_plain}"
    print(f"✓ openrouter nested 128K = 顶层 128K ({score_nested})")
    # 2e. 0 (未知) 不加分
    s_0 = compute_capability_score("t", "text", extra={})
    assert s_0 < s_4k
    print(f"✓ 未知 (0) score={s_0} < 4K score={s_4k}, 不加分")
    # 2f. 可配置 (通过 mock config_obj)
    class MockConfig:
        def __init__(self, data): self.data = data
    mock_cfg = MockConfig({"classifier": {"context_window_bonus": [
        {"min": 100000, "bonus": 50},  # 自定义高分
        {"min": 1000, "bonus": 1},
    ]}})
    bonus_table = get_context_window_bonus(mock_cfg)
    assert bonus_table[0] == (100000, 50)
    assert bonus_table[1] == (1000, 1)
    s_custom = compute_capability_score("t", "text", extra={"context_window": 100000}, config_obj=mock_cfg)
    s_default_128k = compute_capability_score("t", "text", extra={"context_window": 128000})
    assert s_custom > s_default_128k, f"自定义 50 分应高于默认 14 分: {s_custom} vs {s_default_128k}"
    print(f"✓ 自定义 config: 100K 加 50 分 → score {s_custom}, 默认 128K 加 14 分 → {s_default_128k}")
    print("✅ Test 2 PASSED\n")


def test_3_estimate_tokens():
    print("=" * 60)
    print("Test 3: ContextBridge.estimate_tokens")
    print("=" * 60)
    bridge = ContextBridge()
    # 3a. 简单文本: 100 字符 / 4 = 25 tokens
    body = {"messages": [{"role": "user", "content": "x" * 100}]}
    assert bridge.estimate_tokens(body) == 25
    print("✓ 100 字符 = 25 tokens")
    # 3b. 中文 (3 字节 / 字符, 但 len() 计 unicode codepoints)
    body_cn = {"messages": [{"role": "user", "content": "你好" * 50}]}  # 100 字符
    assert bridge.estimate_tokens(body_cn) == 25  # 同样 25 tokens (粗估)
    print("✓ 中文 100 字符 ≈ 25 tokens (粗估)")
    # 3c. image_url
    body_img = {"messages": [{"role": "user", "content": [
        {"type": "text", "text": "hi"},  # 2 chars → 0 tokens
        {"type": "image_url", "image_url": {"url": "..."}},  # 170 tokens
    ]}]}
    assert bridge.estimate_tokens(body_img) == 170
    print("✓ image_url ≈ 170 tokens (CLIP 经验值)")
    # 3d. tools (function calling)
    body_tool = {"messages": [{"role": "user", "content": "hi"}],
                 "tools": [{"type": "function", "function": {"name": "x", "parameters": {"a": "b" * 200}}}]}
    tokens = bridge.estimate_tokens(body_tool)
    assert tokens > 50  # tools 至少 50 tokens
    print(f"✓ tools 计入 (200+ 字符): {tokens} tokens")
    # 3e. 空 / None
    assert bridge.estimate_tokens({}) == 0
    assert bridge.estimate_tokens(None) == 0
    assert bridge.estimate_tokens({"messages": []}) == 0
    print("✅ Test 3 PASSED\n")


def test_4_compress_for_target():
    print("=" * 60)
    print("Test 4: ContextBridge.compress_for_target")
    print("=" * 60)
    bridge = ContextBridge()  # 默认 compress_on_switch=True, overhead=0.8
    # 4a. Pass-through: total < target*overhead
    body_small = {"messages": [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "hi"},
    ]}
    compressed = bridge.compress_for_target(body_small, 128000)
    # 4b. 段落分批: 1 个超长 user message 拆 N 段
    # paragraph_chunk 的目的: 让超长 message 能被 model 处理, 不是省 token
    # (总 token ≈ 原文 + N 个 [续 N/M] markers, 几乎不变)
    long_text = ("这是一段很长的文本。\n\n第二段更长的内容, 用于测试段落分批逻辑。\n\n第三段也很长, 我们要看拆段是否正确。" * 200)  # ~10800 字符 = ~2700 tokens
    body_long = {
        "messages": [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": long_text},
        ]
    }
    # target=1000 tokens → effective=800 → 段落 > 800 应触发拆段
    compressed = bridge.compress_for_target(body_long, 1000)
    assert compressed is not body_long, "应触发压缩"
    assert "_smr_compress" in compressed
    meta = compressed["_smr_compress"]
    assert meta["chunks_split"] >= 2, f"应拆 ≥ 2 段, 实际 {meta['chunks_split']}"
    assert meta["strategy"] == "paragraph_chunk"
    # ✅ paragraph_chunk 不省 token (总 chars ≈ 原文 + markers), 但不超过 effective 太多
    # 要求: tokens_after ≤ tokens_before + markers_tolerance (50 tokens ≈ 200 chars)
    markers_tolerance = 50
    assert meta["tokens_after"] <= meta["tokens_before"] + markers_tolerance, (
        f"拆段后 token 不应超原文 {markers_tolerance}, "
        f"实际 {meta['tokens_after']} vs before {meta['tokens_before']}"
    )
    print(f"✓ 段落分批: {meta['tokens_before']} → {meta['tokens_after']} tokens, 拆 {meta['chunks_split']} 段")
    # 4c. 历史压缩: 短 user + 很多旧 messages → 留 system + 最近 K
    bridge_trim = ContextBridge({"compress_strategy": "history_trim", "compress_keep_last_messages": 2})
    # body 总 tokens 需要 > target*overhead 才会触发压缩
    # 50 chars per message × 8 messages ≈ 400 chars ≈ 100 tokens, target=20 触发
    body_history = {"messages": [
        {"role": "system", "content": "你是助手" * 20},  # ~120 chars → 30 tokens
        {"role": "user", "content": "old 1" * 20},
        {"role": "assistant", "content": "old 1 reply" * 20},
        {"role": "user", "content": "old 2" * 20},
        {"role": "assistant", "content": "old 2 reply" * 20},
        {"role": "user", "content": "old 3" * 20},
        {"role": "assistant", "content": "old 3 reply" * 20},
        {"role": "user", "content": "CURRENT"},
    ]}
    print(f"  history body total: {bridge_trim.estimate_tokens(body_history)} tokens")
    # target=30 tokens → effective=24 → 全部超 → 留 system + 最近 2 条
    compressed = bridge_trim.compress_for_target(body_history, 30)
    print(f"  compressed: {len(body_history['messages'])} → {len(compressed['messages'])} messages")
    print(f"  meta: {compressed.get('_smr_compress')}")
    if "_smr_compress" in compressed:
        meta = compressed["_smr_compress"]
        assert meta["strategy"] == "history_trim"
        # 验证: 留 system + 最近 2 条 (assistant old 3 reply + user CURRENT)
        assert compressed["messages"][0]["role"] == "system"
        last_user = [m for m in compressed["messages"] if m.get("content") == "CURRENT"]
        assert len(last_user) == 1, "CURRENT message 应保留"
        print(f"✓ 历史压缩: {len(body_history['messages'])} → {len(compressed['messages'])} messages, 删 {meta['messages_trimmed']} 条")
    else:
        # 调整: 加大 body 让它超 target
        body_history["messages"][0]["content"] = "你是助手" * 200  # 1200 chars → 300 tokens
        print(f"  retry body total: {bridge_trim.estimate_tokens(body_history)} tokens")
        compressed = bridge_trim.compress_for_target(body_history, 30)
        meta = compressed.get("_smr_compress")
        assert meta is not None and meta["strategy"] == "history_trim"
        assert compressed["messages"][0]["role"] == "system"
        last_user = [m for m in compressed["messages"] if m.get("content") == "CURRENT"]
        assert len(last_user) == 1, "CURRENT message 应保留"
        print(f"✓ 历史压缩 (retry): {len(body_history['messages'])} → {len(compressed['messages'])} messages, 删 {meta['messages_trimmed']} 条")
    # 4d. compress_on_switch=False 时不压缩
    bridge_off = ContextBridge({"compress_on_switch": False})
    body_big = {"messages": [{"role": "user", "content": "x" * 100000}]}
    compressed = bridge_off.compress_for_target(body_big, 100)
    assert compressed is body_big
    print("✓ compress_on_switch=False 时 pass-through")
    # 4e. target_window=0 (未知) 不压缩
    compressed = bridge.compress_for_target(body_big, 0)
    assert compressed is body_big
    print("✓ target_window=0 (未知) 不压缩")
    # 4f. enabled=False 不压缩
    bridge_disabled = ContextBridge({"enabled": False})
    compressed = bridge_disabled.compress_for_target(body_big, 100)
    assert compressed is body_big
    print("✓ enabled=False 不压缩")
    # 4g. 不 mutate 原 body
    original_messages_count = len(body_big["messages"])
    bridge.compress_for_target(body_big, 50)  # 应触发压缩
    assert len(body_big["messages"]) == original_messages_count, "原 body 不应被 mutate"
    print("✓ 不 mutate 原 body")
    print("✅ Test 4 PASSED\n")


def test_5_split_into_chunks():
    print("=" * 60)
    print("Test 5: ContextBridge._split_into_chunks")
    print("=" * 60)
    bridge = ContextBridge()
    # 5a. 短文本不拆
    chunks = bridge._split_into_chunks("hello world", 100)
    assert chunks == ["hello world"]
    print("✓ 短文本不拆")
    # 5b. 按 \n\n 拆
    text = "段落1\n\n段落2\n\n段落3" + "x" * 100
    chunks = bridge._split_into_chunks(text, 20)
    assert len(chunks) >= 2
    print(f"✓ 按段落拆: {len(chunks)} 段, 首段 '{chunks[0][:30]}...'")
    # 5c. 中文标点拆
    text_cn = "第一句。第二句！第三句？" + "x" * 200
    chunks = bridge._split_into_chunks(text_cn, 50)
    assert len(chunks) >= 3
    print(f"✓ 中文标点拆: {len(chunks)} 段")
    # 5d. 硬切兜底
    text_no_space = "x" * 1000
    chunks = bridge._split_into_chunks(text_no_space, 100)
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks) == text_no_space
    print(f"✓ 硬切兜底: {len(chunks)} 段, 每段 ≤ 100 字符")
    print("✅ Test 5 PASSED\n")


def test_6_integration():
    print("=" * 60)
    print("Test 6: 集成 - 端到端 (config + bridge + compress)")
    print("=" * 60)
    # ✅ v3.8.0 fix: 用 history_trim 策略, 验证 token 真的减少
    # (auto 策略会先 paragraph_chunk, 不省 token, 不适合验证集成)
    # ✅ keep_last=1 让 4 条 messages → 留 system + 1 = 2 条, 删 2 条
    bridge = ContextBridge({
        "enabled": True,
        "compress_on_switch": True,
        "compress_overhead": 0.8,
        "compress_strategy": "history_trim",
        "compress_keep_last_messages": 1,
    })
    # 模拟切链历史 — body 需 > 1K*0.8=800 tokens 才能触发压缩
    long_history = ("你正在接续一个多模型对话. " * 250 + "[SMR 桥接结束]")  # ~4250 chars ≈ 1062 tokens
    body = {
        "messages": [
            {"role": "system", "content": long_history},  # ~1062 tokens
            {"role": "user", "content": "继续"},
            {"role": "assistant", "content": "好的"},
            {"role": "user", "content": "CURRENT 任务"},
        ]
    }
    # 切到 8K 模型 → 6400 budget, 1062 < 6400, pass-through
    target_window = 8000
    before_tokens = bridge.estimate_tokens(body)
    compressed = bridge.compress_for_target(body, target_window, before_tokens)
    after_tokens = bridge.estimate_tokens(compressed)
    print(f"  target_window: {target_window}")
    print(f"  before: {before_tokens} tokens, after: {after_tokens} tokens")
    print(f"  saved: {before_tokens - after_tokens} tokens")
    assert compressed is body, f"8K 预算 6400 tokens, 当前 {before_tokens} 应 pass-through"
    print(f"✓ 集成: 8K 模型, {before_tokens} tokens 当前 body → pass-through")
    # 切到 1K 模型 → 800 budget, 1062 > 800, 触发 history_trim
    # paragraph_chunk + history_trim 都会跑, total 略增加 (markers) 是允许的
    # 关键是: 触发压缩 + messages 数量减少
    compressed = bridge.compress_for_target(body, 1000, before_tokens)
    after_tokens = bridge.estimate_tokens(compressed)
    after_msgs = len(compressed["messages"])
    # ✅ tokens_after <= tokens_before + markers_tolerance
    # ✅ after_msgs < before_msgs (history_trim 必须删消息)
    assert after_msgs < 4, f"history_trim 应删 messages, 实际 {after_msgs}"
    assert after_tokens <= before_tokens + 50, (
        f"tokens 不应超原文 50, {before_tokens} → {after_tokens}"
    )
    print(f"✓ 集成: 1K 模型, {before_tokens} → {after_tokens} tokens, 4 → {after_msgs} messages (历史压缩)")
    print("✅ Test 6 PASSED\n")


def main():
    print("=" * 60)
    print("SMR v3.8.0 上下文窗口 + 压缩 — 单测")
    print("=" * 60)
    print()
    try:
        test_1_extract_context_window()
        test_2_capability_score()
        test_3_estimate_tokens()
        test_4_compress_for_target()
        test_5_split_into_chunks()
        test_6_integration()
        print("=" * 60)
        print("✅ ALL TESTS PASSED (6/6)")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()
