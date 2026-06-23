"""
supermodel_router/model_groups.py — 模型分组管理 (v3.9.0)

老大 2026-06-18 16:55 拍:
- 白名单支持 @provider (整 provider 允许) + group:<name> (分组允许)
- 轮询规则按分组 (group_rr / group_failover / group_weighted)
- 模型分组管理: 可以针对各个 provider 按正则拉取模型分组

设计:
- ModelGroup 数据类: name / patterns(List[regex str]) / description / enabled
- ModelGroupManager: CRUD + resolve_group 跨 provider 拉取
- 持久化: state/model_groups_state.json (debounce 5s)
- 线程安全: RLock
- 错误兜底: 正则编译失败 → 拒绝创建 (R39)
"""
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

LOG = logging.getLogger("model_groups")


@dataclass
class ModelGroup:
    """模型分组定义

    patterns: 正则列表 (匹配 model_id 或 provider/model_id)
    e.g. patterns=["claude-3-5.*", "claude-3-haiku.*"] 匹配所有 claude-3-5 + haiku 系列
    """
    name: str
    patterns: List[str] = field(default_factory=list)
    description: str = ""
    enabled: bool = True
    created_at: float = 0.0
    updated_at: float = 0.0
    model_count: int = 0  # 解析出的 model 数 (cache, resolve_group 时更新)

    def to_dict(self) -> dict:
        return asdict(self)


class ModelGroupManager:
    """模型分组管理 (CRUD + 跨 provider 解析)"""

    DEBOUNCE_S = 5.0  # 写盘 debounce

    def __init__(self, state_dir: str = "."):
        self._lock = threading.RLock()
        self._state_dir = Path(state_dir)
        self._state_file = self._state_dir / "model_groups_state.json"
        self._groups: Dict[str, ModelGroup] = {}
        self._last_save: float = 0.0
        self._dirty: bool = False
        self._load()
        # 缓存: 已知 models 列表 (从外部注入, 跟 registry 同步)
        self._known_models_provider: Dict[str, Set[str]] = {}  # provider -> {model_id}

    # ===== 持久化 =====
    def _load(self):
        if not self._state_file.exists():
            LOG.info("ModelGroupManager: no state file, starting empty")
            return
        try:
            data = json.loads(self._state_file.read_text())
            for name, gd in data.get("groups", {}).items():
                self._groups[name] = ModelGroup(
                    name=gd["name"],
                    patterns=gd.get("patterns", []),
                    description=gd.get("description", ""),
                    enabled=gd.get("enabled", True),
                    created_at=gd.get("created_at", 0.0),
                    updated_at=gd.get("updated_at", 0.0),
                    model_count=gd.get("model_count", 0),
                )
            LOG.info("ModelGroupManager: loaded %d groups", len(self._groups))
        except Exception as e:
            LOG.warning("ModelGroupManager: load failed (%s), starting empty", e)

    def _save_async(self):
        self._dirty = True
        now = time.time()
        if now - self._last_save < self.DEBOUNCE_S:
            return
        self._flush()

    def _flush(self):
        with self._lock:
            if not self._dirty:
                return
            self._state_dir.mkdir(parents=True, exist_ok=True)
            data = {
                "groups": {n: g.to_dict() for n, g in self._groups.items()},
                "updated_at": time.time(),
            }
            tmp = self._state_file.with_suffix(".json.tmp")
            try:
                tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
                os.replace(tmp, self._state_file)
                self._dirty = False
                self._last_save = time.time()
            except Exception as e:
                LOG.warning("ModelGroupManager: save failed: %s", e)

    # ===== CRUD =====
    def create_group(self, name: str, patterns: List[str],
                     description: str = "", enabled: bool = True) -> ModelGroup:
        """创建分组 (R39: 正则编译失败 → 拒绝)"""
        if not name or not name.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"group name '{name}' must be alphanumeric/dash/underscore")
        # 验证所有正则可编译
        compiled = []
        for p in patterns:
            try:
                compiled.append(re.compile(p, re.IGNORECASE))
            except re.error as e:
                raise ValueError(f"pattern '{p}' invalid regex: {e}")
        with self._lock:
            if name in self._groups:
                raise ValueError(f"group '{name}' already exists")
            now = time.time()
            group = ModelGroup(
                name=name,
                patterns=list(patterns),
                description=description,
                enabled=enabled,
                created_at=now,
                updated_at=now,
                model_count=len(self._resolve_group_unlocked(name, list(patterns))),
            )
            self._groups[name] = group
            self._save_async()
            self._flush()
            LOG.info("ModelGroup created: %s (patterns=%d, resolved=%d models)",
                     name, len(patterns), group.model_count)
            return group

    def list_groups(self) -> List[dict]:
        """列出所有 group (按 name 排序, 含实时解析计数与样本).

        UI 依赖这里的 model_count/resolved_sample 展示每个分组自己的真实匹配数。
        不能返回全局总数, 也不能返回 stale cache。
        """
        with self._lock:
            items = []
            for g in sorted(self._groups.values(), key=lambda x: x.name):
                resolved = self._resolve_group_unlocked(g.name, g.patterns) if g.enabled else []
                g.model_count = len(resolved)
                d = g.to_dict()
                d["resolved_sample"] = resolved[:5]
                items.append(d)
            return items

    def get_group(self, name: str) -> Optional[dict]:
        with self._lock:
            g = self._groups.get(name)
            return g.to_dict() if g else None

    def update_group(self, name: str, **fields) -> dict:
        """更新 group (不允许改 name)"""
        with self._lock:
            if name not in self._groups:
                raise KeyError(f"group '{name}' not found")
            allowed = {"patterns", "description", "enabled"}
            for k in fields:
                if k not in allowed:
                    raise ValueError(f"field '{k}' not editable")
            g = self._groups[name]
            if "patterns" in fields:
                # 验证新正则
                for p in fields["patterns"]:
                    try:
                        re.compile(p, re.IGNORECASE)
                    except re.error as e:
                        raise ValueError(f"pattern '{p}' invalid regex: {e}")
                g.patterns = list(fields["patterns"])
            if "description" in fields:
                g.description = fields["description"]
            if "enabled" in fields:
                g.enabled = fields["enabled"]
            g.updated_at = time.time()
            # 重新解析 model_count
            g.model_count = len(self._resolve_group_unlocked(name, g.patterns))
            self._save_async()
            self._flush()
            return g.to_dict()

    def delete_group(self, name: str) -> bool:
        with self._lock:
            if name not in self._groups:
                return False
            del self._groups[name]
            self._save_async()
            self._flush()
            LOG.info("ModelGroup deleted: %s", name)
            return True

    # ===== 解析 =====
    def set_known_models(self, provider_models: Dict[str, List[str]]):
        """注入已知 model 列表 (从 registry.refresh_all() 同步)

        provider_models: {provider_name: [model_id, ...]}
        """
        with self._lock:
            self._known_models_provider = {
                p: set(ms) for p, ms in provider_models.items()
            }
            # 重新解析所有 group 的 model_count。注意这里必须是 len(list),
            # 重新解析所有 group 的 model_count；每个 group 独立 resolve，缓存 int 数量。
            for g in self._groups.values():
                g.model_count = len(self._resolve_group_unlocked(g.name, g.patterns))

    def resolve_group(self, name: str) -> List[str]:
        """解析 group → 实际 model 列表 (provider/model_id 格式)

        返回: ['openrouter/anthropic/claude-3-5-sonnet-20241022', 'aihubmix/claude-3-5-sonnet', ...]
        """
        with self._lock:
            g = self._groups.get(name)
            if not g or not g.enabled:
                return []
            return self._resolve_group_unlocked(name, g.patterns)

    def _resolve_group_unlocked(self, name: str, patterns: List[str]) -> List[str]:
        """不加锁内部用"""
        if not patterns or not self._known_models_provider:
            return []
        compiled = []
        for p in patterns:
            try:
                compiled.append(re.compile(p, re.IGNORECASE))
            except re.error:
                continue
        if not compiled:
            return []
        result: List[str] = []
        for provider, mids in self._known_models_provider.items():
            for mid in mids:
                full = f"{provider}/{mid}"
                for rgx in compiled:
                    if rgx.search(mid) or rgx.search(full):
                        result.append(full)
                        break
        return result

    def get_models_in_groups(self, group_names: List[str]) -> Set[str]:
        """批量解析多个 group, 返回 model 集合 (去重)"""
        result: Set[str] = set()
        for n in group_names:
            result.update(self.resolve_group(n))
        return result

    def get_group_for_model(self, model_path: str) -> Optional[str]:
        """反向查找: 给定 provider/model, 属于哪个 group (第一个匹配的, 按 name 排序)

        model_path: 'provider/model_id' 或纯 'model_id'
        返回: group_name 或 None
        """
        with self._lock:
            for g in sorted(self._groups.values(), key=lambda x: x.name):
                if not g.enabled or not g.patterns:
                    continue
                compiled = []
                for p in g.patterns:
                    try:
                        compiled.append(re.compile(p, re.IGNORECASE))
                    except re.error:
                        continue
                for rgx in compiled:
                    if rgx.search(model_path):
                        return g.name
        return None

    def get_path_to_group_mapping(self) -> Dict[str, str]:
        """v3.9.0 (Phase H): 算 path → group_name 映射 (供 engine.pick_chain 用)

        遍历所有 enabled group, 跨 provider 解析出 model → group 的映射。
        pick_chain 在 chain 排序时用 Dict[path, group_name] 桶化 candidates。

        性能: registry.refresh 后缓存, hot path 调用 O(1) dict 查找
        """
        with self._lock:
            mapping: Dict[str, str] = {}
            # 按 group name 排序 (确定性, 调试友好)
            for g in sorted(self._groups.values(), key=lambda x: x.name):
                if not g.enabled:
                    continue
                # 编译 patterns 一次
                compiled = []
                for p in g.patterns:
                    try:
                        compiled.append(re.compile(p, re.IGNORECASE))
                    except re.error:
                        continue
                if not compiled:
                    continue
                # 遍历所有 provider 的 known models
                for provider, models in self._known_models_provider.items():
                    for model_id in models:
                        path = f"{provider}/{model_id}"
                        # 已分配过的 path 不覆盖 (后定义 group 优先级低)
                        if path in mapping:
                            continue
                        for rgx in compiled:
                            if rgx.search(path):
                                mapping[path] = g.name
                                break
            return mapping

    def get_stats(self) -> dict:
        """分组统计: 各 group 跨 provider 分布 + 总览"""
        with self._lock:
            group_stats = []
            for g in sorted(self._groups.values(), key=lambda x: x.name):
                models = self._resolve_group_unlocked(g.name, g.patterns)
                provider_dist: Dict[str, int] = {}
                for m in models:
                    p = m.split("/", 1)[0] if "/" in m else "?"
                    provider_dist[p] = provider_dist.get(p, 0) + 1
                group_stats.append({
                    "name": g.name,
                    "patterns": g.patterns,
                    "description": g.description,
                    "enabled": g.enabled,
                    "model_count": len(models),
                    "provider_dist": provider_dist,
                    "updated_at": g.updated_at,
                })
            return {
                "total_groups": len(self._groups),
                "enabled_groups": sum(1 for g in self._groups.values() if g.enabled),
                "total_providers_known": len(self._known_models_provider),
                "total_models_known": sum(len(s) for s in self._known_models_provider.values()),
                "groups": group_stats,
            }


# ===== 全局单例 (跟 public_key_manager 平行) =====
_model_group_manager: Optional[ModelGroupManager] = None


def init_model_group_manager(state_dir: str = ".") -> ModelGroupManager:
    global _model_group_manager
    _model_group_manager = ModelGroupManager(state_dir=state_dir)
    return _model_group_manager


def get_model_group_manager() -> Optional[ModelGroupManager]:
    return _model_group_manager
