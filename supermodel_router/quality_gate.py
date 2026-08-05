"""
quality_gate.py — Output quality validation and assurance for Fusion.

Ensures fusion output meets minimum quality standards (not worse than
the configured baseline model, e.g. deepseek v4-flash).

Multi-layer quality assessment:
    Layer 1 — Static checks (zero API cost):
        - Emptiness / too short
        - Truncation detection (ends mid-sentence)
        - Repetition detection (same phrase repeated)
        - Error markers ([fusion_error], [fusion_timeout], etc.)
        - Garbled text (high non-text character ratio)
        - Language consistency (prompt zh → answer should have zh)

    Layer 2 — Semantic checks (zero API cost):
        - Keyword overlap with prompt (relevance)
        - Response length vs prompt complexity (adequacy)
        - Structural completeness (has code block if asked, has steps if asked)

    Layer 3 — Baseline fallback (1 API call, only if Layer 1+2 fail):
        - Invoke baseline model (deepseek v4-flash equivalent)
        - Compare and return the better response

Quality score: 0.0 (worst) to 1.0 (best).
Pass threshold configurable, default 0.6.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# defaults
# ---------------------------------------------------------------------------
DEFAULT_BASELINE_MODEL = "openrouter/qwen/qwen-2.5-72b-instruct"  # empirically stable; produces real content via OpenRouter free routing
DEFAULT_MIN_SCORE = 0.55          # minimum quality score to pass
DEFAULT_MIN_LENGTH = 30           # minimum answer length in chars
DEFAULT_MAX_REPETITION_RATIO = 0.35  # max ratio of repeated n-grams
DEFAULT_BASELINE_TIMEOUT = 90.0   # aligned with fusion 90s constraint
DEFAULT_BASELINE_MAX_TOKENS = 2048

# Error markers that indicate fusion failure
_ERROR_MARKERS = [
    "[fusion_error", "[fusion_timeout", "[fusion_failed",
    "[fusion_run_failed",  # critical: full chain exhausted
    "[unknown plan", "fusion_plan_timeout",
    "all_fallbacks_exhausted",
    "RuntimeError(",
    "Traceback (most recent call last)",
]

# Truncation indicators: response ends without proper closure
_TRUNCATION_PATTERNS = [
    re.compile(r"[a-zA-Z,;:]\s*$"),         # ends with letter or punctuation mid-sentence
    re.compile(r"\w+\s*$"),                  # ends with word, no period/newline
]


# ---------------------------------------------------------------------------
# data classes
# ---------------------------------------------------------------------------
@dataclass
class QualityResult:
    """Result of quality assessment."""
    score: float = 0.0           # 0.0 - 1.0
    passed: bool = False
    reason: str = ""
    checks: Dict[str, Any] = field(default_factory=dict)
    fallback_used: bool = False
    fallback_model: Optional[str] = None
    original_score: float = 0.0  # score before fallback
    elapsed_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "passed": self.passed,
            "reason": self.reason,
            "checks": self.checks,
            "fallback_used": self.fallback_used,
            "fallback_model": self.fallback_model,
            "original_score": round(self.original_score, 4),
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


@dataclass
class QualityStats:
    """Running quality statistics."""
    total_assessed: int = 0
    total_passed: int = 0
    total_fallbacks: int = 0
    total_baseline_calls: int = 0
    avg_score: float = 0.0
    _score_sum: float = 0.0

    def record(self, result: QualityResult) -> None:
        self.total_assessed += 1
        self._score_sum += result.score
        self.avg_score = self._score_sum / self.total_assessed
        if result.passed:
            self.total_passed += 1
        if result.fallback_used:
            self.total_fallbacks += 1

    @property
    def pass_rate(self) -> float:
        return self.total_passed / self.total_assessed if self.total_assessed else 0.0

    @property
    def fallback_rate(self) -> float:
        return self.total_fallbacks / self.total_assessed if self.total_assessed else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_assessed": self.total_assessed,
            "total_passed": self.total_passed,
            "total_fallbacks": self.total_fallbacks,
            "total_baseline_calls": self.total_baseline_calls,
            "avg_score": round(self.avg_score, 4),
            "pass_rate": round(self.pass_rate, 4),
            "fallback_rate": round(self.fallback_rate, 4),
        }


# ---------------------------------------------------------------------------
# static check helpers
# ---------------------------------------------------------------------------
def _check_emptiness(answer: str) -> Tuple[float, str]:
    """Check if answer is empty or too short."""
    if not answer or not answer.strip():
        return 0.0, "empty_response"
    stripped = answer.strip()
    if len(stripped) < 10:
        return 0.1, f"too_short ({len(stripped)} chars)"
    if len(stripped) < DEFAULT_MIN_LENGTH:
        return 0.3, f"short_response ({len(stripped)} chars)"
    return 1.0, "ok"


def _check_error_markers(answer: str) -> Tuple[float, str]:
    """Check for fusion error markers in the answer."""
    for marker in _ERROR_MARKERS:
        if marker in answer:
            return 0.0, f"error_marker: {marker}"
    return 1.0, "ok"


def _check_truncation(answer: str) -> Tuple[float, str]:
    """Detect if the answer appears truncated."""
    stripped = answer.rstrip()
    if not stripped:
        return 0.0, "empty"

    # Check if ends with proper closure
    last_char = stripped[-1]

    # Proper endings: period, exclamation, question mark, closing bracket/paren, newline, code block
    proper_endings = {'.', '!', '?', ')', ']', '}', '"', "'", '`', '>', '\n'}
    if last_char in proper_endings:
        return 1.0, "ok"

    # Check if it's a code block end
    if stripped.endswith('```'):
        return 1.0, "ok"

    # Check if it's a list item or table row (acceptable)
    if last_char in {'-', '|', ':', ','}:
        return 0.7, "possibly_truncated"

    # Ends with alphanumeric — likely truncated
    if last_char.isalnum():
        return 0.4, "likely_truncated"

    return 0.8, "possibly_truncated"


def _check_repetition(answer: str) -> Tuple[float, str]:
    """Detect excessive repetition in the answer."""
    if not answer or len(answer) < 50:
        return 1.0, "ok"

    # Check for repeated sentences/lines
    lines = [l.strip() for l in answer.split('\n') if l.strip()]
    if len(lines) > 3:
        line_counts = Counter(lines)
        max_repeat = max(line_counts.values())
        if max_repeat > 2:
            repeat_ratio = sum(c for c in line_counts.values() if c > 1) / len(lines)
            if repeat_ratio > DEFAULT_MAX_REPETITION_RATIO:
                return 0.2, f"repetitive_lines (ratio={repeat_ratio:.2f})"
            return 0.6, f"some_repetition (max={max_repeat})"

    # Check for repeated phrases (5-gram)
    words = answer.split()
    if len(words) > 20:
        ngrams = [' '.join(words[i:i+5]) for i in range(len(words) - 4)]
        ngram_counts = Counter(ngrams)
        max_ngram_repeat = max(ngram_counts.values())
        if max_ngram_repeat > 3:
            return 0.3, f"repetitive_phrases (max_repeat={max_ngram_repeat})"

    return 1.0, "ok"


def _check_garbled(answer: str) -> Tuple[float, str]:
    """Check for garbled or corrupted text."""
    if not answer:
        return 0.0, "empty"

    # Count printable vs non-printable characters
    total = len(answer)
    printable = sum(1 for c in answer if c.isprintable() or c in '\n\r\t')
    non_printable_ratio = 1.0 - (printable / total) if total > 0 else 1.0

    if non_printable_ratio > 0.1:
        return 0.1, f"garbled (non_printable={non_printable_ratio:.2f})"

    # Check for excessive special characters (not counting code blocks)
    # Remove code blocks first
    text_only = re.sub(r'```[\s\S]*?```', '', answer)
    text_only = re.sub(r'`[^`]+`', '', text_only)

    if text_only:
        alpha_count = sum(1 for c in text_only if c.isalpha())
        special_count = sum(1 for c in text_only if not c.isalnum() and not c.isspace() and c not in '.,;:!?\'"()-')
        special_ratio = special_count / len(text_only) if text_only else 0
        if special_ratio > 0.3:
            return 0.4, f"excessive_special_chars (ratio={special_ratio:.2f})"

    return 1.0, "ok"


# ---------------------------------------------------------------------------
# semantic check helpers
# ---------------------------------------------------------------------------
def _check_relevance(prompt: str, answer: str) -> Tuple[float, str]:
    """Check if the answer is relevant to the prompt using keyword overlap."""
    if not prompt or not answer:
        return 0.5, "no_prompt"

    # Extract keywords from prompt (simple approach)
    prompt_lower = prompt.lower()
    answer_lower = answer.lower()

    # Get significant words from prompt (> 3 chars, not stopwords)
    _STOPWORDS = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
        'should', 'may', 'might', 'must', 'shall', 'can', 'need', 'this',
        'that', 'these', 'those', 'with', 'for', 'from', 'about', 'into',
        'through', 'during', 'before', 'after', 'above', 'below', 'under',
        'your', 'you', 'your', 'what', 'how', 'why', 'when', 'where', 'which',
        'who', 'whom', 'whose', 'please', 'help', 'want', 'need', 'tell',
        'give', 'make', 'write', 'create', '的', '了', '是', '在', '我', '你',
        '他', '她', '它', '们', '和', '与', '或', '请', '帮', '给', '写',
    }

    # For CJK text, use character-level matching
    has_cjk = any('\u4e00' <= c <= '\u9fff' for c in prompt)

    if has_cjk:
        # Extract CJK characters as keywords
        cjk_chars = set(c for c in prompt_lower if '\u4e00' <= c <= '\u9fff')
        if cjk_chars:
            matched = sum(1 for c in cjk_chars if c in answer_lower)
            overlap = matched / len(cjk_chars) if cjk_chars else 0
            if overlap < 0.1:
                return 0.3, f"low_relevance (cjk_overlap={overlap:.2f})"
            return min(1.0, 0.5 + overlap * 0.5), f"cjk_overlap={overlap:.2f}"

    # For Latin text, use word-level matching
    prompt_words = set(
        w.strip('.,!?;:"\'()[]{}')
        for w in prompt_lower.split()
        if len(w) > 3 and w not in _STOPWORDS
    )

    if not prompt_words:
        return 0.7, "no_keywords"

    matched = sum(1 for w in prompt_words if w in answer_lower)
    overlap = matched / len(prompt_words) if prompt_words else 0

    if overlap == 0:
        return 0.2, "no_keyword_overlap"
    elif overlap < 0.1:
        return 0.4, f"low_overlap ({overlap:.2f})"
    elif overlap < 0.3:
        return 0.7, f"moderate_overlap ({overlap:.2f})"
    else:
        return 1.0, f"good_overlap ({overlap:.2f})"


def _check_adequacy(prompt: str, answer: str) -> Tuple[float, str]:
    """Check if the answer length is adequate relative to the prompt."""
    if not answer:
        return 0.0, "empty"

    prompt_len = len(prompt) if prompt else 0
    answer_len = len(answer.strip())

    # Very short prompts (greetings, simple questions) — short answers OK
    if prompt_len < 20:
        if answer_len >= 10:
            return 1.0, "adequate"
        return 0.5, "short_for_simple_prompt"

    # Medium prompts — expect at least some substance
    if prompt_len < 200:
        if answer_len >= 50:
            return 1.0, "adequate"
        if answer_len >= 20:
            return 0.6, "brief"
        return 0.3, "too_brief"

    # Long/complex prompts — expect detailed answer
    if answer_len >= 200:
        return 1.0, "detailed"
    if answer_len >= 100:
        return 0.7, "moderate"
    return 0.3, f"too_brief_for_complex ({answer_len} chars)"


def _check_structure(prompt: str, answer: str) -> Tuple[float, str]:
    """Check structural completeness — does the answer have expected format?"""
    if not answer:
        return 0.0, "empty"

    prompt_lower = prompt.lower() if prompt else ""

    # If prompt asks for code, check if answer has code block
    code_indicators = ['code', 'function', 'def ', 'class ', 'implement', 'write a script',
                       '代码', '函数', '实现', '编写']
    asks_code = any(ind in prompt_lower for ind in code_indicators)
    has_code = '```' in answer or 'def ' in answer or 'function ' in answer or 'class ' in answer
    if asks_code and not has_code:
        return 0.0, "missing_code_block"

    # If prompt asks for steps/list, check if answer has list structure
    list_indicators = ['steps', 'list', 'enumerate', 'steps to', 'how to',
                       '步骤', '列出', '列举', '如何']
    asks_list = any(ind in prompt_lower for ind in list_indicators)
    has_list = bool(re.search(r'^\s*[\d\-•*\u2022]\s', answer, re.MULTILINE))
    if asks_list and not has_list:
        return 0.5, "missing_list_structure"

    # If prompt asks for explanation, check if answer has paragraphs
    explain_indicators = ['explain', 'describe', 'why', 'what is', '分析', '解释', '说明', '为什么']
    asks_explain = any(ind in prompt_lower for ind in explain_indicators)
    has_paragraphs = answer.count('\n\n') >= 1 or len(answer) > 100
    if asks_explain and not has_paragraphs:
        return 0.4, "brief_explanation"

    return 1.0, "ok"


# ---------------------------------------------------------------------------
# main quality gate
# ---------------------------------------------------------------------------
class QualityGate:
    """Validates fusion output meets minimum quality standards.

    Usage:
        gate = QualityGate()
        result = await gate.assess(prompt, answer, trace)
        if not result.passed:
            answer, result = await gate.ensure_quality(
                prompt, answer, trace, history
            )
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        config = config or {}
        self.baseline_model: str = config.get(
            "baseline_model", DEFAULT_BASELINE_MODEL
        )
        self.min_score: float = float(config.get("min_score", DEFAULT_MIN_SCORE))
        self.min_length: int = int(config.get("min_length", DEFAULT_MIN_LENGTH))
        self.enable_fallback: bool = config.get("enable_fallback", True)
        self.baseline_timeout: float = float(
            config.get("baseline_timeout", DEFAULT_BASELINE_TIMEOUT)
        )
        self.baseline_max_tokens: int = int(
            config.get("baseline_max_tokens", DEFAULT_BASELINE_MAX_TOKENS)
        )
        self.stats = QualityStats()

    # -- Layer 1 + 2: assessment --

    async def assess(
        self,
        prompt: str,
        answer: str,
        trace: Optional[List[Dict[str, Any]]] = None,
    ) -> QualityResult:
        """Assess answer quality. Returns QualityResult with score and checks."""
        t0 = time.perf_counter()
        trace = trace or []

        checks: Dict[str, Any] = {}

        # Layer 1: Static checks
        s_emptiness, r_emptiness = _check_emptiness(answer)
        checks["emptiness"] = {"score": s_emptiness, "detail": r_emptiness}

        s_error, r_error = _check_error_markers(answer)
        checks["error_markers"] = {"score": s_error, "detail": r_error}

        s_truncation, r_truncation = _check_truncation(answer)
        checks["truncation"] = {"score": s_truncation, "detail": r_truncation}

        s_repetition, r_repetition = _check_repetition(answer)
        checks["repetition"] = {"score": s_repetition, "detail": r_repetition}

        s_garbled, r_garbled = _check_garbled(answer)
        checks["garbled"] = {"score": s_garbled, "detail": r_garbled}

        # Layer 2: Semantic checks
        s_relevance, r_relevance = _check_relevance(prompt, answer)
        checks["relevance"] = {"score": s_relevance, "detail": r_relevance}

        s_adequacy, r_adequacy = _check_adequacy(prompt, answer)
        checks["adequacy"] = {"score": s_adequacy, "detail": r_adequacy}

        s_structure, r_structure = _check_structure(prompt, answer)
        checks["structure"] = {"score": s_structure, "detail": r_structure}

        # -- Compute weighted score --
        # Critical checks (emptiness, error_markers, structure=0) — if 0, score is capped
        critical_fail = False
        critical_reason = ""
        if s_emptiness == 0.0:
            critical_fail = True
            critical_reason = f"critical_fail: {r_emptiness}"
        elif s_error == 0.0:
            critical_fail = True
            critical_reason = f"critical_fail: {r_error}"
        elif s_structure == 0.0:
            critical_fail = True
            critical_reason = f"critical_fail: {r_structure}"

        if critical_fail:
            score = 0.0
            reason = critical_reason
        else:
            # Weighted average
            weights = {
                "truncation": 0.15,
                "repetition": 0.15,
                "garbled": 0.10,
                "relevance": 0.25,
                "adequacy": 0.20,
                "structure": 0.15,
            }
            weighted_sum = sum(
                checks[k]["score"] * w for k, w in weights.items()
            )
            score = weighted_sum

            # Determine reason from lowest-scoring check
            min_check = min(
                ((k, v["score"], v["detail"]) for k, v in checks.items()
                 if k in weights),
                key=lambda x: x[1],
            )
            if score < self.min_score:
                reason = f"low_score: {min_check[0]}={min_check[2]}"
            else:
                reason = "passed"

        passed = score >= self.min_score
        elapsed_ms = (time.perf_counter() - t0) * 1000

        result = QualityResult(
            score=round(score, 4),
            passed=passed,
            reason=reason,
            checks=checks,
            elapsed_ms=elapsed_ms,
        )

        self.stats.record(result)
        LOG.info(
            "quality_gate: score=%.3f passed=%s reason=%s elapsed=%.1fms",
            result.score, result.passed, result.reason, elapsed_ms,
        )
        return result


    @staticmethod
    def _extract_final_from_reasoning(reasoning_text: str) -> str:
        """Extract the final answer from a reasoning model's thinking trace."""
        if not reasoning_text:
            return ""
        text = reasoning_text.strip()
        for marker in ["Final Answer:", "Final answer:", "Answer:", "So the answer is:", "Therefore, the answer is"]:
            idx = text.rfind(marker)
            if idx >= 0:
                tail = text[idx + len(marker):].strip()
                if tail:
                    return tail[:8000]
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if paragraphs:
            return paragraphs[-1][:8000]
        return text[-2000:]

    async def _invoke_direct(
        self,
        model_path: str,
        messages,
        *,
        timeout: float = 60.0,
        max_tokens: int = 2048,
    ):
        """Bypass engine.pick_chain: call exactly the model_path we want."""
        if "/" not in model_path:
            return {"error": f"bad_model_path: {model_path}"}
        provider_name, model_id = model_path.split("/", 1)
        try:
            from .engine import get_global_engine, RouteResult, proxy_chat_request
        except Exception as e:
            return {"error": f"engine_import_failed: {e!r}"}
        engine = get_global_engine()
        if engine is None:
            return {"error": "engine_not_initialized"}
        registry = engine.registry
        ps = registry._providers.get(provider_name) if hasattr(registry, "_providers") else None
        if not ps or not getattr(ps, "api_keys", None):
            return {"error": f"provider_not_found: {provider_name}"}
        api_key = ps.api_keys[0]
        base_url = getattr(ps, "base_url", "")
        if not base_url:
            return {"error": f"provider_no_base_url: {provider_name}"}
        route = RouteResult(
            provider_name=provider_name,
            base_url=base_url,
            api_key=api_key,
            model_id=model_id,
            full_model_path=model_path,
            score=100.0,
            modality="text",
            context_window=0,
        )
        body = {
            "model": model_id,
            "messages": messages,
            "stream": False,
            "max_tokens": max_tokens,
        }
        try:
            out = await proxy_chat_request(
                route=route, body=body, stream=False, timeout=timeout
            )
            if not isinstance(out, dict):
                return {"error": f"non_dict_response: {out!r}"}
            # Merge reasoning_content into content for thinking models.
            # qwythos-9b / qwen-reasoning style: content="" + reasoning_content="..."
            # Without this merge, _extract_text returns "" and the quality
            # gate marks the response as critical fail.
            try:
                choices = out.get("choices") or []
                if choices:
                    msg = choices[0].get("message") or {}
                    rc = msg.get("reasoning_content") or ""
                    real = msg.get("content") or ""
                    if rc and not real:
                        # Strip the raw reasoning down to the actual answer.
                        # Many reasoning models put the answer AFTER the thinking;
                        # some put "Final Answer: X" near the end.
                        final = self._extract_final_from_reasoning(rc)
                        if final:
                            msg["content"] = final
                            out["choices"][0]["message"] = msg
            except Exception:
                pass
            return out
        except Exception as e:
            return {"error": f"direct_call_failed: {e!r}"}

    # -- Layer 3: baseline fallback --

    async def _invoke_baseline(
        self,
        prompt: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Tuple[Optional[str], Optional[QualityResult]]:
        """Invoke the baseline model as a fallback.

        Returns (answer, quality_result) or (None, None) on failure.
        """
        history = history or []
        try:
            # Lazy import to avoid circular dependency
            from .fusion_router import _invoke_leaf

            LOG.info("quality_gate: invoking baseline model %s", self.baseline_model)
            # Direct provider call (bypass engine.pick_chain).  We construct
            # a RouteResult from the registry so the call goes to exactly
            # the model we chose; the previous _invoke_leaf() went through
            # pick_chain which silently fell back to garbage models.
            out = await self._invoke_direct(
                self.baseline_model,
                history + [{"role": "user", "content": prompt}],
                timeout=self.baseline_timeout,
                max_tokens=self.baseline_max_tokens,
            )

            # Extract text
            from .fusion_router import _extract_text
            answer = _extract_text(out)
            if not answer:
                LOG.warning("quality_gate: baseline returned no text, raw=%r", str(out)[:500])
                return None, None

            self.stats.total_baseline_calls += 1
            baseline_q = await self.assess(prompt, answer, [])
            return answer, baseline_q
        except Exception as e:
            LOG.warning("quality_gate: baseline model failed: %s", e)
            return None, None

    async def ensure_quality(
        self,
        prompt: str,
        answer: str,
        trace: Optional[List[Dict[str, Any]]] = None,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Tuple[str, QualityResult]:
        """Ensure answer meets quality standards.

        If the answer fails quality check and fallback is enabled,
        invokes the baseline model and returns the better response.

        Returns (final_answer, quality_result).
        """
        result = await self.assess(prompt, answer, trace)

        if result.passed:
            return answer, result

        # Quality check failed — try baseline fallback
        if not self.enable_fallback:
            LOG.warning(
                "quality_gate: answer failed (score=%.3f) but fallback disabled",
                result.score,
            )
            return answer, result

        LOG.info(
            "quality_gate: answer failed (score=%.3f, reason=%s), trying baseline %s",
            result.score, result.reason, self.baseline_model,
        )

        baseline_answer, baseline_q = await self._invoke_baseline(prompt, history)

        if baseline_answer and baseline_q:
            # Compare and return the better one
            if baseline_q.score > result.score:
                LOG.info(
                    "quality_gate: baseline better (%.3f > %.3f), using baseline",
                    baseline_q.score, result.score,
                )
                baseline_q.fallback_used = True
                baseline_q.fallback_model = self.baseline_model
                baseline_q.original_score = result.score
                return baseline_answer, baseline_q
            else:
                LOG.info(
                    "quality_gate: original still better (%.3f >= %.3f), keeping original",
                    result.score, baseline_q.score,
                )
                return answer, result

        # Baseline also failed — return original
        LOG.warning("quality_gate: baseline fallback failed, returning original")
        return answer, result

    # -- stats --

    def get_stats(self) -> Dict[str, Any]:
        return self.stats.to_dict()

    def reset_stats(self) -> None:
        self.stats = QualityStats()


# ---------------------------------------------------------------------------
# module-level singleton
# ---------------------------------------------------------------------------
_quality_gate: Optional[QualityGate] = None


def init_quality_gate(config: Optional[Dict[str, Any]] = None) -> QualityGate:
    global _quality_gate
    if _quality_gate is None:
        _quality_gate = QualityGate(config)
    return _quality_gate


def get_quality_gate() -> Optional[QualityGate]:
    return _quality_gate


def reset_quality_gate() -> None:
    global _quality_gate
    _quality_gate = None
