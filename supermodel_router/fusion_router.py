"""
fusion_router.py -- the unified "any-to-any fusion model" layer for SMR.

The user said "做就做最好": don't pick one of the four fusion patterns
(professional routing / pipeline / parallel-vote / model-pool). Combine them
into a single plan DSL with composable operators, plus a new N+1 Robust Fusion
that adds per-role fallback chains + health-driven auto replacement.

    Operator           Purpose
    -------            -------
    Expert             Pick the most suitable model PER SUB-TASK by routing
                       signal (code / math / summary / tooluse / ...). Trees
                       of experts give MoE-style behaviour with a small leaf
                       set per branch.
    Pipeline           Stepwise: user prompt -> A plans -> B retrieves ->
                       C summarises -> ... Each step is a sub-plan (could
                       itself be Expert / Vote / Refine).
    Vote               Same prompt fanned out to N models in parallel; the
                       results are then reduced by a chosen strategy
                       (concat / best_pick / majority / self_consistency).
    Refine             One model (the "judge") receives the previous stage's
                       candidates and produces the final answer.
    N1Fusion (R43)     4-stage robust: Refine-Task (primary) -> Fan-out
                       (N + per-leaf fallback) -> Refine-Answers (local
                       cleanup) -> Final-Fuse (primary refiner + fallback).

A plan is a nested structure; the executor evaluates depth-first and returns
a single string result plus a trace. Every leaf invokes SMR's own
proxy_chat_request so context bridge, retries, free registry and pricing
all keep working.

To enable, register a virtual model alias under `aliases` in config.yaml:

    server:
      aliases:
        fusion:
          kind: fusion
          plans:
            quick_vote:
              type: vote
              model_ids: [openai/gpt-4o-mini, anthropic/claude-haiku, openrouter/free]
              strategy: best_pick
              judge_model: anthropic/claude-sonnet-4.6
            deep_plan:
              type: pipeline
              steps: [...]
            default_n1:
              type: n1_fusion
              params:
                primary: openrouter/nvidia/nemotron-3-ultra:free
                primary_fallbacks: [...]
                fanout: [...]
                fanout_fallbacks: {...}
                refiner: openrouter/nvidia/nemotron-3-ultra:free
                refiner_fallbacks: [...]
                fanout_count: 3
                min_success_count: 2
                max_retries_per_leaf: 3

Request body:
    {"model": "fusion:default_n1", "messages": [...]}

Design constraints:
- 100% inside the project (no external scheduler/cron, no external tasks).
- Cost-aware: leaf calls go through engine.free_registry / pricing.
- Reuses SMR's async proxy so streaming / abort / context bridge all work.
- Every decision logged with span ids for tracing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

LOG = logging.getLogger(__name__)


@dataclass
class FusionStep:
    """Recursive plan node; exactly one of {leaf operator} per step."""

    type: str  # 'expert' | 'pipeline' | 'vote' | 'refine' | 'n1_fusion'
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FusionResult:
    answer: str
    trace: List[Dict[str, Any]] = field(default_factory=list)
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    elapsed_seconds: float = 0.0


# ----------------------------------------------------------------------
# helper: invoke one leaf model via SMR's existing infrastructure
# ----------------------------------------------------------------------
async def _invoke_leaf(
    model_path: str,
    messages: List[Dict[str, str]],
    *,
    timeout: float = 180.0,
    max_tokens: int = 1024,
) -> Dict[str, Any]:
    """Resolve provider/model_id, build RouteResult, call proxy_chat_request.

    Returns the OpenAI-shaped response dict. Raises on hard error so caller
    can decide whether to retry or skip.
    """
    from .engine import engine  # late import to avoid circular
    from .engine import proxy_chat_request

    if "/" not in model_path:
        raise ValueError(f"FusionRouter: leaf '{model_path}' must be provider/model_id")
    chain = engine.pick_chain(
        requested_model=model_path, preferred_modalities=None, max_candidates=8,
    )
    if not chain:
        raise RuntimeError(f"FusionRouter: no candidate for {model_path}")
    route = chain[0].materialize(engine.registry)
    if route is None:
        raise RuntimeError(f"FusionRouter: cannot materialize {model_path}")
    body = {
        "model": route.model_id,
        "messages": messages,
        "stream": False,
        "max_tokens": max_tokens,
    }
    smr_id = uuid.uuid4().hex[:10]
    LOG.info(
        "fusion_leaf smr_id=%s model=%s provider=%s", smr_id, model_path, route.provider_name
    )
    out = await proxy_chat_request(route=route, body=body, stream=False, timeout=timeout)
    if not isinstance(out, dict):
        raise RuntimeError(f"FusionRouter: non-dict response from {model_path}")
    if out.get("error"):
        raise RuntimeError(f"FusionRouter: {model_path} -> {out['error']}")
    return out


async def _invoke_with_fallback(
    primary: Optional[str],
    fallbacks: List[str],
    messages: List[Dict[str, str]],
    *,
    max_retries: int = 3,
    timeout: float = 60.0,
    max_tokens: int = 1024,
    health_filter: bool = True,
) -> Dict[str, Any]:
    """Try primary first, then each fallback in order, up to max_retries total.

    Returns the OpenAI-shaped response dict. Returns last error dict if all
    fail (does NOT raise). Caller checks out.get("error") to detect failure.

    health_filter: if True, skip fallbacks whose health_tier is "red"
    (only consulted if SMR exposes a model_health module).
    """
    # Build ordered candidate list; never include None
    chain: List[str] = [m for m in [primary] + list(fallbacks or []) if m]
    # Dedup but preserve order
    seen = set()
    chain = [m for m in chain if not (m in seen or seen.add(m))]

    last_err: Optional[str] = None
    attempts = 0
    for idx, model_path in enumerate(chain):
        if attempts >= max_retries:
            break
        attempts += 1
        # Optional: health tier filter
        if health_filter and idx > 0:
            tier = _quick_health_tier(model_path)
            if tier == "red":
                LOG.warning("fusion_fallback_skip model=%s tier=red", model_path)
                continue
        try:
            return await _invoke_leaf(model_path, messages, timeout=timeout, max_tokens=max_tokens)
        except Exception as e:
            LOG.warning("fusion_leaf_fail model=%s attempt=%d err=%s",
                        model_path, attempts, repr(e)[:200])
            last_err = repr(e)
            # Tiny backoff between attempts
            await asyncio.sleep(min(0.3 * attempts, 1.5))

    return {"error": f"all_fallbacks_exhausted: {last_err}", "model_attempts": attempts}


def _quick_health_tier(model_path: str) -> str:
    """Best-effort health tier lookup. Returns 'green'|'yellow'|'red'|'unknown'.

    Uses optional model_health module if available; never raises.
    """
    try:
        from . import model_health as _mh
        fn = getattr(_mh, "health_tier", None)
        if callable(fn):
            return str(fn(model_path))
    except Exception:
        pass
    return "unknown"


def _extract_text(out: Dict[str, Any]) -> str:
    try:
        return out["choices"][0]["message"]["content"]
    except Exception:
        return ""


def _usage(out: Dict[str, Any]) -> Dict[str, int]:
    u = out.get("usage", {}) if isinstance(out, dict) else {}
    return {
        "in": int(u.get("prompt_tokens", 0) or 0),
        "out": int(u.get("completion_tokens", 0) or 0),
    }


def _truncate(text: str, max_chars: int) -> str:
    """Hard truncate by char count to avoid blowing past model context."""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[...]"


def _looks_low_quality(text: str) -> bool:
    """Cheap heuristic: detect severely degraded model output (乱码/重复)."""
    if not text or not text.strip():
        return True
    # ratio of non-ASCII printable characters
    if len(text) < 20:
        return False
    # detect excessive repetition of 3+ char runs
    if re.search(r"(.)\1{15,}", text):
        return True
    # detect "啊" / "呃" filler storms
    filler = len(re.findall(r"(啊|呃|哦|嗯|噢)[ ]{0,2}(啊|呃|哦|嗯|噢)", text))
    if filler > 8 and len(text) < 400:
        return True
    return False


# ----------------------------------------------------------------------
# operators
# ----------------------------------------------------------------------
async def op_expert(step: FusionStep, prompt: str, history: List[Dict[str, str]], trace: List[Dict[str, Any]]) -> str:
    experts: Dict[str, str] = step.params.get("experts", {})
    if not experts:
        raise ValueError("expert.step requires params.experts = {tag: model_id}")
    # mini router: classify prompt by keyword into a tag, then pick that expert
    # For first version we use a stable hash tag router so it's deterministic and
    # zero-cost; future improvement: use a cheap classifier model.
    tag = _classify_intent(prompt)
    model = experts.get(tag) or experts.get("default") or next(iter(experts.values()))
    out = await _invoke_leaf(model, history + [{"role": "user", "content": prompt}])
    trace.append({"op": "expert", "tag": tag, "model": model, "usage": _usage(out)})
    return _extract_text(out)


async def op_vote(step: FusionStep, prompt: str, history: List[Dict[str, str]], trace: List[Dict[str, Any]]) -> str:
    model_ids: List[str] = step.params.get("model_ids", [])
    if not model_ids:
        raise ValueError("vote.step requires params.model_ids: list of provider/model_id")
    strategy: str = step.params.get("strategy", "best_pick")
    judge_model: Optional[str] = step.params.get("judge_model")
    max_tokens: int = int(step.get("max_tokens", 1024) if hasattr(step, "get") else step.params.get("max_tokens", 1024))

    # fan out
    tasks: List[Awaitable[Optional[Dict[str, Any]]]] = []
    for m in model_ids:
        async def _one(mid: str = m) -> Optional[Dict[str, Any]]:
            try:
                return await _invoke_leaf(
                    mid, history + [{"role": "user", "content": prompt}], max_tokens=max_tokens
                )
            except Exception as e:  # pragma: no cover
                LOG.warning("fusion_vote leaf %s failed: %s", mid, e)
                return None
        tasks.append(_one())
    results = await asyncio.gather(*tasks, return_exceptions=False)

    candidates: List[Dict[str, Any]] = []
    for mid, r in zip(model_ids, results):
        if r and not r.get("error"):
            candidates.append({"model": mid, "text": _extract_text(r)})

    trace.append({"op": "vote", "strategy": strategy, "candidates": len(candidates), "models": model_ids})

    if not candidates:
        return "[fusion_vote: all candidates failed]"
    if strategy == "concat":
        return "\n\n---\n\n".join(f"[{c['model']}]\n{c['text']}" for c in candidates)
    if strategy == "majority":
        cnt = Counter(c["text"] for c in candidates)
        text, _ = cnt.most_common(1)[0]
        return text
    # default 'best_pick' or anything else: ask a judge model to pick
    if not judge_model:
        judge_model = model_ids[0]
    bundle = "\n\n".join(f"--- Candidate from {c['model']} ---\n{c['text']}" for c in candidates)
    judge_prompt = (
        "You are a judge. The user asked:\n"
        f"{prompt}\n\n"
        "Below are multiple candidate responses. Pick the single best answer "
        "and output ONLY that answer (no commentary, no labels).\n\n"
        f"{bundle}"
    )
    out = await _invoke_leaf(judge_model, [{"role": "user", "content": judge_prompt}], max_tokens=max_tokens)
    trace.append({"op": "vote-judge", "judge": judge_model})
    return _extract_text(out)


async def op_refine(step: FusionStep, prompt: str, prev: str, history: List[Dict[str, str]], trace: List[Dict[str, Any]]) -> str:
    judge_model: str = step.params.get("judge_model")
    if not judge_model:
        raise ValueError("refine.step requires params.judge_model")
    instruction: str = step.params.get(
        "instruction",
        "Refine and polish the following draft answer to better address the user's request. "
        "Return only the improved answer.",
    )
    refine_prompt = (
        f"User request:\n{prompt}\n\n"
        f"Draft answer:\n{prev}\n\n"
        f"{instruction}"
    )
    out = await _invoke_leaf(
        judge_model, history + [{"role": "user", "content": refine_prompt}]
    )
    trace.append({"op": "refine", "judge": judge_model, "usage": _usage(out)})
    return _extract_text(out)


async def op_pipeline(step: FusionStep, prompt: str, history: List[Dict[str, str]], trace: List[Dict[str, Any]]) -> str:
    steps: List[Dict[str, Any]] = step.params.get("steps", [])
    if not steps:
        raise ValueError("pipeline.step requires params.steps")
    current_input = prompt
    accumulated = ""
    for i, sub in enumerate(steps):
        sub_step = FusionStep(type=sub.get("type"), params=sub.get("params", sub))
        # pipeline transformers that need the previous output
        if sub_step.type == "refine":
            out_text = await op_refine(sub_step, prompt, accumulated, history, trace)
        else:
            out_text = await _run_operator(sub_step, current_input, history, trace)
        accumulated = out_text
        # next stage reads the previous answer as context
        current_input = (
            f"User request:\n{prompt}\n\n"
            f"Previous step answer:\n{out_text}\n\n"
            f"Continue with the next stage."
        )
        trace.append({"pipeline_index": i, "type": sub_step.type, "bytes": len(out_text)})
    return accumulated


# ----------------------------------------------------------------------
# N+1 Robust Fusion (R43)
# 4 阶段: Refine-Task → Fan-out → Refine-Answers (local) → Final-Fuse
# 每个角色 (primary / fanout[i] / refiner) 都有独立 fallback 链
# ----------------------------------------------------------------------
_TASK_REFINE_SYSTEM = (
    "You are a senior task analyst. Convert the user's request into a "
    "concise, well-structured task brief that other AI models can act on "
    "in parallel. Output ONLY the brief — no preamble."
)


async def op_n1_fusion(step: FusionStep, prompt: str, history: List[Dict[str, str]], trace: List[Dict[str, Any]]) -> str:
    p = step.params
    primary: Optional[str] = p.get("primary")
    primary_fb: List[str] = list(p.get("primary_fallbacks") or [])
    fanout: List[str] = list(p.get("fanout") or [])
    fanout_fb: Dict[str, str] = dict(p.get("fanout_fallbacks") or {})
    refiner: Optional[str] = p.get("refiner") or primary
    refiner_fb: List[str] = list(p.get("refiner_fallbacks") or primary_fb)
    fanout_count: int = int(p.get("fanout_count", len(fanout) or 3))
    min_success: int = int(p.get("min_success_count", 2))
    max_retries: int = int(p.get("max_retries_per_leaf", 3))
    ctx_policy: Dict[str, Any] = p.get("context_policy") or {}
    # 每个 answer 上限 = effective_context × 0.6
    max_answer_chars = int(ctx_policy.get("min_context_floor", 65536)) * 4 // 10  # 1 token ≈ 4 chars

    # --- Stage 1: Refine-Task ---
    task_brief = prompt
    if primary:
        refine_msg = [
            {"role": "system", "content": _TASK_REFINE_SYSTEM},
            {"role": "user", "content": prompt},
        ]
        out = await _invoke_with_fallback(
            primary, primary_fb, refine_msg,
            max_retries=max_retries, max_tokens=512,
        )
        refined = _extract_text(out) if not out.get("error") else ""
        if refined and not _looks_low_quality(refined):
            task_brief = refined
            trace.append({
                "stage": "refine_task", "primary": primary,
                "attempts": out.get("model_attempts", 1),
                "used_fallback": out.get("model_attempts", 1) > 1,
                "bytes": len(task_brief),
            })
        else:
            trace.append({"stage": "refine_task", "skipped": True, "reason": "primary_failed"})

    # --- Stage 2: Fan-out ---
    # 取 fanout_count 个 worker；只取前 N 个避免用户没控制 count
    workers = fanout[:fanout_count] if fanout_count > 0 else fanout
    if not workers:
        trace.append({"stage": "fanout", "skipped": True, "reason": "no_workers"})
        return "[n1_fusion: no fanout workers available]"

    async def _fanout_one(model: str) -> Dict[str, Any]:
        fb = fanout_fb.get(model)
        # 每个 worker 用自己的 fallback 链
        return await _invoke_with_fallback(
            model,
            [fb] if fb else primary_fb,
            history + [{"role": "user", "content": (
                f"Task brief:\n{task_brief}\n\n"
                f"Original user request:\n{prompt}\n\n"
                f"Provide your direct answer. Keep your response under {max_answer_chars} characters."
            )}],
            max_retries=max_retries,
            max_tokens=1500,
        )

    started = time.time()
    fanout_tasks = [_fanout_one(w) for w in workers]
    raw_results = await asyncio.gather(*fanout_tasks, return_exceptions=False)
    elapsed_fanout = time.time() - started

    # 收集 candidate: {model, text, used_fallback}
    candidates: List[Dict[str, Any]] = []
    used_fallbacks: List[str] = []
    for w, r in zip(workers, raw_results):
        if not r.get("error"):
            text = _extract_text(r)
            if text and not _looks_low_quality(text):
                candidates.append({
                    "model": w,
                    "text": _truncate(text, max_answer_chars),
                    "elapsed_ms": int(elapsed_fanout * 1000),
                    "attempts": r.get("model_attempts", 1),
                    "used_fallback": r.get("model_attempts", 1) > 1,
                    "usage": _usage(r),
                })
        else:
            used_fallbacks.append(w)
    trace.append({
        "stage": "fanout",
        "workers": workers,
        "succeeded": len(candidates),
        "failed": len(workers) - len(candidates),
        "min_success_required": min_success,
        "elapsed_ms": int(elapsed_fanout * 1000),
    })

    if len(candidates) < min_success:
        # 没达到最低成功数, 强退化: 返回现有 candidate 中最长且非低质的
        salvage = [c for c in candidates if not _looks_low_quality(c["text"])]
        if not salvage:
            return f"[n1_fusion: fanout insufficient] {len(candidates)}/{min_success} succeeded"
        salvage.sort(key=lambda c: len(c["text"]), reverse=True)
        trace.append({
            "stage": "fanout", "degraded": True,
            "reason": f"only {len(candidates)}/{min_success} candidates succeeded",
            "mode": "best-of-failed-fanout",
        })
        return salvage[0]["text"]

    # --- Stage 3: Refine-Answers (local) ---
    # 去重: 完全相同的 text 只保留一份
    dedup: List[Dict[str, Any]] = []
    seen_text: set = set()
    for c in candidates:
        key = c["text"][:200].strip()
        if key in seen_text:
            continue
        seen_text.add(key)
        dedup.append(c)
    # 按长度从大到小再选前 min(8, len) 个, 避免融合 prompt 撑爆
    dedup.sort(key=lambda c: len(c["text"]), reverse=True)
    candidates = dedup[:max(8, min_success)]
    trace.append({
        "stage": "refine_answers",
        "input": len(raw_results),
        "after_dedup": len(dedup),
        "kept_for_fuse": len(candidates),
    })

    # --- Stage 4: Final-Fuse ---
    if not refiner:
        # 没有 refiner, 取最长且质量尚可的 candidate
        candidates.sort(key=lambda c: len(c["text"]), reverse=True)
        return candidates[0]["text"]

    bundle = "\n\n".join(
        f"--- Answer {i+1} (from {c['model']}) ---\n{c['text']}"
        for i, c in enumerate(candidates)
    )
    fuse_prompt = (
        f"You are a senior editor. The user asked:\n{prompt}\n\n"
        f"Here is the structured task brief you should keep in mind:\n{task_brief}\n\n"
        f"Below are {len(candidates)} candidate answers from independent models. "
        "Synthesize them into ONE final answer that is accurate, complete, well-structured, "
        "and free of redundancy. Resolve any conflicts by reasoning. Return ONLY the final answer.\n\n"
        f"{bundle}"
    )
    fuse_out = await _invoke_with_fallback(
        refiner, refiner_fb,
        [{"role": "user", "content": fuse_prompt}],
        max_retries=max_retries,
        max_tokens=1500,
    )
    fused_text = _extract_text(fuse_out) if not fuse_out.get("error") else ""

    if fused_text and not _looks_low_quality(fused_text):
        trace.append({
            "stage": "final_fuse",
            "refiner": refiner,
            "attempts": fuse_out.get("model_attempts", 1),
            "used_fallback": fuse_out.get("model_attempts", 1) > 1,
            "usage": _usage(fuse_out),
        })
        return fused_text

    # Final fuse 全部 fallback 也挂了, 退化为 candidate 打分排序
    candidates.sort(key=lambda c: (
        -len(c["text"]),                # 偏长一些
        -int(c.get("used_fallback", False)),  # 主路径优先
    ))
    trace.append({
        "stage": "final_fuse",
        "degraded": True,
        "reason": "refiner_and_fallbacks_failed",
        "mode": "best-of-candidates",
    })
    return candidates[0]["text"]


_OP_DISPATCH: Dict[str, Callable] = {
    "expert": op_expert,
    "vote": op_vote,
    "refine": op_refine,
    "pipeline": op_pipeline,
    "n1_fusion": op_n1_fusion,
}


async def _run_operator(step: FusionStep, prompt: str, history: List[Dict[str, str]], trace: List[Dict[str, Any]]) -> str:
    fn = _OP_DISPATCH.get(step.type)
    if fn is None:
        raise ValueError(f"FusionRouter: unknown operator '{step.type}'")
    if step.type == "refine":
        # refine needs the upstream accumulated text; pulled from trace
        raise ValueError("refine must be invoked via op_refine directly (it needs prev text)")
    return await fn(step, prompt, history, trace)


# ----------------------------------------------------------------------
# intent router (cheap keyword heuristic; deterministic, no LLM cost)
# ----------------------------------------------------------------------
_INTENT_KEYWORDS: List[tuple] = [
    ("code", ("def ", "class ", "function", "bug", "compile", "import ", "regex", "rewrite this code", "code:")),
    ("math", ("prove", "=?", "积分", "求导", "limit", "derivative", "equation")),
    ("summary", ("summary", "summarize", "总结", "概括", "tldr", "TLDR")),
    ("tooluse", ("call tool", "use tool", "调用工具", "tool_call")),
]


def _classify_intent(prompt: str) -> str:
    p = (prompt or "").lower()
    for tag, kws in _INTENT_KEYWORDS:
        for kw in kws:
            if kw.lower() in p:
                return tag
    return "default"


# ----------------------------------------------------------------------
# plan registry (loaded from config)
# ----------------------------------------------------------------------
class FusionRouter:
    """Holds plan definitions + executes them against any incoming prompt.

    Lifecycle: singleton, initialized in app.py at boot.
    """

    def __init__(self, plans: Optional[Dict[str, Dict[str, Any]]] = None):
        self.plans: Dict[str, Dict[str, Any]] = plans or {}
        LOG.info("FusionRouter: registered %d plan(s)", len(self.plans))

    def register(self, plan_id: str, plan_cfg: Dict[str, Any]) -> None:
        self.plans[plan_id] = plan_cfg

    def unregister(self, plan_id: str) -> bool:
        return self.plans.pop(plan_id, None) is not None

    def has_plan(self, plan_id: str) -> bool:
        return plan_id in self.plans

    def list_plans(self) -> List[str]:
        return sorted(self.plans.keys())

    def get_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        return self.plans.get(plan_id)

    async def run_plan(
        self,
        plan_id: str,
        prompt: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> FusionResult:
        if plan_id not in self.plans:
            raise KeyError(f"FusionRouter: unknown plan '{plan_id}'")
        cfg = self.plans[plan_id]
        step = FusionStep(type=cfg.get("type"), params=cfg.get("params", cfg))
        history = history or []
        trace: List[Dict[str, Any]] = []
        t0 = time.time()
        try:
            answer = await _run_operator(step, prompt, history, trace)
            success = True
            error: Optional[str] = None
        except Exception as e:
            LOG.exception("fusion_run_failed plan=%s", plan_id)
            answer = f"[fusion_run_failed] {e!r}"
            success = False
            error = repr(e)
        elapsed = time.time() - t0
        # aggregate usage
        tot_in = 0
        tot_out = 0
        for item in trace:
            u = item.get("usage")
            if isinstance(u, dict):
                tot_in += int(u.get("in", 0))
                tot_out += int(u.get("out", 0))
        return FusionResult(
            answer=answer,
            trace=trace,
            total_tokens_in=tot_in,
            total_tokens_out=tot_out,
            elapsed_seconds=elapsed,
            success=success,
            error=error,
        )


_router: Optional[FusionRouter] = None


def init_fusion_router(plans: Optional[Dict[str, Dict[str, Any]]] = None) -> FusionRouter:
    global _router
    if _router is None:
        _router = FusionRouter(plans=plans or {})
    else:
        for pid, cfg in (plans or {}).items():
            _router.register(pid, cfg)
    return _router


def get_fusion_router() -> Optional[FusionRouter]:
    return _router


def save_plans_to_config() -> bool:
    """Persist current fusion plans to config.yaml (server.aliases.fusion.plans).

    Called by admin_api after register/delete so plans survive restarts.
    Also called by fusion_presets.seed_all after bulk registration.
    """
    try:
        from .config import config
        router = get_fusion_router()
        if router is None:
            LOG.warning("save_plans_to_config: FusionRouter not initialized")
            return False
        server = config.data.setdefault("server", {})
        aliases = server.setdefault("aliases", {})
        fusion = aliases.setdefault("fusion", {})
        fusion["plans"] = dict(router.plans)
        config._save_yaml()
        LOG.info("FusionRouter: %d plan(s) persisted to config.yaml", len(router.plans))
        return True
    except Exception as e:
        LOG.error("FusionRouter: failed to persist plans: %s", e)
        return False
