"""
supermodel_router/config.py — 配置加载 + 热重载
"""
import os
import re
import time
import threading
import logging
from pathlib import Path
from typing import Any

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
            self._data["providers"][name] = pcfg
            if persist:
                self._save_yaml()
            self._notify_change()
            LOG.info("Provider added: %s", name)
            return True

    def remove_provider(self, name: str, persist: bool = True) -> bool:
        """删除 provider"""
        with self._lock:
            providers = self._data.get("providers", {})
            if name not in providers:
                return False
            del providers[name]
            if persist:
                self._save_yaml()
            self._notify_change()
            LOG.info("Provider removed: %s", name)
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

    def _save_yaml(self):
        """写回 yaml 文件"""
        try:
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
