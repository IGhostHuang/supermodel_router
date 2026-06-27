"""
orchestrator.py — L3 Multi-Modal Orchestration (v3.23.0)

老大 2026-06-27 钦定: 
  - 多模态 any-to-any 路由
  - 复杂任务 plan-execute (推理 → 生图)
  - 多 provider 并行调度

核心职责:
  1. 任务分类 (text / multimodal-in / image-out / audio-out...)
  2. 计划生成 (简单任务单步, 复杂任务 DAG)
  3. 并行/串行执行 (independent 任务并行, dependent 串行)
  4. 结果融合 (多 provider 结果 cross-encode 选 best)
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

LOG = logging.getLogger(__name__)


class Modality(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    MULTIMODAL = "multimodal"   # 输入多模态 (e.g. image + text)


class TaskKind(str, Enum):
    """任务类型 — 决定 routing 策略"""
    CHAT = "chat"                       # 简单对话
    COMPLETION = "completion"            # 文本补全
    VISION_QA = "vision_qa"             # 图+文 → 文
    IMAGE_GEN = "image_gen"             # 文 → 图
    AUDIO_TRANSCRIBE = "audio_transcribe"  # 音 → 文
    AUDIO_GEN = "audio_gen"             # 文 → 音
    MULTI_STEP = "multi_step"           # 复杂: 推理 → 生图 / 多模态融合
    PARALLEL_FUSION = "parallel_fusion" # 多 provider 并行 + 融合
    UNKNOWN = "unknown"


@dataclass
class TaskSpec:
    """单个任务的 input/output spec"""
    task_id: str = field(default_factory=lambda: f"t-{uuid.uuid4().hex[:8]}")
    kind: TaskKind = TaskKind.CHAT
    inputs: Dict[Modality, Any] = field(default_factory=dict)
    required_outputs: List[Modality] = field(default_factory=lambda: [Modality.TEXT])
    constraints: Dict[str, Any] = field(default_factory=dict)
    # constraints: {"max_cost": 0.001, "max_latency_ms": 10000, "min_quality": 70}
    
    parent_id: Optional[str] = None      # 父任务 (DAG 依赖)
    depends_on: List[str] = field(default_factory=list)


@dataclass
class TaskPlan:
    """任务执行计划 (DAG)"""
    plan_id: str = field(default_factory=lambda: f"plan-{uuid.uuid4().hex[:8]}")
    tasks: List[TaskSpec] = field(default_factory=list)
    parallel_groups: List[List[str]] = field(default_factory=list)
    # parallel_groups: [[task_id, task_id], [task_id]] - 每组并行执行, 组间串行
    
    def topological_order(self) -> List[List[str]]:
        """返回拓扑序的并行组列表"""
        return self.parallel_groups


@dataclass
class TaskResult:
    """任务执行结果"""
    task_id: str
    success: bool
    outputs: Dict[Modality, Any] = field(default_factory=dict)
    error: Optional[str] = None
    provider_used: Optional[str] = None
    model_used: Optional[str] = None
    latency_ms: float = 0.0
    cost: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "success": self.success,
            "provider": self.provider_used,
            "model": self.model_used,
            "latency_ms": round(self.latency_ms, 1),
            "cost": round(self.cost, 5),
            "error": self.error,
        }


class TaskClassifier:
    """根据 input + required output 自动判定 TaskKind
    
    判定规则:
      - 仅 text in + text out → CHAT
      - image in + text out → VISION_QA
      - 仅 text in + image out → IMAGE_GEN
      - audio in + text out → AUDIO_TRANSCRIBE
      - text in + audio out → AUDIO_GEN
      - text in + 多 out (e.g. text + image) → MULTI_STEP (plan-execute)
      - 显式 parallel=True → PARALLEL_FUSION
    """
    
    @staticmethod
    def classify(inputs: Dict[Modality, Any], 
                 required_outputs: List[Modality],
                 hints: Optional[Dict[str, Any]] = None) -> TaskKind:
        hints = hints or {}
        if hints.get("parallel"):
            return TaskKind.PARALLEL_FUSION
        
        has_image_in = Modality.IMAGE in inputs
        has_audio_in = Modality.AUDIO in inputs
        has_text_in = Modality.TEXT in inputs
        needs_image_out = Modality.IMAGE in required_outputs
        needs_audio_out = Modality.AUDIO in required_outputs
        needs_text_out = Modality.TEXT in required_outputs
        
        # 多输出 → 多步
        multi_out = sum([needs_image_out, needs_audio_out, needs_text_out]) > 1
        if multi_out:
            return TaskKind.MULTI_STEP
        
        # 单输出
        if has_image_in and needs_text_out:
            return TaskKind.VISION_QA
        if has_audio_in and needs_text_out:
            return TaskKind.AUDIO_TRANSCRIBE
        if has_text_in and needs_image_out:
            return TaskKind.IMAGE_GEN
        if has_text_in and needs_audio_out:
            return TaskKind.AUDIO_GEN
        if has_text_in and needs_text_out:
            return TaskKind.CHAT
        
        return TaskKind.UNKNOWN


class PlanExecutor:
    """执行 TaskPlan
    
    流程:
      1. 按 parallel_groups 顺序执行
      2. 每组内 task 并发 (asyncio.gather)
      3. 结果累积 + 传给下游
      4. 整体返回 PlanResult
    """
    
    def __init__(self, executor_fn: Callable[[TaskSpec], Awaitable[TaskResult]]):
        self.executor_fn = executor_fn
    
    async def execute(self, plan: TaskPlan) -> List[TaskResult]:
        """Execute plan, return all task results"""
        all_results: List[TaskResult] = []
        completed_ids: Set[str] = set()
        results_by_id: Dict[str, TaskResult] = {}
        
        for group in plan.parallel_groups:
            group_tasks = [t for t in plan.tasks if t.task_id in group]
            group_results = await asyncio.gather(
                *[self.executor_fn(t) for t in group_tasks],
                return_exceptions=True
            )
            for t, r in zip(group_tasks, group_results):
                        if isinstance(r, BaseException):
                            r = TaskResult(task_id=t.task_id, success=False, error=str(r))
                        else:
                            r = r  # type: ignore[assignment]
                        results_by_id[t.task_id] = r
                        completed_ids.add(t.task_id)
                        all_results.append(r)
        
        return all_results


# ─── Plan builder helpers ──────────────────────────────────────────

def build_image_gen_plan(prompt: str, ref_image: Optional[bytes] = None,
                          style: Optional[str] = None) -> TaskPlan:
    """复杂图像生成 plan: 推理 (refine prompt) → 生图
    
    用 PROMPT_REFINER (reasoning model) 先优化 prompt, 再用 IMAGE_GEN
    """
    refine_task = TaskSpec(
        kind=TaskKind.COMPLETION,
        inputs={Modality.TEXT: prompt},
        required_outputs=[Modality.TEXT],
        constraints={"max_cost": 0.0001, "max_latency_ms": 5000},
    )
    
    img_task = TaskSpec(
        kind=TaskKind.IMAGE_GEN,
        inputs={Modality.TEXT: prompt},  # 占位, 执行时替换
        required_outputs=[Modality.IMAGE],
        constraints={"max_cost": 0.005, "max_latency_ms": 30000, "min_quality": 70},
        depends_on=[refine_task.task_id],
    )
    
    return TaskPlan(
        tasks=[refine_task, img_task],
        parallel_groups=[[refine_task.task_id], [img_task.task_id]],
    )


def build_multimodal_fusion_plan(
    text_in: str, 
    image_in: Optional[Any] = None,
    audio_in: Optional[Any] = None,
    outputs: Optional[List[Modality]] = None
) -> TaskPlan:
    """多模态融合 plan: 并行理解多个输入 → 融合生成"""
    outputs = outputs if outputs is not None else [Modality.TEXT]
    
    understanding_tasks = []
    if image_in is not None:
        understanding_tasks.append(TaskSpec(
            kind=TaskKind.VISION_QA,
            inputs={Modality.IMAGE: image_in, Modality.TEXT: "describe this image"},
            required_outputs=[Modality.TEXT],
            constraints={"max_cost": 0.0005, "max_latency_ms": 10000},
        ))
    if audio_in is not None:
        understanding_tasks.append(TaskSpec(
            kind=TaskKind.AUDIO_TRANSCRIBE,
            inputs={Modality.AUDIO: audio_in},
            required_outputs=[Modality.TEXT],
            constraints={"max_cost": 0.001, "max_latency_ms": 15000},
        ))
    
    # 融合任务
    fusion_task = TaskSpec(
        kind=TaskKind.CHAT,
        inputs={Modality.TEXT: text_in},
        required_outputs=outputs,
        constraints={"max_cost": 0.002, "max_latency_ms": 30000, "min_quality": 75},
        depends_on=[t.task_id for t in understanding_tasks],
    )
    
    group_ids = [[t.task_id for t in understanding_tasks]]
    group_ids.append([fusion_task.task_id])
    
    return TaskPlan(
        tasks=understanding_tasks + [fusion_task],
        parallel_groups=group_ids,
    )


def build_parallel_fusion_plan(prompt: str, n_providers: int = 3) -> TaskPlan:
    """并行融合 plan: 同时调 N 个 provider, 选 best (cross-encode fusion)"""
    tasks = [
        TaskSpec(
            task_id=f"parallel-{i}",
            kind=TaskKind.CHAT,
            inputs={Modality.TEXT: prompt},
            required_outputs=[Modality.TEXT],
            constraints={"max_cost": 0.001, "max_latency_ms": 15000},
        )
        for i in range(n_providers)
    ]
    return TaskPlan(
        tasks=tasks,
        parallel_groups=[[t.task_id for t in tasks]],
    )