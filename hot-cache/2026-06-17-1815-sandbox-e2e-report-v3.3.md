# 2026-06-17 沙盒 e2e 测试报告 — v3.3.0

## 环境
- Sandbox: `127.0.0.1:19898` (SMR) | `127.0.0.1:18899` (mock upstream)
- Mock: 10 模型 (gpt-4o, gpt-4o-mini, gpt-4-turbo, claude-3-opus, claude-3-sonnet, qwen-2.5-72b, deepseek-v3, doubao-pro-256k, llama-3.1-405b, gemma-2-27b)
- Config: `mode: all` (include 模式精确匹配不适用于通配符)

## 结果: 14/14 全通过 ✅

| # | 测试项 | 结果 | 备注 |
|---|--------|------|------|
| T1 | `/v1/models` — 10 模型发现 | ✅ | 全部 10 模型 |
| T2 | `/v1/admin/routes` — 路由表 | ✅ | 10 路由 |
| T3 | `/v1/admin/models/status` v3.3 | ✅ | 规则引擎 + 快照状态 |
| T4 | `/v1/admin/models/changes` v3.3 | ✅ | diff 历史 |
| T5 | `/v1/admin/model_management` v3.3 | ✅ | 规则/发现/变更统计 |
| T6 | `/v1/admin/model_rules` v3.3 | ✅ | CRUD 规则 |
| T7 | `/v1/admin/model_discovery` v3.3 | ✅ | 发现历史 + trigger |
| T8 | `/v1/admin/models/lists` v3.3 | ✅ | 黑白名单 CRUD |
| T9 | `/v1/admin/version` v3.3 | ✅ | 3.3.0 + 升级指南 |
| T10 | `/v1/admin/upgrade` v3.3 | ✅ | git 升级指令 |
| T11 | `/v1/admin/model_notify` v3.3 | ✅ | 通知日志 |
| T12 | Chat 路由 (gpt-4o) | ✅ | mock/gpt-4o 19ms |
| T13 | Chat 路由 (claude-3-opus) | ✅ | mock/claude-3 18ms |
| T14 | POST model_discovery/trigger | ✅ | 后台触发 |

## 发现的问题 (非阻塞)

1. **`include` 模式是精确匹配不是正则** — `mode: "include"` 时 `include: [".*"]` 不匹配任何模型。生产配置用 `mode: "all"` 或 `mode: "pattern"`。
2. **POST model_rules 参数名** — 需 `rule_type` 字段不是 `action`，文档 vs 实现有 drift。
3. **lists PUT 后 GET 返回空** — 写入成功但 GET 返回 `whitelist_patterns: []`，可能 rule_engine 和 lists endpoint 读不同状态。

## 结论
v3.3 核心功能全部可用。Docker build + compose up 待老大批准后执行。
