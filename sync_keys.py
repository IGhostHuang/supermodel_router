#!/usr/bin/env python3
"""sync_keys.py — push 时自动替换 config.yaml 真 key → 占位符 (R56 实战坑)

触发: git commit / pre-push hook 自动跑
作用: 把 WSL 端 config.yaml 里的真 key 替换成 <X_API_KEY_PLACEHOLDER> 形式
       让 commit / push 不含真 key (绕过 GH push protection)
       push 之后反向 sync 回真 key (部署用)

用法:
  1. push 前 (本地): python3 sync_keys.py --to-placeholders → config.local.yaml (含真 key) 备份到 ~/.smr-tmp/
  2. push 前 (本地): python3 sync_keys.py --from-local --to-placeholders → config.yaml 替换成占位符
  3. push (含占位符)
  4. push 后 (本地): python3 sync_keys.py --from-backup → config.yaml 还原真 key
  5. (CI/CD): deploy 时 sync_keys.py --from-placeholders --to-local --env=<GH_SECRETS>

老大 6/22 12:48 钦定: "本地 docker 用真 key, push 时替换成示例, 不然咋测试咋调试"
"""
import os, re, sys, shutil, argparse, subprocess
from pathlib import Path
from datetime import datetime

# 关键 KEY 模式 (跟 R27/R28/R29 一致)
KEY_PATTERNS = [
    # OpenRouter
    (r"sk-or-v1-[A-Za-z0-9_\-]+", "OPENROUTER_API_KEY"),
    (r"sk-or-[A-Za-z0-9_\-]+", "OPENROUTER_API_KEY"),  # 老格式
    # VolcEngine ark
    (r"ark-[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}-[a-f0-9]+", "VOLC_ARK_API_KEY"),
    # 通用
    (r"sk-[A-Za-z0-9_\-]{20,}", "API_KEY"),
    (r"sk-ant-[A-Za-z0-9_\-]+", "ANTHROPIC_API_KEY"),
    (r"sk-[A-Za-z0-9]{32,}", "OPENAI_API_KEY"),
    # NVIDIA / ModelScope / Cloudflare
    (r"nvapi-[A-Za-z0-9_\\-]{20,}", "NVIDIA_API_KEY"),
    (r"ms-[A-Za-z0-9_\\-]{20,}", "MODELSCOPE_API_KEY"),
    (r"cfut_[A-Za-z0-9_\\-]{20,}", "CLOUDFLARE_API_KEY"),
]

BACKUP_DIR = Path("/root/.smr-tmp")
CONFIG_PATH = Path("/root/projects/supermodel_router/config.yaml")
CONFIG_LOCAL = Path("/root/projects/supermodel_router/config.local.yaml")

def find_keys(text: str) -> list[tuple[str, str, str]]:
    """找所有 key, 返回 (pattern_name, matched, key_value)"""
    results = []
    for pat, name in KEY_PATTERNS:
        for m in re.finditer(pat, text):
            results.append((name, m.group(0), m.group(0)[:8] + "..." + m.group(0)[-4:]))
    return results

def to_placeholders(config_path: Path) -> int:
    """真 key → 占位符"""
    text = config_path.read_text(encoding="utf-8")
    original = text
    for pat, name in KEY_PATTERNS:
        text = re.sub(pat, f"<{name}_PLACEHOLDER>", text)
    if text == original:
        print(f"  [无 key 替换] {config_path}")
        return 0
    # 备份含真 key 的版本
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / f"config-{int(datetime.now().timestamp())}.yaml"
    shutil.copy2(config_path, backup)
    backup.chmod(0o600)
    # 写占位符版本
    config_path.write_text(text, encoding="utf-8")
    config_path.chmod(0o644)
    # 统计
    keys = find_keys(original)
    print(f"  ✓ {len(keys)} 真 key → 占位符: {config_path}")
    for name, full, masked in keys:
        print(f"    - {name}: {masked} → <{name}_PLACEHOLDER>")
    print(f"  备份: {backup}")
    return len(keys)

def from_backup(config_path: Path) -> int:
    """占位符 → 真 key (从最近 backup 还原)"""
    if not BACKUP_DIR.exists():
        print(f"  ⚠️ 备份目录不存在: {BACKUP_DIR}")
        return 0
    backups = sorted(BACKUP_DIR.glob("config-*.yaml"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not backups:
        print(f"  ⚠️ 无 backup 文件")
        return 0
    latest = backups[0]
    shutil.copy2(latest, config_path)
    config_path.chmod(0o600)
    keys = find_keys(config_path.read_text(encoding="utf-8"))
    print(f"  ✓ 还原真 key: {config_path} ← {latest}")
    print(f"  含 {len(keys)} 真 key")
    return len(keys)

def audit_keys(paths: list[Path]) -> int:
    """6 维度 grep 验真 (R29)"""
    issues = 0
    for p in paths:
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for pat, name in KEY_PATTERNS:
            matches = re.findall(pat, text)
            if matches:
                for m in matches:
                    masked = m[:8] + "..." + m[-4:] if len(m) > 12 else m
                    print(f"  ⚠️ {p} 含 {name}: {masked}")
                issues += len(matches)
    return issues

def main():
    ap = argparse.ArgumentParser(description="SMR push key sync (R56 实战坑)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--to-placeholders", action="store_true", help="真 key → 占位符 (push 前)")
    g.add_argument("--from-backup", action="store_true", help="占位符 → 真 key (push 后)")
    g.add_argument("--audit", action="store_true", help="6 维度 grep 验真 key")
    ap.add_argument("--path", type=Path, default=CONFIG_PATH)
    args = ap.parse_args()

    print(f"=== sync_keys.py — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    if args.to_placeholders:
        n = to_placeholders(args.path)
        print(f"  [OK] {n} key 已占位符, 可安全 commit/push")
    elif args.from_backup:
        n = from_backup(args.path)
        print(f"  [OK] {n} key 已还原, 可本地测试")
    elif args.audit:
        n = audit_keys([args.path, CONFIG_LOCAL])
        if n == 0:
            print(f"  [OK] 0 真 key 暴露, 安全")
        else:
            print(f"  [FAIL] {n} 真 key 暴露, 立即 REDACT")
            sys.exit(1)

if __name__ == "__main__":
    main()
