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
    return JSONResponse({
        "routes": registry.all_routes(),
        "total": len(registry.all_routes()),
    })


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
async def admin_config_reload():
    config.load()
    registry.build()
    registry.refresh_all()
    return JSONResponse({"ok": True})


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
    pcfg["base_url"] = pcfg["base_url"].rstrip("/")

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
async def admin_providers_delete(name: str):
    """删除自定义 provider"""
    ok = config.remove_provider(name)
    if not ok:
        return JSONResponse(
            {"error": f"provider '{name}' not found"},
            status_code=404,
        )
    registry.build()
    registry.refresh_all()
    return JSONResponse({"ok": True, "name": name})


@router.put("/v1/admin/providers/{name}")
async def admin_providers_update(name: str, payload: dict):
    """更新 provider (增量覆盖字段)"""
    pcfg = payload.get("config", {})
    if not pcfg:
        return JSONResponse({"error": "config required"}, status_code=400)
    ok = config.update_provider(name, pcfg)
    if not ok:
        return JSONResponse(
            {"error": f"provider '{name}' not found"},
            status_code=404,
        )
    registry.build()
    registry.refresh_all()
    return JSONResponse({"ok": True, "name": name})


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
    """更新 classifier 配置 (tier_bonus / custom_keywords / modality_base_score)"""
    allowed = {"tier_bonus", "custom_keywords", "modality_base_score"}
    cfg = {k: v for k, v in payload.items() if k in allowed}
    if not cfg:
        return JSONResponse(
            {"error": f"no valid keys. allowed: {sorted(allowed)}"},
            status_code=400,
        )
    config.update_classifier(cfg)
    # 重算所有模型 capability_score
    registry.refresh_all()
    return JSONResponse({"ok": True, "updated": list(cfg.keys())})


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
    """返回当前版本 + 最新 GitHub release + 升级建议"""
    from .version import (
        VERSION as CURRENT_VERSION, BUILD_DATE, GITHUB_REPO,
        get_cached_release, is_newer_version,
    )

    release = None
    if force_check:
        from .version import fetch_latest_release
        release = fetch_latest_release()
    else:
        release = get_cached_release()

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