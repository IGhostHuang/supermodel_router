"""
supermodel_router/fusion_presets.py — Fusion 默认组合预设 (v4.3.0)

设计对齐 group_wizard.py 的 PRESETS 模式:
- 每种融合算子 (vote / expert / pipeline / refine / n1) 各一个"开箱即用"默认组合
- 模型不写死 ID, 而是用 selection rule (ModelFilter + 数量) 在注册时动态挑选
- 一键 seed: 把默认 plan 全部注册进 FusionRouter 并固化到 config.yaml

R42 兼容: 老 fusion plan CRUD 不动; seed 出来的 plan 跟手写 plan 等价.
R43 新增: default_n1 (N+1 Robust Fusion) —— 主模型 + N 路 fan-out + 多层 fallback + 健康替换.
"""
import logging
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("fusion_presets")


# ------------------------------------------------------------------
# 动态选型规则: 每个 preset 声明"要什么样的模型 + 要几个",
# 注册时用 registry + model_filter 实时挑选, 避免写死已下线的模型 ID.
# rule = {"filter": {ModelFilter dict}, "count": N, "prefer_free": bool}
# ------------------------------------------------------------------

FUSION_PRESETS: Dict[str, Dict[str, Any]] = {
    # 1) VOTE — 并行采样投票, 同一 prompt 发给 N 个模型, judge 选最佳
    "default_vote": {
        "name": "🗳️ 并行投票 (Vote)",
        "icon": "🗳️",
        "operator": "vote",
        "description": "同一问题发给 3 个高质量模型并行采样, 由 judge 选最佳答案",
        "select": {
            "members": {"filter": {"quality_min": 70}, "count": 3, "prefer_free": True},
            "judge": {"filter": {"quality_min": 80, "reasoning_min": 70}, "count": 1, "prefer_free": True},
        },
        "build": {"type": "vote", "strategy": "best_pick", "max_tokens": 1024},
    },
    # 2) EXPERT — MoE 思路, 按意图 tag 路由到不同专家
    "default_expert": {
        "name": "🧭 专家路由 (Expert)",
        "icon": "🧭",
        "operator": "expert",
        "description": "按问题意图 (代码/数学/总结/通用) 动态选择对口专家模型",
        "select": {
            "code": {"filter": {"tags_any": ["coding"], "quality_min": 70}, "count": 1, "prefer_free": True},
            "math": {"filter": {"reasoning_min": 75}, "count": 1, "prefer_free": True},
            "summary": {"filter": {"speed_min": 70, "quality_min": 70}, "count": 1, "prefer_free": True},
            "default": {"filter": {"quality_min": 75}, "count": 1, "prefer_free": True},
        },
        "build": {"type": "expert"},
    },
    # 3) PIPELINE — 角色流水线: 规划 → 起草 → 精修
    "default_pipeline": {
        "name": "🔗 角色流水线 (Pipeline)",
        "icon": "🔗",
        "operator": "pipeline",
        "description": "规划(强推理) → 起草(高质量) → 精修(高质量 judge) 三段式流水线",
        "select": {
            "planner": {"filter": {"reasoning_min": 75}, "count": 1, "prefer_free": True},
            "drafter": {"filter": {"quality_min": 75}, "count": 1, "prefer_free": True},
            "refiner": {"filter": {"quality_min": 80}, "count": 1, "prefer_free": True},
        },
        "build": {"type": "pipeline"},
    },
    # 4) REFINE — 单模型草稿 + judge 二次精修 (以 pipeline 承载, 保证可独立执行)
    "default_refine": {
        "name": "✨ 二次精修 (Refine)",
        "icon": "✨",
        "operator": "refine",
        "description": "先用高质量模型出草稿, 再由 judge 模型润色改进 (draft → refine)",
        "select": {
            "drafter": {"filter": {"quality_min": 75}, "count": 1, "prefer_free": True},
            "judge": {"filter": {"quality_min": 80}, "count": 1, "prefer_free": True},
        },
        "build": {"type": "refine",
                  "instruction": "Refine and improve the draft answer for clarity, correctness and completeness. Return only the improved answer."},
    },
    # 5) N+1 ROBUST FUSION — 主模型统御 + N 路并行 + 多层 fallback + 健康替换 (R43 新增)
    #    流程: Refine-Task → Fan-out → Refine-Answers → Final-Fuse
    #    每个角色 (primary / fanout[i] / refiner) 都附 2 个独立 fallback
    "default_n1": {
        "name": "🛡️ N+1 稳健融合 (N+1 Robust)",
        "icon": "🛡️",
        "operator": "n1_fusion",
        "description": "主模型先提炼任务, N 路并行采样, 主模型二次融合. 每路 fan-out 与主模型各自挂 2 个 fallback, 自动替换失败率高模型. 一步到位的混合专家.",
        "select": {
            # primary 主模型: 高质量 + 超大上下文, 优先 ≥128k
            "primary": {"filter": {"quality_min": 80, "context_min": 128000}, "count": 1, "prefer_free": True},
            # primary fallback 主模型备用: 不强求 128k 但要 ≥64k
            "primary_fb": {"filter": {"quality_min": 78, "context_min": 64000}, "count": 2, "prefer_free": True},
            # fan-out 通用: 3 路, 高质量 + 64k 上下文
            "fanout_general": {"filter": {"quality_min": 75, "context_min": 64000}, "count": 3, "prefer_free": True},
            # fan-out 代码: 1 路 (覆盖代码类问题)
            "fanout_code": {"filter": {"tags_any": ["coding"], "quality_min": 70}, "count": 1, "prefer_free": True},
            # fan-out 推理: 1 路 (覆盖数学/逻辑)
            "fanout_reasoning": {"filter": {"reasoning_min": 75}, "count": 1, "prefer_free": True},
            # refiner (融合节点) 备用: 1 个 fallback
            "refiner_fb": {"filter": {"quality_min": 75, "context_min": 32000}, "count": 2, "prefer_free": True},
        },
        "build": {
            "type": "n1_fusion",
            "fanout_count": 3,
            "min_success_count": 2,
            "max_retries_per_leaf": 3,
            "context_policy": {"min_context_floor": 65536, "headroom_ratio": 0.8},
            "fallback_pool": {"use_dynamic_discovery": True, "min_health_tier": "yellow"},
            "stream_policy": {"late_merge": True},
        },
    },
}


def list_presets() -> List[Dict[str, Any]]:
    """列出所有 fusion preset (供 UI 渲染卡片, 不含解析后的模型)."""
    return [
        {"id": pid, "name": p["name"], "icon": p["icon"],
         "operator": p["operator"], "description": p["description"]}
        for pid, p in FUSION_PRESETS.items()
    ]


def get_preset(preset_id: str) -> Dict[str, Any]:
    if preset_id not in FUSION_PRESETS:
        raise KeyError(f"fusion preset '{preset_id}' not found. available: {list(FUSION_PRESETS.keys())}")
    return FUSION_PRESETS[preset_id]


# ------------------------------------------------------------------
# 动态选型: 用 live registry + model_filter 把 select rule 解析成真实 model path
# ------------------------------------------------------------------

def _get_registry():
    """Get the live model registry. admin_api.registry is set at app startup
    and is the canonical source; fall back to engine.registry if present."""
    try:
        from . import admin_api
        if getattr(admin_api, "registry", None) is not None:
            return admin_api.registry
    except Exception:
        pass
    try:
        from . import engine as _eng_mod
        eng = getattr(_eng_mod, "engine", None)
        if eng is not None and getattr(eng, "registry", None) is not None:
            return eng.registry
    except Exception:
        pass
    return None


def _model_context(m) -> int:
    """Best-effort extract model context window (tokens). Default 32k."""
    for attr in ("context_window", "max_context", "context_length"):
        v = getattr(m, attr, None)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    try:
        d = m.to_dict() if hasattr(m, "to_dict") else {}
    except Exception:
        d = {}
    for k in ("context_window", "max_context", "context_length"):
        v = d.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    return 32768


def _pick_models(rule: Dict[str, Any], exclude: Optional[List[str]] = None) -> List[str]:
    """按单条 rule 从 registry 挑 model, 返回 provider/id path 列表.

    优雅降级 (free 模型普遍无 quality/speed 评分, 硬阈值会全空):
      1. 严格 filter → 若命中 == 0
      2. 去掉分数类阈值 (quality/speed/reasoning), 只保留 modality/tags/context → 若仍 0
      3. 全量模型 (免费优先 + capability 降序) 兜底
    prefer_free=True 时免费模型排前面.
    exclude: 已被其它角色选走的 path, 优先避开以保证多样性 (不足时才复用).
    """
    from .model_filter import ModelFilter, apply_filter, model_to_dict
    registry = _get_registry()
    if registry is None:
        LOG.warning("_pick_models: registry unavailable")
        return []
    try:
        all_models = registry.get_models()
    except Exception as e:  # pragma: no cover
        LOG.warning("_pick_models: get_models failed: %s", e)
        return []

    filt_dict = dict(rule.get("filter") or {})
    count = int(rule.get("count", 1))
    prefer_free = bool(rule.get("prefer_free", True))
    exclude = set(exclude or [])

    def _apply(fd: Dict[str, Any]):
        try:
            return apply_filter(ModelFilter.from_dict(fd), all_models)
        except Exception as e:
            LOG.warning("_pick_models: filter failed (%s): %s", fd, e)
            return []

    matched = _apply(filt_dict) if filt_dict else list(all_models)
    if not matched:
        # 降级 2: 去掉分数阈值
        relaxed = {k: v for k, v in filt_dict.items()
                   if k not in ("quality_min", "speed_min", "reasoning_min",
                                "capability_min", "size_min", "size_max")}
        matched = _apply(relaxed) if relaxed else []
    if not matched:
        # 降级 3: 全量兜底
        matched = list(all_models)

    dicts = [model_to_dict(m) for m in matched]

    def _is_free(d: Dict[str, Any]) -> bool:
        p = str(d.get("pricing") or d.get("pricing_type") or "").lower()
        return p in ("free", "limited_free") or ":free" in str(d.get("id", ""))

    dicts.sort(key=lambda d: (
        0 if (prefer_free and _is_free(d)) else 1,
        -(d.get("capability_score") or 0),
        -(d.get("quality_score") or 0),
    ))
    ordered = [d["path"] for d in dicts if d.get("path")]
    # 多样性: 先取未被排除的, 不足再用被排除的补齐
    fresh = [p for p in ordered if p not in exclude]
    result = fresh[:count]
    if len(result) < count:
        result += [p for p in ordered if p not in result][:count - len(result)]
    return result


def resolve_plan(preset_id: str, plan_id: Optional[str] = None) -> Dict[str, Any]:
    """把 preset 的 select rule 解析成一个可直接注册的 fusion plan dict.

    产出的 plan 结构严格对齐 fusion_router.py 各算子的 params schema:
      vote       → {type:vote, model_ids:[...], judge_model, strategy, max_tokens}
      expert     → {type:expert, params:{experts:{tag:model_id}}}
      pipeline   → {type:pipeline, params:{steps:[{type,params}]}}
      refine     → {type:refine, params:{judge_model, instruction}}
      n1_fusion  → {type:n1_fusion, params:{primary, fallbacks_primary, fanout[], fallbacks_fanout{}, refiner, fallbacks_refiner, ...}}
    """
    preset = get_preset(preset_id)
    op = preset["operator"]
    sel = preset.get("select", {})
    build = dict(preset.get("build", {}))
    pid = plan_id or preset_id

    plan: Dict[str, Any]

    if op == "vote":
        members = _pick_models(sel.get("members", {}))
        judge = _pick_models(sel.get("judge", {}), exclude=members)
        plan = {
            "plan_id": pid, "type": "vote",
            "model_ids": members,
            "strategy": build.get("strategy", "best_pick"),
            "max_tokens": build.get("max_tokens", 1024),
        }
        if judge:
            plan["judge_model"] = judge[0]

    elif op == "expert":
        experts: Dict[str, str] = {}
        used: List[str] = []
        for tag, rule in sel.items():
            picked = _pick_models(rule, exclude=used)
            if picked:
                experts[tag] = picked[0]
                used.append(picked[0])
        plan = {"plan_id": pid, "type": "expert", "params": {"experts": experts}}

    elif op == "pipeline":
        used: List[str] = []
        planner = _pick_models(sel.get("planner", {}), exclude=used)
        used += planner
        drafter = _pick_models(sel.get("drafter", {}), exclude=used)
        used += drafter
        refiner = _pick_models(sel.get("refiner", {}), exclude=used)
        steps: List[Dict[str, Any]] = []
        # 规划 (expert 单模型) → 起草 (expert 单模型) → 精修 (refine judge)
        if planner:
            steps.append({"type": "expert", "params": {"experts": {"default": planner[0]}}})
        if drafter:
            steps.append({"type": "expert", "params": {"experts": {"default": drafter[0]}}})
        if refiner:
            steps.append({"type": "refine", "params": {"judge_model": refiner[0]}})
        plan = {"plan_id": pid, "type": "pipeline", "params": {"steps": steps}}

    elif op == "refine":
        # refine 算子需要上游草稿, 单独执行会报错, 故用 pipeline 承载: draft(expert) → refine(judge)
        drafter = _pick_models(sel.get("drafter", {}))
        judge = _pick_models(sel.get("judge", {}), exclude=drafter)
        steps: List[Dict[str, Any]] = []
        if drafter:
            steps.append({"type": "expert", "params": {"experts": {"default": drafter[0]}}})
        refine_params: Dict[str, Any] = {"instruction": build.get("instruction", "")}
        if judge:
            refine_params["judge_model"] = judge[0]
        steps.append({"type": "refine", "params": refine_params})
        plan = {"plan_id": pid, "type": "pipeline", "params": {"steps": steps}}

    elif op == "n1_fusion":
        # N+1 robust fusion: primary + fanout[] + refiner, 每个独立 fallback
        used: List[str] = []

        # Primary 主模型 (1 个)
        primary = _pick_models(sel.get("primary", {}))
        used += primary

        # Primary fallback (2 个, 与 primary 不同 provider)
        primary_fb = _pick_models(sel.get("primary_fb", {}), exclude=used)

        # Fan-out: 取 fanout_general 全集, 优先按 prefer_free 排好
        general = _pick_models(sel.get("fanout_general", {}), exclude=used)
        used += general
        code = _pick_models(sel.get("fanout_code", {}), exclude=used)
        reasoning = _pick_models(sel.get("fanout_reasoning", {}), exclude=used)
        # 合并去重
        fanout_pool: List[str] = list(dict.fromkeys(general + code + reasoning))

        # 每路 fanout 的 fallback: 从剩余未选 + refiner_fb 池里补
        fb_pool = _pick_models(sel.get("refiner_fb", {}), exclude=used)

        fanout_count = int(build.get("fanout_count", 3))
        fanout = fanout_pool[:fanout_count]
        # 如果 fanout 不够 fanout_count 个, 用 fb_pool 补齐
        if len(fanout) < fanout_count and fb_pool:
            for m in fb_pool:
                if m not in fanout:
                    fanout.append(m)
                    if len(fanout) >= fanout_count:
                        break

        # 给每路 fanout 配 1 个 fallback (从 fb_pool 里不重复分配)
        fanout_fallbacks: Dict[str, str] = {}
        used_fb = set(primary) | set(fanout) | set(primary_fb)
        fb_iter = iter(m for m in fb_pool + primary_fb if m not in used_fb)
        for fw in fanout:
            try:
                fb = next(fb_iter)
                fanout_fallbacks[fw] = fb
                used_fb.add(fb)
            except StopIteration:
                break

        # Refiner (融合节点): 用 primary 当主 refiner, fallback 用 primary_fb
        refiner = list(primary)  # 与 primary 复用, 因为 fusion 阶段就是主模型主导
        refiner_fb = list(primary_fb)

        plan = {
            "plan_id": pid,
            "type": "n1_fusion",
            "params": {
                "primary": primary[0] if primary else None,
                "primary_fallbacks": primary_fb,
                "fanout": fanout,
                "fanout_fallbacks": fanout_fallbacks,
                "refiner": refiner[0] if refiner else None,
                "refiner_fallbacks": refiner_fb,
                "fanout_count": fanout_count,
                "min_success_count": int(build.get("min_success_count", 2)),
                "max_retries_per_leaf": int(build.get("max_retries_per_leaf", 3)),
                "context_policy": build.get("context_policy", {}),
                "fallback_pool": build.get("fallback_pool", {}),
                "stream_policy": build.get("stream_policy", {}),
            },
        }

    else:
        raise ValueError(f"unknown fusion operator '{op}'")

    return plan


def seed_all(persist: bool = True) -> Dict[str, Any]:
    """一键初始化: 解析全部默认 preset → 注册进 FusionRouter → 固化 config.yaml.

    返回 {seeded:[...], skipped:[...], errors:{...}}.
    已存在同名 plan 默认跳过 (不覆盖用户手改).
    """
    from .fusion_router import get_fusion_router, save_plans_to_config
    fr = get_fusion_router()
    if fr is None:
        return {"error": "FusionRouter not initialized"}

    seeded, skipped, errors = [], [], {}
    for pid in FUSION_PRESETS:
        try:
            if fr.has_plan(pid):
                skipped.append(pid)
                continue
            plan = resolve_plan(pid)
            fr.register(pid, plan)
            seeded.append(pid)
        except Exception as e:  # pragma: no cover
            LOG.exception("seed_all: failed on %s", pid)
            errors[pid] = repr(e)

    if persist and seeded:
        save_plans_to_config()
    return {"seeded": seeded, "skipped": skipped, "errors": errors,
            "total_plans": len(fr.list_plans())}
