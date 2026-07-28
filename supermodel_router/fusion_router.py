"""
fusion_router.py -- the unified "any-to-any fusion model" layer for SMR.

The user said "做就做最好": don't pick one of the four fusion patterns
(professional routing / pipeline / parallel-vote / model-pool). Combine them
into a single plan DSL with four composable operators:

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
              steps:
                - type: expert
                  experts:
                    code: openai/gpt-4o-mini
                    math: anthropic/claude-haiku
                    summary: openrouter/free
                - type: vote
                  model_ids: [openai/o1, google/gemini-2.5-pro]
                  strategy: concat
                - type: refine
                  judge_model: anthropic/claude-sonnet-4.6

Request body:
    {"model": "fusion:deep_plan", "messages": [...]}

The HTTP layer in openai_routes detects `fusion:` prefix and dispatches to
FusionRouter.run_plan() instead of the regular single-model chain.

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
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

LOG = logging.getLogger(__name__)


@dataclass
class FusionStep:
    """Recursive plan node; exactly one of {leaf operator} per step."""

    type: str  # 'expert' | 'pipeline' | 'vote' | 'refine'
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
    max_tokens: int = int(step.params.get("max_tokens", 1024))

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
        from collections import Counter
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


_OP_DISPATCH: Dict[str, Callable] = {
    "expert": op_expert,
    "vote": op_vote,
    "refine": op_refine,
    "pipeline": op_pipeline,
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

    def has_plan(self, plan_id: str) -> bool:
        return plan_id in self.plans

    def list_plans(self) -> List[str]:
        return sorted(self.plans.keys())

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
        tot_in = sum(item.get("usage", {}).get("in", 0) for item in trace if isinstance(item.get("usage"), dict))
        tot_out = sum(item.get("usage", {}).get("out", 0) for item in trace if isinstance(item.get("usage"), dict))
        return FusionResult(
            answer=answer,
            trace=trace,
            total_tokens_in=tot_in,
            total_tokens_out=tot_out,
            elapsed_seconds=elapsed,
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
    """
    try:
        from .config import config
        router = get_fusion_router()
        if router is None:
            LOG.warning("save_plans_to_config: FusionRouter not initialized")
            return False
        # Ensure nested structure exists in config.data
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

# ----------------------------------------------------------------------
# v4-streaming: SSE streaming support for fusion plans
# ----------------------------------------------------------------------

def _sse_chunk(data: dict) -> str:
    """Format a single SSE data line (OpenAI-compatible)."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def run_plan_streaming(
    router,
    plan_id: str,
    prompt: str,
    history=None,
    smr_request_id: str = "",
):
    """Async generator yielding SSE events for a fusion plan execution.

    Events:
      1. fusion_start: plan metadata
      2. fusion_trace: each trace item as operators complete
      3. content deltas: final answer in OpenAI streaming format
      4. fusion_done: summary + [DONE]
    """
    if plan_id not in router.plans:
        yield _sse_chunk({"error": {"message": f"unknown plan {plan_id!r}",
                                    "type": "fusion_plan_error"}})
        yield "data: [DONE]\n\n"
        return

    cfg = router.plans[plan_id]
    step = FusionStep(type=cfg.get("type"), params=cfg.get("params", cfg))
    history = history or []
    trace = []
    t0 = time.time()
    rid = smr_request_id or uuid.uuid4().hex[:10]

    # 1. emit plan start
    yield _sse_chunk({
        "id": f"fusion-{rid[:8]}",
        "object": "chat.completion.chunk",
        "created": int(t0),
        "model": f"fusion:{plan_id}",
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        "fusion_event": "plan_start",
        "plan_type": step.type,
    })

    # 2. run plan in background, poll trace for new items
    task = asyncio.create_task(_run_operator(step, prompt, history, trace))
    last_len = 0
    while not task.done():
        await asyncio.sleep(0.15)
        while len(trace) > last_len:
            yield _sse_chunk({
                "id": f"fusion-{rid[:8]}",
                "object": "chat.completion.chunk",
                "created": int(t0),
                "model": f"fusion:{plan_id}",
                "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
                "fusion_event": "trace",
                "fusion_trace_item": trace[last_len],
            })
            last_len += 1

    # drain remaining trace
    while len(trace) > last_len:
        yield _sse_chunk({
            "id": f"fusion-{rid[:8]}",
            "object": "chat.completion.chunk",
            "created": int(t0),
            "model": f"fusion:{plan_id}",
            "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
            "fusion_event": "trace",
            "fusion_trace_item": trace[last_len],
        })
        last_len += 1

    # 3. get answer
    try:
        answer = task.result()
        success = True
    except Exception as e:
        LOG.exception("fusion_stream_failed plan=%s", plan_id)
        answer = f"[fusion_run_failed] {e!r}"
        success = False

    elapsed = time.time() - t0
    tot_in = sum(item.get("usage", {}).get("in", 0) for item in trace if isinstance(item.get("usage"), dict))
    tot_out = sum(item.get("usage", {}).get("out", 0) for item in trace if isinstance(item.get("usage"), dict))

    # 4. stream answer in chunks (word-level for compatibility)
    words = answer.split(" ")
    for i, w in enumerate(words):
        delta_text = w + (" " if i < len(words) - 1 else "")
        yield _sse_chunk({
            "id": f"fusion-{rid[:8]}",
            "object": "chat.completion.chunk",
            "created": int(t0),
            "model": f"fusion:{plan_id}",
            "choices": [{"index": 0, "delta": {"content": delta_text}, "finish_reason": None}],
        })

    # 5. final chunk + done
    yield _sse_chunk({
        "id": f"fusion-{rid[:8]}",
        "object": "chat.completion.chunk",
        "created": int(t0),
        "model": f"fusion:{plan_id}",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        "fusion_event": "plan_done",
        "fusion_elapsed": round(elapsed, 3),
        "fusion_tokens_in": tot_in,
        "fusion_tokens_out": tot_out,
        "fusion_success": success,
    })
    yield "data: [DONE]\n\n"
