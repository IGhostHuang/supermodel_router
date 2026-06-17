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
from .model_rules import ModelRuleEngine

LOG = logging.getLogger("models")


# v3.6 知名 provider 的默认 base_url (用户填域名就自动补全)
KNOWN_BASE_URLS: dict[str, str] = {
    "openrouter": "https://openrouter.ai/api/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "yi": "https://api.lingyiwanwu.com/v1",
    "ollama": "http://localhost:11434/v1",
    "lm-studio": "http://localhost:1234/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "volcengine": "https://ark.cn-beijing.volces.com/api/v3",
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
}


def normalize_base_url(name: str, raw: str) -> str:
    """v3.6 自动补全 base_url:
    1. 空 → 按 provider name 查 KNOWN_BASE_URLS
    2. 没 scheme → 加 https://
    3. 知名 provider 域名但缺 /v1 → 自动补 (openrouter.ai/api → https://openrouter.ai/api/v1)
    4. rstrip('/') 标准化
    """
    if not raw:
        return KNOWN_BASE_URLS.get(name.lower(), "")
    s = raw.strip()
    # 1. 没 scheme → 加 https://
    if not s.startswith(("http://", "https://")):
        s = "https://" + s
    # 2. 知名域名补 /v1 (含 localhost 本地服务)
    from urllib.parse import urlparse, urlunparse
    p = urlparse(s)
    host = p.netloc.lower()
    path = p.path.rstrip("/")
    # 知名域名 (带 /v1 默认 path)
    known_hosts_full = {
        "openrouter.ai": "/api/v1",
        "api.openai.com": "/v1",
        "api.anthropic.com": "/v1",
        "api.deepseek.com": "/v1",
        "integrate.api.nvidia.com": "/v1",
        "api.mistral.ai": "/v1",
        "api.groq.com": "/openai/v1",
        "api.moonshot.cn": "/v1",
        "open.bigmodel.cn": "/api/paas/v4",
        "api.lingyiwanwu.com": "/v1",
        "ark.cn-beijing.volces.com": "/api/v3",
        "dashscope.aliyuncs.com": "/compatible-mode/v1",
        "api.siliconflow.cn": "/v1",
        "localhost:11434": "/v1",       # ollama
        "localhost:1234": "/v1",        # lm-studio
        "127.0.0.1:11434": "/v1",       # ollama IP
        "127.0.0.1:1234": "/v1",        # lm-studio IP
    }
    if host in known_hosts_full and not path:
        path = known_hosts_full[host]
    # openrouter 特殊: path=/api 时升级为 /api/v1 (用户常见填法)
    if host == "openrouter.ai" and path in ("", "/api"):
        path = "/api/v1"
    return urlunparse((p.scheme, p.netloc, path, "", "", "")).rstrip("/")


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
    """模型注册中心: 发现 + 过滤 + 健康跟踪 + v3.3 规则引擎集成"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._providers: dict[str, ProviderState] = {}
        self._lock = threading.RLock()
        # model_id → ProviderState 的快速路由表 (带 provider 前缀如 "openrouter/xxx")
        self._route_map: dict[str, ProviderState] = {}
        # v3.3: 模型管理规则引擎
        import os
        self.rule_engine = ModelRuleEngine(rules_dir=cfg.data.get("model_management", {}).get("state_dir", os.path.dirname(cfg._path) if hasattr(cfg, "_path") else "."))
        # v3.3: 上一次每个 provider 的模型 ID 快照 (用于 diff)
        self._prev_model_ids: dict[str, list[str]] = {}
        # v3.3: refresh 完成回调列表 (model_manager 注册到此)
        self._refresh_callbacks: list = []

    def register_refresh_callback(self, fn):
        """注册 refresh 完成后的回调: fn()"""
        self._refresh_callbacks.append(fn)

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
                    base_url=normalize_base_url(name, pcfg.get("base_url", "")),
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
        """发现所有 provider 的模型并应用过滤规则 + v3.3 规则引擎"""
        with self._lock:
            for ps in self._providers.values():
                # v3.3: 保存 old model IDs
                old_ids = list(self._prev_model_ids.get(ps.name, []))
                try:
                    self._refresh_provider(ps, timeout)
                except Exception:
                    LOG.exception("refresh %s failed", ps.name)

                # v3.3: 应用管理规则过滤
                raw_models = [{"id": m.id, **m.extra} for m in ps.models]
                filtered = self.rule_engine.apply_to_model_list(raw_models, ps.name)
                filtered_ids = [m["id"] for m in filtered]
                ps.models = [m for m in ps.models if m.id in filtered_ids]
                ps.model_ids = [m.id for m in ps.models]

                # v3.3: 记录 discovery diff
                self._prev_model_ids[ps.name] = list(ps.model_ids)
                self.rule_engine.record_discovery(ps.name, old_ids, list(ps.model_ids))

            self._rebuild_route_map()
        # v3.3: 触发 refresh 完成回调
        for cb in self._refresh_callbacks:
            try:
                cb()
            except Exception:
                LOG.exception("refresh callback failed")

    def refresh_provider(self, name: str, timeout: float = 15.0):
        """v3.6: 单独刷新一个 provider (针对性获取模型, 不动其他)
        用途: UI 上点 "刷新模型" 按钮, 避免刷新全表
        """
        ps = self._providers.get(name)
        if not ps:
            LOG.warning("refresh_provider: '%s' not found", name)
            return False
        with self._lock:
            try:
                old_ids = list(self._prev_model_ids.get(ps.name, []))
                self._refresh_provider(ps, timeout)
                raw_models = [{"id": m.id, **m.extra} for m in ps.models]
                filtered = self.rule_engine.apply_to_model_list(raw_models, ps.name)
                filtered_ids = [m["id"] for m in filtered]
                ps.models = [m for m in ps.models if m.id in filtered_ids]
                ps.model_ids = [m.id for m in ps.models]
                self._prev_model_ids[ps.name] = list(ps.model_ids)
                self.rule_engine.record_discovery(ps.name, old_ids, list(ps.model_ids))
                self._rebuild_route_map()
                LOG.info("refresh_provider '%s': %d models", name, len(ps.models))
                for cb in self._refresh_callbacks:
                    try:
                        cb()
                    except Exception:
                        LOG.exception("refresh callback failed")
                return True
            except Exception:
                LOG.exception("refresh_provider '%s' failed", name)
                return False

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
