"""
task_planner.py -- SMR v0.5.0 Agent framework: task planning layer.

Given a user request and a set of available tools, ``TaskPlanner`` asks the
injected LLM to produce a structured plan (a list of ``Step`` objects with
dependencies, expected outputs and tool parameters).  When the LLM call
fails or returns malformed JSON the planner falls back to a single-step
plan driven by a small keyword heuristic (``_infer_tool``).

JSON parsing is layered: direct ``json.loads`` -> strip ``\`\`\`json ... \`\`\``
fences -> regex substring scan.  The prompt asks the planner to consider
multiple tool calls and inter-step dependencies so downstream executors
can run steps as a DAG instead of a flat loop.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# Optional config import -- keeps the module usable standalone (tests, scripts).
try:
    from .config import CONFIG  # type: ignore
except Exception:  # noqa: BLE001
    CONFIG = None  # type: ignore[assignment]

LOG = logging.getLogger("task_planner")


# -- helpers ----------------------------------------------------------------- #

def _short_id(prefix: str = "s") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# Keyword -> tool heuristic.  First matched tool wins; tools are tried in order.
_KW: Dict[str, List[str]] = {
    "读": ["file_read", "read_file"], "查看": ["file_read", "read_file"],
    "打开": ["file_read", "read_file"], "read": ["file_read", "read_file"],
    "file": ["file_read", "read_file"], "文件": ["file_read", "read_file"],
    "写": ["file_write", "write_file"], "保存": ["file_write", "write_file"],
    "write": ["file_write", "write_file"],
    "发": ["echo_message", "send_message"], "消息": ["echo_message", "send_message"],
    "message": ["echo_message", "send_message"], "send": ["echo_message", "send_message"],
    "运行": ["run_command", "shell", "exec"], "执行": ["run_command", "shell", "exec"],
    "run": ["run_command", "shell", "exec"], "exec": ["run_command", "shell", "exec"],
    "搜索": ["web_search", "search"], "search": ["web_search", "search"],
    "查询": ["web_search", "search"],
    "http": ["http_request", "fetch"], "请求": ["http_request", "fetch"],
}


def _infer_tool(user_msg: str, tools: Dict[str, str]) -> Optional[str]:
    """Keyword-based tool picker used when LLM planning fails."""
    if not user_msg or not tools:
        return None
    msg = user_msg.lower()
    available = set(tools.keys())
    for kw, candidates in _KW.items():
        if kw in msg:
            for c in candidates:
                if c in available:
                    return c
    for t in available:
        if t.lower() in msg:
            return t
    return None


# -- dataclasses -------------------------------------------------------------- #

@dataclass
class Step:
    """A single executable step inside a ``Plan``."""
    id: str
    description: str
    tool_name: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    expected_output: str = ""
    status: str = "pending"
    result: Optional[str] = None
    observation: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "description": self.description,
                "tool_name": self.tool_name, "params": dict(self.params),
                "depends_on": list(self.depends_on),
                "expected_output": self.expected_output,
                "status": self.status, "result": self.result,
                "observation": self.observation}


@dataclass
class Plan:
    """A complete task plan produced by ``TaskPlanner``."""
    id: str
    user_msg: str
    steps: List[Step]
    created_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "user_msg": self.user_msg,
                "created_at": self.created_at,
                "steps": [s.to_dict() for s in self.steps]}


# -- JSON parsing (layered fallback) ----------------------------------------- #

_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_BLOB = re.compile(r"\{[^{}]*\"steps\"[^{}]*\[.*?\]\s*\}", re.DOTALL)


def _parse_plan_response(response: str) -> Optional[Dict[str, Any]]:
    """Returns the plan dict or None.  Tries three strategies in order."""
    if not response:
        return None
    # 1) direct
    try:
        p = json.loads(response)
        if isinstance(p, dict) and "steps" in p:
            return p
    except (json.JSONDecodeError, ValueError):
        pass
    # 2) markdown fence
    m = _FENCE.search(response)
    if m:
        try:
            p = json.loads(m.group(1))
            if isinstance(p, dict) and "steps" in p:
                return p
        except (json.JSONDecodeError, ValueError):
            pass
    # 3) first {"steps": ...} substring
    m = _BLOB.search(response)
    if m:
        try:
            p = json.loads(m.group(0))
            if isinstance(p, dict) and "steps" in p:
                return p
        except (json.JSONDecodeError, ValueError):
            pass
    return None


# -- planner ----------------------------------------------------------------- #

LLMCallFn = Callable[[str, int], str]


class TaskPlanner:
    """Generate a structured ``Plan`` from a natural-language request.

    Args:
        llm_call_fn: ``(prompt: str, max_tokens: int) -> str``.  Typically
            the SMR main model's ``call`` / ``invoke`` method.
    """

    DEFAULT_MAX_TOKENS = 1024

    def __init__(self, llm_call_fn: LLMCallFn) -> None:
        self.llm_call_fn = llm_call_fn
        self._max_tokens = self._resolve_max_tokens()

    # ---- public ------------------------------------------------------------ #

    def plan(self, user_msg: str, available_tools: List[str]) -> Plan:
        """Plan ``user_msg``.  Always returns a Plan with >=1 step.

        Falls back to a single ``llm_reasoning`` step when the LLM call
        fails or its response cannot be parsed.
        """
        tool_descs = {t: t for t in (available_tools or [])}
        steps = self._plan_with_llm(user_msg, tool_descs) or self._fallback(user_msg, tool_descs)
        return Plan(id=_short_id("plan"), user_msg=user_msg, steps=steps,
                    created_at=time.time())

    # ---- internals --------------------------------------------------------- #

    def _resolve_max_tokens(self) -> int:
        if CONFIG is None:
            return self.DEFAULT_MAX_TOKENS
        try:
            v = getattr(CONFIG, "planner_max_tokens", None)
            if isinstance(v, int) and v > 0:
                return v
        except Exception:  # noqa: BLE001
            LOG.debug("CONFIG.planner_max_tokens lookup failed", exc_info=True)
        return self.DEFAULT_MAX_TOKENS

    def _build_prompt(self, user_msg: str, available_tools: List[str]) -> str:
        tools_block = ", ".join(available_tools) if available_tools \
            else "(no tools available -- pure LLM reasoning only)"
        return (
            "你是 SMR 任务规划器。用户给你一个请求，你必须返回 JSON 格式的步骤列表。\n"
            "每步包括：description, tool_name (从可用工具中选), params, "
            "expected_output, depends_on。\n\n"
            "可用工具: {tools}\n\n"
            "用户请求: {msg}\n\n"
            "提示 (MOA):\n"
            "- 考虑是否需要多次工具调用；复杂任务往往包含 2-4 个步骤。\n"
            "- 步骤间可能存在依赖：如果 step B 需要 step A 的输出，"
            "把 A 的 id 写入 B 的 depends_on。\n"
            "- 没有合适工具时，tool_name 设为 null，params 里放 reasoning prompt。\n"
            "- expected_output 写清后续步骤如何消费它的输出。\n\n"
            "返回 JSON (不要 markdown):\n"
            "{\"plan_description\": \"...\", \"steps\": [...]}\n"
        ).format(tools=tools_block, msg=user_msg)

    def _plan_with_llm(self, user_msg: str, tools: Dict[str, str]) -> List[Step]:
        """Returns [] on any LLM/parse failure -- caller falls back."""
        prompt = self._build_prompt(user_msg, list(tools.keys()))
        try:
            raw = self.llm_call_fn(prompt, self._max_tokens)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("LLM call failed in task planner: %s", exc)
            return []
        parsed = _parse_plan_response(raw)
        if not parsed:
            LOG.warning("Planner LLM returned malformed JSON (len=%d); falling back.",
                        len(raw or ""))
            return []
        steps_raw = parsed.get("steps") or []
        if not isinstance(steps_raw, list):
            LOG.warning("Planner LLM 'steps' is not a list: %r", type(steps_raw))
            return []
        out: List[Step] = []
        for item in steps_raw:
            if not isinstance(item, dict):
                continue
            out.append(Step(
                id=_short_id("step"),
                description=str(item.get("description", "")).strip() or "(no description)",
                tool_name=item.get("tool_name"),
                params=dict(item.get("params") or {}),
                depends_on=list(item.get("depends_on") or []),
                expected_output=str(item.get("expected_output", "")).strip(),
            ))
        if not out:
            LOG.warning("Planner LLM returned empty steps list; falling back.")
        return out

    # ---- fallback ---------------------------------------------------------- #

    def _fallback(self, user_msg: str, tools: Dict[str, str]) -> List[Step]:
        """Single-step fallback plan when LLM planning has failed."""
        tool = _infer_tool(user_msg, tools)
        if tool is not None:
            return [Step(
                id=_short_id("step"),
                description=f"Execute tool '{tool}' for: {user_msg}",
                tool_name=tool, params={"input": user_msg}, depends_on=[],
                expected_output="Tool execution result.",
            )]
        return [Step(
            id=_short_id("step"),
            description=f"Reason about the user's request: {user_msg}",
            tool_name=None, params={"prompt": user_msg}, depends_on=[],
            expected_output="Reasoned answer derived from context.",
        )]


# -- smoke test --------------------------------------------------------------- #

if __name__ == "__main__":  # pragma: no cover
    import asyncio as _asyncio

    async def _demo() -> None:
        async def fake_llm(prompt: str, max_tokens: int) -> str:
            return '{"steps":[{"description":"test"}]}'

        planner = TaskPlanner(fake_llm)  # type: ignore[arg-type]
        plan_obj = planner.plan("test msg", ["file_read", "echo_message"])
        assert len(plan_obj.steps) >= 1
        print(json.dumps(plan_obj.to_dict(), indent=2, ensure_ascii=False))

    _asyncio.run(_demo())