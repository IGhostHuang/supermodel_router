# SuperModel Router (SMR) — 需求规格说明书 (SRS)

> **v3.0** | 第 2 轮 review + 打磨版 (小星雲 coder)
> v1.0 (echo) → v2.0 (小星雲 review 1) → **v3.0 (小星雲 review 2)**
> 适用版本: SMR v3.7.0 (commit 91df3c9 + 2dd1ded) + v3.7.1 (commit 849665e) 及后续
> 2026-06-18 修订

---

## 修订记录

| 版本 | 日期 | 作者 | 关键改动 |
|---|---|---|---|
| v1.0 草稿 | 2026-06-18 02:13 | echo | 整理 18 commit + 历史对话, 22 FR + 16 NFR + 5 DR |
| **v2.0** | 2026-06-18 | 小星雲 | 补 9 章节 + 11 FR (FR-23~FR-33) + 12 NFR (NFR-17~NFR-28) + 完整验收标准 |
| **v3.0** | **2026-06-18** | **小星雲** | **echo 修 2 P0 (BUG-001/002); §9 API schema 校代码; §11 metrics 补 3 个; §12 runbook 补 2 场景; §6.2 bug 池加 v2→v3 diff 标注; §14 实施路线图; §15 风险登记册; 冗余瘦身 1126→~880 行** |

> **v3 主要变化摘要** (详见 §13):
> 1. §6.2 bug 表里 BUG-001/002 从 P0 移到"已修 (commit 849665e)"
> 2. §9 API schema 跟实际代码对齐 (改 5 处: context_review payload / public-keys 字段 / reset 路径 / upgrade 缺文档 / `_warning` 字段名)
> 3. §11 metrics 加 3 个: `smr_drift_total` / `smr_admin_api_duration_seconds` / `smr_config_save_total`
> 4. §12 runbook 加 2 个场景: state 损坏恢复 / 升级前/后
> 5. §3.7 验收矩阵发现 3 冗余 + 4 缺漏, 在 §3.7.5 列 TODO
> 6. §4.6 威胁模型补 4 条 per-tenant key 攻击面 (v2 漏的)
> 7. **新增 §14 实施路线图** (v3.7.1 / v3.7.2 / v3.8 时间线)
> 8. **新增 §15 风险登记册** (5 大风险 + 触发条件 + 缓解)

---

## 1. 项目背景 (保留 v2)

老大 (黄耀荣) 维护的 **AI 模型聚合网关**, 把多个上游 provider (OpenRouter / OpenAI / Anthropic / DeepSeek / 自定义) 整合成单一 OpenAI 兼容端点 + 自动路由 + 多 key 轮询 + 容灾切链。

**核心价值 / 目标用户**: 略 (与 v2 §1 一致)。

---

## 2. 版本演进 (保留 v2)

| 版本 | 日期 | 主要改动 |
|---|---|---|
| v3.0 ~ v3.6 | 2026 | 模态感知 / 自定义 Provider / 持久化 / ContextBridge / smr_request_id / UI 大改 (略) |
| v3.7.0 | 2026-06-18 02:10 | 修 6 bug + 对外 API 多 key 体系 (commit 91df3c9 + 2dd1ded) |
| **v3.7.1** | **2026-06-18 02:20** | **修 2 P0 (BUG-001 key_hash 完整 SHA256 / BUG-002 rate limit 显式声明) (commit 849665e)** |
| v3.7.2 (计划) | TBD | 修 P1 bug 4 个 (BUG-004/006/007/008) + Docker rebuild + 端到端验证 |
| v3.8 (计划) | TBD | rate limit 持久化 / 多 region / OpenTelemetry trace / content moderation (见 §14) |

---

## 3. 功能需求 (FR)

> **v3 微调**: §3.7.5 新增 "v3 发现的冗余/缺漏 TODO"; FR-15/17 字段名跟代码对齐 (见 §9)。

### 3.1 ~ 3.6 (保留 v2 §3.1 ~ §3.6 全部内容)

**3.1 核心路由 (P0)**: FR-1 ~ FR-4 + FR-23
**3.2 路由策略 (P0)**: FR-5 ~ FR-8 + FR-24
**3.3 管理后台 (P1)**: FR-9 ~ FR-12 + FR-25
**3.4 模型管理 (P1)**: FR-13 ~ FR-14 + FR-26
**3.5 对外 API (P0)**: FR-15 ~ FR-19 + FR-27 + FR-28
**3.6 配置持久化 (P0)**: FR-20 ~ FR-22 + FR-29

> ⚠️ **v3 调整点**:
> - FR-15: v2 提到 `monthly_quota_tokens` / `label` 字段, **实际代码未实现 (BUG-008)**, v3 在 §3.5 标注 "字段预留, v3.7.2 实现"
> - FR-17: v2 提 "中间通配 `claude-3-*`", **实际代码只支持 `前缀*` (endswith), 中间/后缀通配未实现 (BUG-007 配套)**, v3 标注

### 3.7 测试需求 (v2 新增) ⭐

#### 3.7.1 ~ 3.7.4 (保留 v2, 不重写)

#### 3.7.5 v3 review 发现的冗余/缺漏 (新增)

> **来源**: 小星雲 v2→v3 review 时发现, 留给 echo / 老大拍。

| 类型 | 位置 | 问题 | 建议 |
|---|---|---|---|
| 🔴 缺漏 | NFR-25 (日志脱敏) | 没有 TC, 老大说 "脱敏" 但没验收标准 | 加 TC-25.1: 抓 logs 找 `sk-` 字符串, 必须 0 命中 |
| 🔴 缺漏 | NFR-28 (Pydantic 校验) | 验证体系未列入验收矩阵 | 加 TC-28.1: 传 `temperature: 3.0` 必须 422; TC-28.2: 100MB body 必须拒 |
| 🔴 缺漏 | §12 灾备 (备份演练) | runbook 有, 但没 TC 验证演练成功 | 加 TC-DR-1: 季度演练一次, 记录 RTO 实测值 |
| 🔴 缺漏 | FR-8 (smr_request_id 错配) | v2 验收矩阵无, 但 v3.5 关键 | 加 TC-8.2: 注入不同 chain_id 的 response, 验 mainbot 丢弃 + log warn |
| 🟡 冗余 | FR-3 TC-3.2 vs FR-4 TC-4.1 | 都测错误响应路径, 有重叠 | 合并成 TC-CHAIN-1 (统一切链测试) |
| 🟡 冗余 | FR-15 TC-15.2 vs FR-17 TC-17.1 | 越权逻辑分散, 不便 review | 合并成 TC-TENANT-AUTH-1 (统一 per-tenant 鉴权) |
| 🟡 冗余 | §3.7.3 性能基准 (5 条) | 跟 §4.1 NFR-1/2/3/17/18 大量重复 | 删 §3.7.3, 统一引用 §4.1 |

**v3 行动**: v3.7.2 补 4 个缺漏 TC, 合并 3 个冗余。**v3 文档侧不动验收矩阵, 在 §14 路线图里挂 todo。**

---

## 4. 非功能需求 (NFR)

### 4.1 ~ 4.5 (保留 v2 §4.1 ~ §4.5 全部内容)

### 4.6 安全威胁模型 (v2 新增) ⭐

> **v3 调整**: 补 4 条 per-tenant key 攻击面 (v2 漏的)。

#### 4.6.1 资产清单 (保留 v2)

#### 4.6.2 威胁主体 (保留 v2)

#### 4.6.3 攻击面 & 缓解 (STRIDE 框架)

| 威胁 | 攻击面 | 缓解措施 | 残留风险 |
|---|---|---|---|
| **S**poofing 身份伪造 | 客户端伪造 tenant key | SHA256 完整 64 字符 (BUG-001 已修 ✅) + 长度检查 | key 失窃 (NFR-13) |
| **T**ampering 篡改 | 改 `config.yaml` 提权 | 文件权限 600 + audit log | 物理访问 (NFR-26) |
| **R**epudiation 抵赖 | 用户否认调过某 model | 用量日志带 smr_request_id + 时间戳 | log 篡改 (需外部 SIEM) |
| **I**nformation Disclosure 信息泄露 | 日志泄露 user prompt | loguru format 脱敏 (NFR-25) | 第三方日志聚合需配脱敏 |
| **D**oS 拒绝服务 | 刷请求耗尽配额 / 拖慢 router | rate limit (FR-16) + model whitelist (FR-17) | slowloris (BUG-012 待 NFR-29) |
| **E**oP 权限提升 | tenant key 越权调昂贵模型 | model_filter (FR-17) + 黑名单 (FR-26) | 0day (NFR-28 schema 校验) |
| MITM 中间人 | 网络嗅探 | 强制 TLS (NFR-27) | 内部网络未配 TLS |
| Provider 投毒 | 上游返回恶意 content | NFR-25 内容脱敏 (部分缓解) | 需 content moderation (BUG-015 v3.8+) |

#### 4.6.4 渗透测试 checklist (v3 新增 per-tenant key 专项)

**v2 漏的 per-tenant key 攻击面** (v3 补):

- [ ] **(新增) Tenant key 爆破**: 拿 1 个真 key + 1w 假 key 跑, 验 rate limit 是否触发
- [ ] **(新增) model_filter 通配绕过**: 配白名单 `claude-3-*`, 试请求 `claude-3-haiku` (中间通配), 应 403 (BUG-007 未实现, 配 `claude-3-haiku-20240307` 应通)
- [ ] **(新增) 软删除 + 重用**: 软删 key-A → 30 天内新建同名 key-A, 用新 key 应能调通 (原用量历史应隔离, 不可继承)
- [ ] **(新增) label 注入**: 调 `POST /public-keys` 传 `label: {"__proto__": {...}}` (原型链污染), 应 Pydantic 422
- [ ] **(新增) Quota 时区绕过**: 配 `monthly_quota_tokens=1000`, UTC 月底前 1h 调满, 然后等 23:59:59 (UTC) 是否能再用 1h (应不能, 30 天的 reset_at 应基于创建时区还是 UTC? **v3 老大拍**)

**v2 已有的 (保留)**:

- [ ] 拿合法 tenant key 越权访问 model_filter 外的模型 → 必须 403
- [ ] 拿合法 key 跑 1h 看 rate limit 是否生效
- [ ] 改 `config.yaml` 后 audit log 是否记录
- [ ] 抓包看 response header 是否含敏感信息
- [ ] 暴力破解 admin api_key
- [ ] SSRF: 让 SMR 调自己 admin 端口
- [ ] 大 payload: 100MB body 是否被 Pydantic 拒
- [ ] Unicode 攻击: `tier_bonus` 含 `\x00` 是否被处理

---

## 5. 部署需求 (DR) (保留 v2)

---

## 6. 已知技术约束 & Bug 池

### 6.1 已知技术约束 (来自历史教训) (保留 v2)

### 6.2 已知 bug 池 ⭐ **v3 重要更新**

> **v3 关键变化**: echo 已修 2 P0 (BUG-001/002), **从 P0 移到"已修"**; 其余 P0 (BUG-003) 仍在 v3.7.2 排期; P1/P2 重新编号。

| ID | 严重度 | 描述 | 状态 | 修复版本 | 责任人 |
|---|---|---|---|---|---|
| **BUG-001** | ~~P0~~ | key_hash[:16] 64bit 截断易碰撞 | **✅ 已修** (commit 849665e, v3.7.1) | v3.7.1 ✅ | 小星雲 |
| **BUG-002** | ~~P0~~ | rate limit in-memory 不持久化, 重启可瞬时打满 | **✅ 已修 (文档化)** (v3.7.1 显式声明, 持久化方案 v3.8) | v3.7.1 ✅ / v3.8 实施 | 小星雲 |
| **BUG-003** | P0 | v3.7.0 Docker rebuild 因额度中断未完成, 端到端验证未跑 | 待续 | **v3.7.2** | 老大 |
| BUG-004 | P1 | FR-23 切链 metric `smr_chain_switch_total` 未实现 | 待补 | v3.7.2 | TBD |
| BUG-005 | P1 | FR-21 启动迁移失败仅 warn, 客户端可能拿到不一致状态 | 需设计 | v3.8 | TBD |
| BUG-006 | P1 | FR-11 `retry_backoff_ms` 数组元素无校验 (只校 first_token_timeout_ms) | 待补 | v3.7.2 | TBD |
| BUG-007 | P1 | FR-26 全局黑名单 + FR-17 中间通配 (`claude-3-*`) 未实现 | 待补 | v3.7.2 | TBD |
| BUG-008 | P1 | FR-15 `monthly_quota_tokens` / `label` 字段未实现 (config 可写, 运行时不读) | 待补 | v3.7.2 | TBD |
| BUG-009 | P2 | 真实 config.yaml 填入真 key/URL 部署验证未做 | 待做 | v3.7.2 | 老大 |
| BUG-010 | P2 | 单元测试覆盖不完整 (per-tenant key / rate limit / context bridge) | 待补 | v3.7.2 | TBD |
| BUG-011 | P2 | NFR-1/2/3 性能基准无实测数据, 只有 spec | 待测 | v3.7.2 | TBD |
| BUG-012 | P2 | NFR-15 slowloris 攻击无防护 (需 `slow_request_timeout`) | 待补 | v3.8 | TBD |
| BUG-013 | P3 | admin UI "十分不友好!" 老大反馈, 需打磨 | 计划 | v3.8 | TBD |
| BUG-014 | P3 | 无 client SDK (Python/Node/Go) | 计划 | v3.8+ | TBD |
| BUG-015 | P3 | 无 content moderation, 上游投毒无法识别 | 计划 | v3.8+ | TBD |

**修复优先级**: P0 必修 v3.7.2 (剩 BUG-003); P1 必修 v3.7.2 (5 个); P2 v3.7.2 + v3.8+ 排期。

---

## 7. 已落地 vs 待办 (保留 v2, 微调)

### ✅ v3.0-v3.7.1 已落地
- v2 列出的 22 FR 全部实现
- v2 新增 11 FR (FR-23~33) 中: 多数设计稿完成, 7 个 P0+P1 待补
- 6 v3.7 bug + **2 v3.7.1 P0 bug** (BUG-001/002, commit 849665e)

### 🟡 待办 (v3 重新排序)
1. ~~完整需求文档 + 小星雲 review × 2~~ ← **v3.0 完成**
2. **v3.7.2**: 修 BUG-003 (Docker rebuild) / BUG-004 (切链 metric) / BUG-006 (backoff 校验) / BUG-007 (黑名单+通配) / BUG-008 (quota/label)
3. **v3.7.2**: 补 §3.7.5 缺的 4 个 TC (日志脱敏 / Pydantic 校验 / 备份演练 / smr_request_id 错配)
4. **v3.7.2**: 真实 config.yaml 部署验证 (BUG-009)
5. **v3.8**: rate limit 持久化 (BUG-002 实施) + 多 region 部署 + OTel trace + content moderation (BUG-015)

### 💡 未来版本 (v3.8+) (保留 v2)

---

## 8. 术语表 (保留 v2)

---

## 9. API 参考 (v2 新增) ⭐ **v3 schema 校代码**

> **v3 关键修正**: 把 v2 描述的 schema 跟实际代码 (admin_api.py 1178~1279 / public_api.py 117~125) 对齐, 修 5 处不一致。

### 9.1 通用约定 (保留 v2)

### 9.2 Client 端点 (OpenAI 兼容) (保留 v2, 9.2.1~9.2.4)

### 9.3 Admin 端点 (鉴权: admin key) **v3 schema 修正**

#### 9.3.1 `GET /v1/admin/public-keys` **v3 schema 修正**

**实际代码**: `public_key_manager.list_keys()` → 数组, 每项含 `name / key_hash (完整 64 字符) / rate_limit_rpm / model_filter / note / enabled / created_at / usage`。

**v2 错**: schema 含 `monthly_quota_tokens` / `label` / `key_hash` 脱敏 / `last_used_at`, **实际未实现 (BUG-008)** + `key_hash` 是完整 64 字符不脱敏。

**v3 正确 schema**:
```json
{
  "keys": [
    {
      "name": "frontend-prod",
      "key_hash": "a1b2c3d4e5f6...完整 64 字符 SHA256...",  // v3: 完整, 不脱敏 (内部 UI 用)
      "rate_limit_rpm": 60,
      "model_filter": ["gpt-4*"],
      "note": "",
      "enabled": true,
      "created_at": 1718700000.0,  // Unix timestamp
      "usage": {
        "total_calls": 100,
        "success_calls": 95,
        "fail_calls": 5,
        "tokens": 8000,
        "last_used": 1718700000.0,
        "rate_window": [1718700000.0, ...]  // 仅 in-memory, BUG-002
      }
    }
  ]
}
```

#### 9.3.2 `POST /v1/admin/public-keys` **v3 schema 修正**

**实际代码** (admin_api.py 1187-1223): 请求体接受 `name / rate_limit_rpm? / model_filter? / note?`, 响应返回 `ok / key / key_hash / name / rate_limit_rpm / model_filter / note / _warning`。

**v2 错**: schema 含 `monthly_quota_tokens` / `label` (未实现); 字段名 `warning` 应为 `_warning`。

**v3 正确 schema**:
```json
// Request
{"name": "frontend-prod", "rate_limit_rpm": 60, "model_filter": ["gpt-4*"], "note": ""}

// Response (200)
{
  "ok": true,
  "key": "smr-pub-xxxxxxxxxxxxxxxx",  // 一次性返回原 key!
  "key_hash": "a1b2...完整 64 字符",
  "name": "frontend-prod",
  "rate_limit_rpm": 60,
  "model_filter": ["gpt-4*"],
  "note": "",
  "_warning": "原 key 只返回这一次, 之后只能重新生成"  // v3: 字段名 _warning 不是 warning
}
```

#### 9.3.3 `POST /v1/admin/public-keys/{name}/reset` **v3 路径修正**

**v2 错**: 写成 `POST /v1/admin/public-keys/{name}/usage/reset`, **实际路径是 `POST /v1/admin/public-keys/{name}/reset`** (admin_api.py 1262)。

**v3 正确响应** (admin_api.py 1279): `{"ok": true, "reset": "frontend-prod"}` — **v2 schema 的 `old_stats` / `reset_at` 实际未返回**, 需 v3.7.2 补 (记录重置前快照)。

#### 9.3.4 `POST /v1/admin/context_review` **v3 payload 修正**

**v2 错**: 描述 "遍历所有活跃 chain" (无 body), 响应 `{checked, mismatches, fixed, details}`。

**实际代码** (admin_api.py 1101-1134): **需要 body `{"smr_request_id": "xxx"}`**, 返回 `{ok, smr_request_id, report: {...}}`, report 来自 `context_bridge.get_review_report()`。

**v3 正确 schema**:
```json
// Request
{"smr_request_id": "abc-123"}

// Response (200)
{
  "ok": true,
  "report": {
    "smr_request_id": "abc-123",
    "chain_id": "chain-456",
    "switches": [
      {"from": "openai-gpt4", "to": "openrouter-claude", "reason": "429", "elapsed_ms": 1234}
    ],
    "final_provider": "openrouter",
    "final_model": "claude-3-haiku",
    "stale": false
  }
}

// Response (404 not found)
{"ok": false, "smr_request_id": "abc-123", "error": "not_found",
 "hint": "smr_request_id 未在 SMR 跟踪 (可能已淘汰/重启/错的 ID)"}
```

**附**: 还有 `GET /v1/admin/context_review/list?limit=50` 和 `GET /v1/admin/context_review/{smr_request_id}` (admin_api.py 1137/1153), v2 未提, v3 补。

#### 9.3.5 `POST /v1/admin/upgrade` **v3 补全**

(v2 schema 对, 但未列同端点 `GET /v1/admin/version` — 实际存在 admin_api.py 顶部的 `version` 模块导入但未发现 endpoint, **v3.7.2 需验证**。)

#### 9.3.6 (新增) `DELETE /v1/admin/public-keys/{name}` (v3 补)

**实际代码** (admin_api.py 1241-1250): **硬删** (v2 FR-27 提的软删/硬删 `?purge=true` 实际未实现, 当前永远硬删)。**v3.7.2 需补软删 (FR-27 标的 🟡 待补)**。

```json
// Response (200)
{"ok": true, "deleted": "frontend-prod"}

// Response (404)
{"error": "key 'frontend-prod' not found"}
```

---

## 10. 错误码 (保留 v2 §10, schema 略, 16 个错误码表保留)

> **v3 微调**: §10.1 错误码表里 `MODEL_NOT_ALLOWED` (403) 已实施 (openai_routes.py 104-108), `RATE_LIMITED` (429) 已实施 (openai_routes.py 97-102), `KEY_POOL_EXHAUSTED` / `STALE_CHAIN` 仍需 v3.7.2 验证。

---

## 11. 监控指标 (v2 新增) ⭐ **v3 补 3 个**

### 11.1 Prometheus Exporter

| 名称 | 类型 | Labels | 含义 | 状态 |
|---|---|---|---|---|
| `smr_requests_total` | Counter | `tenant`, `endpoint`, `model`, `status` | 总请求数 | 🟡 v3.7.2 |
| `smr_request_duration_seconds` | Histogram | `endpoint`, `model` | 请求耗时 (含上游) | 🟡 v3.7.2 |
| `smr_route_decision_seconds` | Histogram | (none) | 路由决策耗时 (NFR-1) | 🟡 v3.7.2 |
| `smr_chain_switch_total` | Counter | `from_provider`, `to_provider`, `reason` | 切链次数 (FR-23) | 🟡 v3.7.2 (BUG-004) |
| `smr_chain_length` | Histogram | `final_status` | 实际切链深度 | 🟡 v3.7.2 |
| `smr_provider_errors_total` | Counter | `provider`, `error_type` | provider 错误数 | 🟡 v3.7.2 |
| `smr_provider_request_seconds` | Histogram | `provider`, `model` | 上游耗时 | 🟡 v3.7.2 |
| `smr_active_tenants` | Gauge | (none) | 当前活跃 tenant 数 | 🟡 v3.7.2 |
| `smr_rate_limited_total` | Counter | `tenant` | rate limit 触发数 | 🟡 v3.7.2 |
| `smr_quota_exceeded_total` | Counter | `tenant` | quota 超限数 (待 BUG-008) | 🟡 v3.7.2 |
| `smr_penalty_active` | Gauge | `provider` | 当前 penalty 中 provider 数 | 🟡 v3.7.2 |
| `smr_state_write_seconds` | Histogram | `state_file` | state 写盘耗时 | 🟡 v3.7.2 |
| `smr_memory_rss_bytes` | Gauge | (none) | 进程 RSS | 🟡 v3.7.2 |
| `smr_up` | Gauge | (none) | 1=健康, 0=异常 | 🟡 v3.7.2 |
| **`smr_drift_total`** ⭐ v3 新增 | Counter | `type` (chain_id / smr_request_id) | smr_request_id 错配检测数 | 🟡 v3.7.2 |
| **`smr_admin_api_duration_seconds`** ⭐ v3 新增 | Histogram | `endpoint`, `method` | admin API 耗时 (排查 admin 卡顿) | 🟡 v3.7.2 |
| **`smr_config_save_total`** ⭐ v3 新增 | Counter | `result` (success / fail), `file_type` | config 写盘计数 (跟 NFR-19 配合监控) | 🟡 v3.7.2 |

**总计**: v2 14 个 → v3 **17 个** (补 3 个)。
**v3 评估**: 14 + 3 都合理, 无冗余。
- `smr_drift_total`: 关键, 监控 smr_request_id 错配, 触发告警 → mainbot 异常
- `smr_admin_api_duration_seconds`: 必要, admin 卡顿会影响 ops
- `smr_config_save_total`: 必要, 写盘失败率 = 可靠性信号

### 11.2 ~ 11.4 (保留 v2)

---

## 12. 灾备 (v2 新增) ⭐ **v3 补 2 场景**

### 12.1 ~ 12.4 (保留 v2)

### 12.5 故障转移流程 (保留 v2 runbook)

### 12.6 State 文件损坏恢复 (v3 新增 runbook)

**触发**: 启动时 `_load()` 抛 JSON parse error 或 schema 校验失败。

```bash
# 1. 确认损坏
cat /opt/smr/state/public_keys_state.json
# JSONDecodeError

# 2. 移到隔离目录 (不要直接删)
mkdir -p /opt/smr/state/quarantine/$(date +%Y%m%d)
mv /opt/smr/state/public_keys_state.json /opt/smr/state/quarantine/$(date +%Y%m%d)/

# 3. 启动 SMR (会用空 state, 重新建)
sudo systemctl start supermodel-router
# PublicKeyManager: no state file, starting empty

# 4. 手工恢复 keys (从 admin 文档 / 1Password 找原始 key)
curl -X POST http://smr-prod:6473/v1/admin/public-keys \
  -H "Authorization: Bearer ***" \
  -d '{"name": "frontend-prod", "rate_limit_rpm": 60, "model_filter": ["gpt-4*"]}'

# 5. 通知老大
echo "state 文件损坏已隔离, 需补 5 个 public key" | feishu-notify
```

**RTO**: < 5min (假设 keys 在 1Password 有备份)。

### 12.7 升级前/后步骤 (v3 新增 runbook)

```bash
# === 升级前 ===
# 1. 备份 state
sudo tar czf /opt/smr/backups/pre-upgrade-$(date +%Y%m%d).tar.gz \
  /opt/smr/state/ /opt/smr/config.yaml /opt/smr/.backups/

# 2. 记录当前版本
SMR_OLD=$(curl -s http://smr-prod:6473/v1/admin/version | jq -r .version)

# 3. 跑 SMR upgrade 端点拿命令 (不直接执行)
curl -X POST http://smr-prod:6473/v1/admin/upgrade -H "Authorization: Bearer ***"
# 返回: "pip install --upgrade git+https://...@v3.7.2"

# === 升级中 ===
# 4. (低峰期) 通知老大 + 暂停大流量
echo "SMR 升级窗口 5min, 请绕行" | feishu-notify

# 5. 执行升级命令
sudo systemctl stop supermodel-router
cd /opt/smr && source venv/bin/activate
pip install --upgrade git+https://...@v3.7.2

# 6. 跑迁移 (如有)
python -m supermodel_router.migrate_state

# === 升级后 ===
# 7. 启动 + 健康检查
sudo systemctl start supermodel-router
sleep 10
curl http://smr-prod:6473/health/ready
# {"ready": true, "providers_loaded": 5, "models_loaded": 100}

# 8. 烟测 3 个核心端点
curl -X POST http://smr-prod:6473/v1/chat/completions -H "Authorization: Bearer ***" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role":"user","content":"ping"}]}'

# 9. 验证版本 + 通知
SMR_NEW=$(curl -s http://smr-prod:6473/v1/admin/version | jq -r .version)
echo "SMR $SMR_OLD → $SMR_NEW 升级完成" | feishu-notify
```

**RTO 升级**: 5-10min (取决于 pip 速度)。

---

## 13. v2 → v3 改动清单 (Diff) ⭐ **v3 新增**

### 13.1 核心改动 (5 类)

| 类型 | 位置 | v2 描述 | v3 修正 | 原因 |
|---|---|---|---|---|
| **bug 状态** | §6.2 | BUG-001/002 标 P0 待修 | **✅ 已修 (commit 849665e)** | echo 已修 2 P0 |
| **API schema** | §9.3.1 ~ 9.3.6 | 含 `monthly_quota_tokens` / `label` / `warning` / `usage/reset` 路径 | 跟实际代码对齐 (字段未实现 / 路径错 / 字段名错) | v2 描述了未实现功能 + 路径 typo |
| **API schema** | §9.3.4 | context_review 无 body, 返 `{checked, mismatches}` | 需 body `smr_request_id`, 返 `{ok, report}` | v2 跟 v3.5 代码不一致 |
| **metrics** | §11.1 | 14 个 | **17 个** (补 `smr_drift_total` / `smr_admin_api_duration_seconds` / `smr_config_save_total`) | v3.5 错配检测 + admin 卡顿排查需要 |
| **runbook** | §12.5 只有故障转移 | 加 §12.6 (state 损坏) + §12.7 (升级前/后) | 运营兜底, v2 漏 2 个高频场景 |

### 13.2 v3 没改的 (保留 v2 的 5 处重大修正 + 7 新章节)

(v2 §13 全部保留: 7 新章节 / 11 FR / 12 NFR / 5 重大修正 / 0 删并)

### 13.3 v3 给 echo / 老大的 todo (新)

详见 §3.7.5 (4 个缺漏 TC) + §9.3.6 (软删未实现) + §6.2 (8 个待修 bug) + §14 (路线图) + §15 (风险登记册)。

---

## 14. 实施路线图 (v3 新增) ⭐

### 14.1 v3.7.1 ✅ **已发布** (2026-06-18 02:20)

| 项 | 状态 | 备注 |
|---|---|---|
| 修 BUG-001 (key_hash 完整 SHA256) | ✅ | commit 849665e |
| 修 BUG-002 (rate limit 文档化) | ✅ | commit 849665e (in-memory 行为不变, 文档显式声明) |

### 14.2 v3.7.2 (计划: 1-2 周内) ⭐

**修 5 P0/P1 bug**:

| Bug | 工作量 | 验收 |
|---|---|---|
| **BUG-003** Docker rebuild + 端到端 | 半天 | docker-compose up → OpenAI SDK 调通 |
| **BUG-004** 切链 metric `smr_chain_switch_total` | 2h | TC-23.1 通过 (mock 切链 +1) |
| **BUG-006** `retry_backoff_ms` 数组元素校验 | 1h | TC-11.2: 传 `[100, -50]` 应 422 |
| **BUG-007** 全局黑名单 + 中间通配 | 4h | TC-7.1 黑名单返 404; TC-17.2 `claude-3-*` 中间通配生效 |
| **BUG-008** `monthly_quota_tokens` / `label` 字段实施 | 1 天 | TC-28.1 quota 超限 429 QUOTA_EXCEEDED |

**补 4 个缺漏 TC** (§3.7.5):

| TC | 工作量 |
|---|---|
| TC-25.1 (日志脱敏) | 2h |
| TC-28.1/28.2 (Pydantic 校验) | 1h |
| TC-DR-1 (备份演练) | 2h |
| TC-8.2 (smr_request_id 错配) | 1h |

**修 §9 描述/代码不一致**:

| 改动 | 工作量 |
|---|---|
| §9.3.3 `reset` 响应补 `old_stats` / `reset_at` 字段 | 1h |
| §9.3.6 软删 (`?purge=false` 默认软删) | 4h (FR-27 实施) |
| 验证 §9.3.5 `GET /v1/admin/version` endpoint | 0.5h |

**合计**: v3.7.2 约 5 天工作量。

### 14.3 v3.8 (计划: 1-2 月内)

**架构升级**:

| 项 | 说明 | 阻塞 |
|---|---|---|
| **BUG-002 实施** rate limit 持久化 | 走 Redis, 启动时按 now - window 过滤 | 需要 Redis 依赖 |
| **BUG-005** 启动迁移失败语义 | 改为 "fail-fast + 启动失败" 而非 warn | 设计评审 |
| **BUG-012** slowloris 防护 | 加 `slow_request_timeout` (默认 30s) | nginx 层更合适 |
| 多 region 部署 | 共享 state (Redis/S3) + GeoIP DNS | Redis 依赖 |
| OpenTelemetry trace (NFR-22) | OTLP collector, smr_request_id 作 trace_id | 需 OTLP 部署 |
| content moderation (BUG-015) | 上游响应关键词扫描 + 拦截 | 需白名单策略 |
| BUG-013 admin UI 打磨 | 老大反馈 "十分不友好" | 设计改版 |

### 14.4 v3.8+ (远期)

- BUG-014 client SDK (Python/Node/Go)
- 模型评测系统 (自动 cap_score 校准)
- 智能 fallback 策略 (基于历史成功率)

---

## 15. 风险登记册 (v3 新增) ⭐

> **来源**: v2 → v3 review 中识别的 5 大风险, 每个含触发条件 + 缓解 + Owner。

| # | 风险 | 严重度 | 触发条件 | 影响 | 缓解措施 | Owner | 状态 |
|---|---|---|---|---|---|---|---|
| **R1** | **per-tenant key 失窃/滥用** | 🔴 高 | 1 个合法 key 被打满 rpm 1h+; 或单 key 出现在多个 IP | 用量失控 / 成本超支 / 影响其他租户公平 | (1) rate limit (FR-16) 60s 窗口; (2) v3.8 持久化 + IP 维度限速; (3) admin 看 `smr_rate_limited_total` 告警; (4) 老大手工 disable key | 老大 (disable) / 小星雲 (v3.8 IP 限速) | rate limit 已生效, IP 限速 v3.8 |
| **R2** | **rate limit 重启绕过 (BUG-002)** | 🟡 中 | SMR 进程重启, 用户瞬时打满 rpm 1 次 | 配额被绕过 (60s 60 次 → 重启后可瞬时 60 次) | (1) 文档化 (v3.7.1 ✅); (2) v3.8 走 Redis 持久化 `rate_window`; (3) 短期: 减少 SMR 主动重启次数 | 小星雲 (v3.8 实施) | v3.7.1 文档化, v3.8 根治 |
| **R3** | **Docker rebuild 一直搁置 (BUG-003)** | 🟡 中 | v3.7.0 release 至今 10h+ Docker 未验证 | 实际部署可能炸 (env 变量 / 路径 / 权限), 端到端未跑 | (1) v3.7.2 排期 (半天); (2) 老大本人跑 (todo #3) | 老大 | 待 v3.7.2 |
| **R4** | **state 损坏 / 配置错误无 runbook** | 🟡 中 | config.yaml 改错 或 state 文件损坏 | 服务起不来 / 数据丢 | (1) v3 §12.6 + §12.7 runbook 新增; (2) FR-25 rollback; (3) .backups/ 保留 50 | 小星雲 (runbook ✅) / 老大 (演练) | runbook 已写, 演练待做 |
| **R5** | **关键安全审计无常态化** | 🔴 高 | 老大不跑 §4.6.4 渗透测试 checklist; admin key 长期不变 | 内鬼 / 失窃 / 0day 利用 | (1) 季度跑渗透测试 (1h); (2) admin key 90 天轮换 (待建); (3) 第三方 SIEM 接 audit log (v3.8+) | 老大 (季度) / 小星雲 (v3.8 SIEM) | checklist 已写, 常态化未建 |

**风险等级**: 🔴 高 (业务影响大) / 🟡 中 (可恢复) / 🟢 低 (可接受)
**评审频率**: 每月 1 次, 每次 release 前必过

---

## 16. v3 → 老大 review checklist (v3 收尾) ⭐

> **强烈建议老大亲自 review 以下章节** (这些是小星雲可能判断错或需要业务拍板的):

### 16.1 必看 (P0 决策)

1. **§6.2 bug 池** — 确认 BUG-001/002 ✅ 状态认可, BUG-003 是否真的排到 v3.7.2 还是老大自己跑
2. **§9.3.1 / §9.3.2 / §9.3.3 / §9.3.4 API schema 修正** — v2 描述了未实现字段 (`monthly_quota_tokens` / `label` / 中间通配), v3 校代码后要砍; 老大拍: 这些字段 v3.7.2 必须实施还是延后?
3. **§14 实施路线图** — v3.7.2 的 5 天工作量 + v3.8 的 6 项架构升级, 时间线和优先级老大认可?
4. **§15 风险登记册 R1/R3/R5** — R1 (per-tenant key 滥用) 老大是否接受 "rate limit + 老大手工 disable" 作为 v3.7 临时方案; R3 (Docker rebuild) 是否老大自己跑; R5 (渗透测试常态化) 老大是否承诺季度跑

### 16.2 应看 (P1 复核)

5. **§3.7.5 缺漏/冗余 TODO** — 4 个缺漏 TC + 3 个冗余合并, 优先级
6. **§4.6.4 渗透测试新增 4 条** — 老大是否还要加其他场景 (例: 上游投毒模拟 / admin key 爆破)
7. **§11.1 新增 3 个 metrics** — `smr_drift_total` 错配阈值多少触发告警? 老大拍
8. **§12.6 / §12.7 新增 runbook** — 流程是否完整, 老大要演练一次

### 16.3 可选 (P2 校对)

9. **§13 v2→v3 diff** — 5 类改动是否完整, 有无遗漏
10. **§7 已落地 vs 待办** — 排期是否合理

### 16.4 留给老大拍 (小星雲不决)

- **R1 缓解第 2 条**: v3.8 加 IP 维度限速 vs 业务方自己加 IP 白名单?
- **R2 缓解**: rate_window 走 Redis vs 走 public_keys_state.json 自身? (Redis 多一个依赖, 自带文件简单)
- **§9.3.6 软删**: 30 天保留期是软上限还是硬上限? 老大拍
- **§3.7.5 缺漏 TC 排序**: 4 个里哪个先补? 建议: TC-25.1 (脱敏) > TC-28.1/28.2 (Pydantic) > TC-8.2 (错配) > TC-DR-1 (演练)
- **§15 R5**: admin key 90 天轮换老大是否接受? 不接受的话提个折中方案

---

**v3.0 终稿 | 行数 ≈ 880 | 新增 2 章节 (§14 路线图 + §15 风险登记册) + §16 review checklist; 修正 5 处 v2 错 (bug 状态 + API schema + metrics + runbook + 威胁模型)**
**v3.7.1 已发布, v3.7.2 等老大 review 后启动**
