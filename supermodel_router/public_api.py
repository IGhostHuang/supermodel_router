"""
supermodel_router/public_api.py — 对外 API 模块 (v3.7.0)

老大 2026-06-18 拍: 中转 router 不对外就丧失核心功能.
设计:
- 多 key 体系: 每个对外用户一个 API key (per-tenant)
- key 元数据: name / key_hash / rate_limit (rpm) / model_filter / enabled
- 用量统计: 每 key 独立计数 (total / success / fail / tokens / last_used)
- 向后兼容: config.server.api_key (单 key) 仍可用, 视为默认公开 key
- 认证顺序: 公开 key (per-tenant) → 默认 key (单 key) → 401

v3.7.0 落地:
- /v1/public/api-keys (admin CRUD)
- 中间件注入 current_api_key 到 request.state
- /v1/public/stats 看每个 key 用量
- 持久化: state/public_keys_state.json (原子写, debounce 5s)
"""
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("public_api")


def _hash_key(key: str) -> str:
    """key 哈希 (完整 64 字符 SHA256, 用于显示和比较)

    v3.7.1 (小星雲 review P0 BUG-001): 用完整 SHA256 避免 64bit 截断碰撞
    之前 [:16] 只 16 hex 字符 = 64bit, 生日攻击 ~2^32 key 才碰撞,
    但多租户 + 攻击者可控 key 名 → 风险升级, 改完整 64 字符
    """
    return hashlib.sha256(key.encode()).hexdigest()


class PublicKeyManager:
    """对外 API key 管理 + 用量追踪

    state 存盘: state/public_keys_state.json
    """
    DEBOUNCE_S = 5.0  # 写盘 debounce

    def __init__(self, state_dir: str = "."):
        self._lock = threading.RLock()
        self._state_dir = Path(state_dir)
        self._state_file = self._state_dir / "public_keys_state.json"
        self._keys: Dict[str, Dict[str, Any]] = {}  # name -> key_meta
        self._usage: Dict[str, Dict[str, Any]] = {}  # key_hash -> usage
        self._last_save: float = 0.0
        self._dirty: bool = False
        self._load()
        self._save_thread: Optional[threading.Thread] = None

    def _load(self):
        """启动时读盘"""
        if not self._state_file.exists():
            LOG.info("PublicKeyManager: no state file, starting empty")
            return
        try:
            data = json.loads(self._state_file.read_text())
            self._keys = data.get("keys", {})
            self._usage = data.get("usage", {})
            LOG.info("PublicKeyManager: loaded %d keys, %d usage records",
                     len(self._keys), len(self._usage))
        except Exception as e:
            LOG.warning("PublicKeyManager: load failed (%s), starting empty", e)

    def _save_async(self):
        """debounce 异步写盘"""
        self._dirty = True
        now = time.time()
        if now - self._last_save < self.DEBOUNCE_S:
            return
        self._flush()

    def _flush(self):
        """立即写盘"""
        with self._lock:
            if not self._dirty:
                return
            self._state_dir.mkdir(parents=True, exist_ok=True)
            data = {
                "keys": self._keys,
                "usage": self._usage,
                "updated_at": time.time(),
            }
            tmp = self._state_file.with_suffix(".json.tmp")
            try:
                tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
                os.replace(tmp, self._state_file)  # atomic rename
                self._dirty = False
                self._last_save = time.time()
            except Exception as e:
                LOG.warning("PublicKeyManager: save failed: %s", e)

    # ---- CRUD ----

    def create_key(self, name: str, rate_limit_rpm: int = 60,
                   model_filter: Optional[List[str]] = None,
                   note: str = "") -> Dict[str, Any]:
        """生成新 key (返回原 key 一次, 之后只存哈希)

        name: 友好名 (e.g. "user-alice" / "team-mobile")
        rate_limit_rpm: 每分钟请求数上限 (0 = 不限)
        model_filter: 白名单模型 (None = 全部)
        """
        with self._lock:
            if name in self._keys:
                raise ValueError(f"key name '{name}' already exists")
            raw_key = f"smr-pub-{secrets.token_urlsafe(24)}"
            key_hash = _hash_key(raw_key)
            self._keys[name] = {
                "name": name,
                "key_hash": key_hash,
                "rate_limit_rpm": rate_limit_rpm,
                "model_filter": model_filter or [],
                "note": note,
                "enabled": True,
                "created_at": time.time(),
            }
            self._usage[key_hash] = {
                "total_calls": 0,
                "success_calls": 0,
                "fail_calls": 0,
                "tokens": 0,
                "last_used": 0.0,
                "rate_window": [],  # (timestamp,) for sliding window
            }
            self._save_async()
            # 立即落盘 (创建是高优操作)
            self._flush()
            return {
                "name": name,
                "key": raw_key,  # 只这一次返回原 key!
                "key_hash": key_hash,
                "rate_limit_rpm": rate_limit_rpm,
                "model_filter": model_filter or [],
                "note": note,
            }

    def list_keys(self) -> List[Dict[str, Any]]:
        """列出所有 key (不含原 key, 只哈希 + 用量)"""
        with self._lock:
            return [
                {**meta, "usage": self._usage.get(meta["key_hash"], {})}
                for meta in self._keys.values()
            ]

    def get_key(self, name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            meta = self._keys.get(name)
            if not meta:
                return None
            return {**meta, "usage": self._usage.get(meta["key_hash"], {})}

    def update_key(self, name: str, **fields) -> Dict[str, Any]:
        """更新 key 元数据 (rate_limit / model_filter / enabled / note)
        不允许改 name / key_hash
        """
        with self._lock:
            if name not in self._keys:
                raise KeyError(f"key '{name}' not found")
            allowed = {"rate_limit_rpm", "model_filter", "enabled", "note"}
            for k in fields:
                if k not in allowed:
                    raise ValueError(f"field '{k}' not editable")
            self._keys[name].update({k: v for k, v in fields.items() if k in allowed})
            self._save_async()
            self._flush()
            return self._keys[name]

    def delete_key(self, name: str) -> bool:
        with self._lock:
            if name not in self._keys:
                return False
            key_hash = self._keys[name]["key_hash"]
            del self._keys[name]
            self._usage.pop(key_hash, None)
            self._save_async()
            self._flush()
            return True

    # ---- 认证 + 用量追踪 ----

    def authenticate(self, raw_key: str) -> Optional[Dict[str, Any]]:
        """验证 key 返回 key_meta (找不到返回 None)"""
        if not raw_key:
            return None
        key_hash = _hash_key(raw_key)
        with self._lock:
            for meta in self._keys.values():
                if meta["key_hash"] == key_hash and meta.get("enabled", True):
                    return meta
        return None

    def check_rate_limit(self, meta: Dict[str, Any]) -> bool:
        """sliding window rate limit, True = 通过, False = 超限

        v3.7.1 (小星雲 review P0 BUG-002): 显式声明 in-memory 不持久化
        SMR 重启 → rate_window 清零 → 用户可瞬时打满 rpm 1 次
        缓解建议 (不在 v3.7.1 范围): rate_window 走 public_keys_state.json
        持久化 + 启动时按 now - window 过滤有效条目
        """
        rpm = meta.get("rate_limit_rpm", 0)
        if rpm <= 0:  # 0 = 不限
            return True
        key_hash = meta["key_hash"]
        now = time.time()
        with self._lock:
            u = self._usage.setdefault(key_hash, {})
            window = u.setdefault("rate_window", [])
            # 清理 60s 之前的 (in-memory, 不持久化)
            window[:] = [t for t in window if now - t < 60]
            if len(window) >= rpm:
                return False
            window.append(now)
            return True

    def check_model_filter(self, meta: Dict[str, Any], model: str) -> bool:
        """model_filter 白名单, True = 允许, False = 拒绝"""
        flt = meta.get("model_filter", [])
        if not flt:  # 空 = 全部允许
            return True
        # 兼容: gpt-4 也匹配 gpt-4*
        for pattern in flt:
            if pattern == model:
                return True
            if pattern.endswith("*") and model.startswith(pattern[:-1]):
                return True
        return False

    def record_usage(self, key_hash: str, success: bool, tokens: int = 0):
        """请求完成时记录 (success/fail + tokens)"""
        with self._lock:
            u = self._usage.setdefault(key_hash, {})
            u["total_calls"] = u.get("total_calls", 0) + 1
            if success:
                u["success_calls"] = u.get("success_calls", 0) + 1
            else:
                u["fail_calls"] = u.get("fail_calls", 0) + 1
            u["tokens"] = u.get("tokens", 0) + tokens
            u["last_used"] = time.time()

    def get_all_usage(self) -> Dict[str, Any]:
        """全局用量汇总 (admin 用)"""
        with self._lock:
            return {
                "total_keys": len(self._keys),
                "enabled_keys": sum(1 for k in self._keys.values() if k.get("enabled", True)),
                "keys": [
                    {**meta, "usage": self._usage.get(meta["key_hash"], {})}
                    for meta in self._keys.values()
                ],
            }


# 全局单例 (lifespan 初始化)
public_key_manager: Optional[PublicKeyManager] = None


def init_public_key_manager(state_dir: str = ".") -> PublicKeyManager:
    global public_key_manager
    public_key_manager = PublicKeyManager(state_dir=state_dir)
    LOG.info("PublicKeyManager initialized: state_dir=%s", state_dir)
    return public_key_manager