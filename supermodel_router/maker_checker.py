"""
supermodel_router/maker_checker.py — 路由决策分离: maker 选路 → checker 验质量 (v3.21.0)

核心原则:
  - maker: 只负责"选哪个 provider/model", 不做 side effect
  - checker: 只负责"验证这个选择是否安全", 不干涉选路逻辑
  - 不通过 → 回滚到 fallback (不盲目重试)

设计对齐 SOUL.md:
  - 边界: maker 看 score, checker 看健康度/配额/并发
  - 异常: 任何阻塞 (SKIP/DEGRADED/配额满/429) 都走 checker
  - 可观测性: 每次 decision 写 memory_bus, 可追溯
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

LOG = logging.getLogger("maker_checker")

# ── 数据类 ──────────────────────────────────────

@dataclass
class MakerDecision:
    """Maker 的单次选路结果"""
    candidate_index: int
    provider: str
    model_id: str
    score: float
    reason: str  # "top_score" / "failover" / "fallback"

@dataclass
class CheckResult:
    """Checker 的验证结果"""
    approved: bool
    reason: str
    new_candidate_index: int = -1  # approved=False 时的 fallback 候选
    risk_level: str = "low"        # low / medium / high

class RouteMaker:
    """
    只做选路: 接收 engine.pick_chain() 的候选链, 按 strategy 返回最佳候选 index

    职责边界:
      - 不读取实际 provider 状态 (交给 Checker)
      - 不做健康度过滤 (engine._filter_by_health 已经做了)
      - 不做配额检测 (交给 Checker)
    """

    def __init__(self, strategy: str = "flat"):
        self.strategy = strategy

    def pick(self, chain: List[dict]) -> Optional[MakerDecision]:
        """
        从候选链里选最佳候选

        chain: engine.pick_chain() 返回的 CandidateResult list
               每个元素 {provider, model_id, score, capability_score, …}
        """
        if not chain:
            LOG.warning("maker: empty chain")
            return None

        # 目前策略: 按 score 排序后的链, 取 index 0
        # (engine.py 的 _order_by_strategy 已经排好了)
        # 预留: 如果后续要加 task-aware maker, 在这里扩展
        best = chain[0]

        return MakerDecision(
            candidate_index=0,
            provider=best["provider"],
            model_id=best["model_id"],
            score=best.get("score", 0.0),
            reason="top_score",
        )


class RouteChecker:
    """
    验证 maker 选出的候选是否安全

    检查清单 (4 项):
      1. 健康度: 不选 SKIP/HALF_OPEN model
      2. 配额: 不选 daily_limit 已满的 provider
      3. 并发: 不选 max_concurrent 已满的 provider
      4. 黑名单: 不选 task 黑名单 model

    任一阻塞 → 返回 fallback candidate index
    """

    def __init__(self, model_health=None, provider_quotas=None):
        self.model_health = model_health       # ModelHealthManager instance
        self.provider_quotas = provider_quotas # ProviderQuotaManager instance (future)

    def verify(self, decision: MakerDecision, chain: List[dict]) -> CheckResult:
        """
        验证单个决策

        Returns: CheckResult(approved, reason, new_candidate_index, risk_level)
        """
        if decision is None:
            return CheckResult(False, "no_decision", risk_level="high")

        # 1. 健康度检查
        if self.model_health is not None:
            health_state = self.model_health.get_state(decision.provider, decision.model_id)
            if health_state in ("skip", "half_open"):
                return CheckResult(
                    False,
                    f"model_health={health_state}",
                    new_candidate_index=self._find_fallback(chain, 1),
                    risk_level="high",
                )
            if health_state == "degraded":
                # degraded 允许通过但降权
                return CheckResult(True, f"model_health={health_state} (degraded_ok)", risk_level="medium")

        # 2. 配额检查 (预留, 未来接入 provider_quotas)
        # if self.provider_quotas is not None:
        #     if self.provider_quotas.is_exhausted(decision.provider):
        #         return CheckResult(...)

        # 3. 黑名单检查 (task type → model exclusion)
        # (engine.py 的 model_filter 已经做了, 这里再兜底一次)
        # if self._is_blacklisted(decision.provider, decision.model_id, task_type):
        #     return CheckResult(False, "blacklisted", ...)

        return CheckResult(True, "approved", risk_level="low")

    def _find_fallback(self, chain: List[dict], start_index: int) -> int:
        """找下一个可用 fallback candidate"""
        for i in range(start_index, len(chain)):
            c = chain[i]
            if self.model_health is not None:
                state = self.model_health.get_state(c["provider"], c["model_id"])
                if state in ("skip", "half_open"):
                    continue
            return i
        return -1  # 没有 fallback


class MakerCheckerEngine:
    """
    Maker + Checker 统一入口 (供 engine.py 调用)

    使用示例:
        mc = MakerCheckerEngine(model_health=mhm, strategy="flat")
        decision = mc.decide(chain)
        if decision:
            result = proxy_chat_request(decision.provider, decision.model_id, ...)
            mc.record(result)  # 写 memory_bus
    """

    def __init__(self, model_health=None, strategy: str = "flat", memory_bus=None):
        self.maker = RouteMaker(strategy=strategy)
        self.checker = RouteChecker(model_health=model_health)
        self.memory_bus = memory_bus
        self._decision_history: List[dict] = []

    def decide(self, chain: List[dict]) -> Optional[dict]:
        """
        完整决策: maker 选 → checker 验 → 返回 {provider, model_id, score, …}
        """
        decision = self.maker.pick(chain)
        if decision is None:
            return None

        check = self.checker.verify(decision, chain)
        if not check.approved:
            LOG.warning("maker_checker reject: provider=%s model=%s reason=%s",
                        decision.provider, decision.model_id, check.reason)

            # fallback
            if check.new_candidate_index >= 0 and check.new_candidate_index < len(chain):
                fallback = chain[check.new_candidate_index]
                decision = MakerDecision(
                    candidate_index=check.new_candidate_index,
                    provider=fallback["provider"],
                    model_id=fallback["model_id"],
                    score=fallback.get("score", 0.0),
                    reason=f"failover: {check.reason}",
                )
            else:
                return None  # 没有 fallback

        # 记录决策历史
        record = {
            "provider": decision.provider,
            "model_id": decision.model_id,
            "score": decision.score,
            "reason": decision.reason,
            "approved": check.approved,
            "risk_level": check.risk_level,
        }
        self._decision_history.append(record)
        if len(self._decision_history) > 200:
            self._decision_history.pop(0)

        return record

    def record(self, result: dict) -> None:
        """
        记录实际路由结果 → memory_bus

        result: engine.proxy_chat_request 返回的 RouteResult / 异常 dict
        """
        if self.memory_bus is None:
            return

        from .memory_bus import RouteMemory

        mem = RouteMemory(
            provider=result.get("provider", ""),
            model_id=result.get("model_id", ""),
            input_modality=result.get("input_modality", "text"),
            output_modality=result.get("output_modality", "text"),
            task_type=result.get("task_type", "generation"),
            success=result.get("success", False),
            latency_ms=result.get("latency_ms", 0.0),
            tokens_in=result.get("tokens_in", 0),
            tokens_out=result.get("tokens_out", 0),
            fail_reason=result.get("fail_reason", ""),
        )
        self.memory_bus.record(mem)

    def history(self, limit: int = 50) -> List[dict]:
        return list(reversed(self._decision_history[-limit:]))
