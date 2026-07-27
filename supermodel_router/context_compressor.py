"""
supermodel_router/context_compressor.py — 阴阳本源 5 层压缩 (v4.0.0 骨架)

# TODO: 5-layer L1-L5 实现 + 周天火候 + 会话指纹 + graceful drain
# L1 灵魂指针 ~10 tok
# L2 摘要记忆 ~50-100 tok
# L3 详细摘要 ~200 tok
# L4 完整本档 全量
# L5 可视化 0 tok
# 切链时注入会话指纹 ≤200 tok + graceful drain
# 灰度开关: ENABLE_CONTEXT_COMPRESSOR=0 (默认关)
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

LOG = logging.getLogger("context_compressor")


class ContextCompressor:
    """阴阳本源 5 层压缩器 (v4.0.0 骨架, Step 6b 补实现)"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.enabled = os.environ.get("ENABLE_CONTEXT_COMPRESSOR", "0") == "1"
        # TODO: L1-L5 内部状态 + 周天火候 + tenant allowlist

    def compress(self, text: str, target_layer: str = "L2") -> Optional[str]:
        """压缩到目标层 (L1/L2/L3/L4/L5)."""
        if not text:
            return None
        if target_layer == "L1":
            return self._compress_to_topic(text)
        if target_layer == "L2":
            return self._compress_to_summary(text)
        if target_layer == "L3":
            return self._compress_to_detail(text)
        if target_layer == "L4":
            return text  # 全量
        return None  # L5 由 admin UI 呈现, 不压缩

    # ── L1: 灵魂指针 ~10 tok ─────────────────────────────
    def _compress_to_topic(self, text: str) -> str:
        """L1 session_topic: 从消息流提取一句话主题 (~10 tok)"""
        import re
        # 取首行 non-empty + 首个句号/问号/换行前
        first = ""
        for line in text.splitlines():
            s = line.strip()
            if s:
                first = s
                break
        # 截首句 (中英文标点)
        m = re.match(r'^(.{1,40}?)[。.?!？!\n]', first)
        topic = m.group(1) if m else first[:40]
        return topic.strip()

    # ── L2: 摘要记忆 ~50-100 tok ────────────────────────
    def _compress_to_summary(self, text: str) -> str:
        """L2 summary: 关键决策/动作/约束 (~50-100 tok)"""
        import re
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        keywords = ("决定", "拍板", "选", "钦定", "必须", "禁", "不要", "改", "增加",
                    "删除", "TODO", "FIXME", "错误", "失败", "成功", "ok", "fail",
                    "->", "→", "决策", "结论")
        picks = []
        for l in lines:
            if any(k in l for k in keywords):
                picks.append(l)
            if len(" ".join(picks)) > 300:
                break
        # 兜底: 若无关键词命中, 取前 3 行
        if not picks:
            picks = lines[:3]
        summary = " | ".join(picks)
        return summary[:400]  # 硬截 ~100 tok

    # ── L3: 详细摘要 ~200 tok ────────────────────────────
    def _compress_to_detail(self, text: str) -> str:
        """L3 detail: 决策链+时间线 (~200 tok)"""
        import re
        lines = [l for l in text.splitlines() if l.strip()]
        # 抓时间戳/决策标记行
        ts_pat = re.compile(r'(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}:\d{2}(:\d{2})?|Step \d+|v\d+\.\d+)')
        marker_kw = ("决定", "拍板", "钦定", "改", "落", "验", "ok", "fail", "->", "→", "TODO")
        picks = []
        for l in lines:
            if ts_pat.search(l) or any(k in l for k in marker_kw):
                picks.append(l.strip())
        if not picks:
            picks = lines[:8]
        detail = "\n".join(picks[:12])
        return detail[:800]  # 硬截 ~200 tok

    # ── L4: 完整本档 全量 ───────────────────────────────
    def _expand_full(self, text: str) -> str:
        """L4: 直接返原文全量"""
        return text

    def expand(self, compressed: str, from_layer: str, to_layer: str) -> Optional[str]:
        """从低层恢复到高层 (若可能). Step 6b 补实现."""
        return None

    def session_fingerprint(self, messages) -> str:
        """生成会话指纹 (≤200 tok). Step 6c 实现.

        结构: sess:Nmsg|roles:<seq>|tools:<set>|head:<50>|tail:<50>|sha:<8>
        """
        import hashlib
        if not messages:
            return "sess:empty|sha:00000000"
        role_map = {"user": "u", "assistant": "a", "system": "s", "tool": "t"}
        role_seq = "".join(role_map.get(m.get("role", "?"), "?") for m in messages)[:40]
        tools = set()
        parts = []
        for m in messages:
            c = m.get("content", "")
            if isinstance(c, list):
                c = " ".join(str(x) for x in c)
            parts.append(str(c))
            if m.get("role") == "tool" and m.get("name"):
                tools.add(m["name"])
        full = "\n".join(parts)
        head = full[:50].replace("\n", " ")
        tail = full[-50:].replace("\n", " ") if len(full) > 50 else ""
        sha = hashlib.sha256(full.encode("utf-8", errors="ignore")).hexdigest()[:8]
        tools_str = ",".join(sorted(tools))[:40]
        fp = f"sess:{len(messages)}msg|roles:{role_seq}|tools:{tools_str}|head:{head}|tail:{tail}|sha:{sha}"
        return fp[:800]  # ~200 tok 硬截

    def graceful_drain(self, session_id: str, target_model: str) -> dict:
        """切链时优雅洗手, flush L1 → L2. Step 6c 实现."""
        import time
        try:
            if not hasattr(self, "_l1_buffer"):
                self._l1_buffer = {}
            if not hasattr(self, "_l2_buffer"):
                self._l2_buffer = {}
            l1 = self._l1_buffer.get(session_id, [])
            drained = len(l1)
            if drained:
                self._l2_buffer.setdefault(session_id, []).extend(l1)
                self._l1_buffer[session_id] = []
            return {
                "session_id": session_id,
                "target_model": target_model,
                "drained": drained,
                "l2_written": drained > 0,
                "ts": time.time(),
                "ok": True,
            }
        except Exception as e:
            return {"session_id": session_id, "target_model": target_model, "ok": False, "err": str(e)}

    def get_fire_intensity(self) -> float:
        """周天火候 0.0-1.0 (按本地时辰). Step 6c 实现.

        子时 23-01 / 午时 11-13 → 0.8
        寅时 03-05 / 申时 15-17 → 0.3
        其余 → 0.5
        """
        from datetime import datetime
        h = datetime.now().hour
        if h in (23, 0, 11, 12):
            return 0.8
        if h in (3, 4, 15, 16):
            return 0.3
        return 0.5

    def _compress_l5(self, messages) -> dict:
        """L5 本源压缩: session_fingerprint + 头尾各 1 轮原文, 中间全弃.

        触发: 上下文超 L4 阈值后仍需压缩.
        返回 schema 对齐 L1-L4: {layer, content, tokens, ok, ...}
        """
        if not messages:
            return {"layer": "L5", "content": "", "tokens": 0,
                    "msgs_kept": 0, "msgs_dropped": 0, "ok": True}
        try:
            fp = self.session_fingerprint(messages)
        except Exception as e:
            fp = f"sess:err|{e}"
        n = len(messages)
        head = messages[0] if n >= 1 else None
        tail = messages[-1] if n >= 2 else None

        def _fmt(m):
            if not m:
                return ""
            c = m.get("content", "")
            if isinstance(c, list):
                c = " ".join(str(x) for x in c)
            return f"[{m.get('role','?')}] {str(c)[:200]}"

        parts = [f"FP: {fp}"]
        if head:
            parts.append(f"HEAD: {_fmt(head)}")
        if tail and tail is not head:
            parts.append(f"TAIL: {_fmt(tail)}")
        content = "\n".join(parts)[:1200]  # FP≤200 + 头尾各≤50 ≈ 300 tok
        kept = 1 if head is tail else (2 if head and tail else (1 if head else 0))
        return {
            "layer": "L5",
            "content": content,
            "tokens": len(content) // 4,
            "msgs_kept": kept,
            "msgs_dropped": max(0, n - kept),
            "ok": True,
        }

    def get_layer(self, x=None, layer: Optional[str] = None) -> dict:
        """获取指定层内容. Step 6b/11 实现.

        x: messages(list) | text(str) | tokens(int, 用于阈值自选层)
        layer: L1/L2/L3/L4/L5, 不传则按 tokens 阈值自选
        返回: {layer, content, tokens, ok, ...}
        """
        if layer is None:
            if isinstance(x, int):
                tk = x
                if tk <= 20:
                    layer = "L1"
                elif tk <= 200:
                    layer = "L2"
                elif tk <= 800:
                    layer = "L3"
                elif tk <= 4000:
                    layer = "L4"
                else:
                    layer = "L5"
                return {"layer": layer, "content": "", "tokens": tk,
                        "ok": True, "note": "threshold-select"}
            layer = "L2"
        if layer == "L5":
            msgs = x if isinstance(x, list) else []
            return self._compress_l5(msgs)
        if isinstance(x, list):
            text = "\n".join(
                str(m.get("content", "")) if isinstance(m, dict) else str(m)
                for m in x
            )
        else:
            text = str(x) if x is not None else ""
        content = self.compress(text, target_layer=layer) or ""
        return {"layer": layer, "content": content,
                "tokens": len(content) // 4, "ok": True}
