"""
supermodel_router PyInstaller 入口
打包时 PyInstaller 从这启动 Uvicorn
"""
import os
import sys
import uvicorn

# PyInstaller 解压路径
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# config.yaml 搜索顺序: 当前目录 → 用户目录 → 打包内路径
def find_config():
    search_paths = [
        os.path.join(os.getcwd(), "config.yaml"),
        os.path.join(os.path.expanduser("~"), ".supermodel_router", "config.yaml"),
        os.path.join(BASE_DIR, "config.yaml"),
    ]
    for p in search_paths:
        if os.path.exists(p):
            return p
    # 都不存在 → 从内置复制
    sample = os.path.join(BASE_DIR, "config.yaml")
    target = os.path.join(os.getcwd(), "config.yaml")
    if os.path.exists(sample) and not os.path.exists(target):
        import shutil
        shutil.copy2(sample, target)
        print(f"📝 已创建默认配置: {target}")
    return target


if __name__ == "__main__":
    config_path = find_config()
    os.environ["SUPERMODEL_CONFIG"] = config_path or ""
    print(f"⚡ SuperModel Router v1.0")
    print(f"📄 Config: {config_path or '(bundled default)'}")

    # 从 config 读 host/port
    host = "0.0.0.0"
    port = 6473
    if config_path:
        try:
            import yaml
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            server_cfg = cfg.get("server", {})
            host = server_cfg.get("host", host)
            port = server_cfg.get("port", port)
        except Exception:
            pass

    print(f"🌐 Listening: http://{host}:{port}")
    print(f"📊 Dashboard: http://{host}:{port}/admin")
    print("=" * 50)

    uvicorn.run(
        "supermodel_router.app:app",
        host=host,
        port=port,
        log_level="info",
        reload=False,      # 生产模式禁止热重载
    )