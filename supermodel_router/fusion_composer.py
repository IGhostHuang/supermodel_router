"""
fusion_composer.py — Intelligent automatic fusion group composition.

Analyzes incoming prompts and dynamically composes optimal fusion plans
without requiring pre-defined plan configurations.

Composition pipeline:
    1. Prompt Analysis
       - Intent classification (code / math / creative / reasoning / factual / chat)
       - Complexity estimation (simple / medium / complex)
       - Domain detection (programming / science / writing / general)
       - Language detection (zh / en / mixed)
       - Token estimation

    2. Operator Selection (rule-based)
       - simple/factual  → expert (single best model, cheapest)
       - creative/open   → vote (diverse perspectives, best_pick)
       - complex/reason  → pipeline (stepwise: analyze → solve → verify)
       - quality-critical → vote + refine (best of N, then polish)

    3. Model Selection (health-aware + capability-matched)
       - Query engine registry for available models
       - Filter by health status (skip SKIP / BANNED / EXPIRED)
       - Score by capability match (intent → model strengths)
       - Ensure provider diversity for vote (avoid same-provider groupthink)
       - Prefer free/cheap models for fan-out, strong model for judge
       - Fallback to configurable model pool if engine unavailable

    4. Plan Building
       - Generate FusionStep config dict
       - Set appropriate max_tokens, timeout, strategy per operator
       - Attach quality baseline model for post-execution validation

Design principles:
    - Zero-config: works out-of-the-box with sensible defaults
    - Credit-efficient: uses minimum models needed, prefers free tier
    - Health-aware: skips unhealthy models automatically
    - Diversity-first: vote groups use different providers when possible
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# defaults
# ---------------------------------------------------------------------------
DEFAULT_JUDGE_MODEL = "deepseek/deepseek-chat"
DEFAULT_BASELINE_MODEL = "deepseek/deepseek-chat"
DEFAULT_MAX_VOTE_MODELS = 4
DEFAULT_MIN_VOTE_MODELS = 2
DEFAULT_PIPELINE_STEPS = 3
DEFAULT_EXPERT_MAX_TOKENS = 2048
DEFAULT_VOTE_MAX_TOKENS = 2048
DEFAULT_PIPELINE_MAX_TOKENS = 2048


# ---------------------------------------------------------------------------
# model pool — fallback when engine registry is unavailable
# ---------------------------------------------------------------------------
# Organized by capability tags.  Each entry is "provider/model_id".
# These are common free/cheap models available across SMR deployments.
_DEFAULT_MODEL_POOL: Dict[str, List[str]] = {
    "code": [
        "deepseek/deepseek-coder",
        "qwen/qwen2.5-coder-32b",
        "openrouter/meta-llama/codellama-70b",
    ],
    "math": [
        "deepseek/deepseek-math",
        "qwen/qwen2.5-math-72b",
        "openrouter/meta-llama/llama-3.1-70b",
    ],
    "reasoning": [
        "deepseek/deepseek-reasoner",
        "qwen/qwen2.5-72b",
        "openrouter/meta-llama/llama-3.1-70b",
    ],
    "creative": [
        "qwen/qwen2.5-72b",
        "deepseek/deepseek-chat",
        "openrouter/mistralai/mistral-large",
    ],
    "general": [
        "deepseek/deepseek-chat",
        "qwen/qwen2.5-72b",
        "openrouter/meta-llama/llama-3.1-70b",
        "openrouter/google/gemma-2-27b",
    ],
    "judge": [
        "deepseek/deepseek-chat",
        "qwen/qwen2.5-72b",
    ],
}

# Provider extraction: "provider/model_id" → "provider"
def _get_provider(model_path: str) -> str:
    if "/" in model_path:
        return model_path.split("/", 1)[0]
    return "unknown"


# ---------------------------------------------------------------------------
# prompt analysis
# ---------------------------------------------------------------------------
@dataclass
class PromptAnalysis:
    """Result of prompt analysis."""
    intent: str = "general"        # code / math / creative / reasoning / factual / chat
    complexity: str = "medium"     # simple / medium / complex
    domain: str = "general"        # programming / science / writing / general
    language: str = "en"           # zh / en / mixed
    estimated_tokens: int = 200
    needs_reasoning: bool = False
    needs_creativity: bool = False
    needs_accuracy: bool = True
    needs_code: bool = False
    prompt_length: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "complexity": self.complexity,
            "domain": self.domain,
            "language": self.language,
            "estimated_tokens": self.estimated_tokens,
            "needs_reasoning": self.needs_reasoning,
            "needs_creativity": self.needs_creativity,
            "needs_accuracy": self.needs_accuracy,
            "needs_code": self.needs_code,
            "prompt_length": self.prompt_length,
        }


# Intent keywords — ordered by specificity (most specific first)
_INTENT_PATTERNS: List[Tuple[str, List[str]]] = [
    ("code", [
        "def ", "class ", "function", "import ", "bug", "compile", "regex",
        "code:", "api endpoint", "rest api", "sql", "query", "algorithm", "refactor",
        "代码", "函数", "编程", "调试", "编译", "算法", "接口",
    ]),
    ("math", [
        "prove", "derivative", "integral", "积分", "求导", "limit",
        "equation", "calculate", "compute", "matrix", "vector", "概率",
        "统计", "证明", "求解", "方程",
    ]),
    ("creative", [
        "write a story", "poem", "creative", "essay", "novel", "screenplay",
        "写一首", "写一篇", "创作", "故事", "小说", "诗歌", "散文",
    ]),
    ("reasoning", [
        "analyze", "compare", "evaluate", "why", "explain why",
        "step by step", "reasoning", "logic", "deduce", "infer",
        "分析", "比较", "评估", "推理", "逻辑", "推导", "逐步",
    ]),
    ("factual", [
        "what is", "who is", "when did", "where is", "how many",
        "define", "list", "fact",
        "什么是", "是谁", "什么时候", "在哪里", "多少", "定义",
    ]),
]


def _detect_language(text: str) -> str:
    """Detect if text is Chinese, English, or mixed."""
    if not text:
        return "en"
    cjk_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    alpha_count = sum(1 for c in text if c.isalpha() and ord(c) < 128)
    total = cjk_count + alpha_count
    if total == 0:
        return "en"
    cjk_ratio = cjk_count / total
    if cjk_ratio > 0.6:
        return "zh"
    elif cjk_ratio > 0.15:
        return "mixed"
    return "en"


def _estimate_tokens(text: str) -> int:
    """Rough token estimation."""
    if not text:
        return 0
    # CJK: ~1.5 chars per token; Latin: ~4 chars per token
    cjk_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    latin_count = len(text) - cjk_count
    return int(cjk_count / 1.5 + latin_count / 4)


def _estimate_complexity(prompt: str, intent: str) -> str:
    """Estimate prompt complexity."""
    # Use effective length — CJK chars count as ~2 Latin chars
    cjk_count = sum(1 for c in prompt if '\u4e00' <= c <= '\u9fff')
    latin_count = len(prompt) - cjk_count
    effective_length = cjk_count * 2 + latin_count
    token_est = _estimate_tokens(prompt)

    # Multi-part questions (indicated by ? count or numbered lists)
    question_marks = prompt.count("?") + prompt.count("？")
    has_numbered = bool(re.search(r'^\s*\d+\.\s', prompt, re.MULTILINE))
    has_code_block = "```" in prompt

    complexity_score = 0
    # Use effective length for CJK-aware complexity
    if effective_length > 500:
        complexity_score += 2
    elif effective_length > 150:
        complexity_score += 1
    if token_est > 100:
        complexity_score += 1
    if question_marks > 1:
        complexity_score += 1
    if has_numbered:
        complexity_score += 1
    if has_code_block:
        complexity_score += 1
    # Intent-based complexity bumps
    if intent in ("reasoning", "math"):
        complexity_score += 2  # reasoning/math are inherently complex
    if intent == "code":
        complexity_score += 1
    if intent == "creative":
        complexity_score += 1

    if complexity_score >= 4:
        return "complex"
    elif complexity_score >= 2:
        return "medium"
    return "simple"


def analyze_prompt(prompt: str) -> PromptAnalysis:
    """Analyze a prompt and return its characteristics.

    This is the main entry point for prompt analysis.  Uses keyword
    matching + heuristics — no API calls needed.
    """
    if not prompt:
        return PromptAnalysis()

    prompt_lower = prompt.lower()

    # -- Intent classification --
    intent = "general"
    for tag, keywords in _INTENT_PATTERNS:
        for kw in keywords:
            if kw.lower() in prompt_lower:
                intent = tag
                break
        if intent != "general":
            break

    # Default: if it's a question, treat as factual
    if intent == "general" and ("?" in prompt or "？" in prompt):
        intent = "factual"
    # If it's very short and no specific intent, treat as chat
    if intent == "general" and len(prompt) < 30:
        intent = "chat"

    # -- Complexity --
    complexity = _estimate_complexity(prompt, intent)

    # -- Language --
    language = _detect_language(prompt)

    # -- Domain --
    domain = "general"
    if intent == "code":
        domain = "programming"
    elif intent == "math":
        domain = "science"
    elif intent == "creative":
        domain = "writing"

    # -- Needs flags --
    needs_code = intent == "code" or "```" in prompt or "def " in prompt
    needs_reasoning = intent in ("reasoning", "math") or complexity == "complex"
    needs_creativity = intent == "creative"
    needs_accuracy = intent in ("factual", "math", "code", "reasoning")

    # -- Token estimation --
    estimated_tokens = _estimate_tokens(prompt)

    return PromptAnalysis(
        intent=intent,
        complexity=complexity,
        domain=domain,
        language=language,
        estimated_tokens=estimated_tokens,
        needs_reasoning=needs_reasoning,
        needs_creativity=needs_creativity,
        needs_accuracy=needs_accuracy,
        needs_code=needs_code,
        prompt_length=len(prompt),
    )


# ---------------------------------------------------------------------------
# operator selection
# ---------------------------------------------------------------------------
def select_operator(analysis: PromptAnalysis) -> str:
    """Select the best fusion operator based on prompt analysis.

    Decision matrix:
        simple + factual/chat  → expert (single model, cheapest)
        simple + code/math     → expert (one strong model suffices)
        medium + creative      → vote (diverse perspectives)
        medium + factual       → expert (one good model is enough)
        medium + reasoning     → pipeline (stepwise)
        complex + any          → pipeline (decompose → solve → verify)
        quality-critical       → vote + refine (best of N, then polish)

    Returns one of: 'expert', 'vote', 'pipeline'
    """
    # Complex prompts always get pipeline
    if analysis.complexity == "complex":
        return "pipeline"

    # Medium complexity
    if analysis.complexity == "medium":
        if analysis.intent == "creative":
            return "vote"
        if analysis.needs_reasoning:
            return "pipeline"
        # Medium factual/code → expert with a strong model
        return "expert"

    # Simple prompts
    if analysis.intent in ("chat", "factual"):
        return "expert"
    if analysis.intent == "code":
        return "expert"
    if analysis.intent == "creative":
        return "vote"

    return "expert"


# ---------------------------------------------------------------------------
# model selection
# ---------------------------------------------------------------------------
@dataclass
class ModelInfo:
    """Lightweight model info for selection."""
    path: str               # "provider/model_id"
    provider: str
    tags: List[str] = field(default_factory=list)  # capability tags
    is_free: bool = False
    health_state: str = "healthy"
    score: float = 0.0


def _get_models_from_engine() -> List[ModelInfo]:
    """Query the engine registry for available models.

    Returns empty list if engine is not available.
    """
    try:
        from .engine import engine
        registry = engine.registry

        models: List[ModelInfo] = []

        # Try to get model list from registry
        # The registry interface varies by SMR version, so we try multiple approaches
        if hasattr(registry, 'list_models'):
            raw_models = registry.list_models()
        elif hasattr(registry, 'models'):
            raw_models = registry.models
        elif hasattr(registry, '_models'):
            raw_models = registry._models
        else:
            LOG.warning("fusion_composer: registry has no model list method")
            return []

        for entry in raw_models:
            if isinstance(entry, str):
                # Simple string path
                models.append(ModelInfo(
                    path=entry,
                    provider=_get_provider(entry),
                ))
            elif isinstance(entry, dict):
                path = entry.get("path") or entry.get("model") or entry.get("id", "")
                if not path or "/" not in path:
                    continue
                models.append(ModelInfo(
                    path=path,
                    provider=_get_provider(path),
                    tags=entry.get("tags", []),
                    is_free=entry.get("is_free", False),
                ))
            elif hasattr(entry, 'path'):
                path = entry.path
                models.append(ModelInfo(
                    path=path,
                    provider=_get_provider(path),
                    tags=getattr(entry, 'tags', []),
                    is_free=getattr(entry, 'is_free', False),
                ))

        LOG.info("fusion_composer: found %d models in registry", len(models))
        return models
    except ImportError:
        LOG.debug("fusion_composer: engine not available, using fallback pool")
        return []
    except Exception as e:
        LOG.warning("fusion_composer: failed to query registry: %s", e)
        return []


def _filter_healthy(models: List[ModelInfo]) -> List[ModelInfo]:
    """Filter out unhealthy models using model_health manager."""
    try:
        from .model_health import get_model_health_manager
        mhm = get_model_health_manager()

        healthy: List[ModelInfo] = []
        for m in models:
            if mhm.should_skip(m.path):
                m.health_state = "skip"
                continue
            m.health_state = "healthy"
            healthy.append(m)

        skipped = len(models) - len(healthy)
        if skipped > 0:
            LOG.info("fusion_composer: filtered %d unhealthy models", skipped)
        return healthy
    except Exception:
        # If model_health is not available, assume all are healthy
        return models


def _score_model(model: ModelInfo, analysis: PromptAnalysis) -> float:
    """Score a model's suitability for the given prompt analysis.

    Higher score = better match.
    """
    score = 0.5  # base score

    # Intent matching
    intent_tag = analysis.intent
    if intent_tag in model.tags:
        score += 0.3
    if analysis.domain in model.tags:
        score += 0.2

    # Free models get bonus for fan-out (cost efficiency)
    if model.is_free:
        score += 0.15

    # Reasoning prompts prefer reasoning-capable models
    if analysis.needs_reasoning:
        if "reasoning" in model.tags or "reasoner" in model.path.lower():
            score += 0.2

    # Code prompts prefer code models
    if analysis.needs_code:
        if "code" in model.tags or "coder" in model.path.lower():
            score += 0.25

    # Language preference: Chinese prompts prefer models with CJK support
    if analysis.language == "zh":
        zh_friendly = any(p in model.path.lower() for p in [
            "qwen", "deepseek", "glm", "baichuan", "yi-",
        ])
        if zh_friendly:
            score += 0.1

    model.score = score
    return score


def _ensure_diversity(
    scored: List[ModelInfo],
    n: int,
) -> List[ModelInfo]:
    """Select top-N models ensuring provider diversity.

    Greedily selects highest-scoring models while avoiding too many
    from the same provider (max 2 per provider for vote groups).
    """
    selected: List[ModelInfo] = []
    provider_counts: Dict[str, int] = {}
    max_per_provider = 2 if n > 2 else 1

    for m in scored:
        if len(selected) >= n:
            break
        count = provider_counts.get(m.provider, 0)
        if count < max_per_provider:
            selected.append(m)
            provider_counts[m.provider] = count + 1

    # If we couldn't get enough with diversity constraint, relax it
    if len(selected) < n:
        for m in scored:
            if m not in selected:
                selected.append(m)
                if len(selected) >= n:
                    break

    return selected[:n]


async def select_models(
    analysis: PromptAnalysis,
    operator: str,
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[List[str], Optional[str]]:
    """Select models for the fusion plan.

    Returns (model_ids, judge_model) where:
        - model_ids: list of "provider/model_id" for fan-out
        - judge_model: "provider/model_id" for judge/refine (or None)

    Args:
        analysis: Prompt analysis result
        operator: Selected operator type
        config: Optional configuration with model_pool overrides
    """
    config = config or {}
    model_pool = config.get("model_pool", _DEFAULT_MODEL_POOL)
    judge_model = config.get("judge_model", DEFAULT_JUDGE_MODEL)
    max_vote = config.get("max_vote_models", DEFAULT_MAX_VOTE_MODELS)
    min_vote = config.get("min_vote_models", DEFAULT_MIN_VOTE_MODELS)

    # Try to get models from engine registry
    registry_models = _get_models_from_engine()

    if registry_models:
        # Filter unhealthy
        healthy = _filter_healthy(registry_models)

        if healthy:
            # Score each model
            for m in healthy:
                _score_model(m, analysis)
            healthy.sort(key=lambda m: m.score, reverse=True)

            if operator == "expert":
                # Pick the single best model
                best = healthy[0]
                return [best.path], judge_model

            elif operator == "vote":
                # Pick N diverse models
                # Determine count based on complexity
                if analysis.complexity == "complex":
                    n = min(max_vote, len(healthy))
                elif analysis.complexity == "medium":
                    n = min(3, len(healthy))
                else:
                    n = min(min_vote, len(healthy))
                n = max(min_vote, n)

                selected = _ensure_diversity(healthy, n)
                model_ids = [m.path for m in selected]
                return model_ids, judge_model

            elif operator == "pipeline":
                # Pick models for pipeline steps:
                # Step 1: best general model (analyzer)
                # Step 2: best domain model (solver)
                # Step 3: best reasoning model (verifier)
                selected: List[ModelInfo] = []

                # Best overall
                if healthy:
                    selected.append(healthy[0])

                # Best for domain
                domain_models = [m for m in healthy if analysis.domain in m.tags or analysis.intent in m.tags]
                if domain_models and domain_models[0] not in selected:
                    selected.append(domain_models[0])
                elif len(healthy) > 1:
                    selected.append(healthy[1])

                # Best for reasoning
                if analysis.needs_reasoning:
                    reason_models = [m for m in healthy if "reasoning" in m.tags or "reasoner" in m.path.lower()]
                    if reason_models and reason_models[0] not in selected:
                        selected.append(reason_models[0])

                model_ids = [m.path for m in selected] if selected else [healthy[0].path]
                return model_ids, judge_model

    # Fallback: use configurable model pool
    LOG.info("fusion_composer: using fallback model pool")

    intent_key = analysis.intent if analysis.intent in model_pool else "general"
    pool = model_pool.get(intent_key, model_pool.get("general", []))

    if not pool:
        pool = model_pool.get("general", [DEFAULT_JUDGE_MODEL])

    if operator == "expert":
        return [pool[0]], judge_model

    elif operator == "vote":
        if analysis.complexity == "complex":
            n = min(max_vote, len(pool))
        elif analysis.complexity == "medium":
            n = min(3, len(pool))
        else:
            n = min(min_vote, len(pool))
        n = max(min_vote, min(n, len(pool)))
        return pool[:n], judge_model

    elif operator == "pipeline":
        # Use first 3 models (or all if fewer)
        return pool[:min(3, len(pool))], judge_model

    return [pool[0]], judge_model


# ---------------------------------------------------------------------------
# plan building
# ---------------------------------------------------------------------------
def build_plan(
    operator: str,
    model_ids: List[str],
    judge_model: Optional[str],
    analysis: PromptAnalysis,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a FusionStep config dict for the composed plan.

    Returns a dict that can be passed to FusionStep.from_dict().
    """
    config = config or {}
    max_tokens = int(config.get("max_tokens", DEFAULT_VOTE_MAX_TOKENS))
    timeout = float(config.get("timeout", 90.0))

    if operator == "expert":
        # Expert: single model, classified by intent
        experts: Dict[str, str] = {}
        if model_ids:
            experts[analysis.intent] = model_ids[0]
            experts["default"] = model_ids[0]
        return {
            "type": "expert",
            "experts": experts,
            "max_tokens": max_tokens,
            "timeout": timeout,
        }

    elif operator == "vote":
        # Vote: fan out to N models, reduce with best_pick
        strategy = "best_pick"
        # For simple prompts, use first_ok (fastest, cheapest)
        if analysis.complexity == "simple":
            strategy = "first_ok"
        # For creative, use concat (show all perspectives)
        elif analysis.intent == "creative" and analysis.complexity != "complex":
            strategy = "majority"

        min_candidates = 1 if analysis.complexity == "simple" else 2

        return {
            "type": "vote",
            "model_ids": model_ids,
            "strategy": strategy,
            "judge_model": judge_model,
            "max_tokens": max_tokens,
            "timeout": timeout,
            "min_candidates": min_candidates,
        }

    elif operator == "pipeline":
        # Pipeline: stepwise processing
        # Step 1: Analyze the prompt
        # Step 2: Generate the answer
        # Step 3: Refine/verify
        steps: List[Dict[str, Any]] = []

        if len(model_ids) >= 1:
            # Step 1: Analyze
            steps.append({
                "type": "expert",
                "experts": {"default": model_ids[0]},
                "max_tokens": max_tokens,
                "timeout": timeout,
            })

        if len(model_ids) >= 2:
            # Step 2: Solve (use second model or same)
            solver = model_ids[1] if len(model_ids) > 1 else model_ids[0]
            steps.append({
                "type": "expert",
                "experts": {"default": solver},
                "max_tokens": max_tokens,
                "timeout": timeout,
            })

        # Step 3: Refine with judge
        if judge_model:
            steps.append({
                "type": "refine",
                "judge_model": judge_model,
                "instruction": (
                    "Review and refine the above answer for accuracy, "
                    "completeness, and clarity. Fix any errors and improve "
                    "the structure. Return only the improved answer."
                ),
                "max_tokens": max_tokens,
                "timeout": timeout,
            })

        return {
            "type": "pipeline",
            "steps": steps,
            "max_tokens": max_tokens,
            "timeout": timeout,
        }

    # Fallback: simple expert
    return {
        "type": "expert",
        "experts": {"default": model_ids[0]} if model_ids else {},
        "max_tokens": max_tokens,
        "timeout": timeout,
    }


# ---------------------------------------------------------------------------
# composed plan
# ---------------------------------------------------------------------------
@dataclass
class ComposedPlan:
    """A dynamically composed fusion plan."""
    plan_id: str                           # "auto_<hash>"
    config: Dict[str, Any]                 # FusionStep config
    analysis: PromptAnalysis
    operator: str
    selected_models: List[str]
    judge_model: Optional[str]
    estimated_cost: float = 0.0            # estimated API calls
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "config": self.config,
            "analysis": self.analysis.to_dict(),
            "operator": self.operator,
            "selected_models": self.selected_models,
            "judge_model": self.judge_model,
            "estimated_cost": self.estimated_cost,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# main composer
# ---------------------------------------------------------------------------
class AutoFusionComposer:
    """Intelligently composes fusion plans based on prompt analysis.

    Usage:
        composer = AutoFusionComposer()
        plan = await composer.compose(prompt, history)
        # plan.config can be passed to FusionRouter.run_plan()
        # Or use FusionRouter.run_auto() which handles this internally.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._config = config or {}
        self._model_pool = self._config.get("model_pool", _DEFAULT_MODEL_POOL)
        self._judge_model = self._config.get("judge_model", DEFAULT_JUDGE_MODEL)
        self._baseline_model = self._config.get("baseline_model", DEFAULT_BASELINE_MODEL)
        self._max_vote = self._config.get("max_vote_models", DEFAULT_MAX_VOTE_MODELS)
        self._min_vote = self._config.get("min_vote_models", DEFAULT_MIN_VOTE_MODELS)
        self._compose_count = 0
        self._compose_cache: Dict[str, ComposedPlan] = {}  # prompt_hash → plan

    async def compose(
        self,
        prompt: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> ComposedPlan:
        """Compose a fusion plan for the given prompt.

        This is the main entry point.  Analyzes the prompt, selects the
        operator and models, and builds the plan config.
        """
        history = history or []

        # Check cache — same prompt gets same plan (avoids re-analysis)
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        if prompt_hash in self._compose_cache:
            cached = self._compose_cache[prompt_hash]
            LOG.debug("fusion_composer: cache hit for prompt hash=%s", prompt_hash)
            return cached

        # 1. Analyze prompt
        analysis = analyze_prompt(prompt)
        LOG.info(
            "fusion_composer: analyzed prompt intent=%s complexity=%s domain=%s lang=%s",
            analysis.intent, analysis.complexity, analysis.domain, analysis.language,
        )

        # 2. Select operator
        operator = select_operator(analysis)
        LOG.info("fusion_composer: selected operator=%s", operator)

        # 3. Select models
        select_config = {
            "model_pool": self._model_pool,
            "judge_model": self._judge_model,
            "max_vote_models": self._max_vote,
            "min_vote_models": self._min_vote,
        }
        model_ids, judge = await select_models(analysis, operator, select_config)
        LOG.info(
            "fusion_composer: selected %d models: %s (judge=%s)",
            len(model_ids), model_ids, judge,
        )

        # 4. Build plan config
        plan_config = build_plan(operator, model_ids, judge, analysis, self._config)

        # 5. Estimate cost (number of API calls)
        if operator == "expert":
            estimated_cost = 1.0
        elif operator == "vote":
            estimated_cost = len(model_ids)
            if plan_config.get("strategy") == "best_pick" and judge:
                estimated_cost += 1  # judge call
        elif operator == "pipeline":
            estimated_cost = len(plan_config.get("steps", []))
        else:
            estimated_cost = 1.0

        plan_id = f"auto_{prompt_hash[:12]}"

        composed = ComposedPlan(
            plan_id=plan_id,
            config=plan_config,
            analysis=analysis,
            operator=operator,
            selected_models=model_ids,
            judge_model=judge,
            estimated_cost=estimated_cost,
        )

        # Cache the composed plan
        self._compose_cache[prompt_hash] = composed
        # Keep cache bounded
        if len(self._compose_cache) > 100:
            # Remove oldest entry
            oldest = min(self._compose_cache.values(), key=lambda p: p.created_at)
            self._compose_cache.pop(oldest.plan_id.replace("auto_", "")[:16], None)

        self._compose_count += 1
        LOG.info(
            "fusion_composer: composed plan=%s operator=%s models=%d cost=%.1f",
            plan_id, operator, len(model_ids), estimated_cost,
        )

        return composed

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_composed": self._compose_count,
            "cache_size": len(self._compose_cache),
            "config": {
                "judge_model": self._judge_model,
                "baseline_model": self._baseline_model,
                "max_vote_models": self._max_vote,
                "min_vote_models": self._min_vote,
            },
        }

    def clear_cache(self) -> None:
        self._compose_cache.clear()


# ---------------------------------------------------------------------------
# module-level singleton
# ---------------------------------------------------------------------------
_composer: Optional[AutoFusionComposer] = None


def init_fusion_composer(config: Optional[Dict[str, Any]] = None) -> AutoFusionComposer:
    global _composer
    if _composer is None:
        _composer = AutoFusionComposer(config)
    return _composer


def get_fusion_composer() -> Optional[AutoFusionComposer]:
    return _composer


def reset_fusion_composer() -> None:
    global _composer
    _composer = None
