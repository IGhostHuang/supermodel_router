# ============================================================
# SuperModel Router Docker 镜像
# ============================================================
# 构建:
#   docker build -t supermodel_router .
# 运行:
#   docker run -d -p 1298:1298 -v /path/to/config.yaml:/app/config.yaml supermodel_router
# ============================================================
FROM python:3.12-slim

LABEL maintainer="echo <supermodel_router>"
LABEL description="SuperModel Router — 多 Provider / 多 Key / 智能路由"

# 工作目录
WORKDIR /app

# 安装依赖 (分层缓存) — 使用预下载的 wheels 避免 DNS 问题
COPY requirements.txt .
COPY pip-cache/ /tmp/pip-cache/
RUN pip install --no-cache-dir --no-index --find-links /tmp/pip-cache -r requirements.txt && rm -rf /tmp/pip-cache

# 复制项目
COPY supermodel_router/ supermodel_router/
COPY run.py .
COPY config.yaml .

# 默认端口
EXPOSE 1298

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:1298/v1/health', timeout=3)" || exit 1

# 启动 (支持 -e LOG_LEVEL=DEBUG 控制日志级别)
ENV LOG_LEVEL=INFO
ENV HOST=0.0.0.0
ENV PORT=1298

CMD python run.py \
  --config /app/config.yaml \
  --host "$HOST" \
  --port "$PORT" \
  --log-level "$LOG_LEVEL"
