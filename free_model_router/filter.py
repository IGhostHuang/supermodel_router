"""
free-model-router model filter — 4 种策略过滤免费模型

策略说明:
  all:       不过滤, 所有模型都可用
  pattern:   正则匹配模型 ID, 仅保留匹配的
  include:   白名单, 仅保留指定 ID
  exclude:   黑名单, 排除指定 ID (支持 glob/wildcard)
"""

import logging
import fnmatch
from typing import Iterable

LOG = logging.getLogger("smr.filter")


def filter_models(
    models: list[dict],
    mode: str = "all",
    pattern: str = "",
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> list[dict]:
    """
    根据规则过滤模型列表.

    Args:
        models: 从 /v1/models 获取的原始模型列表, 每项含 "id" 字段
        mode: 过滤模式 (all/pattern/include/exclude)
        pattern: 正则表达式 (仅 mode=pattern 时)
        include: 白名单 ID 列表
        exclude: 黑名单 ID 列表 (支持 glob: "*" "?" "[...]")

    Returns:
        过滤后的模型列表
    """
    if mode == "all":
        return list(models)

    if mode == "pattern" and pattern:
        import re
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            LOG.error("Invalid pattern '%s': %s", pattern, e)
            return list(models)
        return [m for m in models if regex.search(m["id"])]

    if mode == "include":
        include_set = set(include) if include else set()
        if not include_set:
            LOG.warning("include mode with empty list — returning all models")
            return list(models)
        return [m for m in models if m["id"] in include_set]

    if mode == "exclude":
        exclude_set = set(exclude) if exclude else set()
        if not exclude_set:
            return list(models)
        result = []
        for m in models:
            mid = m["id"]
            # 精确匹配 + glob 通配符匹配
            if mid in exclude_set:
                continue
            if any(fnmatch.fnmatch(mid, pat) for pat in exclude_set):
                continue
            result.append(m)
        return result

    LOG.warning("Unknown filter mode '%s', returning all models", mode)
    return list(models)


def filter_model_ids(
    model_ids: list[str],
    mode: str = "all",
    pattern: str = "",
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> list[str]:
    """过滤模型 ID 字符串列表 (便捷函数)"""
    models = [{"id": mid} for mid in model_ids]
    filtered = filter_models(models, mode, pattern, include, exclude)
    return [m["id"] for m in filtered]


# ── 预置策略 ──

STRATEGIES = {
    "free_only": {
        "mode": "pattern",
        "pattern": ".*[\\-_]free[\\-_]?.*",
    },
    "no_premium": {
        "mode": "exclude",
        "exclude": [
            "gpt-4*", "gpt-3.5*", "claude-*", "gemini-*",
            "claude-3.*", "gpt-4o", "o1*", "o3*",
        ],
    },
    "all": {
        "mode": "all",
    },
}


def get_strategy(name: str) -> dict | None:
    """按名称获取预置策略"""
    return STRATEGIES.get(name)
