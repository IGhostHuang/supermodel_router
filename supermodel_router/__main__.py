"""supermodel_router/__main__.py — python -m supermodel_router entry point"""
import argparse
import uvicorn

from .config import Config, config as default_config
from .app import app


def main():
    parser = argparse.ArgumentParser(prog="supermodel_router")
    parser.add_argument("--config", "-c", help="Path to config.yaml", default=None)
    parser.add_argument("--host", help="Bind host", default=None)
    parser.add_argument("--port", "-p", type=int, help="Bind port", default=None)
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev)")
    args = parser.parse_args()

    # 重新加载 config (如果指定了 --config)
    if args.config:
        new_cfg = Config(args.config)
        # 替换全局 config 实例
        import supermodel_router.config as cfg_mod
        cfg_mod.config = new_cfg
        # 同步到 app 模块
        import supermodel_router.app as app_mod
        app_mod.config = new_cfg

    cfg = default_config
    host = args.host or cfg.server.get("host", "0.0.0.0")
    port = args.port or cfg.server.get("port", 6473)

    print(f"🚀 supermodel_router starting on {host}:{port}")
    print(f"   config: {cfg._path}")
    uvicorn.run(app, host=host, port=port, reload=args.reload, log_level="info")


if __name__ == "__main__":
    main()