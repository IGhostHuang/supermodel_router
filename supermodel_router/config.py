"""
supermodel_router/config.py — 配置加载 + 热重载
"""
import os
import re
import time
import threading
import logging
from pathlib import Path
from typing import Any, Dict

import yaml

LOG = logging.getLogger("config")

DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def _load_config(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class Config:
    """线程安全的配置管理器, 支持热重载"""

    def __init__(self, path: Path | str | None = None):
        self._path = Path(path) if path else DEFAULT_CONFIG_PATH
        self._data: dict = {}
        self._lock = threading.RLock()
        self._watchers: list = []
        self._watcher_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_mtime: float = 0
        self.load()

    # ---- 加载 ----

    def load(self):
        with self._lock:
            self._data = _load_config(self._path)
            self._last_mtime = self._path.stat().st_mtime
            LOG.info("Config loaded from %s", self._path)

    def reload_if_changed(self) -> bool:
        """文件 mtime 变了才 reload, 返回是否 reload"""
        try:
            mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            return False
        if mtime == self._last_mtime:
            return False
        with self._lock:
            self._data = _load_config(self._path)
            self._last_mtime = mtime
        LOG.info("Config reloaded (mtime changed)")
        for cb in self._watchers:
            try:
                cb(self._data)
            except Exception:
                LOG.exception("config watcher callback error")
        return True

    # ---- 访问器 ----

    def get(self, *keys, default=None):
        """config.get('providers', 'openrouter', 'base_url')"""
        with self._lock:
            obj = self._data
            for k in keys:
                if isinstance(obj, dict):
                    obj = obj.get(k)
                else:
                    return default
                if obj is None:
                    return default
            return obj

    @property
    def data(self):
        with self._lock:
            return self._data.copy()

    @property
    def server(self) -> dict:
        return self._data.get("server", {})

    @property
    def routing(self) -> dict:
        return self._data.get("routing", {})

    def group_strategy(self) -> str:
        """v3.9.0 (Phase H): 4 种轮询策略
        - flat: 老 v4 全局降序
        - round-robin-group: 桶间轮询 (默认)
        - group-failover: group 优先级 failover
        - group-weighted: 加权随机
        """
        return self._data.get("routing", {}).get("group_strategy", "round-robin-group")

    def group_weights(self) -> Dict[str, float]:
        """v3.9.0 (Phase H): group-weighted 策略专用, group_name → weight"""
        return self._data.get("routing", {}).get("group_weights", {})

    @property
    def providers(self) -> dict:
        return self._data.get("providers", {})

    def get_provider(self, name: str) -> dict | None:
        p = self.providers.get(name)
        if p and p.get("enabled", True):
            return p
        return None

    def get_provider_names(self) -> list[str]:
        return [k for k, v in self.providers.items() if v.get("enabled", True)]

    # ---- 自定义 Provider CRUD (持久化到 config.yaml) ----

    def add_provider(self, name: str, pcfg: dict, persist: bool = True) -> bool:
        """添加自定义 provider, 立即生效 + 写盘"""
        with self._lock:
            if "providers" not in self._data or self._data["providers"] is None:
                self._data["providers"] = {}
            if name in self._data["providers"]:
                LOG.warning("add_provider: '%s' already exists, use update", name)
                return False
            # v3.6: 默认 enabled=True
            pcfg.setdefault("enabled", True)
            self._data["providers"][name] = pcfg
            if persist:
                self._save_yaml()
            self._notify_change()
            LOG.info("Provider added: %s", name)
            return True

    def remove_provider(self, name: str, persist: bool = True) -> bool:
        """v3.6: 软删除 = enabled=False, 不真删 (UI 可恢复)
        真删用 hard_remove_provider, 仅对已软删的 provider 允许
        """
        with self._lock:
            providers = self._data.get("providers", {})
            if name not in providers:
                return False
            providers[name]["enabled"] = False
            if persist:
                self._save_yaml()
            self._notify_change()
            LOG.info("Provider soft-removed (disabled): %s", name)
            return True

    def disable_provider(self, name: str, reason: str = "", persist: bool = True) -> bool:
        """v3.16.0: 自动/手动禁用 provider (带原因)

        - 设 enabled=False
        - 记录 disabled_at (时间戳) + disabled_reason (string)
        - 持久化到 config.yaml (跟 enabled 一起保存)
        - 触发 reload (registry rebuild)
        """
        import time as _time
        with self._lock:
            providers = self._data.get("providers", {})
            if name not in providers:
                return False
            providers[name]["enabled"] = False
            providers[name]["disabled_at"] = _time.time()
            providers[name]["disabled_reason"] = reason or "auto-disabled by health check"
            if persist:
                self._save_yaml()
            self._notify_change()
            LOG.warning("Provider auto-disabled: %s (reason=%s)", name, providers[name]["disabled_reason"])
            return True

    def enable_provider(self, name: str, persist: bool = True) -> bool:
        """v3.16.0: 重新启用 provider (清 disabled metadata)

        - 设 enabled=True
        - 删 disabled_at + disabled_reason
        """
        with self._lock:
            providers = self._data.get("providers", {})
            if name not in providers:
                return False
            providers[name]["enabled"] = True
            providers[name].pop("disabled_at", None)
            providers[name].pop("disabled_reason", None)
            if persist:
                self._save_yaml()
            self._notify_change()
            LOG.info("Provider re-enabled: %s", name)
            return True

    def get_provider_disabled_meta(self, name: str) -> dict:
        """v3.16.0: 读 provider 禁用 metadata (admin UI 用)"""
        with self._lock:
            providers = self._data.get("providers", {})
            pcfg = providers.get(name, {})
            return {
                "enabled": pcfg.get("enabled", True),
                "disabled_at": pcfg.get("disabled_at"),
                "disabled_reason": pcfg.get("disabled_reason"),
            }

    # ---- v3.17.0: model alias (统一路由名称) ----

    DEFAULT_MODEL_ALIASES = {
        # 老大原话 (6/24): "API 调用统一的模型名, SMR 内部路由决定实际模型"
        # 6/24 改名: model-router → supermodel (我们有我们自己的特别, 不跟外部 model-router 项目重名)
        "auto": {
            "strategy": "modality_auto",   # 按 preferred_modalities 走 modality 路由
            "description": "空 / auto → 走 modality 自动路由",
        },
        "supermodel": {
            "strategy": "best_quality",     # 综合分最高的 model (capability + quality)
            "modality": None,                # 不过滤 modality (可选)
            "min_capability_score": 0,
            "exclude_providers": ["openrouter"],   # 排除已知高延迟 provider (老大实测 89% fail)
            "prefer_low_latency": False,
            "description": "SMR 智能路由: 选 quality_score 最高的可用 model (SMR 独家别名)",
        },
        "model-router": {                        # 旧名, 兼容老调用方 (alias_of supermodel)
            "alias_of": "supermodel",
            "description": "旧名 (向后兼容, 推荐用 supermodel)",
        },
        "router": {                              # 短写
            "alias_of": "supermodel",
            "description": "supermodel 简写",
        },
        "best": {
            "alias_of": "supermodel",
            "description": "supermodel 别名",
        },
        "cheap": {
            "strategy": "free_only",         # 只选 free 模型
            "description": "只选免费模型 (pricing=free 或 limited_free)",
        },
        "fast": {
            "strategy": "lowest_latency",    # 按 EWMA latency 升序
            "max_latency_ms": 10000,
            "description": "选 EWMA 延迟最低的模型 (< 10s)",
        },
    }

    def get_model_aliases(self) -> dict:
        """v3.17.0: 读所有 model aliases (config 覆盖 + 默认值合并)"""
        with self._lock:
            user_aliases = self._data.get("model_aliases", {}) or {}
        # 合并默认值 (用户覆盖优先)
        merged = dict(self.DEFAULT_MODEL_ALIASES)
        for name, cfg in user_aliases.items():
            if isinstance(cfg, dict) and "alias_of" in cfg:
                # alias 别名 (chain), resolve 到实际
                target = cfg["alias_of"]
                if target in merged:
                    merged[name] = dict(merged[target])
                    merged[name]["description"] = cfg.get("description", f"alias of '{target}'")
                else:
                    merged[name] = cfg
            else:
                merged[name] = cfg
        return merged

    def set_model_alias(self, name: str, cfg: dict, persist: bool = True) -> bool:
        """v3.17.0: 设置/修改 model alias (admin UI 用)"""
        with self._lock:
            if "model_aliases" not in self._data or self._data["model_aliases"] is None:
                self._data["model_aliases"] = {}
            self._data["model_aliases"][name] = cfg
            if persist:
                self._save_yaml()
            self._notify_change()
            LOG.info("model alias set: %s -> %s", name, cfg)
            return True

    def hard_remove_provider(self, name: str, persist: bool = True) -> bool:
        """v3.6: 真删 provider, 只有 enabled=False 才允许"""
        with self._lock:
            providers = self._data.get("providers", {})
            if name not in providers:
                return False
            if providers[name].get("enabled", True):
                LOG.warning("hard_remove_provider: '%s' still enabled, must disable first", name)
                return False
            del providers[name]
            if persist:
                self._save_yaml()
            self._notify_change()
            LOG.info("Provider hard-removed: %s", name)
            return True

    def set_provider_enabled(self, name: str, enabled: bool, persist: bool = True) -> bool:
        """v3.6: 启/停 provider (toggle)"""
        with self._lock:
            providers = self._data.get("providers", {})
            if name not in providers:
                return False
            providers[name]["enabled"] = bool(enabled)
            if persist:
                self._save_yaml()
            self._notify_change()
            LOG.info("Provider '%s' enabled=%s", name, enabled)
            return True

    def update_provider(self, name: str, pcfg: dict, persist: bool = True) -> bool:
        """更新 provider (增量覆盖字段)"""
        with self._lock:
            providers = self._data.get("providers", {})
            if name not in providers:
                return False
            providers[name].update(pcfg)
            if persist:
                self._save_yaml()
            self._notify_change()
            LOG.info("Provider updated: %s", name)
            return True

    def update_classifier(self, cfg: dict, persist: bool = True) -> bool:
        """更新 classifier 配置段 (tier_bonus / custom_keywords / modality_base_score)"""
        with self._lock:
            section = self._data.setdefault("classifier", None)
            if section is None:
                self._data["classifier"] = {}
                section = self._data["classifier"]
            section.update(cfg)
            if persist:
                self._save_yaml()
            self._notify_change()
            LOG.info("Classifier config updated: %s", list(cfg.keys()))
            return True

    def update_server(self, srv: dict, persist: bool = True) -> bool:
        """更新 server 段 (host / port / api_key). 注意: port 改动需重启服务."""
        with self._lock:
            section = self._data.setdefault("server", None)
            if section is None:
                self._data["server"] = {}
                section = self._data["server"]
            section.update(srv)
            if persist:
                self._save_yaml()
            self._notify_change()
            LOG.info("Server config updated: %s", list(srv.keys()))
            return True

    def update_routing(self, rt: dict, persist: bool = True) -> bool:
        """更新 routing 段 (strategy / failover_threshold / recovery_interval / max_retry / first_token_timeout_ms / retry_backoff_ms / quality_weights)"""
        with self._lock:
            section = self._data.setdefault("routing", None)
            if section is None:
                self._data["routing"] = {}
                section = self._data["routing"]
            section.update(rt)
            if persist:
                self._save_yaml()
            self._notify_change()
            LOG.info("Routing config updated: %s", list(rt.keys()))
            return True

    # ---- 配置版本管理 (v3.2.0) ----

    def _backup(self) -> Path | None:
        """写盘前自动备份到 .backups/config-YYYYMMDD-HHMMSS.yaml
        只保留最近 50 个备份, 超出按 mtime 删除最旧
        """
        try:
            if not self._path.exists():
                return None
            backup_dir = self._path.parent / ".backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            backup_path = backup_dir / f"config-{timestamp}.yaml"
            import shutil
            # 用 shutil.copy 不 copy2 → 不保留源 mtime, 备份 mtime = 当前时间
            shutil.copy(self._path, backup_path)
            # 清理: 保留最近 50 个
            backups = sorted(backup_dir.glob("config-*.yaml"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in backups[50:]:
                try:
                    old.unlink()
                except OSError:
                    pass
            LOG.debug("Config backup created: %s", backup_path.name)
            return backup_path
        except Exception:
            LOG.exception("_backup failed")
            return None

    def list_backups(self) -> list[dict]:
        """列出所有备份 (按 mtime 倒序)"""
        backup_dir = self._path.parent / ".backups"
        if not backup_dir.exists():
            return []
        backups = sorted(backup_dir.glob("config-*.yaml"), key=lambda p: p.stat().st_mtime, reverse=True)
        result = []
        for p in backups:
            st = p.stat()
            result.append({
                "name": p.name,
                "size_bytes": st.st_size,
                "mtime": st.st_mtime,
                "mtime_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)),
                "age_seconds": round(time.time() - st.st_mtime, 1),
            })
        return result

    def restore_backup(self, backup_name: str) -> bool:
        """从指定备份恢复 (覆盖当前 config.yaml + reload)"""
        backup_dir = self._path.parent / ".backups"
        # 防止路径穿越: 只接受 .yaml 后缀 + 简单文件名
        if "/" in backup_name or "\\" in backup_name or not backup_name.endswith(".yaml"):
            LOG.warning("restore_backup: invalid name '%s'", backup_name)
            return False
        backup_path = backup_dir / backup_name
        if not backup_path.exists():
            LOG.warning("restore_backup: '%s' not found", backup_name)
            return False
        with self._lock:
            import shutil
            # 先备份当前 (再回滚时能再来一次)
            self._backup()
            # copy 不 copy2 → 回滚后的 config mtime 是当前时间
            shutil.copy(backup_path, self._path)
            # 重新 load
            self._data = _load_config(self._path)
            self._last_mtime = self._path.stat().st_mtime
            self._notify_change()
        LOG.info("Config restored from %s", backup_name)
        return True

    def _save_yaml(self):
        """写回 yaml 文件 (v3.2: 写前自动备份)"""
        try:
            # v3.2: 自动备份
            self._backup()
            with open(self._path, "w", encoding="utf-8") as f:
                yaml.safe_dump(self._data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
            self._last_mtime = self._path.stat().st_mtime
            LOG.debug("Config persisted to %s", self._path)
        except Exception:
            LOG.exception("_save_yaml failed")

    def _notify_change(self):
        """触发热重载回调"""
        for cb in self._watchers:
            try:
                cb(self._data)
            except Exception:
                LOG.exception("config watcher callback error")

    # ---- 热重载 ----

    def start_watcher(self, interval: float = 5.0):
        if self._watcher_thread:
            return
        self._stop_event.clear()
        self._watcher_thread = threading.Thread(
            target=self._watch_loop, args=(interval,), daemon=True
        )
        self._watcher_thread.start()
        LOG.info("Config watcher started (%.1fs)", interval)

    def stop_watcher(self):
        self._stop_event.set()
        if self._watcher_thread:
            self._watcher_thread.join(timeout=2)
            self._watcher_thread = None

    def _watch_loop(self, interval: float):
        while not self._stop_event.wait(interval):
            self.reload_if_changed()

    def on_change(self, callback):
        self._watchers.append(callback)


# 全局单例
config = Config()
