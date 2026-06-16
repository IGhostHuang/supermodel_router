"""
free-model-router — 免费模型自动路由网关

核心特性:
- 不限制固定模型, 自动发现 provider 下所有可用模型
- 一个 provider 支持多个 API key 轮询
- 4 种免费模型识别策略: pattern / include / exclude / all
- OpenAI 兼容接口 (/v1/chat/completions, /v1/models 等)
- 健康检查 + 自动故障转移
- 配置热重载
"""

__version__ = "1.0.0"
