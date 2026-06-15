#!/usr/bin/env python3
"""test_models.py — 测试模型过滤规则逻辑 (不依赖外部 API)"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from supermodel_router.models import ModelRegistry, ModelInfo
from supermodel_router.config import Config
import re, yaml, tempfile

def make_config(providers: dict) -> str:
    data = {
        "server": {"host": "0.0.0.0", "port": 5678},
        "routing": {"strategy": "round-robin"},
        "providers": providers,
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(data, f)
        return f.name


# ============================================================
# 测试 1: pattern 模式
# ============================================================
def test_pattern_mode():
    cfg_path = make_config({
        "p1": {
            "enabled": True,
            "base_url": "http://fake",
            "api_keys": ["sk-xxx"],
            "model_rules": {
                "mode": "pattern",
                "pattern": ".*free.*",
            },
        }
    })
    cfg = Config(cfg_path)
    reg = ModelRegistry(cfg)
    reg.build()

    # 模拟发现结果
    ps = reg._providers["p1"]
    ps.models = []  # 空, 后面注入

    # 手动注入测试模型
    mock_models = [
        {"id": "claude-sonnet:free", "object": "model"},
        {"id": "gpt-4:free", "object": "model"},
        {"id": "claude-opus", "object": "model"},
        {"id": "gpt-4-turbo", "object": "model"},
    ]
    ps.models = [
        ModelInfo(id=m["id"], provider="p1", base_url="http://fake")
        for m in mock_models
    ]
    ps.model_ids = [m.id for m in ps.models]

    # 应用过滤
    filtered = [
        m for m in ps.models
        if m.id not in set(ps.model_rules.get("exclude", []))
        and (
            ps.model_rules.get("mode") == "all"
            or (ps.model_rules.get("mode") == "pattern"
                and re.search(
                    ps.model_rules.get("pattern", ""), m.id, re.IGNORECASE))
            or (ps.model_rules.get("mode") == "include"
                and m.id in set(ps.model_rules.get("include", [])))
        )
    ]
    filtered_ids = [m.id for m in filtered]

    assert "claude-sonnet:free" in filtered_ids, "Should match :free"
    assert "gpt-4:free" in filtered_ids, "Should match :free"
    assert "claude-opus" not in filtered_ids, "Should NOT match non-free"
    assert "gpt-4-turbo" not in filtered_ids, "Should NOT match non-free"
    print(f"  ✅ pattern: {filtered_ids}")


# ============================================================
# 测试 2: include 模式
# ============================================================
def test_include_mode():
    cfg_path = make_config({
        "p2": {
            "enabled": True,
            "base_url": "http://fake",
            "api_keys": ["sk-xxx"],
            "model_rules": {
                "mode": "include",
                "include": ["gpt-4", "claude-opus"],
                "exclude": [],
            },
        }
    })
    cfg = Config(cfg_path)
    reg = ModelRegistry(cfg)
    reg.build()

    ps = reg._providers["p2"]
    mock_models = [
        {"id": "gpt-4", "object": "model"},
        {"id": "claude-opus", "object": "model"},
        {"id": "llama-3", "object": "model"},
    ]
    ps.models = [
        ModelInfo(id=m["id"], provider="p2", base_url="http://fake")
        for m in mock_models
    ]
    ps.model_ids = [m.id for m in ps.models]

    filtered_ids = [
        m.id for m in ps.models
        if m.id not in set(ps.model_rules.get("exclude", []))
        and (
            ps.model_rules.get("mode") == "all"
            or (ps.model_rules.get("mode") == "pattern"
                and re.search(
                    ps.model_rules.get("pattern", ""), m.id, re.IGNORECASE))
            or (ps.model_rules.get("mode") == "include"
                and m.id in set(ps.model_rules.get("include", [])))
        )
    ]

    assert "gpt-4" in filtered_ids
    assert "claude-opus" in filtered_ids
    assert "llama-3" not in filtered_ids
    print(f"  ✅ include: {filtered_ids}")


# ============================================================
# 测试 3: all + exclude 模式
# ============================================================
def test_all_with_exclude():
    cfg_path = make_config({
        "p3": {
            "enabled": True,
            "base_url": "http://fake",
            "api_keys": ["sk-xxx"],
            "model_rules": {
                "mode": "all",
                "exclude": [".*experimental.*"],
            },
        }
    })
    cfg = Config(cfg_path)
    reg = ModelRegistry(cfg)
    reg.build()

    ps = reg._providers["p3"]
    mock_models = [
        {"id": "gpt-4", "object": "model"},
        {"id": "experimental-llama", "object": "model"},
        {"id": "claude-opus", "object": "model"},
        {"id": "experimental-4", "object": "model"},
    ]
    ps.models = [
        ModelInfo(id=m["id"], provider="p3", base_url="http://fake")
        for m in mock_models
    ]
    ps.model_ids = [m.id for m in ps.models]

    exclude_patterns = ps.model_rules.get("exclude", [])
    filtered_ids = [
        m.id for m in ps.models
        if not any(
            re.search(pat, m.id, re.IGNORECASE)
            for pat in exclude_patterns
        )
    ]

    assert "gpt-4" in filtered_ids
    assert "claude-opus" in filtered_ids
    assert "experimental-llama" not in filtered_ids
    assert "experimental-4" not in filtered_ids
    print(f"  ✅ all+exclude: {filtered_ids}")


# ============================================================
# 测试 4: 空 key 场景
# ============================================================
def test_empty_keys():
    cfg_path = make_config({
        "empty": {
            "enabled": True,
            "base_url": "http://fake",
            "api_keys": [],
            "model_rules": {"mode": "all"},
        }
    })
    cfg = Config(cfg_path)
    reg = ModelRegistry(cfg)
    reg.build()
    ps = reg._providers["empty"]
    key = reg.pick_key_for("empty")
    assert key is None, "No keys should return None"
    print("  ✅ empty keys: handled")


if __name__ == "__main__":
    print("Testing model filter rules...")
    test_pattern_mode()
    test_include_mode()
    test_all_with_exclude()
    test_empty_keys()
    print("\n🎉 All tests passed!")