"""
free-model-router config loader

支持的 provider 配置格式 (YAML):
```yaml
providers:
  openrouter:
    base_url: "https://openrouter.ai/api/v1"
    api_keys:
      - "sk-or-v1-xxx"
      - "sk-or-v1-yyy"   # 多个 key 自动轮询
    model_rules:
      mode: "all"          # all | pattern | include | exclude
      pattern: ".*free.*"  # 仅 mode=pattern 时用
      include: []          # 仅 mode=include 时用
      exclude:             # 仅 mode=exclude 时用
        - "gpt-4"
        - "claude-*"
    max_concurrent: 3
    enabled: true
```

4 种 model_rules.mode:
- all: 接受 provider 返回的所有模型 (默认)
- pattern: 正则匹配模型 ID, 仅保留匹配的 (如 ".*free.*" 或 ".*-turbo")
- include: 白名单, 仅保留列出的模型 ID
- exclude: 黑名单, 排除列出的模型 ID (支持 glob 通配符)
"""

import logging
import yaml
from pathlib import Path
from typing import Any

LOG = logging.getLogger("fmr.config")


def _default_config() -> dict[str, Any]:
    """返回默认配置模板"""
    return {
        "server": {
            "host": "127.0.0.1",
            "port": 5678,
            "api_key": "",
            "cors_origins": ["*"],
        },
        "routing": {
            "strategy": "round-robin",
            "failover_threshold": 3,
            "recovery_interval": 300,
            "max_retry": 2,
            "first_token_timeout": 10000,
            "retry_backoff_ms": [0, 500],
        },
        "sync": {
            "interval": 3600,
            "auto_discover": True,
        },
        "providers": {},
    }


def load_config(config_path: str) -> dict[str, Any]:
    """加载 YAML 配置, 合并默认值"""
    cfg = _default_config()
    path = Path(config_path)
    if not path.exists():
        LOG.warning("Config not found: %s, using defaults", config_path)
        return cfg
    try:
        with open(path, "r", encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f)
        if user_cfg and isinstance(user_cfg, dict):
            _deep_merge(cfg, user_cfg)
        LOG.info("Loaded config from %s", config_path)
    except Exception:
        LOG.exception("Failed to load config, using defaults")
    return cfg


def _deep_merge(base: dict, override: dict) -> None:
    """递归合并字典"""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def validate_config(cfg: dict[str, Any]) -> list[str]:
    """验证配置, 返回错误列表 (空表示 OK)"""
    errors = []
    providers = cfg.get("providers", {})
    if not providers:
        errors.append("No providers configured")
        return errors

    valid_modes = {"all", "pattern", "include", "exclude"}
    for name, pcfg in providers.items():
        if not pcfg.get("enabled", True):
            continue
        if not pcfg.get("base_url"):
            errors.append(f"Provider '{name}': base_url is required")
        keys = pcfg.get("api_keys", [])
        if not keys or not any(keys):
            errors.append(f"Provider '{name}': at least one non-empty api_key required")
        rules = pcfg.get("model_rules", {})
        mode = rules.get("mode", "all")
        if mode not in valid_modes:
            errors.append(
                f"Provider '{name}': invalid model_rules.mode '{mode}'"
            )
        if mode == "pattern" and not rules.get("pattern"):
            errors.append(f"Provider '{name}': mode='pattern' requires 'pattern'")
        if mode == "include":
            inc = rules.get("include", [])
            if not inc:
                errors.append(f"Provider '{name}': mode='include' requires non-empty list")
    return errors
