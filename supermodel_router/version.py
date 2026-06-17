"""
supermodel_router/version.py — 版本元数据 + GitHub release 检查

v3.6.0 新增 (2026-06-17 23:56 老大拍):
- UI/UX 全面改版: 顶部 toolbar → 左侧 sidebar nav (Dashboard/Providers/Models/Keys/Stats/Config/Classifier/Version)
- 模型列表分页 (每页 20 + 前/后/跳转)
- 真实使用量统计卡片: 总请求 / 成功率 / 平均延迟 / 今日 token
- import_time / export_time / import_keys 单独 export+import
- API key 独立管理页 (/v1/admin/api-keys)
- 持久化复盘文档 (PERSISTENCE.md) + 启动时自动迁移

v3.5.0 新增 (2026-06-17 22:25 老大拍):
- 主动盘点: POST /v1/admin/context_review 拿 smr_request_id 聚合报告
  (用户说"盘点上下文/重新审视/回顾上下文"时, mainbot 调该 endpoint)
- 切链 race condition 防御: stream 模式切链时显式 aclose() 上游 httpx
- smr_request_id 嵌入: response._router.smr_request_id + chain_id
  (mainbot 收 response 时校验错配 → 丢弃)
- per-request 跟踪: ContextBridge 维护 smr_request_id → SwitchRecord[] 映射
  (bounded LRU 1000, 不持久化)

v3.4.0 新增 (2026-06-17 22:00 老大拍):
- 上下文桥接 (ContextBridge): 切换模型时, 注入 system message 同步上下文
- 过期标记: 切到新 candidate 的时间距请求开始 > 30min → 标 stale=true
- 流式 SSE sentinel: data: {"_smr_bridge": {...}} 标记切换 + stale
- 非流式: response._router.switched_from + stale + age_seconds
- /v1/admin/context_bridge endpoints (config/stats/reset)

v3.1 新增: 老大 09:48 拍 C 项
- 当前版本号 (SMR_VERSION)
- 启动时打印版本 + 构建日期
- 后台定期检查 GitHub release, 有新版本时通知
- /v1/admin/version endpoint 暴露版本信息
- /v1/admin/upgrade endpoint 触发升级 (拉新 binary + 重启)

v3.2 新增: 老大 14:40 拍 🅲
- 配置版本管理: 自动备份 .backups/config-*.yaml (保留 50 个)
- /v1/admin/config/backups + /v1/admin/config/restore
- penalty state 持久化 (penalty_state.json) — SMR 重启不丢
"""
import json
import logging
import os
import time
from typing import Optional

LOG = logging.getLogger("version")

# 当前版本 (跟随 release tag)
VERSION = "3.7.1"
BUILD_DATE = "2026-06-18"
GITHUB_REPO = "IGhostHuang/supermodel_router"  # 默认值, 可被 config.version_check.repo 覆盖
RELEASE_CHECK_INTERVAL = 3600  # 1 小时检查一次

_cached_release: Optional[dict] = None
_last_check_time: float = 0.0


def load_version_meta() -> dict:
    """返回当前版本元数据 (lifespan 用)"""
    return {
        "version": VERSION,
        "build_date": BUILD_DATE,
        "title": f"SuperModel Router v{VERSION}",
    }


def get_cached_release(github_token: Optional[str] = None,
                       repo: Optional[str] = None) -> Optional[dict]:
    """获取缓存的 release 信息 (1h 内复用, 不重复打 GitHub API)"""
    global _cached_release, _last_check_time
    now = time.time()
    if _cached_release and (now - _last_check_time) < RELEASE_CHECK_INTERVAL:
        return _cached_release
    return fetch_latest_release(github_token=github_token, repo=repo)


def fetch_latest_release(github_token: Optional[str] = None,
                         repo: Optional[str] = None) -> Optional[dict]:
    """从 GitHub API 拉最新 release"""
    global _cached_release, _last_check_time
    target_repo = repo or GITHUB_REPO
    url = f"https://api.github.com/repos/{target_repo}/releases/latest"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "SMR-VersionChecker"}
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    try:
        import httpx
        resp = httpx.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            _cached_release = {
                "tag": data.get("tag_name", "unknown"),
                "name": data.get("name", ""),
                "published_at": data.get("published_at", ""),
                "url": data.get("html_url", ""),
                "tarball": data.get("tarball_url", ""),
                "zipball": data.get("zipball_url", ""),
                "body": data.get("body", "")[:500],  # 限制大小
                "prerelease": data.get("prerelease", False),
            }
            _last_check_time = time.time()
            LOG.info("GitHub release fetched: %s (%s)",
                     _cached_release["tag"], _cached_release["published_at"])
            return _cached_release
        elif resp.status_code == 404:
            LOG.warning("GitHub repo %s not found or no release", target_repo)
            _cached_release = None
            _last_check_time = time.time()
            return None
        elif resp.status_code == 403:
            # rate limit
            LOG.warning("GitHub API rate limited (403), keeping cached")
            return _cached_release
        else:
            LOG.warning("GitHub release fetch returned %d: %s",
                        resp.status_code, resp.text[:200])
            return _cached_release
    except Exception as e:
        LOG.warning("GitHub release fetch failed: %s", e)
        return _cached_release


def is_newer_version(current: str, latest: str) -> bool:
    """简单 semver 比较 (major.minor.patch)
    current="3.1.0", latest="3.2.0" → True
    current="3.1.0", latest="3.1.0" → False
    current="3.1.0", latest="4.0.0-beta" → True (忽略 prerelease 后缀)
    """
    def parse(v: str) -> tuple:
        # 去掉 -beta / -rc 等后缀
        v = v.split("-")[0].lstrip("v")
        parts = v.split(".")
        return tuple(int(p) if p.isdigit() else 0 for p in parts) + (0,) * (3 - len(parts))

    return parse(latest) > parse(current)


def get_upgrade_command(target_tag: str, repo: Optional[str] = None,
                        method: str = "git") -> str:
    """生成升级命令 (按 method 切换 git pull / pip install / docker pull)

    method:
    - "git": git pull + restart (开发模式)
    - "pip": pip install --upgrade
    - "docker": docker pull + recreate
    - "binary": wget binary + restart (PyInstaller)
    """
    target_repo = repo or GITHUB_REPO

    if method == "git":
        return f"cd /path/to/smr && git pull origin main && systemctl restart smr"
    elif method == "pip":
        return f"pip install --upgrade git+https://github.com/{target_repo}.git"
    elif method == "docker":
        return f"docker pull ghcr.io/{target_repo}:{target_tag} && docker compose up -d"
    elif method == "binary":
        return (
            f"wget https://github.com/{target_repo}/releases/download/{target_tag}/"
            f"supermodel_router -O /usr/local/bin/supermodel_router && "
            f"chmod +x /usr/local/bin/supermodel_router && systemctl restart smr"
        )
    return f"# Unknown upgrade method: {method}"