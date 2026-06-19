# Changelog — SMR (supermodel_router)

> All notable changes to this project are documented here.

---

## v3.10.0 (2026-06-19)

**模型分组策略 + 筛选向导 + 向导持久化**

### 🚀 New
- **路由分组策略 v5**: 4 种 group-level 轮询策略 (round-robin-group / flat / group-failover / group-weighted)
- **分组策略持久化**: `/v1/admin/routing` PUT 可读写 `group_strategy` / `group_weights`
- **筛选向导持久化**: wizard 一键生成分组时, 选择的 strategy 写入 config.yaml
- **wizard UI dropdown**: 分组向导增加策略选择器 (默认 round-robin-group)

### 🔧 Improved
- 优先级: model-level routing 先选候选 → group-level 决定 group 内顺序
- 4 轮询策略完全并存, 兼容 v3.9.0 model-level 字段

---

## v3.9.0 (2026-06-18)

**模型分组向导 + 多 key 轮询完善**

### 🚀 New
- **模型分组向导器**: 13 preset + 5 维自定义筛选 + 批量勾选 + 策略 dropdown
- 多 key 真正轮询 (B1): `_fetch_models` 尝试所有 key, 401/403 自动跳

---

## v3.4.0 (2026-06-17)

**上下文桥接 + 过期标记**

### 🚀 New
- **上下文桥接**: chain rotation 切换时, 新模型看到前模型的对话摘要 + 过期标记
- `/v1/admin/version` (当前 + GitHub release 检查)
- `/v1/admin/upgrade` (git/pip/docker/binary 升级命令生成)

---

## v3.3.0 (2026-06-17)

**PyInstaller + Docker 一键部署**

### 🚀 New
- **PyInstaller 单文件 21MB** 二进制
- **Docker 镜像** docker-compose.yml 一键启动
- `admin_ui.py` 3070 行, `/admin` dashboard

---

## v3.2.0 (2026-06-17)

**错误处理 + 状态持久化**

### 🚀 New
- penalty state 持久化 (model_penalty / last_failure 跨重启保留)
- 5xx 错误消息干净: JSON error parse + regex fallback (B2 修复)
- exclude 正则规则: `model_rules.exclude` 支持正则 (B4 修复)

---

## v3.1.0 (2026-06-17)

**轮询机制 v4**

### 🚀 New
- 高分优先 + key 轮询 + 跨 provider + 降分 + 周期复测
- max_retry 跨 candidate 链重试 (B3 修复)
- penalty admin endpoints

---

## v3.0.0 (2026-06-16)

**模态路由 + 质量评分**

### 🚀 New
- `capability_score` + EWMA latency 质量评分
- 模态感知路由 (chat / images / audio)

---

## v1.0.0 (SMR 阶段 1-2)

### 🚀 New
- OpenAI 兼容网关
- 4 模式过滤 (pattern / include / exclude / all)
- 多 key 轮询 + 401 自动换 key
- 流式 chat 支持

---

## v0.x (FMR 阶段)

> 内部代号 free-model-router, 已废弃

---
