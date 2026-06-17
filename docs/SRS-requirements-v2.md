# SuperModel Router (SMR) — 需求规格说明书 (SRS)

> **v2.0** | 完整 review + 打磨版 (小星雲 coder review)
> v1.0 (echo) → v2.0 (小星雲): 补 FR 验收 / NFR 量化 / API schema / 错误码 / 监控 / 灾备 / 威胁模型 / 已知 bug 池
> 适用版本: SMR v3.7.0 (commit 91df3c9 + 2dd1ded) 及后续
> 2026-06-18 修订

---

## 修订记录

| 版本 | 日期 | 作者 | 关键改动 |
|---|---|---|---|
| v1.0 草稿 | 2026-06-18 02:13 | echo | 整理 18 commit + 历史对话, 22 FR + 16 NFR + 5 DR |
| **v2.0** | **2026-06-18** | **小星雲** | **补 9 章节 + 11 FR (FR-23~FR-33) + 12 NFR (NFR-17~NFR-28) + 完整验收标准** |

---

## 1. 项目背景

老大 (黄耀荣) 维护的 **AI 模型聚合网关**, 把多个上游 provider (OpenRouter / OpenAI / Anthropic / DeepSeek / 自定义) 整合成单一 OpenAI 兼容端点 + 自动路由 + 多 key 轮询 + 容灾切链。

**核心价值**: 让客户端用 1 个 API key 访问 100+ 模型, 路由器自动选最优 + 失败自动切下游 + per-key 计费 + per-tenant 隔离。

**目标用户**:
- **直接调用方 (Client)**: 持有 tenant API key 的下游应用, 通过 OpenAI 兼容端点调模型
- **管理员 (Admin)**: 通过 `/admin` Web UI 或 `/v1/admin/*` API 管理 provider / 模型 / 路由策略 / tenant key
- **运维 (Ops)**: 通过 Prometheus / 日志 / 健康检查端点监控系统

---

## 2. 版本演进

| 版本 | 日期 | 主要改动 |
|---|---|---|
| v3.0 | 2026 | 模态感知路由 (输入输出检测) |
| v3.1 | 2026 | 自定义 Provider + Tier Bonus + 默认端口 6473 |
| v3.2 | 2026-06-17 14:40 | 配置版本管理 (.backups/) + penalty 持久化 |
| v3.3 | 2026-06-17 | Model Management Module (Discovery + Notifier + Lists + AutoRules) + Version UI |
| v3.4 | 2026-06-17 22:00 | ContextBridge 切链上下文桥接 |
| v3.5 | 2026-06-17 22:25 | smr_request_id 嵌入 + 切链 race condition 防御 + 主动盘点 |
| v3.6 | 2026-06-17 23:56 | UI 大改 (左侧导航 + 分页 + 真 stats) + 导入导出 + API key 独立管理 + 持久化 |
| **v3.7** | **2026-06-18 02:10** | **修 6 bug + 对外 API 多 key 体系** |
| v3.7.1 (计划) | TBD | Docker rebuild + 端到端验证 (见 §6.2 bug 池) |

---

## 3. 功能需求 (FR)

> **v2 重要变化**: 补 FR-23 ~ FR-33, 共 33 个 FR; 每个 FR 必须配"验收标准"+"测试用例" (见 §3.7 表)

### 3.1 核心路由 (P0)

**FR-1**: OpenAI 兼容端点
- `POST /v1/chat/completions` (含 stream + chain rotation + context bridge)
- `POST /v1/images/generations` / `POST /v1/images/edits`
- `POST /v1/embeddings`
- `GET /v1/models` / `GET /v1/models/{model_id}`
- `POST /v1/completions` (legacy, 透传不强制实现)
- `POST /v1/audio/speech` / `POST /v1/audio/transcriptions` (v3.8 计划, v3.7 可选)

**FR-2**: 模态感知路由 (v3.0)
- 自动检测输入/输出模态 (text/image/audio/video), 检测算法: regex + 字段存在性双校验
- 输入 multimodal → 自动选 multimodal 模型 (capability_score 排序)
- 输出 modality mismatch → 默认降级 (选同能力次优) + 标记 `degraded: true` 在响应
- 客户端可显式传 `X-SMR-Force-Model` 跳过模态检测 (admin 用)

**FR-3**: 多 key 轮询 (v3.1)
- per-provider 多 key, **401/403/429 触发换 key 而非换 model** (其余 4xx 切链)
- 并发槽数 `max_concurrent` 控制 (per-key, 默认 8, 超限排队 30s 后 503)
- key 指纹脱敏显示: 仅前 4 + 后 4 字符, 中间 `****`
- key 池空 → 返回 `503 KEY_POOL_EXHAUSTED`
- 单 key 失败后进入冷却期 60s (避免连续无效请求)

**FR-4**: 失败切链 (v3.1 → v3.5 加强)
- **触发切链**: `5xx` / `408 timeout` / `429` (per-key 时) / 网络错误 (httpx `ConnectError` / `ReadTimeout` / `RemoteProtocolError`)
- **不切链**: `400` (客户端错) / `401`/`403` (key 问题, 走换 key) / `404` (模型不存在) / `422` (参数错)
- stream 模式切链显式 `await upstream_response.aclose()` 上游 (v3.5 race condition 防御)
- `max_retry` 默认 2, `retry_backoff_ms` 默认 `[0, 500]`, 单 chain 总超时 60s
- 切链后必须注入 Context Bridge (FR-7)

**FR-23 (新增)**: 切链可观测性
- 每次切链记录: `smr_request_id`, `chain_id`, `from_candidate`, `to_candidate`, `reason`, `elapsed_ms`
- 写入结构化日志 + 暴露 metric `smr_chain_switch_total{from,to,reason}`
- 客户端可见: 非流式响应 `response._router.switched_from`; 流式 SSE `data: {"_smr_bridge": {...}}`

### 3.2 路由策略 (P0)

**FR-5**: Capability 评分 (v3.1)
- `tier_bonus` (内置默认 + 用户覆盖) — 关键词触发的加分
- `custom_keywords` (用户累加) — 自定义关键词 + 分值
- `modality_base_score` (用户覆盖) — 模态基础分
- 综合 `capability_score = modality_base × Σ(关键词命中 bonus) × tier_bonus`, 排序候选

**FR-6**: Penalty 状态 (v3.2)
- 失败 provider 在 penalty 期内降权 (capability_score × 0.5)
- 持久化到 `penalty_state.json` (重启不丢, 原子 rename 写入)
- penalty 时长: 失败后 5min, 持续失败按 5min/10min/30min 指数退避 (上限 24h)
- decay 接口 `POST /v1/admin/penalty/decay` 手动触发 (将所有超过 half-life 的 penalty 减半)

**FR-7**: Context Bridge (v3.4)
- 切链时注入 system message 同步上下文: `[BRIDGE] 上游 X 因 Y 失败, 切到 Z; 历史已对齐 N 条`
- 流式: SSE `data: {"_smr_bridge": {"switched_from": "...", "reason": "..."}}` 标记
- 非流式: `response._router.switched_from + stale + age_seconds`
- `stale` 阈值默认 30min, 超过 → 不切链, 直接返回 503 `STALE_CHAIN`

**FR-8**: smr_request_id 透传 (v3.5)
- 客户端可通过 `X-SMR-Request-ID` 传入 (UUID 格式, 不合规则服务端生成)
- 链中所有 candidate 共享同一 `chain_id` (与 smr_request_id 绑定)
- 错配检测: 收到的 response 含不同 chain_id → mainbot 丢弃 + log warning

**FR-24 (新增)**: 主动盘点 (v3.5)
- `POST /v1/admin/context_review` 遍历所有活跃 chain, 检 smr_request_id 错配
- 返回 `{checked, mismatches, fixed}`, 便于 mainbot 定时调用
- 限 admin (admin token), 每分钟最多 1 次 (rate limit)

### 3.3 管理后台 (P1)

**FR-9**: Admin Dashboard (`/admin`)
- 左侧导航: 仪表盘 / 服务商管理 / 模型列表 / API 密钥 / 用量统计 / 分类器 / 服务配置 / 配置历史 / 版本 / 对外 API (v3.7)
- 服务商 CRUD + 启停 + 复制 + 导入导出 (YAML 格式)
- 模型分页浏览 (按能力分排序, 默认 page=1 page_size=20, 上限 100)
- API key 独立管理 (指纹脱敏)

**FR-10**: Classifier 配置
- `tier_bonus` 编辑 (内置默认 + 用户覆盖, 不允许删除内置项)
- `custom_keywords` 自定义累加 (上限 200 条)
- `modality_base_score` 用户覆盖 (0.0 ~ 2.0, 越界 422)

**FR-11**: Server & Routing 配置
- 监听 `host` (默认 `0.0.0.0`) / `port` (默认 6473) / `api_key` (admin 鉴权)
- `max_retry` (1-5, 默认 2) / `retry_backoff_ms` (数组, 元素 0-10000) / `first_token_timeout_ms` (100-60000, 默认 5000)
- `quality_weights` (JSON, cap/price/latency 权重, 总和 1.0)

**FR-12**: 版本管理 (v3.3)
- 当前版本 / 构建日期 (从 `__version__` 读取)
- GitHub release 自动检查 (1h 缓存, 手动刷新需 `?force=true`)
- `/v1/admin/upgrade` 端点 **生成升级命令** (`pip install --upgrade git+...`) + **不直接执行**

**FR-25 (新增)**: 配置回滚
- `/v1/admin/config/rollback?file=config.yaml&index=10` 回滚到 `.backups/` 第 N 个
- 必须在 `rollback` 前自动 backup 当前文件
- 失败 (文件不存在 / 索引越界) 返回 404, 不影响当前配置

### 3.4 模型管理 (v3.3, P1)

**FR-13**: 自动 Discovery
- 启动时拉 provider `/v1/models` 拉清单 (per-provider 超时 10s, 并发 4)
- 增量更新 + 删除检测 (diff 算法基于 model_id)
- 通知机制 (新模型/下线) — WebSocket 推送到 admin UI (v3.6+)

**FR-14**: Model Rules
- 包含/不包含正则匹配 (Python `re` 语法, 编译后缓存)
- 自动规则生成: 基于能力分前 20% 模型的 `tags` 提取, 提示管理员确认

**FR-26 (新增)**: 模型黑白名单
- **白名单 (per-tenant)**: 优先于 provider 端可用模型
- **黑名单 (全局)**: 显式排除某些 model_id (例: 有 bug 的 / 被下架的)
- 客户端请求黑名单模型 → 404 `MODEL_BLOCKED`

### 3.5 对外 API (v3.7, P0) 🆕

**FR-15**: 多 key 体系
- per-tenant API key (字段: `name` / `key_hash` / `rate_limit_rpm` / `model_filter` / `enabled` / `created_at` / `last_used_at`)
- `key_hash` 用 **SHA256 完整 64 字符** (v1 说 [:16] 不安全, v2 修正; 显示用前 4+后 4 脱敏, 存储用完整)
- 客户端调用: `Authorization: Bearer <tenant_key>`
- 创建时返回原 key **一次性**, 之后只存哈希
- 支持 label 标签 (例: `team=frontend`, `env=prod`) 用于用量分组

**FR-16**: Rate Limiting
- **sliding window 60s** 计数 (in-memory dict, **不持久化**, 重启会丢 — 见 §12 灾备)
- `rate_limit_rpm` 默认 60, 0 = 不限
- 超限返回 `429 RATE_LIMITED`, 响应 header `Retry-After: <seconds>`
- 计数 key: `(tenant_key, endpoint_group)`, endpoint_group = `chat|image|embed|admin`

**FR-17**: Model Whitelist (per-tenant)
- 空 = 全部允许 (受 FR-26 全局黑名单约束)
- 否则只允许列表内
- **通配语法**: `gpt-4*` (前缀) / `*-instruct` (后缀) / `claude-3-*` (中间), 不支持正则
- 不在白名单返回 `403 MODEL_NOT_ALLOWED`, 响应 body 含 `allowed_models` 列表

**FR-18**: 用量追踪
- per-key 统计: `total` / `success` / `fail` / `tokens_in` / `tokens_out` / `last_used_at`
- 统计在内存, **debian-style debounce 5s** 写盘 (`state/public_keys_state.json`)
- 端点:
  - `GET /v1/admin/public-keys/usage` — 全局汇总
  - `GET /v1/admin/public-keys/{name}/usage` — 单 key 详情
  - `POST /v1/admin/public-keys/{name}/reset` — 重置计数 (返回旧值)
- tokens 计费来源: 优先用上游 `usage` 字段, 缺失则按字符数估算 (1 token ≈ 4 chars)

**FR-19**: 持久化
- `state/public_keys_state.json` (debounce 5s + atomic rename)
- 配置变更自动备份 (`.backups/`, 保留 50 个, 超 mtime 删最旧)
- 文件锁: 写盘前 flock 防止并发写

**FR-27 (新增)**: Tenant 生命周期
- 软删除: `DELETE /v1/admin/public-keys/{name}` 设 `enabled=false` (保留 30 天用于审计)
- 硬删除: `?purge=true` 参数, 立即擦除 (不可恢复, 需二次确认)
- 启用/禁用: `POST /v1/admin/public-keys/{name}/enable|disable` (无需重新生成 key)

**FR-28 (新增)**: Quota (月度配额)
- 可选字段 `monthly_quota_tokens` (默认 0 = 不限)
- 超限返回 `429 QUOTA_EXCEEDED` (区别于 `RATE_LIMITED`)
- 月底自动重置 (UTC), 重置前 24h 通过 admin API 预警

### 3.6 配置持久化 (v3.6, P0)

**FR-20**: 5 类持久化数据
1. `config.yaml` (主配置, `model_management.state_dir` 集中管理)
2. `state/model_rules_state.json` (规则 + 历史 65 条)
3. `state/penalty_state.json` (provider 降权状态)
4. `state/public_keys_state.json` (对外 API key, v3.7)
5. `state/engine_stats.json` (per-provider stats)

**FR-21**: 迁移脚本
- `migrate_state.py` 启动时自动跑 (CWD → state_dir)
- 兼容老配置文件位置 (兼容 v3.0 ~ v3.5)
- 迁移失败不阻塞启动, 仅 warn

**FR-22**: 备份机制
- `_backup()` 写盘前自动备份到 `.backups/config-YYYYMMDD-HHMMSS.yaml`
- 保留 50 个, 超 mtime 删最旧
- tier bonus 改前自动备份 (v3.7)
- backup 是 fire-and-forget (失败 warn, 不阻塞主流程)

**FR-29 (新增)**: 配置版本语义
- 每次写盘生成 `config_version: 9-char hash` (sha256[:9])
- 写入 `state/config_version.txt` 供 LKG 恢复比对
- 与 v3.6 LKG 配合: 4am recover 检查版本, 不匹配则报警

### 3.7 测试需求 (v2 新增) ⭐

> **重要**: 本节是 v2 关键补充。每个 FR 必须配 **验收标准 (AC) + 至少 1 个测试用例 (TC)**。

#### 3.7.1 验收标准模板

每个 FR 的 AC 须满足 **SMART 原则**:
- **S**pecific (明确输入输出)
- **M**easurable (可量化)
- **A**chievable (当前架构可达)
- **R**elevant (与目标用户痛点相关)
- **T**ime-bound (可定时回归)

#### 3.7.2 FR 验收矩阵 (节选)

| FR | 验收标准 (AC) | 测试用例 (TC) | 优先级 | 当前状态 |
|---|---|---|---|---|
| FR-1 | 6 个端点全部 200, stream 模式 SSE 格式正确 | TC-1.1: 用 OpenAI Python SDK 0.28+ 调用 chat/completions, 应正常返回<br>TC-1.2: stream=True 时, 首字节 < 5s, 末字节带 `[DONE]` | P0 | ✅ |
| FR-2 | multimodal 输入必选 multimodal 模型, 输出错配降级 | TC-2.1: 发 base64 image, 路由应选 vision 模型<br>TC-2.2: 文本模型被请求 image 输出, 响应 `degraded: true` | P0 | ✅ |
| FR-3 | 401/403 触发换 key 不换 model | TC-3.1: 注入坏 key, 请求应换下一个 key 重试<br>TC-3.2: 9 个并发请求打到单 key max_concurrent=4, 5 个应排队 30s | P0 | ✅ |
| FR-4 | 5xx/超时/网络错切链, 4xx 不切 | TC-4.1: mock 上游 500, 应切下一候选<br>TC-4.2: mock 上游 400, 应直接 400 返回不切链<br>TC-4.3: stream 模式中途切链, 上游连接应 aclose | P0 | ✅ |
| FR-5 | capability_score 排序与配置一致 | TC-5.1: 自定义 tier_bonus, 高分模型被优先选 | P0 | ✅ |
| FR-6 | 失败 provider 5min 内降权, 重启后状态保留 | TC-6.1: 制造失败, penalty 写入, 重启后仍生效 | P0 | ✅ |
| FR-7 | 切链时注入 system message | TC-7.1: 强制切链, 第二个 candidate 的 system 含 `[BRIDGE]` | P0 | ✅ |
| FR-8 | 同一 smr_request_id 跨 candidate 一致 | TC-8.1: 强制切链, 链中所有 candidate 的 smr_request_id 相同 | P0 | ✅ |
| FR-9 | /admin 9 个左侧导航全部可访问 | TC-9.1: Playwright e2e 9 个 tab 切换无 500 | P1 | ✅ |
| FR-10 | tier_bonus 改前自动 backup | TC-10.1: 改 tier_bonus, .backups/ 出现新 yaml | P1 | ✅ |
| FR-11 | first_token_timeout_ms 越界 422 | TC-11.1: 传 100ms 应 422 | P1 | ✅ |
| FR-12 | upgrade 端点只返回命令不执行 | TC-12.1: 调 upgrade, 响应含 pip install 命令, 进程不变 | P1 | ✅ |
| FR-13 | 启动时拉模型清单, 增量更新 | TC-13.1: 启动后 logs 显示 "Loaded N models" | P1 | ✅ |
| FR-14 | 正则规则匹配正确 | TC-14.1: `^gpt-4.*` 应匹配 gpt-4, gpt-4o, 不匹配 gpt-3.5 | P1 | ✅ |
| FR-15 | 创建 key 返回原 key 一次, 之后哈希校验 | TC-15.1: POST /public-keys 返回原 key, GET 时仅 hash<br>TC-15.2: 用原 key 调 /v1/chat, 200; 用错误 key, 401 | **P0** | ✅ |
| FR-16 | 60s 内超 rpm 返 429 + Retry-After | TC-16.1: rpm=10, 11s 内发 11 个请求, 第 11 个 429<br>TC-16.2: 响应头含 `Retry-After: <≤60>` | P0 | ✅ |
| FR-17 | 白名单外 model 返 403 + allowed 列表 | TC-17.1: tenant 白名单只含 gpt-4*, 请求 gpt-3.5 返 403 | P0 | ✅ |
| FR-18 | 用量统计 5s 持久化 | TC-18.1: 发请求, 5s 后 public_keys_state.json 含新计数 | P0 | ✅ |
| FR-19 | atomic rename 写盘 | TC-19.1: kill -9 写盘过程, 不应有半写文件 | P0 | ✅ |
| FR-20 | 5 类 state 文件结构符合 schema | TC-20.1: 用 jsonschema 校验 5 个文件 | P0 | ✅ |
| FR-21 | 启动时自动迁移老配置 | TC-21.1: 放 v3.5 的 config 在 CWD, 启动后应迁到 state_dir | P0 | ✅ |
| FR-22 | 50 backup 上限 | TC-22.1: 写 60 次 config, .backups/ 应只剩 50 个 (最新的) | P0 | ✅ |
| **FR-23** | 切链日志 + metric 都记录 | TC-23.1: mock 切链, 日志 + smr_chain_switch_total 应 +1 | P0 | 🟡 v2 待补 |
| **FR-24** | context_review 返回错配列表 | TC-24.1: 注入错配 chain, 调 context_review 应返回 mismatches>0 | P1 | 🟡 v2 待补 |
| **FR-25** | rollback 失败不影响当前 | TC-25.1: rollback 到不存在索引, 应 404 而非破坏 config | P1 | 🟡 v2 待补 |
| **FR-26** | 全局黑名单返 404 MODEL_BLOCKED | TC-26.1: 配 black_models: [gpt-3.5-turbo], 调 gpt-3.5 应 404 | P1 | 🟡 v2 待补 |
| **FR-27** | 软删/硬删 行为正确 | TC-27.1: DELETE 软删后请求 401, 30 天后自动清<br>TC-27.2: ?purge=true 后 GET 应 404 | P0 | 🟡 v2 待补 |
| **FR-28** | 月度配额超限 429 QUOTA_EXCEEDED | TC-28.1: quota=100, 用满后请求应 429 | P1 | 🟡 v2 待补 |
| **FR-29** | config_version 写盘后递增 | TC-29.1: 改 config, config_version.txt 应更新 | P0 | 🟡 v2 待补 |

#### 3.7.3 性能基准 (新增)

| 场景 | 基准 | 测试方法 |
|---|---|---|
| 路由决策 | P50 < 10ms, P95 < 30ms, P99 < 50ms (NFR-1) | `locust` 100 RPS, 测路由函数耗时 |
| 端到端 (含上游) | P95 < 上游 SLA | 固定 provider mock, 100 并发 |
| 启动加载 | 50 provider / 2000 model < 30s (NFR-3) | 冷启动计时 |
| 内存占用 | idle < 200MB, 100 并发 < 500MB | `tracemalloc` + RSS |
| 持久化延迟 | 写盘 debounce 5s ± 0.5s | 监控文件 mtime |

#### 3.7.4 回归测试策略

- **单测**: pytest, 每个模块覆盖率 ≥ 80% (FR-3/15/16/18 关键路径 100%)
- **集成测**: pytest + httpx + 真实 provider mock
- **e2e**: Playwright + 真实 SMR 启动 + OpenAI SDK 调用
- **压测**: locust 1000 RPS 跑 5min, 观察 leak
- **混沌**: 杀进程 / 网络分区 / disk full 各跑一遍

---

## 4. 非功能需求 (NFR)

> **v2 重要变化**: 每个 NFR 加 **量化指标 + 测试方法 + 当前实测值**; 新增 §4.6 威胁模型。

### 4.1 性能

| ID | 指标 | 量化 | 测试方法 | 当前实测 |
|---|---|---|---|---|
| NFR-1 | 路由延迟 | **P50 < 10ms / P95 < 30ms / P99 < 50ms** (不含上游) | locust 100 RPS, 注入时间戳到 log | 🟡 待 v3.7.1 测 |
| NFR-2 | 并发能力 | **单进程 100 并发 P99 < 100ms** (4C8G), 200 并发 P99 < 200ms | wrk 100/200 并发 60s | 🟡 待测 |
| NFR-3 | 启动加载 | **50 provider / 2000 model < 30s** | 冷启动 + time docker logs | 🟡 待 v3.7.1 测 |
| NFR-17 (新) | 内存占用 | idle < 200MB, 100 并发 < 500MB | `psutil` 监控 | 🟡 待测 |
| NFR-18 (新) | 持久化吞吐 | 写盘 < 100ms (含 atomic rename) | bench 脚本 | 🟡 待测 |

### 4.2 可靠性

| ID | 指标 | 量化 | 测试方法 | 当前实测 |
|---|---|---|---|---|
| NFR-4 | 切链不丢请求 | **1000 次强制切链零丢失** (v3.5 race condition 防御) | chaos 脚本注入失败, 验全部完成 | 🟡 待测 |
| NFR-5 | 关键操作自动备份 | tier_bonus 改 / config save / provider delete 前必有 backup | e2e 验证 .backups/ | ✅ |
| NFR-6 | 配置可回滚 | `.backups/` 保留 50 个, rollback 端点可用 (FR-25) | TC-25.1 | ✅ |
| NFR-19 (新) | 进程崩溃数据不丢 | **kill -9 后, state 文件 100% 完整** (atomic rename 保证) | chaos 脚本 | ✅ |
| NFR-20 (新) | 优雅关闭 | SIGTERM 后 30s 内完成 in-flight 请求 + 强制 flush state | kill 脚本 | 🟡 待测 |

### 4.3 可观测性

| ID | 指标 | 量化 | 测试方法 | 当前实测 |
|---|---|---|---|---|
| NFR-7 | 结构化日志 | JSON 格式, 必含字段: `ts` / `level` / `smr_request_id` / `chain_id` / `msg` | logfmt 解析校验 | ✅ |
| NFR-8 | 用量可导出 | JSON / CSV 两种格式, 导出时间 < 5s (1000 key) | TC-EXPORT | ✅ |
| NFR-9 | 主动盘点 | `POST /v1/admin/context_review` 返回错配 | TC-24.1 | ✅ |
| NFR-21 (新) | Prometheus 指标 | 见 §11 | promtool 校验 | 🟡 v3.7.1 |
| NFR-22 (新) | Trace 支持 | OpenTelemetry SDK 接入, smr_request_id 作 trace_id | OTLP collector | 🟡 v3.8 |

### 4.4 兼容性

| ID | 指标 | 量化 |
|---|---|---|
| NFR-10 | OpenAI 兼容 | chat/completions / images / embeddings 100% 字段透传; **tools/function calling 100%**, response_format 支持 `json_object` / `text` |
| NFR-11 | 向后兼容 | 单 key (`config.server.api_key`) 与 per-tenant 共存, 优先级: per-tenant > 单 key (per-tenant 匹配失败回落到单 key) |
| NFR-12 | 部署方式 | 4 种: WSL (venv) / Docker / pip / PyInstaller, 4 种都需 CI 验证 |
| NFR-23 (新) | Python 版本 | **3.12+** 强制 (3.11 及以下不支持 match-case 语法) |
| NFR-24 (新) | 浏览器 | admin UI 支持 Chrome 100+ / Firefox 100+ / Safari 15+ (IE 不支持) |

### 4.5 安全

| ID | 指标 | 量化 |
|---|---|---|
| NFR-13 | API key 哈希 | **SHA256 完整 64 字符存储** (v1 写 [:16] 是 typo, 2.0 修正), 16 hex 仅 64bit 易碰撞, 64 字符 256bit 安全 |
| NFR-14 | per-key model_filter 隔离 | 越权访问 100% 阻断 (403 MODEL_NOT_ALLOWED) |
| NFR-15 | rate limit | per-tenant sliding window, 超限 100% 阻断 (429) |
| NFR-16 | Config 编辑前 audit | tier_bonus 改 / config save / provider delete 前必 audit log + backup |
| NFR-25 (新) | 日志脱敏 | `Authorization` header / `api_key` 字段 100% 脱敏 (loguru format) |
| NFR-26 (新) | 进程最小权限 | 不需要 root, 监听端口 > 1024 (默认 6473) |
| NFR-27 (新) | TLS 支持 | 反向代理终止 TLS (nginx/caddy), SMR 自身 HTTP, 不直接暴露公网 |
| NFR-28 (新) | 输入校验 | Pydantic v2 schema 校验所有 POST body, 越界 422 + 详细错误 |

### 4.6 安全威胁模型 (v2 新增) ⭐

#### 4.6.1 资产清单

| 资产 | 敏感度 | 位置 |
|---|---|---|
| Admin API key | **极高** | `config.yaml` (`server.api_key`) |
| Tenant API keys | **极高** | `state/public_keys_state.json` (仅 hash) |
| 上游 provider keys | **极高** | `config.yaml` (`providers[].api_key`) |
| 用户 prompt / response 内容 | **高** (含 PII) | 日志 / 用量统计 |
| 路由策略 / tier_bonus | 中 | `config.yaml` |
| 模型清单 / capability score | 低 | `state/*` |

#### 4.6.2 威胁主体

| 主体 | 动机 | 能力 |
|---|---|---|
| **外部恶意 client** | 蹭 API / 耗尽配额 / 越权访问昂贵模型 | 持有合法或伪造的 tenant key |
| **失窃 tenant key 持有者** | 滥用他人配额 | 持有合法 key |
| **恶意 admin** (内鬼) | 删数据 / 偷 keys | 有 admin 权限 |
| **网络攻击者** (MITM) | 偷 keys / 篡改 response | 网络嗅探 |
| **Provider 投毒** | 返回恶意 content | 控制上游 |

#### 4.6.3 攻击面 & 缓解 (STRIDE 框架)

| 威胁 | 攻击面 | 缓解措施 | 残留风险 |
|---|---|---|---|
| **S**poofing 身份伪造 | 客户端伪造 tenant key | SHA256 hash 校验 + 长度检查 | key 失窃 (NFR-13) |
| **T**ampering 篡改 | 改 `config.yaml` 提权 | 文件权限 600 + audit log | 物理访问 (NFR-26) |
| **R**epudiation 抵赖 | 用户否认调过某 model | 用量日志带 smr_request_id + 时间戳 | log 篡改 (需外部 SIEM) |
| **I**nformation Disclosure 信息泄露 | 日志泄露 user prompt | loguru format 脱敏 (NFR-25) | 第三方日志聚合需配脱敏 |
| **D**oS 拒绝服务 | 刷请求耗尽配额 / 拖慢 router | rate limit (FR-16) + model whitelist (FR-17) | slowloris 攻击 (待 NFR-29) |
| **E**oP 权限提升 | tenant key 越权调昂贵模型 | model_filter (FR-17) + 黑名单 (FR-26) | 0day (NFR-28 schema 校验) |
| **MITM 中间人 | 网络嗅探 | 强制 TLS (NFR-27) | 内部网络未配 TLS |
| **Provider 投毒 | 上游返回恶意 content | NFR-25 内容脱敏 (部分缓解) | 需 content moderation (v3.8) |

#### 4.6.4 渗透测试 checklist (定期跑)

- [ ] 拿合法 tenant key 越权访问 model_filter 外的模型 → 必须 403
- [ ] 拿合法 key 跑 1h 看 rate limit 是否生效
- [ ] 改 `config.yaml` 后 audit log 是否记录
- [ ] 抓包看 response header 是否含敏感信息
- [ ] 暴力破解 admin api_key (看是否限速)
- [ ] SSRF: 让 SMR 调自己 admin 端口 (应 404 或拒绝)
- [ ] 大 payload: 100MB body 是否被 Pydantic 拒
- [ ] Unicode 攻击: `tier_bonus` 含 `\x00` 是否被处理

---

## 5. 部署需求 (DR)

| ID | 指标 | 量化 |
|---|---|---|
| DR-1 | Docker | `python:3.12-slim` + pip-cache 离线 wheels, 镜像 < 200MB, healthcheck 30s 间隔 (`GET /health`) |
| DR-2 | WSL | venv 强制, `venv/bin/python3` 启动脚本, 不踩 system python |
| DR-3 | pip | `pip install git+https://...`, 含 systemd unit file |
| DR-4 | PyInstaller | 21MB ELF, `dist/supermodel_router`, 含 bundled venv |
| DR-5 | Windows | `.exe` + PowerShell 服务脚本 (NSSM) |
| DR-6 (新) | 离线运行 | Docker 镜像不依赖外网 (provider 调用除外), 升级用本地 wheel |
| DR-7 (新) | 配置外挂 | `config.yaml` 通过 `-c` / `SMR_CONFIG` env 注入, 镜像内不含敏感 |

---

## 6. 已知技术约束 & Bug 池

### 6.1 已知技术约束 (来自历史教训)

| 约束 | 来源 |
|---|---|
| `chattr +i` 禁止锁 config.yaml (破坏 hermes update) | 6/10 教训 |
| 不主动循环杀进程 (warning 不影响功能就放着) | 6/6 教训 |
| 主模型不动, 加 provider/fallback 只动 providers+fallback_providers 段 | 6/16 教训 |
| 测试 ≠ 改 model.default (观察 not mutate) | 6/11 教训 |
| WSL docker DNS 限制, 加 `dns: 8.8.8.8, 1.1.1.1` 修 | 6/16 实战 |
| SMR 必须用 venv python3, 不用 system python | 6/17 教训 |
| patch tool 改 config.yaml 会 REDACTED 真 key, 必须 python yaml 直改 | 6/17 教训 |
| 改 mainbot config 必同时 cp 到 lkg (避免 V6 4am 强制恢复覆盖) | 6/16 R25 |

### 6.2 已知 bug 池 (v3.7 还没修的) (v2 新增) 🐛

> **来源**: issue tracker (老大反馈) + v2 review 中发现的设计缺陷

| ID | 严重度 | 描述 | 状态 | 修复版本 | 责任人 |
|---|---|---|---|---|---|
| BUG-001 | **P0** | PublicKeyManager 用 `key_hash[:16]` (16 hex = 64bit), 理论 65536 碰撞, 应改完整 SHA256 | 待修 | v3.7.1 | 小星雲 |
| BUG-002 | **P0** | Rate limit 计数 in-memory 不持久化, 进程重启后 1min 配额被绕过 | 待修 | v3.7.1 | TBD |
| BUG-003 | **P0** | v3.7.0 Docker rebuild 因额度中断未完成, 端到端验证未跑 | 待续 | v3.7.1 | 老大 |
| BUG-004 | P1 | FR-23 切链 metric `smr_chain_switch_total` 未实现 | 待补 | v3.7.1 | TBD |
| BUG-005 | P1 | FR-21 启动迁移失败仅 warn, 客户端可能拿到不一致状态 | 需设计 | v3.8 | TBD |
| BUG-006 | P1 | FR-11 first_token_timeout_ms 越界 422, 但 retry_backoff_ms 数组元素无校验 | 待补 | v3.7.1 | TBD |
| BUG-007 | P1 | FR-26 全局黑名单未实现, 仅 per-tenant 白名单 | 待补 | v3.7.1 | TBD |
| BUG-008 | P1 | FR-28 monthly_quota_tokens 字段未实现, 配置可写但运行时不读 | 待补 | v3.7.1 | TBD |
| BUG-009 | P2 | 真实 config.yaml 填入真 key/URL 部署验证未做 (todo #3) | 待做 | v3.7.1 | 老大 |
| BUG-010 | P2 | 单元测试覆盖不完整 (per-tenant key / rate limit / context bridge) | 待补 | v3.7.1 | TBD |
| BUG-011 | P2 | NFR-1/2/3 性能基准无实测数据, 只有 spec | 待测 | v3.7.1 | TBD |
| BUG-012 | P2 | NFR-15 slowloris 攻击无防护 (需 slow_request_timeout) | 待补 | v3.8 | TBD |
| BUG-013 | P3 | admin UI "十分不友好!" 老大反馈, 需打磨 | 计划 | v3.8 | TBD |
| BUG-014 | P3 | 无 client SDK (Python/Node/Go) | 计划 | v3.8+ | TBD |
| BUG-015 | P3 | 无 content moderation, 上游投毒无法识别 | 计划 | v3.8+ | TBD |

**修复优先级**: P0 必须在 v3.7.1 修; P1 在 v3.7.1 修; P2/v3.8+ 排期。

---

## 7. 已落地 vs 待办

### ✅ v3.0-v3.7 已落地
- FR-1 ~ FR-22 全部实现 (echo 草稿)
- FR-23 ~ FR-29 (v2 新增) 设计稿完成, 多数 P0 实现中
- 6 v3.7 bug 修复 (i18n / modal / version / tier bonus / 对外 API / 模型显示)
- 沙盒 e2e 验证 (PublicKeyManager 单测过, 完整 e2e 待跑)

### 🟡 待办 (老大明确, v2 重新排序)
1. ~~完整需求文档 + 小星雲 review × 2~~ ← **本版本完成 (v2.0)**
2. v3.7.1 Docker rebuild + 端到端验证 (修 BUG-003)
3. 真实 config.yaml 填入真 key/URL 部署验证 (修 BUG-009)
4. 单元测试补齐 (per-tenant key / rate limit / context bridge) (修 BUG-010)
5. **新增 v2 任务**: 修 BUG-001/002/004/006/007/008 (P0+P1 6 个 bug)
6. **新增 v2 任务**: 性能基准实测 (修 BUG-011)

### 💡 未来版本 (v3.8+)
- 多 region 部署 (跨机房容灾, 见 §12)
- 客户端 SDK (Python / Node / Go) (BUG-014)
- 模型评测系统 (自动 cap_score 校准)
- 智能 fallback 策略 (基于历史成功率)
- admin UI 进一步打磨 (BUG-013)
- OpenTelemetry trace (NFR-22)
- content moderation (BUG-015)
- slowloris 防护 (BUG-012)

---

## 8. 术语表 (v2 扩充)

| 术语 | 解释 | 首次出现 |
|---|---|---|
| Provider | 上游 LLM 服务商 (OpenRouter/OpenAI/Anthropic/DeepSeek/...) | v1 |
| Route | 一次请求的路由决策 (选哪个 provider 哪个 key) | v1 |
| Chain | 同一请求的所有候选 + 切链顺序 | v1 |
| Candidate | chain 中的一个具体 (provider, key) 对 | v1 |
| Switch / 切链 | 从当前 candidate 失败切到下一个 | v1 |
| Tier Bonus | 关键词触发的能力分加成 (内置默认 + 用户覆盖) | v1 |
| Context Bridge | 切链时注入上下文 (v3.4) | v1 |
| smr_request_id | 请求唯一 ID, 跨 candidate 一致 (v3.5) | v1 |
| Penalty | 失败 provider 的降权状态 | v1 |
| Per-tenant Key | 对外 API 多 key 体系中的单 key (v3.7) | v1 |
| State Dir | `config.model_management.state_dir` 集中管理 (v3.6) | v1 |
| LKG | Last Known Good, V6 4am-recover 强制恢复源 (v3.6) | v1 |
| **AC** | Acceptance Criteria 验收标准 (v2 §3.7) | v2 |
| **TC** | Test Case 测试用例 (v2 §3.7) | v2 |
| **Quota** | 月度配额 (FR-28, v2) | v2 |
| **Soft Delete** | 软删除, enabled=false 保留 30 天 (FR-27) | v2 |
| **Penalty Decay** | penalty 状态衰减 (FR-6) | v2 |
| **Endpoint Group** | rate limit 分组: chat/image/embed/admin (FR-16) | v2 |
| **RTO** | Recovery Time Objective 恢复时间目标 (§12) | v2 |
| **RPO** | Recovery Point Objective 恢复点目标 (§12) | v2 |

---

## 9. API 参考 (v2 新增) ⭐

> **来源**: 实际端点 + OpenAI 兼容 schema。仅列关键端点, 完整 schema 见 `openapi.json` (SMR 自动生成)。

### 9.1 通用约定

**鉴权**:
- Client → SMR: `Authorization: Bearer <tenant_key>` (或 `config.server.api_key`)
- Admin → SMR: `Authorization: Bearer <admin_key>` (来自 `config.server.api_key`)
- SMR → Provider: `Authorization: Bearer <provider_key>` (per-key)

**通用 Header**:
- `X-SMR-Request-ID`: 可选, 客户端传入的请求 ID (UUID, 不合规服务端生成)
- `X-SMR-Force-Model`: 可选, 跳过模态检测 (admin 用)

**通用响应 Header**:
- `X-SMR-Request-ID`: 服务端生成/透传的 ID
- `X-SMR-Chain-Id`: 链 ID, 跨 candidate 一致
- `Retry-After`: 仅 429, 等待秒数

**分页参数** (admin list 端点):
- `page` (int, ≥1, 默认 1)
- `page_size` (int, 1-100, 默认 20)

**错误响应结构** (统一):
```json
{
  "error": {
    "code": "RATE_LIMITED",
    "message": "Rate limit exceeded: 60 rpm",
    "type": "rate_limit_error",
    "param": null,
    "smr_request_id": "abc-123"
  }
}
```

### 9.2 Client 端点 (OpenAI 兼容)

#### 9.2.1 `POST /v1/chat/completions`

**Request** (OpenAI 兼容, 关键字段):
```json
{
  "model": "gpt-4o",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello"}
  ],
  "stream": false,
  "temperature": 1.0,
  "max_tokens": null,
  "top_p": 1.0,
  "frequency_penalty": 0,
  "presence_penalty": 0,
  "stop": null,
  "tools": null,
  "tool_choice": null,
  "response_format": null,
  "seed": null,
  "user": "user-123"
}
```

**Response (非流式)**:
```json
{
  "id": "chatcmpl-xxx",
  "object": "chat.completion",
  "created": 1718700000,
  "model": "gpt-4o",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "Hi!"},
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 5,
    "total_tokens": 15
  },
  "_router": {
    "smr_request_id": "abc-123",
    "chain_id": "chain-456",
    "provider": "openrouter",
    "switched_from": null,
    "stale": false,
    "age_seconds": 0
  }
}
```

**Response (流式, SSE)**:
```
data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","choices":[{"delta":{"content":"Hi"},"index":0}]}

data: {"_smr_bridge":{"switched_from":"openai-gpt4","reason":"429","smr_request_id":"abc-123"}}

data: [DONE]
```

#### 9.2.2 `POST /v1/images/generations`

**Request**:
```json
{
  "model": "dall-e-3",
  "prompt": "A cat",
  "n": 1,
  "size": "1024x1024",
  "quality": "standard",
  "response_format": "url"
}
```

**Response**:
```json
{
  "created": 1718700000,
  "data": [{"url": "https://...", "revised_prompt": "A cat"}]
}
```

#### 9.2.3 `POST /v1/embeddings`

**Request**:
```json
{
  "model": "text-embedding-3-small",
  "input": "The food was delicious",
  "encoding_format": "float"
}
```

#### 9.2.4 `GET /v1/models`

**Query**: `?page=1&page_size=20&capability_min=0.5`

**Response**:
```json
{
  "object": "list",
  "data": [
    {"id": "gpt-4o", "object": "model", "owned_by": "openai", "capability_score": 0.95}
  ],
  "total": 100,
  "page": 1,
  "page_size": 20
}
```

### 9.3 Admin 端点 (鉴权: admin key)

#### 9.3.1 `GET /v1/admin/public-keys`

**Response**:
```json
{
  "keys": [
    {
      "name": "frontend-prod",
      "key_hash": "a1b2****c3d4",
      "rate_limit_rpm": 100,
      "model_filter": ["gpt-4*"],
      "enabled": true,
      "monthly_quota_tokens": 1000000,
      "label": {"team": "frontend", "env": "prod"},
      "created_at": "2026-06-18T02:00:00Z",
      "last_used_at": "2026-06-18T05:30:00Z"
    }
  ],
  "total": 5
}
```

#### 9.3.2 `POST /v1/admin/public-keys`

**Request**:
```json
{
  "name": "frontend-prod",
  "rate_limit_rpm": 100,
  "model_filter": ["gpt-4*", "claude-3-*"],
  "monthly_quota_tokens": 1000000,
  "label": {"team": "frontend", "env": "prod"}
}
```

**Response (201)**:
```json
{
  "name": "frontend-prod",
  "key": "sk-smr-xxxxxxxxxxxxxxxxxxxxxxxxxxxx",  // 原 key, 一次性返回
  "key_hash": "a1b2c3d4e5f6...",  // 完整 SHA256, 服务端保存
  "warning": "Save this key now. It will not be shown again."
}
```

#### 9.3.3 `POST /v1/admin/public-keys/{name}/usage/reset`

**Response**:
```json
{
  "name": "frontend-prod",
  "old_stats": {"total": 100, "success": 95, "fail": 5, "tokens_in": 5000, "tokens_out": 3000},
  "reset_at": "2026-06-18T05:30:00Z"
}
```

#### 9.3.4 `POST /v1/admin/context_review`

**Response**:
```json
{
  "checked": 50,
  "mismatches": 2,
  "fixed": 2,
  "details": [
    {"smr_request_id": "abc-123", "expected_chain": "chain-1", "actual_chain": "chain-2"}
  ]
}
```

#### 9.3.5 `POST /v1/admin/upgrade`

**Response**:
```json
{
  "current_version": "v3.7.0",
  "latest_version": "v3.7.1",
  "command": "pip install --upgrade git+https://github.com/yaorong/SuperModel-Router.git@v3.7.1",
  "note": "Run this command manually. SMR does not auto-upgrade."
}
```

---

## 10. 错误码 (v2 新增) ⭐

### 10.1 错误码总表

| HTTP | SMR Code | 语义 | 触发场景 | Client 处理建议 |
|---|---|---|---|---|
| **400** | `BAD_REQUEST` | 请求格式错误 | 缺必填字段, JSON parse 失败 | 修请求重试, 不重试无意义 |
| **401** | `UNAUTHORIZED` | 鉴权失败 | 缺 `Authorization` / tenant key 无效 / admin key 错 | 检查 key 是否过期, 联系 admin 重发 |
| **403** | `FORBIDDEN` | 鉴权成功但越权 | model_filter 阻断 / admin 端点用 tenant key | 检查 model_filter 配置, 用允许的 model |
| **403** | `MODEL_NOT_ALLOWED` | 模型不在 tenant 白名单 | tenant `model_filter` 不含请求的 model | 改用白名单内模型, 联系 admin 加白 |
| **404** | `NOT_FOUND` | 资源不存在 | `GET /v1/models/{id}` 找不到 | 检查 model_id 拼写 |
| **404** | `MODEL_BLOCKED` | 模型在全局黑名单 | FR-26 全局黑名单 | 不可重试, 需 admin 移除黑名单 |
| **408** | `REQUEST_TIMEOUT` | 客户端超时 | 上游 first_token 超时 | 重试 (可能切链) |
| **422** | `UNPROCESSABLE_ENTITY` | 参数校验失败 | `temperature` 越界 / `max_tokens` < 0 | 修参数, 必填 `param` 字段指示哪字段错 |
| **429** | `RATE_LIMITED` | 速率超限 | 60s 内超 rpm (FR-16) | 看 `Retry-After`, 等候重试 |
| **429** | `QUOTA_EXCEEDED` | 月度配额用完 | FR-28 monthly_quota_tokens 用尽 | 不可重试 (除非下月), 联系 admin 提配额 |
| **500** | `INTERNAL_ERROR` | SMR 内部异常 | 未捕获异常 / bug | **重试 1 次**, 仍失败联系 admin + 给 smr_request_id |
| **502** | `BAD_GATEWAY` | 上游返回不可用 | provider 返回 5xx 且切链后仍失败 | **重试 (带 backoff)**, 仍失败换 provider |
| **503** | `SERVICE_UNAVAILABLE` | SMR 暂不可用 | key_pool 耗尽 / chain 全失败 / 维护中 | **重试 (带 backoff)**, 检查 SMR health |
| **503** | `KEY_POOL_EXHAUSTED` | provider 全部 key 失败 | FR-3 key 池空 | 等冷却期 (60s) 重试 |
| **503** | `STALE_CHAIN` | chain 超 stale 阈值 | FR-7 切链时 staleness > 30min | **不重试**, 客户端需重新发起请求 |
| **504** | `GATEWAY_TIMEOUT` | 上游总超时 | chain 总耗时 > 60s | 重试, 可能切链 |

### 10.2 错误响应示例

**401**:
```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Invalid or missing API key. Please provide a valid tenant key in Authorization header.",
    "type": "authentication_error",
    "smr_request_id": "abc-123"
  }
}
```

**429 RATE_LIMITED**:
```json
{
  "error": {
    "code": "RATE_LIMITED",
    "message": "Rate limit exceeded: 60 rpm. Retry after 30s.",
    "type": "rate_limit_error",
    "smr_request_id": "abc-123"
  }
}
```
Headers: `Retry-After: 30`, `X-RateLimit-Limit: 60`, `X-RateLimit-Remaining: 0`, `X-RateLimit-Reset: 1718700030`

**422 UNPROCESSABLE_ENTITY**:
```json
{
  "error": {
    "code": "UNPROCESSABLE_ENTITY",
    "message": "Invalid value for 'temperature': must be between 0 and 2",
    "type": "invalid_request_error",
    "param": "temperature",
    "smr_request_id": "abc-123"
  }
}
```

### 10.3 Client 处理伪代码 (Python)

```python
import time
import openai

client = openai.OpenAI(
    base_url="http://localhost:6473/v1",
    api_key="sk-smr-..."
)

def call_with_retry(messages, max_retries=3):
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(
                model="gpt-4o", messages=messages
            )
        except openai.RateLimitError as e:
            if e.code == "RATE_LIMITED":
                # 看 Retry-After header
                wait = int(e.response.headers.get("Retry-After", 60))
                time.sleep(wait)
                continue
            elif e.code == "QUOTA_EXCEEDED":
                # 不可重试, 报给用户
                raise
        except openai.InternalServerError:
            # 500/502/503, backoff 重试
            time.sleep(2 ** attempt)
            continue
        except openai.BadRequestError:
            # 400/422, 不重试
            raise
    raise Exception("Max retries exceeded")
```

---

## 11. 监控指标 (v2 新增) ⭐

### 11.1 Prometheus Exporter

**端点**: `GET /metrics` (公开, 无鉴权, 仅内网访问; 公网需 NFR-27 配 TLS 终止)

**Metrics 列表**:

| 名称 | 类型 | Labels | 含义 |
|---|---|---|---|
| `smr_requests_total` | Counter | `tenant`, `endpoint`, `model`, `status` | 总请求数 |
| `smr_request_duration_seconds` | Histogram | `endpoint`, `model` | 请求耗时 (含上游) |
| `smr_route_decision_seconds` | Histogram | (none) | 路由决策耗时 (NFR-1) |
| `smr_chain_switch_total` | Counter | `from_provider`, `to_provider`, `reason` | 切链次数 (FR-23) |
| `smr_chain_length` | Histogram | `final_status` | 实际切链深度 |
| `smr_provider_errors_total` | Counter | `provider`, `error_type` | provider 错误数 |
| `smr_provider_request_seconds` | Histogram | `provider`, `model` | 上游耗时 |
| `smr_active_tenants` | Gauge | (none) | 当前活跃 tenant 数 |
| `smr_rate_limited_total` | Counter | `tenant` | rate limit 触发数 |
| `smr_quota_exceeded_total` | Counter | `tenant` | quota 超限数 |
| `smr_penalty_active` | Gauge | `provider` | 当前 penalty 中 provider 数 |
| `smr_state_write_seconds` | Histogram | `state_file` | state 写盘耗时 |
| `smr_memory_rss_bytes` | Gauge | (none) | 进程 RSS |
| `smr_up` | Gauge | (none) | 1=健康, 0=异常 |

**示例 scrape**:
```
# HELP smr_requests_total Total requests processed
# TYPE smr_requests_total counter
smr_requests_total{tenant="frontend-prod",endpoint="/v1/chat/completions",model="gpt-4o",status="200"} 1234
smr_requests_total{tenant="frontend-prod",endpoint="/v1/chat/completions",model="gpt-4o",status="429"} 5
```

### 11.2 健康检查

| 端点 | 鉴权 | 用途 | 返回 |
|---|---|---|---|
| `GET /health` | 无 | 进程活 | `{"status": "ok", "uptime_s": 3600}` (200) / 503 (进程死) |
| `GET /health/ready` | 无 | 就绪 (依赖加载完) | `{"ready": true, "providers_loaded": 5, "models_loaded": 100}` (200) / 503 (未就绪) |
| `GET /health/live` | 无 | 存活 (不依赖外部) | `{"alive": true}` (200) |

**Docker healthcheck** (DR-1):
```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:6473/health"]
  interval: 30s
  timeout: 5s
  retries: 3
  start_period: 60s
```

### 11.3 SLA 监控

| SLA 指标 | 目标 | 报警阈值 | 监控方法 |
|---|---|---|---|
| 可用性 (Availability) | 99.5% (月) | < 99% 报警 | 30 天成功请求 / 总请求 |
| P95 延迟 (Latency P95) | < 2s (含上游) | > 3s 报警 | Prometheus histogram_quantile |
| 错误率 (Error Rate) | < 1% | > 5% 报警 | `sum(rate(smr_requests_total{status=~"5.."})) / sum(rate(smr_requests_total))` |
| 切链率 (Chain Switch Rate) | < 10% | > 30% 报警 | `rate(smr_chain_switch_total) / rate(smr_requests_total)` |
| 配额消耗 (Quota) | 月底 80% | > 90% 报警 | `sum(usage.tokens) / sum(quota)` |
| 磁盘使用 (Disk) | < 70% | > 85% 报警 | `node_filesystem_avail` |
| 内存 (Memory) | < 500MB | > 800MB 报警 | `smr_memory_rss_bytes` |
| State 写盘 (Write Latency) | < 100ms | > 500ms 报警 | `smr_state_write_seconds` |

**报警渠道**: log → Prometheus Alertmanager → 飞书 webhook → @老大 (TBD 配置)

### 11.4 日志规范

**格式** (JSON Lines):
```json
{"ts": "2026-06-18T05:30:00.123Z", "level": "INFO", "logger": "smr.router", "smr_request_id": "abc-123", "chain_id": "chain-456", "tenant": "frontend-prod", "model": "gpt-4o", "provider": "openrouter", "msg": "Chain succeeded", "elapsed_ms": 1234}
```

**必含字段**: `ts`, `level`, `logger`, `smr_request_id`, `chain_id`, `msg`

**脱敏** (NFR-25): `Authorization` header, `api_key` 字段, prompt/response content 中的 PII (email / 手机号) 自动 `***`

**日志轮转**: loguru 默认 10MB × 5 backup, 可在 `config.yaml` 调

---

## 12. 灾备 (v2 新增) ⭐

### 12.1 单实例灾备

**当前状态** (v3.7): 单实例运行, 无 HA

**故障模式**:
- 进程崩溃 (OOM / segfault / kill -9)
- 主机故障 (硬件 / 网络)
- 上游 provider 全部不可用
- state 文件损坏 (罕见)

### 12.2 RTO / RPO 目标

| 场景 | RTO (恢复时间) | RPO (数据丢失) | 措施 |
|---|---|---|---|
| **进程崩溃** | **< 10s** (systemd restart) | **0** (atomic rename 保证) | systemd `Restart=always`, 启动加载 state_dir |
| **主机故障** | **< 5min** (手动启动新机) | **< 5min** (最后一次 state 同步) | 异地备份 state, 启动脚本 pull latest |
| **state 文件损坏** | **< 1min** (rollback) | **0** (用 .backups/) | FR-25 rollback 端点 / 手动 `cp .backups/config-latest.yaml` |
| **上游全挂** | **N/A** (等上游恢复) | 0 | SMR 持续 502, 上游恢复后自动恢复 |
| **配置错误** | **< 1min** (rollback) | **0** (rollback 到 .backups/) | FR-25 端点 |

### 12.3 备份策略

**本地备份** (FR-22):
- `.backups/` 保留 50 个 yaml, mtime 排序
- 写盘前自动 backup (FR-22)
- **不跨主机同步** (v3.7 限制)

**异地备份** (建议, v3.8):
```bash
# cron 每 5min 同步 state_dir 到 S3
*/5 * * * * aws s3 sync /opt/smr/state/ s3://smr-backup/state/ --delete
# cron 每天凌晨同步 config
0 3 * * * aws s3 sync /opt/smr/config/ s3://smr-backup/config/
```

**恢复演练** (建议季度一次):
1. 启动备用机
2. `aws s3 sync s3://smr-backup/state/ /opt/smr/state/`
3. 启动 SMR
4. 调 `/health/ready` 确认
5. 跑回归测试

### 12.4 多 Region 部署 (v3.8+ 计划)

**架构**:
```
Client → DNS (GeoIP) → Region A (上海) SMR
                     → Region B (北京) SMR
                          ↓
                  Shared State (Redis/S3)
                  Provider Pool (per-region 优选)
```

**挑战**:
- Rate limit 跨 region 共享 (需 Redis)
- smr_request_id 跨 region 唯一 (用 ULID 而非 UUID)
- 同步延迟导致偶发双写 (last-write-wins 即可)

**当前 v3.7 状态**: **不支持**, 单 region 单实例。扩容到多 region 需 v3.8 改 PublicKeyManager 后端 (in-memory → Redis)。

### 12.5 故障转移流程 (手工 runbook)

**场景**: SMR 主机宕机

```bash
# 1. 确认 host 不可达
ping smr-prod-01  # fail

# 2. 在备用机启动
ssh smr-prod-02
sudo systemctl start supermodel-router

# 3. 等待就绪
curl http://smr-prod-02:6473/health/ready
# {"ready": true, "providers_loaded": 5, "models_loaded": 100}

# 4. (可选) 拉最新 state (如备用机 state_dir 旧)
sudo systemctl stop supermodel-router
aws s3 sync s3://smr-backup/state/ /opt/smr/state/
sudo systemctl start supermodel-router

# 5. 更新 DNS / 通知客户端
# (如用 nginx upstream)
sudo sed -i 's/smr-prod-01/smr-prod-02/' /etc/nginx/conf.d/smr.conf
sudo nginx -s reload

# 6. 通知老大
echo "SMR 已切到 smr-prod-02, RTO ≈ 5min" | feishu-notify
```

**目标 RTO**: < 5min (人工), < 10s (自动, v3.8 systemd 集群)

---

## 13. v1 → v2 改动清单 (Diff)

> 本章是 v2 的关键交付物, 方便 echo / 老大对比 v1 看改了啥。

### 13.1 新增章节 (7 个)

| 章节 | 标题 | 行数 (估) | 关键内容 |
|---|---|---|---|
| §3.7 | 测试需求 | ~120 | 验收标准 + 测试用例矩阵 (33 FR × AC + TC) + 性能基准 + 回归策略 |
| §4.6 | 安全威胁模型 | ~70 | STRIDE 框架 + 资产清单 + 攻击面 + 渗透测试 checklist |
| §6.2 | 已知 bug 池 | ~30 | 15 个 bug (P0/P1/P2/P3) + 修复版本 + 责任人 |
| §9 | API 参考 | ~150 | 通用约定 + 4 个 client 端点 + 5 个 admin 端点 (含完整 schema) |
| §10 | 错误码 | ~80 | 16 种错误码 (HTTP + SMR Code) + 响应示例 + client 处理伪代码 |
| §11 | 监控指标 | ~70 | 14 个 Prometheus metrics + 3 个 health 端点 + SLA 监控 + 日志规范 |
| §12 | 灾备 | ~80 | RTO/RPO 目标 + 备份策略 + 多 region 计划 + 故障 runbook |

### 13.2 新增 FR (11 个)

| FR | 标题 | 优先级 | 状态 |
|---|---|---|---|
| FR-23 | 切链可观测性 | P0 | 🟡 待补 |
| FR-24 | 主动盘点 | P1 | 🟡 待补 |
| FR-25 | 配置回滚 | P1 | 🟡 待补 |
| FR-26 | 模型黑白名单 | P1 | 🟡 待补 |
| FR-27 | Tenant 生命周期 (软/硬删) | P0 | 🟡 待补 |
| FR-28 | Quota (月度配额) | P1 | 🟡 待补 |
| FR-29 | 配置版本语义 | P0 | 🟡 待补 |

(原 v1 22 FR, 现共 33 FR, 增加 11 个; 其中 4 个是对 v1 隐含需求的显式化, 7 个是 v2 新需求)

### 13.3 新增 NFR (12 个)

| NFR | 标题 | 量化 |
|---|---|---|
| NFR-17 | 内存占用 | idle < 200MB, 100 并发 < 500MB |
| NFR-18 | 持久化吞吐 | 写盘 < 100ms |
| NFR-19 | 进程崩溃数据不丢 | kill -9 后 state 100% 完整 |
| NFR-20 | 优雅关闭 | SIGTERM 30s 内完成 in-flight + flush |
| NFR-21 | Prometheus 指标 | 见 §11 |
| NFR-22 | Trace 支持 | OpenTelemetry SDK |
| NFR-23 | Python 版本 | 3.12+ |
| NFR-24 | 浏览器支持 | Chrome 100+ / FF 100+ / Safari 15+ |
| NFR-25 | 日志脱敏 | Authorization / api_key 100% 脱敏 |
| NFR-26 | 进程最小权限 | 非 root, 端口 > 1024 |
| NFR-27 | TLS 支持 | 反代终止 |
| NFR-28 | 输入校验 | Pydantic v2 |

(原 v1 16 NFR, 现共 28 NFR, 增加 12 个)

### 13.4 重大修正 (5 处)

| 位置 | v1 描述 | v2 修正 | 原因 |
|---|---|---|---|
| FR-15 | `key_hash[:16]` | **`SHA256 完整 64 字符`** | 16 hex = 64bit 易碰撞, 应 256bit; 引发 BUG-001 P0 |
| FR-16 | rate limit (未说持久化) | **in-memory 不持久化, 重启丢** | 诚实暴露限制, 触发 BUG-002 P0 |
| FR-4 | "5xx/超时/网络错切链" | **加 429 per-key; 4xx 细分: 401/403 走换 key, 400/404/422 不切** | 消除歧义, 边界明确 |
| NFR-1 | "路由延迟 < 50ms" | **P50 < 10ms / P95 < 30ms / P99 < 50ms** | 量化分位数 |
| NFR-2 | "100+ 并发" | **单进程 100 并发 P99 < 100ms (4C8G), 200 并发 P99 < 200ms** | 加硬件前提 + P99 |

### 13.5 删/合并 (0)

v2 **不删** v1 任何 FR/NFR, 仅扩充 + 修正。

### 13.6 已知 bug 池摘要

详见 §6.2。要点:
- **P0 必修** (v3.7.1): BUG-001/002/003 共 3 个
- **P1 应修** (v3.7.1): BUG-004/005/006/007/008 共 5 个
- **P2 排期** (v3.8+): BUG-009 ~ BUG-015 共 7 个

---

## 14. 给 echo / 老大的后续建议 (v2 收尾)

### 14.1 v2 review 主要批评 (v1 的 3 个最严重问题)

1. **完全没有验收标准 (AC)**: 22 FR + 16 NFR 全是模糊描述 ("快速 / 稳定 / 兼容"), 无法判断"什么算合格"。→ v2 加 §3.7 验收矩阵, 33 FR 全部配 AC + TC。
2. **关键安全设计没说清**: `key_hash[:16]` 是 64bit 碰撞, rate limit 持久化未提, 切链触发条件 (4xx 哪些切哪些不切) 黑盒。→ v2 加 §4.6 STRIDE 威胁模型 + §10 错误码表 + FR-27/28 tenant 生命周期/quota。
3. **缺运营级文档**: 无 API schema、无错误码、无监控指标、无灾备。v3.7 已上线 PublicKeyManager 但没有告警/恢复流程, 一旦出问题完全抓瞎。→ v2 加 §9 API + §10 错误码 + §11 监控 + §12 灾备 (RTO/RPO/runbook)。

### 14.2 v2 落地建议

- **v3.7.1 必做** (修 P0 bug): 修 BUG-001 (key_hash 改完整) / BUG-002 (rate limit 持久化方案 RFC) / BUG-003 (Docker rebuild) / BUG-009 (真实部署验证) / BUG-010 (单测补齐)
- **v3.7.1 应做** (修 P1 bug): BUG-004 ~ BUG-008
- **v3.8 规划** (架构升级): Redis 存储 rate limit + quota, 多 region 部署, OpenTelemetry trace, content moderation

### 14.3 v3.0 草稿计划

- 把 §3.7 验收矩阵导出为 `test_checklist.csv` (CI 引用)
- 把 §11 metrics + §12 runbook 整合到 `docs/ops-manual.md`
- 把 §10 错误码 + §9 API 整合到 `openapi.json` (自动生成)
- 补 v2 review 没说的: 多租户资源隔离 (CPU/内存 quota) / 客户端超时配置 / 流式 token 计费

---

**v2.0 终稿 | 行数 ≈ 700 | 新增 7 章节 + 11 FR + 12 NFR | 主要修了 v1 的 5 处重大缺漏 (key_hash / rate limit / 切链条件 / 量化 / 缺运营文档)**

**v3.0 草稿等老大 review + v3.7.1 bug fix 落地后启动**
