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
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .config import config
from .context_bridge import ContextBridge  # v3.4.0
from .version import VERSION as SMR_VERSION, BUILD_DATE as SMR_BUILD_DATE
from .model_health import ModelHealth, get_model_health_manager, HealthState  # v3.15.0 + v3.16.0
import json
from pathlib import Path
SMR_APP_TITLE = f"SuperModel Router v{SMR_VERSION}"

LOG = logging.getLogger("admin_api")
router = APIRouter()

registry: Any = None
engine: Any = None
app: Any = None  # v3.23.0: FastAPI app ref (for app-scoped state)
state_dir: Any = None  # v3.23.0: state directory
model_manager: Any = None
# v3.4.0: ContextBridge 单例
context_bridge: ContextBridge | None = None
# v3.22.0: 周天循环系统引用
_memory_bus: Any = None
_loop_engine: Any = None
_scheduler: Any = None
_start_time = 0


def init(app_registry, app_engine, app_model_manager, start_time, app_bridge: ContextBridge | None = None,
         memory_bus=None, loop_engine=None, scheduler=None, fastapi_app=None, app_state_dir=None):
    global registry, engine, model_manager, _start_time, context_bridge
    global _memory_bus, _loop_engine, _scheduler, app, state_dir
    registry = app_registry
    engine = app_engine
    app = fastapi_app
    state_dir = app_state_dir or "/app/state"
    model_manager = app_model_manager
    _start_time = start_time
    context_bridge = app_bridge
    _memory_bus = memory_bus
    _loop_engine = loop_engine
    _scheduler = scheduler


def _pricing_display(pricing: str) -> str:
    """UI-facing binary price label: only 免费 / 收费."""
    return "免费" if pricing in {"free", "limited_free"} else "收费"


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
    
    # v3.23.0 (Codez #14): acceptance_rate 硬闸门指标
    acceptance = _compute_acceptance_rate()
    
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
        "acceptance": acceptance,
    })


def _compute_acceptance_rate() -> dict:
    """v3.23.0 (Codez #14): 接受率指标
    
    定义:
      - calls_total: 所有路由决策次数
      - calls_accepted: 选中的 candidate 数 (链长)
      - calls_failed: 调用失败的次数
      - calls_succeeded: 调用成功的次数
      - acceptance_rate: succeeded / total (整体)
      - per_provider: 每个 provider 的成功率
    """
    try:
        # 从 engine_stats.json 读所有 provider 的 stats
        stats_path = Path(config.data.get("model_management", {}).get("state_dir", "/app/state")) / "engine_stats.json"
        if not stats_path.exists():
            return {"available": False, "reason": "engine_stats.json not found"}
        
        stats = json.loads(stats_path.read_text())
        
        total_calls = 0
        total_success = 0
        total_fail = 0
        per_provider = {}
        
        for provider_name, p_stats in stats.items():
            if not isinstance(p_stats, dict):
                continue
            tc = p_stats.get("total_calls", 0)
            ts = p_stats.get("success_calls", 0)
            tf = p_stats.get("fail_calls", 0)
            total_calls += tc
            total_success += ts
            total_fail += tf
            per_provider[provider_name] = {
                "calls": tc,
                "success": ts,
                "fail": tf,
                "rate": round(ts / tc, 4) if tc > 0 else None,
            }
        
        overall_rate = round(total_success / total_calls, 4) if total_calls > 0 else None
        
        # Codez 警告: < 50% 就是在亏本
        warning = None
        if overall_rate is not None and overall_rate < 0.5 and total_calls > 10:
            warning = f"acceptance_rate {overall_rate:.1%} < 50% threshold (Codez #14 硬闸门)"
        
        return {
            "available": True,
            "total_calls": total_calls,
            "total_success": total_success,
            "total_fail": total_fail,
            "overall_rate": overall_rate,
            "warning": warning,
            "per_provider": per_provider,
            "codez_threshold": 0.5,
        }
    except Exception as e:
        return {"available": False, "reason": str(e)}


@router.get("/v1/admin/modalities")
async def admin_modalities():
    """各模态的模型数量分布"""
    return JSONResponse(registry.get_modality_counts())


@router.get("/v1/admin/routes")
async def admin_routes():
    """v3.6: 路由列表 + 模型详情 (含 pricing_type)
    v3.8.0: 加 context_window + capability_score (从 classifier 拿)
    """
    from .classifier import classify_pricing, compute_capability_score, pricing_detail, PRICING_FREE, PRICING_LIMITED_FREE
    out = []
    for r in registry.all_routes():
        # r 格式: "provider/model_id"
        if "/" in r:
            p, mid = r.split("/", 1)
            pricing = classify_pricing(p, mid)
            price_info = pricing_detail(p, mid)
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
                "pricing": pricing, "pricing_type": pricing, "pricing_display": _pricing_display(pricing),
                "pricing_detail": price_info,
                "is_free": pricing in {PRICING_FREE, PRICING_LIMITED_FREE},
                "context_window": ctx, "score": score,
            })
        else:
            out.append({"route": r, "provider": "?", "model": r, "pricing": "unknown"})
    return JSONResponse({"routes": out, "total": len(out)})


# v3.15.0: 参数量 cache loader (启动时加载,改 cache 需重启容器)
import json as _json_size
from pathlib import Path as _Path_size

_MODEL_SIZE_CACHE_DICT: dict = {}

def _load_model_size_cache():
    """从 /app/data/model_size_cache.json (mount 自 WSL ./data/) 加载参数量 cache。
    启动时调用一次;失败返回空 dict。"""
    global _MODEL_SIZE_CACHE_DICT
    try:
        cache_path = _Path_size("/app/data/model_size_cache.json")
        if not cache_path.exists():
            print(f"WARN [model_size_cache] /app/data/model_size_cache.json 不存在")
            _MODEL_SIZE_CACHE_DICT = {}
            return
        raw = _json_size.loads(cache_path.read_text())
        _MODEL_SIZE_CACHE_DICT = {m["model_id"]: m for m in raw.get("models", [])}
        print(f"INFO [model_size_cache] 加载 {len(_MODEL_SIZE_CACHE_DICT)} 个模型参数量")
    except Exception as e:
        print(f"WARN [model_size_cache] 加载失败: {e}")
        _MODEL_SIZE_CACHE_DICT = {}

# 启动时立即加载
_load_model_size_cache()

def _model_size_lookup(model_id: str, key: str, default=None):
    """查 cache,返回 model 字段值 (默认 default)。"""
    return _MODEL_SIZE_CACHE_DICT.get(model_id, {}).get(key, default)


@router.get("/v1/admin/models")
async def admin_models(provider: str | None = None, pricing: str | None = None):
    """v3.6: 详细模型列表 (含 pricing_type, capability_score, modality)
    query: ?provider=openrouter 过滤 provider
           ?pricing=free       过滤收费类型
    """
    from .classifier import classify_pricing, pricing_detail, PRICING_FREE, PRICING_LIMITED_FREE
    out = []
    for ps in registry._providers.values():
        if provider and ps.name != provider:
            continue
        for m in ps.models:
            p = classify_pricing(ps.name, m.id)
            price_info = pricing_detail(ps.name, m.id)
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
                "pricing_type": p,
                "pricing_display": _pricing_display(p),
                "pricing_detail": price_info,
                "is_free": p in {PRICING_FREE, PRICING_LIMITED_FREE},
                "base_url": ps.base_url,
                # v3.15.0: 参数量标签 (注入自 data/model_size_cache.json)
                "size_b": _model_size_lookup(m.id, "size_b"),
                "size_class": _model_size_lookup(m.id, "size_class", default="unknown"),
                "size_source": _model_size_lookup(m.id, "source", default="none"),
                "size_confidence": _model_size_lookup(m.id, "confidence", default=0.0),
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
            "model_count": len(registry.get_model_ids(pname)),
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


# ── v3.23.0: L1 Free Resource Admin Endpoints (老大钦定重点 6/27) ──
@router.get("/v1/admin/free-models")
async def admin_free_models(modality: str | None = None):
    """列出所有 free-eligible 模型 + 实时配额状态"""
    fr = getattr(engine, "free_registry", None)
    if fr is None:
        return JSONResponse({"ok": False, "error": "FreeModelRegistry 未初始化"}, status_code=503)
    models = fr.get_all(modality=modality)
    return JSONResponse({
        "ok": True,
        "total": len(models),
        "by_tier": fr.count_by_tier(),
        "models": [
            {
                "path": m.full_path,
                "tier": m.tier,
                "state": m.state,
                "quality": round(m.quality_score, 1),
                "latency_ms": round(m.avg_latency_ms, 0),
                "daily_used": m.daily_used,
                "consecutive_429": m.consecutive_429,
                "success/fail": f"{m.success_count}/{m.fail_count}",
                "signals": m.detection_signals,
            }
            for m in models
        ],
    })


@router.post("/v1/admin/free-models/refresh")
async def admin_free_models_refresh():
    """重新扫描所有 provider, 识别 free 模型"""
    fr = getattr(engine, "free_registry", None)
    if fr is None:
        return JSONResponse({"ok": False, "error": "FreeModelRegistry 未初始化"}, status_code=503)
    provider_models = {}
    for pname, ps in registry._providers.items():
        provider_models[pname] = [m.id for m in ps.models]
    new_count = fr.refresh(models_by_provider=provider_models)
    return JSONResponse({
        "ok": True,
        "newly_identified": new_count,
        "total": fr.count(),
        "summary": fr.export_summary(),
    })


@router.post("/v1/admin/free-models/reset-quota")
async def admin_free_models_reset_quota():
    """每日配额重置 (手动, 也可 cron 自动)"""
    fr = getattr(engine, "free_registry", None)
    if fr is None:
        return JSONResponse({"ok": False, "error": "FreeModelRegistry 未初始化"}, status_code=503)
    fr.reset_daily_quota()
    return JSONResponse({"ok": True, "message": "已重置 daily quota"})


@router.get("/v1/admin/budget/estimate")
async def admin_budget_estimate(provider: str, model_id: str,
                                est_input_tokens: int = 1000,
                                est_output_tokens: int = 500):
    """单模型 cost 估算"""
    from .budget_router import CostTable
    fr = getattr(engine, "free_registry", None)
    ct = CostTable(free_paths=fr.get_all_paths() if fr else set())
    est = ct.estimate(provider, model_id, est_input_tokens, est_output_tokens)
    return JSONResponse({
        "ok": True,
        "estimate": {
            "path": est.full_path,
            "is_free": est.is_free,
            "tier": est.tier,
            "cost_per_1k_input": est.cost_per_1k_input,
            "cost_per_1k_output": est.cost_per_1k_output,
            "estimated_cost": est.estimate_cost(est_input_tokens, est_output_tokens),
            "value_score": est.value_score(est_input_tokens, est_output_tokens),
        }
    })


@router.post("/v1/admin/loop/v2-summary")
async def admin_loop_v2_summary():
    """v3.23.0 全部新组件健康度总览 (free/orchestrator/middleware/session_memory/budget)"""
    fr = getattr(engine, "free_registry", None)
    free_summary = fr.export_summary() if fr else None

    from .session_memory import SessionMemoryStore
    # 用 engine 持有的 (如果有); 否则新建
    sm = getattr(app, "_session_memory", None)
    if sm is None:
        sm = SessionMemoryStore(state_dir=state_dir)
        app._session_memory = sm

    return JSONResponse({
        "ok": True,
        "version": "3.23.0",
        "components": {
            "L1_free_models": {
                "initialized": fr is not None,
                "summary": free_summary,
            },
            "L2_budget_router": {
                "initialized": True,
                "note": "CostTable + BudgetAwareRouter 内置",
            },
            "L3_orchestrator": {
                "initialized": True,
                "note": "TaskClassifier + PlanExecutor + 3 plan builders",
            },
            "L4_middleware": {
                "initialized": True,
                "note": "ContextCompressor + ContextSlicer + PromptRefiner",
            },
            "L5_session_memory": {
                "initialized": True,
                "stats": sm.stats(),
            },
        }
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
async def admin_api_keys_list(provider: str | None = None, show_full_keys: bool = False):
    """列出所有 provider 的 API key 摘要 (脱敏指纹 + 数量).
    show_full_keys=true 返回完整 key (admin 本机场景).
    """
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
                k if show_full_keys else ((k[:8] + "..." + k[-4:]) if len(k) > 12 else "***")
                for k in keys
            ],
            "show_full_keys": show_full_keys,
            "enabled": pcfg.get("enabled", True),
        })
    return JSONResponse({"version": "3.7.0", "count": len(items), "keys": items, "show_full_keys": show_full_keys})


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
    """更新 routing 段配置 (strategy / failover_threshold / recovery_interval / max_retry / first_token_timeout_ms / retry_backoff_ms / quality_weights / group_strategy / group_weights)

    v3.9.0 (Phase H): 加 group_strategy + group_weights (4 策略轮询)
    """
    allowed = {
        "strategy", "failover_threshold", "recovery_interval",
        "max_retry", "first_token_timeout_ms", "retry_backoff_ms",
        "quality_weights",
        # v3.9.0 (Phase H): group-based 轮询
        "group_strategy", "group_weights",
    }
    # v3.9.0 (Phase H): group_strategy 校验
    if "group_strategy" in payload:
        gs = payload["group_strategy"]
        if gs not in ("flat", "round-robin-group", "group-failover", "group-weighted"):
            return JSONResponse(
                {"error": f"group_strategy must be one of: flat / round-robin-group / group-failover / group-weighted, got '{gs}'"},
                status_code=400,
            )
    # v3.9.0 (Phase H): group_weights 校验 (Dict[str, float])
    if "group_weights" in payload:
        gw = payload["group_weights"]
        if not isinstance(gw, dict):
            return JSONResponse(
                {"error": "group_weights must be Dict[str, float]"},
                status_code=400,
            )
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


# ============================================================
# v3.15.0: Model Health 管理 (老大 2026-06-24 钦定)
# - 健康度指标 5 个: consecutive_fails / rolling_success_rate / ewma_latency_ms / last_success_at / last_fail_at
# - 路由时跳过非健康模型 (降低延迟)
# - 健康度恢复检测: half-open circuit breaker (SKIP → HALF_OPEN → probe → HEALTHY/SKIP 退避)
# ============================================================

@router.get("/v1/admin/model-health")
async def admin_model_health_get(path: str | None = None):
    """查看 model 健康度

    query:
      path (可选): 指定单个 model "provider/model_id", 不传返回全部
    返回:
      {summary: {total_models, by_state}, health: {path: {...}}}
    """
    mhm = getattr(engine, "model_health", None)
    if mhm is None:
        return JSONResponse({"error": "model_health not initialized"}, status_code=503)
    if path:
        all_h = mhm.get_all_health()
        entry = all_h.get(path)
        if entry is None:
            return JSONResponse({"error": f"path '{path}' not in health records"}, status_code=404)
        return JSONResponse({"path": path, **entry, "summary": mhm.get_summary()})
    return JSONResponse({"health": mhm.get_all_health(), "summary": mhm.get_summary()})


@router.post("/v1/admin/model-health/probe/{path:path}")
async def admin_model_health_probe(path: str):
    """强制 probe 单个 model (用于 admin 主动验证恢复)

    path URL 例: /v1/admin/model-health/probe/openrouter%2Fgpt-4o
    返回: {path, probe_success, ...}
    """
    mhm = getattr(engine, "model_health", None)
    if mhm is None:
        return JSONResponse({"error": "model_health not initialized"}, status_code=503)
    result = mhm.force_probe(path)
    if "error" in result:
        return JSONResponse(result, status_code=400)
    return JSONResponse(result)


@router.post("/v1/admin/model-health/probe-all")
async def admin_model_health_probe_all():
    """触发批量 probe: 扫描所有 SKIP/HALF_OPEN 状态 model

    不阻塞: 把待 probe 列表推到 background checker, 立即返回
    """
    mhm = getattr(engine, "model_health", None)
    if mhm is None:
        return JSONResponse({"error": "model_health not initialized"}, status_code=503)
    # 触发 background scan (不阻塞, 等下个 probe_interval tick)
    return JSONResponse({
        "ok": True,
        "hint": "probe triggered, results will appear in next probe interval (30s default)",
        "summary": mhm.get_summary(),
    })


@router.post("/v1/admin/model-health/reset/{path:path}")
async def admin_model_health_reset(path: str):
    """重置单个 model 健康度 (admin 主动恢复, 用于紧急情况)

    path URL 例: /v1/admin/model-health/reset/openrouter%2Fgpt-4o
    """
    mhm = getattr(engine, "model_health", None)
    if mhm is None:
        return JSONResponse({"error": "model_health not initialized"}, status_code=503)
    with mhm._lock:
        if path not in mhm._health:
            return JSONResponse({"error": f"path '{path}' not in health records"}, status_code=404)
        mhm._health[path] = ModelHealth(path=path)
    mhm._save_async()
    return JSONResponse({"ok": True, "path": path, "state": "healthy", "reset": True})


# ============================================================
# v3.16.0: Provider 级健康度 + 自动禁用管理 (老大 2026-06-24 补)
# - 整个 provider 所有 model 健康度 SKIP 持续 ≥ 7 天 → 自动禁用 + 记录原因
# - admin API: query / re-enable / force-check-now
# ============================================================

@router.get("/v1/admin/provider-health")
async def admin_provider_health_get():
    """列出所有 provider 健康度汇总 (含 disabled metadata)

    返回: {providers: [{provider, enabled, model_states, oldest_skip_age_seconds, will_disable_in}], summary: {...}}
    """
    mhm = getattr(engine, "model_health", None)
    if mhm is None:
        return JSONResponse({"error": "model_health not initialized"}, status_code=503)
    providers = mhm.get_provider_health_summary(registry)
    # 叠加 disabled metadata (从 config 读)
    for p in providers:
        meta = config.get_provider_disabled_meta(p["provider"])
        p["disabled_at"] = meta.get("disabled_at")
        p["disabled_reason"] = meta.get("disabled_reason")
    return JSONResponse({
        "providers": providers,
        "config": {
            "provider_disable_threshold_seconds": mhm.cfg.get("provider_disable_threshold_seconds"),
            "provider_check_min_models": mhm.cfg.get("provider_check_min_models"),
            "provider_check_interval_seconds": mhm.cfg.get("provider_check_interval_seconds"),
            "provider_check_enabled": mhm.cfg.get("provider_check_enabled"),
        },
        "summary": {
            "total_providers": len(providers),
            "enabled_providers": sum(1 for p in providers if p.get("enabled")),
            "auto_disabled_providers": sum(1 for p in providers if not p.get("enabled")),
            "will_disable_soon": sum(1 for p in providers if p.get("will_disable_in", 0) > 0),
        },
    })


@router.post("/v1/admin/provider-health/re-enable/{name}")
async def admin_provider_re_enable(name: str, clear_quota: bool = True):
    """手动 re-enable provider (清 disabled metadata)

    name: provider name (URL 不需要编码, FastAPI 自动处理)
    clear_quota: 是否同时清配额相关字段 (quota_skip_until, quota_type)
                 默认 True. 续费后场景 = 一次性把 quota 字段全清 0, 重新探测.
                 只清普通 skip 状态 = 传 false (保留 quota 信息只观察).
    """
    if config.enable_provider(name):
        # 清该 provider 下所有 model 健康度 (重新探测)
        mhm = getattr(engine, "model_health", None)
        paths_to_reset: list = []
        quota_cleared = 0
        if mhm is not None:
            with mhm._lock:
                paths_to_reset = [p for p in mhm._health.keys() if p.split("/", 1)[0] == name]
                for path in paths_to_reset:
                    mh = mhm._health[path]
                    mh.state = HealthState.HEALTHY.value
                    mh.consecutive_fails = 0
                    mh.consecutive_success = 0
                    mh.skip_until = 0.0
                    mh.first_skip_at = 0.0
                    mh.cooldown_seconds = mhm.cfg["skip_initial_seconds"]
                    if clear_quota and mh.quota_skip_until > 0:
                        mh.quota_skip_until = 0.0
                        mh.quota_type = ""
                        quota_cleared += 1
            mhm._save_async()
        return JSONResponse({
            "ok": True,
            "name": name,
            "enabled": True,
            "models_reset": len(paths_to_reset),
            "quota_cleared": quota_cleared,
        })
    return JSONResponse({"error": f"provider '{name}' not found"}, status_code=404)


@router.get("/v1/admin/quota/status")
async def admin_quota_status():
    """查询所有 model 的配额状态

    返回: {
      summary: {total_quota_models, by_type: {daily: N, monthly: N, ...}},
      quota_models: [{
        path, quota_type, quota_skip_until, quota_skip_at_iso,
        remaining_seconds, provider_disabled
      }]
    }
    """
    mhm = getattr(engine, "model_health", None)
    if mhm is None:
        return JSONResponse({"error": "model_health not initialized"}, status_code=503)

    now = time.time()
    quota_models = []
    by_type: dict[str, int] = {}

    with mhm._lock:
        for path, mh in mhm._health.items():
            if mh.quota_skip_until <= 0:
                continue
            quota_type = mh.quota_type or "unknown"
            by_type[quota_type] = by_type.get(quota_type, 0) + 1
            remaining = max(0, mh.quota_skip_until - now)
            provider_name = path.split("/", 1)[0]
            provider_disabled = False
            try:
                providers = getattr(config, "providers", None) or {}
                pcfg = providers.get(provider_name, {}) if providers else {}
                provider_disabled = pcfg.get("enabled", True) is False
            except Exception:
                pass
            quota_models.append({
                "path": path,
                "provider": provider_name,
                "model_id": path.split("/", 1)[1] if "/" in path else "",
                "quota_type": quota_type,
                "quota_skip_until": mh.quota_skip_until,
                "quota_skip_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mh.quota_skip_until)),
                "remaining_seconds": int(remaining),
                "remaining_human": _format_remaining(remaining),
                "provider_disabled": provider_disabled,
                "last_probe_error": mh.last_probe_error or "",
            })

    # 按 remaining_seconds 升序 (最近到期的在前)
    quota_models.sort(key=lambda x: x["remaining_seconds"])

    return JSONResponse({
        "summary": {
            "total_quota_models": len(quota_models),
            "by_type": by_type,
        },
        "quota_models": quota_models,
    })


@router.post("/v1/admin/quota/recover/{path:path}")
async def admin_quota_recover(path: str):
    """手动清掉某个 model 的配额 skip 状态 (续费后场景)

    path: provider/model_id 路径 (URL 自动处理 /)
    """
    mhm = getattr(engine, "model_health", None)
    if mhm is None:
        return JSONResponse({"error": "model_health not initialized"}, status_code=503)
    with mhm._lock:
        mh = mhm._health.get(path)
        if mh is None:
            return JSONResponse({"error": f"model '{path}' not found"}, status_code=404)
        old_quota_type = mh.quota_type
        old_skip_until = mh.quota_skip_until
        mh.quota_skip_until = 0.0
        mh.quota_type = ""
        # 同时清普通 skip + 重置 cooldown, 让 model 立即可路由
        mh.skip_until = 0.0
        mh.first_skip_at = 0.0
        mh.cooldown_seconds = mhm.cfg["skip_initial_seconds"]
        mh.consecutive_fails = 0
        mh.state = HealthState.HEALTHY.value
    mhm._save_async()
    LOG.info("admin_quota_recover: %s quota cleared (was type=%s, skip_until=%.0f)",
             path, old_quota_type, old_skip_until)
    return JSONResponse({
        "ok": True,
        "path": path,
        "old_quota_type": old_quota_type,
        "old_skip_until": old_skip_until,
    })


def _format_remaining(seconds: float) -> str:
    """把秒数格式化为人类可读 (30d / 7h / 45m)"""
    if seconds <= 0:
        return "0s"
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes and not days:
        parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "<1m"


@router.post("/v1/admin/provider-health/check-now")
async def admin_provider_check_now():
    """强制立即跑一次 provider 自动禁用扫描 (不阻塞, 等下个 tick 也行)

    返回: {candidates: [{provider, reason, duration_seconds, skip_model_count}]}
    """
    mhm = getattr(engine, "model_health", None)
    if mhm is None:
        return JSONResponse({"error": "model_health not initialized"}, status_code=503)
    candidates = mhm.check_provider_disable_candidates(registry)
    # 立即禁用 (如果 enabled)
    disabled = []
    for c in candidates:
        if config.disable_provider(c["provider"], c["reason"]):
            disabled.append(c["provider"])
    return JSONResponse({
        "candidates": candidates,
        "auto_disabled_now": disabled,
        "summary": f"{len(candidates)} candidates, {len(disabled)} auto-disabled",
    })


# ============================================================
# v3.17.0: Model Alias (统一路由名称) 管理 — 老大 6/24 钦定
# "API 调用统一的模型名, SMR 内部路由决定实际模型"
# 默认 alias: model-router / router / auto / cheap / fast / best
# ============================================================

@router.get("/v1/admin/model-aliases")
async def admin_model_aliases_get():
    """列出所有 model aliases (默认值 + 用户自定义)

    返回: {aliases: {name: {strategy, description, ...}}, default: bool}
    """
    aliases = config.get_model_aliases()
    out = {}
    for name, cfg in aliases.items():
        if isinstance(cfg, dict):
            out[name] = cfg
    return JSONResponse({"aliases": out, "count": len(out)})


@router.put("/v1/admin/model-aliases/{name}")
async def admin_model_aliases_set(name: str, payload: dict):
    """设置/修改一个 model alias (admin UI 用)

    payload: {strategy, modality?, min_capability_score?, exclude_providers?, prefer_low_latency?, max_latency_ms?}
    """
    if config.set_model_alias(name, payload):
        return JSONResponse({"ok": True, "name": name, "alias": payload})
    return JSONResponse({"error": "set failed"}, status_code=500)


@router.delete("/v1/admin/model-aliases/{name}")
async def admin_model_aliases_delete(name: str):
    """删除一个 model alias (恢复默认值)"""
    with config._lock:
        user_aliases = config._data.get("model_aliases", {}) or {}
        if name in user_aliases:
            del user_aliases[name]
            config._save_yaml()
            config._notify_change()
            return JSONResponse({"ok": True, "name": name, "deleted": True})
    return JSONResponse({"error": f"alias '{name}' not in user config"}, status_code=404)


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


# ============================================================
# v3.9.0 (Phase G): 按 model 分组的用量端点
# 设计: 复用 PublicKeyManager.get_usage_by_model (按 count 降序)
# 用量按 model 分组统计 (来自 record_usage 的 model_name 参数)
# ============================================================

@router.get("/v1/admin/public-keys/{name}/usage-by-model")
async def admin_public_keys_usage_by_model(name: str):
    """按 model 分组的用量统计 (v3.9.0 Phase G)

    返回: {name, total_calls, by_model: {model: count, ...}, last_used}
    """
    from .public_api import public_key_manager
    if not public_key_manager:
        return JSONResponse({"error": "public_key_manager not initialized"}, status_code=503)
    meta = public_key_manager.get_key(name)
    if not meta:
        return JSONResponse({"error": f"key '{name}' not found"}, status_code=404)
    key_hash = meta.get("key_hash", "")
    by_model = public_key_manager.get_usage_by_model(key_hash)
    # 拿该 key 总用量
    with public_key_manager._lock:
        u = public_key_manager._usage.get(key_hash, {})
        total_calls = u.get("total_calls", 0)
        last_used = u.get("last_used", 0)
    return JSONResponse({
        "name": name,
        "key_hash": key_hash,
        "total_calls": total_calls,
        "by_model": by_model,
        "last_used": last_used,
    })


# ============================================================
# v3.9.0: Model Groups 管理 (老大 16:55 拍)
# 设计: name / patterns(List[regex str]) / description / enabled
# 跨 provider 解析: set_known_models 注入 registry model 列表
# ============================================================

@router.get("/v1/admin/model-groups")
async def admin_model_groups_list():
    """列出所有模型分组 (按 name 排序)"""
    from .model_groups import get_model_group_manager
    mgm = get_model_group_manager()
    if not mgm:
        return JSONResponse({"error": "model_group_manager not initialized"}, status_code=503)
    return JSONResponse({"groups": mgm.list_groups()})


@router.post("/v1/admin/model-groups")
async def admin_model_groups_create(payload: dict):
    """创建模型分组

    body: {name: str, patterns: [regex_str], description?: str, enabled?: bool=true}
    """
    from .model_groups import get_model_group_manager
    mgm = get_model_group_manager()
    if not mgm:
        return JSONResponse({"error": "model_group_manager not initialized"}, status_code=503)
    name = (payload.get("name") or "").strip()
    patterns = payload.get("patterns", [])
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    if not isinstance(patterns, list) or not patterns:
        return JSONResponse({"error": "patterns must be non-empty list"}, status_code=400)
    try:
        group = mgm.create_group(
            name=name,
            patterns=patterns,
            description=payload.get("description", ""),
            enabled=payload.get("enabled", True),
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    group_dict = group.to_dict()
    return JSONResponse({"ok": True, "group": group_dict, "model_count": group_dict.get("model_count", 0)})


@router.get("/v1/admin/model-groups/stats")
async def admin_model_groups_stats():
    """分组统计: 跨 provider 分布 + 总览"""
    from .model_groups import get_model_group_manager
    mgm = get_model_group_manager()
    if not mgm:
        return JSONResponse({"error": "model_group_manager not initialized"}, status_code=503)
    return JSONResponse(mgm.get_stats())


@router.get("/v1/admin/model-groups/{name}")
async def admin_model_groups_get(name: str):
    """获取单个分组详情 (含 resolve 后的 model 列表)"""
    from .model_groups import get_model_group_manager
    mgm = get_model_group_manager()
    if not mgm:
        return JSONResponse({"error": "model_group_manager not initialized"}, status_code=503)
    g = mgm.get_group(name)
    if not g:
        return JSONResponse({"error": f"group '{name}' not found"}, status_code=404)
    resolved = mgm.resolve_group(name)
    if isinstance(g, dict):
        g = {**g, "model_count": len(resolved), "resolved_sample": resolved[:5]}
    return JSONResponse({"group": g, "resolved_models": resolved, "count": len(resolved)})


@router.put("/v1/admin/model-groups/{name}")
async def admin_model_groups_update(name: str, payload: dict):
    """更新分组 (不允许改 name)"""
    from .model_groups import get_model_group_manager
    mgm = get_model_group_manager()
    if not mgm:
        return JSONResponse({"error": "model_group_manager not initialized"}, status_code=503)
    try:
        g = mgm.update_group(name, **payload)
    except KeyError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "group": g})


@router.delete("/v1/admin/model-groups/{name}")
async def admin_model_groups_delete(name: str):
    """删除分组 (硬删)"""
    from .model_groups import get_model_group_manager
    mgm = get_model_group_manager()
    if not mgm:
        return JSONResponse({"error": "model_group_manager not initialized"}, status_code=503)
    ok = mgm.delete_group(name)
    if not ok:
        return JSONResponse({"error": f"group '{name}' not found"}, status_code=404)
    return JSONResponse({"ok": True, "deleted": name})


@router.get("/v1/admin/model-groups/{name}/resolve")
async def admin_model_groups_resolve(name: str):
    """解析 group 实际匹配的 model 列表 (跨 provider 实时拉)"""
    from .model_groups import get_model_group_manager
    mgm = get_model_group_manager()
    if not mgm:
        return JSONResponse({"error": "model_group_manager not initialized"}, status_code=503)
    if name not in mgm._groups:
        return JSONResponse({"error": f"group '{name}' not found"}, status_code=404)
    resolved = mgm.resolve_group(name)
    return JSONResponse({"name": name, "resolved_models": resolved, "count": len(resolved)})


# ============================================================
# v3.10.0 (Phase J + K): 模型筛选引擎 + 场景化向导
# 设计:
# - GET /v1/admin/models/filter: 多维度筛选 (provider/context/quality/speed/reasoning/modality/tags)
# - POST /v1/admin/model-groups/from-filter: 把 filter 结果自动创建 group + 可选 API key
# - GET /v1/admin/model-groups/wizard/presets: 列出 13 个预设场景 + 当前匹配数
# - POST /v1/admin/model-groups/from-wizard: preset → filter → 创建 group
# ============================================================

@router.get("/v1/admin/models/filter")
async def admin_models_filter(
    providers: str = "",          # 逗号分隔 "openrouter,newapi"
    context_min: int = 0,
    context_max: int = 0,
    quality_min: float = 0,
    speed_min: float = 0,
    reasoning_min: float = 0,
    modality: str = "",
    tags_any: str = "",           # 逗号分隔 "vision,coding"
    tags_all: str = "",
    exclude_tags: str = "",
    limit: int = 200,
):
    """v3.10.0 (Phase J): 多维度筛选模型

    Query params:
    - providers: 逗号分隔, 空 = 全部
    - context_min/max: 上下文范围 (0 = 不限)
    - quality_min/speed_min/reasoning_min: 评分下限 (0 = 不限)
    - modality: 子串匹配 (text/multimodal/image-gen)
    - tags_any: OR (含任一)
    - tags_all: AND (必须全含)
    - exclude_tags: 排除含任一
    - limit: 返回数量上限 (默认 200)

    返回: {models: [{path, ...metadata}, ...], total, filter: {...}}
    """
    from .model_filter import ModelFilter, apply_filter, model_to_dict
    f = ModelFilter(
        providers=[p.strip() for p in providers.split(",") if p.strip()] or None,
        context_min=context_min or None,
        context_max=context_max or None,
        quality_min=quality_min or None,
        speed_min=speed_min or None,
        reasoning_min=reasoning_min or None,
        modality=modality or None,
        tags_any=[t.strip() for t in tags_any.split(",") if t.strip()] or None,
        tags_all=[t.strip() for t in tags_all.split(",") if t.strip()] or None,
        exclude_tags=[t.strip() for t in exclude_tags.split(",") if t.strip()] or None,
    )
    all_models = registry.get_models()  # List[ModelInfo]
    matched = apply_filter(f, all_models)
    models_dict = [model_to_dict(m) for m in matched[:limit]]
    return JSONResponse({
        "models": models_dict,
        "total": len(matched),
        "returned": len(models_dict),
        "filter": f.to_dict(),
    })


@router.post("/v1/admin/model-groups/from-filter")
async def admin_model_groups_from_filter(payload: dict):
    """v3.10.0 (Phase J): 把 filter 匹配的 model 自动创建 group + 可选 API key

    body: {
      name: str,                    # group name (必填)
      filter: {...},                # ModelFilter dict
      patterns?: [str],             # 自定义 regex patterns (不传则自动从匹配 model 推断)
      description?: str,
      strategy?: str,               # round-robin-group / flat / group-failover / group-weighted
      create_api_key?: bool=true,   # 是否生成 API key
      api_key_name?: str,           # API key name (默认 = group name + "-key")
      api_key_rate_limit_rpm?: int=60,
    }

    返回: {ok, group: {...}, resolved_count, api_key?: {key, key_hash, name}}
    """
    from .model_groups import get_model_group_manager
    from .model_filter import ModelFilter, apply_filter, model_to_dict

    mgm = get_model_group_manager()
    if not mgm:
        return JSONResponse({"error": "model_group_manager not initialized"}, status_code=503)

    name = (payload.get("name") or "").strip()
    dry_run = bool(payload.get("dry_run"))
    if not name and not dry_run:
        return JSONResponse({"error": "name required"}, status_code=400)
    if name and not dry_run and name in mgm._groups:
        return JSONResponse({"error": f"group '{name}' already exists"}, status_code=409)

    filter_data = payload.get("filter") or {}
    f = ModelFilter.from_dict(filter_data)
    all_models = registry.get_models()
    matched = apply_filter(f, all_models)

    # v3.25.0 dry_run: 早返回, 不实际创建 group
    if dry_run:
        return JSONResponse({
            "ok": True,
            "dry_run": True,
            "resolved_count": len(matched),
            "resolved_models": [{"model_id": m.id, "provider": getattr(m, "provider", "")} for m in matched],
            "filter": f.to_dict(),
        })

    if not matched:
        return JSONResponse({
            "error": "no models matched filter",
            "filter": f.to_dict(),
            "hint": "放宽条件 (降低 quality_min / context_min) 或先用 GET /v1/admin/models/filter 试",
        }, status_code=400)

    # patterns 推断: 从匹配 model id 提公共前缀 regex
    patterns = payload.get("patterns")
    if not patterns:
        import re as _re_for_filter
        # 自动: 每个匹配 model 1 个 regex
        patterns = [f".*{_re_for_filter.escape(m.id)}.*" for m in matched]
        # 去重
        patterns = list(dict.fromkeys(patterns))

    # 创建 group
    try:
        group = mgm.create_group(
            name=name,
            patterns=patterns,
            description=payload.get("description", f"Auto-created from filter (matched {len(matched)} models)"),
            enabled=True,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    # 写 strategy 到 config
    strategy = payload.get("strategy")
    if strategy:
        if strategy not in ("flat", "round-robin-group", "group-failover", "group-weighted"):
            mgm.delete_group(name)
            return JSONResponse({
                "error": f"strategy must be: flat / round-robin-group / group-failover / group-weighted, got '{strategy}'",
            }, status_code=400)
        config.update_routing({"group_strategy": strategy})

    result = {
        "ok": True,
        "group": {
            "name": group.name,
            "patterns": group.patterns,
            "description": group.description,
            "enabled": group.enabled,
            "model_count": len(matched),
            "auto_generated_patterns": len(patterns),
        },
        "resolved_count": len(matched),
        "matched_samples": [m.id for m in matched[:5]],
        "filter": f.to_dict(),
    }

    # 可选生成 API key
    if payload.get("create_api_key", True):
        from .public_api import public_key_manager
        if public_key_manager is None:
            mgm.delete_group(name)
            return JSONResponse({"error": "public_key_manager not initialized"}, status_code=503)
        key_name = (payload.get("api_key_name") or f"{name}-key").strip()
        if not all(c.isalnum() or c in "-_" for c in key_name):
            mgm.delete_group(name)
            return JSONResponse({"error": "api_key_name must be alphanumeric/dash/underscore"}, status_code=400)
        try:
            key_result = public_key_manager.create_key(
                name=key_name,
                rate_limit_rpm=int(payload.get("api_key_rate_limit_rpm", 60)),
                model_filter=[f"group:{name}"],  # 自动绑定到 group
                note=f"Auto-generated from filter (group={name}, matched={len(matched)})",
            )
        except ValueError as e:
            mgm.delete_group(name)
            return JSONResponse({"error": f"key creation failed: {e}"}, status_code=409)
        result["api_key"] = {
            "key": key_result["key"],
            "key_hash": key_result["key_hash"],
            "name": key_result["name"],
            "model_filter": key_result["model_filter"],
            "_warning": "原 key 只返回这一次, 之后只能重新生成",
        }

    return JSONResponse(result)


@router.get("/v1/admin/model-groups/wizard/presets")
async def admin_wizard_presets():
    """v3.10.0 (Phase K): 列出 13 个预设场景 + 当前每个匹配的 model 数

    返回: {presets: [{id, name, icon, description, filter, current_match_count, sample_models}], total: 13}
    """
    from .group_wizard import list_presets, preset_to_filter
    from .model_filter import apply_filter, model_to_dict

    all_models = registry.get_models()
    presets_out = []
    for p in list_presets():
        f = preset_to_filter(p["id"])
        matched = apply_filter(f, all_models)
        presets_out.append({
            "id": p["id"],
            "name": p["name"],
            "icon": p["icon"],
            "description": p["description"],
            "filter": p["filter"],
            "current_match_count": len(matched),
            "sample_models": [m.id for m in matched[:3]],
        })
    return JSONResponse({
        "presets": presets_out,
        "total": len(presets_out),
        "total_models_available": len(all_models),
    })


@router.post("/v1/admin/model-groups/from-wizard")
async def admin_model_groups_from_wizard(payload: dict):
    """v3.10.0 (Phase K): preset → filter → 创建 group (Phase J 端点的快捷版)

    body: {
      preset: str,                  # preset id (必填)
      name?: str,                   # group name (默认 = preset + "-{timestamp}")
      strategy?: str,               # round-robin-group (默认) / flat / group-failover / group-weighted
      create_api_key?: bool=true,
      api_key_name?: str,
      api_key_rate_limit_rpm?: int=60,
    }

    返回: 同 from-filter
    """
    from .group_wizard import get_preset, preset_to_filter

    preset_id = (payload.get("preset") or "").strip()
    try:
        preset = get_preset(preset_id)
    except KeyError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    name = (payload.get("name") or f"{preset_id}-wizard").strip()
    strategy = payload.get("strategy") or "round-robin-group"

    # 复用 from-filter 端点逻辑
    from .model_filter import ModelFilter
    filter_dict = preset["filter"]
    f = ModelFilter.from_dict(filter_dict)

    # 直接复用 from-filter 的实现 (避免重复逻辑)
    # 内部调用 create_group + 可选 key
    from .model_groups import get_model_group_manager
    from .model_filter import apply_filter
    mgm = get_model_group_manager()
    if not mgm:
        return JSONResponse({"error": "model_group_manager not initialized"}, status_code=503)
    if name in mgm._groups:
        return JSONResponse({"error": f"group '{name}' already exists"}, status_code=409)

    all_models = registry.get_models()
    matched = apply_filter(f, all_models)
    if not matched:
        return JSONResponse({
            "error": f"preset '{preset_id}' matched 0 models",
            "filter": f.to_dict(),
        }, status_code=400)

    # v3.25.0 dry_run: 早返回, 不实际创建 group
    if payload.get("dry_run"):
        return JSONResponse({
            "ok": True,
            "dry_run": True,
            "preset": preset_id,
            "preset_name": preset["name"],
            "resolved_count": len(matched),
            "resolved_models": [{"model_id": m.id, "provider": getattr(m, "provider", "")} for m in matched],
            "filter": f.to_dict(),
        })

    import re as _re
    patterns = [_re.escape(m.id) for m in matched]
    patterns = [f".*{p}.*" for p in patterns]
    patterns = list(dict.fromkeys(patterns))

    try:
        group = mgm.create_group(
            name=name,
            patterns=patterns,
            description=f"Wizard preset: {preset['name']} (matched {len(matched)})",
            enabled=True,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    if strategy not in ("flat", "round-robin-group", "group-failover", "group-weighted"):
        mgm.delete_group(name)
        return JSONResponse({
            "error": f"strategy must be: flat / round-robin-group / group-failover / group-weighted, got '{strategy}'",
        }, status_code=400)
    config.update_routing({"group_strategy": strategy})

    result = {
        "ok": True,
        "preset": preset_id,
        "preset_name": preset["name"],
        "group": {
            "name": group.name,
            "patterns": len(patterns),
            "description": group.description,
            "model_count": len(matched),
        },
        "resolved_count": len(matched),
        "matched_samples": [m.id for m in matched[:5]],
        "strategy": strategy,
        "filter": f.to_dict(),
    }

    if payload.get("create_api_key", True):
        from .public_api import public_key_manager
        if public_key_manager is None:
            mgm.delete_group(name)
            return JSONResponse({"error": "public_key_manager not initialized"}, status_code=503)
        key_name = (payload.get("api_key_name") or f"{name}-key").strip()
        if not all(c.isalnum() or c in "-_" for c in key_name):
            mgm.delete_group(name)
            return JSONResponse({"error": "api_key_name must be alphanumeric/dash/underscore"}, status_code=400)
        try:
            key_result = public_key_manager.create_key(
                name=key_name,
                rate_limit_rpm=int(payload.get("api_key_rate_limit_rpm", 60)),
                model_filter=[f"group:{name}"],
                note=f"Auto-generated by wizard preset '{preset_id}'",
            )
        except ValueError as e:
            mgm.delete_group(name)
            return JSONResponse({"error": f"key creation failed: {e}"}, status_code=409)
        result["api_key"] = {
            "key": key_result["key"],
            "key_hash": key_result["key_hash"],
            "name": key_result["name"],
            "model_filter": key_result["model_filter"],
            "_warning": "原 key 只返回这一次, 之后只能重新生成",
        }

    return JSONResponse(result)


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


# ── v3.22.0: Admin API — LoopEngine 状态与控制 ──────────────────────────────
@router.get("/v1/admin/loop/status")
async def admin_loop_status():
    """LoopEngine 当前状态: 运行 ticks / 内存 / 层统计"""
    le: Any = _loop_engine
    if le is None:
        return JSONResponse({"initialized": False}, status_code=503)
    mb: Any = _memory_bus
    layer_stats: dict = {}
    if mb is not None:
        try:
            layer_stats = {
                layer: len(mb.get(layer)) for layer in ("objective", "warning", "cognitive")
                if hasattr(mb, "get")
            }
        except Exception as e:
            layer_stats = {"error": str(e)}
    status: dict = {
        "initialized": True,
        "version": getattr(le, "version", "v3.22.0"),
        "tick_interval": getattr(le, "tick_interval", 300),
        "state_dir": getattr(le, "_state_dir", "."),
        "tick_count": getattr(le, "_tick_count", 0),
        "last_tick_at": getattr(le, "_last_tick_at", None),
        "last_repair_at": getattr(le, "_last_repair_at", None),
        "history_count": len(getattr(le, "_history", [])),
        "layers": layer_stats,
    }
    return JSONResponse(status)


@router.post("/v1/admin/loop/trigger")
async def admin_loop_trigger():
    """手动触发一次 LoopEngine tick (不影响定时节奏)"""
    le: Any = _loop_engine
    if le is None:
        return JSONResponse({"error": "LoopEngine not initialized"}, status_code=503)
    result: dict = await le.trigger()
    return JSONResponse({"ok": True, "result": result})


# ── v3.22.0: Admin API — 完整系统状态 (loop + memory + scheduler) ──────────
@router.get("/v1/admin/system/full")
async def admin_system_full(status_code=200):
    """完整系统健康快照: Engine + Health + LoopEngine + MemoryBus"""
    ok: list = []
    warn: list = []

    # Engine core
    from .version import load_version_meta
    try:
        vm = load_version_meta()
        ok.append(f"SMR v{vm['version']} ({vm['build_date']})")
    except Exception:
        ok.append("SMR (version unknown)")

    # Model health
    try:
        mhm = engine.model_health if engine else None
        if mhm:
            hs = mhm.get_summary()
            total: int = hs.get("total", 0)
            if total > 0:
                healthy_frac: float = hs.get("healthy", 0) / max(total, 1)
                ok.append(f"health: {hs.get('healthy', 0)}/{total} healthy ({healthy_frac * 100:.0f}%)")
                if hs.get("missing", 0) >= 1:
                    warn.append(f"missing: {hs.get('missing', 0)}")
            skip: int = hs.get("skip", 0) + hs.get("cooldown", 0)
            if skip > 0:
                warn.append(f"on-skip: {skip}")
            if hs.get("fallback", 0) >= 1:
                warn.append(f"on-fallback: {hs.get('fallback', 0)}")
    except Exception as e:
        warn.append(f"health error: {str(e)[:60]}")

    # Loop engine
    le: Any = _loop_engine
    if le is None:
        warn.append("loop_engine: not initialized")
    else:
        ok.append(f"loop_engine: v{le.version} (interval={le.tick_interval}s, ticks={le._tick_count})")

    # Memory bus
    mb: Any = _memory_bus
    if mb is None:
        warn.append("memory_bus: not initialized")
    else:
        counts: dict = {layer: len(mb[layer]) for layer in ("objective", "warning", "cognitive")}
        ok.append(f"memory_bus: {counts['objective']}+{counts['warning']}+{counts['cognitive']} entries")

    # Scheduler
    sc: Any = _scheduler
    if sc is None:
        warn.append("scheduler: not initialized")
    else:
        ok.append(f"scheduler: v3.22.0")

    version_str: str = ok[0] if ok else "SMR"
    n_warn: int = len(warn)
    summary: str = version_str + " · " + "  ".join(ok[1:])
    if n_warn > 0:
        summary += " · ⚠" + "  ".join(warn)
    else:
        summary += " · ALL_OK"

    response: dict = {
        "status": "healthy" if not warn else ("degraded" if n_warn < 3 else "warning"),
        "summary": summary,
        "ok": ok,
        "warn": warn,
        "loop_engine": {
            "initialized": le is not None,
            "tick_count": getattr(le, "_tick_count", 0) if le else 0,
            "last_tick_at": getattr(le, "_last_tick_at", None) if le else None,
            "last_repair_at": getattr(le, "_last_repair_at", None) if le else None,
            "history": (le._history if isinstance(getattr(le, "_history", None), list) else []) if le else [],
            "layers": {layer: len(getattr(mb, layer, [])) for layer in ("objective", "warning", "cognitive")} if mb else {},
        },
        "scheduler": {"initialized": sc is not None},
    }
    return JSONResponse(response)


# ── v3.29: Token 成本 API ────────────────────────────────────

@router.get("/v1/admin/cost")
async def admin_cost_lookup(model: str = "", provider: str = ""):
    """查询模型 token 成本

    Query params:
      - model: 模型名 (支持模糊匹配)
      - provider: provider 名 (可选)
    """
    from .pricing import get_pricing
    pricing = get_pricing()

    if model:
        in_cost, out_cost, is_free = pricing.lookup(model, provider)
        cost_1k = pricing.calculate_cost(model, provider, 1000, 1000) * 1000
        return JSONResponse({
            "model": model,
            "provider": provider or "auto",
            "input_cost_per_1m": in_cost,
            "output_cost_per_1m": out_cost,
            "is_free": is_free,
            "estimated_1k_input_1k_output_usd": round(cost_1k, 8),
        })

    # 返回已知模型清单
    all_models = pricing._models if pricing else {}
    return JSONResponse({
        "total_models": len(all_models),
        "models": {k: {"input": v.get("input",0), "output": v.get("output",0)} for k,v in list(all_models.items())[:50]},
        "providers": {k: {"default_input": v.get("default_input",0), "default_output": v.get("default_output",0)}
                      for k,v in (pricing._providers if pricing else {}).items()},
    })

