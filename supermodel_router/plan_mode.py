"""
plan_mode.py -- "plan first, execute later" three-stage mode for SMR.

Inspired by ``@plannotator/pi-extension``.  Complex routing configuration
changes (fusion strategy tweaks, health threshold adjustments, model pool
updates) should not be applied blindly.  Instead:

    1. **Plan**     -- Analyze the requested change, generate a step-by-step
                       plan with impact assessment and risk level.
    2. **Confirm**  -- The plan is presented to the operator (via Admin API
                       or CLI) for explicit approval.  No changes are made yet.
    3. **Execute**  -- On approval, automatically create a Git Checkpoint,
                       apply the changes, and verify.  If anything fails,
                       auto-rollback to the checkpoint.

Supported change types:
    * ``fusion_strategy``  -- Modify a fusion plan (add/remove models, change
                              vote strategy, adjust timeouts).
    * ``health_threshold`` -- Adjust model_health thresholds (skip counts,
                              cooldown durations, rolling window sizes).
    * ``model_pool``       -- Add or remove models from the routing pool.
    * ``custom``           -- User-defined change with arbitrary steps.

Usage::

    from supermodel_router.plan_mode import RoutingPlanner, ChangeRequest

    planner = RoutingPlanner()

    # Stage 1: Plan
    plan = await planner.plan_change(ChangeRequest(
        change_type="fusion_strategy",
        target="quick_vote",
        description="Switch vote strategy from best_pick to majority",
        params={"strategy": "majority"},
    ))

    # Stage 2: Confirm (operator reviews plan.to_dict())
    # ... operator calls admin API or CLI to confirm ...

    # Stage 3: Execute
    result = await planner.execute_plan(plan.plan_id)
    if not result.success:
        print(f"Failed, rolled back: {result.error}")
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------
PLAN_TTL_SECONDS = 600  # Plans expire after 10 minutes if not confirmed
MAX_PENDING_PLANS = 50  # Maximum unconfirmed plans in memory


# ---------------------------------------------------------------------------
# enums
# ---------------------------------------------------------------------------
class ChangeType(str, Enum):
    """Supported change types for Plan Mode."""

    FUSION_STRATEGY = "fusion_strategy"
    HEALTH_THRESHOLD = "health_threshold"
    MODEL_POOL = "model_pool"
    CUSTOM = "custom"


class PlanStatus(str, Enum):
    """Lifecycle status of a plan."""

    PENDING = "pending"      # Created, awaiting confirmation
    APPROVED = "approved"    # Confirmed by operator, ready to execute
    EXECUTING = "executing"  # Currently being applied
    COMPLETED = "completed"  # Successfully applied
    FAILED = "failed"        # Execution failed (auto-rolled back)
    EXPIRED = "expired"      # TTL exceeded, no longer executable
    CANCELLED = "cancelled"  # Explicitly cancelled by operator


class RiskLevel(str, Enum):
    """Risk assessment levels."""

    LOW = "low"        # Single config value change, easily reversible
    MEDIUM = "medium"  # Multiple files affected, may impact routing
    HIGH = "high"      # Core routing logic change, potential service disruption


# ---------------------------------------------------------------------------
# data classes
# ---------------------------------------------------------------------------
@dataclass
class ChangeRequest:
    """A request to change SMR configuration.

    Attributes:
        change_type: The type of change (fusion_strategy, health_threshold, etc.).
        target: The target of the change (e.g. plan_id for fusion, section
                name for health thresholds).
        description: Human-readable description of what the change does.
        params: Change-specific parameters.
        requested_by: Identifier of who requested the change (for audit trail).
    """

    change_type: str
    target: str
    description: str
    params: Dict[str, Any] = field(default_factory=dict)
    requested_by: str = "system"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "change_type": self.change_type,
            "target": self.target,
            "description": self.description,
            "params": self.params,
            "requested_by": self.requested_by,
        }


@dataclass
class PlanStep:
    """A single step in an execution plan.

    Attributes:
        action: The action to perform (e.g. "update_config", "register_plan",
                "set_threshold").
        target: The file or object to modify.
        description: What this step does.
        params: Step-specific parameters.
        rollback_action: How to undo this step (None if irreversible).
    """

    action: str
    target: str
    description: str
    params: Dict[str, Any] = field(default_factory=dict)
    rollback_action: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "target": self.target,
            "description": self.description,
            "params": self.params,
            "rollback_action": self.rollback_action,
        }


@dataclass
class ChangePlan:
    """A generated plan awaiting confirmation.

    Attributes:
        plan_id: Unique identifier for this plan.
        request: The original change request.
        steps: Ordered list of steps to execute.
        risk: Assessed risk level.
        impact: Description of what will be affected.
        checkpoint_label: Label for the auto-created checkpoint.
        status: Current lifecycle status.
        created_at: When the plan was generated.
        expires_at: When the plan expires (TTL).
        checkpoint_id: ID of the checkpoint created during execution (if any).
        executed_at: When the plan was executed (if applicable).
        error: Error message if execution failed.
    """

    plan_id: str
    request: ChangeRequest
    steps: List[PlanStep] = field(default_factory=list)
    risk: str = RiskLevel.LOW.value
    impact: str = ""
    checkpoint_label: str = ""
    status: str = PlanStatus.PENDING.value
    created_at: float = 0.0
    expires_at: float = 0.0
    checkpoint_id: Optional[str] = None
    executed_at: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "request": self.request.to_dict(),
            "steps": [s.to_dict() for s in self.steps],
            "risk": self.risk,
            "impact": self.impact,
            "checkpoint_label": self.checkpoint_label,
            "status": self.status,
            "created_at": self.created_at,
            "created_at_iso": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(self.created_at)
            ) if self.created_at else "",
            "expires_at": self.expires_at,
            "expires_at_iso": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(self.expires_at)
            ) if self.expires_at else "",
            "checkpoint_id": self.checkpoint_id,
            "executed_at": self.executed_at,
            "error": self.error,
        }

    @property
    def is_expired(self) -> bool:
        """Check if this plan has exceeded its TTL."""
        return time.time() > self.expires_at and self.status == PlanStatus.PENDING.value


@dataclass
class ChangeResult:
    """Result of executing a plan.

    Attributes:
        success: Whether execution completed without errors.
        plan_id: The plan that was executed.
        checkpoint_id: The checkpoint created before execution (for rollback).
        applied_steps: Number of steps successfully applied.
        rolled_back: True if the plan was auto-rolled back after failure.
        error: Error message if success is False.
    """

    success: bool
    plan_id: str
    checkpoint_id: Optional[str] = None
    applied_steps: int = 0
    rolled_back: bool = False
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "plan_id": self.plan_id,
            "checkpoint_id": self.checkpoint_id,
            "applied_steps": self.applied_steps,
            "rolled_back": self.rolled_back,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# step executors (pluggable)
# ---------------------------------------------------------------------------
StepExecutor = Callable[[PlanStep], Any]
"""A callable that executes a single PlanStep and returns a result.

If it raises an exception, the plan is considered failed and rollback
is triggered.  The return value is currently unused but available for
future logging/verification.
"""

RollbackExecutor = Callable[[PlanStep, Any], None]
"""A callable that rolls back a single PlanStep.

Receives the step and the result from the forward execution (currently
always None).  Should not raise -- rollback errors are logged but don't
stop the rollback chain.
"""


# ---------------------------------------------------------------------------
# RoutingPlanner
# ---------------------------------------------------------------------------
class RoutingPlanner:
    """Generates, manages, and executes configuration change plans.

    Lifecycle: singleton, initialized at app boot.

    The planner maintains an in-memory registry of pending plans.  Each
    plan has a TTL (default 10 minutes); expired plans are automatically
    marked as expired and cannot be executed.

    Thread/async safety: all plan operations are protected by an
    ``asyncio.Lock``.
    """

    def __init__(self):
        self._plans: Dict[str, ChangePlan] = {}
        self._lock = asyncio.Lock()
        self._step_executors: Dict[str, StepExecutor] = {}
        self._rollback_executors: Dict[str, RollbackExecutor] = {}
        self._step_results: Dict[str, List[Any]] = {}  # plan_id -> results

        # Register built-in step executors
        self._register_builtin_executors()

        LOG.info("RoutingPlanner: initialized")

    # ------------------------------------------------------------------
    # Stage 1: Plan generation
    # ------------------------------------------------------------------
    async def plan_change(self, request: ChangeRequest) -> ChangePlan:
        """Generate a plan for a configuration change request.

        Analyzes the request, generates step-by-step execution plan,
        assesses risk, and returns the plan for operator confirmation.

        Args:
            request: The change request describing what to modify.

        Returns:
            A ``ChangePlan`` with status ``pending``, awaiting confirmation.
        """
        plan_id = f"plan_{uuid.uuid4().hex[:12]}"
        now = time.time()

        # Generate steps based on change type
        steps = self._generate_steps(request)

        # Assess risk
        risk = self._assess_risk(request, steps)

        # Generate impact description
        impact = self._describe_impact(request, steps)

        # Generate checkpoint label
        checkpoint_label = f"plan-{plan_id}-{request.change_type}"

        plan = ChangePlan(
            plan_id=plan_id,
            request=request,
            steps=steps,
            risk=risk,
            impact=impact,
            checkpoint_label=checkpoint_label,
            status=PlanStatus.PENDING.value,
            created_at=now,
            expires_at=now + PLAN_TTL_SECONDS,
        )

        async with self._lock:
            # Prune expired plans if we're at capacity
            self._prune_expired_plans()
            if len(self._plans) >= MAX_PENDING_PLANS:
                # Remove oldest pending plan
                oldest = min(
                    (p for p in self._plans.values()
                     if p.status == PlanStatus.PENDING.value),
                    key=lambda p: p.created_at,
                    default=None,
                )
                if oldest:
                    del self._plans[oldest.plan_id]

            self._plans[plan_id] = plan

        LOG.info(
            "RoutingPlanner: generated plan '%s' (%d steps, risk=%s, type=%s)",
            plan_id, len(steps), risk, request.change_type,
        )
        return plan

    # ------------------------------------------------------------------
    # Stage 2: Confirmation
    # ------------------------------------------------------------------
    async def confirm_plan(self, plan_id: str) -> ChangePlan:
        """Mark a plan as approved, ready for execution.

        Args:
            plan_id: The plan to confirm.

        Returns:
            The updated ``ChangePlan`` with status ``approved``.

        Raises:
            ValueError: If the plan doesn't exist, is expired, or is not
                        in pending status.
        """
        async with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                raise ValueError(f"plan '{plan_id}' not found")

            if plan.is_expired:
                plan.status = PlanStatus.EXPIRED.value
                raise ValueError(f"plan '{plan_id}' has expired")

            if plan.status != PlanStatus.PENDING.value:
                raise ValueError(
                    f"plan '{plan_id}' is not pending (status={plan.status})"
                )

            plan.status = PlanStatus.APPROVED.value
            LOG.info("RoutingPlanner: plan '%s' confirmed", plan_id)
            return plan

    async def cancel_plan(self, plan_id: str) -> ChangePlan:
        """Cancel a pending plan.

        Args:
            plan_id: The plan to cancel.

        Returns:
            The updated ``ChangePlan`` with status ``cancelled``.

        Raises:
            ValueError: If the plan doesn't exist or is already executing/completed.
        """
        async with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                raise ValueError(f"plan '{plan_id}' not found")

            if plan.status in (PlanStatus.EXECUTING.value, PlanStatus.COMPLETED.value):
                raise ValueError(
                    f"plan '{plan_id}' cannot be cancelled (status={plan.status})"
                )

            plan.status = PlanStatus.CANCELLED.value
            LOG.info("RoutingPlanner: plan '%s' cancelled", plan_id)
            return plan

    # ------------------------------------------------------------------
    # Stage 3: Execution
    # ------------------------------------------------------------------
    async def execute_plan(self, plan_id: str) -> ChangeResult:
        """Execute a confirmed plan with auto-checkpoint and rollback.

        Flow:
            1. Verify plan is approved and not expired.
            2. Create a Git Checkpoint (auto-snapshot).
            3. Execute each step in order.
            4. If any step fails, auto-rollback to the checkpoint.
            5. Mark plan as completed or failed.

        Args:
            plan_id: The plan to execute.

        Returns:
            A ``ChangeResult`` describing the outcome.
        """
        async with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                return ChangeResult(
                    success=False, plan_id=plan_id,
                    error=f"plan '{plan_id}' not found",
                )

            if plan.status != PlanStatus.APPROVED.value:
                return ChangeResult(
                    success=False, plan_id=plan_id,
                    error=f"plan '{plan_id}' not approved (status={plan.status})",
                )

            if plan.is_expired:
                plan.status = PlanStatus.EXPIRED.value
                return ChangeResult(
                    success=False, plan_id=plan_id,
                    error=f"plan '{plan_id}' has expired",
                )

            plan.status = PlanStatus.EXECUTING.value

        # Create checkpoint before executing
        checkpoint_id = None
        try:
            from .git_checkpoint import get_checkpoint_manager
            cp_mgr = get_checkpoint_manager()
            if cp_mgr is not None:
                cp = await cp_mgr.create_checkpoint(plan.checkpoint_label)
                checkpoint_id = cp.checkpoint_id if cp.checkpoint_id else None
                LOG.info(
                    "RoutingPlanner: created checkpoint '%s' for plan '%s'",
                    checkpoint_id, plan_id,
                )
        except Exception as e:
            LOG.warning("RoutingPlanner: checkpoint creation failed: %s", e)

        # Execute steps
        step_results: List[Any] = []
        applied = 0
        execution_error: Optional[str] = None

        for i, step in enumerate(plan.steps):
            try:
                executor = self._step_executors.get(step.action)
                if executor is None:
                    raise ValueError(
                        f"no executor registered for action '{step.action}'"
                    )

                result = executor(step) if not asyncio.iscoroutinefunction(executor) \
                    else await executor(step)
                step_results.append(result)
                applied += 1
                LOG.info(
                    "RoutingPlanner: plan '%s' step %d/%d '%s' OK",
                    plan_id, i + 1, len(plan.steps), step.action,
                )
            except Exception as e:
                execution_error = f"step {i + 1} '{step.action}' failed: {e!r}"
                LOG.error(
                    "RoutingPlanner: plan '%s' step %d failed: %s",
                    plan_id, i + 1, e,
                )
                break

        self._step_results[plan_id] = step_results

        # Handle failure: auto-rollback
        rolled_back = False
        if execution_error is not None:
            LOG.warning("RoutingPlanner: plan '%s' failed, attempting rollback", plan_id)
            rolled_back = await self._rollback_plan(plan, step_results, applied)

            async with self._lock:
                plan.status = PlanStatus.FAILED.value
                plan.checkpoint_id = checkpoint_id
                plan.executed_at = time.time()
                plan.error = execution_error

            return ChangeResult(
                success=False,
                plan_id=plan_id,
                checkpoint_id=checkpoint_id,
                applied_steps=applied,
                rolled_back=rolled_back,
                error=execution_error,
            )

        # Success
        async with self._lock:
            plan.status = PlanStatus.COMPLETED.value
            plan.checkpoint_id = checkpoint_id
            plan.executed_at = time.time()

        LOG.info("RoutingPlanner: plan '%s' completed (%d steps)", plan_id, applied)
        return ChangeResult(
            success=True,
            plan_id=plan_id,
            checkpoint_id=checkpoint_id,
            applied_steps=applied,
        )

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------
    def get_plan(self, plan_id: str) -> Optional[ChangePlan]:
        """Get a plan by ID."""
        return self._plans.get(plan_id)

    def list_plans(
        self,
        status: Optional[str] = None,
    ) -> List[ChangePlan]:
        """List plans, optionally filtered by status.

        Args:
            status: If provided, only return plans with this status.

        Returns:
            List of plans, newest first.
        """
        plans = list(self._plans.values())
        if status:
            plans = [p for p in plans if p.status == status]
        plans.sort(key=lambda p: p.created_at, reverse=True)
        return plans

    # ------------------------------------------------------------------
    # Executor registration (for extensibility)
    # ------------------------------------------------------------------
    def register_executor(
        self,
        action: str,
        executor: StepExecutor,
        rollback: Optional[RollbackExecutor] = None,
    ) -> None:
        """Register a custom step executor.

        Args:
            action: The action name that triggers this executor.
            executor: The forward execution callable.
            rollback: Optional rollback callable.  If None, rollback for
                      this action is a no-op.
        """
        self._step_executors[action] = executor
        if rollback:
            self._rollback_executors[action] = rollback
        LOG.info("RoutingPlanner: registered executor for action '%s'", action)

    # ------------------------------------------------------------------
    # internal: step generation
    # ------------------------------------------------------------------
    def _generate_steps(self, request: ChangeRequest) -> List[PlanStep]:
        """Generate execution steps based on the change request type.

        Each change type has a different step pattern.  Custom requests
        pass through their steps directly.
        """
        ct = request.change_type

        if ct == ChangeType.FUSION_STRATEGY.value:
            return self._gen_fusion_steps(request)
        elif ct == ChangeType.HEALTH_THRESHOLD.value:
            return self._gen_health_steps(request)
        elif ct == ChangeType.MODEL_POOL.value:
            return self._gen_model_pool_steps(request)
        elif ct == ChangeType.CUSTOM.value:
            # Custom requests carry their steps in params["steps"]
            raw_steps = request.params.get("steps", [])
            if not isinstance(raw_steps, list):
                raw_steps = []
            return [
                PlanStep(
                    action=s.get("action", "custom"),
                    target=s.get("target", ""),
                    description=s.get("description", ""),
                    params=s.get("params", {}),
                    rollback_action=s.get("rollback_action"),
                )
                for s in raw_steps
                if isinstance(s, dict)
            ]
        else:
            LOG.warning("RoutingPlanner: unknown change type '%s', generating no-op", ct)
            return []

    def _gen_fusion_steps(self, request: ChangeRequest) -> List[PlanStep]:
        """Generate steps for fusion strategy changes."""
        steps: List[PlanStep] = []
        params = request.params
        target_plan = request.target

        if "strategy" in params:
            steps.append(PlanStep(
                action="update_fusion_plan",
                target=f"fusion:{target_plan}",
                description=f"Update fusion plan '{target_plan}' strategy to '{params['strategy']}'",
                params={
                    "plan_id": target_plan,
                    "updates": {"strategy": params["strategy"]},
                },
                rollback_action="update_fusion_plan",
            ))

        if "model_ids" in params:
            steps.append(PlanStep(
                action="update_fusion_plan",
                target=f"fusion:{target_plan}",
                description=f"Update model_ids for '{target_plan}'",
                params={
                    "plan_id": target_plan,
                    "updates": {"model_ids": params["model_ids"]},
                },
                rollback_action="update_fusion_plan",
            ))

        if "timeout" in params:
            steps.append(PlanStep(
                action="update_fusion_plan",
                target=f"fusion:{target_plan}",
                description=f"Update timeout for '{target_plan}' to {params['timeout']}s",
                params={
                    "plan_id": target_plan,
                    "updates": {"timeout": params["timeout"]},
                },
                rollback_action="update_fusion_plan",
            ))

        if not steps:
            steps.append(PlanStep(
                action="no_op",
                target=target_plan,
                description="No actionable parameters found in request",
            ))

        return steps

    def _gen_health_steps(self, request: ChangeRequest) -> List[PlanStep]:
        """Generate steps for health threshold changes."""
        params = request.params
        target = request.target  # e.g. "model_health" config section

        updates = {}
        for key in (
            "consecutive_fails_skip", "skip_initial_seconds", "skip_max_seconds",
            "rolling_window_size", "rolling_rate_skip_below", "ewma_alpha",
            "ewma_latency_skip_ms", "probe_interval_seconds",
        ):
            if key in params:
                updates[key] = params[key]

        if not updates:
            return [PlanStep(
                action="no_op",
                target=target,
                description="No threshold parameters to update",
            )]

        return [PlanStep(
            action="update_health_threshold",
            target=f"config:{target}",
            description=f"Update {len(updates)} health threshold(s): {', '.join(updates.keys())}",
            params={"updates": updates},
            rollback_action="update_health_threshold",
        )]

    def _gen_model_pool_steps(self, request: ChangeRequest) -> List[PlanStep]:
        """Generate steps for model pool changes."""
        params = request.params
        steps: List[PlanStep] = []

        for model_id in params.get("add_models", []):
            steps.append(PlanStep(
                action="add_model",
                target=f"pool:{model_id}",
                description=f"Add model '{model_id}' to routing pool",
                params={"model_id": model_id},
                rollback_action="remove_model",
            ))

        for model_id in params.get("remove_models", []):
            steps.append(PlanStep(
                action="remove_model",
                target=f"pool:{model_id}",
                description=f"Remove model '{model_id}' from routing pool",
                params={"model_id": model_id},
                rollback_action="add_model",
            ))

        if not steps:
            steps.append(PlanStep(
                action="no_op",
                target="model_pool",
                description="No model additions or removals specified",
            ))

        return steps

    # ------------------------------------------------------------------
    # internal: risk assessment
    # ------------------------------------------------------------------
    def _assess_risk(
        self,
        request: ChangeRequest,
        steps: List[PlanStep],
    ) -> str:
        """Assess the risk level of a plan.

        Heuristics:
            * HIGH: Changes to fusion strategy or removing models
            * MEDIUM: Adding models or changing health thresholds
            * LOW: Single-value config updates
        """
        ct = request.change_type

        if ct == ChangeType.FUSION_STRATEGY.value:
            # Changing vote strategy is high-risk (affects all requests)
            if "strategy" in request.params:
                return RiskLevel.HIGH.value
            return RiskLevel.MEDIUM.value

        if ct == ChangeType.MODEL_POOL.value:
            if request.params.get("remove_models"):
                return RiskLevel.HIGH.value
            return RiskLevel.MEDIUM.value

        if ct == ChangeType.HEALTH_THRESHOLD.value:
            # Threshold changes affect routing decisions globally
            return RiskLevel.MEDIUM.value

        return RiskLevel.LOW.value

    def _describe_impact(
        self,
        request: ChangeRequest,
        steps: List[PlanStep],
    ) -> str:
        """Generate a human-readable impact description."""
        ct = request.change_type
        target = request.target

        if ct == ChangeType.FUSION_STRATEGY.value:
            return (
                f"Modifies fusion plan '{target}'. "
                f"This affects all requests using model 'fusion:{target}'. "
                f"{len(steps)} step(s) will be applied. "
                f"A checkpoint will be created before execution."
            )

        if ct == ChangeType.HEALTH_THRESHOLD.value:
            return (
                f"Adjusts health monitoring thresholds for '{target}'. "
                f"This changes how quickly unhealthy models are detected and "
                f"recovered. {len(steps)} step(s), affects all routing decisions."
            )

        if ct == ChangeType.MODEL_POOL.value:
            add_count = len(request.params.get("add_models", []))
            rem_count = len(request.params.get("remove_models", []))
            return (
                f"Modifies routing model pool: +{add_count} additions, "
                f"-{rem_count} removals. Affects which models receive traffic."
            )

        return f"Custom change: {request.description} ({len(steps)} steps)"

    # ------------------------------------------------------------------
    # internal: rollback
    # ------------------------------------------------------------------
    async def _rollback_plan(
        self,
        plan: ChangePlan,
        step_results: List[Any],
        applied_count: int,
    ) -> bool:
        """Rollback a failed plan.

        Two strategies:
            1. If a checkpoint was created, restore from checkpoint.
            2. If no checkpoint, attempt step-by-step rollback using
               registered rollback executors.

        Args:
            plan: The failed plan.
            step_results: Results from forward execution.
            applied_count: Number of steps that were successfully applied.

        Returns:
            True if rollback succeeded, False otherwise.
        """
        # Strategy 1: Checkpoint-based rollback (preferred)
        if plan.checkpoint_id:
            try:
                from .git_checkpoint import get_checkpoint_manager
                cp_mgr = get_checkpoint_manager()
                if cp_mgr is not None:
                    result = await cp_mgr.rollback(plan.checkpoint_id)
                    if result.success:
                        LOG.info(
                            "RoutingPlanner: checkpoint rollback succeeded for plan '%s'",
                            plan.plan_id,
                        )
                        return True
                    else:
                        LOG.warning(
                            "RoutingPlanner: checkpoint rollback partial: "
                            "restored=%d skipped=%d",
                            len(result.restored_files),
                            len(result.skipped_files),
                        )
                        # Fall through to step-by-step rollback
            except Exception as e:
                LOG.error("RoutingPlanner: checkpoint rollback failed: %s", e)

        # Strategy 2: Step-by-step rollback
        success = True
        for i in range(min(applied_count, len(plan.steps)) - 1, -1, -1):
            step = plan.steps[i]
            rollback_fn = self._rollback_executors.get(step.rollback_action or "")
            if rollback_fn is None:
                LOG.debug(
                    "RoutingPlanner: no rollback executor for action '%s', skipping",
                    step.rollback_action,
                )
                continue
            try:
                result = step_results[i] if i < len(step_results) else None
                if asyncio.iscoroutinefunction(rollback_fn):
                    await rollback_fn(step, result)
                else:
                    rollback_fn(step, result)
                LOG.info("RoutingPlanner: rolled back step %d '%s'", i + 1, step.action)
            except Exception as e:
                LOG.error(
                    "RoutingPlanner: rollback step %d '%s' failed: %s",
                    i + 1, step.action, e,
                )
                success = False

        return success

    # ------------------------------------------------------------------
    # internal: maintenance
    # ------------------------------------------------------------------
    def _prune_expired_plans(self) -> None:
        """Mark expired plans and remove old non-pending plans.

        Called within the lock.  Expired plans are marked as expired;
        plans in terminal states older than 1 hour are deleted.
        """
        now = time.time()
        to_delete: List[str] = []

        for plan_id, plan in self._plans.items():
            if plan.status == PlanStatus.PENDING.value and plan.is_expired:
                plan.status = PlanStatus.EXPIRED.value
                LOG.info("RoutingPlanner: plan '%s' expired", plan_id)

            # Delete terminal-state plans older than 1 hour
            if plan.status in (
                PlanStatus.COMPLETED.value,
                PlanStatus.FAILED.value,
                PlanStatus.EXPIRED.value,
                PlanStatus.CANCELLED.value,
            ):
                if now - plan.created_at > 3600:
                    to_delete.append(plan_id)

        for pid in to_delete:
            del self._plans[pid]
            self._step_results.pop(pid, None)

    # ------------------------------------------------------------------
    # internal: built-in executors (no-ops; real logic injected at deploy)
    # ------------------------------------------------------------------
    def _register_builtin_executors(self) -> None:
        """Register built-in step executors.

        These are no-op stubs that log the action.  In a full deployment,
        they would be overridden or supplemented with real implementations
        that modify config files, update the fusion router, etc.
        """
        async def _noop_executor(step: PlanStep) -> None:
            LOG.info("RoutingPlanner: [noop] executing '%s' on '%s': %s",
                     step.action, step.target, step.description)

        async def _noop_rollback(step: PlanStep, result: Any) -> None:
            LOG.info("RoutingPlanner: [noop] rolling back '%s' on '%s'",
                     step.action, step.target)

        for action in (
            "update_fusion_plan", "update_health_threshold",
            "add_model", "remove_model", "no_op", "custom",
        ):
            self._step_executors[action] = _noop_executor
            self._rollback_executors[action] = _noop_rollback


# ---------------------------------------------------------------------------
# module-level singleton
# ---------------------------------------------------------------------------
_planner: Optional[RoutingPlanner] = None
_init_lock = asyncio.Lock()


async def init_routing_planner() -> RoutingPlanner:
    """Initialize or get the module-level RoutingPlanner singleton.

    Async-safe: uses a lock so concurrent callers don't race on creation.
    """
    global _planner
    async with _init_lock:
        if _planner is None:
            _planner = RoutingPlanner()
        return _planner


def get_routing_planner() -> Optional[RoutingPlanner]:
    """Get the current routing planner singleton (may be None if not init'd)."""
    return _planner


def reset_routing_planner() -> None:
    """Reset the singleton -- primarily for testing."""
    global _planner
    _planner = None
