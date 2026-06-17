"""
supermodel_router/model_rules.py — 模型管理规则引擎 v3.3

统一管理:
- 自动发现 + 变更通知
- 白名单/黑名单 (正则 + 手动)
- 自动加黑/加白规则
- 持久化规则 + discovery 历史
"""
import json
import logging
import os
import re
import time
import threading
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

LOG = logging.getLogger("model_rules")

RULES_FILE = "model_rules_state.json"


# ── 数据类 ────────────────────────────────────────────────

@dataclass
class ModelRule:
    """单条模型规则"""
    id: str                              # 唯一 ID (auto: rule_<hash>)
    rule_type: str                       # "blacklist" | "whitelist" | "auto_black" | "auto_white"
    pattern: str                         # 正则表达式
    description: str = ""                # 人类可读说明
    enabled: bool = True
    created_at: float = 0.0
    hit_count: int = 0                   # 触发次数
    last_hit: float = 0.0               # 上次触发时间

    def matches(self, model_id: str, provider: str = "") -> bool:
        """检查模型 ID 是否匹配此规则"""
        if not self.enabled:
            return False
        # 在 provider/model 全路径上匹配
        full = f"{provider}/{model_id}" if provider else model_id
        try:
            return bool(re.search(self.pattern, full, re.IGNORECASE))
        except re.error:
            # 正则语法错误 → 降级为字面匹配
            return model_id.lower() in self.pattern.lower()


@dataclass
class DiscoveryRecord:
    """单次发现记录"""
    timestamp: float
    provider: str
    model_count: int                     # 当前模型数
    new_models: list[str] = field(default_factory=list)
    removed_models: list[str] = field(default_factory=list)
    blacklisted: list[str] = field(default_factory=list)
    whitelisted: list[str] = field(default_factory=list)
    notifications_sent: int = 0


@dataclass
class ModelDiff:
    """两次发现之间的差异"""
    provider: str
    old_models: list[str]
    new_models: list[str]
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    timestamp: float = 0.0


# ── 规则引擎 ──────────────────────────────────────────────

class ModelRuleEngine:
    """
    模型管理规则引擎

    职责:
    1. 管理 blacklist / whitelist / auto_black / auto_white 规则
    2. 在每次 discovery 时对模型列表执行规则匹配
    3. 触发通知 (webhook)
    4. 记录 discovery 历史
    5. 持久化规则到 JSON
    """

    def __init__(self, rules_dir: str = "."):
        self._lock = threading.RLock()
        self._rules_dir = rules_dir
        self._rules: list[ModelRule] = []
        self._history: list[DiscoveryRecord] = []
        self._diffs: list[ModelDiff] = []
        self._notify_log: list[dict] = []

        # 通知回调 (由 app.py 注册)
        self._notify_callback: Optional[Callable] = None

        # 统计
        self._last_discovery: float = 0
        self._total_discoveries: int = 0

        # 加载持久化规则
        self._load_state()

    # ── 规则 CRUD ──────────────────────────────────────────

    def add_rule(self, rule_type: str, pattern: str,
                 description: str = "", enabled: bool = True) -> ModelRule:
        """添加规则"""
        with self._lock:
            rule_id = f"rule_{rule_type}_{abs(hash(pattern)) % 100000:05d}"
            # 去重: 同 type + 同 pattern 不重复加
            for r in self._rules:
                if r.rule_type == rule_type and r.pattern == pattern:
                    return r

            rule = ModelRule(
                id=rule_id,
                rule_type=rule_type,
                pattern=pattern,
                description=description,
                enabled=enabled,
                created_at=time.time(),
            )
            self._rules.append(rule)
            self._save_state()
            LOG.info("Rule added: %s [%s] %s", rule_id, rule_type, pattern)
            return rule

    def remove_rule(self, rule_id: str) -> bool:
        """删除规则"""
        with self._lock:
            for i, r in enumerate(self._rules):
                if r.id == rule_id:
                    self._rules.pop(i)
                    self._save_state()
                    LOG.info("Rule removed: %s", rule_id)
                    return True
            return False

    def update_rule(self, rule_id: str, **kwargs) -> Optional[ModelRule]:
        """更新规则字段"""
        with self._lock:
            for r in self._rules:
                if r.id == rule_id:
                    for k, v in kwargs.items():
                        if hasattr(r, k) and k not in ("id", "created_at", "hit_count"):
                            setattr(r, k, v)
                    self._save_state()
                    return r
            return None

    def get_rules(self, rule_type: str | None = None) -> list[dict]:
        """获取规则列表"""
        with self._lock:
            rules = self._rules
            if rule_type:
                rules = [r for r in rules if r.rule_type == rule_type]
            return [asdict(r) for r in rules]

    def get_rule(self, rule_id: str) -> Optional[dict]:
        """获取单条规则"""
        with self._lock:
            for r in self._rules:
                if r.id == rule_id:
                    return asdict(r)
            return None

    # ── 规则执行 ──────────────────────────────────────────

    def evaluate_models(self, models: list[dict], provider: str = "") -> dict:
        """
        对模型列表执行所有规则, 返回分类结果

        Returns:
            {
                "allowed": [model_ids],     # 通过 (whitelist + 无 black 命中)
                "blocked": [model_ids],     # 被 blacklist 拦截
                "auto_added": [model_ids],  # auto_white 命中自动加入
                "auto_blocked": [model_ids], # auto_black 命中自动拉黑
                "unchanged": [model_ids],   # 无规则命中, 保持原样
                "stats": {
                    "total": N,
                    "whitelist_hits": N,
                    "blacklist_hits": N,
                    "auto_white_hits": N,
                    "auto_black_hits": N,
                }
            }
        """
        result = {
            "allowed": [],
            "blocked": [],
            "auto_added": [],
            "auto_blocked": [],
            "unchanged": [],
            "stats": {},
        }

        with self._lock:
            whitelist_rules = [r for r in self._rules if r.rule_type == "whitelist" and r.enabled]
            blacklist_rules = [r for r in self._rules if r.rule_type == "blacklist" and r.enabled]
            auto_white_rules = [r for r in self._rules if r.rule_type == "auto_white" and r.enabled]
            auto_black_rules = [r for r in self._rules if r.rule_type == "auto_black" and r.enabled]

            for m in models:
                mid = m.get("id", "") if isinstance(m, dict) else getattr(m, "id", "")
                matched = False

                # 1. blacklist 硬拦截 (最高优先级)
                for rule in blacklist_rules:
                    if rule.matches(mid, provider):
                        rule.hit_count += 1
                        rule.last_hit = time.time()
                        result["blocked"].append(mid)
                        matched = True
                        break

                if matched:
                    continue

                # 2. whitelist 显式通过
                for rule in whitelist_rules:
                    if rule.matches(mid, provider):
                        rule.hit_count += 1
                        rule.last_hit = time.time()
                        result["allowed"].append(mid)
                        matched = True
                        break

                if matched:
                    continue

                # 3. auto_black 自动拉黑
                for rule in auto_black_rules:
                    if rule.matches(mid, provider):
                        rule.hit_count += 1
                        rule.last_hit = time.time()
                        result["auto_blocked"].append(mid)
                        matched = True
                        break

                if matched:
                    continue

                # 4. auto_white 自动加白
                for rule in auto_white_rules:
                    if rule.matches(mid, provider):
                        rule.hit_count += 1
                        rule.last_hit = time.time()
                        result["auto_added"].append(mid)
                        matched = True
                        break

                if not matched:
                    result["unchanged"].append(mid)

            result["stats"] = {
                "total": len(models),
                "whitelist_hits": len(result["allowed"]),
                "blacklist_hits": len(result["blocked"]),
                "auto_white_hits": len(result["auto_added"]),
                "auto_black_hits": len(result["auto_blocked"]),
                "unchanged": len(result["unchanged"]),
            }

            self._save_state()
            return result

    def apply_to_model_list(self, models: list[dict], provider: str = "") -> list[dict]:
        """
        根据规则过滤模型列表
        blocked + auto_blocked → 移除
        allowed + auto_added → 保留
        unchanged → 保留 (无规则命中)
        """
        result = self.evaluate_models(models, provider)
        blocked_set = set(result["blocked"] + result["auto_blocked"])
        return [m for m in models
                if (m.get("id", "") if isinstance(m, dict) else getattr(m, "id", ""))
                not in blocked_set]

    # ── 变更追踪 + 通知 ──────────────────────────────────

    def record_discovery(self, provider: str, old_ids: list[str], new_ids: list[str],
                          all_models: list[dict] | None = None) -> DiscoveryRecord:
        """
        记录一次发现, 计算 diff, 执行规则, 触发通知

        Returns:
            DiscoveryRecord 含 diff + 规则命中 + 通知状态
        """
        old_set = set(old_ids)
        new_set = set(new_ids)
        added = list(new_set - old_set)
        removed = list(old_set - new_set)

        record = DiscoveryRecord(
            timestamp=time.time(),
            provider=provider,
            model_count=len(new_ids),
            new_models=added,
            removed_models=removed,
        )

        # 执行规则 (如果有模型列表)
        eval_result: dict = {"blocked": [], "auto_blocked": [], "auto_added": []}
        if all_models:
            eval_result = self.evaluate_models(all_models, provider)
            record.blacklisted = eval_result["blocked"] + eval_result["auto_blocked"]
            record.whitelisted = eval_result["auto_added"]

        # 只在有变更时通知
        if added or removed:
            self._diffs.append(ModelDiff(
                provider=provider,
                old_models=old_ids,
                new_models=new_ids,
                added=added,
                removed=removed,
                timestamp=time.time(),
            ))

            # 触发通知
            if self._notify_callback:
                try:
                    self._notify_callback(provider, added, removed, eval_result)
                    record.notifications_sent = 1
                except Exception as e:
                    LOG.error("Notification failed: %s", e)

            LOG.info("Discovery [%s]: +%d -%d models",
                     provider, len(added), len(removed))

        with self._lock:
            self._history.append(record)
            # 保留最近 500 条历史
            if len(self._history) > 500:
                self._history = self._history[-500:]
            self._total_discoveries += 1
            self._last_discovery = time.time()
            self._save_state()

        return record

    def set_notify_callback(self, callback: Callable):
        """注册通知回调 (webhook/feishu)"""
        self._notify_callback = callback

    # ── 历史查询 ──────────────────────────────────────────

    def get_history(self, provider: str | None = None, limit: int = 50) -> list[dict]:
        """获取发现历史"""
        with self._lock:
            history = self._history
            if provider:
                history = [h for h in history if h.provider == provider]
            return [asdict(h) for h in history[-limit:]]

    def get_diffs(self, provider: str | None = None, limit: int = 20) -> list[dict]:
        """获取最近变更"""
        with self._lock:
            diffs = self._diffs
            if provider:
                diffs = [d for d in diffs if d.provider == provider]
            return [asdict(d) for d in diffs[-limit:]]

    def get_notify_log(self, limit: int = 50) -> list[dict]:
        """获取通知日志"""
        with self._lock:
            return self._notify_log[-limit:]

    def get_stats(self) -> dict:
        """获取管理统计"""
        with self._lock:
            return {
                "total_rules": len(self._rules),
                "active_rules": len([r for r in self._rules if r.enabled]),
                "blacklist_rules": len([r for r in self._rules if r.rule_type == "blacklist"]),
                "whitelist_rules": len([r for r in self._rules if r.rule_type == "whitelist"]),
                "auto_black_rules": len([r for r in self._rules if r.rule_type == "auto_black"]),
                "auto_white_rules": len([r for r in self._rules if r.rule_type == "auto_white"]),
                "total_discoveries": self._total_discoveries,
                "last_discovery": self._last_discovery,
                "history_count": len(self._history),
                "diff_count": len(self._diffs),
            }

    # ── 持久化 ──────────────────────────────────────────

    def _save_state(self):
        """保存规则 + 历史到 JSON"""
        try:
            state = {
                "rules": [asdict(r) for r in self._rules],
                "history": [asdict(h) for h in self._history[-100:]],
                "notify_log": self._notify_log[-100:],
                "stats": {
                    "total_discoveries": self._total_discoveries,
                    "last_discovery": self._last_discovery,
                },
            }
            path = os.path.join(self._rules_dir, RULES_FILE)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception as e:
            LOG.error("Failed to save rules state: %s", e)

    def _load_state(self):
        """从 JSON 加载规则 + 历史"""
        try:
            path = os.path.join(self._rules_dir, RULES_FILE)
            if not os.path.exists(path):
                return
            with open(path) as f:
                state = json.load(f)
            self._rules = [ModelRule(**r) for r in state.get("rules", [])]
            self._history = [DiscoveryRecord(**h) for h in state.get("history", [])]
            self._notify_log = state.get("notify_log", [])
            stats = state.get("stats", {})
            self._total_discoveries = stats.get("total_discoveries", 0)
            self._last_discovery = stats.get("last_discovery", 0)
            LOG.info("Loaded %d rules, %d history records",
                     len(self._rules), len(self._history))
        except Exception as e:
            LOG.warning("Failed to load rules state: %s", e)
