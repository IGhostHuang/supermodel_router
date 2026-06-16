"""
free-model-router 单元测试 — provider 模块 (多 key 轮询 + 健康跟踪)
"""
import asyncio
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from free_model_router.provider import Provider, ProviderManager


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestProviderKeyRotation(unittest.TestCase):
    def _make(self, n_keys=3):
        cfg = {
            "base_url": "https://x.test/v1",
            "api_keys": [f"sk-{i}" for i in range(n_keys)],
            "model_rules": {"mode": "all"},
            "max_concurrent": 2,
        }
        return Provider("p1", cfg)

    def test_keys_loaded(self):
        p = self._make(3)
        self.assertEqual(len(p.api_keys), 3)
        self.assertEqual(p.key_count, 3)
        self.assertTrue(p.has_keys)

    def test_round_robin_picks_next(self):
        p = self._make(3)
        seen = [_run(p.pick_key()) for _ in range(6)]
        # 轮询 3 个 key 6 次 — 每个出现 2 次
        self.assertEqual(seen.count("sk-0"), 2)
        self.assertEqual(seen.count("sk-1"), 2)
        self.assertEqual(seen.count("sk-2"), 2)

    def test_failed_key_skipped(self):
        p = self._make(3)
        p.record_key_failure("sk-1", http_code=401)  # 5min cooldown
        # 接下来 5 次: 应该跳过 sk-1
        seen = [_run(p.pick_key()) for _ in range(5)]
        self.assertNotIn("sk-1", seen)
        self.assertIn("sk-0", seen)
        self.assertIn("sk-2", seen)


class TestProviderFreeModels(unittest.TestCase):
    def _make(self, primary=None, fallbacks=None):
        cfg = {
            "base_url": "https://x.test/v1",
            "api_keys": ["sk-1"],
            "model_rules": {"mode": "all"},
            "max_concurrent": 2,
        }
        p = Provider("p1", cfg)
        if primary:
            p.set_free_models([primary] + (fallbacks or []))
        return p

    def test_set_free_models_keeps_primary(self):
        p = self._make()
        p.primary_model = "alpha"
        p.set_free_models(["alpha", "beta", "gamma"])
        self.assertEqual(p.primary_model, "alpha")  # primary 仍在 free_models 中, 不重选

    def test_set_free_models_replaces_primary_if_missing(self):
        p = self._make()
        p.primary_model = "stale"
        p.set_free_models(["fresh-1", "fresh-2"])
        self.assertEqual(p.primary_model, "fresh-1")  # 旧的被踢, 选新列表第一个

    def test_empty_free_models(self):
        p = self._make()
        p.primary_model = "old"
        p.set_free_models([])
        self.assertEqual(p.primary_model, "old")  # 没有可选项, 不动

    def test_select_model_prefers_primary(self):
        p = self._make()
        p.set_free_models(["a", "b", "c"])
        p.primary_model = "b"
        self.assertEqual(p.select_model(), "b")

    def test_select_model_skips_disabled(self):
        p = self._make()
        p.set_free_models(["a", "b", "c"])
        p.primary_model = "a"
        p.disable_model("a", reason="401")
        # primary 被禁用, 应回退到下一个
        m = p.select_model()
        self.assertNotEqual(m, "a")


class TestProviderSlot(unittest.TestCase):
    def _make(self):
        return Provider("p1", {
            "base_url": "https://x.test/v1",
            "api_keys": ["sk-1"],
            "model_rules": {"mode": "all"},
            "max_concurrent": 2,
        })

    def test_acquire_release(self):
        p = self._make()
        self.assertTrue(_run(p.acquire_slot()))
        self.assertTrue(_run(p.acquire_slot()))
        self.assertEqual(p.slot_used, 2)
        # 槽位满
        self.assertFalse(_run(p.acquire_slot()))
        _run(p.release_slot())
        self.assertEqual(p.slot_used, 1)
        self.assertTrue(_run(p.acquire_slot()))


class TestProviderHealth(unittest.TestCase):
    def _make(self):
        return Provider("p1", {
            "base_url": "https://x.test/v1",
            "api_keys": ["sk-1"],
            "model_rules": {"mode": "all"},
            "max_concurrent": 2,
        })

    def test_failures_lead_to_degraded(self):
        p = self._make()
        p.fallback_models = ["fb"]
        for _ in range(3):
            p.report_failure()
        self.assertEqual(p.status, "degraded")

    def test_more_failures_lead_to_unavailable(self):
        p = self._make()
        for _ in range(6):
            p.report_failure()
        self.assertEqual(p.status, "unavailable")

    def test_success_recovers(self):
        p = self._make()
        for _ in range(3):
            p.report_failure()
        p.report_success()
        p.report_success()
        p.report_success()
        self.assertEqual(p.status, "healthy")


class TestProviderManager(unittest.TestCase):
    def test_init_and_get(self):
        cfg = {
            "p1": {"base_url": "https://a.test/v1", "api_keys": ["sk-1"],
                   "model_rules": {"mode": "all"}, "enabled": True},
            "p2": {"base_url": "https://b.test/v1", "api_keys": ["sk-2"],
                   "model_rules": {"mode": "all"}, "enabled": True},
            "p3": {"base_url": "https://c.test/v1", "api_keys": ["sk-3"],
                   "model_rules": {"mode": "all"}, "enabled": False},
        }
        mgr = ProviderManager(cfg)
        # 3 个都被创建 (包括 disabled, Provider 自己管 enabled)
        self.assertIsNotNone(mgr.get("p1"))
        self.assertIsNotNone(mgr.get("p2"))
        self.assertIsNotNone(mgr.get("p3"))
        # 但 active_providers 只算 enabled
        active = mgr.active_providers()
        active_ids = [p.id for p in active]
        self.assertIn("p1", active_ids)
        self.assertIn("p2", active_ids)
        self.assertNotIn("p3", active_ids)


if __name__ == "__main__":
    unittest.main()
