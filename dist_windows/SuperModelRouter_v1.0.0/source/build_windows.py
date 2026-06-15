"""
build_windows.py — 使用 PyInstaller 打包 supermodel_router 为 Windows 单 .exe
用法: python build_windows.py
"""

import os
import sys
import shutil
import subprocess

OUTPUT_DIR = "dist_windows"
EXE_NAME = "supermodel_router.exe"


def build():
    # 清理旧构建
    for d in ["build", OUTPUT_DIR]:
        if os.path.exists(d):
            shutil.rmtree(d)

    # PyInstaller 参数
    args = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",              # 单文件 exe
        "--name", "supermodel_router",
        "--distpath", OUTPUT_DIR,
        "--workpath", "build",
        "--specpath", "build",
        "--add-data", f"config.yaml{os.pathsep}.",
        "--add-data", f"test_config.yaml{os.pathsep}.",
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "fastapi",
        "--hidden-import", "pydantic",
        "--hidden-import", "httpx",
        "--hidden-import", "yaml",
        "--collect-all", "supermodel_router",
        # 控制台窗口（运行时可看到日志，也可以用 --noconsole 隐藏）
        "--console",
        "run_pyinstaller.py",
    ]

    # Windows 平台额外参数
    if sys.platform == "win32":
        args.extend(["--icon", "NONE"])

    print("🚀 Building supermodel_router.exe ...")
    subprocess.check_call(args)

    # 验证产物
    exe_path = os.path.join(OUTPUT_DIR, EXE_NAME)
    if os.path.exists(exe_path):
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print(f"✅ 构建成功: {exe_path} ({size_mb:.1f} MB)")
    else:
        print("❌ 构建失败: exe 未生成")
        sys.exit(1)

    print("\n📦 打包内容:")
    for f in os.listdir(OUTPUT_DIR):
        fpath = os.path.join(OUTPUT_DIR, f)
        if os.path.isfile(fpath):
            print(f"   {f:40s} {os.path.getsize(fpath)/1024:.1f} KB")

    return exe_path


if __name__ == "__main__":
    build()