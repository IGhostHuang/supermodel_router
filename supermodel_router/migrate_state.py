"""
supermodel_router/migrate_state.py — v3.6.0 状态迁移

从老版本 (v3.5.x 及以前, CWD 散落文件) 迁移到 v3.6.x 统一 STATE_DIR.

使用:
    python -m supermodel_router.migrate_state
    SMR_STATE_DIR=/app/state python -m supermodel_router.migrate_state
"""
import os
import shutil
import sys
from pathlib import Path

OLD_LOCATIONS = [
    "model_rules_state.json",
    "model_rules.json",
    "state.json",
    "stats.json",
    "penalty_state.json",
]
NEW_STATE_DIR = Path(os.getenv("SMR_STATE_DIR", "./state"))


def main() -> int:
    """执行迁移. return 0 成功, 1 失败"""
    try:
        NEW_STATE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"❌ 无法创建 state 目录 {NEW_STATE_DIR}: {e}")
        return 1
    migrated = []
    skipped = []
    for fname in OLD_LOCATIONS:
        src = Path(fname)
        if not src.exists():
            continue
        dst = NEW_STATE_DIR / fname
        if dst.exists():
            skipped.append((str(src), str(dst), "dst already exists"))
            continue
        try:
            shutil.copy(str(src), str(dst))
            migrated.append(str(src))
        except OSError as e:
            print(f"❌ copy {src} → {dst} 失败: {e}")
            return 1
    if not migrated and not skipped:
        print(f"no migration needed (no old files found in CWD={os.getcwd()})")
        return 0
    log_path = NEW_STATE_DIR / "migrate.log"
    import datetime
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            ts = datetime.datetime.now().isoformat(timespec="seconds")
            f.write(f"\n[{ts}] migrated={len(migrated)} skipped={len(skipped)}\n")
            for s in migrated:
                f.write(f"  ✅ {s}\n")
            for s, d, reason in skipped:
                f.write(f"  ⏭️ {s} → {d} ({reason})\n")
    except OSError as e:
        print(f"⚠️ 写 log 失败: {e}")
    print(f"✅ migrated {len(migrated)} files. skipped {len(skipped)} files. log → {log_path}")
    for s in migrated:
        print(f"  ✅ {s} → {NEW_STATE_DIR / Path(s).name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
