"""
supermodel_router/detector.py — 请求输入/输出类型检测

分析请求 body, 自动判断用户需要什么模态:
  - 纯文本 → 文本
  - 带图片 → 多模态
  - images/generations → 生图
  - 等
"""
import logging

LOG = logging.getLogger("detector")


def detect_chat_input_modality(body: dict) -> str:
    """
    从 /v1/chat/completions 的 body 检测输入的模态类型。
    返回: "text" | "image" | "audio" | "mixed"
    """
    messages = body.get("messages", [])
    has_image = False
    has_text = False
    has_audio = False

    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            if content.strip():
                has_text = True
            continue

        # content 是 list[ContentPart]
        if isinstance(content, list):
            for part in content:
                part_type = part.get("type", "")
                if part_type == "text":
                    has_text = True
                elif part_type == "image_url":
                    has_image = True
                elif part_type == "audio":
                    has_audio = True
                elif part_type == "input_audio":
                    has_audio = True

    if has_image and has_text:
        return "mixed"
    if has_image:
        return "image"
    if has_audio:
        return "audio"
    return "text"


def detect_chat_output_modality(body: dict) -> str:
    """
    从 /v1/chat/completions 的 body 检测期望的输出模态类型。
    默认返回 "text", 可拓展到响应格式推测。
    """
    # 有 response_format 指示
    response_format = body.get("response_format", {})
    if isinstance(response_format, dict):
        rtype = response_format.get("type", "")
        if rtype == "json_object":
            return "text"
        if rtype == "text":
            return "text"

    return "text"


def detect_image_gen_params(body: dict) -> dict:
    """
    从 /v1/images/generations body 提取参数.
    返回: {"prompt": str, "n": int, "size": str, ...}
    """
    return {
        "prompt": body.get("prompt", ""),
        "n": body.get("n", 1),
        "size": body.get("size", "1024x1024"),
    }


def detect_streaming(body: dict) -> bool:
    return body.get("stream", False)


def match_modality_for_request(
    input_modality: str,
    output_modality: str,
) -> list[str]:
    """
    根据输入/输出类型, 返回合适的模型模态优先级列表。
    返回: ["multimodal", "text-only", ...]  (按优先级降序)

    规则:
      input text,  output text  → text-only, multimodal
      input image, output text  → multimodal
      input text,  output image → image-gen
      input image, output image → image-gen (img2img)
      input text,  output video → video-gen
      input audio, output text  → multimodal, text-only
      input text,  output audio → audio-gen
    """
    if input_modality == "image" and output_modality == "text":
        return [MULTIMODAL]

    if output_modality == "image":
        return [IMAGE_GEN]

    if output_modality == "video":
        return [VIDEO_GEN]

    if output_modality == "audio":
        return [AUDIO_GEN]

    if input_modality == "audio":
        return [MULTIMODAL, TEXT_ONLY]

    # 默认: text in → text out
    return [TEXT_ONLY, MULTIMODAL]


# 常量引用
TEXT_ONLY = "text-only"
MULTIMODAL = "multimodal"
IMAGE_GEN = "image-gen"
VIDEO_GEN = "video-gen"
AUDIO_GEN = "audio-gen"