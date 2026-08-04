"""
supermodel_router -- SMR (Super Model Router) package.

This package provides the core routing and fusion capabilities for SMR:

Core modules:
    fusion_router        Multi-model fusion with Expert / Vote / Pipeline / Refine operators.
    free_auto_discovery  In-process L1/L2/L3 free model discovery scheduler.

v4 modules:
    fusion_composer      Intelligent auto-composition of fusion plans from prompt analysis.
    quality_gate         Output quality validation with baseline model fallback.

Phase 1 modules (roadmap):
    git_checkpoint       File-system config snapshot + rollback (no Git dependency).
    plan_mode            Plan-Confirm-Execute three-stage mode for config changes.

Phase 2 modules (roadmap):
    context_compressor   Tiered token compression for Fusion conversations.

Note: other modules (engine, model_health, openai_routes, etc.) may live in
the full SMR deployment but are not present in this lightweight package.
"""

from __future__ import annotations

# -- Version
__version__ = "0.4.0"

# -- Public re-exports from fusion_router
# These are the most commonly used symbols.  Access everything else via
# ``from supermodel_router.fusion_router import ...``
try:
    from .fusion_router import (
        FusionRouter,
        FusionResult,
        FusionStep,
        init_fusion_router,
        get_fusion_router,
        reset_fusion_router,
    )
    _HAS_FUSION = True
except ImportError:
    _HAS_FUSION = False

# -- Public re-exports from fusion_composer (v4)
try:
    from .fusion_composer import (
        AutoFusionComposer,
        ComposedPlan,
        PromptAnalysis,
        ModelInfo,
        analyze_prompt,
        select_operator,
        select_models,
        build_plan,
        init_fusion_composer,
        get_fusion_composer,
        reset_fusion_composer,
    )
    _HAS_COMPOSER = True
except ImportError:
    _HAS_COMPOSER = False

# -- Public re-exports from quality_gate (v4)
try:
    from .quality_gate import (
        QualityGate,
        QualityResult,
        QualityStats,
        init_quality_gate,
        get_quality_gate,
        reset_quality_gate,
    )
    _HAS_QUALITY_GATE = True
except ImportError:
    _HAS_QUALITY_GATE = False

# -- Public re-exports from free_auto_discovery
try:
    from .free_auto_discovery import (
        FreeAutoDiscovery,
        DiscoveryRunResult,
        init_free_auto_discovery,
        get_free_auto_discovery,
        reset_free_auto_discovery,
    )
    _HAS_DISCOVERY = True
except ImportError:
    _HAS_DISCOVERY = False

# -- Public re-exports from git_checkpoint (Phase 1)
try:
    from .git_checkpoint import (
        CheckpointManager,
        Checkpoint,
        RollbackResult,
        DiffEntry,
        init_checkpoint_manager,
        get_checkpoint_manager,
        reset_checkpoint_manager,
    )
    _HAS_CHECKPOINT = True
except ImportError:
    _HAS_CHECKPOINT = False

# -- Public re-exports from plan_mode (Phase 1)
try:
    from .plan_mode import (
        RoutingPlanner,
        ChangeRequest,
        ChangePlan,
        PlanStep,
        ChangeResult,
        ChangeType,
        PlanStatus,
        RiskLevel,
        init_routing_planner,
        get_routing_planner,
        reset_routing_planner,
    )
    _HAS_PLAN_MODE = True
except ImportError:
    _HAS_PLAN_MODE = False

# -- Public re-exports from context_compressor (Phase 2)
try:
    from .context_compressor import (
        ContextCompressor,
        CompressionStats,
        init_context_compressor,
        get_context_compressor,
        reset_context_compressor,
    )
    _HAS_COMPRESSOR = True
except ImportError:
    _HAS_COMPRESSOR = False


__all__ = [
    # version
    "__version__",
    # fusion_router
    "FusionRouter",
    "FusionResult",
    "FusionStep",
    "init_fusion_router",
    "get_fusion_router",
    "reset_fusion_router",
    # fusion_composer (v4)
    "AutoFusionComposer",
    "ComposedPlan",
    "PromptAnalysis",
    "ModelInfo",
    "analyze_prompt",
    "select_operator",
    "select_models",
    "build_plan",
    "init_fusion_composer",
    "get_fusion_composer",
    "reset_fusion_composer",
    # quality_gate (v4)
    "QualityGate",
    "QualityResult",
    "QualityStats",
    "init_quality_gate",
    "get_quality_gate",
    "reset_quality_gate",
    # free_auto_discovery
    "FreeAutoDiscovery",
    "DiscoveryRunResult",
    "init_free_auto_discovery",
    "get_free_auto_discovery",
    "reset_free_auto_discovery",
    # git_checkpoint (Phase 1)
    "CheckpointManager",
    "Checkpoint",
    "RollbackResult",
    "DiffEntry",
    "init_checkpoint_manager",
    "get_checkpoint_manager",
    "reset_checkpoint_manager",
    # plan_mode (Phase 1)
    "RoutingPlanner",
    "ChangeRequest",
    "ChangePlan",
    "PlanStep",
    "ChangeResult",
    "ChangeType",
    "PlanStatus",
    "RiskLevel",
    "init_routing_planner",
    "get_routing_planner",
    "reset_routing_planner",
    # context_compressor (Phase 2)
    "ContextCompressor",
    "CompressionStats",
    "init_context_compressor",
    "get_context_compressor",
    "reset_context_compressor",
]


def available_modules() -> dict:
    """Return which submodules are available in this environment.

    Useful for health checks and admin UIs.
    """
    return {
        "fusion_router": _HAS_FUSION,
        "fusion_composer": _HAS_COMPOSER,
        "quality_gate": _HAS_QUALITY_GATE,
        "free_auto_discovery": _HAS_DISCOVERY,
        "git_checkpoint": _HAS_CHECKPOINT,
        "plan_mode": _HAS_PLAN_MODE,
        "context_compressor": _HAS_COMPRESSOR,
    }
