"""
package_windows.py — 创建 Windows 安装包 (ZIP)
在 WSL/Linux 上打包, 到 Windows 解压即用
不交叉编译 .exe (需在 Windows 上运行 build_package.ps1)
"""
import os
import shutil
import zipfile
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
OUTPUT_DIR = PROJECT_DIR / "dist_windows"
PACKAGE_DIR = OUTPUT_DIR / "SuperModelRouter_v1.0.0"


def collect_source_files():
    """收集源码文件 (不含 venv/build/dist/__pycache__)"""
    files = []
    for root, dirs, names in os.walk(PROJECT_DIR):
        # 排除
        rel = Path(root).relative_to(PROJECT_DIR)
        parts = rel.parts
        if any(p in parts for p in ("venv", "build", "__pycache__", ".git", ".gitignore")):
            continue

        for name in names:
            fpath = Path(root) / name
            # 只保留需要分发的文件
            ext = fpath.suffix.lower()
            if ext in (".py", ".yaml", ".json", ".md", ".txt",
                       ".bat", ".ps1", ".service", ".example",
                       ".dockerignore", ".gitignore"):
                files.append(fpath)
            elif name in ("Dockerfile", "docker-compose.yml", "requirements.txt", "run.py", "run_smr_pyinstaller.py", "smr.spec"):
                files.append(fpath)
            elif name == "build_windows.py":
                files.append(fpath)
    return files


def build_package():
    """创建完整分发目录"""
    if PACKAGE_DIR.exists():
        shutil.rmtree(PACKAGE_DIR)

    # 源码
    src_dir = PACKAGE_DIR / "source"
    for fpath in collect_source_files():
        dst = src_dir / fpath.relative_to(PROJECT_DIR)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fpath, dst)

    # deploy/ 到根目录 (方便直接使用)
    deploy_src = PROJECT_DIR / "deploy"
    if deploy_src.exists():
        for item in deploy_src.iterdir():
            dst = PACKAGE_DIR / item.name
            if item.is_file():
                shutil.copy2(item, dst)
            elif item.is_dir():
                shutil.copytree(item, dst)

    # build_package.ps1 放根目录
    shutil.copy2(
        PROJECT_DIR / "deploy" / "build_package.ps1" if (PROJECT_DIR / "deploy" / "build_package.ps1").exists() else PROJECT_DIR / "build_windows.py",
        PACKAGE_DIR / "build_package.ps1" if (PROJECT_DIR / "deploy" / "build_package.ps1").exists() else PACKAGE_DIR / "build_windows.py"
    )

    return PACKAGE_DIR


def create_zip(package_dir):
    """打 ZIP 包"""
    zip_path = package_dir.parent / f"{package_dir.name}.zip"
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, names in os.walk(package_dir):
            for name in names:
                fpath = Path(root) / name
                arcname = str(fpath.relative_to(package_dir.parent))
                zf.write(fpath, arcname)

    return zip_path


if __name__ == "__main__":
    print("📦 打包 SuperModel Router Windows 安装包...")

    pkg = build_package()
    print(f"✅ 分发目录: {pkg}")

    # 文件计数
    py_count = len(list(pkg.rglob("*.py")))
    ps1_count = len(list(pkg.rglob("*.ps1")))
    total = sum(1 for _ in pkg.rglob("*") if _.is_file())
    print(f"   源码: {py_count} .py / {ps1_count} .ps1 / 共 {total} 文件")

    # ZIP
    zip_file = create_zip(pkg)
    size_mb = zip_file.stat().st_size / (1024 * 1024)
    print(f"✅ ZIP 包: {zip_file} ({size_mb:.1f} MB)")

    print("\n" + "=" * 60)
    print("  Windows 安装步骤:")
    print("  1. 解压 SuperModelRouter_v1.0.0.zip")
    print("  2. 以管理员运行 PowerShell → build_package.ps1")
    print("     (自动安装 Python 依赖 + PyInstaller 打包)")
    print("  3. 或直接运行: install.ps1")
    print("=" * 60)