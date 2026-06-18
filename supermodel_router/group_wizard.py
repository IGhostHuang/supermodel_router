"""
supermodel_router/group_wizard.py — 场景化分组向导 (v3.10.0 Phase K)

13 个预定义 preset, 把"用户意图"翻译成 ModelFilter.

R39 实战: regex 编译失败要 raise 不吞 (R 已踩 pattern[5:] bug)
R42 向后兼容: 老 model_groups CRUD 完全不动, wizard 创建的 group 跟普通 group 等价
"""
import logging
from typing import Any, Dict, List

from .model_filter import ModelFilter, apply_filter

LOG = logging.getLogger("group_wizard")


# 13 个 preset: id → {name, icon, description, filter}
PRESETS: Dict[str, Dict[str, Any]] = {
    "long-context-quality": {
        "name": "📚 长上下文高质量",
        "description": "上下文 ≥ 100K + 高质量输出, 适合长文档分析 / 大代码库",
        "icon": "📚",
        "filter": {
            "context_min": 100000,
            "quality_min": 80,
        },
    },
    "context-32k-balanced": {
        "name": "📖 32K 上下文平衡",
        "description": "16K-64K 上下文 + 质量 ≥ 75, 日常对话/中等文档",
        "icon": "📖",
        "filter": {
            "context_min": 16000,
            "context_max": 64000,
            "quality_min": 75,
        },
    },
    "context-200k-monster": {
        "name": "📕 200K 超长上下文",
        "description": "上下文 ≥ 128K + 推理能力强, 整本书/大型代码仓库",
        "icon": "📕",
        "filter": {
            "context_min": 128000,
            "reasoning_min": 75,
        },
    },
    "fast-response": {
        "name": "⚡ 快速响应",
        "description": "speed_score ≥ 80, latency < 2s, 实时聊天/补全",
        "icon": "⚡",
        "filter": {
            "speed_min": 80,
        },
    },
    "fast-quality-balanced": {
        "name": "⚖️ 速度质量平衡",
        "description": "speed ≥ 70 + quality ≥ 75, 兼顾响应速度 + 输出质量",
        "icon": "⚖️",
        "filter": {
            "speed_min": 70,
            "quality_min": 75,
        },
    },
    "high-quality": {
        "name": "🎯 高质量输出",
        "description": "quality_score ≥ 85, 专业写作/深度分析",
        "icon": "🎯",
        "filter": {
            "quality_min": 85,
        },
    },
    "top-tier": {
        "name": "🏆 顶级输出",
        "description": "quality ≥ 92 + reasoning ≥ 85, 旗舰模型",
        "icon": "🏆",
        "filter": {
            "quality_min": 92,
            "reasoning_min": 85,
        },
    },
    "image-generation": {
        "name": "🎨 图像生成",
        "description": "modality = image-generation, DALL-E / Stable Diffusion",
        "icon": "🎨",
        "filter": {
            "modality": "image-gen",
        },
    },
    "vision-understanding": {
        "name": "👁️ 视觉理解",
        "description": "支持 vision input, GPT-4o / Claude / Gemini",
        "icon": "👁️",
        "filter": {
            "tags_any": ["vision", "multimodal"],
        },
    },
    "any-to-any": {
        "name": "🌐 Any-to-Any",
        "description": "全模态支持 (multimodal), 多模态输入输出",
        "icon": "🌐",
        "filter": {
            "modality": "multimodal",
        },
    },
    "reasoning-strong": {
        "name": "🧠 强推理",
        "description": "reasoning_score ≥ 80, 数学/逻辑/代码推理 (o1, r1)",
        "icon": "🧠",
        "filter": {
            "reasoning_min": 80,
        },
    },
    "coding-specialist": {
        "name": "💻 代码专精",
        "description": "tags = coding, DeepSeek / Qwen / CodeLlama",
        "icon": "💻",
        "filter": {
            "tags_any": ["coding"],
            "quality_min": 70,
        },
    },
    "budget-friendly": {
        "name": "💰 性价比",
        "description": "quality ≥ 70 + speed ≥ 65, 日常任务省成本",
        "icon": "💰",
        "filter": {
            "quality_min": 70,
            "speed_min": 65,
        },
    },
}


def get_preset(preset_id: str) -> Dict[str, Any]:
    """v3.10.0: 取单个 preset (含 filter dict)"""
    if preset_id not in PRESETS:
        raise KeyError(f"preset '{preset_id}' not found. available: {list(PRESETS.keys())}")
    return PRESETS[preset_id]


def list_presets() -> List[Dict[str, Any]]:
    """v3.10.0: 列出所有 preset (供 UI 渲染 wizard cards)"""
    return [
        {"id": pid, **p}
        for pid, p in PRESETS.items()
    ]


def preset_to_filter(preset_id: str) -> ModelFilter:
    """v3.10.0: preset → ModelFilter 实例"""
    p = get_preset(preset_id)
    return ModelFilter.from_dict(p["filter"])


def get_filter_for_preset(preset_id: str) -> Dict[str, Any]:
    """v3.10.0: preset filter (dict, 给 UI 显示 + POST 用)"""
    p = get_preset(preset_id)
    return p["filter"]


def apply_preset(preset_id: str, models: List[Any]) -> List[Any]:
    """v3.10.0: 应用 preset filter 到 models"""
    f = preset_to_filter(preset_id)
    return apply_filter(f, models)
