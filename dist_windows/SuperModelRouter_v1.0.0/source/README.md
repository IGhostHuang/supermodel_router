# SMR (supermodel_router)

> **SMR (前 FMR / free-model-router)** — OpenAI 兼容的多 provider LLM 路由网关
> v1.0.0 · 2026-06-16 · 独立运行模式（不集成到 Hermes）

---

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
  port: 19876
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
# 直接运行
python3 -m free_model_router --config config.yaml --port 19876

# systemd 部署
sudo cp deploy/smr.service /etc/systemd/system/
sudo systemctl enable --now smr
```

### 调用 (OpenAI 兼容)

```bash
# 非流式
curl -X POST http://127.0.0.1:19876/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role":"user","content":"ping"}]
  }'

# 流式
curl -N -X POST http://127.0.0.1:19876/v1/chat/completions \
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
python3 -m free_model_router --config config.yaml --port 19876 &
python3 tests_free_model_router/test_e2e.py
python3 tests_free_model_router/test_stream.py   # 流式 (SMR 阶段 2 修复后)
```

---

## 📦 部署

### Docker

```bash
docker build -t smr:1.0.0 .
docker run -d -p 19876:19876 \
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
  port: 19876
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

- **v1.0.0 (SMR 阶段 1)** — OpenAI 兼容网关, 4 模式过滤, 多 key 轮询, 401 换 key
- **v1.0.0 (SMR 阶段 2)** — 修复流式 chat 500 bug (560eb61)
- **v0.x (FMR 阶段)** — 内部代号 free-model-router, 已废弃

---

## 📄 许可证

MIT
