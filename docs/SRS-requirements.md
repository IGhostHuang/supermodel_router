# SuperModel Router (SMR) — 需求规格说明书 (SRS)

> **v1.0 草稿** | 整理自老大 (黄耀荣) 历史对话 + commit log + 实战反馈
> 2026-06-18 起草 | 待小星雲 review

---

## 1. 项目背景

老大 (黄耀荣) 维护的 **AI 模型聚合网关**, 把多个上游 provider (OpenRouter / OpenAI / Anthropic / DeepSeek / 自定义) 整合成单一 OpenAI 兼容端点 + 自动路由 + 多 key 轮询 + 容灾切链。

**核心价值**: 让客户端用 1 个 API key 访问 100+ 模型, 路由器自动选最优 + 失败自动切下游 + per-key 计费。

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

---

## 3. 功能需求 (从历史对话整理)

### 3.1 核心路由 (P0)

**FR-1**: OpenAI 兼容端点
- `POST /v1/chat/completions` (含 stream + chain rotation + context bridge)
- `POST /v1/images/generations` / `/v1/images/edits`
- `POST /v1/embeddings`
- `GET /v1/models` / `GET /v1/models/{model_id}`

**FR-2**: 模态感知路由 (v3.0)
- 自动检测输入/输出模态 (text/image/audio/video)
- 输入 multimodal 自动选 multimodal 模型
- 输出 modality mismatch 自动降级或拒答

**FR-3**: 多 key 轮询 (v3.1)
- per-provider 多 key, 401/403 触发换 key 而非换 model
- 并发槽数 (max_concurrent) 控制
- key 指纹脱敏显示

**FR-4**: 失败切链 (v3.1 → v3.5 加强)
- 5xx/超时/网络错误 → 切下一个候选
- stream 模式切链显式 `aclose()` 上游 (v3.5 race condition 防御)
- max_retry 默认 2, retry_backoff_ms 默认 [0, 500]

### 3.2 路由策略 (P0)

**FR-5**: Capability 评分 (v3.1)
- tier_bonus (内置默认 + 用户覆盖)
- custom_keywords (用户累加)
- modality_base_score (用户覆盖)
- 综合 capability_score 排序候选

**FR-6**: Penalty 状态 (v3.2)
- 失败 provider 在 penalty 期内降权
- persistence 到 `penalty_state.json` (重启不丢)
- decay 接口手动触发

**FR-7**: Context Bridge (v3.4)
- 切链时注入 system message 同步上下文
- 流式: SSE `data: {"_smr_bridge": {...}}` 标记
- 非流式: `response._router.switched_from + stale + age_seconds`
- stale 阈值默认 30min

**FR-8**: smr_request_id 透传 (v3.5)
- 每个请求生成/透传唯一 ID
- chain_id 跨 candidate 一致
- 错配检测 → mainbot 收到丢弃

### 3.3 管理后台 (P1)

**FR-9**: Admin Dashboard (`/admin`)
- 左侧导航: 仪表盘 / 服务商管理 / 模型列表 / API 密钥 / 用量统计 / 分类器 / 服务配置 / 配置历史 / 版本 / 对外 API (v3.7)
- 服务商 CRUD + 启停 + 复制 + 导入导出
- 模型分页浏览 (按能力分排序)
- API key 独立管理 (指纹脱敏)

**FR-10**: Classifier 配置
- tier_bonus 编辑 (内置默认 + 用户覆盖)
- custom_keywords 自定义累加
- modality_base_score 用户覆盖

**FR-11**: Server & Routing 配置
- 监听端口 / host / api_key
- max_retry / retry_backoff_ms / first_token_timeout_ms
- quality_weights

**FR-12**: 版本管理 (v3.3)
- 当前版本 / 构建日期
- GitHub release 自动检查 (1h 缓存)
- /v1/admin/upgrade 端点 (生成升级命令, 不直接执行)

### 3.4 模型管理 (v3.3, P1)

**FR-13**: 自动 Discovery
- 启动时拉 provider `/v1/models` 拉清单
- 增量更新 + 删除检测
- 通知机制 (新模型/下线)

**FR-14**: Model Rules
- 包含 / 不包含正则匹配
- 自动规则生成 (基于能力分)

### 3.5 对外 API (v3.7, P0) 🆕

**FR-15**: 多 key 体系
- per-tenant API key (name / key_hash[:16] / rate_limit_rpm / model_filter / enabled)
- 客户端调用: `Authorization: Bearer smr-pub-{token}`
- 创建时返回原 key 一次性, 之后只存 SHA256[:16] 哈希

**FR-16**: Rate Limiting
- sliding window 60s 计数
- rpm 默认 60, 0 = 不限
- 超限返回 429

**FR-17**: Model Whitelist
- 空 = 全部允许
- 否则只允许列表内 (支持 `gpt-4*` 通配)
- 不在白名单返回 403

**FR-18**: 用量追踪
- per-key: total / success / fail / tokens / last_used
- `/v1/admin/public-keys/usage` 全局汇总
- `/v1/admin/public-keys/{name}/reset` 重置计数

**FR-19**: 持久化
- `state/public_keys_state.json` (debounce 5s + atomic rename)
- 配置变更自动备份 (.backups/, 保留 50 个)

### 3.6 配置持久化 (v3.6, P0)

**FR-20**: 5 类持久化数据
1. `config.yaml` (主配置, model_management.state_dir 集中管理)
2. `state/model_rules_state.json` (规则 + 历史 65 条)
3. `state/penalty_state.json` (provider 降权状态)
4. `state/public_keys_state.json` (对外 API key, v3.7)
5. `state/engine_stats.json` (per-provider stats)

**FR-21**: 迁移脚本
- `migrate_state.py` 启动时自动跑 (CWD → state_dir)
- 兼容老配置文件位置

**FR-22**: 备份机制
- `_backup()` 写盘前自动备份到 `.backups/config-YYYYMMDD-HHMMSS.yaml`
- 保留 50 个, 超 mtime 删最旧
- tier bonus 改前自动备份 (v3.7)

---

## 4. 非功能需求

### 4.1 性能

**NFR-1**: 路由延迟 < 50ms (不含上游调用)
**NFR-2**: 单进程支持 100+ 并发请求
**NFR-3**: 模型 registry 启动加载 < 30s

### 4.2 可靠性

**NFR-4**: 切链不丢请求 (v3.5 race condition 防御)
**NFR-5**: 关键操作 (config save / provider delete / tier bonus) 自动备份
**NFR-6**: 配置文件损坏可回滚 (`.backups/` 保留 50 个)

### 4.3 可观测性

**NFR-7**: 结构化日志 (`logging.getLogger("xxx")`)
**NFR-8**: 用量统计可导出 (per-key + per-provider)
**NFR-9**: 主动盘点 (`POST /v1/admin/context_review`)

### 4.4 兼容性

**NFR-10**: OpenAI API 100% 兼容 (客户端零修改接入)
**NFR-11**: 向后兼容 `config.server.api_key` 单 key 模式 (与 v3.7 per-tenant 共存)
**NFR-12**: WSL / Docker / pip / PyInstaller 4 种部署方式

### 4.5 安全

**NFR-13**: API key 哈希存储 (SHA256[:16], 原 key 仅创建时一次性返回)
**NFR-14**: per-key model_filter 隔离 (防止越权访问昂贵模型)
**NFR-15**: rate limit 防滥用 (per-tenant)
**NFR-16**: Config 编辑前必须 audit + backup

---

## 5. 部署需求

**DR-1**: Docker 部署 (`Dockerfile` + `docker-compose.yml`)
- python:3.12-slim + pip-cache 离线 wheels
- 端口 6473 (HTTP webhook) 或 wss 长连接
- healthcheck 30s 间隔

**DR-2**: WSL 本地开发 (`venv/bin/python3` 必须用, 不踩 system python)
**DR-3**: pip 安装 (`pip install git+https://...`)
**DR-4**: PyInstaller 二进制 (21MB ELF, dist/supermodel_router)
**DR-5**: Windows 安装包 (`.exe` + PowerShell 服务脚本)

---

## 6. 已知技术约束

| 约束 | 来源 |
|---|---|
| `chattr +i` 禁止锁 config.yaml (破坏 hermes update) | 6/10 教训 |
| 不主动循环杀进程 (warning 不影响功能就放着) | 6/6 教训 |
| 主模型不动, 加 provider/fallback 只动 providers+fallback_providers 段 | 6/16 教训 |
| 测试 ≠ 改 model.default (观察 not mutate) | 6/11 教训 |
| WSL docker DNS 限制 (容器内 GitHub fetch 404), 加 `dns: 8.8.8.8, 1.1.1.1` 修 | 6/16 实战 |
| SMR 必须用 venv python3, 不用 system python | 6/17 教训 |
| patch tool 改 config.yaml 会 REDACTED 真 key, 必须 python yaml 直改 | 6/17 教训 |
| 改 mainbot config 必同时 cp 到 lkg (避免 V6 4am 强制恢复覆盖) | 6/16 R25 |

---

## 7. 已落地 vs 待办

### ✅ v3.0-v3.7 已落地
- 上述 FR-1 ~ FR-22 全部实现
- 6 v3.7 bug 修复 (i18n/modal/version/tier bonus/对外 API/模型显示)
- 沙盒 e2e 验证 (PublicKeyManager 单测过, 完整 e2e 待跑)

### 🟡 待办 (老大明确)
1. **完整需求文档 + 小星雲 review × 2** ← 本文档
2. v3.7.0 Docker rebuild + 端到端验证 (昨晚因额度中断)
3. 真实 config.yaml 填入真 key/URL 部署验证
4. 单元测试补齐 (per-tenant key / rate limit / context bridge)

### 💡 未来版本 (v3.8+)
- 多 region 部署 (跨机房容灾)
- 客户端 SDK (Python / Node / Go)
- 模型评测系统 (自动 cap_score 校准)
- 智能 fallback 策略 (基于历史成功率)
- admin UI 进一步打磨 (老大反馈 "十分不友好!")

---

## 8. 术语表

| 术语 | 解释 |
|---|---|
| Provider | 上游 LLM 服务商 (OpenRouter/OpenAI/Anthropic/DeepSeek/...) |
| Route | 一次请求的路由决策 (选哪个 provider 哪个 key) |
| Chain | 同一请求的所有候选 + 切链顺序 |
| Candidate | chain 中的一个具体 (provider, key) 对 |
| Switch / 切链 | 从当前 candidate 失败切到下一个 |
| Tier Bonus | 关键词触发的能力分加成 (内置默认 + 用户覆盖) |
| Context Bridge | 切链时注入上下文 (v3.4) |
| smr_request_id | 请求唯一 ID, 跨 candidate 一致 (v3.5) |
| Penalty | 失败 provider 的降权状态 |
| Per-tenant Key | 对外 API 多 key 体系中的单 key (v3.7) |
| State Dir | config.model_management.state_dir 集中管理 (v3.6) |
| LKG | Last Known Good, V6 4am-recover 强制恢复源 (v3.6) |

---

**v1.0 草稿, 待小星雲 review 完善 + 迭代到 v3.0**