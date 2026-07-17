"""
supermodel_router/pricing.py — Token 成本加载器 (v1.0.0)

从 pricing.json 加载模型价格, 支持:
- 精确匹配: provider/model_id
- 模糊匹配: 正则 pattern (e.g. ":free")
- Provider 默认价格
- 所有匹配规则返回 (input_cost, output_cost, is_free)
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

LOG = logging.getLogger("pricing")

PRICING_FILE = "pricing.json"

# ── 数据结构 ─────────────────────────────────────────────────

class PricingDB:
    """模型价格数据库 (单例)"""

    def __init__(self, pricing_path: Optional[Path] = None):
        self._path = pricing_path
        self._data: dict = {}
        self._models: Dict[str, dict] = {}
        self._providers: Dict[str, dict] = {}
        self._patterns: List[dict] = []
        self._loaded = False
        self._default_free = True

    def load(self, pricing_path: Optional[Path] = None) -> bool:
        """加载 pricing.json"""
        path = pricing_path or self._path
        if path is None:
            return False
        try:
            data = json.loads(Path(path).read_text())
            self._data = data
            self._models = data.get("models", {})
            self._providers = data.get("providers", {})
            self._patterns = data.get("patterns", [])
            self._default_free = data.get("_meta", {}).get("default_free", True)
            self._loaded = True
            LOG.info("PricingDB: loaded %d models, %d providers, %d patterns",
                     len(self._models), len(self._providers), len(self._patterns))
            return True
        except Exception as e:
            LOG.warning("PricingDB: load failed: %s", e)
            return False

    def lookup(self, model_id: str, provider: str = "") -> Tuple[float, float, bool]:
        """
        查询模型价格 → (input_cost_per_1m, output_cost_per_1m, is_free)

        优先级:
          1. 精确匹配 models 表
          2. Patterns 正则匹配
          3. Provider 默认价格
          4. 全局默认 (free=True)
        """
        # 1) 精确匹配
        full_key = f"{provider}/{model_id}" if provider else model_id
        if full_key in self._models:
            m = self._models[full_key]
            in_cost = float(m.get("input", 0))
            out_cost = float(m.get("output", 0))
            is_free = (in_cost + out_cost) == 0
            return in_cost, out_cost, is_free

        # 也试 model_id only
        if model_id in self._models:
            m = self._models[model_id]
            in_cost = float(m.get("input", 0))
            out_cost = float(m.get("output", 0))
            is_free = (in_cost + out_cost) == 0
            return in_cost, out_cost, is_free

        # 2) 正则匹配
        for pat in self._patterns:
            match_str = pat.get("match", "")
            if re.search(match_str, full_key):
                in_cost = float(pat.get("input", 0))
                out_cost = float(pat.get("output", 0))
                is_free = (in_cost + out_cost) == 0
                return in_cost, out_cost, is_free

        # 3) Provider 默认
        if provider and provider in self._providers:
            p = self._providers[provider]
            in_cost = float(p.get("default_input", 0))
            out_cost = float(p.get("default_output", 0))
            is_free = (in_cost + out_cost) == 0
            return in_cost, out_cost, is_free

        # 4) 全局默认
        if self._default_free:
            return 0.0, 0.0, True
        return 0.0, 0.0, self._default_free

    def calculate_cost(self, model_id: str, provider: str,
                       input_tokens: int = 0, output_tokens: int = 0) -> float:
        """计算单次调用的成本 (USD)"""
        in_cost, out_cost, _ = self.lookup(model_id, provider)
        input_usd = (input_tokens / 1_000_000) * in_cost
        output_usd = (output_tokens / 1_000_000) * out_cost
        return input_usd + output_usd

    def is_free(self, model_id: str, provider: str = "") -> bool:
        """是否为免费模型"""
        _, _, is_free = self.lookup(model_id, provider)
        return is_free


# ── 全局单例 ─────────────────────────────────────────────────

_pricing_db: Optional[PricingDB] = None


def get_pricing(pricing_path: Optional[Path] = None) -> PricingDB:
    """获取全局 PricingDB 实例"""
    global _pricing_db
    if _pricing_db is None:
        _pricing_db = PricingDB(pricing_path)
        if pricing_path:
            _pricing_db.load(pricing_path)
    return _pricing_db


def init_pricing(pricing_path: Path) -> PricingDB:
    """初始化并加载"""
    global _pricing_db
    _pricing_db = PricingDB(pricing_path)
    _pricing_db.load(pricing_path)
    return _pricing_db
