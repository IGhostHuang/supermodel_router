#!/usr/bin/env python3
"""
supermodel_router — OpenAI-compatible 多 provider / 多 key / 智能路由
用法:
  python run.py                   # 默认 0.0.0.0:5678
  python run.py --port 8080       # 指定端口
  python run.py --config my.yaml  # 指定配置
"""
import sys
import os
import argparse
import logging
import yaml

# 把项目根目录加 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    parser = argparse.ArgumentParser(description="SuperModel Router v1.0")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--host", default=None, help="监听地址")
    parser.add_argument("--port", type=int, default=None, help="监听端口")
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    # 设置日志
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)-10s] %(levelname)-5s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 加载配置
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.config)
    from supermodel_router.config import config as cfg
    cfg._path = __import__("pathlib").Path(config_path)
    cfg.load()

    # 确定 host/port: 环境变量 > CLI 参数 > config > 默认
    host = args.host or os.environ.get("HOST") or cfg.server.get("host", "0.0.0.0")
    port = args.port or int(os.environ.get("PORT", 0)) or cfg.server.get("port", 5678)

    # 启动 uvicorn
    import uvicorn
    uvicorn.run(
        "supermodel_router.app:app",
        host=host,
        port=port,
        reload=args.reload,
        log_level=args.log_level.lower(),
    )


if __name__ == "__main__":
    main()
