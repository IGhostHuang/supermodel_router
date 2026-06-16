"""
free-model-router PyInstaller 入口
打包时 PyInstaller 从这里启动 fmr (用 fmr 自己的 main())

用法:
    直接打包: pyinstaller run_fmr_pyinstaller.py --name free-model-router --onefile
    或者: pyinstaller fmr.spec
"""
import os
import sys

# PyInstaller 解压路径 (--onefile 解压到临时目录)
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS  # type: ignore[attr-defined]
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def find_config():
    """config.yaml 搜索顺序: 当前目录 → 用户目录 → 打包内路径"""
    search_paths = [
        os.path.join(os.getcwd(), "config.yaml"),
        os.path.join(os.path.expanduser("~"), ".free_model_router", "config.yaml"),
        os.path.join(BASE_DIR, "config.yaml"),
    ]
    for p in search_paths:
        if os.path.exists(p):
            return p
    # 都不存在 → 从内置复制到当前目录
    sample = os.path.join(BASE_DIR, "config.yaml")
    target = os.path.join(os.getcwd(), "config.yaml")
    if os.path.exists(sample) and not os.path.exists(target):
        import shutil
        shutil.copy2(sample, target)
        print(f"[fmr] 已创建默认配置: {target}", file=sys.stderr)
        return target
    return None


if __name__ == "__main__":
    # 注入 config 路径到 sys.argv, 让 free_model_router.main 自动加载
    config_path = find_config()
    if config_path:
        sys.argv.extend(["--config", config_path])

    # 调用 fmr 自己的 main()
    from free_model_router.main import main
    sys.exit(main())
