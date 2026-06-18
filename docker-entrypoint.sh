#!/bin/sh
# docker-entrypoint.sh — SMR v3.10.0 容器入口
# 职责:
#   1. 首次启动: 初始化 state (model_metadata 从预置 data 灌入)
#   2. 老 state 自动迁移 (字段补全)
#   3. 渲染真 API key 到 config.yaml (占位符 → 环境变量)
#   4. 启动 SMR

set -e

DATA_DIR="${DATA_DIR:-/app/data}"
STATE_DIR="${STATE_DIR:-/app/state}"
CONFIG_FILE="${CONFIG_FILE:-/app/config.yaml}"
LOG_PREFIX="[entrypoint]"

echo "$LOG_PREFIX SMR v3.10.0 entrypoint"
echo "$LOG_PREFIX DATA_DIR=$DATA_DIR"
echo "$LOG_PREFIX STATE_DIR=$STATE_DIR"
echo "$LOG_PREFIX CONFIG_FILE=$CONFIG_FILE"

# 1. 首次启动: 初始化 state
mkdir -p "$STATE_DIR" "$DATA_DIR/.backups"

if [ ! -f "$STATE_DIR/.initialized" ]; then
    echo "$LOG_PREFIX 🆕 First run: initializing state..."

    # 1a. 预置 model_metadata.json (image 内置 default, 可被卷挂载覆盖)
    if [ -f /app/data/seed/model_metadata.json ] && [ ! -f "$STATE_DIR/model_metadata.json" ]; then
        cp /app/data/seed/model_metadata.json "$STATE_DIR/model_metadata.json"
        echo "$LOG_PREFIX ✅ Seeded model_metadata.json"
    fi

    # 1b. 初始化空 state (如果不存在)
    for f in public_keys_state.json penalty_state.json model_rules_state.json engine_stats.json; do
        if [ ! -f "$STATE_DIR/$f" ]; then
            case "$f" in
                *_state.json|engine_stats.json) echo '{}' > "$STATE_DIR/$f" ;;
                *) echo '{}' > "$STATE_DIR/$f" ;;
            esac
            echo "$LOG_PREFIX ✅ Created $f"
        fi
    done

    touch "$STATE_DIR/.initialized"
    echo "$LOG_PREFIX ✅ State initialized"
else
    echo "$LOG_PREFIX ✓ State already initialized"

    # 2. 老 state 自动迁移 (字段补全 — 失败跳过)
    python3 -c "
import json, os, sys
state_dir = '$STATE_DIR'
migrated = []

# v3.9.0: model_metadata.json: 老 schema 兼容 (老 v3.9.0 写 {version, updated_at, models: {...}})
#                          + 新 schema 平铺 {model_id: metadata, _version: "3.10.0"}
mm_path = os.path.join(state_dir, 'model_metadata.json')
if os.path.exists(mm_path):
    try:
        with open(mm_path) as f:
            mm = json.load(f)
        migrated_any = False
        # 老 schema 检测: 有 'version' + 'models' 嵌套
        if isinstance(mm, dict) and 'version' in mm and 'models' in mm and isinstance(mm.get('models'), dict):
            old_models = mm['models']
            mm_flat = {'_version': '3.10.0'}
            for mid, m in old_models.items():
                if isinstance(m, dict):
                    m.setdefault('metadata_source', 'migrated_from_v390')
                mm_flat[mid] = m
            with open(mm_path, 'w') as f:
                json.dump(mm_flat, f, indent=2, ensure_ascii=False)
            migrated.append(f'model_metadata.json:migrated v3.9.0 schema -> v3.10.0 flat ({len(old_models)} entries)')
            migrated_any = True
        # 新 schema 字段补全
        if isinstance(mm, dict) and '_version' in mm:
            for mid, m in mm.items():
                if mid.startswith('_'): continue
                if isinstance(m, dict) and 'metadata_source' not in m:
                    m['metadata_source'] = 'seed'
                    migrated_any = True
            if migrated_any:
                with open(mm_path, 'w') as f:
                    json.dump(mm, f, indent=2, ensure_ascii=False)
                migrated.append('model_metadata.json:filled metadata_source for flat entries')
    except Exception as e:
        print(f'[migration] model_metadata.json skipped: {e}', file=sys.stderr)

# model_rules_state.json: 不动 (向后兼容 v3.9.0)
# public_keys_state.json: 不动
# penalty_state.json: 不动

if migrated:
    print('🔄 Migrated:', '; '.join(migrated))
else:
    print('✓ No migration needed')
" || echo "$LOG_PREFIX ⚠️ Migration script failed (non-fatal)"
fi

# 3. 渲染真 API key 到 config.yaml (占位符 → 环境变量)
#    占位符格式: <PROVIDER_NAME_API_KEY_PLACEHOLDER>
if [ -f "$CONFIG_FILE" ] && [ -d /run/secrets ]; then
    echo "$LOG_PREFIX 🔑 Rendering secrets from /run/secrets..."
    python3 -c "
import os, re
config_path = '$CONFIG_FILE'
secret_dir = '/run/secrets'

with open(config_path) as f:
    content = f.read()

# 匹配 <XXX_API_KEY_PLACEHOLDER> 形式
pattern = re.compile(r'<([A-Z_]+)_API_KEY_PLACEHOLDER>')
matches = set(pattern.findall(content))
rendered = 0
for var in matches:
    # 兼容多种命名风格
    candidates = [var, var.lower(), var.replace('_API_KEY', ''), var.lower().replace('_api_key', ''),
                  var.lower().replace('_api_key_placeholder', '')]  # also try without _PLACEHOLDER suffix
    secret_value = None
    for cand in candidates:
        secret_path = os.path.join(secret_dir, cand)
        if os.path.exists(secret_path):
            with open(secret_path) as sf:
                secret_value = sf.read().strip()
            break
        env_val = os.environ.get(cand) or os.environ.get(var)
        if env_val:
            secret_value = env_val
            break

    if secret_value:
        content = content.replace(f'<{var}_API_KEY_PLACEHOLDER>', secret_value)
        rendered += 1
        print(f'  ✓ Rendered {var}')
    else:
        print(f'  ⚠️ No secret found for {var} (candidates: {candidates})', flush=True)

if rendered > 0:
    with open(config_path, 'w') as f:
        f.write(content)
    print(f'🔑 Rendered {rendered} secrets into {config_path}')
else:
    print('✓ No placeholders to render')
" || echo "$LOG_PREFIX ⚠️ Secret rendering failed (non-fatal, SMR may start without keys)"
fi

# 4. 启动 SMR
echo "$LOG_PREFIX 🚀 Starting SMR..."
exec python3 run.py \
    --config "$CONFIG_FILE" \
    --host "${HOST:-0.0.0.0}" \
    --port "${PORT:-6473}" \
    --log-level "${LOG_LEVEL:-INFO}"
