# SuperModel Router — 多 Provider / 多 Key / 智能路由

OpenAI-兼容 API 代理，支持：
- **多 Provider 聚合** — 一个端点背后聚合 N 个 provider
- **每 Provider 多 API Key** — 自动轮询 (round-robin)
- **灵活模型过滤** — pattern / include / exclude / all
- **自动模型发现** — 定期拉 `/v1/models` 按规则过滤
- **健康追踪 + 自动恢复** — failover + degraded + recovery
- **可热重载配置** — 改 yaml 自动生效
- **全 OpenAI-兼容** — 无缝接入任何 OpenAI SDK

---

## 🪟 Windows 安装 (推荐)

### 快速安装 (5 分钟)

```powershell
# 1. 以管理员运行 PowerShell
# 2. 解压 SuperModelRouter_v1.0.0.zip
# 3. 运行构建
.\build_package.ps1
# 4. 安装服务（开机自启）
.\install.ps1
```

### 手动构建

```powershell
# 1. 克隆项目
git clone https://github.com/kuroko-love/supermodel_router.git
cd supermodel_router

# 2. 一键构建 (自动装依赖+PyInstaller打包)
.\deploy\build_package.ps1

# 3. 产物: supermodel_router.exe 在本目录
# 4. 安装为 Windows 服务 (开机自启)
.\deploy\install.ps1

# 或用 run.bat 前台调试
.\deploy\run.bat
```

### 管理命令

```powershell
.\deploy\install.ps1 -Interactive    # 前台运行 (调试用)
.\deploy\install.ps1 -Start          # 启动 Windows 服务
.\deploy\install.ps1 -Stop           # 停止服务
.\deploy\install.ps1 -Uninstall      # 完整卸载
.\deploy\run.bat                     # 前台启动
```

### 访问

```
🌐 API:   http://127.0.0.1:1298/v1
📊 管理:  http://127.0.0.1:1298/admin
```

---

## 🐧 Linux 快速开始

### 1. 安装

```bash
cd ~/projects/supermodel_router
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
  api_key: ""   # 可选, 留空不鉴权

routing:
  strategy: "round-robin"      # round-robin | random | quality
  failover_threshold: 3        # 连续 N 次失败标记 degraded
  recovery_interval: 300       # 5min 后自动恢复
  max_retry: 2
  first_token_timeout_ms: 10000
  retry_backoff_ms: [0, 500]

providers:
  openrouter:
    enabled: true
    base_url: "https://openrouter.ai/api/v1"
    api_keys:
      - "sk-or-v1-xxx"
      - "sk-or-v1-yyy"        # 多个 key 自动轮询
    model_rules:
      mode: "pattern"
      pattern: ".*free.*"
      exclude: []
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

```yaml
# 只收 ID 包含 "free" 且不含 "paid" 的
mode: "pattern"
pattern: ".*free.*"
exclude: [".*paid.*"]

# 只要特定几个
mode: "include"
include: ["claude-3-opus", "gpt-4-turbo"]

# 全要，但不要某些
mode: "all"
exclude: ["experimental-.*"]
```

### 3. 启动

```bash
cd ~/projects/supermodel_router
source venv/bin/activate
python run.py
# → http://0.0.0.0:1298
```

### 4. systemd 服务 (Linux)

```bash
sudo cp deploy/supermodel_router.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now supermodel_router
```

---

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

---

## 客户端调用

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:1298/v1",
    api_key="your-secret-key",
)

# 自动路由
resp = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Hello"}]
)
print(resp.choices[0].message.content)

# 指定 provider/model
resp = client.chat.completions.create(
    model="openrouter/anthropic/claude-3.5-sonnet:free",
    messages=[{"role": "user", "content": "Hello"}]
)
```

## CLI 管理工具

```bash
source venv/bin/activate

# 健康检查
python -m supermodel_router.cli health

# 列出模型
python -m supermodel_router.cli models
python -m supermodel_router.cli models --provider openrouter

# 列出路由
python -m supermodel_router.cli routes

# 刷新模型
python -m supermodel_router.cli refresh

# 自定义地址
python -m supermodel_router.cli --base-url http://other-host:5679 health
```

---

## 集成到 Hermes

```yaml
custom_providers:
  supermodel_router:
    base_url: "http://localhost:1298/v1"
    api_key: "your-secret-key"

models:
  default: supermodel_router
```