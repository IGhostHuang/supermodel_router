# 部署指南 — SMR v3.10.0

> 3 分钟接入 + 完整路径 + 故障排查 + 维护

---

## TL;DR

```bash
# Docker 一键部署 (推荐)
docker-compose up -d
# 验证
curl http://localhost:6473/v1/health
# Web UI
open http://localhost:6473/admin
```

---

## 1. 系统要求

| 需求 | 最低 | 推荐 |
|---|---|---|
| Docker | 20.10+ | 29.5.3+ |
| Compose | v2.0+ | v5.1.4+ |
| 内存 | 512MB | 2GB |
| 磁盘 | 500MB | 2GB |
| 网络 | 能访问 LLM API | 稳定低延迟 |

---

## 2. 快速开始

### 2.1 Docker Compose (推荐)

```bash
cd /root/projects/supermodel_router

# 启动
docker-compose up -d

# 验证
curl http://localhost:6473/v1/health
# → {"status":"ok","version":"3.10.0",...}

# 查看日志
docker-compose logs -f --tail=50

# 停止
docker-compose down
```

### 2.2 PyInstaller 二进制

```bash
cd /root/projects/supermodel_router

# 直接运行
./dist/supermodel_router

# 或指定端口
SMR_PORT=6473 ./dist/supermodel_router
```

### 2.3 源码模式 (开发)

```bash
cd /root/projects/supermodel_router
source venv/bin/activate
python3 run.py
```

---

## 3. 配置

### 3.1 config.yaml

```yaml
# 必填: Provider 列表
providers:
  - name: openrouter
    base_url: https://openrouter.ai/api/v1
    api_keys:
      - sk-or-v1-YOUR_KEY_HERE
    model_rules:
      pattern: .*          # 正则过滤模型
      include: []          # 白名单
      exclude: [.*-legacy.*]  # 黑名单正则

# 路由策略
routing:
  strategy: quality_weighted   # quality_weighted / flat / balanced
  group_strategy: round-robin-group  # v3.10.0 新
  max_retry: 2
  retry_backoff_ms: [0, 500]
  first_token_timeout_ms: 15000

# 服务器
server:
  host: 0.0.0.0
  port: 6473
```

### 3.2 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| SMR_CONFIG | ./config.yaml | 配置文件路径 |
| SMR_PORT | 6473 | 监听端口 |
| SMR_LOG_LEVEL | INFO | 日志级别 |
| SMR_STATS_DIR | ./state | 状态持久化目录 |

---

## 4. API 端点

### 4.1 OpenAI 兼容

| 端点 | 方法 | 说明 |
|---|---|---|
| `/v1/models` | GET | 可用模型列表 |
| `/v1/chat/completions` | POST | 聊天 (stream + non-stream) |
| `/v1/embeddings` | POST | 向量 (如模型支持) |
| `/v1/images/generations` | POST | 生图 (如模型支持) |

### 4.2 管理

| 端点 | 方法 | 说明 |
|---|---|---|
| `/v1/health` | GET | 健康检查 + 版本 |
| `/v1/admin/routes` | GET | 路由表 |
| `/v1/admin/version` | GET | 当前版本 + GitHub 最新 |
| `/v1/admin/upgrade` | POST | 生成升级命令 |
| `/v1/admin/routing` | GET/PUT | 读/写路由配置 |
| `/v1/admin/stats` | GET | provider 统计 |

### 4.3 Web UI

`/admin` → Dashboard (路由表 / Provider 状态 / 版本管理 / 分组向导)

---

## 5. 故障排查

### 5.1 启动失败

| 症状 | 原因 | 解决 |
|---|---|---|
| `Address already in use` | 端口被占 | `lsof -i:6473` 找进程, kill 或换端口 |
| `ModuleNotFoundError` | 没进 venv | `source venv/bin/activate` |
| `Connection refused` | Provider API 不通 | 检查 base_url / 网络 |

### 5.2 请求 500

| 症状 | 原因 | 解决 |
|---|---|---|
| 所有请求 500 | Provider key 全过期 | 检查 api_keys |
| 偶尔 500 | 上游限流 | 检查 429 / retry 配置 |
| 超时 | 上游慢 | 调大 `first_token_timeout_ms` |

### 5.3 版本管理 403

| 症状 | 原因 | 解决 |
|---|---|---|
| `/v1/admin/version` 报 403 | GitHub API rate limit | 等 1h 或配 GITHUB_TOKEN |

---

## 6. 维护

### 6.1 备份

```bash
# 备份状态 (provider stats + penalty)
tar czf smr-state-backup.tar.gz ./state/

# 备份配置
cp config.yaml config.yaml.bak.$(date +%Y%m%d)
```

### 6.2 升级

```bash
# 方法 1: Docker
docker-compose pull && docker-compose up -d

# 方法 2: 二进制
curl -o dist/supermodel_router <新二进制URL>
chmod +x dist/supermodel_router
# 重启 (systemd 自动重启 或手动)

# 方法 3: 源码
git pull && pip install -r requirements.txt
```

### 6.3 日志

```bash
# 实时日志
tail -f logs/smr.log

# Docker
docker-compose logs -f --tail=100

# systemd
journalctl -u supermodel_router -f
```

---

## 7. 架构图

```
Client → :6473 → Routing Engine → Provider A (key rotation)
                                    → Provider B (failover)
                                    → Provider C (retry chain)
                   ↓
              Admin UI /admin
              Admin API /v1/admin/*
              版本管理 /v1/admin/version + /upgrade
```

---

v3.10.0 · 2026-06-19 · echo 一气呵成
