#!/usr/bin/env python3
"""
SMR model size merge — 端到端合并 A (parse) + C (estimate) 阶段
输入: SMR /v1/admin/models 输出 (200+ 模型)
输出: data/model_size_cache.json (4 档 size_class 全量覆盖)

老大原话 (2026-07-02 08:25):
> "识别模型体量,明确标记参数量,如 550b 397b 等没有准确数值的全网搜索参数量,至少判段是否大于 200b"

执行顺序:
1. 取 SMR 模型列表 → normalize (去 provider/ 和 :free)
2. parse (强规则) → 60-70% 命中
3. estimate (兜底) → 20-30% 命中
4. 合并 → size_class 分布统计
5. 仍 unknown 的列出来 (B 阶段 web_search)

用法:
    python merge_size_cache.py --output data/model_size_cache.json
    python merge_size_cache.py --test  # 用预存 /tmp/smr_all_models.json
"""
import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone
from urllib.request import urlopen, Request

sys.path.insert(0, str(Path(__file__).parent))
from parse_model_size import parse_one, classify_size  # noqa: E402
from estimate_size import estimate_one, normalize_model_id  # noqa: E402


def parse_v2(model_id: str) -> dict:
    """升级版 parse: 标准化 + 提取 MoE active 参数"""
    raw = model_id
    norm = normalize_model_id(model_id)

    # 阶段 1: 强规则
    r = parse_one(norm)

    # 阶段 2: MoE -aXXb activated 参数兜底
    if r["size_b"] is None:
        # 优先匹配 `-数字b` 紧跟前面的 -aXXb (e.g. qwen3-next-80b-a3b, active=3B)
        # 模式 1: `数字b-aXXb` → total=数字, active=XX
        m1 = re.search(r"(\d+\.?\d*)b-a(\d+\.?\d*)b", norm, re.IGNORECASE)
        if m1:
            total = float(m1.group(1))
            active = float(m1.group(2))
            return {
                "model_id": raw,
                "size_b": active,
                "size_class": classify_size(active),
                "source": "regex_moe_active",
                "confidence": 0.9,
                "note": f"MoE total={total}B active={active}B (从 {norm})",
                "normalized_to": norm,
                "total_b": total,
            }
        # 模式 2: `-aXXb` (无 total) → active=XX
        m2 = re.search(r"-a(\d+\.?\d*)b", norm, re.IGNORECASE)
        if m2:
            active = float(m2.group(1))
            return {
                "model_id": raw,
                "size_b": active,
                "size_class": classify_size(active),
                "source": "regex_moe_active",
                "confidence": 0.85,
                "note": f"MoE active={active}B (从 {norm})",
                "normalized_to": norm,
            }
        # 模式 3: 单纯 qwen3-coder 等
        # 跳过,留给 estimate

    return {**r, "model_id": raw}


def classify_one(model_id: str) -> dict:
    """端到端: parse → estimate → unknown fallback"""
    norm = normalize_model_id(model_id)

    # 阶段 1: parse (regex + MoE)
    r = parse_v2(model_id)

    if r["size_b"] is not None or r["size_class"] in ("anomaly",):
        return r

    # 阶段 2: estimate (兜底规则)
    est = estimate_one(model_id)
    if est:
        return {
            "model_id": model_id,
            **est,
            "size_b": est.get("size_b"),
            "extracted_at": datetime.now(timezone.utc).isoformat(),
        }

    # 阶段 3: 仍 unknown
    return {
        "model_id": model_id,
        "size_b": None,
        "size_class": "unknown",
        "source": "unknown",
        "confidence": 0.0,
        "note": "需 B 阶段 web_search",
        "normalized_to": norm,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_smr_models(base_url: str = "http://localhost:6473") -> list[str]:
    """从 SMR admin API 拿模型列表"""
    req = Request(f"{base_url}/v1/admin/models")
    data = json.loads(urlopen(req, timeout=8).read())
    models = data.get("models", data.get("data", []))
    return [m.get("id", m.get("model_id", "?")) for m in models]


def merge(model_ids: list[str]) -> dict:
    """合并所有模型分类"""
    classified = [classify_one(m) for m in model_ids]

    by_class = {"<13B": 0, "13-70B": 0, "70-200B": 0, ">200B": 0, "unknown": 0, "anomaly": 0}
    by_source = {"regex": 0, "regex_active": 0, "regex_moe_active": 0, "estimate": 0, "unknown": 0}
    need_web_search = []

    for c in classified:
        cls = c.get("size_class", "unknown")
        by_class[cls] = by_class.get(cls, 0) + 1
        src = c.get("source", "unknown")
        by_source[src] = by_source.get(src, 0) + 1
        if cls == "unknown":
            need_web_search.append(c["model_id"])

    return {
        "version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(classified),
        "by_size_class": by_class,
        "by_source": by_source,
        "covered": len(classified) - len(need_web_search),
        "covered_rate": (len(classified) - len(need_web_search)) / max(1, len(classified)),
        "need_web_search": need_web_search,
        "models": classified,
    }


def main():
    import argparse
    p = argparse.ArgumentParser(description="SMR model size merge (A+C end-to-end)")
    p.add_argument("--output", default="data/model_size_cache.json", help="输出路径")
    p.add_argument("--from-smr", action="store_true", help="从 SMR API 拉模型列表")
    p.add_argument("--input", help="预拉模型 ID JSON 路径")
    p.add_argument("--base-url", default="http://localhost:6473")
    args = p.parse_args()

    # 拿模型列表
    if args.from_smr:
        print(f"🔍 从 {args.base_url}/v1/admin/models 拉模型列表...")
        model_ids = fetch_smr_models(args.base_url)
    elif args.input:
        model_ids = json.loads(Path(args.input).read_text())
    else:
        # 默认从 /tmp/smr_all_models.json
        default_input = Path("/tmp/smr_all_models.json")
        if not default_input.exists():
            print("❌ 没模型列表源,请 --from-smr 或 --input /path/to/ids.json")
            return 1
        model_ids = json.loads(default_input.read_text())

    print(f"📊 共 {len(model_ids)} 个模型,正在分类...")
    result = merge(model_ids)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    print(f"✅ 写 {result['total']} 模型 → {out_path}")
    print(f"\n📈 size_class 分布:")
    for cls in ["<13B", "13-70B", "70-200B", ">200B", "unknown", "anomaly"]:
        cnt = result["by_size_class"].get(cls, 0)
        rate = cnt / max(1, result["total"]) * 100
        bar = "█" * int(rate / 2)
        print(f"   {cls:<10} {cnt:>4} ({rate:>5.1f}%) {bar}")
    print(f"\n🎯 覆盖率: {result['covered']}/{result['total']} ({result['covered_rate']*100:.1f}%)")
    print(f"📝 source 分布: {result['by_source']}")
    if result["need_web_search"]:
        print(f"\n🔍 仍 unknown 需 B 阶段 web_search ({len(result['need_web_search'])} 个):")
        for m in result["need_web_search"][:20]:
            print(f"   - {m}")
        if len(result["need_web_search"]) > 20:
            print(f"   ... 还有 {len(result['need_web_search'])-20} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
