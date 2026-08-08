# SuperModel Router (SMR) — 项目结构文档

**版本**：v0.5.6 (2026-08-09)

**仓库**：https://github.com/IGhostHuang/supermodel_router

**说明**：本文档基于 v0.5.6 版本（包含本次更新的 agent:fast / agent:hybrid / agent auto-dispatch / Trae IDE 集成修复）。

**规模**：50 个 Python 模块，共 26807 行代码

## 目录结构

```
supermodel_router/
├── __init__.py                 # 包入口, __version__ = "0.4.0" (待同步到 v0.5.6)
├── app.py                     # FastAPI 应用主入口, lifespan + 路由注册
├── config.py                  # 配置加载 + provider 管理 + 自动发现
├── version.py                 # 版本号 + GitHub release 检查
├── engine.py                  # 核心引擎: pick_chain / health-aware 路由
├── model_health.py            # 模型健康度状态机 (HEALTHY/DEGRADED/SKIP/HALF_OPEN)
├── penalty.py                 # 模型惩罚分状态
├── quality_gate.py            # 输出质量评估门
├── openai_routes.py           # /v1/chat/completions + agent:* 模式分发 ⭐
├── admin_api.py               # /v1/admin/* 管理 API
├── admin_ui.py                # 管理 UI 模板 + 服务
│
├── agent_loop.py              # AgentLoop ReAct 主循环 ⭐
├── agent_state.py             # SQLite agent 状态持久化
├── tool_registry.py           # 工具注册表 (file_read, run_command, http_get...)
├── moa_selector.py            # MOA 复杂度评分 + 模型选择 ⭐
├── fusion_router.py           # Fusion plans 多阶段路由
├── context_bridge.py          # 切链时上下文注入 (system message sync)
│
├── free_auto_discovery/       # 免费模型自动发现模块
│   ├── __init__.py
│   ├── scanner.py             # 扫描 OpenRouter/free-llm API
│   └── evaluator.py           # 评估候选模型质量
│
├── v0_9_integration/          # 易经算法集成 (3.11+)
│   ├── __init__.py
│   ├── trigram_router.py      # 8 卦 provider 映射
│   └── nine_gong.py           # 9 宫 dashboard 布局
│
└── tests/                     # 单元测试
```

## 文件清单

### __main__.py/ (1 文件)

#### `__main__.py`
- **规模**: 37 行
- **说明**: supermodel_router/__main__.py — python -m supermodel_router entry point
- **函数**: main

### ability_index.py/ (1 文件)

#### `ability_index.py`
- **规模**: 172 行
- **说明**: supermodel_router/ability_index.py — Ability 索引表 (v4.0.0)
- **类**: ChannelLink, AbilityIndex

### admin_api.py/ (1 文件)

#### `admin_api.py`
- **规模**: 2890 行
- **说明**: supermodel_router/admin_api.py — 管理 API 端点 (v3.2.0 拆分)
- **函数**: init, _pricing_display, _refresh_async, _compute_acceptance_rate, _load_model_size_cache, _model_size_lookup, _fingerprint_key, _format_remaining
- **异步**: health, admin_modalities, admin_routes, admin_models, admin_providers_list, admin_stats, admin_refresh, admin_free_models...

### admin_ui.py/ (1 文件)

#### `admin_ui.py`
- **规模**: 2202 行
- **说明**: supermodel_router/admin_ui.py - v4.1.0 5-Tab Navigation + Fusion Panel + Config Consolidation
- **异步**: admin_page, admin_9gong, admin_guide_page

### admin_ui_guide.py/ (1 文件)

#### `admin_ui_guide.py`
- **规模**: 528 行
- **说明**: admin_ui_guide.py — SMR Admin UI 使用指引页 (v3.28 增量)
- **异步**: admin_guide_page

### agent_loop.py/ (1 文件)

#### `agent_loop.py` ⭐
- **规模**: 403 行
- **说明**: agent_loop.py — SMR v0.5.0 ReAct Agent Loop
- **类**: AgentResult, AgentLoop
- **函数**: _extract_after_marker, _parse_action, _to_stored_plan, init_agent_loop, get_agent_loop
- **异步**: _checkpoint_entities

### app.py/ (1 文件)

#### `app.py` ⭐
- **规模**: 663 行
- **说明**: supermodel_router/app.py — FastAPI 主服务 v3 (模态感知路由)
- **函数**: _refresh_async
- **异步**: lifespan, public_usage_middleware, smr_design_page, smr_design_meta

### budget_router.py/ (1 文件)

#### `budget_router.py`
- **规模**: 423 行
- **说明**: budget_router.py — L2 Smart Budget-Aware Routing (v3.23.0)
- **类**: ModelCostEstimate, CostTable, QualityEstimator, BudgetAwareRouter, RoutingStrategyDispatcher
- **函数**: auto_select, get_dispatcher

### classifier.py/ (1 文件)

#### `classifier.py`
- **规模**: 587 行
- **说明**: supermodel_router/classifier.py — 模型能力分类 + 评分 v2
- **函数**: normalize_pricing_provider, get_tier_bonus, get_custom_keywords, get_modality_base_score, is_cloudflare_limited_free, pricing_detail, classify_pricing, classify_model...

### cli.py/ (1 文件)

#### `cli.py`
- **规模**: 121 行
- **说明**: model-router CLI — 管理 Model Router 服务
- **函数**: api_get, cmd_health, cmd_models, cmd_routes, cmd_stats, cmd_refresh, main

### config.py/ (1 文件)

#### `config.py`
- **规模**: 478 行
- **说明**: supermodel_router/config.py — 配置加载 + 热重载
- **类**: Config
- **函数**: _load_config

### context_bridge.py/ (1 文件)

#### `context_bridge.py`
- **规模**: 670 行
- **说明**: supermodel_router/context_bridge.py — v3.5.0 上下文桥接 + 过期标记 + 主动盘点 + 切链 abort
- **类**: SwitchRecord, ContextBridge
- **函数**: _format_attempt_block

### context_compressor.py/ (1 文件)

#### `context_compressor.py`
- **规模**: 249 行
- **说明**: supermodel_router/context_compressor.py — 阴阳本源 5 层压缩 (v4.0.0 骨架)
- **类**: ContextCompressor

### detector.py/ (1 文件)

#### `detector.py`
- **规模**: 128 行
- **说明**: supermodel_router/detector.py — 请求输入/输出类型检测
- **函数**: detect_chat_input_modality, detect_chat_output_modality, detect_image_gen_params, detect_streaming, match_modality_for_request

### discovery_pipeline.py/ (1 文件)

#### `discovery_pipeline.py`
- **规模**: 277 行
- **说明**: L3 discovery pipeline: verify L1/L2 finds and integrate into SMR.
- **类**: DiscoveryPipeline

### engine.py/ (1 文件)

#### `engine.py`
- **规模**: 1737 行
- **说明**: supermodel_router/engine.py — 路由引擎 v3: 质量评分 + 模态路由 + 并发槽位 + 错误分类
- **类**: ProviderStats, RouteResult, CandidateResult, RouteEngine
- **函数**: classify_error, compute_quality_score, compute_combined_score, set_global_engine, get_global_engine
- **异步**: proxy_chat_request, proxy_images_generations, _proxy_dashscope, _proxy_huggingface, _proxy_modelscope_async, _proxy_normal, _proxy_stream

### free_auto_discovery.py/ (1 文件)

#### `free_auto_discovery.py`
- **规模**: 283 行
- **说明**: free_auto_discovery.py -- in-process scheduler that drives L1/L2/L3 discovery.
- **类**: DiscoveryRunResult, FreeAutoDiscovery
- **函数**: init_free_auto_discovery, get_free_auto_discovery

### free_models.py/ (1 文件)

#### `free_models.py`
- **规模**: 446 行
- **说明**: free_models.py — L1 Free Resource Layer (v3.23.0)
- **类**: FreeModelInfo, QuotaStatus, FreeModelRegistry
- **函数**: init_free_model_registry, get_free_model_registry

### fusion_composer.py/ (1 文件)

#### `fusion_composer.py`
- **规模**: 907 行
- **说明**: fusion_composer.py — Intelligent automatic fusion group composition.
- **类**: PromptAnalysis, ModelInfo, ComposedPlan, AutoFusionComposer
- **函数**: _get_provider, _detect_language, _estimate_tokens, _estimate_complexity, analyze_prompt, select_operator, _get_models_from_engine, _filter_healthy...
- **异步**: select_models

### fusion_metrics.py/ (1 文件)

#### `fusion_metrics.py`
- **规模**: 222 行
- **说明**: fusion_metrics.py — in-memory per-plan statistics for n1_fusion.
- **类**: _PlanBucket, FusionMetrics
- **函数**: _percentile, _state_dir

### fusion_presets.py/ (1 文件)

#### `fusion_presets.py`
- **规模**: 441 行
- **说明**: supermodel_router/fusion_presets.py — Fusion 默认组合预设 (v4.3.0)
- **函数**: list_presets, get_preset, _get_registry, _model_context, _get_health_manager, _pick_models, resolve_plan, seed_all

### fusion_router.py/ (1 文件)

#### `fusion_router.py`
- **规模**: 1337 行
- **说明**: fusion_router.py -- the unified "any-to-any fusion model" layer for SMR.
- **类**: FusionStep, FusionResult, FusionRouter
- **函数**: _quick_health_tier, _extract_text, _usage, _truncate, _looks_low_quality, _classify_intent, init_fusion_router, get_fusion_router...
- **异步**: _invoke_leaf, _invoke_with_fallback, op_expert, op_vote, op_refine, op_pipeline, op_n1_fusion, _run_operator...

### git_checkpoint.py/ (1 文件)

#### `git_checkpoint.py`
- **规模**: 565 行
- **说明**: git_checkpoint.py -- lightweight file-system checkpoint / rollback for SMR.
- **类**: Checkpoint, RollbackResult, DiffEntry, CheckpointManager
- **函数**: get_checkpoint_manager, reset_checkpoint_manager, _atomic_write_json, _files_equal
- **异步**: init_checkpoint_manager

### group_wizard.py/ (1 文件)

#### `group_wizard.py`
- **规模**: 165 行
- **说明**: supermodel_router/group_wizard.py — 场景化分组向导 (v3.10.0 Phase K)
- **函数**: get_preset, list_presets, preset_to_filter, get_filter_for_preset, apply_preset

### loop_engine.py/ (1 文件)

#### `loop_engine.py`
- **规模**: 121 行
- **说明**: supermodel_router/loop_engine.py — 周天循环 Loop Engine (v3.21.0)
- **类**: LoopEngine
- **函数**: get_loop_engine

### maker_checker.py/ (1 文件)

#### `maker_checker.py`
- **规模**: 230 行
- **说明**: supermodel_router/maker_checker.py — 路由决策分离: maker 选路 → checker 验质量 (v3.21.0)
- **类**: MakerDecision, CheckResult, RouteMaker, RouteChecker, MakerCheckerEngine

### memory_bus.py/ (1 文件)

#### `memory_bus.py`
- **规模**: 302 行
- **说明**: supermodel_router/memory_bus.py — 跨请求经验复用 + 路由记忆 (v3.21.0 周天循环)
- **类**: RouteMemory, AggregatedPattern, MemoryBus

### middleware.py/ (1 文件)

#### `middleware.py`
- **规模**: 293 行
- **说明**: middleware.py — L4 Middleware Models (v3.23.0)
- **类**: CompressedContext, ContextCompressor, ContextSlicer, PromptRefiner, MiddlewarePipeline
- **异步**: _default_noop_call

### moa_selector.py/ (1 文件)

#### `moa_selector.py` ⭐
- **规模**: 453 行
- **说明**: moa_selector.py — SMR v0.5.0 自适应 Mixture-of-Agents 选择器
- **类**: ModelChoice, MOAConfig, MOASelector, MOAResult
- **函数**: record_outcome, is_in_cooldown, score_complexity, complexity_label, get_moa_selector, reset_moa_selector
- **异步**: run_moa, _invoke

### model_discovery.py/ (1 文件)

#### `model_discovery.py`
- **规模**: 228 行
- **说明**: L1 Model Discovery — probe known free-tier LLM API platforms.
- **类**: DiscoveredModel, ModelDiscovery

### model_filter.py/ (1 文件)

#### `model_filter.py`
- **规模**: 154 行
- **说明**: supermodel_router/model_filter.py — 多维度模型筛选引擎 (v3.10.0 Phase J)
- **类**: ModelFilter
- **函数**: apply_filter, model_to_dict

### model_groups.py/ (1 文件)

#### `model_groups.py`
- **规模**: 355 行
- **说明**: supermodel_router/model_groups.py — 模型分组管理 (v3.9.0)
- **类**: ModelGroup, ModelGroupManager
- **函数**: init_model_group_manager, get_model_group_manager

### model_health.py/ (1 文件)

#### `model_health.py`
- **规模**: 1006 行
- **说明**: supermodel_router/model_health.py — 模型健康度管理 (v3.15.0)
- **类**: HealthState, ModelHealth, ModelHealthManager, SingleflightGuard, QuotaSemaphore, SuccessDecay, AvailabilityGuard
- **函数**: get_model_health_manager, init_model_health_manager, get_availability_guard

### model_manager.py/ (1 文件)

#### `model_manager.py`
- **规模**: 402 行
- **说明**: supermodel_router/model_manager.py — 模型管理模块 v3.3
- **类**: ModelSnapshot, DiffResult, DiscoveryEngine, ModelNotifier, ListMgr, AutoRules, ModelManager

### model_rules.py/ (1 文件)

#### `model_rules.py`
- **规模**: 441 行
- **说明**: supermodel_router/model_rules.py — 模型管理规则引擎 v3.3
- **类**: ModelRule, DiscoveryRecord, ModelDiff, ModelRuleEngine

### models.py/ (1 文件)

#### `models.py`
- **规模**: 566 行
- **说明**: supermodel_router/models.py — 模型发现 + 过滤 + 分类
- **类**: ModelInfo, ProviderState, ModelRegistry
- **函数**: normalize_base_url, _extract_context_window

### openai_routes.py/ (1 文件)

#### `openai_routes.py` ⭐
- **规模**: 1129 行
- **说明**: supermodel_router/openai_routes.py — OpenAI 兼容 API 路由 (v3.2.0 拆分)
- **函数**: init
- **异步**: public_chat_completions, chat_completions, images_generations, images_edits, embeddings, list_models, get_model

### orchestrator.py/ (1 文件)

#### `orchestrator.py`
- **规模**: 272 行
- **说明**: orchestrator.py — L3 Multi-Modal Orchestration (v3.23.0)
- **类**: Modality, TaskKind, TaskSpec, TaskPlan, TaskResult, TaskClassifier, PlanExecutor
- **函数**: build_image_gen_plan, build_multimodal_fusion_plan, build_parallel_fusion_plan

### plan_mode.py/ (1 文件)

#### `plan_mode.py`
- **规模**: 969 行
- **说明**: plan_mode.py -- "plan first, execute later" three-stage mode for SMR.
- **类**: ChangeType, PlanStatus, RiskLevel, ChangeRequest, PlanStep, ChangePlan, ChangeResult, RoutingPlanner
- **函数**: get_routing_planner, reset_routing_planner
- **异步**: init_routing_planner

### platform_scanner.py/ (1 文件)

#### `platform_scanner.py`
- **规模**: 171 行
- **说明**: L2 new-platform community scanner.
- **类**: NewPlatformCandidate, PlatformScanner

### pricing.py/ (1 文件)

#### `pricing.py`
- **规模**: 141 行
- **说明**: supermodel_router/pricing.py — Token 成本加载器 (v1.0.0)
- **类**: PricingDB
- **函数**: get_pricing, init_pricing

### public_api.py/ (1 文件)

#### `public_api.py`
- **规模**: 350 行
- **说明**: supermodel_router/public_api.py — 对外 API 模块 (v3.7.0)
- **类**: PublicKeyManager
- **函数**: _hash_key, init_public_key_manager

### quality_gate.py/ (1 文件)

#### `quality_gate.py`
- **规模**: 754 行
- **说明**: quality_gate.py — Output quality validation and assurance for Fusion.
- **类**: QualityResult, QualityStats, QualityGate
- **函数**: _check_emptiness, _check_error_markers, _check_truncation, _check_repetition, _check_garbled, _check_relevance, _check_adequacy, _check_structure...

### scheduler.py/ (1 文件)

#### `scheduler.py`
- **规模**: 337 行
- **说明**: supermodel_router/scheduler.py — 智能调度: 任务链 + 并行聚合 + 中间模型 (v3.21.0)
- **类**: StageResult, ChainPlan, TaskScheduler

### scoring_engine.py/ (1 文件)

#### `scoring_engine.py`
- **规模**: 533 行
- **说明**: supermodel_router/scoring_engine.py — Auto-Combo 12 因子评分引擎 (v4.0.0)
- **类**: ScoringContext, FactorResult, ScoringResult, AutoComboScorer
- **函数**: get_scorer, build_context, compute_auto_combo_score

### session_memory.py/ (1 文件)

#### `session_memory.py`
- **规模**: 234 行
- **说明**: session_memory.py — L5 Context Continuity v2 (v3.23.0)
- **类**: SessionFact, SessionMemoryStore
- **异步**: extract_facts_from_session

### task_planner.py/ (1 文件)

#### `task_planner.py`
- **规模**: 282 行
- **说明**: task_planner.py -- SMR v0.5.0 Agent framework: task planning layer.
- **类**: Step, Plan, TaskPlanner
- **函数**: _short_id, _infer_tool, _parse_plan_response

### tool_registry.py/ (1 文件)

#### `tool_registry.py`
- **规模**: 501 行
- **说明**: tool_registry.py -- tool registration center for the SMR v0.5.0 Agent framework.
- **类**: CircuitOpenError, Tool, ToolResult, ToolRegistry
- **函数**: _stringify, _truncate, _cache_key, _schema, init_tool_registry, get_tool_registry, reset_tool_registry
- **异步**: _file_read, _file_write, _list_directory, _search_files, _http_get, _run_command, _echo_message

### tools/ (1 文件)

#### `tools/parse_model_size.py`
- **规模**: 203 行
- **说明**: parse_model_size.py — SMR v3.15.0 A阶段 参数量识别工具
- **函数**: _parse_one, parse_model_list, save_cache, load_cache, run_a3

### version.py/ (1 文件)

#### `version.py` ⭐
- **规模**: 449 行
- **说明**: supermodel_router/version.py — 版本元数据 + GitHub release 检查
- **函数**: load_version_meta, get_cached_release, fetch_latest_release, is_newer_version, get_upgrade_command

## Agent 模式深度解析 (v0.5.6)

SMR 在 `openai_routes.py` 中实现了 4 个 agent 模式 + 1 个自动 dispatch:

| 模式 | 触发方式 | 内部实现 | 速度 | 用途 |
|------|---------|---------|------|------|
| `agent` (bare) | 模型名=`agent` | normalize 到 `agent:auto` → 自动选择 | 5-60s | **推荐默认** |
| `agent:auto` | dispatcher | 根据 query 特征选 fast/moa/auto/hybrid | 5-60s | 同 bare |
| `agent:fast` | dispatcher 短查询 | 单次 `_bare/deepseek-v4-flash` 调用 | **5-15s** | 连通性测试/简单对话 |
| `agent:moa` | dispatcher 长查询+质量关键词 | MOA 多模型投票 | 12-60s | 纯文本追求质量 |
| `agent:auto` (manual) | `agent:auto` | ReAct + 工具调用 (单 LLM 决策) | 30-60s | 需要工具的简单任务 |
| `agent:hybrid` | dispatcher 含工具动词 / 手动 | MOA×2 + ReAct + 工具 | **15-30s** | 最高质量多步任务 |

### Auto-Dispatch 规则 (v0.5.5)

```python
tool_keywords = (读, 写, 执行, 运行, 搜索, 查找, 获取, file_read, run_command, ...)
quality_keywords = (分析, 对比, 写代码, 总结, 翻译, plan, analyze, ...)

if has_tool:        → agent:hybrid
elif short (<30):   → agent:fast
elif wants_quality: → agent:moa
else:               → agent:fast
```

### agent:hybrid 三阶段

```
Phase 1: Plan MOA     (1 fast model, 5-10s)
  → 输出 5 步执行计划

Phase 2: ReAct Execute  (AgentLoop, 5-30s)
  → 调用 run_command / file_read / http_get / echo_message 等工具
  → SQLite 持久化状态 (data/agent_state.db)
  → 迭代直到完成

Phase 3: Synth MOA    (1 fast model, 5-10s)
  → 综合执行轨迹 + 工具结果
  → 输出最终答案
```

### 关键修复历史

| 版本 | 修复 |
|------|------|
| v0.5.1 | agent:hybrid 模式上线 (MOA + ReAct + MOA) |
| v0.5.2 | agent:* 模式支持 SSE streaming (text/event-stream) |
| v0.5.3 | agent:* 注册到 /v1/models (ChatBox/Trae 验证模型存在) |
| v0.5.4 | agent:fast 模式 (5-15s 单 LLM 调用) |
| v0.5.5 | hybrid 加速 (30-200s → 15-30s, 砍 plan/synth MOA 投票数) |
| v0.5.5 | bare 'agent' 自动 dispatch |
| v0.5.6 | bare 'agent' normalize 到 'agent:auto' 修复路由问题 |

## 关键集成点

### ChatBox / Trae IDE 接入

```
API 格式:    OpenAI Chat Completions 格式
请求地址:    http://<SMR_HOST>:6473/v1
模型 ID:    agent   (推荐, 自动选择)
API 密钥:   sk-any-string (SMR 不强制验证)
```

### 注册的 agent 模型 (在 GET /v1/models 中)

```
agent           🤖 自动选择 (推荐)
agent:fast      ⚡ 单次 LLM 调用 (5-15 秒)
agent:moa       MOA 多模型投票 (12-60 秒)
agent:auto      ReAct + 工具调用 (30-60 秒)
agent:hybrid    MOA×2 + ReAct (15-30 秒, 最高质量)
```
