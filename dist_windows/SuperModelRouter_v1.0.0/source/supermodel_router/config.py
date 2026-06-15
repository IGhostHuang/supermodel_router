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
