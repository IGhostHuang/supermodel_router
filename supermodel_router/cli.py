#!/usr/bin/env python3
"""
model-router CLI — 管理 Model Router 服务
"""
import sys
import os
import json
import argparse
import urllib.request
import urllib.error

BASE_URL = "http://localhost:6473"


def api_get(path):
    url = f"{BASE_URL}{path}"
    try:
        resp = urllib.request.urlopen(url, timeout=5)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": e.code, "body": e.read().decode()[:500]}
    except Exception as e:
        return {"error": str(e)}


def cmd_health(args):
    d = api_get("/v1/health")
    if "error" in d:
        print(f"✗ {d['error']}")
        return 1
    print(f"Status: {d['status']}")
    print(f"Uptime: {d['uptime_seconds']:.0f}s")
    print(f"Models: {d['total_models']}")
    for pname, ps in d.get("providers", {}).items():
        status = "🟢" if not ps["degraded"] else "🔴"
        print(f"  {status} {pname}: {ps['models']} models, "
              f"fail_count={ps['fail_count']}")


def cmd_models(args):
    path = f"/v1/models"
    if args.provider:
        path += f"?provider={args.provider}"
    d = api_get(path)
    if "error" in d:
        print(f"✗ {d['error']}")
        return 1
    models = d.get("data", [])
    print(f"Models ({len(models)}):")
    for m in models:
        print(f"  {m['id']:50s}  [{m.get('provider','?')}]")
    return 0


def cmd_routes(args):
    d = api_get("/v1/admin/routes")
    if "error" in d:
        print(f"✗ {d['error']}")
        return 1
    routes = d.get("routes", [])
    print(f"Routes ({d['total']}):")
    for r in routes:
        print(f"  {r}")


def cmd_stats(args):
    d = api_get("/v1/admin/stats")
    if "error" in d:
        print(f"✗ {d['error']}")
        return 1
    if not d:
        print("No stats yet")
        return 0
    print("Provider Stats:")
    for pname, s in d.items():
        print(f"  {pname}: {s['total_calls']} calls, "
              f"{s['success_calls']} ok, "
              f"{s['fail_calls']} fail, "
              f"avg {s['avg_latency_ms']}ms")


def cmd_refresh(args):
    d = api_get("/v1/admin/refresh")
    print(f"Refresh: {'ok' if d.get('ok') else 'fail'}")
    for pname, ps in d.get("providers", {}).items():
        print(f"  {pname}: {ps['models']} models")


def main():
    parser = argparse.ArgumentParser(prog="model-router")
    parser.add_argument("--base-url", default="http://localhost:6473")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("health", help="服务健康检查")
    p_models = sub.add_parser("models", help="列出模型")
    p_models.add_argument("--provider", help="按 provider 过滤")

    sub.add_parser("routes", help="列出所有路由")
    sub.add_parser("stats", help="路由统计")
    sub.add_parser("refresh", help="刷新模型列表")

    args = parser.parse_args()
    global BASE_URL
    BASE_URL = args.base_url

    cmds = {
        "health": cmd_health,
        "models": cmd_models,
        "routes": cmd_routes,
        "stats": cmd_stats,
        "refresh": cmd_refresh,
    }
    f = cmds.get(args.command)
    if f:
        return f(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())