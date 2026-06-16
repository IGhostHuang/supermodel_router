"""
supermodel_router/classifier.py — 模型能力分类 + 评分 v2

根据模型 ID、provider 元数据、命名规则自动分类:
  - text-only     纯文本
  - multimodal    多模态 (图片+文字输入)
  - image-gen     图片生成
  - video-gen     视频生成
  - audio-gen     音频生成
"""
import re
import logging

LOG = logging.getLogger("classifier")

# ── 能力分类 ──────────────────────────────────────────────

# 模态类型 (按能力升序排列)
TEXT_ONLY = "text-only"
MULTIMODAL = "multimodal"  # vision + text
IMAGE_GEN = "image-gen"
VIDEO_GEN = "video-gen"
AUDIO_GEN = "audio-gen"
EMBEDDING = "embedding"

# 所有模态
ALL_MODALITIES = [TEXT_ONLY, MULTIMODAL, IMAGE_GEN, VIDEO_GEN, AUDIO_GEN, EMBEDDING]

# 基础能力分 (用于 capability_score)
MODALITY_BASE_SCORE = {
    TEXT_ONLY: 50,
    MULTIMODAL: 85,    # 能做文本也能做视觉, 最 versatile
    IMAGE_GEN: 70,
    VIDEO_GEN: 75,
    AUDIO_GEN: 60,
    EMBEDDING: 30,
}

# tier 加成
TIER_BONUS = {
    "turbo": 25,
    "ultra": 25,
    "pro": 20,
    "premium": 20,
    "latest": 15,
    "new": 10,
    "flash": 5,
    "lite": -10,
    "mini": -15,
    "small": -15,
    "nano": -20,
    "tiny": -20,
    "legacy": -25,
    "old": -25,
}

# ── 分类规则: (pattern, modality) ──────────────────────────

# 多模态 / Vision 模型 — 按"最高匹配"覆盖, 需在 text-only 前
VISION_PATTERNS = [
    r"vision",
    r"-vl\b",            # qwen-vl, glm-4v
    r"multimodal",
    r"gemini.*pro.*vision",
    r"gemini.*ultra.*vision",
    r"gemini.*flash.*vision",
    r"gemma.*vision",
    r"llava",
    r"cogvlm",
    r"cogview",
    r"internvl",
    r"internlm.*vl",
    r"yi.*vl",
    r"step.*vl",
    r"minicpm.*v",
    r"phi.*vision",
    r"idefics",
    r"florence",
    r"paligemma",
    r"kosmos",
    r"fuyu",
    r"gpt-4o\b(?!.*mini)",   # GPT-4o (不是 4o-mini)
    r"gpt-4\.1\b",             # GPT-4.1 has vision
    r"claude.*sonnet.*(?:4|5)", # Claude 3.5 Sonnet+, 4 Sonnet+ have vision
    r"claude.*opus.*4\b",
    r"claude.*haiku.*3\.5",   # 3.5 Haiku has vision
]

# 图片生成
IMAGE_GEN_PATTERNS = [
    r"dall.e",
    r"stable.diffusion",
    r"sdxl",
    r"flux\b",
    r"midjourney",
    r"image.gen",
    r"imagen",
    r"firefly",
    r"pixart",
    r"latent.consistency",
    r"playground.*v\d",
    r"deepfloyd",
    r"wuerstchen",
    r"kandinsky",
    r"openjourney",
    r"dreamshaper",
    r"realistic.vision",
    r"anything.*v\d",
    r"rev.*animated",
    r"adventure.*diffusion",
]

# 视频生成
VIDEO_GEN_PATTERNS = [
    r"sora",
    r"kling",
    r"pika",
    r"runway.*gen",
    r"gen.*3\b.*alpha",
    r"video.gen",
    r"veo",
    r"mochi",
    r"cogvideo",
]

# 音频生成
AUDIO_GEN_PATTERNS = [
    r"elevenlabs",
    r"tts",
    r"text.to.speech",
    r"speech",
    r"audio.gen",
    r"musicgen",
    r"audiocraft",
    r"bark\b",
    r"valle",
    r"voice.*gen",
    r"fish.*speech",
    r"cosyvoice",
    r"gpt.*sovits",
]

# Embedding
EMBEDDING_PATTERNS = [
    r"embedding",
    r"embed",
    r"text-embedding",
    r"text.similarity",
    r"ada\b",
]

# 已知的多模态模型 (ID 可能没有明确 vision 关键词)
KNOWN_MULTIMODAL = {
    "gpt-4o", "gpt-4o-2024-08-06", "gpt-4o-2024-05-13",
    "gpt-4-turbo", "gpt-4-vision-preview",
    "claude-3-5-sonnet-20241022", "claude-3-5-sonnet-latest",
    "claude-3-opus-latest", "claude-3-haiku-3.5",
    "claude-sonnet-4-20250514", "claude-sonnet-4-latest",
    "claude-opus-4-20250514", "claude-opus-4-latest",
    "gemini-2.0-flash-001", "gemini-2.0-flash-lite-001",
    "gemini-2.0-pro-001",
    "gemini-1.5-pro", "gemini-1.5-flash", "gemini-1.5-flash-8b",
    "gemini-1.0-pro-vision",
}

# OpenAI 多模态模型 (GPT-4o 系列以上都有 vision)
OPENAI_MULTIMODAL_PREFIXES = ["gpt-4o", "gpt-4-turbo", "gpt-4-vision"]

# ── 分类函数 ──────────────────────────────────────────────

def classify_model(model_id: str, provider: str = "",
                   extra: dict | None = None) -> str:
    """
    返回模型所属模态类型。
    优先级: 已知列表 > vision/多模态 > 图片 > 视频 > 音频 > embedding > text-only
    """
    mid_lower = model_id.lower()

    # 1. 已知多模态名单
    if mid_lower in KNOWN_MULTIMODAL:
        return MULTIMODAL

    # 2. OpenRouter 前缀匹配
    if provider == "openrouter":
        for prefix in OPENAI_MULTIMODAL_PREFIXES:
            if mid_lower.startswith(prefix):
                return MULTIMODAL

    # 3. 检查 extra 中的元数据 (OpenRouter 有 architecture/capabilities)
    if extra:
        arch = extra.get("architecture", {})
        if isinstance(arch, dict):
            if arch.get("modality") == "multimodal":
                return MULTIMODAL
            if arch.get("input_modality") == "image":
                return MULTIMODAL
        cap = extra.get("capabilities", {})
        if isinstance(cap, dict):
            if any(cap.get(k) for k in ("vision", "image_input", "multimodal")):
                return MULTIMODAL

    # 4. 转写标准模式 (先高后低)
    mid_for_pattern = mid_lower.replace("-", "").replace("_", "").replace("/", " ")

    # 音频
    for pat in AUDIO_GEN_PATTERNS:
        if re.search(pat, mid_lower):
            return AUDIO_GEN

    # 视频
    for pat in VIDEO_GEN_PATTERNS:
        if re.search(pat, mid_lower):
            return VIDEO_GEN

    # 图片生成
    for pat in IMAGE_GEN_PATTERNS:
        if re.search(pat, mid_lower):
            return IMAGE_GEN

    # 多模态/vision (必须在纯文本之前)
    for pat in VISION_PATTERNS:
        if re.search(pat, mid_lower):
            return MULTIMODAL

    # Embedding
    for pat in EMBEDDING_PATTERNS:
        if re.search(pat, mid_lower):
            return EMBEDDING

    # 5. provider 级别的默认规则
    if provider in ("openai", "azure"):
        # OpenAI 的 gpt-4 系列默认有 vision (除了 gpt-3.5)
        if "gpt-4" in mid_lower and "gpt-4o-mini" not in mid_lower:
            return MULTIMODAL
        if "gpt-4o-mini" in mid_lower:
            return MULTIMODAL  # 4o-mini 也有 vision

    # 6. 默认: 纯文本
    return TEXT_ONLY


# ── 能力评分 ──────────────────────────────────────────────

def compute_capability_score(model_id: str, modality: str,
                             extra: dict | None = None) -> float:
    """
    0-100 分, 基于:
      - 基类分 (modal 越强越高)
      - tier 加成 (turbo/pro/lite 等)
      - 上下文窗口 (越大越高)
    """
    mid_lower = model_id.lower()

    # 基类分
    score = MODALITY_BASE_SCORE.get(modality, 50)

    # tier 加成
    for keyword, bonus in TIER_BONUS.items():
        if keyword in mid_lower:
            score += bonus
            break  # 只取第一个匹配

    # OpenRouter 上下文长度
    if extra:
        ctx = None
        if isinstance(extra.get("top_provider"), dict):
            ctx = extra["top_provider"].get("context_length")
        if not ctx and isinstance(extra.get("architecture"), dict):
            ctx = extra["architecture"].get("context_length")
        if ctx:
            if ctx >= 200000:
                score += 20          # 200K+ 超长上下文
            elif ctx >= 128000:
                score += 15          # 128K
            elif ctx >= 32000:
                score += 10          # 32K
            elif ctx >= 16000:
                score += 5           # 16K

    return max(0, min(100, score))


# ── 工具 ──────────────────────────────────────────────────

def get_modality_display(modality: str) -> str:
    """返回中文字符描述"""
    labels = {
        TEXT_ONLY: "📝 纯文本",
        MULTIMODAL: "🖼️ 多模态",
        IMAGE_GEN: "🎨 生图",
        VIDEO_GEN: "🎬 生视频",
        AUDIO_GEN: "🔊 生音频",
        EMBEDDING: "📊 向量",
    }
    return labels.get(modality, modality)


def modality_needs_image_input(modality: str) -> bool:
    """该模态是否需要图片输入"""
    return modality == MULTIMODAL


def modality_needs_text_input(modality: str) -> bool:
    """该模态是否需要文本输入"""
    return modality in (TEXT_ONLY, MULTIMODAL, IMAGE_GEN)


def get_input_modalities(modality: str) -> list[str]:
    """返回该模态接受的输入类型"""
    mapping = {
        TEXT_ONLY: ["text"],
        MULTIMODAL: ["text", "image"],
        IMAGE_GEN: ["text", "image"],
        VIDEO_GEN: ["text", "image"],
        AUDIO_GEN: ["text"],
        EMBEDDING: ["text"],
    }
    return mapping.get(modality, ["text"])


def get_output_modalities(modality: str) -> list[str]:
    """返回该模态的输出类型"""
    mapping = {
        TEXT_ONLY: ["text"],
        MULTIMODAL: ["text"],
        IMAGE_GEN: ["image"],
        VIDEO_GEN: ["video"],
        AUDIO_GEN: ["audio"],
        EMBEDDING: ["embedding"],
    }
    return mapping.get(modality, ["text"])
