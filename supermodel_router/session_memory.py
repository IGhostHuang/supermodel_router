"""
session_memory.py — L5 Context Continuity v2 (v3.23.0)

老大 2026-06-27 钦定: 切模型时保持上下文连贯, 跨 session 记忆关键事实

核心职责:
  1. 从历史 session 提取 key facts (user prefs / project context / open issues)
  2. 跨 session recall (新 session 注入相关 facts)
  3. ContextBridge 增强 (切模型时无缝迁移关键 facts)

存储: state/session_memory.json (持久化)
提取: 用便宜模型 + rule-based extraction
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

LOG = logging.getLogger(__name__)


@dataclass
class SessionFact:
    """从 session 提取的关键事实"""
    fact_id: str = field(default_factory=lambda: f"fact-{uuid.uuid4().hex[:8]}")
    session_id: str = ""
    category: str = "general"  # "user_pref" | "project_ctx" | "open_issue" | "decision" | "general"
    content: str = ""
    keywords: List[str] = field(default_factory=list)
    importance: float = 0.5      # 0-1, 由 LLM 评估
    created_at: float = field(default_factory=time.time)
    last_recalled_at: Optional[float] = None
    recall_count: int = 0
    source: str = "extracted"    # "extracted" | "manual" | "rule"
    
    def to_dict(self) -> dict:
        return asdict(self)


class SessionMemoryStore:
    """跨 session 记忆存储
    
    设计:
      - 持久化到 JSON (simple, 无外部依赖)
      - 按 category / keyword 检索
      - 自动衰减 (长期不 recall 的 facts 降权)
      - 防爆 (max 1000 facts, LRU 淘汰)
    """
    
    MAX_FACTS = 1000
    DECAY_INTERVAL_S = 7 * 86400    # 7 天未 recall → importance 衰减
    DECAY_FACTOR = 0.8              # 每次 -20%
    
    def __init__(self, state_dir: str = "/app/state"):
        self._state_dir = Path(state_dir)
        self._state_file = self._state_dir / "session_memory.json"
        self._facts: Dict[str, SessionFact] = {}
        self._load()
    
    def _load(self):
        try:
            if self._state_file.exists():
                data = json.loads(self._state_file.read_text())
                for fid, fdata in data.items():
                    self._facts[fid] = SessionFact(**fdata)
                LOG.info("SessionMemoryStore: 加载 %d facts", len(self._facts))
        except Exception as e:
            LOG.warning("SessionMemoryStore load failed: %s", e)
    
    def _save(self):
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(
                {fid: f.to_dict() for fid, f in self._facts.items()},
                indent=2, ensure_ascii=False
            ))
            tmp.replace(self._state_file)
        except Exception as e:
            LOG.warning("SessionMemoryStore save failed: %s", e)
    
    def add_fact(self, fact: SessionFact) -> str:
        if len(self._facts) >= self.MAX_FACTS:
            self._evict_lru()
        self._facts[fact.fact_id] = fact
        self._save()
        return fact.fact_id
    
    def recall(self, query_keywords: List[str], 
               top_k: int = 5,
               min_importance: float = 0.3) -> List[SessionFact]:
        """基于 keyword 召回相关 facts
        
        算法:
          - 每个 fact 的 keyword 跟 query 求 Jaccard 相似度
          - importance 排序
          - 命中 → recall_count+1, last_recalled_at 更新
        """
        if not query_keywords:
            return []
        
        scored = []
        for f in self._facts.values():
            if f.importance < min_importance:
                continue
            sim = self._jaccard(set(query_keywords), set(f.keywords))
            if sim > 0:
                scored.append((sim * f.importance, f))
        
        scored.sort(key=lambda x: -x[0])
        results = [f for _, f in scored[:top_k]]
        
        # 更新 recall 状态
        now = time.time()
        for f in results:
            f.last_recalled_at = now
            f.recall_count += 1
        self._save()
        
        return results
    
    def decay_stale_facts(self):
        """定期清理 (cron 触发)"""
        now = time.time()
        to_remove = []
        for f in self._facts.values():
            if f.last_recalled_at is None:
                last = f.created_at
            else:
                last = f.last_recalled_at
            if now - last > self.DECAY_INTERVAL_S:
                f.importance *= self.DECAY_FACTOR
                if f.importance < 0.1:
                    to_remove.append(f.fact_id)
        for fid in to_remove:
            del self._facts[fid]
        if to_remove:
            self._save()
            LOG.info("SessionMemoryStore: 衰减移除 %d stale facts", len(to_remove))
    
    def _evict_lru(self):
        """满 1000 时, 删 importance 最低的 10%"""
        if not self._facts:
            return
        n_evict = max(1, len(self._facts) // 10)
        sorted_facts = sorted(self._facts.values(), key=lambda f: f.importance)
        for f in sorted_facts[:n_evict]:
            del self._facts[f.fact_id]
        LOG.info("SessionMemoryStore: LRU 淘汰 %d facts", n_evict)
    
    @staticmethod
    def _jaccard(a: Set[str], b: Set[str]) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)
    
    def stats(self) -> dict:
        by_category: Dict[str, int] = {}
        for f in self._facts.values():
            by_category[f.category] = by_category.get(f.category, 0) + 1
        return {
            "total_facts": len(self._facts),
            "by_category": by_category,
            "top_recalled": sorted(
                [{"fact_id": f.fact_id, "category": f.category,
                  "content": f.content[:80], "recall_count": f.recall_count}
                 for f in self._facts.values()],
                key=lambda x: -x["recall_count"]
            )[:5],
        }


# ─── Fact extraction helpers ───────────────────────────────────────

async def extract_facts_from_session(
    messages: List[Dict[str, Any]], 
    session_id: str,
    extractor_fn=None,  # async (prompt, params) -> text
) -> List[SessionFact]:
    """从 session 提取 facts (用 LLM)
    
    提取 prompt 模板: 让 LLM 输出 JSON 列表 [{category, content, keywords, importance}]
    """
    if not messages or not extractor_fn:
        return []
    
    # 只提取最近 20 条 (节省 token)
    recent = messages[-20:] if len(messages) > 20 else messages
    text = "\n".join(
        f"[{m.get('role','?')}] {str(m.get('content',''))[:200]}"
        for m in recent
    )
    
    extract_prompt = (
        "请从以下对话中提取关键事实 (user preferences, project context, "
        "open issues, decisions), 输出 JSON list:\n"
        "格式: [{\"category\": \"user_pref\", \"content\": \"...\", "
        "\"keywords\": [\"k1\", \"k2\"], \"importance\": 0.7}]\n\n"
        "对话:\n" + text
    )
    
    try:
        result = await asyncio.wait_for(
            extractor_fn(extract_prompt, {"max_tokens": 500, "temperature": 0.2}),
            timeout=10.0,
        )
        # 解析 JSON
        facts_data = json.loads(result)
        if not isinstance(facts_data, list):
            return []
        
        facts = []
        for fd in facts_data[:10]:  # 最多 10 facts/session
            if not isinstance(fd, dict) or "content" not in fd:
                continue
            facts.append(SessionFact(
                session_id=session_id,
                category=fd.get("category", "general"),
                content=fd.get("content", ""),
                keywords=fd.get("keywords", []),
                importance=min(1.0, max(0.0, float(fd.get("importance", 0.5)))),
                source="extracted",
            ))
        return facts
    except Exception as e:
        LOG.warning("extract_facts_from_session 失败: %s", e)
        return []

import asyncio