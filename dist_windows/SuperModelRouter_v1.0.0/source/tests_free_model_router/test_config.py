"""
free-model-router 单元测试 — config 模块
"""
import os
import sys
import tempfile
import unittest

# 让 import free_model_router 找到根目录的包
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from free_model_router.config import load_config, validate_config


class TestLoadConfig(unittest.TestCase):
    def test_default_when_missing(self):
        cfg = load_config("/nonexistent/path.yaml")
        self.assertIn("server", cfg)
        self.assertEqual(cfg["server"]["port"], 5678)
        self.assertEqual(cfg["routing"]["strategy"], "round-robin")

    def test_partial_override(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("server:\n  port: 9999\n  host: 0.0.0.0\n")
            path = f.name
        try:
            cfg = load_config(path)
            # 被覆盖
            self.assertEqual(cfg["server"]["port"], 9999)
            self.assertEqual(cfg["server"]["host"], "0.0.0.0")
            # 默认值保留
            self.assertEqual(cfg["server"]["cors_origins"], ["*"])
            self.assertIn("providers", cfg)
        finally:
            os.unlink(path)

    def test_providers_deep_merge(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("""
providers:
  openrouter:
    base_url: "https://x.test/v1"
    api_keys: ["sk-x"]
    model_rules:
      mode: "pattern"
      pattern: ".*free.*"
""")
            path = f.name
        try:
            cfg = load_config(path)
            p = cfg["providers"]["openrouter"]
            self.assertEqual(p["base_url"], "https://x.test/v1")
            self.assertEqual(p["model_rules"]["mode"], "pattern")
            # max_concurrent 应该有默认值或者 None? 取决于代码
        finally:
            os.unlink(path)


class TestValidateConfig(unittest.TestCase):
    def _base_cfg(self, **overrides):
        cfg = {
            "server": {"host": "127.0.0.1", "port": 5678},
            "providers": {
                "p1": {
                    "base_url": "https://x.test/v1",
                    "api_keys": ["sk-1"],
                    "model_rules": {"mode": "all"},
                    "enabled": True,
                }
            },
        }
        for k, v in overrides.items():
            if k == "providers":
                cfg["providers"].update(v)
            else:
                cfg[k] = v
        return cfg

    def test_ok(self):
        self.assertEqual(validate_config(self._base_cfg()), [])

    def test_no_providers(self):
        errs = validate_config({"providers": {}})
        self.assertEqual(len(errs), 1)
        self.assertIn("No providers", errs[0])

    def test_missing_base_url(self):
        cfg = self._base_cfg()
        del cfg["providers"]["p1"]["base_url"]
        errs = validate_config(cfg)
        self.assertTrue(any("base_url" in e for e in errs))

    def test_empty_api_keys(self):
        cfg = self._base_cfg()
        cfg["providers"]["p1"]["api_keys"] = []
        errs = validate_config(cfg)
        self.assertTrue(any("api_key" in e for e in errs))

    def test_invalid_mode(self):
        cfg = self._base_cfg()
        cfg["providers"]["p1"]["model_rules"]["mode"] = "wrong"
        errs = validate_config(cfg)
        self.assertTrue(any("invalid model_rules.mode" in e for e in errs))

    def test_pattern_requires_pattern_field(self):
        cfg = self._base_cfg()
        cfg["providers"]["p1"]["model_rules"] = {"mode": "pattern"}
        errs = validate_config(cfg)
        self.assertTrue(any("requires 'pattern'" in e for e in errs))

    def test_include_requires_non_empty_list(self):
        cfg = self._base_cfg()
        cfg["providers"]["p1"]["model_rules"] = {"mode": "include", "include": []}
        errs = validate_config(cfg)
        self.assertTrue(any("requires non-empty" in e for e in errs))

    def test_disabled_provider_skipped(self):
        cfg = self._base_cfg()
        cfg["providers"]["p1"]["enabled"] = False
        del cfg["providers"]["p1"]["base_url"]
        cfg["providers"]["p1"]["api_keys"] = []
        self.assertEqual(validate_config(cfg), [])


if __name__ == "__main__":
    unittest.main()
