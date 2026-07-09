#!/usr/bin/env python3
"""parse_model_size.py — SMR v3.15.0 A阶段 参数量识别工具

强规则（期望 60-70% 命中）:
- 正则提取: (\d+\.?\d*)[bB](?:illion|ase)?[mM]?
- 排除 false positive: 16k / 128k-turbo / gpt-3.5-16k（token 后缀）
- 排除 MoE 总数: 8x7b / 120b-a12b → unknown 标记 moe_unknown_b
- 数字范围 sanity: <0.1B 或 >2000B 视为 anomaly

输出: data/model_size_cache.json
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

# 项目根目录: supermodel_router/
BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_PATH = BASE_DIR / "data" / "model_size_cache.json"

# ---------------------------------------------------------------
# 正则
# ---------------------------------------------------------------
# 匹配 "550b" / "397B" / "120b-a12b" / "26b-a4b-it"
# 捕获组 1 = 数值部分
RE_SIZE = re.compile(r"(\d+\.?\d*)[bB](?:illion|ase)?", re.IGNORECASE)
# 排除 token 后缀: 16k / 128k / 256k 等
RE_TOKEN_SUFFIX = re.compile(r"\d+k$", re.IGNORECASE)
# 排除 MoE 格式: 8x7b / 26b-a4b / 120b-a12b
RE_MOE = re.compile(r"(\d+\.?\d*)[bB].*?-a\d+[bB]?", re.IGNORECASE)

# ---------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------


def _parse_one(model_id: str) -> dict:
    """解析单个 model_id 的参数量信息。"""
    now = datetime.now(timezone.utc).isoformat()

    # 1) MoE 检测: 120b-a12b / 26b-a4b / 8x7b
    moe_match = RE_MOE.search(model_id)
    if moe_match:
        return {
            "model_id": model_id,
            "size_b": None,
            "size_class": "unknown",
            "source": "regex",
            "confidence": 0.0,
            "extracted_at": now,
            "note": "moe_unknown_b",
        }

    # 2) 全局搜索参数量标记
    matches = list(RE_SIZE.finditer(model_id))
    if not matches:
        return {
            "model_id": model_id,
            "size_b": None,
            "size_class": "unknown",
            "source": "regex",
            "confidence": 0.0,
            "extracted_at": now,
            "note": "no_size_marker",
        }

    # 3) 取最后一个匹配（通常是模型本体参数，前面可能 provider 前缀有无关数字）
    raw = matches[-1].group(1)
    try:
        size_b = float(raw)
    except ValueError:
        return {
            "model_id": model_id,
            "size_b": None,
            "size_class": "unknown",
            "source": "regex",
            "confidence": 0.0,
            "extracted_at": now,
            "note": f"parse_failed:{raw}",
        }

    # 4) 排除 token 后缀误判: 如 "3.5-16k" 里的 3.5 被当成 3.5B
    #    检查匹配位置后面是否紧接 k（token 后缀）
    m = matches[-1]
    suffix = model_id[m.end() :]
    if RE_TOKEN_SUFFIX.search(suffix):
        return {
            "model_id": model_id,
            "size_b": None,
            "size_class": "unknown",
            "source": "regex",
            "confidence": 0.0,
            "extracted_at": now,
            "note": "token_suffix_excluded",
        }

    # 5) 范围 sanity
    if size_b < 0.1 or size_b > 2000:
        return {
            "model_id": model_id,
            "size_b": size_b,
            "size_class": "unknown",
            "source": "regex",
            "confidence": 0.3,
            "extracted_at": now,
            "note": "anomaly",
        }

    # 6) size_class
    if size_b < 13:
        size_class = "<13B"
    elif size_b < 70:
        size_class = "13-70B"
    elif size_b <= 200:
        size_class = "70-200B"
    else:
        size_class = ">200B"

    confidence = 0.95 if ":" not in model_id.split("/")[-1] else 0.85

    return {
        "model_id": model_id,
        "size_b": size_b,
        "size_class": size_class,
        "source": "regex",
        "confidence": confidence,
        "extracted_at": now,
        "note": "",
    }


def parse_model_list(model_ids: list[str]) -> dict[str, dict]:
    return {mid: _parse_one(mid) for mid in model_ids}


def save_cache(cache: dict[str, dict], path: Path = CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


def load_cache(path: Path = CACHE_PATH) -> dict[str, dict]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


# ---------------------------------------------------------------
# 单测 (A3)
# ---------------------------------------------------------------
A3_CASES = [
    ("qwen3-550b", 550.0, ">200B"),
    ("llama-3.1-405b-instruct", 405.0, ">200B"),
    ("mistral-nemo-12b", 12.0, "<13B"),
    ("qwen2.5-coder-32b-instruct", 32.0, "13-70B"),
    ("llama-3.2-3b", 3.0, "<13B"),
    ("deepseek-v3", None, "unknown"),
    ("claude-opus-4", None, "unknown"),
    ("gpt-3.5-turbo-16k", None, "unknown"),
    ("qwen2.5-72b-instruct", 72.0, "70-200B"),
    ("gpt-4o", None, "unknown"),
    # 额外 MoE 测试
    ("nvidia/nemotron-3-ultra-550b-a55b:free", None, "unknown"),
    ("google/gemma-4-26b-a4b-it:free", None, "unknown"),
    ("qwen3.5-122b-a10b", None, "unknown"),
]


def run_a3() -> bool:
    passed = 0
    failed = []
    for model_id, exp_size, exp_class in A3_CASES:
        result = _parse_one(model_id)
        ok = (result["size_b"] == exp_size) and (result["size_class"] == exp_class)
        tag = "✅" if ok else "❌"
        print(f"{tag} {model_id!r} -> size={result['size_b']} class={result['size_class']} note={result['note']!r}")
        if ok:
            passed += 1
        else:
            failed.append((model_id, result, exp_size, exp_class))

    print(f"\nA3 结果: {passed}/{len(A3_CASES)} 通过")
    return len(failed) == 0


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        ok = run_a3()
        sys.exit(0 if ok else 1)
    else:
        # 默认: 从 stdin 或参数读 model_id 列表
        if len(sys.argv) > 1:
            ids = sys.argv[1:]
        else:
            ids = [line.strip() for line in sys.stdin if line.strip()]
        cache = parse_model_list(ids)
        save_cache(cache)
        print(f"已写 {len(cache)} 条到 {CACHE_PATH}")
