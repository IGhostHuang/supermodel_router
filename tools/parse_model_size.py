#!/usr/bin/env python3
"""
SMR model size parser — A 阶段 (强规则,期望 60-70% 命中)
输入: model_id 列表
输出: data/model_size_cache.json

老大原话 (2026-07-02 08:25):
> "识别模型体量,明确标记参数量,如 550b 397b 等"

用法:
    python parse_model_size.py --input models.json --output data/model_size_cache.json
    python parse_model_size.py --test  # 跑内置测试 10 例

size_class 4 档 (echo 默认值,等老大拍):
    <13B / 13-70B / 70-200B / >200B / unknown
"""
import json
import re
import argparse
import sys
from pathlib import Path
from datetime import datetime, timezone


# === 强规则:正则 + 大小范围 + 排除 false positive ===
PARSE_RULES = [
    # 标准 数字+b
    (re.compile(r"(\d+\.?\d*)\s*[bB](?:illion)?\b"), "extracted", "数字+B"),
    # Qwen2.5-72B-Instruct 形式
    (re.compile(r"(\d+\.?\d*)b(?:-[a-z]+)*$", re.IGNORECASE), "extracted", "末位数字B"),
    # 数字+B 后缀
    (re.compile(r"(\d+\.?\d*)[bB]\b"), "extracted", "B结尾"),
]


# === False positive 黑名单 (这些 B 不是参数量) ===
FALSE_POSITIVE_PATTERNS = [
    re.compile(r"\d+k\b", re.IGNORECASE),  # 16k context token
    re.compile(r"-\d+ctx\b", re.IGNORECASE),
    re.compile(r"-\d+k-context\b", re.IGNORECASE),
    re.compile(r"\d+ctx\b", re.IGNORECASE),
]


# === MOE 模型特殊处理 (e.g. 8x7b 不是总参数) ===
MOE_PATTERNS = [
    re.compile(r"(\d+)x(\d+\.?\d*)[bB]", re.IGNORECASE),
]


def classify_size(size_b: float) -> str:
    """4 档分级 (老大拍板)"""
    if size_b is None:
        return "unknown"
    if size_b < 13:
        return "<13B"
    if size_b < 70:
        return "13-70B"
    if size_b <= 200:
        return "70-200B"
    return ">200B"


def is_false_positive(model_id: str) -> bool:
    """是否 false positive (16k/tokens 不是参数量)"""
    for p in FALSE_POSITIVE_PATTERNS:
        if p.search(model_id):
            return True
    return False


def is_moe(model_id: str) -> bool:
    """MOE 模型特殊标记 (专家数 x 单专家)"""
    for p in MOE_PATTERNS:
        if p.search(model_id):
            return True
    return False


def parse_one(model_id: str) -> dict:
    """解析单个 model_id,返回 size info"""
    base = {
        "model_id": model_id,
        "size_b": None,
        "size_class": "unknown",
        "source": "regex",
        "confidence": 0.0,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "note": "",
    }

    # false positive 检查
    if is_false_positive(model_id):
        return {**base, "note": "false_positive (数字+ktoken等)"}

    # MOE 模型特殊标记
    if is_moe(model_id):
        m = MOE_PATTERNS[0].search(model_id)
        if m:
            experts = int(m.group(1))
            per_expert = float(m.group(2))
            return {
                **base,
                "size_b": experts * per_expert,  # 总参估算 (粗略)
                "size_class": classify_size(experts * per_expert),
                "note": f"MOE {experts}x{per_expert}B = total ≈ {experts * per_expert}B (单 expert≠单卡加载)",
                "confidence": 0.6,
            }

    # 强规则解析
    for pattern, rule_type, rule_label in PARSE_RULES:
        m = pattern.search(model_id)
        if m:
            try:
                size = float(m.group(1))
            except (ValueError, IndexError):
                continue

            # 异常范围
            if size < 0.1 or size > 2000:
                return {
                    **base,
                    "size_b": size,
                    "size_class": "anomaly",
                    "note": f"异常数字 {size}B (期望 0.1-2000)",
                    "confidence": 0.3,
                }

            return {
                **base,
                "size_b": size,
                "size_class": classify_size(size),
                "note": rule_label,
                "confidence": 0.95,
            }

    # 未识别
    return {**base, "note": "未识别,需全网搜或估算"}


def parse_all(model_ids: list[str]) -> list[dict]:
    return [parse_one(m) for m in model_ids]


def run_test_cases() -> dict:
    """内置 10 个测试用例 (覆盖率)"""
    test_cases = [
        ("qwen3-550b", 550.0, ">200B"),
        ("llama-3.1-405b-instruct", 405.0, ">200B"),
        ("mistral-nemo-12b", 12.0, "<13B"),
        ("qwen2.5-coder-32b-instruct", 32.0, "13-70B"),
        ("llama-3.2-3b", 3.0, "<13B"),
        ("deepseek-v3", None, "unknown"),
        ("claude-opus-4", None, "unknown"),
        ("gpt-3.5-turbo-16k", None, "unknown"),  # false positive
        ("qwen2.5-72b-instruct", 72.0, "70-200B"),
        ("gpt-4o", None, "unknown"),
        # bonus
        ("mixtral-8x7b-instruct", 56.0, "13-70B"),  # MOE 8x7 = 56B
        ("gpt-4-turbo", None, "unknown"),  # 需估算
    ]
    results = []
    correct = 0
    for model_id, expected_size, expected_class in test_cases:
        r = parse_one(model_id)
        size_match = (
            (r["size_b"] is None and expected_size is None)
            or (r["size_b"] is not None and expected_size is not None
                and abs(r["size_b"] - expected_size) < 0.5)
        )
        class_match = r["size_class"] == expected_class
        ok = size_match and class_match
        if ok:
            correct += 1
        results.append({
            "model_id": model_id,
            "got": (r["size_b"], r["size_class"]),
            "expected": (expected_size, expected_class),
            "ok": ok,
            "note": r["note"],
        })
    return {
        "total": len(test_cases),
        "correct": correct,
        "accuracy": correct / len(test_cases),
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description="SMR model size parser (A 阶段)")
    parser.add_argument("--input", help="输入模型 ID JSON 文件 (list[str])")
    parser.add_argument("--output", help="输出 cache JSON 路径")
    parser.add_argument("--test", action="store_true", help="跑内置测试")
    args = parser.parse_args()

    if args.test:
        result = run_test_cases()
        print(f"测试结果: {result['correct']}/{result['total']} = {result['accuracy']*100:.1f}%")
        print()
        print(f"{'model_id':<40} {'got':<22} {'expected':<22} {'ok':<4} {'note'}")
        print("-" * 110)
        for r in result["results"]:
            got_str = f"{r['got'][0]} {r['got'][1]}"
            exp_str = f"{r['expected'][0]} {r['expected'][1]}"
            print(f"{r['model_id']:<40} {got_str:<22} {exp_str:<22} {'✅' if r['ok'] else '❌':<4} {r['note']}")
        return 0 if result["accuracy"] >= 0.7 else 1

    if not args.input or not args.output:
        parser.print_help()
        return 1

    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    model_ids = json.loads(input_path.read_text())
    if not isinstance(model_ids, list):
        print(f"ERROR: {args.input} 应为 list[str]")
        return 1

    results = parse_all(model_ids)

    # 统计
    by_class = {}
    identified = 0
    for r in results:
        cls = r["size_class"]
        by_class[cls] = by_class.get(cls, 0) + 1
        if r["size_b"] is not None:
            identified += 1

    cache = {
        "version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "identified_by_regex": identified,
        "identified_rate": identified / len(results),
        "by_size_class": by_class,
        "models": results,
    }

    output_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False))
    print(f"✅ 写 {len(results)} 模型 → {output_path}")
    print(f"   强规则命中: {identified}/{len(results)} ({cache['identified_rate']*100:.1f}%)")
    print(f"   size_class 分布: {by_class}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
