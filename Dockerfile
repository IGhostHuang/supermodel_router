# ============================================================
# SuperModel Router Docker 镜像
# ============================================================
# 构建:
#   docker build -t supermodel_router .
# 运行:
#   docker run -d -p 6473:6473 -v /path/to/config.yaml:/app/config.yaml supermodel_router
# ============================================================
FROM python:3.12-slim

LABEL maintainer="echo <supermodel_router>"
LABEL description="SuperModel Router — 多 Provider / 多 Key / 智能路由"

# 工作目录
WORKDIR /app

# 安装依赖 (分层缓存) — 标准 pip install
# v3.10.1 修 BUG-003: 删 pip-cache 离线假设 (目录不存在, --no-index 强制离线会失败)
# R55 实战坑修法 (老大 6/22 12:56 拍 🅰️): docker daemon DNS 不通 pypi.org, 改清华 pip 镜像
COPY requirements.txt .
RUN pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --no-cache-dir -r requirements.txt

# 复制项目
COPY supermodel_router/ supermodel_router/
COPY run.py .
COPY config.yaml .

# v3.8.1: 同步设计文档 (供 /design 端点 serve)
COPY docs/SMR-design.html docs/SMR-design.html
COPY docs/UPGRADE.md docs/UPGRADE.md
COPY scripts/sync_design_to_admin.py scripts/sync_design_to_admin.py
RUN python3 scripts/sync_design_to_admin.py --dst /app/docs/SMR-design.html --check || \
    python3 scripts/sync_design_to_admin.py --dst /app/docs/SMR-design.html

# v3.10.0: 内置 default model_metadata (首次启动 seed, 可被 state 卷覆盖)
RUN mkdir -p /app/data/seed
COPY supermodel_router/static/model_metadata.default.json /app/data/seed/model_metadata.json

# v3.10.0: docker-entrypoint.sh — 初始化 state + 渲染 secrets + 启动 SMR
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# 默认端口
EXPOSE 6473

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:6473/v1/health', timeout=3)" || exit 1

# v3.10.0: 默认配置目录
ENV LOG_LEVEL=INFO
ENV HOST=0.0.0.0
ENV PORT=6473
ENV STATE_DIR=/app/state
ENV DATA_DIR=/app/data
ENV CONFIG_FILE=/app/config.yaml

# v3.10.0: 入口改为 docker-entrypoint.sh (state 初始化 + secrets 渲染 + SMR 启动)
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]


# 保留 CMD 以便用户 override (entrypoint 末尾 exec python run.py)
CMD []


# legacy CMD (保留参考, 不再生效):
# CMD python run.py \
#     --config /app/config.yaml \
#     --host "$HOST" \
#     --port "$PORT" \
#     --log-level "$LOG_LEVEL"
