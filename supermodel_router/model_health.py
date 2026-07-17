"""
supermodel_router/model_health.py — 模型健康度管理 (v3.15.0)

老大 2026-06-24 钦定:
- 健康度指标 5 个: consecutive_fails / rolling_success_rate / ewma_latency_ms / last_success_at / last_fail_at
- 路由时跳过非健康模型 (降低延迟)
- **健康度恢复检测** (half-open circuit breaker 模式):
  - SKIP 状态到期 → HALF_OPEN → background probe (每 30s)
  - probe 成功 → HEALTHY (重置)
  - probe 失败 → SKIP 指数退避 (60s → 120s → 240s → 300s cap)
- 持久化: state/model_health.json (类似 penalty_state.json)
- 集成: engine.pick_chain() 路由前 filter + engine.record_success/failure 联动

设计参考: Hystrix / Resilience4j circuit breaker half-open state
"""
import asyncio
import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

LOG = logging.getLogger("model_health")

STATE_FILE = "model_health.json"


class HealthState(str, Enum):
    HEALTHY = "healthy"       # 正常路由
    DEGRADED = "degraded"     # 降权路由 (penalty multiplier)
    SKIP = "skip"             # 跳过路由 (circuit open)
    HALF_OPEN = "half_open"   # 恢复检测中 (probe 进行, 不接真实流量)
    # v3.29: Terminal States — 永久故障, 不会因 cooldown 到期自动恢复
    BANNED = "banned"                 # key 被 ban
    EXPIRED = "expired"               # key 过期
    CREDITS_EXHAUSTED = "credits_exhausted"  # 余额/额度耗尽

    @classmethod
    def is_terminal(cls, state: str) -> bool:
        """终端态: 不会自动恢复, 需要手动 admin reset"""
        return state in (cls.BANNED.value, cls.EXPIRED.value, cls.CREDITS_EXHAUSTED.value)


# 默认阈值 (可被 config 覆盖)
DEFAULT_CONFIG = {
    "consecutive_fails_skip": 3,       # 连续失败 N 次 → SKIP
    "skip_initial_seconds": 60,        # 首次 SKIP 持续时间
    "skip_max_seconds": 300,            # SKIP 最长 (cap)
    "skip_backoff_factor": 2.0,         # 指数退避倍数
    "rolling_window_size": 100,         # 滚动窗口大小
    "rolling_rate_skip_below": 30.0,    # 滚动成功率 < N% → SKIP (sample ≥ min_sample)
    "rolling_min_sample": 10,           # 最小样本数 (样本不足不判定)
    "ewma_alpha": 0.3,                  # EWMA α (新值权重)
    "ewma_latency_skip_ms": 60000.0,    # EWMA 延迟 > N ms → SKIP
    "degraded_consecutive_success_recover": 3,  # DEGRADED 连续 N 次成功 → HEALTHY
    "probe_interval_seconds": 30,       # background checker 扫描周期
    "probe_timeout_seconds": 10,        # 单 model probe 超时
    "degraded_penalty": 0.4,            # DEGRADED 模型 penalty multiplier
    # v3.16.0: provider 级自动禁用
    "provider_disable_threshold_seconds": 604800,  # 7 天 — 所有 model SKIP 持续 ≥ 7 天 → 禁用 provider
    "provider_check_min_models": 3,                # 至少 N 个 model 才判定 (避免 1 model provider 误判)
    "provider_check_interval_seconds": 600,        # 每 10 分钟扫 1 次 (跟 probe_interval 错开)
    "provider_check_enabled": True,                # 全局开关 (false = 只检测不自动禁用)
}


@dataclass
class ModelHealth:
    """单个 model 健康度状态"""
    path: str                            # "provider/model_id"
    state: str = HealthState.HEALTHY.value
    consecutive_fails: int = 0
    consecutive_success: int = 0
    total_calls: int = 0
    total_success: int = 0
    total_fail: int = 0
    recent_window: deque = field(default_factory=lambda: deque(maxlen=DEFAULT_CONFIG["rolling_window_size"]))
    ewma_latency_ms: float = 0.0
    last_success_at: float = 0.0
    last_fail_at: float = 0.0
    last_latency_ms: float = 0.0
    skip_until: float = 0.0              # SKIP 到期时间戳
    skip_count: int = 0                  # 累计 SKIP 次数 (用于退避)
    cooldown_seconds: int = DEFAULT_CONFIG["skip_initial_seconds"]  # 当前 cooldown
    first_skip_at: float = 0.0           # v3.16.0: 首次进入 SKIP 的时间戳 (provider 级禁用判定用)
    quota_skip_until: float = 0.0        # v3.18.0: 配额耗尽导致的长 SKIP (续费后由 admin 手动清 0)
    quota_type: str = ""                 # v3.18.0: 配额类型 (monthly / weekly / daily / token_plan / balance)
    degrade_weight: float = 0.0         # v3.21.0: 降级权重, 每 tick 衰减
    last_probe_at: float = 0.0
    last_probe_success: bool = False
    last_probe_error: str = ""
    updated_at: float = 0.0

    def rolling_success_rate(self) -> float:
        if len(self.recent_window) == 0:
            return 100.0  # 无数据 → 假定健康
        ok = sum(1 for x in self.recent_window if x == 1)
        return (ok / len(self.recent_window)) * 100.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["recent_window"] = list(self.recent_window)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "ModelHealth":
        rw = data.pop("recent_window", [])
        mh = cls(**data)
        mh.recent_window = deque(rw, maxlen=DEFAULT_CONFIG["rolling_window_size"])
        return mh


class ModelHealthManager:
    """全局单例 (类似 public_key_manager)

    用法:
        from .model_health import get_model_health_manager
        mhm = get_model_health_manager()
        mhm.record_success("openrouter/gpt-4o", latency_ms=1200)
        mhm.record_failure("openrouter/gpt-4o", latency_ms=30000, error="timeout")
        if mhm.should_skip("openrouter/gpt-4o"): ...
        filtered = mhm.filter_candidates(candidates)
    """
    _instance: Optional["ModelHealthManager"] = None
    _instance_lock = threading.Lock()

    def __init__(self, state_dir: Path, config: Optional[dict] = None):
        self.state_dir = Path(state_dir)
        self.state_file = self.state_dir / STATE_FILE
        self._health: Dict[str, ModelHealth] = {}
        self._lock = threading.RLock()
        self.cfg = {**DEFAULT_CONFIG, **(config or {})}
        # probe 函数: 由 app.py 在启动时注入 (async, 接受 path, 返回 bool)
        self._probe_func: Optional[Callable] = None
        # v3.16.0: provider 自动禁用 callback (由 app.py 注入, 调 config.disable_provider)
        self._provider_disable_callback: Optional[Callable] = None
        self._last_provider_check: float = 0.0
        # v3.29: Anti-thundering-herd — 防并发失败重复触发
        self._failure_debounce: Dict[str, float] = {}  # path → last_fail_ts
        self._debounce_window: float = 5.0  # 5 秒内同一 path 不重复计数
        self._bg_task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None  # asyncio.Event (loop 启动后初始化)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._load()  # 注: _load 必须在 __init__ 之前 def (Python class 内部方法顺序)

    # ---- 持久化 (必须在 __init__ 之前 def, 否则 __init__ 调 self._load() 时 class 还没这个 attr) ----

    def _load(self):
        if not self.state_file.exists():
            LOG.info("ModelHealthManager: no state file, starting fresh")
            return
        try:
            data = json.loads(self.state_file.read_text())
            with self._lock:
                for path, entry in data.get("health", {}).items():
                    self._health[path] = ModelHealth.from_dict(dict(entry, path=path))
            LOG.info("ModelHealthManager: loaded %d model health records", len(self._health))
        except Exception as e:
            LOG.warning("ModelHealthManager: load failed: %s (starting fresh)", e)

    # ---- 单例 ----

    @classmethod
    def get_instance(cls) -> "ModelHealthManager":
        with cls._instance_lock:
            if cls._instance is None:
                # 延迟到第一次 get_instance() 时初始化 (需要 state_dir)
                raise RuntimeError("ModelHealthManager not initialized, call init_manager(state_dir) first")
            return cls._instance

    @classmethod
    def init_manager(cls, state_dir: Path, config: Optional[dict] = None) -> "ModelHealthManager":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(state_dir, config)
            return cls._instance

    @classmethod
    def reset(cls):
        """测试用"""
        with cls._instance_lock:
            if cls._instance and cls._instance._bg_task:
                cls._instance.stop_background_checker()
            cls._instance = None

    # ---- 记录 success / failure (旧 _load 已上移, 见 line 128) ----

    def _save_async(self):
        """fire-and-forget save (调用方不阻塞)"""
        def _save():
            try:
                self.state_dir.mkdir(parents=True, exist_ok=True)
                data = {
                    "saved_at": time.time(),
                    "health": {p: mh.to_dict() for p, mh in self._health.items()},
                }
                tmp = self.state_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=2))
                tmp.replace(self.state_file)
            except Exception as e:
                LOG.warning("ModelHealthManager: save failed: %s", e)
        threading.Thread(target=_save, daemon=True).start()

    # ---- 记录 success / failure ----

    def _ensure(self, path: str) -> ModelHealth:
        if path not in self._health:
            self._health[path] = ModelHealth(path=path)
        return self._health[path]

    def record_success(self, path: str, latency_ms: float = 0.0):
        """引擎调用: 一次成功"""
        with self._lock:
            mh = self._ensure(path)
            mh.total_calls += 1
            mh.total_success += 1
            mh.consecutive_fails = 0
            mh.consecutive_success += 1
            mh.last_success_at = time.time()
            mh.last_latency_ms = latency_ms
            mh.recent_window.append(1)
            # EWMA 延迟
            if mh.ewma_latency_ms == 0:
                mh.ewma_latency_ms = latency_ms
            else:
                α = self.cfg["ewma_alpha"]
                mh.ewma_latency_ms = α * latency_ms + (1 - α) * mh.ewma_latency_ms
            # 状态转换
            old_state = mh.state
            if mh.state == HealthState.SKIP.value:
                # SKIP 期间不应有 success (路由会跳过), 但如果发生 (probe 成功)
                # v3.29: Success-decay — probe 成功时 halve failure count + 降 cooldown
                mh.consecutive_fails = max(0, mh.consecutive_fails // 2)
                mh.cooldown_seconds = max(
                    self.cfg["skip_initial_seconds"],
                    mh.cooldown_seconds // 2
                )
                if mh.consecutive_fails == 0:
                    mh.state = HealthState.HEALTHY.value
                    mh.skip_until = 0
                    mh.skip_count = 0
                    LOG.info("model_health: %s SKIP → HEALTHY (success-decay cleared)", path)
                else:
                    LOG.info("model_health: %s SKIP success-decay: fails→%d, cooldown→%ds",
                             path, mh.consecutive_fails, mh.cooldown_seconds)
            elif mh.state == HealthState.HALF_OPEN.value:
                # HALF_OPEN 期间的成功说明 probe 成功
                mh.state = HealthState.HEALTHY.value
                mh.consecutive_success = 0
                mh.skip_until = 0
                mh.cooldown_seconds = self.cfg["skip_initial_seconds"]
                LOG.info("model_health: %s HALF_OPEN → HEALTHY (probe success via record)", path)
            elif mh.state == HealthState.DEGRADED.value:
                if mh.consecutive_success >= self.cfg["degraded_consecutive_success_recover"]:
                    mh.state = HealthState.HEALTHY.value
                    mh.consecutive_success = 0
                    LOG.info("model_health: %s DEGRADED → HEALTHY (consecutive_success=%d)", path, mh.consecutive_success)
            else:  # HEALTHY
                mh.state = HealthState.HEALTHY.value
            mh.updated_at = time.time()
            # v3.16.0: 不再是 SKIP, 清 first_skip_at (provider 级禁用判定用)
            if mh.state != HealthState.SKIP.value:
                mh.first_skip_at = 0.0
            if old_state != mh.state:
                LOG.info("model_health: %s %s → %s (success, latency=%.0fms)", path, old_state, mh.state, latency_ms)
        self._save_async()

    def record_failure(self, path: str, latency_ms: float = 0.0, error: str = "",
                       quota_exhausted: bool = False, quota_type: str = ""):
        """引擎调用: 一次失败"""
        with self._lock:
            # v3.29: Anti-thundering-herd — 同一 path 在 debounce_window 内重复失败只计 1 次
            now = time.time()
            last_ts = self._failure_debounce.get(path, 0)
            if now - last_ts >= self._debounce_window:
                self._failure_debounce[path] = now
            else:
                # 已在窗口内, 跳过 consecutive_fails++, 但仍记录统计
                mh = self._health.get(path)
                if mh:
                    mh.last_fail_at = now
                    mh.total_calls += 1
                    mh.recent_window.append(0)
                    if latency_ms > 0 and mh.ewma_latency_ms > 0:
                        α = self.cfg["ewma_alpha"]
                        mh.ewma_latency_ms = α * latency_ms + (1 - α) * mh.ewma_latency_ms
                return  # debounce skip
            mh = self._ensure(path)
            mh.total_calls += 1
            mh.total_fail += 1
            mh.consecutive_success = 0
            mh.consecutive_fails += 1
            mh.last_fail_at = time.time()
            mh.last_latency_ms = latency_ms
            mh.recent_window.append(0)
            # EWMA 延迟 (失败也计入, 用于识别慢失败)
            if latency_ms > 0:
                if mh.ewma_latency_ms == 0:
                    mh.ewma_latency_ms = latency_ms
                else:
                    α = self.cfg["ewma_alpha"]
                    mh.ewma_latency_ms = α * latency_ms + (1 - α) * mh.ewma_latency_ms
            # 状态转换
            old_state = mh.state
            now = time.time()
            # 1) 配额耗尽 → 长 SKIP (不受普通 cooldown 影响)
            if quota_exhausted and quota_type:
                mh.quota_type = quota_type
                # 配额 skip 时长: 根据 type 映射
                quota_durations = {
                    "daily": 86400,
                    "weekly": 604800,
                    "monthly": 2592000,
                    "token_plan": 86400,
                    "balance": 86400,
                }
                quota_duration = quota_durations.get(quota_type, 86400)
                mh.quota_skip_until = now + quota_duration
                mh.skip_until = now + quota_duration  # 普通 skip 也同步, 确保路由不走
                mh.skip_count += 1
                mh.state = HealthState.SKIP.value
                mh.last_probe_success = False
                mh.last_probe_error = f"quota_exhausted({quota_type})"[:200]
                LOG.warning("model_health: %s → SKIP (quota_exhausted=%s, skip_until=%.0f)",
                            path, quota_type, mh.quota_skip_until)
            # 2) SKIP 到期检查 → HALF_OPEN
            elif mh.state == HealthState.SKIP.value and now >= mh.skip_until:
                mh.state = HealthState.HALF_OPEN.value
                LOG.info("model_health: %s SKIP → HALF_OPEN (cooldown expired)", path)
                old_state = mh.state
            # 3) HALF_OPEN + 失败 → 重新 SKIP (指数退避)
            elif mh.state == HealthState.HALF_OPEN.value:
                mh.skip_count += 1
                mh.cooldown_seconds = min(
                    int(mh.cooldown_seconds * self.cfg["skip_backoff_factor"]),
                    self.cfg["skip_max_seconds"],
                )
                mh.skip_until = now + mh.cooldown_seconds
                mh.state = HealthState.SKIP.value
                mh.last_probe_success = False
                mh.last_probe_error = error[:200]
                LOG.warning("model_health: %s HALF_OPEN → SKIP (cooldown=%ds, skip_count=%d, err=%s)",
                            path, mh.cooldown_seconds, mh.skip_count, error[:80])
            # 4) HEALTHY/DEGRADED + 连续失败 ≥ 阈值 → SKIP
            elif mh.state in (HealthState.HEALTHY.value, HealthState.DEGRADED.value):
                if mh.consecutive_fails >= self.cfg["consecutive_fails_skip"]:
                    mh.skip_count += 1
                    mh.skip_until = now + mh.cooldown_seconds
                    mh.state = HealthState.SKIP.value
                    LOG.warning("model_health: %s %s → SKIP (consecutive_fails=%d, cooldown=%ds, err=%s)",
                                path, old_state, mh.consecutive_fails, mh.cooldown_seconds, error[:80])
                elif mh.consecutive_fails >= 1:
                    mh.state = HealthState.DEGRADED.value
                    if old_state != HealthState.DEGRADED.value:
                        LOG.info("model_health: %s → DEGRADED (consecutive_fails=%d)", path, mh.consecutive_fails)
            # 5) 滚动成功率 + EWMA 检查 (仅 HEALTHY/DEGRADED)
            if mh.state in (HealthState.HEALTHY.value, HealthState.DEGRADED.value):
                rate = mh.rolling_success_rate()
                if len(mh.recent_window) >= self.cfg["rolling_min_sample"] and rate < self.cfg["rolling_rate_skip_below"]:
                    mh.skip_count += 1
                    mh.skip_until = now + mh.cooldown_seconds
                    mh.state = HealthState.SKIP.value
                    LOG.warning("model_health: %s → SKIP (rolling_rate=%.1f%% < %.1f%%)",
                                path, rate, self.cfg["rolling_rate_skip_below"])
                elif mh.ewma_latency_ms > self.cfg["ewma_latency_skip_ms"] and mh.ewma_latency_ms > 0:
                    mh.skip_count += 1
                    mh.skip_until = now + self.cfg["skip_initial_seconds"]
                    mh.state = HealthState.SKIP.value
                    LOG.warning("model_health: %s → SKIP (ewma_latency=%.0fms > %.0fms)",
                                path, mh.ewma_latency_ms, self.cfg["ewma_latency_skip_ms"])
            mh.updated_at = time.time()
            # v3.16.0: 进入 SKIP 时记录 first_skip_at (首次, 续期不变)
            if mh.state == HealthState.SKIP.value and mh.first_skip_at == 0.0:
                mh.first_skip_at = time.time()
        self._save_async()

    # ---- 路由查询 ----

    def should_skip(self, path: str) -> bool:
        """路由前查询: True = 跳过此 model"""
        with self._lock:
            mh = self._health.get(path)
            if not mh:
                return False
            # v3.29: Terminal States — 永久跳过, 不回自动恢复
            if HealthState.is_terminal(mh.state):
                return True
            now = time.time()
            if mh.state == HealthState.SKIP.value:
                if now >= mh.skip_until:
                    # 到期 → 标记 HALF_OPEN (等 probe)
                    mh.state = HealthState.HALF_OPEN.value
                    LOG.info("model_health: %s SKIP → HALF_OPEN (cooldown expired during should_skip)", path)
                    return False
                return True
            if mh.state == HealthState.HALF_OPEN.value:
                # HALF_OPEN 期间不接真实流量, 路由跳过
                return True
            return False

    def get_penalty_multiplier(self, path: str) -> float:
        """DEGRADED 模型返回 penalty multiplier (0..1, 用于综合分乘法)
        HEALTHY=1.0 (无惩罚), DEGRADED=0.6 (降权 40%), SKIP=0.0
        """
        with self._lock:
            mh = self._health.get(path)
            if not mh:
                return 1.0
            if mh.state == HealthState.SKIP.value:
                return 0.0
            if mh.state == HealthState.DEGRADED.value:
                return 1.0 - self.cfg["degraded_penalty"]
            return 1.0

    def filter_candidates(self, candidates: list) -> list:
        """路由前过滤: 移除 SKIP + 给 DEGRADED 加 penalty"""
        if not candidates:
            return candidates
        result = []
        skipped = []
        for c in candidates:
            path = f"{c.provider_name}/{c.model_id}"
            if self.should_skip(path):
                skipped.append(path)
                continue
            # 给 CandidateResult 加 penalty 字段 (如果有的话, 修改原对象)
            mult = self.get_penalty_multiplier(path)
            if hasattr(c, "score") and mult < 1.0:
                c.score = c.score * mult
            result.append(c)
        if skipped:
            LOG.debug("model_health: filtered out %d unhealthy: %s", len(skipped), skipped[:5])
        return result

    # ---- 后台恢复检测 (probe) ----

    def set_probe_func(self, func: Callable):
        """app.py 启动时注入 probe 函数

        probe_func(path: str) -> bool (async): 返回 True = 健康
        """
        self._probe_func = func

    def set_provider_disable_callback(self, func: Callable):
        """v3.16.0: app.py 启动时注入 provider 自动禁用 callback

        callback(provider_name: str, reason: str) -> bool: 返回 True = 已禁用
        """
        self._provider_disable_callback = func
        LOG.info("model_health: provider_disable_callback set")

    def start_background_checker(self, loop: asyncio.AbstractEventLoop):
        """在 FastAPI lifespan 中启动 background checker"""
        if self._bg_task and not self._bg_task.done():
            LOG.warning("ModelHealthManager: background checker already running")
            return
        self._loop = loop
        self._stop_event = asyncio.Event()  # 必须 loop 启动后建
        self._bg_task = loop.create_task(self._background_loop())
        LOG.info("ModelHealthManager: background checker started (interval=%ds)", self.cfg["probe_interval_seconds"])

    def stop_background_checker(self):
        if self._stop_event is not None:
            self._stop_event.set()
        if self._bg_task and not self._bg_task.done():
            self._bg_task.cancel()

    async def _background_loop(self):
        """每 N 秒扫描一次 SKIP/HALF_OPEN 状态, 触发 probe
        v3.16.0: 同时按 provider_check_interval_seconds 跑 provider 自动禁用扫描
        """
        while not (self._stop_event and self._stop_event.is_set()):
            try:
                await self._scan_and_probe()
            except Exception as e:
                LOG.warning("model_health background loop error: %s", e)
            # v3.16.0: provider 级扫描 (错开 interval)
            now = time.time()
            pc_interval = self.cfg.get("provider_check_interval_seconds", 600)
            if now - self._last_provider_check >= pc_interval:
                try:
                    await self._scan_and_disable_providers()
                except Exception as e:
                    LOG.warning("model_health provider disable scan error: %s", e)
                self._last_provider_check = now
            try:
                if self._stop_event is not None:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self.cfg["probe_interval_seconds"])
                    break  # stop event 被 set → 退出
            except asyncio.TimeoutError:
                pass  # 正常超时, 继续下一轮

    async def _scan_and_disable_providers(self):
        """v3.16.0: 扫描所有 provider, 找满足条件的 (所有 model SKIP + 持续 ≥ threshold) → 自动禁用

        需要 app.py 注入 _provider_disable_callback (调 config.disable_provider)
        callback signature: () -> list[disabled_provider_names]
        """
        cb = self._provider_disable_callback
        if not cb or not self.cfg.get("provider_check_enabled", True):
            return
        try:
            # callback 是 sync, 在 executor 跑避免阻塞 event loop
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, cb)
            if result:
                LOG.warning("model_health: auto-disabled providers: %s", result)
        except Exception as e:
            LOG.warning("provider_disable_callback error: %s", e)

    def check_provider_disable_candidates(self, registry) -> list:
        """v3.16.0: 扫描 registry 所有 provider, 返满足条件的 candidates

        条件:
        1. provider enabled (未禁用)
        2. provider 至少 N 个 model (默认 3, 避免 1 model provider 误判)
        3. 该 provider 所有 model 当前 state = SKIP
        4. 最早 first_skip_at 到 now ≥ threshold (默认 7 天)

        返 list of {"provider": str, "reason": str, "duration_seconds": float, "skip_model_count": int}
        """
        threshold = self.cfg.get("provider_disable_threshold_seconds", 604800)
        min_models = self.cfg.get("provider_check_min_models", 3)
        now = time.time()
        candidates = []
        # 按 provider 分组 (从 _health keys)
        by_provider: Dict[str, List[str]] = {}
        with self._lock:
            for path in self._health.keys():
                provider = path.split("/", 1)[0] if "/" in path else path
                by_provider.setdefault(provider, []).append(path)
        # 跟 registry._providers 实际 provider 比对
        if registry is None or not hasattr(registry, "_providers"):
            return candidates
        for pname, ps in registry._providers.items():
            # 跳过已禁用的 (已经 disable_provider 处理过)
            if not getattr(ps, "enabled", True):
                continue
            paths = by_provider.get(pname, [])
            if len(paths) < min_models:
                continue
            all_skip = True
            oldest_first_skip = now
            valid_count = 0
            for path in paths:
                mh = self._health.get(path)
                if not mh or mh.state != HealthState.SKIP.value:
                    all_skip = False
                    break
                valid_count += 1
                if mh.first_skip_at > 0:
                    oldest_first_skip = min(oldest_first_skip, mh.first_skip_at)
            if not all_skip:
                continue
            duration = now - oldest_first_skip
            if duration >= threshold:
                days = duration / 86400.0
                reason = (
                    f"Auto-disabled: all {valid_count} models in SKIP state for "
                    f"{days:.1f} days (threshold={threshold/86400:.0f}d). "
                    f"Re-enable via admin UI after manual investigation."
                )
                candidates.append({
                    "provider": pname,
                    "reason": reason,
                    "duration_seconds": duration,
                    "skip_model_count": valid_count,
                })
        return candidates

    def get_provider_health_summary(self, registry) -> list:
        """v3.16.0: admin UI 用 — 返每个 provider 健康度汇总 (供 provider 列表渲染)"""
        threshold = self.cfg.get("provider_disable_threshold_seconds", 604800)
        now = time.time()
        # 按 provider 分组 _health
        by_provider: Dict[str, Dict[str, int]] = {}
        with self._lock:
            for path, mh in self._health.items():
                provider = path.split("/", 1)[0] if "/" in path else path
                if provider not in by_provider:
                    by_provider[provider] = {"healthy": 0, "degraded": 0, "skip": 0, "half_open": 0, "total": 0}
                by_provider[provider][mh.state] = by_provider[provider].get(mh.state, 0) + 1
                by_provider[provider]["total"] += 1
        out = []
        if registry is not None and hasattr(registry, "_providers"):
            for pname, ps in registry._providers.items():
                stats = by_provider.get(pname, {"healthy": 0, "degraded": 0, "skip": 0, "half_open": 0, "total": 0})
                # 最早 first_skip_at (for provider 即将禁用提示)
                oldest_first_skip = now
                for path in by_provider.get(pname, []):
                    mh = self._health.get(path)
                    if mh and mh.first_skip_at > 0:
                        oldest_first_skip = min(oldest_first_skip, mh.first_skip_at)
                duration = now - oldest_first_skip if stats.get("skip", 0) > 0 else 0
                will_disable = (
                    stats.get("skip", 0) >= self.cfg.get("provider_check_min_models", 3)
                    and stats.get("healthy", 0) == 0
                    and stats.get("degraded", 0) == 0
                    and stats.get("half_open", 0) == 0
                    and duration >= threshold
                )
                out.append({
                    "provider": pname,
                    "enabled": getattr(ps, "enabled", True),
                    "model_states": stats,
                    "oldest_skip_age_seconds": round(duration, 1) if duration > 0 else 0,
                    "will_disable_in": round(threshold - duration, 1) if (duration > 0 and duration < threshold) else 0,
                })
        return out

    async def _scan_and_probe(self):
        """扫描所有 model, 对 SKIP 到期/HALF_OPEN 触发 probe"""
        if not self._probe_func:
            return
        now = time.time()
        to_probe = []
        with self._lock:
            for path, mh in self._health.items():
                if mh.state == HealthState.SKIP.value and now >= mh.skip_until:
                    to_probe.append(path)
                elif mh.state == HealthState.HALF_OPEN.value:
                    to_probe.append(path)
        if not to_probe:
            return
        LOG.info("model_health: probing %d models for recovery: %s", len(to_probe), to_probe[:5])
        # 并发 probe (上限 5 个)
        sem = asyncio.Semaphore(5)
        async def _one(path):
            async with sem:
                try:
                    ok = await asyncio.wait_for(
                        self._probe_func(path),
                        timeout=self.cfg["probe_timeout_seconds"]
                    )
                    self._on_probe_result(path, ok, error="" if ok else "probe returned False")
                except asyncio.TimeoutError:
                    self._on_probe_result(path, False, error="probe timeout")
                except Exception as e:
                    self._on_probe_result(path, False, error=str(e)[:200])
        await asyncio.gather(*[_one(p) for p in to_probe])

    def _on_probe_result(self, path: str, success: bool, error: str = ""):
        """probe 回调: 成功 → HEALTHY, 失败 → 重新 SKIP (退避)"""
        with self._lock:
            mh = self._health.get(path)
            if not mh:
                return
            mh.last_probe_at = time.time()
            mh.last_probe_success = success
            mh.last_probe_error = error
            if success:
                # 恢复 → HEALTHY (重置)
                mh.state = HealthState.HEALTHY.value
                mh.consecutive_fails = 0
                mh.consecutive_success = 0
                mh.skip_until = 0
                mh.cooldown_seconds = self.cfg["skip_initial_seconds"]
                LOG.info("model_health: %s HALF_OPEN/SKIP → HEALTHY (probe success)", path)
            else:
                # 失败 → 重新 SKIP (指数退避)
                mh.skip_count += 1
                mh.cooldown_seconds = min(
                    int(mh.cooldown_seconds * self.cfg["skip_backoff_factor"]),
                    self.cfg["skip_max_seconds"],
                )
                mh.skip_until = time.time() + mh.cooldown_seconds
                mh.state = HealthState.SKIP.value
                LOG.warning("model_health: %s HALF_OPEN → SKIP (probe failed, cooldown=%ds, err=%s)",
                            path, mh.cooldown_seconds, error[:80])
            mh.updated_at = time.time()
            # v3.16.0: probe 失败转 SKIP 时记录 first_skip_at
            if mh.state == HealthState.SKIP.value and mh.first_skip_at == 0.0:
                mh.first_skip_at = time.time()
        self._save_async()

    # ---- 查询接口 (admin API) ----

    def get_all_health(self) -> dict:
        """返回所有 model 健康度 (admin API 用)"""
        now = time.time()
        with self._lock:
            result = {}
            for path, mh in self._health.items():
                skip_remaining = 0
                if mh.state == HealthState.SKIP.value:
                    skip_remaining = max(0, mh.skip_until - now)
                result[path] = {
                    "state": mh.state,
                    "consecutive_fails": mh.consecutive_fails,
                    "consecutive_success": mh.consecutive_success,
                    "total_calls": mh.total_calls,
                    "total_success": mh.total_success,
                    "total_fail": mh.total_fail,
                    "rolling_success_rate": round(mh.rolling_success_rate(), 2),
                    "ewma_latency_ms": round(mh.ewma_latency_ms, 1),
                    "last_success_at": mh.last_success_at,
                    "last_fail_at": mh.last_fail_at,
                    "skip_until": mh.skip_until,
                    "skip_remaining_seconds": round(skip_remaining, 1),
                    "skip_count": mh.skip_count,
                    "cooldown_seconds": mh.cooldown_seconds,
                    "last_probe_at": mh.last_probe_at,
                    "last_probe_success": mh.last_probe_success,
                    "last_probe_error": mh.last_probe_error,
                    "updated_at": mh.updated_at,
                }
        return result

    def get_summary(self) -> dict:
        """汇总统计"""
        with self._lock:
            states = {"healthy": 0, "degraded": 0, "skip": 0, "half_open": 0}
            for mh in self._health.values():
                states[mh.state] = states.get(mh.state, 0) + 1
        return {
            "total_models": len(self._health),
            "by_state": states,
        }

    def force_probe(self, path: str) -> Optional[dict]:
        """admin API: 强制 probe 某个 model (同步等待, 但 probe_func 仍是 async)"""
        probe_fn = self._probe_func
        if probe_fn is None:
            return {"error": "probe_func not set"}
        with self._lock:
            if path not in self._health:
                return {"error": f"path '{path}' not in health records"}
        try:
            if self._loop is None:
                return {"error": "no event loop, cannot probe synchronously"}
            future = asyncio.run_coroutine_threadsafe(
                probe_fn(path), self._loop
            )
            ok = future.result(timeout=self.cfg["probe_timeout_seconds"] + 2)
            self._on_probe_result(path, ok, error="" if ok else "probe returned False")
            return {"path": path, "probe_success": ok}
        except Exception as e:
            return {"error": str(e)[:200]}

    def decay_model_penalty(self) -> None:
        """v3.21.0: 每次 loop_engine tick 调用, 对 degrade/cooldown 状态做衰减

        衰减规则 (SOUL.md §"道家阴阳 8 圈 守则 10 上下文衰减"):
          - SKIP 状态: skip_remaining 每 tick 自然减少 (已存在 tick 逻辑)
          - degrade 权重: 每 tick 减 5%, 直到 0 (新)
          - cooldown_seconds: 每 tick 减 1%, 直到初始值

        LoopEngine 每 5min tick 调用一次, 不需要在 probe hot-path 执行.
        """
        update = False
        now = time.time()
        with self._lock:
            for path, mh in self._health.items():
                changed = False
                # degrade 权重衰减 (5% / tick)
                if hasattr(mh, 'degrade_weight'):
                    if mh.degrade_weight > 0.01:
                        mh.degrade_weight *= 0.95
                        if mh.degrade_weight < 0.01:
                            mh.degrade_weight = 0.0
                        changed = True
                # cooldown 衰减 (1% / tick), cooldown_seconds 是 int, 结果 cast 回 int
                initial = self.cfg.get('skip_initial_seconds', 30)
                if mh.cooldown_seconds > initial:
                    new_val = int(mh.cooldown_seconds * 0.99)
                    mh.cooldown_seconds = max(initial, new_val)
                    changed = True
                if changed:
                    mh.updated_at = now
                    update = True
        if update:
            self._save_async()
            LOG.debug("model_health: decay_model_penalty applied")


# ---- 模块级便捷接口 (必须在 class 外面, 否则 class 提前结束, method 都变 module-level!) ----

def get_model_health_manager() -> "ModelHealthManager":
    """获取全局 ModelHealthManager 实例 (必须在 init_model_health_manager 之后调用)"""
    return ModelHealthManager.get_instance()


def init_model_health_manager(state_dir, config: Optional[dict] = None) -> "ModelHealthManager":
    """初始化全局 ModelHealthManager 单例 (幂等)"""
    return ModelHealthManager.init_manager(state_dir=Path(state_dir), config=config)
