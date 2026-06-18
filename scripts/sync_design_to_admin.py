#!/usr/bin/env python3
"""
sync_design_to_admin.py — 把 docs/SMR-design.html 同步到 admin 静态目录

用途: 让 docker 部署后, /design 端点能直接 serve 最新设计文档

使用:
  python scripts/sync_design_to_admin.py
  python scripts/sync_design_to_admin.py --src docs/SMR-design.html --dst /app/docs/SMR-design.html
  python scripts/sync_design_to_admin.py --check   # 校验, 不写

集成到 docker build:
  Dockerfile 加 RUN python scripts/sync_design_to_admin.py --dst /app/docs/SMR-design.html

退出码:
  0 = 同步成功
  1 = 源文件不存在
  2 = 目标写入失败
"""
import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DEFAULT_SRC = REPO_ROOT / "docs" / "SMR-design.html"
DEFAULT_DST = REPO_ROOT / "supermodel_router" / "static" / "SMR-design.html"


def main():
    ap = argparse.ArgumentParser(description="SMR 设计文档同步到 admin")
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC,
                    help=f"源文件 (默认: {DEFAULT_SRC})")
    ap.add_argument("--dst", type=Path, default=DEFAULT_DST,
                    help=f"目标文件 (默认: {DEFAULT_DST}, dev 模式) / "
                         f"docker 部署建议用 /app/docs/SMR-design.html")
    ap.add_argument("--check", action="store_true", help="校验, 不写")
    args = ap.parse_args()

    src: Path = args.src.resolve()
    dst: Path = args.dst.resolve()

    print(f"src: {src}")
    print(f"dst: {dst}")

    # 1. 校验源存在
    if not src.exists():
        print(f"⚠️  WARN: {src} 不存在, 跳过 sync", file=sys.stderr)
        sys.exit(0)

    src_size = src.stat().st_size
    src_mtime = src.stat().st_mtime
    print(f"源文件: {src_size} bytes, mtime={src_mtime}")

    # 2. check 模式
    if args.check:
        if dst.exists():
            dst_size = dst.stat().st_size
            dst_mtime = dst.stat().st_mtime
            if dst_size == src_size and abs(dst_mtime - src_mtime) < 2:
                print(f"✅ 目标已同步 ({dst_size} bytes)")
                return 0
            else:
                print(f"⚠️  目标过期: dst={dst_size}/{dst_mtime}, src={src_size}/{src_mtime}")
                return 1
        else:
            print(f"⚠️  目标不存在: {dst}")
            return 1

    # 3. 写目标
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)  # copy2 保留 mtime
        print(f"✅ 同步成功: {dst} ({src_size} bytes)")
        return 0
    except Exception as e:
        print(f"❌ 同步失败: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
