"""
supermodel_router/ability_index.py — Ability 索引表 (v4.0.0)

O(1) 查询: model_id → 候选渠道链 (provider + api_key + base_url)
纯内存 dict, 定期从 registry 重建。不热更新时不做 I/O。

能力:
  - 精确匹配 model_id → [渠道链]
  - 模糊匹配 provider/model_id → [渠道链]
  - 标记已禁用/已降级模型
  - 定期扫描刷新 (防呆)

用法:
    index = AbilityIndex()
    index.build(registry)       # 从 ModelRegistry 重建
    chain = index.lookup("deepseek-v3")  # O(1) 返回链
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .models import ModelRegistry

LOG = logging.getLogger("ability_index")


@dataclass
class ChannelLink:
    """单条渠道链节点"""
    provider: str
    model_id: str
    base_url: str
    api_key: str
    key_index: int = 0
    capability_score: float = 50.0
    tier: str = "standard"
    is_free: bool = True
    context_window: int = 8192
    disabled: bool = False


@dataclass
class AbilityIndex:
    """
    O(1) 模型→渠道链索引

    结构:
        _model_index:   model_id → [ChannelLink, ...]
        _provider_index: provider/model_id → ChannelLink
        _last_build:    float 时间戳
        _rebuild_interval: int 秒 (默认 60)
    """
    _model_index: Dict[str, List[ChannelLink]] = field(default_factory=dict)
    _provider_index: Dict[str, ChannelLink] = field(default_factory=dict)
    _last_build: float = 0.0
    _rebuild_interval: int = 60

    def build(self, registry: Any) -> None:
        """
        从 ModelRegistry 重建索引。
        遍历所有 provider → model → api_key 组合。
        """
        from .models import ModelRegistry

        if not isinstance(registry, ModelRegistry):
            LOG.warning("ability_index build: invalid registry type %s", type(registry).__name__)
            return

        self._model_index.clear()
        self._provider_index.clear()
        now = time.time()

        providers = registry._providers if hasattr(registry, '_providers') else {}
        if not providers:
            LOG.warning("ability_index build: registry has no providers")
            self._last_build = now
            return

        count = 0
        for pname, ps in providers.items():
            if not hasattr(ps, 'models') or not ps.models:
                continue
            for m in ps.models:
                if not hasattr(m, 'id') or not m.id:
                    continue
                keys = ps.api_keys if hasattr(ps, 'api_keys') and ps.api_keys else ['_dummy_']
                base_url = ps.base_url if hasattr(ps, 'base_url') else ''
                for ki, key in enumerate(keys):
                    link = ChannelLink(
                        provider=pname,
                        model_id=m.id,
                        base_url=base_url,
                        api_key=key,
                        key_index=ki,
                        capability_score=getattr(m, 'capability_score', 50.0),
                        tier=self._infer_tier(getattr(m, 'capability_score', 50.0)),
                        is_free=getattr(m, 'is_free', True),
                        context_window=getattr(m, 'context_window', 8192),
                        disabled=False,
                    )
                    # model_index: model_id → [links]
                    if m.id not in self._model_index:
                        self._model_index[m.id] = []
                    self._model_index[m.id].append(link)
                    # provider_index: provider/model_id → link
                    full_path = f"{pname}/{m.id}/{ki}"
                    self._provider_index[full_path] = link
                    count += 1

        self._last_build = now
        LOG.info("ability_index: built %d channel links from %d providers", count, len(providers))

    def lookup(self, model_id: str) -> List[ChannelLink]:
        """O(1) 精确查找 model_id → 渠道链"""
        if self._stale():
            LOG.warning("ability_index: stale index, rebuild needed")
        return self._model_index.get(model_id, [])

    def lookup_provider(self, provider: str, model_id: str) -> Optional[ChannelLink]:
        """O(1) 精确查找 provider/model_id → 单条链接"""
        # 先找 model_index 再过滤 provider
        links = self._model_index.get(model_id, [])
        for link in links:
            if link.provider == provider:
                return link
        return None

    def mark_disabled(self, full_path: str) -> None:
        """标记某条渠道为禁用"""
        link = self._provider_index.get(full_path)
        if link:
            link.disabled = True
            LOG.info("ability_index: marked disabled %s", full_path)

    def mark_enabled(self, full_path: str) -> None:
        """恢复某条渠道"""
        link = self._provider_index.get(full_path)
        if link:
            link.disabled = False
            LOG.info("ability_index: marked enabled %s", full_path)

    def get_all_models(self) -> List[str]:
        """返回所有已索引的 model_id"""
        return list(self._model_index.keys())

    def get_all_paths(self) -> List[str]:
        """返回所有已索引的 full_path (provider/model_id/ki)"""
        return list(self._provider_index.keys())

    def stats(self) -> dict:
        """索引统计"""
        return {
            "models": len(self._model_index),
            "channels": len(self._provider_index),
            "last_build": self._last_build,
            "age_seconds": time.time() - self._last_build if self._last_build > 0 else -1,
        }

    def _infer_tier(self, capability_score: float) -> str:
        if capability_score >= 80:
            return "premium"
        if capability_score >= 60:
            return "standard"
        return "budget"

    def _stale(self) -> bool:
        return self._last_build > 0 and (time.time() - self._last_build > self._rebuild_interval)