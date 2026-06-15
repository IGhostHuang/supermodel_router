# SuperModel Router — 多 Provider / 多 Key / 智能路由

OpenAI-兼容 API 代理，支持：
- **多 Provider 聚合** — 一个端点背后聚合 N 个 provider
- **每 Provider 多 API Key** — 自动轮询 (round-robin)
- **灵活模型过滤** — pattern / include / exclude / all
- **自动模型发现** — 定期拉 `/v1/models` 按规则过滤
- **健康追踪 + 自动恢复** — failover + degraded + recovery
- **可热重载配置** — 改 yaml 自动生效
- **全 OpenAI-兼容** — 无缝接入任何 OpenAI SDK

## 快速开始

### 1. 安装

```bash
cd ~/projects/supersupermodel_router
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置

编辑 `config.yaml`:

```yaml
server:
  host: "0.0.0.0"
  port: 1298
  api_key: "your-secret-key"   # 可选, 留空不鉴权

routing:
  strategy: "round-robin"      # round-robin | random | failover
  failover_threshold: 3        # 连续 N 次失败标记 degraded
  recovery_interval: 300       # 5min 后自动恢复
  max_retry: 2                 # 失败重试次数
  retry_backoff_ms: [0, 500]   # [首次等待, 二次等待]

providers:
  openrouter:
    enabled: true
    base_url: "https://openrouter.ai/api/v1"
    api_keys:
      - "sk-or-v1-xxx"
      - "sk-or-v1-yyy"        # 多个 key 自动轮询
    model_rules:
      mode: "pattern"          # all | pattern | include | exclude
      pattern: ".*:free"       # 只取 ID 以 :free 结尾的模型
      exclude: []              # 额外排除某些模型
    max_concurrent: 3
    health_check_interval: 300

  newapi:
    enabled: true
    base_url: "https://your-newapi/v1"
    api_keys:
      - "sk-xxx"
    model_rules:
      mode: "include"
      include:
        - "gpt-4"
        - "claude-3.5"
      exclude: []
    max_concurrent: 3
    health_check_interval: 600
```

### 模型过滤规则

| mode | 说明 | 配合字段 |
|------|------|----------|
| `all` | 该 provider 所有模型 | `exclude` 可选排除 |
| `pattern` | 正则匹配模型 ID | `pattern` (必需) + `exclude` |
| `include` | 白名单列表 | `include` (必需) + `exclude` |
| `exclude` | 黑名单模式 (其余全收) | `exclude` (必需, 正则) |

示例：
```yaml
# 只收 ID 包含 "free" 且不含 "paid" 的
mode: "pattern"
pattern: ".*free.*"
exclude: [".*paid.*"]

# 只要特定几个
mode: "include"
include: ["claude-3-opus", "gpt-4-turbo"]

# 全要, 但不要某些
mode: "all"
exclude: ["experimental-.*"]
```

### 3. 启动

```bash
cd ~/projects/supersupermodel_router
source venv/bin/activate
python run.py
# → http://0.0.0.0:1298
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/chat/completions` | POST | OpenAI-兼容聊天 |
| `/v1/models` | GET | 列出模型 |
| `/v1/health` | GET | 健康状况 |
| `/v1/admin/routes` | GET | 所有路由 (provider/model) |
| `/v1/admin/stats` | GET | 路由统计 |
| `/v1/admin/refresh` | POST | 刷新模型列表 |
| `/v1/admin/config/reload` | POST | 重载 config.yaml |

## 客户端调用

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:1298/v1",
    api_key="your-secret-key",    # config 里配置的 key
)

# 自动路由: 让 engine 选模型
resp = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Hello"}]
)
print(resp.choices[0].message.content)

# 指定模型: provider/model_id
resp = client.chat.completions.create(
    model="openrouter/anthropic/claude-3.5-sonnet:free",
    messages=[{"role": "user", "content": "Hello"}]
)
```

## CLI 管理工具

```bash
source venv/bin/activate

# 健康检查
python -m supersupermodel_router.cli health

# 列出模型
python -m supermodel_router.cli models
python -m supermodel_router.cli models --provider openrouter

# 列出路由
python -m supermodel_router.cli routes

# 统计
python -m supermodel_router.cli stats

# 刷新模型
python -m supermodel_router.cli refresh

# 自定义地址
python -m supermodel_router.cli --base-url http://other-host:5679 health
```

## 集成到 Hermes

在 Hermes 的 `config.yaml` 中添加 custom_provider:

```yaml
custom_providers:
  supersupermodel_router:
    base_url: "http://localhost:1298/v1"
    api_key: "your-secret-key"

models:
  routing: supersupermodel_router
  default: supersupermodel_router
```

然后切换到 supersupermodel_router provider 即可使用所有聚合模型。