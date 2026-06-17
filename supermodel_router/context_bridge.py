"""
supermodel_router/context_bridge.py — v3.5.0 上下文桥接 + 过期标记 + 主动盘点 + 切链 abort

v3.4.0 老大 2026-06-17 22:00 拍:
  "smr 每次切换模型的时候,也同步上下文和任务给新的模型,不然对话连不上。
   同时要判断回传的信息是否过期,已过期的话,加上过期标记。"

v3.5.0 老大 2026-06-17 22:25 拍:
  1. 主动盘点: 用户说"盘点上下文/重新审视/回顾上下文"时, SMR 主动聚合该请求
     完整 SwitchRecord 历史 (参与模型/切链次数/原因/累积 partial/stale)
  2. 切链 race condition 防御: 切到下一 candidate 时, 显式 abort 上游 in-flight
     httpx request (stream 模式) — 防止旧请求的慢回复"晚到" 错配给新请求

3+2 大机制:
1. SwitchHistory 跟踪 (per-request, in-memory, 不持久化)
2. ContextBridge.inject_into_body: 切到下一 candidate 时, 在新 body.messages 头部
   插入 system prompt, 告诉新模型"前面发生了什么"
3. Staleness 标记: 切到下一 candidate 的时间 - 请求开始时间 > threshold → stale=True
   流式: SSE sentinel `data: {"_smr_bridge": {...}}` 发在新 chunk 最前
   非流式: result._router.switched_from + _router.stale
4. Per-Request 跟踪 (v3.5): smr_request_id → SwitchRecord[] + request_start_time,
   主动盘点 endpoint /v1/admin/context_review 拿这个聚合报告
5. 切链 abort (v3.5): stream 模式切链时, 显式 await current_agen.aclose() 关上游
   httpx 连接, 防止旧模型的迟缓 reply 晚到错配新请求

OpenAI 协议兼容:
- 多加 1 个 system message 完全合法
- SSE sentinel 是标准 `data: {...}\n\n` 格式
- client 不感知, 跟 v3.3 行为一致 (fail-safe: 注入失败 → fallback 原 body)
"""
import json
import time
import uuid
import logging
import threading
from dataclasses import dataclass, field, asdict
from typing import Optional

LOG = logging.getLogger("context_bridge")


# ── 默认模板 ─────────────────────────────────────────────────

DEFAULT_INJECT_TEMPLATE = """[SMR 上下文桥接 v{version}]

你正在接续一个多模型对话. 前面有 {n_attempts} 次模型尝试 (都失败或部分响应):

{attempt_blocks}

你的任务:
1. 直接基于**用户最后一条消息**和**已有对话历史**给一个完整回答
2. 不要重复前面模型已经成功输出的内容
3. 如果切到你的时间已经超过 {age_minutes} 分钟, 请明确提醒用户"信息可能已过期"
4. 如果前面的部分响应 (PARTIAL) 来自不同模型, 你可以视为"另一个 AI 的草稿", 在此基础上完善或重写

[SMR 桥接结束 — 下面是真实对话]
"""


def _format_attempt_block(idx: int, rec: "SwitchRecord") -> str:
    """把 1 个 SwitchRecord 格式化成 prompt 段落"""
    age_s = int(rec.switch_time - rec.request_start_time)
    partial_preview = ""
    if rec.partial_text:
        preview = rec.partial_text.strip()[:300]
        if len(rec.partial_text.strip()) > 300:
            preview += "...(truncated)"
        partial_preview = f"   PARTIAL: {preview}\n"
    return (
        f"  尝试 #{idx + 1}: {rec.from_full_path}\n"
        f"   状态: {rec.response_status} (http={rec.http_code})\n"
        f"   错误: {rec.error_message[:200]}\n"
        f"   距请求开始: {age_s} 秒\n"
        f"{partial_preview}"
    )


# ── 数据类 ──────────────────────────────────────────────────

@dataclass
class SwitchRecord:
    """一次切换的元数据 (per-request 跟踪用)"""
    from_provider: str
    from_model: str
    from_full_path: str
    partial_text: str = ""              # 流式切时已发出的 chunks 拼接
    switch_time: float = 0.0            # 切换时间戳 (epoch)
    request_start_time: float = 0.0     # 整个请求的起始时间
    response_status: str = ""           # "timeout" / "http_5xx" / "http_401" / "stream_error" / "exception"
    http_code: int = 0
    error_message: str = ""
    attempt_index: int = 0              # 第几次尝试 (0=first)

    def is_stale(self, threshold_s: int) -> bool:
        """是否过期: 当前时间距请求开始 > threshold (即整个请求已耗时)
        而不是 "切到下一 candidate 的时间" — 因为 1 次切换时切换是瞬时的,
        age 永远是 ~0. 改用"整个请求已耗时"更准确 (跨长 retry chain 也算).
        """
        if self.request_start_time <= 0:
            return False
        return (time.time() - self.request_start_time) > threshold_s

    def age_seconds(self) -> int:
        if self.request_start_time <= 0:
            return 0
        return int(time.time() - self.request_start_time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["age_seconds"] = self.age_seconds()
        d["stale"] = False  # 填充时再算
        return d


# ── 核心: ContextBridge 单例 ────────────────────────────────

class ContextBridge:
    """v3.4.0 上下文桥接 + 过期标记引擎

    用法:
        bridge = ContextBridge(cfg)
        history: list[SwitchRecord] = []
        # ... 第 1 个 candidate 失败时:
        history.append(SwitchRecord(...))
        # 切到下一 candidate 前:
        new_body = bridge.inject_into_body(body, history)
        # 流式路径: 在新流首 chunk 前发 sentinel
        sentinel = bridge.build_sse_sentinel(history)
    """

    def __init__(self, cfg: Optional[dict] = None):
        cfg = cfg or {}
        self.enabled: bool = cfg.get("enabled", True)
        self.stale_threshold_s: int = int(cfg.get("stale_threshold_seconds", 1800))  # 30min 默认
        self.max_history: int = int(cfg.get("max_history", 5))
        self.sentinel_enabled: bool = cfg.get("sentinel_enabled", True)
        self.inject_template: str = cfg.get("inject_template", DEFAULT_INJECT_TEMPLATE)
        self.version: str = cfg.get("version", "3.5.0")
        # v3.5.0: 切链 abort 默认开启 (防止旧模型慢回复晚到错配)
        self.abort_on_switch: bool = cfg.get("abort_on_switch", True)

        # 线程安全: in-memory stats
        self._lock = threading.RLock()
        self._stats = {
            "injections_total": 0,        # 成功 inject system message 次数
            "injections_fallback": 0,     # inject 失败回退到原 body 次数
            "stale_marks_total": 0,       # 标记 stale=True 次数
            "switch_records_total": 0,    # 累计 SwitchRecord 数
            "sentinels_sent_total": 0,    # SSE sentinel 发出次数
            "current_history_size": 0,    # 当前活跃 request 的 history 大小 (峰值)
            "aborts_total": 0,            # v3.5.0: 切链 abort 次数
            "reviews_total": 0,           # v3.5.0: 主动盘点请求次数
        }

        # v3.5.0: per-request 跟踪 (smr_request_id → 历史)
        # 不持久化, SMR 重启后清零. 主动盘点仅在请求生命周期内有效.
        # 用 bounded LRU 防止内存泄漏 (key=request_id, value=(request_meta, list[SwitchRecord]))
        self._max_tracked_requests = int(cfg.get("max_tracked_requests", 1000))
        self._tracked_requests: dict[str, tuple[dict, list[SwitchRecord]]] = {}
        self._tracked_order: list[str] = []  # FIFO

    # ── 配置热更新 ──

    def update_config(self, cfg: dict):
        """热更新配置 (admin API 用)"""
        with self._lock:
            if "enabled" in cfg:
                self.enabled = bool(cfg["enabled"])
            if "stale_threshold_seconds" in cfg:
                self.stale_threshold_s = int(cfg["stale_threshold_seconds"])
            if "max_history" in cfg:
                self.max_history = int(cfg["max_history"])
            if "sentinel_enabled" in cfg:
                self.sentinel_enabled = bool(cfg["sentinel_enabled"])
            if "inject_template" in cfg:
                self.inject_template = str(cfg["inject_template"])
            LOG.info("ContextBridge config updated: enabled=%s threshold=%ds max_history=%d",
                     self.enabled, self.stale_threshold_s, self.max_history)

    def get_config(self) -> dict:
        return {
            "enabled": self.enabled,
            "stale_threshold_seconds": self.stale_threshold_s,
            "max_history": self.max_history,
            "sentinel_enabled": self.sentinel_enabled,
            "abort_on_switch": self.abort_on_switch,  # v3.5.0
            "max_tracked_requests": self._max_tracked_requests,  # v3.5.0
            "inject_template_preview": self.inject_template[:200],
            "version": self.version,
        }

    # ── 核心: 注入 system message ──

    def build_inject_message(self, history: list[SwitchRecord]) -> Optional[dict]:
        """根据 history 构造要插入的 system message
        返回 None 表示不需要注入 (disabled 或 history 为空)
        """
        if not self.enabled or not history:
            return None
        try:
            n = len(history)
            blocks = "\n".join(_format_attempt_block(i, r) for i, r in enumerate(history))
            last_age_s = history[-1].age_seconds() if history else 0
            content = self.inject_template.format(
                version=self.version,
                n_attempts=n,
                attempt_blocks=blocks,
                age_minutes=last_age_s // 60,
                age_seconds=last_age_s,
            )
            return {"role": "system", "content": content}
        except Exception as e:
            LOG.warning("build_inject_message failed: %s", e)
            return None

    def inject_into_body(self, body: dict, history: list[SwitchRecord]) -> dict:
        """把 system message 插入 body.messages 头部, 复制 body 避免 mutate 原对象
        失败时 fallback 到原 body (fail-safe)
        """
        if not self.enabled:
            return body
        # trim 到 max_history
        if len(history) > self.max_history:
            history = history[-self.max_history:]
        try:
            msg = self.build_inject_message(history)
            if not msg:
                return body
            # copy body 避免 mutate
            new_body = {**body, "messages": [msg] + list(body.get("messages", []))}
            with self._lock:
                self._stats["injections_total"] += 1
                self._stats["current_history_size"] = max(
                    self._stats["current_history_size"], len(history)
                )
            return new_body
        except Exception as e:
            LOG.warning("inject_into_body failed, fallback to original: %s", e)
            with self._lock:
                self._stats["injections_fallback"] += 1
            return body

    # ── SSE 流式 sentinel ──

    def build_sse_sentinel(self, history: list[SwitchRecord]) -> str:
        """构造流式切换 sentinel (SSE 格式, 客户端可读 `_smr_bridge` key 识别切换)
        返回空字符串表示不发
        """
        if not self.sentinel_enabled or not history:
            return ""
        try:
            last = history[-1]
            payload = {
                "_smr_bridge": {
                    "version": self.version,
                    "switched_from_count": len(history),
                    "switched_from": [
                        {**r.to_dict(), "stale": r.is_stale(self.stale_threshold_s)}
                        for r in history
                    ],
                    "stale": last.is_stale(self.stale_threshold_s),
                    "age_seconds": last.age_seconds(),
                    "stale_threshold_seconds": self.stale_threshold_s,
                }
            }
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as e:
            LOG.warning("build_sse_sentinel failed: %s", e)
            return ""

    # ── 记录与统计 ──

    def record_switch(self, rec: SwitchRecord):
        """记录一次切换 (供 app.py 在切链时调用)
        注: v3.5.0 起, SwitchRecord 也通过 append_switch_to_request 存到 per-request 跟踪
        这里只更新全局 stats
        """
        with self._lock:
            self._stats["switch_records_total"] += 1
            if rec.is_stale(self.stale_threshold_s):
                self._stats["stale_marks_total"] += 1

    def record_sentinel_sent(self):
        with self._lock:
            self._stats["sentinels_sent_total"] += 1

    def get_stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    def reset_stats(self):
        with self._lock:
            for k in self._stats:
                if k != "current_history_size":
                    self._stats[k] = 0
            LOG.info("ContextBridge stats reset")

    # ── 非流式 response 辅助: 构造 switched_from 元数据 ──

    def build_switched_from_metadata(self, history: list[SwitchRecord]) -> dict:
        """给非流式 response._router 加 switched_from + stale 字段"""
        if not history:
            return {}
        last = history[-1]
        return {
            "switched_from": [
                {**r.to_dict(), "stale": r.is_stale(self.stale_threshold_s)}
                for r in history
            ],
            "stale": last.is_stale(self.stale_threshold_s),
            "age_seconds": last.age_seconds(),
            "stale_threshold_seconds": self.stale_threshold_s,
            "bridge_version": self.version,
        }

    # ── v3.5.0: Per-Request 跟踪 + 主动盘点 ──────────────────────

    def register_request(self, smr_request_id: str, request_meta: dict):
        """注册一个新请求 (openai_routes 入口调用)

        request_meta: {
            "chain_id": str,                  # 跨 candidate 一致
            "requested_model": str,            # 用户请求的 model
            "stream": bool,
            "request_start_time": float,      # epoch
        }
        """
        with self._lock:
            # FIFO 淘汰最老
            if smr_request_id in self._tracked_requests:
                self._tracked_order.remove(smr_request_id)
            elif len(self._tracked_order) >= self._max_tracked_requests:
                oldest = self._tracked_order.pop(0)
                self._tracked_requests.pop(oldest, None)
                LOG.debug("v3.5.0 request tracking evicted: %s (full)", oldest)
            self._tracked_requests[smr_request_id] = (request_meta, [])
            self._tracked_order.append(smr_request_id)

    def append_switch_to_request(self, smr_request_id: str, rec: SwitchRecord):
        """切链时调用, 追加 SwitchRecord 到该 request_id 的历史"""
        with self._lock:
            entry = self._tracked_requests.get(smr_request_id)
            if not entry:
                LOG.warning("v3.5.0 append_switch_to_request: smr_request_id=%s not tracked (可能已淘汰或重启)", smr_request_id)
                return
            _, history = entry
            history.append(rec)
            # 同步 stat
            self._stats["switch_records_total"] += 1
            if rec.is_stale(self.stale_threshold_s):
                self._stats["stale_marks_total"] += 1

    def record_abort(self):
        """切链 abort 计数 (openai_routes 在 aclose() 后调用)"""
        with self._lock:
            self._stats["aborts_total"] += 1

    def record_review(self):
        """主动盘点 endpoint 调用次数"""
        with self._lock:
            self._stats["reviews_total"] += 1

    def get_review_report(self, smr_request_id: str) -> Optional[dict]:
        """v3.5.0 主动盘点: 聚合报告

        返回 None 表示 request_id 未找到 (已淘汰 / SMR 重启 / 错的 ID)

        报告结构:
        {
            "smr_request_id": str,
            "chain_id": str,
            "requested_model": str,
            "stream": bool,
            "request_start_time": float,
            "request_age_seconds": int,
            "is_stale": bool,
            "switch_count": int,
            "switched_from": [SwitchRecord dict, ...],   # 全部参与过的 candidate
            "current_candidate": str | None,            # 最后一个 candidate (None=链耗尽)
            "accumulated_partial": str,                 # 全部切链前的 partial 拼接
            "bridge_version": str,
            "stale_threshold_seconds": int,
        }
        """
        with self._lock:
            entry = self._tracked_requests.get(smr_request_id)
            if not entry:
                return None
            request_meta, history = entry
            if not history:
                # 没有切链: 报告"无切换"
                return {
                    "smr_request_id": smr_request_id,
                    "chain_id": request_meta.get("chain_id"),
                    "requested_model": request_meta.get("requested_model"),
                    "stream": request_meta.get("stream", False),
                    "request_start_time": request_meta.get("request_start_time", 0),
                    "request_age_seconds": int(time.time() - request_meta.get("request_start_time", time.time())),
                    "is_stale": False,
                    "switch_count": 0,
                    "switched_from": [],
                    "current_candidate": request_meta.get("requested_model"),
                    "accumulated_partial": "",
                    "bridge_version": self.version,
                    "stale_threshold_seconds": self.stale_threshold_s,
                    "summary": "本次请求没有切链, 直接由首选模型完成.",
                }
            last = history[-1]
            is_stale = last.is_stale(self.stale_threshold_s)
            partials = "\n---\n".join(r.partial_text for r in history if r.partial_text)
            return {
                "smr_request_id": smr_request_id,
                "chain_id": request_meta.get("chain_id"),
                "requested_model": request_meta.get("requested_model"),
                "stream": request_meta.get("stream", False),
                "request_start_time": request_meta.get("request_start_time", 0),
                "request_age_seconds": last.age_seconds(),
                "is_stale": is_stale,
                "switch_count": len(history),
                "switched_from": [
                    {**r.to_dict(), "stale": r.is_stale(self.stale_threshold_s)}
                    for r in history
                ],
                "current_candidate": last.from_full_path,  # 最后一个失败的
                "accumulated_partial": partials,
                "bridge_version": self.version,
                "stale_threshold_seconds": self.stale_threshold_s,
                "summary": self._build_summary(history, is_stale),
            }

    def _build_summary(self, history: list[SwitchRecord], is_stale: bool) -> str:
        """生成自然语言摘要 (给 mainbot 拼飞书消息用)"""
        n = len(history)
        first = history[0]
        last = history[-1]
        if n == 1:
            desc = f"切了 1 次链: {first.from_full_path} → {last.from_full_path or '当前'}"
        else:
            desc = f"切了 {n} 次链: {first.from_full_path} → ... → {last.from_full_path}"
        age = last.age_seconds()
        age_str = f"{age // 60} 分 {age % 60} 秒" if age >= 60 else f"{age} 秒"
        stale_note = "⚠️ 信息可能已过期 (超过阈值)" if is_stale else "✅ 信息新鲜"
        return f"{desc}. 总耗时 {age_str}. {stale_note}."

    def list_tracked_requests(self, limit: int = 50) -> list[dict]:
        """列出当前在跟踪的 request_id (给 admin UI / debug 用)"""
        with self._lock:
            out = []
            for rid in self._tracked_order[-limit:][::-1]:
                entry = self._tracked_requests.get(rid)
                if not entry:
                    continue
                meta, hist = entry
                out.append({
                    "smr_request_id": rid,
                    "chain_id": meta.get("chain_id"),
                    "requested_model": meta.get("requested_model"),
                    "request_age_seconds": int(time.time() - meta.get("request_start_time", time.time())),
                    "switch_count": len(hist),
                })
            return out
