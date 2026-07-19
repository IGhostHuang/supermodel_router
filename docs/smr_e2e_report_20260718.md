# SMR 端到端测试报告

**日期**: 2026-07-18 20:00 CST
**SMR 版本**: 3.5.0 (bridge)
**容器**: `supermodel_router` (healthy)
**测试 agent**: housekeeper (伊芙)
**测试 key**: `__b1_e2e_v3` / `__b1_e2e_v4` / `__b1_final` / `__b1_rl_test` / `__t10_probe` — 全部已清理
**剩余正式 key**: 5 个 (`state-test-*` / `echo-test-*` / `Hermes` / `long-context-quality-vn4h-key` / `all-key`)

---

## 1. 根因修复 (P0)

**问题**: `config.yaml` 中 openrouter `api_keys` 被替换为脱敏串 (`sk-or-...1423` / `sk-or-...a1cb`), entrypoint 只渲染 `<XXX_PLACEHOLDER>` 格式, 对裸脱敏串无感 → 401 → 全线 penalty。

**修复**:
1. 从 `secrets/openrouter` + `secrets/openrouter.key2` 读取真 key (73B)
2. Python 精确替换脱敏串 → 真 key
3. 原文件备份: `config.yaml.bak-b1-20260718-194614`
4. `config.yaml` + `secrets/` 双双在 `.gitignore` 内, 无泄露风险

---

## 2. 对话能力 (L1/L2/T1)

| 测试 | 项 | 结果 |
|---|---|---|
| **L1 非流式** | HTTP / model / content / cost | 200 / `openrouter/gemma-4-26b-a4b-it:free` / "我是 Gemma 4..." / $0.0000115 |
| **L2 流式 SSE** | 帧数 / [DONE] / content chunks | 39 行 / 1 / 9 (首帧 bridge v3.5 切换通知) |
| **T1 上下文** | history→回答 | "喵喵" ✅ (`newapi/ark-code-latest`, 87 tokens) |

---

## 3. 多模态 (T2)

- **输入**: 10×10 红色 PNG (75B) → base64 (100B) → `data:image/png;base64,`
- **模型**: `newapi/ark-code-latest`
- **回答**: "红色" ✅

---

## 4. 限流 (T6/T9)

| 测试 | 结果 |
|---|---|
| **T6 RPM=6, 快发 8 次** | 6×200 + 2×429 ✅ 精准限流 |
| **T9 并发 5 请求 (xargs -P 5)** | 全 200, 耗时 3.0-5.9s ✅ 真并行 |

---

## 5. 管理面板 (T3/T4/T5/T13)

### T4 — Providers (10 个)

| Provider | Enabled | 模型 | Provider | Enabled | 模型 |
|---|---|---|---|---|---|
| openrouter | ✅ | 18 | 魔塔免费模型 | ✅ | 55 |
| nvidia | ✅ | 7 | cloudflare | ✅ | 0 |
| newapi | ✅ | 65 | cloudflare2 | ✅ | 0 |
| fusion | ✅ | 147 | dashscope_bailian | ❌ | 0 |
| — | — | — | huggingface / volc_ark | ❌ | 0 |

**总计**: 292 模型 · 81 admin 端点

### T13 — Budget Estimate

```
GET /v1/admin/budget/estimate?provider=newapi&model_id=ark-code-latest&prompt_tokens=1000&completion_tokens=500
→ HTTP 200
  cost_per_1k_input: $0.001 · cost_per_1k_output: $0.002
  estimated_cost: $0.0 (免费) · value_score: 5000
```

⚠️ **参数名坑**: `provider=` + `model_id=` (snake_case), 不是 `model=`

---

## 6. Provider Health (T12)

| Provider | Healthy | Degraded | Skip | 总计 |
|---|---|---|---|---|
| openrouter | 8 | 5 | 1 | 14 |
| nvidia | 6 | 1 | 0 | 8 |
| newapi | 1 | 3 | 0 | 6 |
| fusion | 3 | 4 | 0 | 7 |
| 魔塔免费模型 | 3 | 6 | 0 | 9 |
| cloudflare / cloudflare2 | 0 / 0 | 1 / 1 | 0 / 0 | 1 / 1 |

---

## 7. Penalty Lifecycle (T10)

| 步骤 | 操作 | 结果 |
|---|---|---|
| 10a | POST /penalty/reset | ✅ cleared: 0 |
| 10b | 5× gemma 请求 (触发) | 1×200 + 4×503 |
| 10c | GET /penalty (查询) | 4 条 (openrouter 家族全上榜) |
| 10d | 再请求 gemma (惩罚中) | ❌ "No available models" |
| 2min 后 | 再请求 gemma | ✅ 200, chain 1/2, 切换 1 次 |

**结论**: 机制正常, 但**全家族 penalty 时链内无跨 provider 兜底** ⚠️

---

## 8. Key 管理 (T14/T15)

| 端点 | 功能 |
|---|---|
| `GET /v1/admin/public-keys/usage` | 按 key 聚合 |
| `GET /v1/admin/public-keys/{name}/usage-by-model` | 按模型细粒度 |
| `POST /v1/admin/public-keys/{name}/reset` | 重置用量 |

**示例**: `__b1_final` total_calls=11 → gemma-4-26b 6 次 + qwen3-coder 5 次

---

## 9. 已知风险 & 后续待办

| # | 问题 | 严重度 | 建议 |
|---|---|---|---|
| R1 | openrouter free-per-day 配额耗尽 (明日 UTC 0 重置) | 🟡 | 期间用 newapi/fusion/魔塔 链路 |
| R2 | 全家族 penalty 时无跨 provider fallback | 🟡 | 给高频免费模型配非-openrouter fallback |
| R3 | 无效 key 返 503 (应 401) | 🟢 | 路由层前置鉴权 |
| R4 | config.yaml 真 key 持久性 | 🟢 | 脱敏版覆盖会再断, `secrets/` 有备份 |
| R5 | Budget estimate 参数名非标准 (`model_id` vs `model`) | 🟢 | 写进 README |

---

## 10. 未测项 (后续锦上添花)

- **TPM 限流** — key schema 只有 `rate_limit_rpm`, TPM 疑是 provider 侧
- **Embeddings** — `/v1/embeddings` + `qwen3-embedding` provider
- **Chain 审计** — 遍历高频模型 fallback 拓扑
- **Stress test** — 100 并发 × 5min

---

## 11. 结论

**SMR 对话功能完全恢复 ✅**

- 非流式 / 流式 / 上下文 / 多模态 / 并发 / 限流 全部通过
- 8 provider · 292 模型 · 81 admin 端点 可用
- Penalty 机制正常, 2min 自然恢复
- Key 管理 / 用量 / 预算 面板完整

**当前瓶颈**: OpenRouter 免费日配额耗尽 (明日重置), fallback 链路 (newapi/ark-code-latest 等) 工作正常。

---

*报告生成: 2026-07-18 20:00 CST · 测试 agent: b1 (伊芙 housekeeper)*
