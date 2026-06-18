# SMR 升级指南 (v3.10.0+)

## 📦 数据持久化机制

**所有 mutable state 通过 Docker volume 挂载到宿主**, 容器重建/升级 = state 不丢.

### State 文件清单 (v3.10.0)

| 文件 | 用途 | 卷挂载路径 |
|---|---|---|
| `state/public_keys_state.json` | API key 元数据 + 绑定 group | `./state:/app/state` |
| `state/model_rules_state.json` | 模型过滤规则 | `./state:/app/state` |
| `state/model_metadata.json` | 模型元数据 (v3.10.0 新增, seed 由 image 内置) | `./state:/app/state` |
| `state/penalty_state.json` | 限流 penalty state | `./state:/app/state` |
| `state/engine_stats.json` | 引擎运行统计 (非关键) | `./state:/app/state` |
| `.backups/` | 配置/版本备份 | `./.backups:/app/.backups` |
| `config.yaml` | 主配置 | `./config.yaml:/app/config.yaml:ro` |
| `logs/` | 容器日志 | `./logs:/app/logs` |

**核心原则**: 这些路径全部**不进 image** (`.dockerignore` 已锁), 升级 = 重建 image + 复用旧卷.

---

## 🚀 标准升级流程

### 🅰️ 同端口升级 (推荐, 客户端 0 改动)

```bash
# 1. 升级前快照 (重要!)
mkdir -p .backups/pre-v3.10.0-upgrade-$(date +%Y%m%d-%H%M%S)
SNAP=$(ls -td .backups/pre-v3.10.0-upgrade-* | head -1)
cp -a state/ docs/ "$SNAP/"
cp config.yaml "$SNAP/config.yaml.bak"
echo "✅ Snapshot: $SNAP"

# 2. 拉新代码
git pull origin main  # 或 git pull origin HEAD (sibling commit 安全)

# 3. 重建镜像 (state 卷不会被覆盖)
docker compose build --no-cache

# 4. 重启容器 (复用 state 卷)
docker compose up -d

# 5. 端到端验真
curl -s http://localhost:6473/v1/health | jq
curl -s http://localhost:6473/v1/admin/version | jq

# 6. 验 state 还在
ls -la state/  # public_keys_state.json 应保留
curl -s http://localhost:6473/v1/admin/api-keys | jq '.keys | length'  # 应等于升级前数量
```

### 🅱️ 蓝绿升级 (大版本, 想 0 中断)

```bash
# 1. 启 v3.10.0 在 :6475 (临时)
docker run -d \
  --name smr-v310-green \
  -p 6475:6473 \
  -v $(pwd)/state:/app/state \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  supermodel_router:v3.10.0

# 2. 验证绿环境
curl -s http://localhost:6475/v1/health

# 3. 切流量 (改前端 .env SMR_BASE_URL)
# 等待所有 in-flight 请求结束 (10-30s)

# 4. 停旧容器
docker stop supermodel_router  # v3.9.0 蓝
docker rm supermodel_router

# 5. 启动 v3.10.0 在 :6473 (从绿环境复用 state)
docker run -d \
  --name supermodel_router \
  -p 6473:6473 \
  -v $(pwd)/state:/app/state \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  supermodel_router:v3.10.0
```

---

## 🔑 真 API Key 注入 (3 种方式)

### 方式 1: Docker secrets (推荐, v3.10.0 entrypoint 自动渲染)

```bash
# 1. 创建 secrets 目录
mkdir -p secrets/

# 2. 写入真 key (chmod 600)
echo -n "sk-or-v1-..." > secrets/openrouter
chmod 600 secrets/openrouter

# 3. config.yaml 用占位符 (R27):
#    api_key: <OPENROUTER_API_KEY_PLACEHOLDER>

# 4. docker-compose.yml 加:
#    volumes:
#      - ./secrets:/run/secrets:ro

# 5. 启动: entrypoint 自动从 /run/secrets/openrouter 读真 key 替换占位符
```

### 方式 2: 环境变量 (开发/测试)

```bash
docker run -d \
  -e OPENROUTER_API_KEY="sk-or-v1-..." \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  supermodel_router:v3.10.0
```

### 方式 3: config.yaml 直写 (⚠️ 不推荐, 易泄漏)

直接 `api_key: sk-or-v1-...` 写进 config.yaml, **风险**: push protection 拦截 / 误 commit 进 git / 容器导出时被读.

---

## 🔄 老 state 自动迁移 (v3.10.0 entrypoint)

升级到 v3.10.0 时, entrypoint 会自动:

1. **检查 `.initialized` 标记**: 没有 → 首次启动, seed model_metadata.json
2. **有标记**: 跑迁移脚本, 字段补全:
   - `model_metadata.json`: 加 `_version` 字段 + 每个 model 加 `metadata_source` 字段
   - 其他 state 文件: **不动** (向后兼容 v3.9.0)
3. **失败跳过**: 单文件迁移失败不阻塞启动

手动触发迁移:
```bash
docker exec supermodel_router python3 -c "
import json, os
# ... 迁移逻辑见 docker-entrypoint.sh
"
```

---

## 🔙 回滚 SOP

如果 v3.10.0 升级后出问题:

```bash
# 1. 停新容器
docker compose down

# 2. 切回旧 image (从备份)
SNAP=$(ls -td .backups/pre-v3.10.0-upgrade-* | head -1)
echo "回滚到: $SNAP"

# 3. 回滚 config.yaml (state 通常不用动, 向后兼容)
cp "$SNAP/config.yaml.bak" config.yaml

# 4. 重启旧 image
docker tag supermodel_router:v3.9.0 supermodel_router:latest
docker compose up -d

# 5. 验真
curl -s http://localhost:6473/v1/health | jq '.version'
# 应返回 "3.9.0"
```

**关键**: state 文件**不删**, v3.9.0 也能读 v3.10.0 写的 state (向后兼容).

---

## 🐛 升级失败排查

| 症状 | 排查 |
|---|---|
| 容器起不来 | `docker logs supermodel_router 2>&1 \| tail -50` 看 entrypoint 报错 |
| 配置没生效 | `docker exec supermodel_router cat /app/config.yaml` 看 secret 是否渲染成功 |
| state 丢失 | `docker exec supermodel_router ls -la /app/state` 检查卷挂载 |
| 旧 key 不工作 | v3.10.0 可能改了 key hash 算法, 检查 `/v1/admin/api-keys` |
| 模型元数据空 | `docker exec supermodel_router cat /app/state/model_metadata.json` |

---

## 📊 升级前检查清单

- [ ] 升级前快照已做 (`.backups/pre-vX.Y.Z-upgrade-*/`)
- [ ] git working tree 干净 (`git status`)
- [ ] 当前 image tag 已记录 (`docker images | grep supermodel_router`)
- [ ] 当前 :6473 容器 ID 已知 (`docker ps --filter "name=supermodel_router"`)
- [ ] secrets/ 目录已 chmod 600 (用 secrets 方式时)
- [ ] 占位符替换完毕 (grep `<.*_API_KEY_PLACEHOLDER>` config.yaml)

---

## 🎯 v3.10.0 升级特别提示

**新增 state 文件**: `state/model_metadata.json`

**新增端点**: 4 个 (wizard 相关, 已在 Phase L/M 列表)

**新增模型元数据**: 21 个模型, 人工标注 (claude / gpt-4o / gemini / deepseek 等)
