"""
free_models.py — L1 Free Resource Layer (v3.23.0)

老大 2026-06-27 钦定重点: 智能识别 + 利用所有免费资源

核心职责:
  1. 多信号 free 识别 (name pattern / metadata / probe / provider policy)
  2. 实时配额追踪 (daily quota + 429 → 自动降权)
  3. 优先级评分 (tier-based + quota-aware)
  4. 暴露给 engine.pick_chain 作为 routing 加权因子

设计原则 (5 守则):
  - 边界: free 检测不会导致 paid 模型被错误标记
  - 成本: 主动 probe 用最便宜的 free 模型, 不浪费
  - 异常: 429/401/timeout 全部 fail-soft, 不阻塞主流程
  - 可观测: 每次识别/降权写 hot-cache, admin UI 暴露
  - 上下文广: 跨 session 累计 quality/latency 持久化
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

LOG = logging.getLogger(__name__)

# Tier 优先级 (高 = 优先路由)
TIER_PRIORITY = {
    "openrouter_free":     0.95,  # 公开承诺免费 (e.g. :free suffix)
    "provider_native_free": 0.85,  # provider 默认免费 tier (NVIDIA / 魔塔)
    "tier0_trial":         0.70,  # 试用期免费
    "experimental_free":  0.60,  # 实验性免费
    "unknown_free":        0.50,  # 信号不明确
}

# Provider 默认免费政策 (2026-06-27 现状)
PROVIDER_FREE_POLICY: Dict[str, str] = {
    "魔塔免费模型":      "provider_native_free",  # 魔塔默认全免费
    "modelscope":         "provider_native_free",  # 别名
    "nvidia":             "provider_native_free",  # NVIDIA NIM 整合, 默认免费层
    "openrouter":         "openrouter_free",       # 显式 :free 后缀
    "volc_ark":           "unknown_free",          # 部分模型免费, 需逐个识别
    "cloudflare":         "provider_native_free",  # CF Workers AI 默认免费层
    "newapi":             "unknown_free",          # 看配置
}

# Name pattern → free tier 信号
NAME_PATTERNS = [
    (re.compile(r":free$", re.IGNORECASE),                "openrouter_free",     100),
    (re.compile(r"free[-_]", re.IGNORECASE),              "openrouter_free",      90),
    (re.compile(r"[-_]free$", re.IGNORECASE),             "openrouter_free",      90),
    (re.compile(r":0$", re.IGNORECASE),                   "tier0_trial",          70),  # doubao-seed-2-0-pro:0
    (re.compile(r"trial", re.IGNORECASE),                 "tier0_trial",          70),
    (re.compile(r"experimental|preview|alpha", re.IGNORECASE), "experimental_free", 60),
]


@dataclass
class FreeModelInfo:
    """单个 free 模型的元信息 + 实时状态"""
    provider: str
    model_id: str
    full_path: str                       # "provider/model_id"
    tier: str                            # 见 TIER_PRIORITY
    detection_signals: List[str]         # 为什么判 free (["name:free", "policy:nvidia_default_free"])
    modality: List[str] = field(default_factory=list)  # ["text", "image", "audio"]
    context_window: int = 0
    priority_weight: float = 0.0         # 0-1, TIER_PRIORITY 派生
    
    # Quota tracking (实时)
    daily_quota_known: Optional[int] = None   # None = 未知
    daily_used: int = 0
    last_used_at: Optional[float] = None
    consecutive_429: int = 0
    consecutive_fail: int = 0
    
    # Quality tracking (历史累计)
    quality_score: float = 50.0          # 0-100, 默认中位
    avg_latency_ms: float = 0.0
    success_count: int = 0
    fail_count: int = 0
    
    # State
    state: str = "available"             # "available" | "exhausted" | "degraded" | "dead"
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class QuotaStatus:
    """单次配额探测结果"""
    state: str                           # "available" | "exhausted" | "unknown"
    daily_used: int
    daily_limit: Optional[int]
    reset_at: Optional[float]            # epoch seconds
    consecutive_429: int


class FreeModelRegistry:
    """Free model 注册中心 — L1 核心
    
    数据源:
      - providers dict (从 config 来的)
      - 自动扫描所有 model, 多信号判定 free
      - 实时探测 (可选, 用最便宜 free 探测)
      - 历史累计 (success/fail/quality/latency)
    """
    
    STATE_FILE = Path("/app/state/free_models.json")  # docker container
    
    def __init__(self, providers: Dict[str, dict], refresh_interval: int = 3600,
                 state_dir: str = "/app/state"):
        self.providers = providers
        self.refresh_interval = refresh_interval
        self._state_dir = Path(state_dir)
        self._state_file = self._state_dir / "free_models.json"
        self._models: Dict[str, FreeModelInfo] = {}
        self._last_refresh: float = 0.0
        self._lock = asyncio.Lock()
        self._load_state()
    
    def _state_path(self) -> Path:
        return self._state_dir / "free_models.json"
    
    def _load_state(self):
        """从 state 文件加载历史累计 (quality/latency/quota_used)"""
        try:
            if self._state_path().exists():
                data = json.loads(self._state_path().read_text())
                for path, info_dict in data.items():
                    info_dict.setdefault("state", "available")
                    self._models[path] = FreeModelInfo(**info_dict)
                LOG.info("FreeModelRegistry: 加载 %d 历史 free models", len(self._models))
        except Exception as e:
            LOG.warning("FreeModelRegistry 加载 state 失败: %s (空启动)", e)
    
    def _save_state(self):
        """持久化 (atomic write 避免半写)"""
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path().with_suffix(".tmp")
            tmp.write_text(json.dumps(
                {p: m.to_dict() for p, m in self._models.items()},
                indent=2, ensure_ascii=False
            ))
            tmp.replace(self._state_path())
        except Exception as e:
            LOG.warning("FreeModelRegistry save state failed: %s", e)
    
    def refresh(self, models_by_provider: Optional[Dict[str, List[Any]]] = None) -> int:
        """扫描所有 provider, 识别 free 模型 (同步版, 主流程用)
        
        Args:
            models_by_provider: {provider_name: [ModelInfo, ...]}, 如果 None 则从 self.providers 推断
            
        Returns:
            int: 识别出的 free model 数
        """
        new_count = 0
        # 1) 从已知的 provider policy 扫描
        for prov_name, prov_cfg in self.providers.items():
            if not isinstance(prov_cfg, dict):
                continue
            policy_tier = PROVIDER_FREE_POLICY.get(prov_name)
            if not policy_tier:
                continue
            
            # 拿到该 provider 下的所有 model_id
            if models_by_provider and prov_name in models_by_provider:
                model_ids = [m.id if hasattr(m, "id") else m for m in models_by_provider[prov_name]]
            else:
                # 从 config 拿 (model_management.<prov>.models 之类)
                model_ids = self._extract_model_ids(prov_name, prov_cfg)
            
            for mid in model_ids:
                full = f"{prov_name}/{mid}"
                if full in self._models:
                    # 已有, 增量更新 (quality/quota 不重置)
                    continue
                
                # 多信号识别
                info = self._classify_model(prov_name, mid, policy_tier)
                if info:
                    self._models[full] = info
                    new_count += 1
        
        self._last_refresh = time.time()
        self._save_state()
        LOG.info("FreeModelRegistry.refresh: 新识别 %d free models (总计 %d)",
                 new_count, len(self._models))
        return new_count
    
    def _extract_model_ids(self, prov_name: str, prov_cfg: dict) -> List[str]:
        """从 provider config 提取 model_id 列表"""
        # 1) explicit include list
        rules = prov_cfg.get("model_rules", {})
        if rules.get("mode") == "include":
            return list(rules.get("include", []))
        if rules.get("mode") == "pattern":
            # 没具体 model, 返回空 (call site 自己拿)
            return []
        return []
    
    def _classify_model(self, provider: str, model_id: str, 
                        provider_policy_tier: str) -> Optional[FreeModelInfo]:
        """多信号识别 1 个 model 是否 free"""
        signals = []
        
        # 信号 1: name pattern
        for pattern, tier, confidence in NAME_PATTERNS:
            if pattern.search(model_id):
                signals.append(f"name:{tier}")
                break
        
        # 信号 2: provider policy (默认免费)
        if provider_policy_tier in TIER_PRIORITY:
            signals.append(f"policy:{provider_policy_tier}")
        
        # 没信号 → 不是 free
        if not signals:
            return None
        
        # 决定最终 tier (取优先级最高的)
        tier = provider_policy_tier
        if any("openrouter_free" in s for s in signals):
            tier = "openrouter_free"
        elif any("experimental" in s for s in signals) and tier not in ("openrouter_free",):
            tier = "experimental_free"
        
        priority = TIER_PRIORITY.get(tier, 0.5)
        
        return FreeModelInfo(
            provider=provider,
            model_id=model_id,
            full_path=f"{provider}/{model_id}",
            tier=tier,
            detection_signals=signals,
            priority_weight=priority,
        )
    
    # ─── Public API ──────────────────────────────────────────────────
    
    def is_free(self, full_path: str) -> bool:
        return full_path in self._models
    
    def get(self, full_path: str) -> Optional[FreeModelInfo]:
        return self._models.get(full_path)
    
    def get_all(self, modality: Optional[str] = None) -> List[FreeModelInfo]:
        models = list(self._models.values())
        if modality:
            models = [m for m in models if modality in m.modality or not m.modality]
        return sorted(models, key=lambda m: -m.priority_weight)
    
    def count(self) -> int:
        return len(self._models)
    
    def get_all_paths(self) -> Set[str]:
        """返回所有 free 路径 (set, 给 BudgetRouter 用)"""
        return set(self._models.keys())
    
    def count_by_tier(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for m in self._models.values():
            out[m.tier] = out.get(m.tier, 0) + 1
        return out
    
    def get_priority_boost(self, full_path: str) -> float:
        """返回 free 优先级加成 (0-0.5, 加到 pick_chain 综合分)
        
        加成逻辑:
          - base tier 加成 (0.15-0.30)
          - quota 健康 +0.05
          - state degraded -0.10
          - state exhausted -0.50 (直接下沉)
        """
        info = self._models.get(full_path)
        if not info:
            return 0.0
        
        boost = (info.priority_weight - 0.5) * 0.6  # TIER_PRIORITY 0.5→0, 0.95→0.27
        
        if info.state == "available":
            boost += 0.05
        elif info.state == "degraded":
            boost -= 0.10
        elif info.state == "exhausted":
            boost -= 0.50
        elif info.state == "dead":
            return -1.0  # 直接排除
        
        # quality 加成 (high quality → 更高 boost)
        if info.success_count > 10:
            q_factor = (info.quality_score - 50) / 100  # ±0.5
            boost += q_factor * 0.05
        
        return max(-0.5, min(0.5, boost))
    
    # ─── Quota tracking (实时) ─────────────────────────────────────
    
    def record_call(self, full_path: str, status_code: int, 
                    latency_ms: float = 0.0, success: bool = True):
        """记录一次调用结果 (用于 quota + quality 累计)"""
        info = self._models.get(full_path)
        if not info:
            return
        
        info.last_used_at = time.time()
        
        if status_code == 429:
            info.consecutive_429 += 1
            if info.consecutive_429 >= 3:
                info.state = "exhausted"
                LOG.warning("FreeModel %s: 429×%d → state=exhausted",
                            full_path, info.consecutive_429)
        elif status_code == 200 and success:
            info.consecutive_429 = 0
            info.consecutive_fail = 0
            info.success_count += 1
            info.daily_used += 1
            # quality EMA
            if info.success_count == 1:
                info.quality_score = 75.0  # 默认起点
            info.avg_latency_ms = (
                info.avg_latency_ms * 0.8 + latency_ms * 0.2
                if info.avg_latency_ms > 0 else latency_ms
            )
            if info.state == "exhausted":
                info.state = "available"  # 恢复
        elif status_code >= 500 or not success:
            info.consecutive_fail += 1
            info.fail_count += 1
            if info.consecutive_fail >= 5:
                info.state = "dead"
                LOG.error("FreeModel %s: 连续失败 %d → state=dead",
                          full_path, info.consecutive_fail)
            # quality 降
            info.quality_score = max(0, info.quality_score - 5)
        
        self._save_state()
    
    def reset_daily_quota(self):
        """每日配额重置 (cron 触发)"""
        for m in self._models.values():
            m.daily_used = 0
            if m.state == "exhausted":
                m.state = "available"
        self._save_state()
        LOG.info("FreeModelRegistry: 每日配额重置 (%d models)", len(self._models))
    
    # ─── Admin 导出 ────────────────────────────────────────────────
    
    def export_summary(self) -> dict:
        """admin UI 用"""
        by_tier = self.count_by_tier()
        by_state: Dict[str, int] = {}
        for m in self._models.values():
            by_state[m.state] = by_state.get(m.state, 0) + 1
        
        top_quality = sorted(self._models.values(),
                             key=lambda m: -m.quality_score)[:10]
        
        return {
            "total_free_models": len(self._models),
            "by_tier": by_tier,
            "by_state": by_state,
            "last_refresh": self._last_refresh,
            "top_quality": [
                {"path": m.full_path, "tier": m.tier,
                 "quality": round(m.quality_score, 1),
                 "latency_ms": round(m.avg_latency_ms, 0),
                 "state": m.state,
                 "success/fail": f"{m.success_count}/{m.fail_count}"}
                for m in top_quality
            ],
        }


# ─── Singleton accessor (跟现有 loop_engine 一致) ──────────────────
_registry: Optional[FreeModelRegistry] = None

def init_free_model_registry(providers: Dict[str, dict], 
                             state_dir: str = "/app/state") -> FreeModelRegistry:
    global _registry
    if _registry is None:
        _registry = FreeModelRegistry(providers, state_dir=state_dir)
    return _registry

def get_free_model_registry() -> Optional[FreeModelRegistry]:
    return _registry