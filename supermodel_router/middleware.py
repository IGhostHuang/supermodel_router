"""
middleware.py — L4 Middleware Models (v3.23.0)

老大 2026-06-27 钦定: 引入中间模型执行压缩上下文或切片上下文
  - 长 ctx 截断前用便宜模型压缩
  - 多模态场景下用推理模型优化 prompt

中间模型设计原则:
  - 必须用最便宜的模型 (free 优先)
  - 异步执行, 不阻塞主流程
  - 失败 fallback (直接用原始 ctx)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

LOG = logging.getLogger(__name__)


@dataclass
class CompressedContext:
    """压缩后的上下文"""
    original_tokens: int
    compressed_tokens: int
    compressed_text: str
    method: str                              # "summarize" | "slice" | "truncate"
    quality_loss: float                      # 0-1, 0 = no loss


# ─── Middleware 接口 ────────────────────────────────────────────────

MiddlewareCallFn = Callable[[str, Dict[str, Any]], Awaitable[str]]
"""统一中间件调用签名: (prompt, params) -> response_text"""


async def _default_noop_call(prompt: str, params: Dict[str, Any]) -> str:
    """Fallback: 不可用时返回空 (caller 决定怎么处理)"""
    return ""


# ─── 1. Context Compressor ─────────────────────────────────────────

class ContextCompressor:
    """长 ctx 用便宜模型压缩到 target_tokens 以内
    
    触发条件:
      - input tokens > ctx_threshold (默认 60% of model ctx_window)
      - 或 user 显式指定 compress=True
    
    压缩策略:
      - 保留 system prompt (100%)
      - 保留最近 N 轮对话 (100%)
      - 中间历史 → 用 cheap model 生成摘要
    
    默认 cheap model: freemodel/model-router (8K ctx, 便宜)
    """
    
    def __init__(self, call_fn: Optional[MiddlewareCallFn] = None,
                 cheap_model: str = "freemodel/model-router",
                 ctx_threshold_ratio: float = 0.6,
                 keep_recent_turns: int = 4):
        self.call_fn = call_fn or _default_noop_call
        self.cheap_model = cheap_model
        self.ctx_threshold_ratio = ctx_threshold_ratio
        self.keep_recent_turns = keep_recent_turns
    
    def should_compress(self, messages: List[Dict[str, Any]], 
                        target_ctx_window: int) -> bool:
        """判断是否需要压缩"""
        est_tokens = self._estimate_tokens(messages)
        threshold = target_ctx_window * self.ctx_threshold_ratio
        return est_tokens > threshold
    
    async def compress(self, messages: List[Dict[str, Any]], 
                       target_tokens: int = 2000) -> CompressedContext:
        """压缩 messages 到 target_tokens 以内"""
        if len(messages) <= self.keep_recent_turns + 1:
            # 太少, 不需要压缩
            text = self._messages_to_text(messages)
            return CompressedContext(
                original_tokens=self._estimate_tokens(messages),
                compressed_tokens=self._estimate_text_tokens(text),
                compressed_text=text,
                method="truncate",
                quality_loss=0.0,
            )
        
        # 拆: system + 旧历史 + 最近 N 轮
        system_msgs = [m for m in messages if m.get("role") == "system"]
        recent_msgs = [m for m in messages if m.get("role") != "system"][-self.keep_recent_turns:]
        old_msgs = [m for m in messages if m.get("role") != "system"][:-self.keep_recent_turns]
        
        # 压缩旧历史
        old_text = self._messages_to_text(old_msgs)
        prompt = (
            "请将以下对话历史压缩成简洁摘要, 保留关键事实和上下文, "
            "200 字以内:\n\n" + old_text
        )
        
        try:
            summary = await asyncio.wait_for(
                self.call_fn(prompt, {"model": self.cheap_model, 
                                       "max_tokens": 500,
                                       "temperature": 0.2}),
                timeout=10.0,
            )
            if not summary:
                # 失败 fallback
                summary = old_text[:target_tokens * 2]
                method = "truncate"
                quality_loss = 0.3
            else:
                method = "summarize"
                quality_loss = 0.1
        except (asyncio.TimeoutError, Exception) as e:
            LOG.warning("ContextCompressor 调用失败: %s, fallback truncate", e)
            summary = old_text[:target_tokens * 2]
            method = "truncate"
            quality_loss = 0.3
        
        # 重组
        compressed_msgs = system_msgs + [{"role": "system", 
                                          "content": f"[历史摘要] {summary}"}] + recent_msgs
        compressed_text = self._messages_to_text(compressed_msgs)
        
        return CompressedContext(
            original_tokens=self._estimate_tokens(messages),
            compressed_tokens=self._estimate_text_tokens(compressed_text),
            compressed_text=compressed_text,
            method=method,
            quality_loss=quality_loss,
        )
    
    @staticmethod
    def _estimate_tokens(messages: List[Dict[str, Any]]) -> int:
        """粗估 token 数 (4 chars ≈ 1 token for English)"""
        text = ContextCompressor._messages_to_text(messages)
        return len(text) // 4
    
    @staticmethod
    def _estimate_text_tokens(text: str) -> int:
        return len(text) // 4
    
    @staticmethod
    def _messages_to_text(messages: List[Dict[str, Any]]) -> str:
        parts = []
        for m in messages:
            role = m.get("role", "?")
            content = m.get("content", "")
            if isinstance(content, list):
                content = str(content)
            parts.append(f"[{role}] {content}")
        return "\n".join(parts)


# ─── 2. Context Slicer ─────────────────────────────────────────────

class ContextSlicer:
    """按主题切片上下文, 不同主题分发到不同模型
    
    场景:
      - 长对话涉及多主题 (代码 + 闲聊 + 翻译)
      - 切分后, 不同 slice 用不同擅长模型处理
    """
    
    def __init__(self, call_fn: Optional[MiddlewareCallFn] = None,
                 classifier_model: str = "freemodel/model-router"):
        self.call_fn = call_fn or _default_noop_call
        self.classifier_model = classifier_model
    
    async def slice_by_topic(self, messages: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """按主题切分 messages, 返回 N 个 slice"""
        if len(messages) <= 2:
            return [messages]
        
        text = ContextCompressor._messages_to_text(messages)
        prompt = (
            "请把以下对话按主题分段, 输出每段的 [START] 和 [END] 标记, "
            "不要修改原文:\n\n" + text[:3000]
        )
        
        try:
            result = await asyncio.wait_for(
                self.call_fn(prompt, {"model": self.classifier_model, 
                                       "max_tokens": 1000,
                                       "temperature": 0.0}),
                timeout=8.0,
            )
            # 简单解析: 按 [START]/[END] 切
            slices = self._parse_slices(messages, result)
            return slices if slices else [messages]
        except Exception as e:
            LOG.warning("ContextSlicer 失败: %s, fallback 整体返回", e)
            return [messages]
    
    @staticmethod
    def _parse_slices(messages, result_text: str) -> List[List[Dict]]:
        """解析 LLM 输出的 slice 标记"""
        # 简化: 如果 result 没有 [START] 标记, fallback
        if "[START]" not in result_text:
            return []
        # TODO: 真解析 (本期先 fallback)
        return []


# ─── 3. Prompt Refiner ─────────────────────────────────────────────

class PromptRefiner:
    """用推理模型优化 prompt (常用于 IMAGE_GEN 前置)
    
    场景:
      - user 简单 prompt "一只猫"
      - 用 reasoning model 扩写成 "a cute orange tabby cat sitting on..."
      - 再用 image model 生成, 质量显著提升
    
    多步任务 (plan-execute):
      refine_task → image_task (orchestrator.py build_image_gen_plan)
    """
    
    def __init__(self, call_fn: Optional[MiddlewareCallFn] = None,
                 reasoning_model: str = "openrouter/qwen/qwen-2.5-coder-32b-instruct:free"):
        self.call_fn = call_fn or _default_noop_call
        self.reasoning_model = reasoning_model
    
    async def refine(self, raw_prompt: str, target: str = "image",
                     style: Optional[str] = None,
                     extra_ctx: Optional[str] = None) -> str:
        """优化 prompt 用于 image generation"""
        style_hint = f"风格: {style}。" if style else ""
        ctx_hint = f"参考上下文: {extra_ctx}" if extra_ctx else ""
        
        refine_prompt = (
            f"请将以下用户的简单描述优化成适合 {target} 生成的详细 prompt, "
            f"保留用户原意, 增加细节 (光影/构图/氛围)。{style_hint}{ctx_hint}\n\n"
            f"用户原 prompt: {raw_prompt}\n\n"
            f"输出仅优化后的 prompt, 不要解释。"
        )
        
        try:
            refined = await asyncio.wait_for(
                self.call_fn(refine_prompt, {
                    "model": self.reasoning_model,
                    "max_tokens": 300,
                    "temperature": 0.7,
                }),
                timeout=8.0,
            )
            return refined.strip() or raw_prompt
        except Exception as e:
            LOG.warning("PromptRefiner 失败: %s, fallback 原始 prompt", e)
            return raw_prompt


# ─── 统一中间件调度 ─────────────────────────────────────────────────

class MiddlewarePipeline:
    """组合所有中间件, 在主流程入口按需调用"""
    
    def __init__(self, call_fn: MiddlewareCallFn):
        self.compressor = ContextCompressor(call_fn=call_fn)
        self.slicer = ContextSlicer(call_fn=call_fn)
        self.refiner = PromptRefiner(call_fn=call_fn)
        self._stats = {"compress_calls": 0, "slice_calls": 0, "refine_calls": 0,
                       "compress_saved_tokens": 0}
    
    async def maybe_compress(self, messages, target_ctx_window):
        """如果 ctx 超阈值, 压缩"""
        if not self.compressor.should_compress(messages, target_ctx_window):
            return None
        result = await self.compressor.compress(messages)
        self._stats["compress_calls"] += 1
        self._stats["compress_saved_tokens"] += (
            result.original_tokens - result.compressed_tokens
        )
        LOG.info("Middleware.compress: %d → %d tokens (method=%s, loss=%.2f)",
                 result.original_tokens, result.compressed_tokens, 
                 result.method, result.quality_loss)
        return result
    
    async def maybe_refine_for_image(self, prompt, style=None):
        """image_gen 前优化 prompt"""
        if len(prompt) < 30:
            # 短 prompt 才需要 refine
            refined = await self.refiner.refine(prompt, target="image", style=style)
            self._stats["refine_calls"] += 1
            return refined
        return prompt
    
    def get_stats(self) -> dict:
        return dict(self._stats)