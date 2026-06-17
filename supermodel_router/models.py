"""
supermodel_router/models.py — 模型发现 + 过滤 + 分类
"""
import logging
import time
import re
import threading
from dataclasses import dataclass, field

import httpx

from .config import Config
from .classifier import (
    classify_model, compute_capability_score,
    get_modality_display, TEXT_ONLY,
)

LOG = logging.getLogger("models")


@dataclass
class ModelInfo:
    id: str
    provider: str
    base_url: str
    object: str = "model"
    created: int = 0
    owned_by: str = ""
    # /v1/models 返回的额外字段透传
    extra: dict = field(default_factory=dict)
    # ── v2 能力分类 ──
    modality: str = TEXT_ONLY             # 模态类型
    capability_score: float = 50.0         # 能力分 (0-100)
    modality_display: str = "📝 纯文本"    # 前端展示


@dataclass
class ProviderState:
    name: str
    base_url: str
    api_keys: list[str]
    model_rules: dict
    max_concurrent: int = 3
    enabled: bool = True
    # 运行时状态
    models: list[ModelInfo] = field(default_factory=list)
    model_ids: list[str] = field(default_factory=list)  # 过滤后的 ID 列表
    key_index: int = 0  # round-robin key 指针
    # 健康
    degraded: bool = False
    fail_count: int = 0
    last_fail_time: float = 0
    last_model_refresh: float = 0


class ModelRegistry:
    """模型注册中心: 发现 + 过滤 + 健康跟踪"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._providers: dict[str, ProviderState] = {}
        self._lock = threading.RLock()
        # model_id → ProviderState 的快速路由表 (带 provider 前缀如 "openrouter/xxx")
        self._route_map: dict[str, ProviderState] = {}

    # ---- 初始化 ----

    def build(self):
        """根据 config 初始化所有 provider"""
        with self._lock:
            self._providers.clear()
            self._route_map.clear()
            for name, pcfg in self.cfg.providers.items():
                if not pcfg.get("enabled", True):
                    continue
                ps = ProviderState(
                    name=name,
                    base_url=pcfg["base_url"].rstrip("/"),
                    api_keys=pcfg.get("api_keys", []),
                    model_rules=pcfg.get("model_rules", {"mode": "all"}),
                    max_concurrent=pcfg.get("max_concurrent", 3),
                )
                self._providers[name] = ps
            LOG.info(
                "Registry built: %d providers",
                len(self._providers),
            )

    def refresh_all(self, timeout: float = 15.0):
        """发现所有 provider 的模型并应用过滤规则"""
        with self._lock:
            for ps in self._providers.values():
                try:
                    self._refresh_provider(ps, timeout)
                except Exception:
                    LOG.exception("refresh %s failed", ps.name)
            self._rebuild_route_map()

    def _refresh_provider(self, ps: ProviderState, timeout: float):
        """拉 /v1/models 并按规则过滤"""
        models = self._fetch_models(ps, timeout)
        if not models:
            ps.models = []
            ps.model_ids = []
            return

        # 应用过滤规则
        mode = ps.model_rules.get("mode", "all")
        pattern = ps.model_rules.get("pattern", "")
        include_set = set(ps.model_rules.get("include", []))
        exclude_patterns = ps.model_rules.get("exclude", [])  # v4 修复: 当正则处理

        filtered = []
        for m in models:
            mid = m["id"]
            # exclude 走正则 (跟 pattern 模式一致)
            if exclude_patterns and any(
                re.search(p, mid, re.IGNORECASE) for p in exclude_patterns
            ):
                continue
            if mode == "all":
                filtered.append(m)
            elif mode == "pattern":
                if re.search(pattern, mid, re.IGNORECASE):
                    filtered.append(m)
            elif mode == "include":
                if mid in include_set:
                    filtered.append(m)

        ps.models = []
        for m in filtered:
            mid = m["id"]
            modality = classify_model(mid, ps.name, m)
            cap_score = compute_capability_score(mid, modality, m, config_obj=self.cfg)
            ps.models.append(ModelInfo(
                id=mid,
                provider=ps.name,
                base_url=ps.base_url,
                object=m.get("object", "model"),
                created=m.get("created", 0),
                owned_by=m.get("owned_by", ""),
                extra={k: v for k, v in m.items()
                       if k not in ("id", "object", "created", "owned_by")},
                modality=modality,
                capability_score=round(cap_score, 1),
                modality_display=get_modality_display(modality),
            ))
        ps.model_ids = [m.id for m in ps.models]
        ps.last_model_refresh = time.time()
        LOG.info(
            "%s: %d models (filtered from %d)",
            ps.name, len(ps.models), len(models),
        )

    def _fetch_models(self, ps: ProviderState, timeout: float) -> list[dict]:
        """从 /v1/models 拉模型列表, 尝试所有 key (v4: 401/403 → 自动换 key)

        v4 修复: 之前第 1 个 key 401 → raise_for_status 直接抛, 0 models。
        现在: 401/403 → 跳到下一个 key, 全部 key 失败才返回空。
        """
        if not ps.api_keys:
            LOG.warning("%s: no api_key configured", ps.name)
            return []

        last_err = None
        tried = 0
        # round-robin 起点 (保证下一次不会总从 sk-bad 开始)
        start_idx = ps.key_index % len(ps.api_keys) if ps.api_keys else 0
        keys = ps.api_keys[start_idx:] + ps.api_keys[:start_idx]

        for key in keys:
            tried += 1
            url = f"{ps.base_url}/models"
            headers = {"Authorization": f"Bearer {key}"}
            try:
                resp = httpx.get(url, headers=headers, timeout=timeout)
                if resp.status_code in (200,):
                    ps.key_index = (start_idx + tried) % len(ps.api_keys)
                    data = resp.json()
                    LOG.info("%s: %d models fetched via key_idx=%d",
                             ps.name, len(data.get("data", [])),
                             (start_idx + tried - 1) % len(ps.api_keys))
                    return data.get("data", [])
                elif resp.status_code in (401, 403):
                    LOG.warning("%s: key_idx=%d returned %d, trying next",
                                ps.name, (start_idx + tried - 1) % len(ps.api_keys),
                                resp.status_code)
                    last_err = f"HTTP {resp.status_code}"
                    continue
                else:
                    LOG.warning("%s: key_idx=%d returned %d (not retryable)",
                                ps.name, (start_idx + tried - 1) % len(ps.api_keys),
                                resp.status_code)
                    last_err = f"HTTP {resp.status_code}"
                    # 4xx (非 401/403) 不重试 — 同一 provider 同样问题
                    break
            except httpx.TimeoutException:
                LOG.warning("%s: key_idx=%d timeout, trying next",
                            ps.name, (start_idx + tried - 1) % len(ps.api_keys))
                last_err = "timeout"
                continue
            except Exception as e:
                LOG.warning("%s: key_idx=%d error: %s, trying next",
                            ps.name, (start_idx + tried - 1) % len(ps.api_keys), e)
                last_err = str(e)
                continue

        LOG.error("%s: all %d keys failed (last_err=%s)",
                  ps.name, len(ps.api_keys), last_err)
        return []

    # ---- 路由 ----

    def _pick_key(self, ps: ProviderState) -> str | None:
        """round-robin 选 key"""
        if not ps.api_keys:
            return None
        idx = ps.key_index % len(ps.api_keys)
        ps.key_index = idx + 1
        return ps.api_keys[idx]

    def pick_key_for(self, provider_name: str) -> str | None:
        ps = self._providers.get(provider_name)
        if not ps:
            return None
        return self._pick_key(ps)

    def get_models(self, provider: str | None = None) -> list[ModelInfo]:
        """获取模型列表, 可按 provider 过滤"""
        with self._lock:
            if provider:
                ps = self._providers.get(provider)
                return list(ps.models) if ps else []
            result = []
            for ps in self._providers.values():
                result.extend(ps.models)
            return result

    def get_model_ids(self, provider: str | None = None) -> list[str]:
        """获取过滤后的模型 ID 列表"""
        with self._lock:
            if provider:
                ps = self._providers.get(provider)
                return list(ps.model_ids) if ps else []
            result = []
            for ps in self._providers.values():
                result.extend(ps.model_ids)
            return result

    def resolve(self, model_id: str) -> tuple[str, str, str] | None:
        """
        解析 model_id → (provider_name, base_url, api_key)
        支持 "provider/model" 前缀, 也支持裸 model ID (在全表搜索)
        """
        with self._lock:
            # 带前缀: openrouter/xxx
            if "/" in model_id:
                pname, mid = model_id.split("/", 1)
                ps = self._providers.get(pname)
                if ps and mid in ps.model_ids:
                    key = self._pick_key(ps)
                    if key:
                        return pname, ps.base_url, key

            # 裸 ID: 在所有 provider 中搜
            for ps in self._providers.values():
                if model_id in ps.model_ids:
                    key = self._pick_key(ps)
                    if key:
                        return ps.name, ps.base_url, key

            return None

    def all_routes(self) -> list[str]:
        """返回所有 "provider/model_id" 路由路径"""
        with self._lock:
            result = []
            for ps in self._providers.values():
                for mid in ps.model_ids:
                    result.append(f"{ps.name}/{mid}")
            return result

    # ---- 健康跟踪 ----

    def mark_fail(self, provider: str):
        ps = self._providers.get(provider)
        if not ps:
            return
        ps.fail_count += 1
        ps.last_fail_time = time.time()
        threshold = self.cfg.routing.get("failover_threshold", 3)
        if ps.fail_count >= threshold:
            ps.degraded = True
            LOG.warning(
                "%s: DEGRADED (fail_count=%d)", provider, ps.fail_count
            )

    def mark_ok(self, provider: str):
        ps = self._providers.get(provider)
        if not ps:
            return
        ps.fail_count = 0
        if ps.degraded:
            ps.degraded = False
            LOG.info("%s: RECOVERED", provider)

    def check_recovery(self):
        """检查 degraded provider 是否到 recovery 时间"""
        interval = self.cfg.routing.get("recovery_interval", 300)
        now = time.time()
        for ps in self._providers.values():
            if ps.degraded and (now - ps.last_fail_time) > interval:
                ps.degraded = False
                LOG.info("%s: auto-recovered (interval=%ds)", ps.name, interval)

    # ---- 内部 ----

    def _rebuild_route_map(self):
        self._route_map.clear()
        for ps in self._providers.values():
            for mid in ps.model_ids:
                self._route_map[f"{ps.name}/{mid}"] = ps

    def get_state(self) -> dict:
        """返回各 provider 状态摘要"""
        with self._lock:
            return {
                name: {
                    "enabled": ps.enabled,
                    "degraded": ps.degraded,
                    "fail_count": ps.fail_count,
                    "models": len(ps.model_ids),
                    "base_url": ps.base_url,
                }
                for name, ps in self._providers.items()
            }

    # ── v2 模态分组 ────────────────────────────────────

    def get_models_by_modality(self, modality: str) -> list[ModelInfo]:
        """按模态类型获取模型列表"""
        with self._lock:
            return [
                m for ps in self._providers.values()
                for m in ps.models
                if m.modality == modality
            ]

    def get_modality_counts(self) -> dict[str, int]:
        """统计各模态的模型数量"""
        counts = {}
        with self._lock:
            for ps in self._providers.values():
                for m in ps.models:
                    counts[m.modality] = counts.get(m.modality, 0) + 1
        return counts
