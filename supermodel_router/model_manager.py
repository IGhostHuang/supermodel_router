"""
supermodel_router/model_manager.py — 模型管理模块 v3.3

职责:
  Discovery: 每次 refresh_all() 对比新旧快照, 输出 added/removed/unchanged
  Notifier:  新模型/移除模型时触发通知 (webhook + log)
  ListMgr:   黑白名单管理 (精确 + 正则 + per-provider 覆盖)
  AutoRules: 自动加黑/加白决策引擎 (基于 penalty + pattern 规则)

由 app.py lifespan 注入 config + registry, 挂载到 admin_api 路由。
通过 registry.register_refresh_callback() 自动触发, 不需要手动调用。
"""
import json
import time
import logging
import re
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import Config
from .models import ModelRegistry

LOG = logging.getLogger("model_manager")


# ── 数据结构 ──────────────────────────────────────────────

@dataclass
class ModelSnapshot:
    """某次 refresh 的模型快照"""
    ts: float
    models: dict[str, dict]  # model_id → {provider, modality, capability_score, ...}

    def model_ids(self) -> set[str]:
        return set(self.models.keys())

    @classmethod
    def from_registry(cls, registry: ModelRegistry) -> "ModelSnapshot":
        """从当前 registry 状态构建快照"""
        models = {}
        for ps in registry._providers.values():
            for m in ps.models:
                models[f"{ps.name}/{m.id}"] = {
                    "provider": ps.name,
                    "modality": m.modality,
                    "capability_score": m.capability_score,
                }
        return cls(ts=time.time(), models=models)


@dataclass
class DiffResult:
    """两次快照的差异"""
    ts: float
    added: list[dict]
    removed: list[dict]
    unchanged_count: int

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed)

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.ts)),
            "added": self.added,
            "removed": self.removed,
            "unchanged_count": self.unchanged_count,
            "has_changes": self.has_changes,
        }


# ── Discovery Engine ──────────────────────────────────────

class DiscoveryEngine:
    """模型发现引擎: 快照对比 + 差异检测"""

    def __init__(self, registry: ModelRegistry, snapshot_dir: Path | str | None = None):
        self.registry = registry
        self._snapshot_dir = Path(snapshot_dir) if snapshot_dir else None
        self._last_snapshot: ModelSnapshot | None = None
        self._last_diff: DiffResult | None = None
        self._diff_history: list[DiffResult] = []
        self._lock = threading.Lock()

    @property
    def last_diff(self) -> DiffResult | None:
        return self._last_diff

    def take_snapshot(self) -> ModelSnapshot:
        """从当前 registry 拍快照"""
        return ModelSnapshot.from_registry(self.registry)

    def diff(self, old: ModelSnapshot, new: ModelSnapshot) -> DiffResult:
        """对比两个快照, 返回差异"""
        old_ids = old.model_ids()
        new_ids = new.model_ids()
        added_ids = new_ids - old_ids
        removed_ids = old_ids - new_ids

        added = [{"model_id": mid, **new.models[mid]} for mid in sorted(added_ids)]
        removed = [{"model_id": mid, **old.models[mid]} for mid in sorted(removed_ids)]

        result = DiffResult(
            ts=time.time(),
            added=added,
            removed=removed,
            unchanged_count=len(old_ids & new_ids),
        )

        with self._lock:
            self._last_diff = result
            self._diff_history.append(result)
            # 保留最近 100 条
            if len(self._diff_history) > 100:
                self._diff_history = self._diff_history[-100:]

        return result

    def refresh_and_diff(self) -> DiffResult:
        """拍新快照并与上次对比"""
        new_snap = self.take_snapshot()
        if self._last_snapshot is None:
            # 首次: 无差异, 全部视为 unchanged
            result = DiffResult(
                ts=time.time(),
                added=[],
                removed=[],
                unchanged_count=len(new_snap.models),
            )
        else:
            result = self.diff(self._last_snapshot, new_snap)
        self._last_snapshot = new_snap
        return result

    def save_snapshot(self):
        """持久化最新快照到磁盘"""
        if not self._snapshot_dir or not self._last_snapshot:
            return
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)
        path = self._snapshot_dir / "latest.json"
        try:
            data = {
                "ts": self._last_snapshot.ts,
                "models": self._last_snapshot.models,
            }
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception:
            LOG.warning("save_snapshot failed")

    def load_snapshot(self):
        """从磁盘加载上次快照"""
        if not self._snapshot_dir:
            return
        path = self._snapshot_dir / "latest.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            self._last_snapshot = ModelSnapshot(
                ts=data["ts"],
                models=data.get("models", {}),
            )
        except Exception:
            LOG.warning("load_snapshot failed")


# ── Notifier ──────────────────────────────────────────────

class ModelNotifier:
    """模型变更通知器: webhook + log"""

    def __init__(self, cfg: Config):
        self._webhook_url = cfg.data.get("model_management", {}).get("notify", {}).get("webhook_url")
        self._notify_log = cfg.data.get("model_management", {}).get("notify", {}).get("log", True)

    def notify(self, diff: DiffResult):
        """有新模型或移除模型时触发通知"""
        if not diff.has_changes:
            return

        if self._notify_log:
            if diff.added:
                models_str = ", ".join(a["model_id"] for a in diff.added)
                LOG.info("[Discovery] 🆕 new models: %s", models_str)
            if diff.removed:
                models_str = ", ".join(r["model_id"] for r in diff.removed)
                LOG.info("[Discovery] ❌ removed models: %s", models_str)

        if self._webhook_url:
            self._send_webhook(diff)

    def _send_webhook(self, diff: DiffResult):
        """发送 webhook 通知"""
        try:
            payload = diff.to_dict()
            resp = httpx.post(self._webhook_url, json=payload, timeout=5)
            if resp.status_code >= 400:
                LOG.warning("webhook notify failed: %d", resp.status_code)
        except Exception as e:
            LOG.warning("webhook notify error: %s", e)


# ── List Manager ──────────────────────────────────────────

class ListMgr:
    """黑白名单管理: 精确匹配 + 正则匹配 + per-provider 覆盖"""

    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._global_whitelist: list[str] = []
        self._global_blacklist: list[str] = []
        self._provider_whitelist: dict[str, list[str]] = {}
        self._provider_blacklist: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        self.reload_from_config()

    def reload_from_config(self):
        """从 config 重新加载黑白名单"""
        with self._lock:
            mm = self._cfg.data.get("model_management", {})
            lists = mm.get("lists", {})
            self._global_whitelist = lists.get("whitelist_patterns", [])
            self._global_blacklist = lists.get("blacklist_patterns", [])

    def check(self, model_id: str, provider: str = "") -> tuple[bool | None, str]:
        """检查模型是否允许
        返回: (True=白名单, False=黑名单, None=未匹配)
        """
        with self._lock:
            # 1. per-provider 精确匹配
            if provider:
                wl = self._provider_whitelist.get(provider, [])
                if model_id in wl:
                    return True, f"provider {provider} whitelist"
                bl = self._provider_blacklist.get(provider, [])
                if model_id in bl:
                    return False, f"provider {provider} blacklist"

            # 2. global regex patterns
            for pat in self._global_whitelist:
                if re.search(pat, model_id, re.IGNORECASE):
                    return True, f"global whitelist: {pat}"
            for pat in self._global_blacklist:
                if re.search(pat, model_id, re.IGNORECASE):
                    return False, f"global blacklist: {pat}"

            return None, "no match"

    def add_to_whitelist(self, model_id: str, provider: str = "", persist: bool = True):
        with self._lock:
            if provider:
                self._provider_whitelist.setdefault(provider, []).append(model_id)
            else:
                self._global_whitelist.append(model_id)

    def add_to_blacklist(self, model_id: str, provider: str = "", persist: bool = True):
        with self._lock:
            if provider:
                self._provider_blacklist.setdefault(provider, []).append(model_id)
            else:
                self._global_blacklist.append(model_id)

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "global": {
                    "whitelist_patterns": list(self._global_whitelist),
                    "blacklist_patterns": list(self._global_blacklist),
                },
                "per_provider": {
                    "whitelist": {k: list(v) for k, v in self._provider_whitelist.items()},
                    "blacklist": {k: list(v) for k, v in self._provider_blacklist.items()},
                },
            }


# ── Auto Rules ────────────────────────────────────────────

class AutoRules:
    """自动加黑/加白决策引擎
    基于: penalty 阈值 + pattern 规则 + 自动黑名单恢复 (cooldown)
    """

    def __init__(self, cfg: Config, list_mgr: ListMgr):
        self._cfg = cfg
        self._list_mgr = list_mgr
        self._auto_blacklisted: dict[str, dict] = {}  # model_id → {reason, ts, cooldown}
        self._lock = threading.Lock()
        self.reload_from_config()

    def reload_from_config(self):
        pass  # config 变更时由 app.py 触发

    def tick(self, registry: ModelRegistry, engine=None):
        """每次 refresh 后运行自动规则"""
        mm = self._cfg.data.get("model_management", {})
        rules_cfg = mm.get("auto_rules", {})
        default_action = rules_cfg.get("default_action", "allow")

        # cooldown 恢复检查
        with self._lock:
            now = time.time()
            recovered = []
            for mid, info in self._auto_blacklisted.items():
                cooldown = info.get("cooldown", 3600)
                if now - info["ts"] > cooldown:
                    recovered.append(mid)
            for mid in recovered:
                del self._auto_blacklisted[mid]
                LOG.info("[AutoRules] cooldown expired, recovered: %s", mid)

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "auto_blacklisted": {
                    mid: {
                        "reason": info.get("reason", ""),
                        "ts": info.get("ts", 0),
                        "cooldown_remaining": max(0, info.get("cooldown", 3600) - (time.time() - info.get("ts", 0))),
                    }
                    for mid, info in self._auto_blacklisted.items()
                },
            }


# ── 统一入口 ──────────────────────────────────────────────

class ModelManager:
    """模型管理模块统一入口

    挂载到 app.py lifespan, 注入 config + registry。
    通过 registry.register_refresh_callback() 自动触发 discovery + notification。
    """

    def __init__(self, cfg: Config, registry: ModelRegistry, engine=None, snapshot_dir: str | None = None):
        self.cfg = cfg
        self.registry = registry
        self.engine = engine

        # 子模块
        snap_path = Path(snapshot_dir) if snapshot_dir else Path(__file__).parent.parent / ".model-snapshots"
        self.discovery = DiscoveryEngine(registry, snap_path)
        self.notifier = ModelNotifier(cfg)
        self.list_mgr = ListMgr(cfg)
        self.auto_rules = AutoRules(cfg, self.list_mgr)

        # 加载上次快照
        self.discovery.load_snapshot()

        mm = cfg.data.get("model_management", {})
        self.enabled = mm.get("enabled", False)
        LOG.info("ModelManager init: enabled=%s, providers=%d",
                 self.enabled, len(registry._providers))

    def on_refresh(self):
        """每次 refresh_all() 后自动触发 (通过 registry._refresh_callbacks 调用)"""
        if not self.enabled:
            return None
        try:
            # 1. 快照对比
            diff = self.discovery.refresh_and_diff()

            # 2. 通知
            self.notifier.notify(diff)

            # 3. 自动规则
            self.auto_rules.tick(self.registry, self.engine)

            # 4. 持久化快照
            self.discovery.save_snapshot()

            return diff
        except Exception:
            LOG.exception("ModelManager.on_refresh failed")
            return None

    def on_config_reload(self):
        """config.yaml 热重载时同步更新名单"""
        if not self.enabled:
            return
        try:
            self.list_mgr.reload_from_config()
            self.auto_rules.reload_from_config()
        except Exception:
            LOG.exception("ModelManager.on_config_reload failed")

    def status(self) -> dict:
        """返回模块状态摘要"""
        return {
            "enabled": self.enabled,
            "last_diff": self.discovery.last_diff.to_dict() if self.discovery.last_diff else None,
            "lists": self.list_mgr.to_dict(),
            "auto_rules": self.auto_rules.to_dict(),
            "snapshot_count": len(self.discovery._last_snapshot.models) if self.discovery._last_snapshot else 0,
        }
