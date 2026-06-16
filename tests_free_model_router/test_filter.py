"""
free-model-router 单元测试 — filter 模块 (4 种免费模型识别策略)
"""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from free_model_router.filter import (
    filter_models,
    filter_model_ids,
    get_strategy,
    STRATEGIES,
)


MODELS = [
    {"id": "gpt-4o"},
    {"id": "gpt-4o-mini"},
    {"id": "claude-3-sonnet"},
    {"id": "claude-3-opus"},
    {"id": "llama-3-free"},
    {"id": "llama-3.1-70b-free"},
    {"id": "gemini-2.5-pro"},
    {"id": "o1-preview"},
    {"id": "o3-mini"},
]


class TestFilterAll(unittest.TestCase):
    def test_all_returns_everything(self):
        out = filter_models(MODELS, mode="all")
        self.assertEqual(len(out), len(MODELS))


class TestFilterPattern(unittest.TestCase):
    def test_free_keyword(self):
        out = filter_models(MODELS, mode="pattern", pattern=r".*free.*")
        ids = [m["id"] for m in out]
        self.assertIn("llama-3-free", ids)
        self.assertIn("llama-3.1-70b-free", ids)
        self.assertNotIn("gpt-4o", ids)
        self.assertNotIn("o1-preview", ids)

    def test_turbo_keyword(self):
        out = filter_models(MODELS, mode="pattern", pattern=r"o\d")
        ids = [m["id"] for m in out]
        self.assertIn("o1-preview", ids)
        self.assertIn("o3-mini", ids)
        self.assertNotIn("gpt-4o-mini", ids)  # 'o4o-mini' 不含 o1/o3

    def test_invalid_pattern_returns_all(self):
        out = filter_models(MODELS, mode="pattern", pattern="[invalid")
        # fallback: 返回所有
        self.assertEqual(len(out), len(MODELS))


class TestFilterInclude(unittest.TestCase):
    def test_whitelist(self):
        out = filter_models(MODELS, mode="include", include=["gpt-4o", "llama-3-free"])
        ids = [m["id"] for m in out]
        self.assertEqual(set(ids), {"gpt-4o", "llama-3-free"})

    def test_empty_include_returns_all(self):
        out = filter_models(MODELS, mode="include", include=[])
        # 行为: 返回所有 (warning)
        self.assertEqual(len(out), len(MODELS))


class TestFilterExclude(unittest.TestCase):
    def test_exact_match(self):
        out = filter_models(MODELS, mode="exclude", exclude=["o1-preview", "o3-mini"])
        ids = [m["id"] for m in out]
        self.assertNotIn("o1-preview", ids)
        self.assertNotIn("o3-mini", ids)
        self.assertIn("gpt-4o", ids)

    def test_glob_wildcard(self):
        out = filter_models(
            MODELS, mode="exclude", exclude=["gpt-4*", "claude-3-*", "o*"]
        )
        ids = [m["id"] for m in out]
        self.assertNotIn("gpt-4o", ids)
        self.assertNotIn("gpt-4o-mini", ids)
        self.assertNotIn("claude-3-sonnet", ids)
        self.assertNotIn("claude-3-opus", ids)
        self.assertNotIn("o1-preview", ids)
        self.assertNotIn("o3-mini", ids)
        # llama-3-free / gemini-2.5-pro 应保留
        self.assertIn("llama-3-free", ids)
        self.assertIn("gemini-2.5-pro", ids)


class TestFilterModelIds(unittest.TestCase):
    def test_returns_id_list(self):
        ids = filter_model_ids(
            ["a-free", "a-paid", "b-free"],
            mode="pattern", pattern=".*free.*",
        )
        self.assertEqual(ids, ["a-free", "b-free"])


class TestPresetStrategies(unittest.TestCase):
    def test_strategies_present(self):
        self.assertIn("free_only", STRATEGIES)
        self.assertIn("no_premium", STRATEGIES)
        self.assertIn("all", STRATEGIES)

    def test_free_only_strategy_filters_free_models(self):
        strat = get_strategy("free_only")
        self.assertIsNotNone(strat)
        out = filter_models(
            MODELS,
            mode=strat["mode"],
            pattern=strat.get("pattern", ""),
        )
        ids = [m["id"] for m in out]
        for mid in ids:
            self.assertRegex(mid, r".*[\-_]free[\-_]?.*")

    def test_no_premium_strategy_excludes_paid(self):
        strat = get_strategy("no_premium")
        self.assertIsNotNone(strat)
        out = filter_models(
            MODELS,
            mode=strat["mode"],
            exclude=strat.get("exclude"),
        )
        ids = [m["id"] for m in out]
        # 不应包含 gpt-* claude-* o* gemini-*
        for mid in ids:
            self.assertFalse(mid.startswith("gpt"), mid)
            self.assertFalse(mid.startswith("claude"), mid)
            self.assertFalse(mid.startswith("o"), mid)
            self.assertFalse(mid.startswith("gemini"), mid)


class TestUnknownMode(unittest.TestCase):
    def test_unknown_mode_returns_all(self):
        out = filter_models(MODELS, mode="nonsense")
        self.assertEqual(len(out), len(MODELS))


if __name__ == "__main__":
    unittest.main()
