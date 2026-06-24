"""
supermodel_router/version.py — 版本元数据 + GitHub release 检查

v3.18.0 (2026.06.25 老大拍 "smr quota recover/status 走 Admin UI 不写 CLI"):
- 配额耗尽检测 + 长 SKIP (老大拍 UI 路线, 不写 smr CLI)
- 新增 ModelHealth 字段:
  * quota_skip_until: float (配额耗尽导致的长 SKIP 到期时间戳)
  * quota_type: str (daily/weekly/monthly/token_plan/balance, 空字符串 = 非配额 skip)
- engine.py classify_error (HTTP code → retry/disable/rate_limit 映射):
  * 新增 quota_exhausted: bool 字段 (默认 False)
  * 新增 quota_type: str 字段 (默认空, 命中配额关键词时填充)
  * 429 命中配额关键词 (daily/monthly/weekly/billing/充值/insufficient/balance/plan/套餐) → 配额耗尽
  * 配额类型映射 (retry_after): daily=1h / weekly=7d / monthly=30d / token_plan=24h / balance=24h
- model_health.py record_failure 新增 quota_exhausted / quota_type 参数:
  * 配额耗尽时设 quota_skip_until = now + quota_duration (不受普通 cooldown 退避影响)
  * 同时设 skip_until = quota_skip_until, 确保路由跳过
  * 标记 state=SKIP, last_probe_error="quota_exhausted(<type>)"
- admin_api.py 3 新端点:
  * GET  /v1/admin/quota/status            → 所有 model 配额状态 (path/quota_type/skip_until/remaining)
  * POST /v1/admin/quota/recover/{path:path} → 手动清单个 model 的配额 skip (续费后场景)
  * 增强 POST /v1/admin/provider-health/re-enable/{name} 加 clear_quota 参数 (默认 true)
- 设计: 配额耗尽 = 长 SKIP (避免每次 cooldown 到期后 429 反复重试), admin UI 续费后一键清
- 配套 admin_ui.py: 新增 🪫 配额状态卡片 + 续费后一键清按钮

v3.17.0 (2026.06.24 老大拍 "API 调用统一的模型名, SMR 内部路由决定实际模型"):
- 新增 model alias 机制 (统一路由名称, client 不关心实际 provider/model)
- config.py:
  * DEFAULT_MODEL_ALIASES (默认 6 个 alias):
    - auto: 走 modality 自动路由
    - model-router / router / best: strategy=best_quality (quality + capability 综合分)
    - cheap: strategy=free_only (只选免费模型, openrouter :free 后缀 + pricing=0)
    - fast: strategy=lowest_latency (按 EWMA 延迟升序)
  * get_model_aliases() — 合并默认值 + 用户覆盖 (alias_of chain)
  * set_model_alias(name, cfg) — admin UI 增删改
- engine.py:
  * _collect_candidate_models 加 alias 解析 (优先级最高)
  * _resolve_alias(name) — alias 名 → routing 配置
  * _apply_alias_strategy(cfg, modalities) — 按 strategy 过滤 + 排序 model
    策略: best_quality / free_only / lowest_latency / modality_auto / random
  * _is_provider_enabled / _filter_free_models 辅助方法
  * 自动排除 disabled provider (跟 v3.16.0 provider 健康度检测集成)
  * 自动排除 alias_cfg.exclude_providers (默认 model-router 排除 openrouter 因为 89% fail)
- admin_api.py 3 新端点:
  * GET    /v1/admin/model-aliases                 → 列出所有 alias
  * PUT    /v1/admin/model-aliases/{name}          → 设置/修改 alias
  * DELETE /v1/admin/model-aliases/{name}          → 删除 alias (恢复默认)
- 设计: alias 机制让 client 用统一名 (e.g. model-router) → SMR 内部按 routing 策略选最优

v3.16.0 (2026.06.24 老大拍 "provider 全 SKIP 持续 1 周 → 自动禁用 + 加原因"):
- 新增 ModelHealth 字段 `first_skip_at` (首次进入 SKIP 时间戳, provider 级禁用判定用)
- record_failure / _on_probe_result: 进入 SKIP 时记录 first_skip_at (续期不变)
- record_success: 离开 SKIP 时清 first_skip_at (重新计时)
- 新增 DEFAULT_CONFIG 字段:
  * provider_disable_threshold_seconds = 604800 (7 天)
  * provider_check_min_models = 3 (避免 1 model provider 误判)
  * provider_check_interval_seconds = 600 (后台每 10min 扫 1 次)
  * provider_check_enabled = True (全局开关)
- 新方法 ModelHealthManager.check_provider_disable_candidates(registry)
  条件: provider enabled + ≥ N model + 全部 SKIP + 最早 first_skip_at 到 now ≥ threshold
- 新方法 ModelHealthManager.get_provider_health_summary(registry) (admin UI 用)
- 新方法 ModelHealthManager.set_provider_disable_callback(func) (app.py 注入)
- 新 _background_loop 段: 每 provider_check_interval_seconds 跑 _scan_and_disable_providers
- config.py 新方法:
  * disable_provider(name, reason) — 设 enabled=False + disabled_at + disabled_reason (持久化)
  * enable_provider(name) — 清 disabled metadata
  * get_provider_disabled_meta(name) — admin UI 用
- admin_api.py 3 新端点:
  * GET  /v1/admin/provider-health              → 所有 provider 健康度汇总
  * POST /v1/admin/provider-health/re-enable/{name} → 手动 re-enable + 清该 provider 所有 model health
  * POST /v1/admin/provider-health/check-now   → 强制扫描 + 立即禁用
- app.py: 注入 _provider_disable_scan callback (每 10min 跑 1 次, 调 config.disable_provider)
- 设计: model_health 不直接 import config (避免循环依赖), 通过 callback 模式解耦

v3.15.0 (2026.06.24 老大拍 "添加模型健康度指标 + 跳过非健康 + 降低路由延迟"):
- 新模块 model_health.py — ModelHealthManager (全局单例, 状态机 + 滚动窗口 + EWMA)
- 健康度指标 5 个: consecutive_fails / rolling_success_rate (窗口 100) / ewma_latency_ms / last_success_at / last_fail_at
- 状态机: HEALTHY / DEGRADED / SKIP / HALF_OPEN (circuit breaker pattern)
- 跳过策略 (路由时):
  * consecutive_fails >= 3 → SKIP 60s (初始 cooldown)
  * rolling_success_rate < 30% (sample ≥ 10) → SKIP 300s
  * ewma_latency_ms > 60000 → SKIP 60s
- 健康度恢复检测 (老大 6/24 钦定):
  * SKIP 到期 → HALF_OPEN (不等真实流量)
  * background checker 每 30s probe (HEAD base_url/v1/models, 不消耗 token 配额)
  * probe 成功 → HEALTHY (重置 consecutive_fails, cooldown 还原 60s)
  * probe 失败 → 重新 SKIP 指数退避 (60s → 120s → 240s → 300s cap)
- engine 集成 (engine.py):
  * pick_chain() 收集候选后调 _filter_by_health() 跳过 SKIP + 给 DEGRADED 加 penalty
  * record_success/failure 联动 model_health.record_*(path, latency_ms, error)
- admin API 4 端点 (admin_api.py):
  * GET  /v1/admin/model-health                  → 所有 model 健康度 + summary
  * POST /v1/admin/model-health/probe/{path}     → 强制 probe 单个 model
  * POST /v1/admin/model-health/probe-all        → 触发批量 probe
  * POST /v1/admin/model-health/reset/{path}     → 重置单 model (admin 主动恢复)
- admin UI (admin_ui.py):
  * 模型列表加 "🏥 健康度" 列 (4 色 badge: 绿/黄/红/蓝 + tooltip 显示指标)
  * toolbar 上方 summary bar (4 chip: 健康/降级/跳过/探测中 + 立即探测按钮)
- 持久化: state/model_health.json (类似 penalty_state.json)
- 设计参考: Hystrix / Resilience4j circuit breaker half-open state
- 降延迟效果: 路由前 SKIP 跳过 + DEGRADED 降权, 避免 89% fail 那种 model 反复进入候选链

v3.14.0 (2026.06.24 老大拍 自主 review admin UI 优化):
- 模型列表 UI/UX 全面改版: 多维筛选 + 实时搜索 + 4 字段排序
  * 🔍 搜索框: 模糊匹配 model id / provider (实时, 无延迟)
  * 🏷️ Provider 多选 chip: 动态生成 (从 data 提取, 实时数量)
  * 📂 分类 chip 保留: text-only/multimodal/image-gen/video-gen/audio-gen/embedding
  * ⇅ 排序: 能力分/上下文/模型名/价格 (4 字段 × 升/降序 = 8 种排序)
  * ↺ 一键重置: 清空所有筛选 + 排序恢复默认
- 头部计数智能化: "共 10 个" / "匹配 3 / 10 · 第 1/2 页"
- 改进空状态: emoji + 标题 + 描述 + "清空筛选" 按钮 (无匹配时)
- 重构去重: 抽 getFilteredSortedModels() + getSortValue() 公共函数
  (修复 v3.6.0 changePageSize 重复渲染表格 HTML bug)
- PAGE_SIZE 改 const → let 支持每页大小切换
- renderModels 支持无参调用 (用 lastModelsData)
- Provider filter chip 风格统一 (跟 wizard 一致)

v3.13.0 (2026.06.22 老大拍 SMR v3.13.0 = R55 12 累计 完整理完完善善):
- R55 12 累计 BUG 修 (SMR build 12 次踩 5 大坑)
  1. `| tail`/`| tee` 掩盖 build exit → 用 `bash -c 'docker build ... 2>&1; echo "BUILD_EXIT=$?" >> log'`
  2. background=true 跑 echo 字符串假完成 → 真 echo -c 看 build output
  3. 报告"完成"不看 log 完整 → 5b 主动 ls 必看 `tail -30 /tmp/log` + `BUILD_EXIT=`
  4. 擅自动 docker-compose → 老大批准前不擅动 (R10 边界)
  5. down 之前不验真 image tag → SMR down 之前必 `docker images | grep supermodel`

v3.11.0 (2026.06.21 v0.9+ v1.0 易经算法集成):
- 3 蒸馏精华赋能 SMR 核心路由: 体 (后天八卦 8 卦 dashboard) + 用 (先天八卦 1-9 数) + 时 (12 时辰火候)
- 5 provider 卦位映射 (config.yaml v0_9_integration.provider_trigram):
  - minimax-cn=离/南/8/火/午时履 / newapi=乾/西北/9/金/戌时大有
  - freemodel=震/东/3/木/子时屯 / openrouter=兑/西/4/金/酉时同人
  - local=坎/北/7/水/亥时未济 / 中央 5=元任务心跳
- /admin/9-gong 路由: 派活 dashboard 8 卦布局 (戴九履一) + 12 时辰火候 + 五行精确化
- 9 还 7 返: 代码 refactor (3.4→3.10 = 9 版回还) + 核心 BUG 修 (c52f3e0 = 7 返, 5b+venv+PAT+gh-cred+GH-httpx)
- 3 cron 自动化: by-five-element / by-san-yi / by-fire-候 (每周一)
- docker-compose.yml 加 admin_ui.py + dashboard HTML 体积挂载
- TROUBLESHOOTING.md: 7 风险排错 SOP (全推到容器内/5b/venv/体积/健康/模型/PAT)
- SKILL.md: §炼己/§大象/§逆运算 3 必含章节

v3.10.0 (2026.06.19 老大拍 3 项全满足, 一气呵成):
- 4 轮询策略并存 (model-level + group-level 双层):
  - model-level: routing.strategy (老 v3.9.0 字段, 实际只 1 个实现)
      - flat (默认, 全局降序; 老 v4 行为)
    * group-level: routing.group_strategy (v3.10.0 新字段, 默认 round-robin-group)
      - round-robin-group: 默认, group 内轮询, group 间 round-robin
      - flat: 全局降序
      - group-failover: 按 group 优先级
      - group-weighted: 按 group_weights 加权随机
  - 优先级: model-level 先选候选 -> group-level 决定 group 顺序
  - config.yaml 默认 round-robin-group + strategy: flat, UI /v1/admin/routing PUT 可改
  - wizard UI 一键生成 group 时可选 4 策略 (UI 覆盖 config 默认)
  - v3.10.1 修 BUG-001: 删 quality_weighted/balanced (未实现, 静默 fallback)

v3.10.0 新增 (2026.06.18 老大拍 A 一气呵成 + 数据持久化):
- 模型分组向导器: 13 preset + 5 维自定义筛选 + 批量勾选 + 策略 dropdown
- 端点 4 个:
  * GET  /v1/admin/models/filter       (provider/context/quality/speed/modality/tags)
  * POST /v1/admin/model-groups/from-filter   (filter 自动建 group)
  * GET  /v1/admin/model-groups/wizard/presets      (13 preset 列表 + 实时匹配数)
  * POST /v1/admin/model-groups/from-wizard   (preset 到 filter 到建 group + key)
- ModelInfo 扩字段: quality_score / speed_score / reasoning_score / tags / metadata_source
- model_metadata.json: 持久化元数据 (EWMA 自动算 + auto_tags + R40 backup)
- _sync_mgm() 修复: registry callback 注册后立即手动调一次 (startup refresh 已完成 sync 永远不触发)
- wizard UI: 13 preset 卡片 + 5 维筛选 + 实时匹配数 + 模型批量勾选 + 策略 dropdown + 一键生成
- 数据持久化 (老大拍, docker 升级保留数据):
  * docker-entrypoint.sh: 首次启动 seed model_metadata + 老 state 自动迁移
  * secrets/ 卷挂载 entrypoint 从 /run/secrets/* 渲染真 key 到 config.yaml 占位符
  * .dockerignore: 锁 state/secrets/backups 不进 image
  * UPGRADE.md: 同端口/蓝绿升级 SOP + 回滚 + 老 state 自动迁移

v3.8.0 新增 (2026.06.18 老大拍):
- 模型上下文窗口加分: 7 档细分 (4K/8K/16K/32K/64K/128K/200K+), 加分可配置
  (config.classifier.context_window_bonus)
- 上下文压缩 (切链时): 按目标 model context_window 限制, 3 策略 (pass-through / 段落分批 / 历史压缩)
  1. Pass-through: total <= target * overhead (默认 0.8) 原样
  2. 段落分批 (paragraph_chunk): 超长 user message 拆 N 段, 每段 <= chunk_tokens
  3. 历史压缩 (history_trim): 旧 messages 摘要, 保留 system + 最近 K 轮
- ModelInfo + CandidateResult + RouteResult 新增 context_window 顶层字段
- _extract_context_window helper (跟 provider 解耦, 优先顶层到 openrouter nested 到 0)
- /v1/admin/context_bridge 新 stats: compressions_total + tokens_saved_total
- openai_routes.py 切链时调 compress_for_target, 含 _smr_compress metadata (debug 用)
- 总开关: context_bridge.compress_on_switch (false = 不压缩)

v3.7.x 修 (2026.06.18 老大拍):
- v3.7.1: /v1/public/chat/completions 端点 + UI 版本号修正 (commit 8cff72c)
- v3.7.2: 修 2 P0 (中间件 token 累计 / model_filter 通配) + 1 P2 (state 路径) + secret leak
  防御 (commit 1c68ab2)
  注意: 1c68ab2 commit message 提 v3.7.2 但没改 version.py, UI 一直显示 v3.7.1
  -> v3.8.0 同步 bump version.py

v3.6.0 新增 (2026.06.17 23:56 老大拍):
- UI/UX 全面改版: 顶部 toolbar 左侧 sidebar nav (Dashboard/Providers/Models/Keys/Stats/Config/Classifier/Version)
- 模型列表分页 (每页 20 + 前/后/跳转)
- 真实使用量统计卡片: 总请求 / 成功率 / 平均延迟 / 今日 token
- import_time / export_time / import_keys 单独 export+import
- API key 独立管理页 (/v1/admin/api-keys)
- 持久化复盘文档 (PERSISTENCE.md) + 启动时自动迁移

v3.5.0 新增 (2026.06.17 22:25 老大拍):
- 主动盘点: POST /v1/admin/context_review 拿 smr_request_id 聚合报告
  (用户说"盘点上下文/重新审视/回顾上下文"时, mainbot 调该 endpoint)
- 切链 race condition 防御: stream 模式切链时显式 aclose() 上游 httpx
- smr_request_id 嵌入: response._router.smr_request_id + chain_id
  (mainbot 收 response 时校验错配 丢弃)
- per-request 跟踪: ContextBridge 维护 smr_request_id 到 SwitchRecord[] 映射
  (bounded LRU 1000, 不持久化)

v3.4.0 新增 (2026.06.17 22:00 老大拍):
- 上下文桥接 (ContextBridge): 切换模型时, 注入 system message 同步上下文
- 过期标记: 切到新 candidate 的时间距请求开始 > 30min 标 stale=true
- 流式 SSE sentinel: data: {"_smr_bridge": {...}} 标记切换 + stale
- 非流式: response._router.switched_from + stale + age_seconds
- /v1/admin/context_bridge endpoints (config/stats/reset)

v3.1 新增: 老大 09:48 拍 C 项
- 当前版本号 (SMR_VERSION)
- 启动时打印版本 + 构建日期
- 后台定期检查 GitHub release, 有新版本时通知
- /v1/admin/version endpoint 暴露版本信息
- /v1/admin/upgrade endpoint 触发升级 (拉新 binary + 重启)

v3.2 新增: 老大 14:40 拍 C 项
- 配置版本管理: 自动备份 .backups/config-*.yaml (保留 50 个)
- /v1/admin/config/backups + /v1/admin/config/restore
- penalty state 持久化 (penalty_state.json) — SMR 重启不丢
"""
import json
import logging
import os
import time
from typing import Optional

LOG = logging.getLogger("version")

# 当前版本 (跟随 release tag)
VERSION = "3.18.0"
BUILD_DATE = "2026-06-25"

GITHUB_REPO = "IGhostHuang/supermodel_router"  # 默认值, 可被 config.version_check.repo 覆盖
RELEASE_CHECK_INTERVAL = 3600  # 1 小时检查一次

_cached_release: Optional[dict] = None
_last_check_time: float = 0.0


def load_version_meta() -> dict:
    """返回当前版本元数据 (lifespan 用)"""
    return {
        "version": VERSION,
        "build_date": BUILD_DATE,
        "title": f"SuperModel Router v{VERSION}",
    }


def get_cached_release(github_token: Optional[str] = None,
                       repo: Optional[str] = None) -> Optional[dict]:
    """获取缓存的 release 信息 (1h 内复用, 不重复打 GitHub API)"""
    global _cached_release, _last_check_time
    now = time.time()
    if _cached_release and (now - _last_check_time) < RELEASE_CHECK_INTERVAL:
        return _cached_release
    return fetch_latest_release(github_token=github_token, repo=repo)


def fetch_latest_release(github_token: Optional[str] = None,
                         repo: Optional[str] = None) -> Optional[dict]:
    """从 GitHub API 拉最新 release"""
    global _cached_release, _last_check_time
    target_repo = repo or GITHUB_REPO
    url = f"https://api.github.com/repos/{target_repo}/releases/latest"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "SMR-VersionChecker"}
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    try:
        import httpx
        resp = httpx.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            _cached_release = {
                "tag": data.get("tag_name", "unknown"),
                "name": data.get("name", ""),
                "published_at": data.get("published_at", ""),
                "url": data.get("html_url", ""),
                "tarball": data.get("tarball_url", ""),
                "zipball": data.get("zipball_url", ""),
                "body": data.get("body", "")[:500],  # 限制大小
                "prerelease": data.get("prerelease", False),
            }
            _last_check_time = time.time()
            LOG.info("GitHub release fetched: %s (%s)",
                     _cached_release["tag"], _cached_release["published_at"])
            return _cached_release
        elif resp.status_code == 404:
            LOG.warning("GitHub repo %s not found or no release", target_repo)
            _cached_release = None
            _last_check_time = time.time()
            return None
        elif resp.status_code == 403:
            # rate limit
            LOG.warning("GitHub API rate limited (403), keeping cached")
            return _cached_release
        else:
            LOG.warning("GitHub release fetch returned %d: %s",
                        resp.status_code, resp.text[:200])
            return _cached_release
    except Exception as e:
        LOG.warning("GitHub release fetch failed: %s", e)
        return _cached_release


def is_newer_version(current: str, latest: str) -> bool:
    """简单 semver 比较 (major.minor.patch)
    current="3.1.0", latest="3.2.0" -> True
    current="3.1.0", latest="3.1.0" -> False
    current="3.1.0", latest="4.0.0-beta" -> True (忽略 prerelease 后缀)
    """
    def parse(v: str) -> tuple:
        # 去掉 -beta / -rc 等后缀
        v = v.split("-")[0].lstrip("v")
        parts = v.split(".")
        return tuple(int(p) if p.isdigit() else 0 for p in parts) + (0,) * (3 - len(parts))

    return parse(latest) > parse(current)


def get_upgrade_command(target_tag: str, repo: Optional[str] = None,
                        method: str = "git") -> str:
    """生成升级命令 (按 method 切换 git pull / pip install / docker pull)

    method:
    - "git": git pull + restart (开发模式)
    - "pip": pip install --upgrade
    - "docker": docker pull + recreate
    - "binary": wget binary + restart (PyInstaller)
    """
    target_repo = repo or GITHUB_REPO

    if method == "git":
        return f"cd /path/to/smr && git pull origin main && systemctl restart smr"
    elif method == "pip":
        return f"pip install --upgrade git+https://github.com/{target_repo}.git"
    elif method == "docker":
        return f"docker pull ghcr.io/{target_repo}:{target_tag} && docker compose up -d"
    elif method == "binary":
        return (
            f"wget https://github.com/{target_repo}/releases/download/{target_tag}/"
            f"supermodel_router -O /usr/local/bin/supermodel_router && "
            f"chmod +x /usr/local/bin/supermodel_router && systemctl restart smr"
        )
    return f"# Unknown upgrade method: {method}"
