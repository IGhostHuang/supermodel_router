"""
supermodel_router PyInstaller 入口
打包时 PyInstaller 从这启动 Uvicorn

支持的命令行参数 (跟 run.py 对齐):
  --config PATH   配置文件路径
  --host ADDR     监听地址
  --port N        监听端口
  --log-level LVL 日志级别 DEBUG/INFO/WARNING/ERROR
"""
import argparse
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


def main():
    parser = argparse.ArgumentParser(description="SuperModel Router v3.4.0")
    parser.add_argument("--config", default=None, help="配置文件路径 (默认自动搜索)")
    parser.add_argument("--host", default=None, help="监听地址 (覆盖 config)")
    parser.add_argument("--port", type=int, default=None, help="监听端口 (覆盖 config)")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    # 找配置
    if args.config:
        config_path = args.config
        if not os.path.isabs(config_path):
            config_path = os.path.abspath(config_path)
    else:
        config_path = find_config()

    os.environ["SUPERMODEL_CONFIG"] = config_path or ""
    print(f"⚡ SuperModel Router v3.4.0")
    print(f"📄 Config: {config_path or '(bundled default)'}")

    # 从 config 读 host/port (命令行未给时)
    host = args.host or "0.0.0.0"
    port = args.port or 6473
    if config_path:
        try:
            import yaml
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            server_cfg = cfg.get("server", {})
            if not args.host:
                host = server_cfg.get("host", host)
            if not args.port:
                port = server_cfg.get("port", port)
        except Exception as e:
            print(f"⚠️  读 config 失败: {e}, 用默认值")

    print(f"🌐 Listening: http://{host}:{port}")
    print(f"📊 Dashboard: http://{host}:{port}/admin")
    print("=" * 50)

    uvicorn.run(
        "supermodel_router.app:app",
        host=host,
        port=port,
        log_level=args.log_level.lower(),
        reload=False,      # 生产模式禁止热重载
    )


if __name__ == "__main__":
    main()