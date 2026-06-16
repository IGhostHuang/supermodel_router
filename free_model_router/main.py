"""
free-model-router main entry

用法:
    python -m free_model_router --config config.yaml
    python -m free_model_router --config config.yaml --host 0.0.0.0 --port 5678
    python -m free_model_router --print-config   # 打印默认配置

启动流程:
  1. 加载 config.yaml
  2. 校验配置
  3. 初始化 ProviderManager
  4. 首次 discovery (从各 provider 拉取 /v1/models)
  5. 更新每个 provider 的 free_models 列表
  6. 启动 Router + HTTP server
  7. (可选) 启动 config 热重载 watcher
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Any

from .config import load_config, validate_config
from .discovery import DiscoveryManager, HTTPDiscovery
from .provider import ProviderManager
from .router import Router
from .server import FreeModelRouterServer

LOG = logging.getLogger("smr.main")


# ── 默认配置模板 (打印用) ──


DEFAULT_CONFIG = """\
# free-model-router 配置文件示例
# 支持一个平台多个 key 自动轮询 + 4 种免费模型识别策略

server:
  host: "127.0.0.1"
  port: 5678
  api_key: ""                # 网关鉴权 key, 留空表示不鉴权
  cors_origins: ["*"]

routing:
  strategy: "random"         # random | round-robin | least-loaded
  max_retry: 2               # 单请求最大 provider 切换次数
  first_token_timeout: 10000 # 流式首字节超时 (ms)
  request_timeout: 60000     # 非流式总超时 (ms)

sync:
  auto_discover: true        # 启动时自动发现
  interval: 3600             # 定时刷新间隔 (秒)

providers:
  # ── 例子 1: OpenRouter (按 "free" 关键字匹配) ──
  openrouter:
    base_url: "https://openrouter.ai/api/v1"
    api_keys:
      - "sk-or-v1-xxx"
      - "sk-or-v1-yyy"       # 多个 key 自动轮询
    model_rules:
      mode: "pattern"
      pattern: ".*free.*"    # 匹配包含 free 的模型
    max_concurrent: 3
    enabled: true

  # ── 例子 2: 智谱 (白名单指定) ──
  zhipu:
    base_url: "https://open.bigmodel.cn/api/paas/v4"
    api_keys:
      - "your-zhipu-api-key"
    model_rules:
      mode: "include"
      include:
        - "glm-4-flash"
        - "glm-4-flash-250414"
    max_concurrent: 5
    enabled: true

  # ── 例子 3: 自建中转 (排除付费模型) ──
  custom:
    base_url: "https://api.example.com/v1"
    api_keys:
      - "sk-custom-1"
    model_rules:
      mode: "exclude"
      exclude:
        - "gpt-4*"
        - "claude-*"
        - "o1*"
    max_concurrent: 3
    enabled: true

  # ── 例子 4: 全部免费 (不限制) ──
  allfree:
    base_url: "https://api.example.com/v1"
    api_keys:
      - "sk-all-1"
    model_rules:
      mode: "all"            # 所有模型都视为免费
    max_concurrent: 2
    enabled: true
"""


# ── 异步初始化 ──


async def bootstrap(config: dict[str, Any], manager: ProviderManager,
                    discovery: DiscoveryManager) -> dict[str, Any]:
    """首次发现 + 把结果灌到 provider 列表"""
    providers_cfg = config.get("providers") or {}
    enabled_pids = [pid for pid, p in providers_cfg.items()
                    if p.get("enabled", True)]
    if not enabled_pids:
        LOG.warning("No enabled providers configured")
        return {}

    LOG.info("Discovering models for %d providers...", len(enabled_pids))
    results = await discovery.discover_all(providers_cfg, force=True)
    summary: dict[str, Any] = {}
    for pid, result in results.items():
        p = manager.get(pid)
        if not p:
            continue
        free_ids = [m.id for m in result.free_models]
        if free_ids:
            p.set_free_models(free_ids)
            if not p.primary_model and free_ids:
                p.primary_model = free_ids[0]
                p.fallback_models = free_ids[1:3]
        summary[pid] = {
            "free_model_count": len(free_ids),
            "total_model_count": len(result.all_models),
            "primary": p.primary_model,
            "error": result.error,
        }
        LOG.info("  %s: %d free / %d total, primary=%s%s",
                 pid, len(free_ids), len(result.all_models),
                 p.primary_model, f" err={result.error}" if result.error else "")
    return summary


# ── 主函数 ──


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="free-model-router",
        description="免费模型自动路由网关 — 4 种策略 / 多 key 轮询 / 无固定模型限制",
    )
    p.add_argument("-c", "--config", default="config.yaml",
                   help="配置文件路径 (默认: config.yaml)")
    p.add_argument("--host", default=None,
                   help="监听地址 (覆盖 config)")
    p.add_argument("--port", type=int, default=None,
                   help="监听端口 (覆盖 config)")
    p.add_argument("--print-config", action="store_true",
                   help="打印默认配置模板到 stdout")
    p.add_argument("--validate", action="store_true",
                   help="只校验配置不启动")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="DEBUG 日志")
    return p.parse_args()


def make_server(config: dict[str, Any]) -> FreeModelRouterServer:
    """构造所有组件 + 启动 server (含首次 discovery)"""
    manager = ProviderManager(config.get("providers") or {})
    discovery = DiscoveryManager(strategy=HTTPDiscovery())
    routing_cfg = config.get("routing") or {}
    router = Router(
        manager,
        strategy=routing_cfg.get("strategy", "random"),
        max_retry=int(routing_cfg.get("max_retry", 2)),
    )
    server_cfg = config.get("server") or {}
    server = FreeModelRouterServer(
        config=config,
        provider_manager=manager,
        router=router,
        discovery_manager=discovery,
        host=server_cfg.get("host", "127.0.0.1"),
        port=int(server_cfg.get("port", 5678)),
    )

    # 注入: 让 admin/discover 能更新 provider 的 free_models
    original_run = server.run_discovery
    async def run_discovery_with_update(force: bool = False) -> dict:
        results = await original_run(force=force)
        # 更新每个 provider 的 free_models
        for pid, result in results.items():
            p = manager.get(pid)
            if p and result.free_models:
                p.set_free_models([m.id for m in result.free_models])
        return results
    server.run_discovery = run_discovery_with_update  # type: ignore[method-assign]
    return server


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose)
    if args.print_config:
        print(DEFAULT_CONFIG)
        return 0

    config_path = Path(args.config)
    if not config_path.exists():
        LOG.error("Config file not found: %s", config_path)
        LOG.info("Generate a template with --print-config > config.yaml")
        return 1

    config = load_config(str(config_path))
    if args.host:
        config.setdefault("server", {})["host"] = args.host
    if args.port:
        config.setdefault("server", {})["port"] = args.port

    errors = validate_config(config)
    if errors:
        for e in errors:
            LOG.error("Config: %s", e)
        if args.validate:
            return 2
        LOG.warning("Config has errors, starting anyway (some providers may be skipped)")

    if args.validate:
        LOG.info("Config validation passed")
        return 0

    # 启动
    server = make_server(config)
    # 首次发现 (在 HTTP server 起来前, 这样 /v1/models 立即可用)
    async def _initial():
        return await bootstrap(config, server.provider_manager,
                               server.discovery_manager)
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        summary = loop.run_until_complete(_initial())
    except Exception as e:
        LOG.exception("Initial discovery failed: %s", e)
        summary = {}

    # 启动 HTTP server (在独立线程中跑 event loop)
    server.start()

    LOG.info("=" * 60)
    LOG.info("free-model-router v1.0.0 已启动")
    LOG.info("  监听地址: http://%s:%d",
             server.host, server.port)
    LOG.info("  Chat:    POST http://%s:%d/v1/chat/completions",
             server.host, server.port)
    LOG.info("  Models:  GET  http://%s:%d/v1/models",
             server.host, server.port)
    LOG.info("  Admin:   http://%s:%d/admin", server.host, server.port)
    LOG.info("=" * 60)

    # 信号处理
    stop_event = {"v": False}
    def _signal_handler(signum, frame):
        LOG.info("Received signal %d, shutting down...", signum)
        stop_event["v"] = True
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        while not stop_event["v"]:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        LOG.info("Stopping...")
        server.stop()
        LOG.info("Bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
