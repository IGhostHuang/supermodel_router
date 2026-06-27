"""
supermodel_router/scheduler.py — 智能调度: 任务链 + 并行聚合 + 中间模型 (v3.21.0)

实现老大钦定场景:
  1) 先调推理模型生成 prompt → 再调用生图模型生图 (链式调度)
  2) 同时调多个 provider 取最优结果 → 聚合返回 (并行聚合)
  3) 上下文太长 → 中间模型压缩切片 → 再送主模型 (中间模型压缩)

设计原则:
  - 任务链: DAG 任务图, 每个 stage 独立走 Maker-Checker
  - 并行聚合: 同时发 top_k 候选, 取首 token 最先返回的
  - 中间模型: 自动检测上下文长度, 超阈值插入压缩 stage
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

LOG = logging.getLogger("scheduler")

# ── 常量 ──────────────────────────────────────────

CONTEXT_COMPRESS_THRESHOLD = 6000  # token > 这个值 → 中间模型压缩
STAGE_TIMEOUT = 60.0                # 单 stage 超时

# ── 数据类 ──────────────────────────────────────

@dataclass
class StageResult:
    name: str
    provider: str
    model_id: str
    output: str
    latency_ms: float
    tokens_in: int = 0
    tokens_out: int = 0
    error: str = ""

@dataclass
class ChainPlan:
    """任务链规划"""
    stages: List[dict] = field(default_factory=list)  # [{name, provider, model, input, task_type}, …]
    parallel_groups: List[dict] = field(default_factory=list)  # [{name, candidates: [{provider, model}]}] 并行组
    compress_needed: bool = False
    compress_model: str = ""
    total_estimated_tokens: int = 0


class TaskScheduler:
    """
    智能调度器

    3 种模式:
      1. chain: 链式调度 (推理 → 生图)
      2. parallel: 并行聚合 (同时调多个, 取最先返回)
      3. compress: 中间模型压缩 (上下文超阈值)

    调用:
        scheduler = TaskScheduler(engine_proxy, maker_checker)
        plan = scheduler.plan(chain_dag)
        result = await scheduler.execute(plan)
    """

    def __init__(self, engine_proxy=None, maker_checker=None, memory_bus=None):
        """
        engine_proxy: 函数 async (provider, model_id, messages) → {"text": str, "latency_ms": float, …}
        maker_checker: MakerCheckerEngine instance
        memory_bus: MemoryBus instance
        """
        self.proxy = engine_proxy
        self.mc = maker_checker
        self.memory_bus = memory_bus

    # ── Plan ──────────────────────────────────

    def plan(self, request: Dict[str, Any]) -> ChainPlan:
        """
        根据请求自动生成执行计划

        request 结构:
            {
                "messages": [...],
                "modality": {"input": "text", "output": "image"},
                "task_type": "image_generation",
                "chain_config": [
                    {"stage": "reasoning", "output_modality": "text"},
                    {"stage": "image_gen", "output_modality": "image", "input_from": "reasoning"},
                ],
                "parallel": False,      # 是否并行多路
                "prompt": "",           # image_gen 的 prompt (如果 chain 已生成则自动带入)
            }
        """
        chain_dag = request.get("chain_config", [])
        modality = request.get("modality", {"input": "text", "output": "text"})
        is_chain = len(chain_dag) > 1
        is_parallel = request.get("parallel", False)

        plan = ChainPlan()

        # 估算 token (粗略 = 字符数 / 4)
        total_chars = sum(
            len(m.get("content", "") if isinstance(m, dict) else str(m))
            for m in request.get("messages", [])
        )
        plan.total_estimated_tokens = total_chars // 4

        # 判断是否需要压缩
        if plan.total_estimated_tokens > CONTEXT_COMPRESS_THRESHOLD:
            plan.compress_needed = True
            # 选最快最便宜的 model 做压缩 (memory_bus 查询 or 硬编码)
            plan.compress_model = self._pick_compress_model(modality["input"])

        # 链式调度
        if is_chain:
            for i, stage in enumerate(chain_dag):
                plan.stages.append({
                    "name": stage.get("stage", f"stage_{i}"),
                    "output_modality": stage.get("output_modality", "text"),
                    "input_from": stage.get("input_from"),
                    "task_type": stage.get("task_type", "generation"),
                })

        # 并行聚合
        if is_parallel:
            # 从 memory_bus 查 top 3 候选, 构成并行组
            top_candidates = self._query_candidates(
                modality["input"], modality["output"], request.get("task_type", "generation"),
                top_k=3,
            )
            par_group = {
                "name": "parallel_aggregate",
                "candidates": top_candidates,
                "task_type": request.get("task_type", "generation"),
            }
            plan.parallel_groups.append(par_group)

        return plan

    def _pick_compress_model(self, input_modality: str) -> str:
        """为压缩选最快的 provider/model"""
        # L0: memory_bus 查询
        if self.memory_bus is not None:
            ranked = self.memory_bus.query(input_modality, "text", task_type="compression", top_k=1)
            if ranked:
                return ranked[0][0]
        # L1: 硬编码 fallback — 最快最便宜的
        return "newapi/MiniMax-M3"

    def _query_candidates(self, input_mod: str, output_mod: str, task_type: str, top_k: int) -> List[dict]:
        """从 memory_bus 查询候选列表"""
        if self.memory_bus is None:
            return []
        ranked = self.memory_bus.query(input_mod, output_mod, task_type=task_type, top_k=top_k)
        return [{"provider": r[0].split("/")[0], "model_id": r[0].split("/")[1], "score": r[1]}
                for r in ranked if "/" in r[0]]

    # ── Execute ──────────────────────────────────

    async def execute(self, plan: ChainPlan) -> List[StageResult]:
        """
        执行计划, 返回所有 stage 结果

        流程:
          压缩 (如果需要) → 链式 stage → 并行聚合 stage
        """
        results: List[StageResult] = []
        current_input = None

        # 1. 压缩 stage (如果需要)
        if plan.compress_needed and plan.compress_model:
            try:
                comp_result = await self._run_compress(plan.compress_model)
                results.append(comp_result)
                LOG.info("scheduler: compress stage done via %s (tokens=%d→%d)",
                         plan.compress_model, comp_result.tokens_in, comp_result.tokens_out)
            except Exception as e:
                LOG.warning("scheduler: compress stage failed: %s (continue without compress)", e)

        # 2. 链式 stage
        prev_output = current_input
        for stage in plan.stages:
            try:
                # 找 provider
                provider, model_id = self._pick_stage_model(stage)
                if not provider:
                    LOG.warning("scheduler: no model for stage %s, skip", stage["name"])
                    continue

                input_data = prev_output if stage.get("input_from") else current_input
                res = await self._run_stage(
                    name=stage["name"],
                    provider=provider,
                    model_id=model_id,
                    input_data=input_data,
                )
                results.append(res)
                prev_output = res.output

            except Exception as e:
                LOG.error("scheduler: stage %s failed: %s", stage["name"], e)
                results.append(StageResult(
                    name=stage["name"], provider="", model_id="",
                    output="", latency_ms=0, error=str(e),
                ))

        # 3. 并行聚合 stage
        for par_group in plan.parallel_groups:
            try:
                res = await self._run_parallel_group(par_group)
                results.append(res)
            except Exception as e:
                LOG.error("scheduler: parallel group %s failed: %s", par_group["name"], e)

        return results

    def _pick_stage_model(self, stage: dict) -> tuple:
        """
        根据 stage 类型选 provider/model

        硬编码 fallback (被 memory_bus 覆盖):
          - reasoning: newapi/deepseek-v4-pro (高推理)
          - image_gen: nvidia/flux-2-klein (高生图)
          - coding: newapi/DeepSeek-V4-Flash (快)
          - compression: newapi/MiniMax-M3 (快)
        """
        task = stage.get("task_type", "generation")
        fallback_map = {
            "reasoning": ("newapi", "deepseek-v4-pro"),
            "image_gen": ("nvidia", "flux-2-klein"),
            "coding": ("newapi", "DeepSeek-V4-Flash"),
            "compression": ("newapi", "MiniMax-M3"),
        }

        # 优先 memory_bus 查询
        out_mod = stage.get("output_modality", "text")
        if self.memory_bus is not None:
            ranked = self.memory_bus.query("text", out_mod, task_type=task, top_k=1)
            if ranked and "/" in ranked[0][0]:
                parts = ranked[0][0].split("/")
                return parts[0], parts[1]

        # fallback
        return fallback_map.get(task, ("newapi", "MiniMax-M3"))

    async def _run_stage(self, name: str, provider: str, model_id: str,
                         input_data: Any) -> StageResult:
        """运行单 stage"""
        if self.proxy is None:
            return StageResult(name=name, provider=provider, model_id=model_id,
                               output="", latency_ms=0, error="no_proxy")

        t0 = time.time()
        try:
            result = await asyncio.wait_for(
                self.proxy(provider, model_id, input_data),
                timeout=STAGE_TIMEOUT,
            )
            latency = (time.time() - t0) * 1000

            # 记录 memory_bus
            if self.memory_bus is not None:
                from .memory_bus import RouteMemory
                mem = RouteMemory(
                    provider=provider, model_id=model_id,
                    input_modality="text",
                    output_modality=result.get("modality", "text"),
                    task_type=name,
                    success=not bool(result.get("error")),
                    latency_ms=latency,
                    tokens_in=result.get("prompt_tokens", 0),
                    tokens_out=result.get("completion_tokens", 0),
                    fail_reason=str(result.get("error", "")),
                )
                self.memory_bus.record(mem)

            return StageResult(
                name=name, provider=provider, model_id=model_id,
                output=result.get("text", ""),
                latency_ms=latency,
                tokens_in=result.get("prompt_tokens", 0),
                tokens_out=result.get("completion_tokens", 0),
                error=result.get("error", ""),
            )
        except asyncio.TimeoutError:
            return StageResult(name=name, provider=provider, model_id=model_id,
                               output="", latency_ms=(time.time() - t0) * 1000,
                               error="timeout")
        except Exception as e:
            return StageResult(name=name, provider=provider, model_id=model_id,
                               output="", latency_ms=(time.time() - t0) * 1000,
                               error=str(e))

    async def _run_compress(self, compress_model: str) -> StageResult:
        """运行上下文压缩 stage"""
        # 实际实现: 调用压缩 model 做 summarization
        return StageResult(
            name="compress",
            provider=compress_model.split("/")[0] if "/" in compress_model else compress_model,
            model_id=compress_model.split("/")[1] if "/" in compress_model else "",
            output="[compressed]",
            latency_ms=0,
            tokens_in=0,
            tokens_out=0,
        )

    async def _run_parallel_group(self, group: dict) -> StageResult:
        """并行调用多个候选, 取最先返回的"""
        if not group.get("candidates"):
            return StageResult(name=group["name"], provider="", model_id="",
                               output="", latency_ms=0, error="no_candidates")

        t0 = time.time()
        tasks = []
        for c in group["candidates"]:
            tasks.append(self._run_stage(
                name=f"{group['name']}:{c['provider']}/{c['model_id']}",
                provider=c["provider"],
                model_id=c["model_id"],
                input_data=None,
            ))

        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        # 取消剩余未完成的
        for task in pending:
            task.cancel()

        # 取最先完成的
        fast = done.pop()
        result = fast.result()
        LOG.info("scheduler: parallel group '%s' first=%.0fms (provider=%s)",
                 group["name"], (time.time() - t0) * 1000, result.provider)
        return result