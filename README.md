# SMR (supermodel_router)

> **SMR (前 FMR / free-model-router)** — OpenAI 兼容的多 provider LLM 路由网关
> **v3.10.0** · 2026-06-19 · 轮询 v5 + 模型分组向导 + 版本自更新

---

## 🎉 v3.10.0 新增 (2026-06-19) — 模型分组向导 + 轮询策略 v5

**4 种 group-level 策略**: round-robin-group (默认) / flat / group-failover / group-weighted

**分组向导持久化**: wizard 一键生成选择的 strategy 写入 config.yaml

**优先级**: model-level routing 先选候选 → group-level 决定 group 内顺序

---

## 🎉 v3.4.0 新增 (2026-06-17) — 上下文桥接 + 过期标记

**痛点**: chain rotation 切换到新模型时, 新模型只看到原始 prompt, 不知道前面模型为什么失败/部分响应, 容易"对话断层"重复输出或答非所问; 同时如果请求耗时 > 30min, 新模型根本不知道信息可能已过期, 还在用"最新"的口吻回答过时的事实.

**3 大机制**:

1. **非流式切换注入 system prompt** — 切到新 candidate 前, 自动拼一份"上下文桥接"system message:
   ```
   [SMR 上下文桥接 v3.4.0]
   你正在接续一个多模型对话. 前面有 N 次模型尝试 (都失败或部分响应):
   [候选 A (mock-model-a) — 失败]
     状态: http_401, 错误: Unauthorized
   [候选 B (mock-model-b) — 失败]
     状态: timeout, 错误: 连接超时

   你的任务:
   1. 直接基于**用户最后一条消息**和**已有对话历史**给一个完整回答
   2. 不要重复前面模型已经成功输出的内容
   3. 如果切到你的时间已经超过 30 分钟, 请明确提醒用户"信息可能已过期"
   ```
2. **流式切换发 sentinel** — 切到新 candidate 的第一个 chunk 前 yield 一条 `data: {"_smr_bridge": {...}}` SSE 事件, 客户端按 SSE 协议解析即可知道切换了 + 切了几次 + 当前是否过期
3. **过期标记 stale** — 整个请求耗时 (time.time() - request_start_time) > `stale_threshold_seconds` (默认 1800s/30min) 时, 响应 `_router.stale=true`, 流式 sentinel 也带 `stale: true`. UI 可显示"⚠️ 信息可能已过期"

**API 新增字段** (非流式响应 `_router`):
```json
{
  "_router": {
    "provider": "mock-b",
    "model": "mock-model-b",
    "chain_position": 1,
    "switched_from": [
      {
        "from_provider": "mock-a",
        "from_model": "mock-model-a",
        "from_full_path": "mock-a/mock-model-a",
        "status": "http_401",
        "http_code": 401,
        "error": "Unauthorized",
        "partial_text": null,
        "stale": false
      }
    ],
    "stale": false,
    "age_seconds": 0,
    "stale_threshold_seconds": 1800
  }
}
```

**SSE Sentinel 协议** (流式首个 chunk):
```
data: {"_smr_bridge": {"version": "3.4.0", "switched_from_count": 1, "switched_from": [...], "stale": false, "age_seconds": 3, "stale_threshold_seconds": 1800}}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk",...}
```

**配置** (`config.yaml`):
```yaml
context_bridge:
  enabled: true               # 全局开关
  stale_threshold_seconds: 1800  # 30 min 过期阈值
  max_history: 5              # 最多保留几次切换记录
  sentinel_enabled: true      # 流式是否发 sentinel
  inject_template: |          # 注入 system prompt 模板 (支持 {version}/{n_attempts}/{attempt_blocks}/{age_minutes})
    [SMR 上下文桥接 v{version}]
    你正在接续一个多模型对话...
```

**Admin API**:
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/admin/context_bridge` | 查看 config + stats |
| PUT | `/v1/admin/context_bridge` | 热更新 enabled/threshold/max_history/sentinel_enabled |
| POST | `/v1/admin/context_bridge/reset` | 清零 stats (保留 enabled + threshold + history) |

**端到端验证**: `tests/test_context_bridge_e2e.py` 3 场景 12/12 通过 (非流式切换 / 流式 sentinel / stale 过期标记).

**PyInstaller 升级**: 入口 `run_smr_pyinstaller.py` 新增 `--host/--port/--config/--log-level` argparse, 跟 `run.py` 行为对齐.

---

## 🎉 v3.3.0 新增 (2026-06-17)

- **轮询机制 v4**: 高分模型优先, 同 model 全部 key 失败再换下一 model (跨 provider); 失败时自动降分避免下次重复失败路径; 周期复测自动恢复分数
- **多 key 真正轮询** (B1 修复): `/v1/models` 拉取阶段 401/403 自动换 key, 不再因第 1 个 key 失败而 0 模型
- **exclude 正则匹配** (B4 修复): `exclude: [".*-legacy.*"]` 真正生效 (之前是字面匹配)
- **错误消息干净** (B2 修复): 4xx/5xx 不再泄漏 raw HTTP status line + headers
- **版本管理** (C): `/v1/admin/version` (当前+GitHub release) + `/v1/admin/upgrade` (生成 git/pip/docker/binary 升级命令)
- **Penalty 管理**: `/v1/admin/penalty` GET 状态, `/reset` 清零, `/decay` (支持 `force:true` 立即复测)
- **PyInstaller 单文件 21MB** + **Docker 镜像 v3.3.0** 一键部署

## 🎯 设计目标

SMR 解决"多 provider 多 key 难管理"问题:

- **统一入口** — 多个 OpenAI 兼容 provider (OpenRouter / NewAPI / 自建 vLLM ...) 合并成 1 个 OpenAI 兼容 API
- **智能路由** — 按 model 名称自动选 provider, 按 key 状态自动选 key
- **错误隔离** — 一个 provider 401/429/5xx 不会影响其他 provider
- **多 key 轮询** — 单 provider 多 key 负载均衡, 401/403 立即换 key
- **4 模式过滤** — `pattern` (正则) / `include` (白名单) / `exclude` (黑名单) / `all`

---

## 🚀 快速开始

### 安装

```bash
git clone https://github.com/IGhostHuang/supermodel_router.git
cd supermodel_router
pip install -r requirements.txt
```

### 配置 (config.yaml)

```yaml
server:
  host: 127.0.0.1
  port: 6473
  api_key: "your-secret-key"  # 可选, Bearer 鉴权

providers:
  openrouter:
    name: openrouter/free
    base_url: https://openrouter.ai/api/v1
    api_keys: ["sk-or-..."]
    model_rules:
      mode: pattern
      pattern: ".*:free$"        # 只用 :free 后缀的模型
  
  newapi:
    name: mainrouter
    base_url: https://your-newapi.com/v1
    api_keys: ["sk-...", "sk-..."]  # 2 个 key 轮询
    model_rules:
      mode: all
```

### 启动

```bash
# 默认端口 6473
python3 -m supermodel_router --config config.yaml

# 自定义端口 (CLI 参数优先)
python3 -m supermodel_router --config config.yaml --port 8080

# systemd 部署
sudo cp deploy/smr.service /etc/systemd/system/
sudo systemctl enable --now smr
```

### 修改默认端口 (3 种方式, 优先级从高到低)

| # | 方式 | 示例 | 生效时机 |
|---|---|---|---|
| 1 | CLI 参数 | `python3 -m supermodel_router --port 8080` | 立即 |
| 2 | 环境变量 | `PORT=8080 python3 -m supermodel_router` | 立即 |
| 3 | config.yaml | 编辑 `server.port: 8080` + 重启服务 | 重启后 |

**Docker / docker-compose**:
- Docker: `-e PORT=8080` 或 `-p 8080:8080`
- docker-compose.yml: 改 `services.supermodel_router.environment.PORT` + `ports` 两处

**Windows (install.ps1)**:
- 默认配置 `server.port: 6473` (在 `C:\ProgramData\SuperModelRouter\config.yaml`)
- 编辑该文件改 `port: 8080`, 重启服务 `.\install.ps1 -Start`

### 调用 (OpenAI 兼容)

```bash
# 非流式
curl -X POST http://127.0.0.1:6473/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role":"user","content":"ping"}]
  }'

# 流式
curl -N -X POST http://127.0.0.1:6473/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "stream": true,
    "messages": [{"role":"user","content":"ping"}]
  }'
```

---

## 🔧 架构

```
Client (OpenAI 兼容)
  ↓ HTTP/SSE
┌─────────────────────────────────────────┐
│  SMR Gateway (Python http.server)       │
│  ├─ /v1/chat/completions  (流式 + 非流式) │
│  ├─ /v1/models             (合并模型列表) │
│  ├─ /v1/providers          (provider 状态)│
│  ├─ /admin                 (管理面板)     │
│  └─ /health                (健康检查)     │
├─────────────────────────────────────────┤
│  Router                                  │
│  ├─ Select target (按 model + 过滤)      │
│  ├─ Forward (httpx async)                │
│  ├─ Classify error (401/429/5xx)        │
│  ├─ Key rotate (同 provider 换 key)      │
│  └─ Provider rotate (跨 provider 切换)   │
├─────────────────────────────────────────┤
│  Provider Manager                        │
│  ├─ 4 模式过滤 (pattern/include/exclude/all)│
│  ├─ Health tracking (degraded 5min 恢复) │
│  └─ Rate limit (Retry-After)             │
└─────────────────────────────────────────┘
  ↓ HTTPS
Upstream providers (OpenRouter / NewAPI / ...)
```

---

## 📊 路由策略

### Model 解析

1. **精确匹配**: `provider/model_id` 直接路由
2. **模糊匹配**: `gpt-4` → 在所有 provider 中找 `gpt-4*` 模型, 按质量分排序
3. **auto 模式**: 不指定 model → 按质量分自动选最优

### 错误处理

| HTTP | 行为 |
|------|------|
| 200 | 成功, 记录 latency + tokens |
| 401/403 | **同 provider 换 key** (不换 model) |
| 404/410 | **永久 disable model** (此 provider 跳过) |
| 429 | Retry-After 后冷却, 不重试 |
| 5xx | 换 provider 重试, 直到耗尽 |

### Key 轮询

- **多 key**: `api_keys: ["key1", "key2", "key3"]` round-robin
- **健康隔离**: 401 触发的 key 进 cooldown 5min, 5min 后恢复
- **不互相污染**: key1 401 不影响 key2/key3

---

## 🧪 测试

```bash
# 单元测试 (不依赖 mock upstream)
python3 -m pytest tests_free_model_router/test_filter.py -v   # 13 tests
python3 -m pytest tests_free_model_router/test_config.py -v   # 11 tests
python3 -m pytest tests_free_model_router/test_provider.py -v # 13 tests

# 沙盒 e2e 测试 (需要 mock_upstream.py)
cd /tmp/sandbox-fmr
python3 mock_upstream.py 18765 &
python3 -m free_model_router --config config.yaml --port 6473 &
python3 tests_free_model_router/test_e2e.py
python3 tests_free_model_router/test_stream.py   # 流式 (SMR 阶段 2 修复后)
```

---

## 🆕 v3.1 — 自定义 Provider + 自定义 Tier Bonus

SMR v3.1 加 5 个管理 API + dashboard UI:

### API 端点

| Method | Path | 作用 |
|---|---|---|
| `POST` | `/v1/admin/providers` | 添加自定义 provider (OpenAI / Azure / 自建 / 中转 / newapi 等任意 OpenAI 兼容 API) |
| `DELETE` | `/v1/admin/providers/{name}` | 删除 provider |
| `PUT` | `/v1/admin/providers/{name}` | 更新 provider (增量覆盖) |
| `GET` | `/v1/admin/classifier` | 读 classifier 配置 (含兜底内置默认) |
| `PUT` | `/v1/admin/classifier` | 改 tier_bonus / custom_keywords / modality_base_score |

### Dashboard UI

打开 `/admin`, 新增 2 个按钮:

- **➕ 添加 Provider** — 表单弹窗, 填 name / base_url / api_keys / model filter mode
- **⚙️ Tier Bonus** — 3 个 KV 编辑器: tier_bonus (覆盖内置) / custom_keywords (累加) / modality_base_score

每个 provider 卡片右侧有 **🗑️ 删除** 按钮 (config 同步移除).

### 自定义 Classifier — `config.yaml`

```yaml
classifier:
  # 覆盖内置 tier 加成 (内置默认: turbo=25, pro=20, lite=-10 ...)
  tier_bonus:
    pro: 30        # 把 pro 从 20 改成 30
    turbo: 50      # 把 turbo 从 25 改成 50
    custom_tier: 15 # 添加新 tier

  # 自定义关键词加分 (叠加, 不 break, 多关键词命中累加)
  custom_keywords:
    reasoning: 30  # 含 "reasoning" 的模型 +30 分
    coder: 25      # 含 "coder" 的模型 +25 分
    r1: 20         # DeepSeek R1 系列 +20

  # 覆盖模态基类分 (内置: text-only=50, multimodal=85, image-gen=70 ...)
  modality_base_score:
    text-only: 60
    multimodal: 90
```

**优先级**: 内置默认 < config.yaml classifier < PUT /v1/admin/classifier (运行时)

### 能力分公式

```
capability_score = modality_base_score[modality]
                 + tier_bonus[kw] (内置或用户覆盖, 第一个匹配 break)
                 + Σ custom_keywords[kw] (用户自定义, 累加)
                 + context_length_bonus (200K+20, 128K+15, 32K+10, 16K+5)
                 clip(0, 100)
```

### 端到端验证 (已通过)

```bash
# 1. 添加自定义 provider (任何 OpenAI 兼容 API)
curl -X POST http://127.0.0.1:6473/v1/admin/providers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "myopenai",
    "config": {
      "base_url": "https://api.openai.com",
      "api_keys": ["sk-xxx"],
      "model_rules": {"mode": "pattern", "pattern": "gpt-4.*"},
      "max_concurrent": 3
    }
  }'

# 2. 调整 tier 加成 (把 pro 从 20 改 30)
curl -X PUT http://127.0.0.1:6473/v1/admin/classifier \
  -H "Content-Type: application/json" \
  -d '{"tier_bonus": {"pro": 30}, "custom_keywords": {"reasoning": 30}}'

# 3. 立即生效 — registry 自动 rebuild + 模型 cap_score 重算
curl http://127.0.0.1:6473/v1/models | jq '.data[0].capability_score'
```

---

## 📦 部署

### Docker

```bash
docker build -t smr:1.0.0 .
docker run -d -p 6473:6473 \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  --name smr \
  smr:1.0.0
```

### Windows 服务 (PyInstaller)

```bash
python build_windows.py  # 打包 smr.exe
python package_windows.py  # 创建 smr-windows.zip
# 部署: 拷贝到 Windows + nssm install smr smr.exe
```

### systemd

```bash
sudo cp deploy/smr.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now smr
sudo systemctl status smr
```

---

## 🔑 配置参考

```yaml
server:
  host: 127.0.0.1
  port: 6473
  api_key: "your-secret"  # 留空 = 无鉴权

routing:
  max_retry: 2               # 最大重试次数
  retry_backoff_ms: [0, 500] # 重试 backoff
  failover_threshold: 3      # 连续 N 次失败 → degraded
  recovery_interval: 300     # 5min 自动恢复

providers:
  <name>:
    name: <model_id>            # OpenAI 兼容的 model id
    base_url: <url>              # 兼容 OpenAI /v1 接口
    api_keys: [<key1>, <key2>]   # 1+ key
    model_rules:
      mode: all                  # all | pattern | include | exclude
      pattern: ""                # 正则 (pattern 模式)
      include: []                # 白名单 (include 模式)
      exclude: []                # 黑名单 (exclude 模式)
    max_concurrent: 3            # 并发槽位
```

---

## 🐛 已知问题

- **流式实时性破坏**: `_forward_stream` 在 async with 内 cache 整段 stream 后 return chunks, 必须等上游发完所有 chunk 才开始写 client. 优化方向: 改 async generator 保持 stream 跨 async with 边界.
- **WSL 推送限制**: git push 走 HTTPS 偶发 GnuTLS recv error, 推荐 Windows 侧 push.

---

## 📜 版本历史

- **v3.11.0 (2026-06-21)** — 易经算法集成: 8 卦 dashboard (/admin/9-gong) + 5 provider 卦位 (config provider_trigram) + 12 时辰火候 (by-fire-候 cron) + 3 cron (by-five-element / by-san-yi / by-fire-候); version.py/admin_ui 同步 bump, 同端口 6473 升级
- **v3.10.0 (2026-06-19)** — 轮询策略 v5: 4 种 group-level 策略 (round-robin-group/flat/group-failover/group-weighted); 分组向导持久化 (wizard strategy → config.yaml)
- **v3.4.0 (2026-06-17)** — 上下文桥接 (chain rotation 过期标记); prompt 超时警告 (>30min 自动 system warning)
- **v3.1.0 (2026-06-17)** — 轮询机制 v4 (高分优先 + key 轮询 + 跨 provider + 降分 + 周期复测); 多 key 真正轮询 (B1); exclude 正则 (B4); 错误消息干净 (B2); 版本管理 (C: /v1/admin/version + upgrade); penalty admin endpoints
- **v3.0.0 (2026-06-16)** — 模态路由 + 质量评分 (capability_score + EWMA latency)
- **v1.0.0 (SMR 阶段 2)** — 修复流式 chat 500 bug (560eb61)
- **v1.0.0 (SMR 阶段 1)** — OpenAI 兼容网关, 4 模式过滤, 多 key 轮询, 401 换 key
- **v0.x (FMR 阶段)** — 内部代号 free-model-router, 已废弃

---

## 📄 许可证

MIT
