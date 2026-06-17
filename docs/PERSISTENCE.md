# SMR v3.6.0 持久化数据复盘

> **核心承诺**: 用户不应该每次升级 / 重启 / 部署新镜像 都重新配置 provider / API key / 路由规则。
> 本文盘点哪些数据**必须持久化**、**存在哪里**、**如何迁移**。

---

## 1. 持久化数据矩阵 (v3.6.0)

| 数据类型 | 存盘位置 | 写入时机 | 读取时机 | 升级影响 |
|---|---|---|---|---|
| **provider 配置** (含 api_keys, base_url, model_rules) | `config.yaml` (yaml 格式) | add/update/clone/import 立刻写盘 | 启动时 + `/v1/admin/config/reload` | ⚠️ 改镜像启动命令可能丢, **volume 必须挂** |
| **config 自动备份** (历史 50 份) | `config_backups/config_<ts>.yaml` | add/update 前自动备份 | `/v1/admin/config/backups` | ✅ 历史保留, 不影响升级 |
| **classifier 配置** (tier_bonus/keywords) | `config.yaml` 的 `classifier` 段 | `/v1/admin/classifier` PUT | 启动时 + 实时 | ⚠️ 同上 |
| **provider 状态** (enabled True/False) | `config.yaml` 的 `providers.X.enabled` | enable/disable API 立刻写盘 | 启动时 | ⚠️ 软删除状态保留 |
| **model_rules state** (allow/deny list) | `state/model_rules_<provider>.json` | UI/DevTools add/remove | 启动时 + reload | ✅ 独立 state_dir |
| **stats** (累计 calls/success/fail) | 内存 only | 实时更新 | `/v1/admin/stats` API | ❌ 重启清零 (按设计) |
| **provider model discovery cache** | 内存 only | 后台 refresh 异步 | registry in-memory | ❌ 重启后自动重新拉 |
| **penalty / EWMA state** | 内存 only | 实时更新 | engine in-memory | ❌ 重启重置 |

---

## 2. 升级时"哪些会丢" — 风险等级

| 等级 | 数据 | 原因 | 缓解方案 |
|---|---|---|---|
| 🔴 高 | **providers + api_keys + classifier** | `config.yaml` 没挂 volume → 容器重启 = 全部丢失 | docker-compose 必须挂 `.:/app/config` |
| 🟡 中 | **model_rules_state.json** | 路径可能在 CWD (沙盒), 不在 STATE_DIR | 启动时跑 `migrate_state.py` (见 §4) |
| 🟢 低 | stats / cache / penalty | 内存数据, 设计如此 | 不可恢复, 但不影响主流程 |

---

## 3. Docker 部署 — 必须挂载的 volume

```yaml
# docker-compose.yml (v3.6.0 已正确配置)
services:
  supermodel_router:
    volumes:
      - ./config.yaml:/app/config/config.yaml:ro    # 主配置 (只读, 防误改)
      - ./config_backups:/app/config/config_backups # 自动备份目录
      - ./state:/app/state                            # v3.6 新增 state 目录
```

**反模式 (会丢数据)**:
```yaml
# ❌ 不挂 config.yaml → 容器重启丢所有 provider
services:
  supermodel_router:
    # 没 volumes 段
```

---

## 4. 状态迁移脚本 `migrate_state.py`

从老版本 (v3.5.x 及以前, CWD 散落文件) 迁移到 v3.6.x (统一 STATE_DIR):

```python
# supermodel_router/migrate_state.py — v3.6.0
import os
import shutil
import json
from pathlib import Path

OLD_LOCATIONS = [
    "model_rules_state.json",
    "model_rules.json",
    "state.json",
]
OLD_CWD_PREFIX = "./"  # 老版本默认在 CWD
NEW_STATE_DIR = Path(os.getenv("SMR_STATE_DIR", "./state"))

def main():
    NEW_STATE_DIR.mkdir(parents=True, exist_ok=True)
    migrated = []
    for fname in OLD_LOCATIONS:
        src = Path(OLD_CWD_PREFIX) / fname
        if not src.exists():
            continue
        dst = NEW_STATE_DIR / fname
        if dst.exists():
            print(f"skip {fname}: already in {dst}")
            continue
        shutil.copy(src, dst)
        migrated.append(str(src))
        print(f"✅ migrated: {src} → {dst}")
    if not migrated:
        print("no migration needed (no old files found)")
        return
    # 写 migration log
    log_path = NEW_STATE_DIR / "migrate.log"
    with open(log_path, "a") as f:
        f.write(f"\n[{os.popen('date').read().strip()}] migrated {len(migrated)} files:\n")
        for f_path in migrated:
            f.write(f"  - {f_path}\n")
    print(f"✅ migrated {len(migrated)} files. log → {log_path}")

if __name__ == "__main__":
    main()
```

**使用方式**:
```bash
# 容器启动时自动跑 (或手动跑)
python -m supermodel_router.migrate_state

# 或 (源码模式)
python migrate_state.py
```

---

## 5. 启动检查清单 (部署前必读)

```bash
# 1) config.yaml 存在且含至少 1 个 provider
[ -f config.yaml ] && grep -q "^providers:" config.yaml || echo "❌ config.yaml missing or empty"

# 2) config_backups 目录可写 (用于自动备份)
mkdir -p config_backups && touch config_backups/.test && rm config_backups/.test

# 3) state 目录可写
mkdir -p state && touch state/.test && rm state/.test

# 4) (可选) 跑迁移脚本
python -m supermodel_router.migrate_state
```

---

## 6. 备份与恢复策略

### 6.1 自动备份
- 每次 `add/update/enable/disable/clone/import` 前, 备份当前 `config.yaml` 到 `config_backups/config_<unix_ts>.yaml`
- 保留 50 份 (滚动), 超出删除最老的

### 6.2 手动备份 (推荐: 升级前)
```bash
cp config.yaml config.yaml.pre-v3.6.bak
```

### 6.3 恢复
- **UI**: `/v1/admin/config/backups` 看历史, 点击 restore
- **API**: `POST /v1/admin/config/restore?backup=<filename>`
- **CLI**: `cp config_backups/config_<ts>.yaml config.yaml` + 重启

### 6.4 跨机迁移
```bash
# 旧机
smr-cli export --include-keys > providers_$(date +%F).json

# 新机
# 1) 启动 SMR (默认空配置)
# 2) UI → Providers → 📥 导入 → 选 json → 填真 api_key → 提交
# ⚠️ include_keys=True 导出的 json 含真实 key, 妥善保管
```

---

## 7. v3.6.0 新增的持久化能力

| 新增 | API | 说明 |
|---|---|---|
| **Provider 导出** | `GET /v1/admin/providers/export?include_keys=false` | 导出配置 (key 默认 REDACTED) |
| **Provider 导入** | `POST /v1/admin/providers/import` | 批量导入 (key 不能 REDACTED) |
| **Provider 复制** | `POST /v1/admin/providers/{name}/clone` | 复制为新 name (key 用占位) |
| **API Key 独立管理** | `GET/POST/DELETE /v1/admin/api-keys` | 不动 provider 配置, 只改 api_keys |
| **state_dir 集中** | env `SMR_STATE_DIR` | model_rules state 统一位置 |

---

## 8. 常见问题

**Q1: 容器重启后, 添加的 provider 全没了?**
A: `docker-compose.yml` 没挂 `./config.yaml:/app/config/config.yaml`, 修改 compose 后 `docker compose up -d`。

**Q2: model_rules_state.json 在哪?**
A: v3.6.0 起默认在 `state/` 目录。环境变量 `SMR_STATE_DIR=/app/state` 改位置。老版本散落在 CWD, 跑 `migrate_state.py`。

**Q3: API key 在哪存储?**
A: provider 的 `api_keys: [...]` 列表里, 写在 `config.yaml` 中。**包含真实 key**, 文件 chmod 600。

**Q4: 导出 json 后, 另一个机器导入提示 "REDACTED"?**
A: 安全设计 — 导出的 key 默认 REDACTED, 必须填真 key 才能导入。导出时加 `?include_keys=true` 含真 key (高风险)。

**Q5: stats 累计数据为什么重启后清零?**
A: 按设计 — stats 是 runtime memory 数据, 不持久化。需要持久化请加 `/v1/admin/stats/persist` 端点 (v3.7+ 候选)。

---

## 9. 版本演进路线

- **v3.4.x**: config 在 CWD (`./config.yaml`), state 散落文件
- **v3.5.0**: 加 context_review / context_bridge
- **v3.6.0** (当前):
  - 加 `_normalize_base_url` (URL 自动补全)
  - 加 state_dir 集中配置
  - reload 默认 mode=memory (不覆盖)
  - api-keys 独立管理
  - provider clone / export / import
  - sidebar nav + UI 全面改版
- **v3.7 候选**:
  - stats 持久化 (sqlite)
  - 多租户 (per-user api_keys)
  - 自动备份到远端 (S3/webdav)