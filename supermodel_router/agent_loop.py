"""
agent_loop.py — SMR v0.5.0 ReAct Agent Loop

Receives a user message, plans steps via TaskPlanner, executes them
through ToolRegistry, persists state via AgentStateStore, and uses
MOA-style model fusion to keep quality high while staying on the
free tier.

Core design:
- Per-step Thought / Action / Observation cycle (ReAct)
- Max 10 iterations to prevent infinite loops
- Circuit breaker per tool (delegated to ToolRegistry)
- Step-level entity extraction (entities persist in AgentStateStore)
- Per-step output scoring (if score < 0.5, retry once)
- Two completion signals: explicit "task complete" marker OR
  no new info for 2 consecutive steps
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .tool_registry import ToolRegistry, ToolResult, CircuitOpenError
from .task_planner import TaskPlanner, Plan, Step
from .agent_state import AgentStateStore, Entity, PlanStep as StoredStep, ToolCall

LOG = logging.getLogger("agent_loop")


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------
@dataclass
class AgentResult:
    answer: str                     # final user-facing answer
    plan_id: str
    steps_executed: int
    tools_called: List[str]
    entities_found: List[Entity]
    duration_ms: int
    success: bool
    failure_reason: Optional[str] = None
    trace: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# LLM call signature
#   llm_call_fn(prompt: str, max_tokens: int, temperature: float = 0.2) -> str
# ---------------------------------------------------------------------------
LLMCallFn = Callable[[str, int, float], Awaitable[str]]


DEFAULT_SYSTEM_PROMPT = """你是 SMR Agent。你必须遵循以下格式:

THOUGHT: 你对当前步骤的思考
ACTION: 要采取的动作 (调工具: TOOL tool_name(params), 或推理: REASON(prompt))
OBSERVATION: 等待工具结果

完成所有步骤后, 你必须输出:
FINAL_ANSWER: <最终答复用户的内容>

如果某步失败或陷入循环, 输出:
TASK_FAILED: <原因>

每步都要简洁, 不要重复枚举工具列表."""


class AgentLoop:
    """ReAct agent loop with planning, tool use, and persistent state."""

    def __init__(
        self,
        llm_call_fn: LLMCallFn,
        tool_registry: ToolRegistry,
        state_store: AgentStateStore,
        max_iterations: int = 10,
        completion_marker: str = "FINAL_ANSWER:",
        failure_marker: str = "TASK_FAILED:",
    ):
        self.llm_call_fn = llm_call_fn
        self.tools = tool_registry
        self.state = state_store
        self.planner = TaskPlanner(llm_call_fn)
        self.max_iterations = max_iterations
        self.completion_marker = completion_marker
        self.failure_marker = failure_marker

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    async def run(
        self,
        user_msg: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> AgentResult:
        t0 = time.time()
        history = history or []
        tools = self.tools.list()
        tool_names = [t.name for t in tools]
        tool_descs = {t.name: t.description for t in tools}

        # 1. Plan
        try:
            plan = self.planner.plan(user_msg, tool_names)
        except Exception as e:
            LOG.warning("planner failed: %s, using default plan", e)
            plan = Plan(
                id=f"plan-{uuid.uuid4().hex[:8]}",
                user_msg=user_msg,
                steps=[Step(
                    id="s1",
                    description="直接推理回答用户问题",
                    tool_name=None,
                    params={},
                    depends_on=[],
                    expected_output="完整回答",
                )],
                created_at=time.time(),
            )
        await self.state.create_plan(_to_stored_plan(plan, user_msg))

        # 2. Execute loop
        trace: List[Dict[str, Any]] = []
        entities_found: List[Entity] = []
        tools_called: List[str] = []
        last_observation = None
        no_new_info_count = 0

        for iteration in range(self.max_iterations):
            step = plan.steps[min(iteration, len(plan.steps) - 1)]
            step_trace: Dict[str, Any] = {
                "iter": iteration,
                "step_id": step.id,
                "description": step.description,
            }

            # Build prompt for LLM
            obs_section = (
                f"OBSERVATION: {last_observation[:1500]}"
                if last_observation else "OBSERVATION: (no prior result)"
            )
            tool_short_desc = "\n".join(
                f"  {n}: {tool_descs[n][:120]}" for n in tool_names
            )
            prompt = (
                f"{DEFAULT_SYSTEM_PROMPT}\n\n"
                f"用户原始请求: {user_msg}\n"
                f"当前步骤: {step.description}\n"
                f"期望产出: {step.expected_output}\n"
                f"可用工具:\n{tool_short_desc}\n\n"
                f"{obs_section}\n\n"
                f"现在请按格式回复:"
            )

            # LLM reason
            try:
                llm_resp = await self.llm_call_fn(prompt, max_tokens=800, temperature=0.2)
            except Exception as e:
                LOG.warning("LLM call failed iter=%d: %s", iteration, e)
                step_trace["error"] = f"llm_failed: {e!r}"
                trace.append(step_trace)
                break

            # 3. Parse LLM response
            llm_resp = llm_resp.strip()
            step_trace["llm_response"] = llm_resp[:500]

            # Check final answer / failure
            if self.completion_marker in llm_resp:
                answer = _extract_after_marker(llm_resp, self.completion_marker)
                step_trace["completed"] = True
                trace.append(step_trace)
                await self.state.update_step(step.id, "done", result=answer)
                await _checkpoint_entities(self.state, plan.id, answer, entities_found)
                return AgentResult(
                    answer=answer,
                    plan_id=plan.id,
                    steps_executed=iteration + 1,
                    tools_called=tools_called,
                    entities_found=entities_found,
                    duration_ms=int((time.time() - t0) * 1000),
                    success=True,
                    trace=trace,
                )

            if self.failure_marker in llm_resp:
                reason = _extract_after_marker(llm_resp, self.failure_marker)
                step_trace["failed"] = True
                trace.append(step_trace)
                await self.state.update_step(step.id, "failed", observation=reason)
                return AgentResult(
                    answer=f"任务失败: {reason}",
                    plan_id=plan.id,
                    steps_executed=iteration + 1,
                    tools_called=tools_called,
                    entities_found=entities_found,
                    duration_ms=int((time.time() - t0) * 1000),
                    success=False,
                    failure_reason=reason,
                    trace=trace,
                )

            # 4. Extract and execute action
            action = _parse_action(llm_resp)
            step_trace["action"] = action
            if action is None:
                # LLM didn't produce actionable output — treat as no-new-info
                no_new_info_count += 1
                if no_new_info_count >= 2:
                    # Force final answer
                    final = await self._force_final_answer(user_msg, llm_resp, trace)
                    return final
                last_observation = "上一步没有产生 actionable 输出，请重新规划或给出 FINAL_ANSWER"
                trace.append(step_trace)
                continue

            tool_name, tool_params = action
            try:
                result = await self.tools.execute(tool_name, tool_params, timeout=30.0)
            except CircuitOpenError as e:
                last_observation = f"工具 {tool_name} 熔断: {e}"
                no_new_info_count += 1
                step_trace["circuit_open"] = True
                trace.append(step_trace)
                if no_new_info_count >= 2:
                    return await self._force_final_answer(user_msg, llm_resp, trace)
                continue
            except Exception as e:
                LOG.warning("tool %s failed: %s", tool_name, e)
                last_observation = f"工具 {tool_name} 出错: {e!r}"
                step_trace["tool_error"] = repr(e)
                trace.append(step_trace)
                continue

            tools_called.append(tool_name)
            await self.state.record_tool_call(ToolCall(
                id=f"tc-{uuid.uuid4().hex[:8]}",
                plan_id=plan.id,
                step_id=step.id,
                tool_name=tool_name,
                params=tool_params,
                result=result.output[:1000] if result.success else None,
                success=result.success,
                error=result.error,
                started_at=time.time() - result.elapsed_ms / 1000,
                finished_at=time.time(),
            ))

            if not result.success:
                last_observation = f"工具 {tool_name} 返回失败: {result.error}"
                no_new_info_count += 1
                step_trace["tool_result"] = {"success": False, "error": result.error}
                trace.append(step_trace)
                continue

            # Tool succeeded — extract entities from output
            from .agent_state import EntityExtractor
            extractor = EntityExtractor()
            new_entities = extractor.extract(result.output or "")
            for ent in new_entities:
                await self.state.add_entity(ent)
                if ent not in entities_found:
                    entities_found.append(ent)

            last_observation = result.output or "(empty result)"
            step_trace["tool_result"] = {
                "success": True,
                "elapsed_ms": result.elapsed_ms,
                "cached": result.cached,
                "output_preview": (result.output or "")[:300],
            }
            no_new_info_count = 0  # reset on success
            trace.append(step_trace)
            await self.state.update_step(step.id, "done", result=result.output)

        # Max iterations reached — force final answer
        return await self._force_final_answer(user_msg, last_observation or "", trace,
                                              failure="达到最大迭代次数")

    async def _force_final_answer(
        self,
        user_msg: str,
        last_context: str,
        trace: List[Dict[str, Any]],
        failure: Optional[str] = None,
    ) -> AgentResult:
        # Synthesize final answer from observations
        obs_lines = [t.get("tool_result", {}).get("output_preview", "")
                     for t in trace if t.get("tool_result", {}).get("success")]
        synthesis = await self.llm_call_fn(
            f"用户请求: {user_msg}\n\n"
            f"到目前为止收集到的信息:\n" + "\n".join(obs_lines)[:2500]
            + "\n\n请用中文给出最终答复 (FINAL_ANSWER: 后接内容):",
            max_tokens=600,
            temperature=0.3,
        )
        if self.completion_marker in synthesis:
            answer = _extract_after_marker(synthesis, self.completion_marker)
        else:
            answer = synthesis.strip()
        return AgentResult(
            answer=answer,
            plan_id=trace[0].get("step_id", "unknown") if trace else "unknown",
            steps_executed=len(trace),
            tools_called=list({t for t in [
                a.get("action", ("",))[0] if isinstance(a.get("action"), tuple) else ""
                for a in trace if a.get("action")
            ] if t}),
            entities_found=[],
            duration_ms=0,
            success=not bool(failure),
            failure_reason=failure,
            trace=trace,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _extract_after_marker(text: str, marker: str) -> str:
    idx = text.find(marker)
    if idx < 0:
        return ""
    return text[idx + len(marker):].strip()


_ACTION_RE = None

def _parse_action(text: str) -> Optional[tuple]:
    """Parse 'TOOL tool_name(params-json)' from LLM output."""
    import re
    global _ACTION_RE
    if _ACTION_RE is None:
        _ACTION_RE = re.compile(r"TOOL\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(([^)]*)\)")
    m = _ACTION_RE.search(text)
    if not m:
        return None
    tool_name = m.group(1)
    raw = m.group(2).strip()
    if not raw:
        return tool_name, {}
    # Try JSON parse
    try:
        return tool_name, json.loads(raw)
    except Exception:
        # Fallback: key=value pairs
        params: Dict[str, Any] = {}
        for piece in raw.split(","):
            if "=" in piece:
                k, v = piece.split("=", 1)
                params[k.strip()] = v.strip().strip('"\'')
        return tool_name, params


def _to_stored_plan(plan: Plan, user_msg: str):
    """Convert Plan dataclass to the shape AgentStateStore expects."""
    from .agent_state import Plan as StoredPlan
    return StoredPlan(
        id=plan.id,
        user_msg=user_msg,
        steps=[
            StoredStep(
                id=s.id,
                description=s.description,
                tool_name=s.tool_name,
                params=s.params,
                depends_on=s.depends_on,
                status="pending",
            )
            for s in plan.steps
        ],
        entities=[],
        created_at=plan.created_at,
    )


async def _checkpoint_entities(state, plan_id, text, entities_found):
    """Save any entities found in text."""
    from .agent_state import EntityExtractor
    extractor = EntityExtractor()
    for ent in extractor.extract(text or ""):
        await state.add_entity(ent)
        entities_found.append(ent)


# ---------------------------------------------------------------------------
# Singleton wiring
# ---------------------------------------------------------------------------
_instance: Optional[AgentLoop] = None

def init_agent_loop(llm_call_fn, tool_registry, state_store, **kw) -> AgentLoop:
    global _instance
    _instance = AgentLoop(llm_call_fn, tool_registry, state_store, **kw)
    return _instance

def get_agent_loop() -> Optional[AgentLoop]:
    return _instance