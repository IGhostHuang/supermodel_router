#!/usr/bin/env python3
"""A3 单元测试 (10 个覆盖用例,期望 >=7 直接识别)"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from tools.parse_model_size import _parse_model_size

CASES = [
    # model_id, expected_size_b, expected_size_class
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
]

passed = 0
failed = 0
for model_id, exp_size, exp_class in CASES:
    res = _parse_model_size(model_id)
    ok = (res["size_b"] == exp_size and res["size_class"] == exp_class)
    if ok:
        passed += 1
        status = "PASS"
    else:
        failed += 1
        status = "FAIL"
    print(f"{status}: {model_id} -> {res['size_b']}/{res['size_class']} (期望 {exp_size}/{exp_class})")

print(f"\n结果: {passed}/{len(CASES)} PASS")
sys.exit(0 if failed == 0 else 1)
