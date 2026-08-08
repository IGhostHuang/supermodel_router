"""moa_selector.py — SMR v0.5.0 自适应 Mixture-of-Agents 选择器

核心理念:
  不是写死的 fusion plan, 而是根据任务复杂度、当前可用 provider 健康度、
  历史表现动态选 1-4 个模型并行, 用 quality_gate 评分决定最佳输出。

策略:
  - 复杂度评分 (0-100) 由 5 个维度加权
  - 按分数选 1/2/3/4 个模型
  - 优先级: freellmapi (12s) > openrouter (54s) > local qwythos (162s)
  - 已 429 的 provider 自动降级
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

LOG = logging.getLogger("moa_selector")


@dataclass
class ModelChoice:
    """A single model selected for the MOA round."""
    provider: str         # "fusion" / "openrouter" / "local" / etc.
    model: str            # bare model id (e.g. "deepseek-v4-flash")
    full_id: str          # "fusion/deepseek-v4-flash"
    timeout: float        # seconds
    role: str             # "primary" / "critic" / "diversity" / "fast"
    estimated_speed_s: float = 12.0


@dataclass
class MOAConfig:
    """Result of MOASelector.select()."""
    complexity: int                  # 0-100
    complexity_label: str            # "trivial" / "simple" / "medium" / "complex" / "expert"
    choices: List[ModelChoice]
    strategy: str                    # "single" / "parallel+best" / "ensemble+critic" / "deep_dive"
    parallel: bool
    require_critic: bool
    rationale: str = ""


# ---------------------------------------------------------------------------
# Provider health (very lightweight — uses the model's track record)
# ---------------------------------------------------------------------------
_health: Dict[str, Dict[str, Any]] = {}


def record_outcome(provider: str, model: str, success: bool, latency_ms: int = 0) -> None:
    """Called after each model invocation so the selector learns."""
    key = f"{provider}/{model}"
    h = _health.setdefault(key, {"success": 0, "fail": 0, "last_429": 0.0, "last_seen": 0.0})
    if success:
        h["success"] += 1
    else:
        h["fail"] += 1
        h["last_429"] = time.time()
    h["last_seen"] = time.time()


def is_in_cooldown(provider: str, model: str, cooldown_s: float = 60.0) -> bool:
    key = f"{provider}/{model}"
    h = _health.get(key, {})
    return (time.time() - h.get("last_429", 0.0)) < cooldown_s


# ---------------------------------------------------------------------------
# Complexity scorer
# ---------------------------------------------------------------------------
_KEYWORDS_HEAVY = {
    "分析", "综合", "推理", "深度", "对比", "论证", "解释", "评估", "总结",
    "analyze", "synthesize", "reason", "compare", "evaluate", "explain",
    "comprehensive", "detailed", "in-depth",
}
_KEYWORDS_CODE = {
    "代码", "code", "function", "function", "实现", "implement", "编写", "write",
    "算法", "algorithm", "重构", "refactor", "bug", "调试", "debug",
}
_KEYWORDS_CREATIVE = {
    "故事", "story", "诗", "poem", "创意", "creative", "文案", "copy",
    "营销", "marketing", "小说", "novel",
}
_KEYWORDS_TOOLS = {
    "获取", "获取", "读取", "查询", "查", "备份", "发", "发送给",
    "get", "fetch", "read", "query", "send", "find", "search", "lookup",
}


def score_complexity(user_msg: str, history: Optional[List[Dict[str, str]]] = None) -> int:
    """Return 0-100 complexity score."""
    history = history or []
    text = (user_msg or "").lower()

    score = 20  # baseline

    # length
    if len(user_msg) > 1000:
        score += 25
    elif len(user_msg) > 400:
        score += 15
    elif len(user_msg) > 150:
        score += 8

    # keyword density
    for kw in _KEYWORDS_HEAVY:
        if kw in text:
            score += 6
    for kw in _KEYWORDS_CODE:
        if kw in text:
            score += 5
    for kw in _KEYWORDS_CREATIVE:
        if kw in text:
            score += 4
    for kw in _KEYWORDS_TOOLS:
        if kw in text:
            score += 4

    # multi-turn context
    if len(history) > 4:
        score += 8
    elif len(history) > 1:
        score += 4

    # code markers
    if "```" in user_msg or "def " in user_msg or "class " in user_msg:
        score += 10

    # list / multi-step indicators
    if re.search(r"\b步骤|step\b|first|second|then|然后|接着|最后", text, re.I):
        score += 5

    # multi-intent (multiple sentences with question marks)
    if user_msg.count("?") + user_msg.count("？") >= 2:
        score += 6

    return min(score, 100)


def complexity_label(score: int) -> str:
    if score < 25:
        return "trivial"
    if score < 50:
        return "simple"
    if score < 70:
        return "medium"
    if score < 85:
        return "complex"
    return "expert"


# ---------------------------------------------------------------------------
# Catalog (provider_name -> list of candidate model bare_ids)
# ---------------------------------------------------------------------------
DEFAULT_CATALOG: Dict[str, Dict[str, Any]] = {
    # v0.5.0: BARE model IDs (no provider prefix). SMR's engine.pick_chain
    # resolves them to the best available provider automatically.
    # Verified working: deepseek-v4-flash, qwythos-9b (and others via auth).
    "_bare": {
        "fast":   ["qwythos-9b", "deepseek-v4-flash"],
        "smart":  ["qwythos-9b", "deepseek-v4-flash"],
        "deep":   ["qwythos-9b"],
        "default_speed_s": 60.0,
    },
    # openrouter-provider entries (only accessible with auth key)
    "openrouter": {
        "fast":   ["inclusionai/ling-3.0-tiny:free", "poolside/laguna-s-2.1:free",
                   "cohere/north-mini-code:free"],
        "smart":  ["nvidia/nemotron-3-super-120b-a12b:free",
                   "google/gemma-4-31b-it:free"],
        "deep":   ["nvidia/nemotron-3-super-120b-a12b:free"],
        "default_speed_s": 30.0,
    },
    # newapi (mostly Chinese, requires auth)
    "newapi": {
        "fast":   ["MiniMax/MiniMax- M3", "deepseek-ai/DeepSeek-V4-Flash-0731"],
        "smart":  ["Qwen/Qwen3.5-397B-A17B", "deepseek-ai/DeepSeek-V4-Pro"],
        "deep":   ["Qwen/Qwen3.5-397B-A17B"],
        "default_speed_s": 25.0,
    },
    # 魔塔免费模型 (modelstudio Chinese, requires auth)
    "魔塔免费模型": {
        "fast":   ["deepseek-ai/DeepSeek-V4-Flash-0731", "deepseek-ai/DeepSeek-V4-Pro"],
        "smart":  ["deepseek-ai/DeepSeek-V4-Pro"],
        "deep":   ["deepseek-ai/DeepSeek-V4-Pro"],
        "default_speed_s": 20.0,
    },
    "local": {
        "fast":   ["qwythos-9b"],
        "smart":  ["qwythos-9b"],
        "deep":   ["qwythos-9b"],
        "default_speed_s": 162.0,
    },
}


# ---------------------------------------------------------------------------
# MOA Selector
# ---------------------------------------------------------------------------
class MOASelector:
    """Pick 1-4 models + strategy based on task complexity + provider health."""

    def __init__(self, catalog: Optional[Dict[str, Dict[str, Any]]] = None):
        self.catalog = catalog or DEFAULT_CATALOG

    def select(
        self,
        user_msg: str,
        history: Optional[List[Dict[str, str]]] = None,
        explicit_preference: Optional[str] = None,  # "fast" / "smart" / "deep"
        force_n_models: Optional[int] = None,  # v0.5.1: explicit override for hybrid mode
    ) -> MOAConfig:
        score = score_complexity(user_msg, history)
        label = complexity_label(score)
        tier = explicit_preference or self._tier_for(score)

        # Decide strategy
        if force_n_models is not None and force_n_models > 0:
            # v0.5.1: explicit override (used by agent:hybrid)
            n_models = min(force_n_models, 4)  # cap at 4
            if n_models == 1:
                strategy = "single"; parallel = False; require_critic = False
            else:
                strategy = "parallel+best"; parallel = True
                require_critic = (n_models >= 3)
        elif score < 25:
            strategy = "single"
            parallel = False
            require_critic = False
            n_models = 1
        elif score < 50:
            strategy = "parallel+best"
            parallel = True
            require_critic = False
            n_models = 2
        elif score < 70:
            strategy = "parallel+best"
            parallel = True
            require_critic = False
            n_models = 3
        elif score < 85:
            strategy = "ensemble+critic"
            parallel = True
            require_critic = True
            n_models = 3  # 2 producers + 1 critic
        else:
            strategy = "deep_dive"
            parallel = True
            require_critic = True
            n_models = 4  # 3 producers + 1 critic

        # Build candidate list, filtering out cooled-down models
        # v0.5.0: priority is _bare (engine.pick_chain will route to the best
        # provider with proper auth) > openrouter (only with auth) > newapi > local.
        choices: List[ModelChoice] = []
        provider_order = ("_bare", "openrouter", "newapi", "魔塔免费模型", "local")
        for prov_name in provider_order:
            cat = self.catalog.get(prov_name, {})
            models = cat.get(tier) or cat.get("fast") or []
            for m in models:
                if is_in_cooldown(prov_name, m):
                    continue
                choices.append(ModelChoice(
                    provider=prov_name,
                    model=m,
                    full_id=f"{prov_name}/{m}",
                    timeout=30.0 if prov_name != "local" else 200.0,
                    role="primary",
                    estimated_speed_s=cat.get("default_speed_s", 30.0),
                ))
                break  # one per provider per tier
            if len(choices) >= n_models:
                break

        # If still short, fill from fusion fast tier
        if len(choices) < n_models:
            fast = (self.catalog.get("fusion", {}).get("fast") or [])
            for m in fast:
                if any(c.model == m for c in choices):
                    continue
                if is_in_cooldown("fusion", m):
                    continue
                choices.append(ModelChoice(
                    provider="fusion",
                    model=m,
                    full_id=f"fusion/{m}",
                    timeout=30.0,
                    role="primary",
                    estimated_speed_s=12.0,
                ))
                if len(choices) >= n_models:
                    break

        # Truncate to requested count
        choices = choices[:n_models]

        # Append a critic if needed
        if require_critic:
            critic_prov = self.catalog.get("openrouter", {}).get("smart", ["openai/gpt-4o-mini"])[0]
            critic_choice = ModelChoice(
                provider="openrouter",
                model=critic_prov,
                full_id=f"openrouter/{critic_prov}",
                timeout=45.0,
                role="critic",
                estimated_speed_s=30.0,
            )
            if not is_in_cooldown("openrouter", critic_prov):
                choices.append(critic_choice)

        rationale = (
            f"complexity={score} ({label}); tier={tier}; "
            f"strategy={strategy}; {len(choices)} model(s) selected"
        )
        LOG.info("MOA select: %s; models=%s", rationale, [c.full_id for c in choices])

        return MOAConfig(
            complexity=score,
            complexity_label=label,
            choices=choices,
            strategy=strategy,
            parallel=parallel,
            require_critic=require_critic,
            rationale=rationale,
        )

    @staticmethod
    def _tier_for(score: int) -> str:
        if score < 35:
            return "fast"
        if score < 75:
            return "smart"
        return "deep"


# ---------------------------------------------------------------------------
# MOA Runner — invokes selected models, picks best
# ---------------------------------------------------------------------------
@dataclass
class MOAResult:
    best_answer: str
    best_model: str
    best_score: float
    all_outputs: List[Tuple[str, str, float]]  # (model_full_id, output, score)
    complexity: int
    strategy: str
    duration_ms: int


async def run_moa(
    config: MOAConfig,
    prompt: str,
    history: Optional[List[Dict[str, str]]],
    invoke_fn: Callable[[str, List[Dict[str, str]]], Awaitable[Any]],
    score_fn: Callable[[str, str, str], Awaitable[float]],
    quality_gate_scorer=None,
) -> MOAResult:
    """Execute the chosen MOA config and return the best output.

    invoke_fn(model_full_id, messages) -> {"content": str, "error": str?}
    score_fn(prompt, output, model_full_id) -> float in [0, 1]
    """
    t0 = time.time()
    messages = (history or []) + [{"role": "user", "content": prompt}]

    # Build tasks
    tasks = []
    for choice in config.choices:
        if choice.role == "critic":
            # Critics evaluate the producers' outputs
            continue
        tasks.append((choice, _invoke(invoke_fn, choice, messages)))

    if config.parallel and len(tasks) > 1:
        results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)
    else:
        results = []
        for _, coro in tasks:
            try:
                results.append(await coro)
            except Exception as e:
                results.append(e)

    producer_outputs: List[Tuple[ModelChoice, str]] = []
    for (choice, _), r in zip(tasks, results):
        if isinstance(r, Exception) or not isinstance(r, dict):
            record_outcome(choice.provider, choice.model, False)
            continue
        if r.get("error"):
            record_outcome(choice.provider, choice.model, False)
            continue
        content = (r.get("content") or "").strip()
        if not content:
            # Try reasoning_content
            content = (r.get("reasoning_content") or "").strip()
        if content:
            record_outcome(choice.provider, choice.model, True)
            producer_outputs.append((choice, content))

    if not producer_outputs:
        return MOAResult(
            best_answer="所有模型都失败了，请稍后重试。",
            best_model="none",
            best_score=0.0,
            all_outputs=[],
            complexity=config.complexity,
            strategy=config.strategy,
            duration_ms=int((time.time() - t0) * 1000),
        )

    # Score each
    scored: List[Tuple[ModelChoice, str, float]] = []
    for choice, content in producer_outputs:
        s = await score_fn(prompt, content, choice.full_id)
        scored.append((choice, content, s))

    scored.sort(key=lambda x: x[2], reverse=True)
    best_choice, best_content, best_score = scored[0]

    return MOAResult(
        best_answer=best_content,
        best_model=best_choice.full_id,
        best_score=best_score,
        all_outputs=[(c.full_id, c_, s) for c, c_, s in scored],
        complexity=config.complexity,
        strategy=config.strategy,
        duration_ms=int((time.time() - t0) * 1000),
    )


async def _invoke(invoke_fn, choice, messages):
    return await invoke_fn(choice.full_id, messages)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_selector: Optional[MOASelector] = None

def get_moa_selector() -> MOASelector:
    global _selector
    if _selector is None:
        _selector = MOASelector()
    return _selector


def reset_moa_selector() -> None:
    global _selector
    _selector = None