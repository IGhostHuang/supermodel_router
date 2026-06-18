"""
supermodel_router/admin_api.py — 管理 API 端点 (v3.2.0 拆分)

v3.4.0 新增 (2026-06-17):
- /v1/admin/context_bridge (config + stats)
- /v1/admin/context_bridge/reset (清零 stats)

endpoint 列表:
- /v1/health
- /v1/admin/modalities /routes /stats /refresh
- /v1/admin/config (reload)
- /v1/admin/providers (POST/DELETE/PUT)
- /v1/admin/classifier (GET/PUT)
- /v1/admin/server (GET/PUT)
- /v1/admin/routing (GET/PUT)
- /v1/admin/penalty (GET/reset/decay)
- /v1/admin/version /upgrade
- /v1/admin/config/backups (v3.2.0 新增)
- /v1/admin/config/restore (v3.2.0 新增)
- /v1/admin/context_bridge (v3.4.0 新增)
"""
import time
import copy
import logging
from typing import Any
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .config import config
from .context_bridge import ContextBridge  # v3.4.0
from .version import VERSION as SMR_VERSION, BUILD_DATE as SMR_BUILD_DATE
SMR_APP_TITLE = f"SuperModel Router v{SMR_VERSION}"

LOG = logging.getLogger("admin_api")
router = APIRouter()

registry: Any = None
engine: Any = None
model_manager: Any = None
# v3.4.0: ContextBridge 单例
context_bridge: ContextBridge | None = None
_start_time = 0


def init(app_registry, app_engine, app_model_manager, start_time, app_bridge: ContextBridge | None = None):
    global registry, engine, model_manager, _start_time, context_bridge
    registry = app_registry
    engine = app_engine
    model_manager = app_model_manager
    _start_time = start_time
    context_bridge = app_bridge


def _refresh_async(reg, tag: str = "manual", only: str | None = None):
    """v3.6: 异步刷新 model registry (不阻塞 HTTP 响应)
    tag: 日志标记 (add:openai / delete:anthropic / refresh:openrouter)
    only: 只刷一个 provider name, 走 reg.refresh_provider(name) (待实现, 暂走 refresh_all)
    """
    import threading
    def _do():
        try:
            if only and hasattr(reg, "refresh_provider"):
                reg.refresh_provider(only)
            else:
                reg.refresh_all()
        except Exception as e:
            import logging
            logging.getLogger("admin").exception("background refresh failed (%s): %s", tag, e)
    threading.Thread(target=_do, daemon=True, name=f"refresh-{tag}").start()


@router.get("/v1/health")
async def health():
    from .version import VERSION as CURRENT_VERSION, BUILD_DATE
    penalty_state = engine.get_model_penalty()
    return JSONResponse({
        "status": "ok",
        "version": CURRENT_VERSION,
        "build_date": BUILD_DATE,
        "title": SMR_APP_TITLE,
        "uptime_seconds": round(time.time() - _start_time, 1),
        "total_models": len(registry.get_model_ids()),
        "providers": registry.get_state(),
        "model_penalty_summary": {
            "count": len(penalty_state["penalties"]),
            "top_5": dict(sorted(
                penalty_state["penalties"].items(),
                key=lambda x: -x[1]
            )[:5]),
        },
    })


@router.get("/v1/admin/modalities")
async def admin_modalities():
    """各模态的模型数量分布"""
    return JSONResponse(registry.get_modality_counts())


@router.get("/v1/admin/routes")
async def admin_routes():
    """v3.6: 路由列表 + 模型详情 (含 pricing_type)
    v3.8.0: 加 context_window + capability_score (从 classifier 拿)
    """
    from .classifier import classify_pricing, compute_capability_score
    out = []
    for r in registry.all_routes():
        # r 格式: "provider/model_id"
        if "/" in r:
            p, mid = r.split("/", 1)
            pricing = classify_pricing(p, mid)
            # ✅ v3.8.0: 加 context_window + score
            try:
                model = registry.get_model(p, mid)
                ctx = getattr(model, "context_window", 0) or 0
            except Exception:
                ctx = 0
            try:
                score = round(compute_capability_score(mid, "text", extra={"context_window": ctx}), 1)
            except Exception:
                score = None
            out.append({
                "route": r, "provider": p, "model": mid,
                "pricing": pricing, "context_window": ctx, "score": score,
            })
        else:
            out.append({"route": r, "provider": "?", "model": r, "pricing": "unknown"})
    return JSONResponse({"routes": out, "total": len(out)})


@router.get("/v1/admin/models")
async def admin_models(provider: str | None = None, pricing: str | None = None):
    """v3.6: 详细模型列表 (含 pricing_type, capability_score, modality)
    query: ?provider=openrouter 过滤 provider
           ?pricing=free       过滤收费类型
    """
    from .classifier import classify_pricing, PRICING_FREE
    out = []
    for ps in registry._providers.values():
        if provider and ps.name != provider:
            continue
        for m in ps.models:
            p = classify_pricing(ps.name, m.id)
            if pricing and p != pricing:
                continue
            out.append({
                "id": m.id,
                "provider": ps.name,
                "modality": m.modality,
                "modality_display": m.modality_display,
                "capability_score": m.capability_score,
                "context_window": m.context_window,  # v3.8.0: 上下文窗口 (0=未知)
                "pricing": p,
                "is_free": p == PRICING_FREE,
                "base_url": ps.base_url,
            })
    return JSONResponse({"models": out, "total": len(out)})


@router.get("/v1/admin/providers")
async def admin_providers_list(include_disabled: bool = True):
    """v3.6: provider 列表 (含 enabled 状态)"""
    from .models import KNOWN_BASE_URLS
    out = []
    for pname, pcfg in (config.get("providers") or {}).items():
        enabled = pcfg.get("enabled", True)
        if not include_disabled and not enabled:
            continue
        keys = pcfg.get("api_keys", [])
        out.append({
            "name": pname,
            "enabled": enabled,
            "base_url": pcfg.get("base_url", ""),
            "is_known": pname.lower() in KNOWN_BASE_URLS,
            "key_count": len(keys),
            "key_fingerprint": keys[0][:8] + "..." + keys[0][-4:] if keys and len(keys[0]) > 12 else ("***" if keys else ""),
            "model_rules": pcfg.get("model_rules", {"mode": "all"}),
            "max_concurrent": pcfg.get("max_concurrent", 3),
        })
    return JSONResponse({"providers": out, "total": len(out)})


@router.get("/v1/admin/stats")
async def admin_stats():
    return JSONResponse(engine.get_stats())


@router.post("/v1/admin/refresh")
async def admin_refresh():
    registry.refresh_all()
    return JSONResponse({
        "ok": True,
        "providers": registry.get_state(),
    })


@router.post("/v1/admin/config/reload")
async def admin_config_reload(mode: str = "memory"):
    """v3.6: 重新加载配置
    - mode=memory (default): 只 rebuild registry + refresh, 不重读 yaml
      适用: UI 上 add/update/delete provider 后, 想立即刷新路由
    - mode=disk: 强制从 yaml 重读 (用户手动改 yaml 后)
      注意: 会用 yaml 内容覆盖 in-memory (如果有未 persist 的改动会丢)
    """
    if mode == "disk":
        config.load()
    # memory 模式不动 config.data, add/update/delete 已经改 in-memory 了
    registry.build()
    registry.refresh_all()
    return JSONResponse({
        "ok": True,
        "mode": mode,
        "providers": registry.get_state(),
    })


@router.get("/v1/admin/config")
async def admin_config_get():
    import copy
    data = copy.deepcopy(config.data)
    for pname, pcfg in data.get("providers", {}).items():
        keys = pcfg.get("api_keys", [])
        pcfg["api_keys"] = [
            k[:8] + "..." + k[-4:] if len(k) > 12 else "***"
            for k in keys
        ]
    if data.get("server", {}).get("api_key"):
        data["server"]["api_key"] = "***"
    return JSONResponse(data)


# ============================================================
# 自定义 Provider 管理 API
# ============================================================

@router.post("/v1/admin/providers")
async def admin_providers_add(payload: dict):
    """添加自定义 provider
    payload = {"name": "myopenai", "config": {"base_url": "...", "api_keys": [...], ...}}
    """
    name = payload.get("name")
    pcfg = payload.get("config", {})
    if not name or not pcfg.get("base_url"):
        return JSONResponse(
            {"error": "name and config.base_url required"},
            status_code=400,
        )
    if not pcfg.get("api_keys"):
        return JSONResponse(
            {"error": "config.api_keys (list) required"},
            status_code=400,
        )
    # 默认值补全
    pcfg.setdefault("enabled", True)
    pcfg.setdefault("max_concurrent", 3)
    pcfg.setdefault("model_rules", {"mode": "all"})
    # v3.6 自动补全 base_url (用户填 openrouter.ai/api → https://openrouter.ai/api/v1)
    from .models import normalize_base_url
    pcfg["base_url"] = normalize_base_url(name, pcfg["base_url"])

    ok = config.add_provider(name, pcfg)
    if not ok:
        return JSONResponse(
            {"error": f"provider '{name}' already exists"},
            status_code=409,
        )
    # 立即注册 (build 必须同步,否则 provider 不在 registry 里)
    registry.build()
    # refresh 异步跑 — 不让 provider 网络慢/挂时阻塞响应
    _refresh_async(registry, tag=f"add:{name}")
    return JSONResponse({
        "ok": True,
        "name": name,
        "config": pcfg,
        "refreshing": True,
        "hint": "model discovery running in background, GET /v1/admin/providers to check status",
    })


@router.delete("/v1/admin/providers/{name}")
async def admin_providers_delete(name: str, force: bool = False):
    """v3.6: 删除 provider
    - force=false (默认): 软删除 = enabled=False, 可恢复
    - force=true: 硬删除 (仅对已停用的 provider 允许, 防止误删)
    """
    if force:
        ok = config.hard_remove_provider(name)
        if not ok:
            return JSONResponse(
                {"error": f"provider '{name}' not found or still enabled, disable first"},
                status_code=400,
            )
    else:
        ok = config.remove_provider(name)
        if not ok:
            return JSONResponse(
                {"error": f"provider '{name}' not found"},
                status_code=404,
            )
    registry.build()
    _refresh_async(registry, tag=f"delete:{name}")
    return JSONResponse({"ok": True, "name": name, "soft": not force})


@router.post("/v1/admin/providers/{name}/enable")
async def admin_providers_enable(name: str):
    """v3.6: 启用 provider"""
    ok = config.set_provider_enabled(name, True)
    if not ok:
        return JSONResponse({"error": f"provider '{name}' not found"}, status_code=404)
    registry.build()
    _refresh_async(registry, tag=f"enable:{name}")
    return JSONResponse({"ok": True, "name": name, "enabled": True})


@router.post("/v1/admin/providers/{name}/disable")
async def admin_providers_disable(name: str):
    """v3.6: 停用 provider"""
    ok = config.set_provider_enabled(name, False)
    if not ok:
        return JSONResponse({"error": f"provider '{name}' not found"}, status_code=404)
    registry.build()
    return JSONResponse({"ok": True, "name": name, "enabled": False})


# v3.6.0: API Key 独立管理 (Phase G)
import hashlib
def _fingerprint_key(key: str) -> str:
    """生成 key 指纹 (sha256 前 12 字符, 脱敏但能识别)"""
    if not key:
        return ""
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"sha256:{h[:12]}"


@router.get("/v1/admin/api-keys")
async def admin_api_keys_list(provider: str | None = None):
    """列出所有 provider 的 API key 摘要 (脱敏指纹 + 数量, 不含真实 key)."""
    providers = config.data.get("providers", {})
    items = []
    for name, pcfg in providers.items():
        if provider and name != provider:
            continue
        keys = pcfg.get("api_keys", [])
        # 算联合 fingerprint (所有 key 拼一起的 sha256)
        joined = "|".join(keys)
        items.append({
            "provider": name,
            "count": len(keys),
            "fingerprint": _fingerprint_key(joined) if keys else None,
            "preview": [
                (k[:8] + "..." + k[-4:]) if len(k) > 12 else "***"
                for k in keys
            ],
            "enabled": pcfg.get("enabled", True),
        })
    return JSONResponse({"version": "3.7.0", "count": len(items), "keys": items})


@router.post("/v1/admin/api-keys")
async def admin_api_keys_add(payload: dict):
    """为某个 provider 添加新的 API key (追加到现有 keys 列表).
    payload = {"provider": "myopenai", "api_key": "sk-xxx"}
    """
    provider = (payload.get("provider") or "").strip()
    api_key = (payload.get("api_key") or "").strip()
    if not provider or not api_key:
        return JSONResponse({"error": "provider and api_key required"}, status_code=400)
    pcfg = config.get("providers", provider)
    if not pcfg:
        return JSONResponse({"error": f"provider '{provider}' not found"}, status_code=404)
    existing = list(pcfg.get("api_keys", []))
    if api_key in existing:
        return JSONResponse({"error": "api_key already exists for this provider"}, status_code=409)
    existing.append(api_key)
    ok = config.update_provider(provider, {"api_keys": existing})
    if not ok:
        return JSONResponse({"error": "update_provider failed"}, status_code=500)
    # 触发一次 refresh (新 key 可能解锁新 model)
    _refresh_async(registry, tag=f"addkey:{provider}")
    return JSONResponse({
        "ok": True,
        "provider": provider,
        "count": len(existing),
        "added_fingerprint": _fingerprint_key(api_key),
    })


@router.delete("/v1/admin/api-keys/{provider}")
async def admin_api_keys_clear(provider: str, key_index: int | None = None):
    """清空 provider 的所有 API key (或指定 index 删除一个).
    query: ?key_index=N 删除第 N 个 (0-based)
    """
    pcfg = config.get("providers", provider)
    if not pcfg:
        return JSONResponse({"error": f"provider '{provider}' not found"}, status_code=404)
    existing = list(pcfg.get("api_keys", []))
    if not existing:
        return JSONResponse({"error": "no keys to remove"}, status_code=400)
    if key_index is not None:
        if key_index < 0 or key_index >= len(existing):
            return JSONResponse({"error": f"key_index {key_index} out of range (0-{len(existing)-1})"}, status_code=400)
        removed = existing.pop(key_index)
        ok = config.update_provider(provider, {"api_keys": existing})
        if not ok:
            return JSONResponse({"error": "update_provider failed"}, status_code=500)
        return JSONResponse({
            "ok": True,
            "provider": provider,
            "removed_fingerprint": _fingerprint_key(removed),
            "remaining": len(existing),
        })
    # 全清
    ok = config.update_provider(provider, {"api_keys": []})
    if not ok:
        return JSONResponse({"error": "update_provider failed"}, status_code=500)
    return JSONResponse({"ok": True, "provider": provider, "cleared": True, "remaining": 0})


# v3.6.0: 复制 provider (Phase F)
@router.post("/v1/admin/providers/{name}/clone")
async def admin_providers_clone(name: str, payload: dict | None = None):
    """复制一个 provider 为新名称.
    payload = {"new_name": "myopenai_copy"}
    api_keys 不复制 (用占位 key, 防止 key 泄露到配置历史)
    """
    pcfg = config.get("providers", name)
    if not pcfg:
        return JSONResponse({"error": f"provider '{name}' not found"}, status_code=404)
    payload = payload or {}
    new_name = payload.get("new_name", "").strip()
    if not new_name:
        return JSONResponse({"error": "new_name required"}, status_code=400)
    if new_name == name:
        return JSONResponse({"error": "new_name must differ from original"}, status_code=400)
    if config.get("providers", new_name):
        return JSONResponse({"error": f"provider '{new_name}' already exists"}, status_code=409)
    import copy
    new_cfg = copy.deepcopy(pcfg)
    new_cfg["api_keys"] = [f"REPLACE_ME_{name}"]
    new_cfg["enabled"] = True
    from .models import normalize_base_url
    new_cfg["base_url"] = normalize_base_url(new_name, new_cfg.get("base_url", ""))
    ok = config.add_provider(new_name, new_cfg)
    if not ok:
        return JSONResponse({"error": "add_provider failed"}, status_code=500)
    registry.build()
    _refresh_async(registry, tag=f"clone:{name}->{new_name}")
    return JSONResponse({
        "ok": True,
        "name": new_name,
        "cloned_from": name,
        "config": new_cfg,
        "hint": f"api_keys 用占位 'REPLACE_ME_{name}', 真实 key 需用 UI / API 重新填入",
    })


# v3.6.0: 导出所有 provider 配置 (Phase F)
@router.get("/v1/admin/providers/export")
async def admin_providers_export(include_disabled: bool = True, include_keys: bool = False):
    """导出 provider 配置为 JSON.
    include_keys=False (默认): api_keys 替换为占位, 防止 secret 泄露
    include_keys=True: 真实 api_keys 一起导出 (高风险, 仅本机使用)
    """
    providers = config.data.get("providers", {})
    out = []
    for name, pcfg in providers.items():
        if not include_disabled and not pcfg.get("enabled", True):
            continue
        import copy
        item = {"name": name, "config": copy.deepcopy(pcfg)}
        if not include_keys:
            item["config"]["api_keys"] = [f"REDACTED_{i}" for i in range(len(pcfg.get("api_keys", [])))]
        out.append(item)
    return JSONResponse({
        "version": "3.7.0",
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "include_keys": include_keys,
        "providers": out,
    })


# v3.6.0: 导入 provider 配置 (Phase F)
@router.post("/v1/admin/providers/import")
async def admin_providers_import(payload: dict):
    """导入 provider 配置 (来自 /v1/admin/providers/export).
    payload = {"providers": [{"name": "x", "config": {...}}, ...]}
    - 已存在的 provider: 跳过 (skip), 避免覆盖
    - 新 provider: 添加
    - api_keys 是 REDACTED 占位的: 拒绝 (必须填真 key)
    - 失败的: 返回 failed 列表
    """
    items = payload.get("providers", [])
    if not isinstance(items, list) or not items:
        return JSONResponse({"error": "providers list required"}, status_code=400)
    added, skipped, failed = [], [], []
    from .models import normalize_base_url
    for it in items:
        name = it.get("name", "").strip()
        pcfg = it.get("config", {})
        if not name or not pcfg.get("base_url"):
            failed.append({"name": name or "?", "reason": "missing name or base_url"})
            continue
        keys = pcfg.get("api_keys", [])
        if any(k.startswith("REDACTED_") for k in keys):
            failed.append({"name": name, "reason": "api_keys are REDACTED placeholders, fill real keys first"})
            continue
        pcfg.setdefault("enabled", True)
        pcfg.setdefault("max_concurrent", 3)
        pcfg.setdefault("model_rules", {"mode": "all"})
        pcfg["base_url"] = normalize_base_url(name, pcfg["base_url"])
        if config.get("providers", name):
            skipped.append(name)
            continue
        ok = config.add_provider(name, pcfg)
        if ok:
            added.append(name)
        else:
            failed.append({"name": name, "reason": "add_provider returned False"})
    if added:
        registry.build()
        for name in added:
            _refresh_async(registry, tag=f"import:{name}", only=name)
    return JSONResponse({
        "ok": True,
        "added": added,
        "skipped": skipped,
        "failed": failed,
        "summary": f"added={len(added)} skipped={len(skipped)} failed={len(failed)}",
    })


@router.post("/v1/admin/providers/{name}/refresh")
async def admin_providers_refresh(name: str):
    """v3.6: 单独刷新一个 provider 的模型列表 (针对性获取)"""
    pcfg = config.get("providers", name)
    if not pcfg:
        return JSONResponse({"error": f"provider '{name}' not found"}, status_code=404)
    if not pcfg.get("enabled", True):
        return JSONResponse({"error": f"provider '{name}' is disabled"}, status_code=400)
    # 触发单 provider refresh (异步)
    _refresh_async(registry, tag=f"refresh:{name}", only=name)
    return JSONResponse({
        "ok": True,
        "name": name,
        "refreshing": True,
        "hint": f"fetching models from {pcfg.get('base_url')}",
    })


@router.put("/v1/admin/providers/{name}")
async def admin_providers_update(name: str, payload: dict):
    """更新 provider (增量覆盖字段)"""
    pcfg = payload.get("config", {})
    if not pcfg:
        return JSONResponse({"error": "config required"}, status_code=400)
    # v3.6: 如果更新了 base_url, 自动 normalize
    if "base_url" in pcfg:
        from .models import normalize_base_url
        pcfg["base_url"] = normalize_base_url(name, pcfg["base_url"])
    ok = config.update_provider(name, pcfg)
    if not ok:
        return JSONResponse(
            {"error": f"provider '{name}' not found"},
            status_code=404,
        )
    registry.build()
    _refresh_async(registry, tag=f"update:{name}")
    return JSONResponse({"ok": True, "name": name, "config": pcfg})


# ============================================================
# 自定义 Classifier (tier_bonus / custom_keywords / modality_base_score) API
# ============================================================

@router.get("/v1/admin/classifier")
async def admin_classifier_get():
    """读取当前 classifier 配置 (含兜底内置默认)"""
    from .classifier import TIER_BONUS, MODALITY_BASE_SCORE, CUSTOM_KEYWORDS_DEFAULT
    return JSONResponse({
        "configured": config.data.get("classifier") or {},
        "defaults": {
            "tier_bonus": TIER_BONUS,
            "modality_base_score": MODALITY_BASE_SCORE,
            "custom_keywords": CUSTOM_KEYWORDS_DEFAULT,
        },
    })


@router.put("/v1/admin/classifier")
async def admin_classifier_update(payload: dict):
    """更新 classifier 配置 (tier_bonus / custom_keywords / modality_base_score)

    v3.7.0: 自动备份当前 config 到 .backups/, 24h 内可恢复 (config.py:_backup 已实现)
    """
    allowed = {"tier_bonus", "custom_keywords", "modality_base_score"}
    cfg = {k: v for k, v in payload.items() if k in allowed}
    if not cfg:
        return JSONResponse(
            {"error": f"no valid keys. allowed: {sorted(allowed)}"},
            status_code=400,
        )
    # v3.7.0: 触发备份 (返回 backup_id 让 UI 显示)
    backup_path = None
    try:
        backup_path = config._backup()
    except Exception as e:
        LOG.warning("backup before classifier update failed: %s", e)
    config.update_classifier(cfg)
    # 重算所有模型 capability_score
    registry.refresh_all()
    return JSONResponse({
        "ok": True,
        "updated": list(cfg.keys()),
        "backup_id": backup_path.name if backup_path else None,  # v3.7.0
        "backup_recover_hint": "PUT /v1/admin/config/restore {backup_id}",  # v3.7.0
    })


# ============================================================
# Server / Routing 段手动修改 API
# ============================================================

@router.get("/v1/admin/server")
async def admin_server_get():
    """读取 server 段配置 (api_key 自动 REDACT)"""
    import copy
    data = copy.deepcopy(config.data.get("server") or {})
    if data.get("api_key"):
        data["api_key"] = "***"
    return JSONResponse(data)


@router.put("/v1/admin/server")
async def admin_server_update(payload: dict):
    """更新 server 段配置 (host / port / api_key). 注意: port 改动需重启服务."""
    allowed = {"host", "port", "api_key"}
    srv = {k: v for k, v in payload.items() if k in allowed}
    if not srv:
        return JSONResponse(
            {"error": f"no valid keys. allowed: {sorted(allowed)}"},
            status_code=400,
        )
    # 类型校验
    if "port" in srv:
        try:
            srv["port"] = int(srv["port"])
            if not (1 <= srv["port"] <= 65535):
                raise ValueError
        except (ValueError, TypeError):
            return JSONResponse(
                {"error": "port must be integer 1-65535"},
                status_code=400,
            )
    old_port = config.server.get("port")
    config.update_server(srv)
    new_port = config.server.get("port")
    needs_restart = ("port" in srv and old_port != new_port)
    return JSONResponse({
        "ok": True,
        "updated": list(srv.keys()),
        "restart_required": needs_restart,
        "note": "port 改动需重启 SMR 服务生效 (其它字段实时生效)" if needs_restart else None,
    })


@router.get("/v1/admin/routing")
async def admin_routing_get():
    """读取 routing 段配置"""
    return JSONResponse(config.data.get("routing") or {})


@router.put("/v1/admin/routing")
async def admin_routing_update(payload: dict):
    """更新 routing 段配置 (strategy / failover_threshold / recovery_interval / max_retry / first_token_timeout_ms / retry_backoff_ms / quality_weights)"""
    allowed = {
        "strategy", "failover_threshold", "recovery_interval",
        "max_retry", "first_token_timeout_ms", "retry_backoff_ms",
        "quality_weights",
    }
    rt = {k: v for k, v in payload.items() if k in allowed}
    if not rt:
        return JSONResponse(
            {"error": f"no valid keys. allowed: {sorted(allowed)}"},
            status_code=400,
        )
    config.update_routing(rt)
    return JSONResponse({"ok": True, "updated": list(rt.keys())})


# ============================================================
# v4: Model Penalty 管理 (admin) — 老大 09:48 拍
# ============================================================

@router.get("/v1/admin/penalty")
async def admin_penalty_get():
    """查看所有 model penalty 状态 (用于 dashboard 调试 + 复测决策)"""
    return JSONResponse(engine.get_model_penalty())


@router.post("/v1/admin/penalty/reset")
async def admin_penalty_reset(payload: dict | None = None):
    """手动清零 model penalty (强制复测恢复)

    payload = {"model": "openrouter/gpt-4o"} 或 {} (清空所有)
    """
    payload = payload or {}
    target = payload.get("model")
    result = engine.reset_model_penalty(target)
    return JSONResponse(result)


@router.post("/v1/admin/penalty/decay")
async def admin_penalty_decay(payload: dict | None = None):
    """手动触发 penalty decay (admin 强制复测恢复)

    payload = {"force": true}  # 跳过 interval 等待, 立即对所有 model 衰减
    """
    payload = payload or {}
    force = payload.get("force", False)
    if force:
        # 强制衰减 (跳过 interval 检查) — 用于 dashboard "立即复测" 按钮
        with engine._lock:
            updated = 0
            for path in list(engine._model_penalty.keys()):
                old = engine._model_penalty[path]
                new = max(0.0, old - engine.cfg.routing.get("penalty_decay_step", 0.1))
                if new <= 0.001:
                    del engine._model_penalty[path]
                    engine._model_last_failure.pop(path, None)
                else:
                    engine._model_penalty[path] = new
                updated += 1
            if updated:
                engine._persist_stats()
        return JSONResponse({"ok": True, "updated_models": updated, "forced": True})
    # 普通: 等 interval 过了才衰减
    updated = engine.decay_model_penalty()
    return JSONResponse({"ok": True, "updated_models": updated, "forced": False})


# ============================================================
# v3.1: 版本管理 (admin) — 老大 09:48 拍 C 项
# ============================================================

@router.get("/v1/admin/version")
async def admin_version_get(force_check: bool = False):
    """返回当前版本 + 最新 GitHub release + 升级建议

    v3.7.0: GitHub fetch 失败不再阻塞, 始终返回 current 版本元数据
    """
    from .version import (
        VERSION as CURRENT_VERSION, BUILD_DATE, GITHUB_REPO,
        get_cached_release, is_newer_version,
    )

    release = None
    fetch_error = None
    try:
        if force_check:
            from .version import fetch_latest_release
            release = fetch_latest_release()
        else:
            release = get_cached_release()
    except Exception as e:
        # v3.7.0: 不抛错, 只记录, UI 显示 "未配置"
        fetch_error = str(e)
        LOG.warning("version endpoint: GitHub fetch failed: %s", e)

    has_update = False
    latest_tag = None
    if release:
        latest_tag = release["tag"]
        has_update = is_newer_version(CURRENT_VERSION, latest_tag)

    return JSONResponse({
        "current": {
            "version": CURRENT_VERSION,
            "build_date": BUILD_DATE,
            "title": SMR_APP_TITLE,
            "repository": GITHUB_REPO,
        },
        "latest_release": release,
        "has_update": has_update,
        "upgrade_methods": ["git", "pip", "docker", "binary"],
        "checked_at": time.time(),
        "fetch_error": fetch_error,  # v3.7.0: 告知 UI 是否有错 (不阻塞)
    })


@router.post("/v1/admin/upgrade")
async def admin_upgrade(payload: dict | None = None):
    """生成升级命令 (不直接执行 — 需手动确认)

    payload = {"method": "git" | "pip" | "docker" | "binary",
               "target_tag": "v3.2.0"}  # 可选, 默认 latest

    返回: 升级命令 + 风险提示
    """
    payload = payload or {}
    method = payload.get("method", "git")
    target_tag = payload.get("target_tag", "latest")

    from .version import (
        fetch_latest_release, get_upgrade_command, GITHUB_REPO,
    )
    release = fetch_latest_release()
    if target_tag == "latest" and release:
        target_tag = release["tag"]

    cmd = get_upgrade_command(target_tag, repo=GITHUB_REPO, method=method)
    return JSONResponse({
        "ok": True,
        "method": method,
        "target_tag": target_tag,
        "command": cmd,
        "warning": (
            "升级会重启服务, 期间请求会失败。建议在低峰期执行, "
            "或先在 staging 环境验证。命令需要手动在终端执行 (SMR 不直接执行危险操作)。"
        ),
        "release_notes": release["body"][:500] if release else "",
    })


# ============================================================
# v3.3: Model Management API — 完整重写 (2026-06-17)
# ============================================================

@router.get("/v1/admin/model_management")
async def admin_model_management():
    """模型管理概览: 规则统计 + 发现状态 + 最近变更"""
    stats = registry.rule_engine.get_stats()
    recent = registry.rule_engine.get_diffs(limit=5)
    last = registry._prev_model_ids
    return JSONResponse({
        "stats": stats,
        "providers": {
            name: {"models": len(ids), "prev_count": len(ids)}
            for name, ids in last.items()
        },
        "recent_changes": recent,
    })


@router.get("/v1/admin/model_rules")
async def admin_model_rules_list(rule_type: str | None = None):
    """列出模型管理规则 (可按 rule_type 过滤)"""
    rules = registry.rule_engine.get_rules(rule_type=rule_type)
    return JSONResponse({"rules": rules, "total": len(rules)})


@router.post("/v1/admin/model_rules")
async def admin_model_rules_add(payload: dict):
    """创建模型管理规则

    payload = {
        "rule_type": "blacklist|whitelist|auto_black|auto_white",
        "pattern": "regex",
        "description": "optional human-readable note",
        "enabled": true
    }
    """
    rule_type = payload.get("rule_type", "")
    pattern = payload.get("pattern", "")
    if rule_type not in ("blacklist", "whitelist", "auto_black", "auto_white"):
        return JSONResponse({"error": "rule_type must be: blacklist|whitelist|auto_black|auto_white"}, status_code=400)
    if not pattern:
        return JSONResponse({"error": "pattern required (regex)"}, status_code=400)
    rule = registry.rule_engine.add_rule(
        rule_type=rule_type,
        pattern=pattern,
        description=payload.get("description", ""),
        enabled=payload.get("enabled", True),
    )
    return JSONResponse({"ok": True, "rule": rule.__dict__})


@router.delete("/v1/admin/model_rules/{rule_id}")
async def admin_model_rules_delete(rule_id: str):
    """删除规则"""
    ok = registry.rule_engine.remove_rule(rule_id)
    if not ok:
        return JSONResponse({"error": f"rule '{rule_id}' not found"}, status_code=404)
    return JSONResponse({"ok": True, "deleted": rule_id})


@router.put("/v1/admin/model_rules/{rule_id}")
async def admin_model_rules_update(rule_id: str, payload: dict):
    """更新规则 (enabled / pattern / description)"""
    rule = registry.rule_engine.update_rule(rule_id, **payload)
    if not rule:
        return JSONResponse({"error": f"rule '{rule_id}' not found"}, status_code=404)
    return JSONResponse({"ok": True, "rule": rule.__dict__})


@router.get("/v1/admin/model_discovery")
async def admin_model_discovery(provider: str | None = None, limit: int = 50):
    """模型发现历史 (按时间倒序)"""
    history = registry.rule_engine.get_history(provider=provider, limit=limit)
    return JSONResponse({"history": history, "total": len(history)})


@router.post("/v1/admin/model_discovery/trigger")
async def admin_model_discovery_trigger():
    """手动触发一次模型发现 (异步)"""
    import threading
    def _run():
        try:
            registry.refresh_all()
        except Exception:
            LOG.exception("manual discovery failed")
    th = threading.Thread(target=_run, daemon=True, name="smr-manual-discovery")
    th.start()
    return JSONResponse({"ok": True, "message": "discovery triggered in background"})


@router.get("/v1/admin/model_notify")
async def admin_model_notify(limit: int = 50):
    """模型变更通知日志"""
    log = registry.rule_engine.get_notify_log(limit=limit)
    return JSONResponse({"log": log, "total": len(log)})


@router.post("/v1/admin/model_notify/test")
async def admin_model_notify_test():
    """测试通知 (发送测试 webhook + log)"""
    ok = registry.rule_engine.record_discovery(
        provider="test",
        old_models=["gpt-4o", "claude-3"],
        new_models=["gpt-4o", "claude-3", "test-new-model"],
        all_models=[{"id": "gpt-4o"}, {"id": "claude-3"}, {"id": "test-new-model"}],
    )
    return JSONResponse({"ok": True, "diff_recorded": True, "message": "test discovery recorded (check /v1/admin/model_notify)"})




@router.get("/v1/admin/config/backups")
async def admin_config_backups_list():
    """列出所有配置备份 (按 mtime 倒序)"""
    return JSONResponse({
        "backups": config.list_backups(),
        "config_path": str(config._path),
    })


@router.post("/v1/admin/config/restore")
async def admin_config_restore(payload: dict):
    """从指定备份恢复 config.yaml

    payload = {"name": "config-20260617-150000.yaml"}
    """
    name = payload.get("name", "").strip()
    if not name:
        return JSONResponse(
            {"error": "name required (e.g. config-20260617-150000.yaml)"},
            status_code=400,
        )
    ok = config.restore_backup(name)
    if not ok:
        return JSONResponse(
            {"error": f"backup '{name}' not found or invalid"},
            status_code=404,
        )
    return JSONResponse({
        "ok": True,
        "restored_from": name,
        "note": "config reloaded. providers 需 registry.build() 重新构建 (下一版本加自动 build)",
    })


# ── v3.3: Model Management API ──────────────────────────

@router.get("/v1/admin/models/status")
async def admin_models_status():
    """模型管理模块状态: discovery + lists + auto_rules"""
    if model_manager is None:
        return JSONResponse({"error": "model_manager not initialized"}, status_code=503)
    return JSONResponse(model_manager.status())


@router.get("/v1/admin/models/changes")
async def admin_models_changes():
    """最近的模型变更 (added/removed/unchanged)"""
    if model_manager is None:
        return JSONResponse({"error": "model_manager not initialized"}, status_code=503)
    diff = model_manager.discovery.last_diff
    if diff is None:
        return JSONResponse({"message": "no changes recorded yet", "diff": None})
    return JSONResponse(diff.to_dict())


@router.get("/v1/admin/models/lists")
async def admin_models_lists():
    """获取黑白名单"""
    if model_manager is None:
        return JSONResponse({"error": "model_manager not initialized"}, status_code=503)
    return JSONResponse(model_manager.list_mgr.to_dict())


@router.put("/v1/admin/models/lists")
async def admin_models_lists_update(payload: dict):
    """更新黑白名单 (支持 patch 模式)

    payload:
    {"blacklist": {"patterns": ["^embed"]}, "whitelist": {"patterns": ["^gpt"]}}
    """
    if model_manager is None:
        return JSONResponse({"error": "model_manager not initialized"}, status_code=503)
    with model_manager.list_mgr._lock:
        bl = payload.get("blacklist", {})
        wl = payload.get("whitelist", {})
        if "patterns" in bl:
            model_manager.list_mgr._global_blacklist = bl["patterns"]
        if "patterns" in wl:
            model_manager.list_mgr._global_whitelist = wl["patterns"]
    return JSONResponse({"ok": True, "lists": model_manager.list_mgr.to_dict()})


@router.post("/v1/admin/models/lists/test")
async def admin_models_lists_test(payload: dict):
    """测试某个 model_id 是否在黑白名单内

    payload: {"model_id": "openrouter/xxx", "provider": "openrouter"}
    """
    if model_manager is None:
        return JSONResponse({"error": "model_manager not initialized"}, status_code=503)
    mid = payload.get("model_id", "")
    provider = payload.get("provider", "")
    allowed, reason = model_manager.list_mgr.check(mid, provider)
    return JSONResponse({
        "model_id": mid,
        "provider": provider,
        "allowed": allowed,
        "reason": reason,
    })


@router.post("/v1/admin/models/notify/test")
async def admin_models_notify_test():
    """测试通知 (手动触发一次 discovery)"""
    if model_manager is None:
        return JSONResponse({"error": "model_manager not initialized"}, status_code=503)
    diff = model_manager.on_refresh()
    return JSONResponse({
        "ok": True,
        "diff": diff.to_dict() if diff else None,
    })


# ============================================================
# v3.4.0: Context Bridge (上下文桥接 + 过期标记) 管理 — 老大 22:00 拍
# ============================================================

@router.get("/v1/admin/context_bridge")
async def admin_context_bridge_get():
    """查看 ContextBridge 当前配置 + 统计

    返回: {
        "config": {enabled, stale_threshold_seconds, max_history, sentinel_enabled, ...},
        "stats": {injections_total, stale_marks_total, switch_records_total, sentinels_sent_total, ...}
    }
    """
    if context_bridge is None:
        return JSONResponse({"error": "context_bridge not initialized"}, status_code=503)
    return JSONResponse({
        "config": context_bridge.get_config(),
        "stats": context_bridge.get_stats(),
    })


@router.put("/v1/admin/context_bridge")
async def admin_context_bridge_update(payload: dict | None = None):
    """热更新 ContextBridge 配置

    payload: {"enabled": bool?, "stale_threshold_seconds": int?, "max_history": int?, "sentinel_enabled": bool?, "inject_template": str?}

    注: 改 inject_template 不持久化, 重启后从 config.yaml 重新读
    """
    if context_bridge is None:
        return JSONResponse({"error": "context_bridge not initialized"}, status_code=503)
    payload = payload or {}
    context_bridge.update_config(payload)
    return JSONResponse({
        "ok": True,
        "updated_fields": list(payload.keys()),
        "config": context_bridge.get_config(),
    })


@router.post("/v1/admin/context_bridge/reset")
async def admin_context_bridge_reset():
    """清零 ContextBridge 统计 (不影响配置)"""
    if context_bridge is None:
        return JSONResponse({"error": "context_bridge not initialized"}, status_code=503)
    context_bridge.reset_stats()
    return JSONResponse({
        "ok": True,
        "stats": context_bridge.get_stats(),
    })


# ============================================================
# v3.5.0: 主动盘点 (Context Review) — 老大 22:25 拍
# ============================================================
# 触发链路: 飞书用户说"盘点上下文/重新审视/回顾上下文"
#         → mainbot 调本 endpoint (smr_request_id)
#         → 返回 SwitchRecord 聚合报告
#         → mainbot 拼成自然语言回复给用户
#
# 4 编码原则:
# - 边界: 盘点是 admin 操作, 不是每次 chat completion 都跑
# - 成本: 仅在用户主动说盘点时跑, 默认不消耗 LLM token
# - 异常: smr_request_id 未找到 → 200 + {not_found: true, hint}
# - 可观测性: reviews_total stat + e2e 测试覆盖

@router.post("/v1/admin/context_review")
async def admin_context_review(payload: dict | None = None):
    """v3.5.0 主动盘点: 拿指定 smr_request_id 的 SwitchRecord 聚合报告

    payload: {
        "smr_request_id": str,       # 必填, mainbot 发的 _smr_request_id
    }

    响应: 见 context_bridge.get_review_report docstring
    失败: 200 {ok: false, error: "...", smr_request_id: "..."}
    """
    if context_bridge is None:
        return JSONResponse({"ok": False, "error": "context_bridge not initialized"}, status_code=503)
    payload = payload or {}
    smr_request_id = payload.get("smr_request_id", "").strip()
    if not smr_request_id:
        return JSONResponse({
            "ok": False,
            "error": "smr_request_id is required",
            "hint": "mainbot 发的 chat completions body 应含 _smr_request_id (SMR 自动生成也支持, 从 response._router.smr_request_id 拿)",
        }, status_code=400)
    context_bridge.record_review()
    report = context_bridge.get_review_report(smr_request_id)
    if report is None:
        return JSONResponse({
            "ok": False,
            "smr_request_id": smr_request_id,
            "error": "not_found",
            "hint": "smr_request_id 未在 SMR 跟踪 (可能已淘汰/重启/错的 ID). 重启后所有跟踪清零.",
        }, status_code=404)
    return JSONResponse({
        "ok": True,
        "report": report,
    })


@router.get("/v1/admin/context_review/list")
async def admin_context_review_list(limit: int = 50):
    """v3.5.0: 列出当前在跟踪的 smr_request_id (给 admin UI / debug 用)

    ?limit=50 (默认)
    """
    if context_bridge is None:
        return JSONResponse({"error": "context_bridge not initialized"}, status_code=503)
    limit = max(1, min(limit, 200))
    tracked = context_bridge.list_tracked_requests(limit=limit)
    return JSONResponse({
        "count": len(tracked),
        "tracked": tracked,
    })


@router.get("/v1/admin/context_review/{smr_request_id}")
async def admin_context_review_get(smr_request_id: str):
    """v3.5.0: GET 版本盘点 (跟 POST 一样, 方便 curl 调试)"""
    if context_bridge is None:
        return JSONResponse({"ok": False, "error": "context_bridge not initialized"}, status_code=503)
    if not smr_request_id:
        return JSONResponse({"ok": False, "error": "smr_request_id is required"}, status_code=400)
    context_bridge.record_review()
    report = context_bridge.get_review_report(smr_request_id)
    if report is None:
        return JSONResponse({
            "ok": False,
            "smr_request_id": smr_request_id,
            "error": "not_found",
        }, status_code=404)
    return JSONResponse({"ok": True, "report": report})

# ============================================================
# 对外 API (per-tenant key) 管理 — v3.7.0
# ============================================================
# 老大 2026-06-18 拍: 中转 router 不对外就丧失核心功能, 需要多 key 体系
# 设计: name / key_hash (前16) / rate_limit_rpm / model_filter / enabled
# 用量: total / success / fail / tokens / last_used
# ============================================================

@router.get("/v1/admin/public-keys")
async def admin_public_keys_list():
    """列出所有对外 API key (不含原 key, 只哈希 + 元数据 + 用量)"""
    from .public_api import public_key_manager
    if not public_key_manager:
        return JSONResponse({"error": "public_key_manager not initialized"}, status_code=503)
    return JSONResponse({"keys": public_key_manager.list_keys()})


@router.post("/v1/admin/public-keys")
async def admin_public_keys_create(payload: dict):
    """创建新对外 key (返回原 key 一次, 之后只存哈希)

    body: {name: str, rate_limit_rpm?: int=60, model_filter?: [str], note?: str}
    """
    from .public_api import public_key_manager
    if not public_key_manager:
        return JSONResponse({"error": "public_key_manager not initialized"}, status_code=503)
    name = (payload.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    if not all(c.isalnum() or c in "-_" for c in name):
        return JSONResponse(
            {"error": "name must be alphanumeric / dash / underscore"},
            status_code=400,
        )
    try:
        result = public_key_manager.create_key(
            name=name,
            rate_limit_rpm=int(payload.get("rate_limit_rpm", 60)),
            model_filter=payload.get("model_filter"),
            note=payload.get("note", ""),
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    # v3.7.0: 一次性返回原 key, 提醒用户复制保存
    return JSONResponse({
        "ok": True,
        "key": result["key"],  # ⚠️ 唯一一次返回原 key!
        "key_hash": result["key_hash"],
        "name": result["name"],
        "rate_limit_rpm": result["rate_limit_rpm"],
        "model_filter": result["model_filter"],
        "note": result["note"],
        "_warning": "原 key 只返回这一次, 之后只能重新生成",
    })


@router.put("/v1/admin/public-keys/{name}")
async def admin_public_keys_update(name: str, payload: dict):
    """更新 key 元数据 (rate_limit / model_filter / enabled / note)"""
    from .public_api import public_key_manager
    if not public_key_manager:
        return JSONResponse({"error": "public_key_manager not initialized"}, status_code=503)
    try:
        meta = public_key_manager.update_key(name, **payload)
    except KeyError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "meta": meta})


@router.delete("/v1/admin/public-keys/{name}")
async def admin_public_keys_delete(name: str):
    """删除对外 key (硬删)"""
    from .public_api import public_key_manager
    if not public_key_manager:
        return JSONResponse({"error": "public_key_manager not initialized"}, status_code=503)
    ok = public_key_manager.delete_key(name)
    if not ok:
        return JSONResponse({"error": f"key '{name}' not found"}, status_code=404)
    return JSONResponse({"ok": True, "deleted": name})


@router.get("/v1/admin/public-keys/usage")
async def admin_public_keys_usage():
    """对外 API 全局用量 (per-key + 汇总)"""
    from .public_api import public_key_manager
    if not public_key_manager:
        return JSONResponse({"error": "public_key_manager not initialized"}, status_code=503)
    return JSONResponse(public_key_manager.get_all_usage())


@router.post("/v1/admin/public-keys/{name}/reset")
async def admin_public_keys_reset_usage(name: str):
    """重置某 key 的用量计数 (不改 key 本身)"""
    from .public_api import public_key_manager
    if not public_key_manager:
        return JSONResponse({"error": "public_key_manager not initialized"}, status_code=503)
    meta = public_key_manager.get_key(name)
    if not meta:
        return JSONResponse({"error": f"key '{name}' not found"}, status_code=404)
    key_hash = meta["key_hash"]
    with public_key_manager._lock:
        public_key_manager._usage[key_hash] = {
            "total_calls": 0, "success_calls": 0, "fail_calls": 0,
            "tokens": 0, "last_used": 0.0, "rate_window": [],
        }
        public_key_manager._save_async()
        public_key_manager._flush()
    return JSONResponse({"ok": True, "reset": name})
