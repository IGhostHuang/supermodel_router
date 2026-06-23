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

# v3.6: 模型收费类型 (provider 层面 + 模型名关键词)
PRICING_FREE = "free"
PRICING_LIMITED_FREE = "limited_free"  # Cloudflare @cf/*: 每日 10000 Neurons 免费额度, UTC 0 点重置
PRICING_PAID = "paid"
PRICING_UNKNOWN = "unknown"

# 已知免费 provider (整 provider 都是免费模型)
FREE_PROVIDERS: set[str] = {
    "ollama",            # 本地部署, 永远免费
    "lm-studio",         # 本地
    "vllm",              # 本地
    "nvidia-nim-local",  # 本地 NIM
    "modelscope",        # 魔塔平台免费模型
    "魔塔免费模型",       # 老大配置里的中文 provider 名
}

# 已知付费 provider (默认都是付费, 除非模型名带 free 关键词)
PAID_PROVIDERS: set[str] = {
    "openai",
    "anthropic",
    "google",
    "mistral",
    "cohere",
    "deepseek",
    "moonshot",
    "zhipu",
    "yi",
    "openrouter",        # 多数付费, 但有 free 关键词的免费
    "newapi",            # 转发, 看模型名判断
    "siliconflow",       # 多数付费
    "dashscope",         # 多数付费
    "volcengine",        # 付费
    "freemodel",         # 看名字
}

# 混和 (NVIDIA NIM 多数免费, 部分付费; openrouter 多数付费, 部分免费)
MIXED_PROVIDERS: set[str] = {
    "nvidia",            # NVIDIA NIM 有 free + paid
}

# 免费模型名关键词 (大小写不敏感)
FREE_KEYWORDS: tuple[str, ...] = (
    ":free",             # openrouter 命名: xxx:free
    "-free",             # 部分: deepseek-v3:free
    "_free",             # 部分
    "free-",             # 部分
    "/free/",            # 部分
    "llama-3",           # nvidia nim 多数免费
    "nemotron",          # nvidia nim 多数免费
    "llama-3.1-405b-instruct",  # nvidia nim 免费
)

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

# tier 加成 (从 config.classifier.tier_bonus 读, 兜底用内置默认)
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

# 用户自定义关键词加分 (config.classifier.custom_keywords)
CUSTOM_KEYWORDS_DEFAULT: dict[str, int] = {}


def get_tier_bonus(config_obj=None) -> dict[str, int]:
    """
    从 config.classifier.tier_bonus 读, 兜底用内置 TIER_BONUS.
    config_obj = Config 实例 (config 全局单例).
    """
    if config_obj is None:
        return dict(TIER_BONUS)
    cfg_section = config_obj.data.get("classifier") or {}
    user_bonus = cfg_section.get("tier_bonus") or {}
    if not user_bonus:
        return dict(TIER_BONUS)
    # 合并: 用户值覆盖内置, 用户没写的保留内置
    merged = dict(TIER_BONUS)
    merged.update(user_bonus)
    return merged


def get_custom_keywords(config_obj=None) -> dict[str, int]:
    """用户自定义关键词加分"""
    if config_obj is None:
        return dict(CUSTOM_KEYWORDS_DEFAULT)
    cfg_section = config_obj.data.get("classifier") or {}
    return dict(cfg_section.get("custom_keywords") or CUSTOM_KEYWORDS_DEFAULT)


def get_modality_base_score(config_obj=None) -> dict[str, int]:
    """模态基类分"""
    if config_obj is None:
        return dict(MODALITY_BASE_SCORE)
    cfg_section = config_obj.data.get("classifier") or {}
    user_score = cfg_section.get("modality_base_score") or {}
    if not user_score:
        return dict(MODALITY_BASE_SCORE)
    merged = dict(MODALITY_BASE_SCORE)
    merged.update(user_score)
    return merged


# ── v3.6: 模型收费类型分类 ───────────────────────────────

def is_cloudflare_limited_free(model_id: str) -> bool:
    """Cloudflare Workers AI 托管开源模型: 模型名/id 以 @cf/ 开头。

    这类模型不是“无限免费”, 而是统一走 Neurons 计费体系,
    共享每日 10000 免费额度, UTC 0 点重置。
    """
    return (model_id or "").strip().lower().startswith("@cf/")


def pricing_detail(provider_name: str, model_id: str) -> dict:
    """返回 UI 可直接展示的价格/额度详情。"""
    pricing = classify_pricing(provider_name, model_id)
    if pricing == PRICING_LIMITED_FREE:
        return {
            "pricing": PRICING_LIMITED_FREE,
            "label": "免费额度",
            "description": "Cloudflare 托管开源模型；统一走 Neurons 计费体系；共享每日 10000 免费额度；UTC 0 点重置",
            "quota": {
                "unit": "Neurons",
                "free_daily": 10000,
                "shared": True,
                "reset": "UTC 0 点",
            },
        }
    if pricing == PRICING_FREE:
        return {"pricing": PRICING_FREE, "label": "免费", "description": "免费模型"}
    if pricing == PRICING_PAID:
        return {"pricing": PRICING_PAID, "label": "收费", "description": "按供应商规则计费"}
    return {"pricing": PRICING_UNKNOWN, "label": "未知", "description": "未识别价格规则"}


def classify_pricing(provider_name: str, model_id: str) -> str:
    """v3.6: 判断模型收费类型
    规则:
    0. model_id 以 @cf/ 开头 → limited_free (Cloudflare Neurons 每日 10000 免费额度, UTC 0 点重置)
    1. provider 在 FREE_PROVIDERS → free
    2. provider 在 PAID_PROVIDERS:
       - 模型名含 free 关键词 → free
       - 其他 → paid
    3. provider 在 MIXED_PROVIDERS (nvidia):
       - 模型名含 free 关键词 → free
       - 其他 → paid (默认保守)
    4. 其他 (未知 provider) → unknown
    """
    pn = (provider_name or "").lower()
    mid = (model_id or "").lower()
    if is_cloudflare_limited_free(mid):
        return PRICING_LIMITED_FREE
    if pn in FREE_PROVIDERS:
        return PRICING_FREE
    # 任何 provider 只要模型名带 free 关键词 → free
    for kw in FREE_KEYWORDS:
        if kw in mid:
            return PRICING_FREE
    if pn in PAID_PROVIDERS or pn in MIXED_PROVIDERS:
        return PRICING_PAID
    return PRICING_UNKNOWN

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

# v3.8.0: context_window 7 档细分 (从高到低, 给高分)
# 默认 bonus 表 (可被 config.classifier.context_window_bonus 覆盖)
DEFAULT_CONTEXT_WINDOW_BONUS: list[tuple[int, int]] = [
    (200_000, 20),  # 200K+ (Claude 200K / Gemini 1.5 Pro 1M 等)
    (128_000, 14),  # 128K (GPT-4 Turbo)
    (64_000, 10),   # 64K (GPT-4 早期)
    (32_000, 7),    # 32K (GPT-4 32K / Claude 100K)
    (16_000, 5),    # 16K (GPT-3.5 16K)
    (8_000, 3),     # 8K
    (4_000, 2),     # 4K
]


def _extract_context_window_from_extra(extra: dict | None) -> int:
    """从 extra dict 抽 context_window (跟 models._extract_context_window 行为一致)

    优先级:
      1. extra.context_window (顶层)
      2. extra.top_provider.context_length (openrouter nested)
      3. extra.architecture.context_length (openrouter nested fallback)
      4. 0 (未知)
    """
    if not extra or not isinstance(extra, dict):
        return 0
    v = extra.get("context_window")
    if isinstance(v, (int, float)) and v > 0:
        return int(v)
    tp = extra.get("top_provider")
    if isinstance(tp, dict):
        v = tp.get("context_length")
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    arch = extra.get("architecture")
    if isinstance(arch, dict):
        v = arch.get("context_length")
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    return 0


def get_context_window_bonus(config_obj=None) -> list[tuple[int, int]]:
    """从 config.classifier.context_window_bonus 读, 兜底用内置 DEFAULT_CONTEXT_WINDOW_BONUS

    config 格式 (list[dict]): [{"min": 200000, "bonus": 20}, {"min": 128000, "bonus": 14}, ...]
    按 min 降序排列
    """
    if config_obj is None:
        return list(DEFAULT_CONTEXT_WINDOW_BONUS)
    cfg_section = config_obj.data.get("classifier") or {}
    user_bonus = cfg_section.get("context_window_bonus")
    if not user_bonus:
        return list(DEFAULT_CONTEXT_WINDOW_BONUS)
    try:
        out = []
        for item in user_bonus:
            mn = int(item.get("min", 0))
            bn = int(item.get("bonus", 0))
            if mn > 0 and bn > 0:
                out.append((mn, bn))
        out.sort(key=lambda x: -x[0])  # 降序
        return out if out else list(DEFAULT_CONTEXT_WINDOW_BONUS)
    except Exception:
        return list(DEFAULT_CONTEXT_WINDOW_BONUS)


def compute_capability_score(model_id: str, modality: str,
                             extra: dict | None = None,
                             config_obj=None) -> float:
    """
    0-100 分, 基于:
      - 基类分 (modal 越强越高, 从 config 读, 兜底内置)
      - tier 加成 (turbo/pro/lite 等, 从 config 读, 兜底内置)
      - 自定义关键词加成 (用户 config 自定义)
      - 上下文窗口 (越大越高, 7 档细分, 从 config 读, 兜底内置)
    config_obj = Config 实例, 传 None 用内置默认.
    """
    mid_lower = model_id.lower()

    # 基类分 (从 config 读, 兜底内置)
    base_scores = get_modality_base_score(config_obj)
    score = base_scores.get(modality, 50)

    # tier 加成 (内置 + 用户)
    tier_bonus = get_tier_bonus(config_obj)
    for keyword, bonus in tier_bonus.items():
        if keyword in mid_lower:
            score += bonus
            break  # 只取第一个匹配

    # 用户自定义关键词加成 (叠加, 不 break, 允许多关键词命中累加)
    custom_kw = get_custom_keywords(config_obj)
    if custom_kw:
        for keyword, bonus in custom_kw.items():
            if keyword in mid_lower:
                score += bonus

    # v3.8.0: 上下文窗口加分 (7 档细分, 可配置)
    ctx = _extract_context_window_from_extra(extra)
    if ctx:
        bonus_table = get_context_window_bonus(config_obj)
        for min_ctx, bonus in bonus_table:
            if ctx >= min_ctx:
                score += bonus
                break  # 只取第一档 (最大)

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
