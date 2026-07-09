#!/usr/bin/env python3
"""
SMR model size estimator — C 阶段 (估算兜底, v2.1)
v2.1 加规则覆盖 51 unknown 中的 ~30 个 (实际 210 模型测试: unknown 51 → ~21 个)

兜底原则 (老大 7/2 钦定):
- 闭源旗舰 (gpt-4/o1/o3/claude-opus/gemini-pro) → 默认 >200B
- 闭源中端 → 默认 50-200B
- 完全未知 → unknown
"""
import re
import json
import sys
from pathlib import Path
from datetime import datetime, timezone


# 兜底规则: (pattern, size_class, confidence, note)
# 顺序很重要: 具体的先 (gemini-3.5-flash) 再通用 (gemini-flash)
ESTIMATE_RULES = [
    # === Gemini 3.x 系列 (新发布,按型号分) ===
    (re.compile(r"^gemini-3\.1-pro", re.IGNORECASE), ">200B", 0.85, "Gemini 3.1 Pro 旗舰"),
    (re.compile(r"^gemini-3\.5-flash", re.IGNORECASE), "50-200B", 0.7, "Gemini 3.5 Flash"),
    (re.compile(r"^gemini-3\.1-pro", re.IGNORECASE), ">200B", 0.7, ""),
    (re.compile(r"^gemini-3\.1-flash-lite", re.IGNORECASE), "50-200B", 0.6, "Gemini 3.1 Flash Lite"),
    (re.compile(r"^gemini-3-flash", re.IGNORECASE), "50-200B", 0.6, "Gemini 3 Flash"),
    (re.compile(r"^gemini-3-pro", re.IGNORECASE), ">200B", 0.7, "Gemini 3 Pro"),
    (re.compile(r"^gemini-2\.5-pro", re.IGNORECASE), ">200B", 0.7, "Gemini 2.5 Pro"),
    (re.compile(r"^gemini-2\.5-flash-lite", re.IGNORECASE), "13-70B", 0.6, "Gemini 2.5 Flash Lite"),
    (re.compile(r"^gemini-2\.5-flash", re.IGNORECASE), "50-200B", 0.7, "Gemini 2.5 Flash"),

    # === Gemini 旧版本 ===
    (re.compile(r"^gemini-?ultra", re.IGNORECASE), ">200B", 0.7, "Gemini Ultra 估算"),
    (re.compile(r"^gemini-2\.0-pro", re.IGNORECASE), ">200B", 0.7, ""),
    (re.compile(r"^gemini-1\.5-pro", re.IGNORECASE), ">200B", 0.7, ""),
    (re.compile(r"^gemini-1\.0-pro", re.IGNORECASE), ">200B", 0.7, ""),
    (re.compile(r"^gemini-pro", re.IGNORECASE), ">200B", 0.7, ""),

    # === Gemini Flash 全系列 ===
    (re.compile(r"^gemini-flash", re.IGNORECASE), "50-200B", 0.6, "Gemini Flash 系列估算"),
    (re.compile(r"^gemini-2\.0-flash", re.IGNORECASE), "50-200B", 0.6, ""),
    (re.compile(r"^gemini-1\.5-flash", re.IGNORECASE), "50-200B", 0.6, ""),
    (re.compile(r"^gemini-nano", re.IGNORECASE), "<13B", 0.6, "Gemini Nano 终端侧"),

    # === GPT 全系列 ===
    (re.compile(r"^gpt-4(?!-mini)(?!-o-mini)", re.IGNORECASE), ">200B", 0.7, "GPT-4 闭源旗舰估算"),
    (re.compile(r"^gpt-4-turbo", re.IGNORECASE), ">200B", 0.7, ""),
    (re.compile(r"^gpt-4o([\-\.]|$)", re.IGNORECASE), "50-200B", 0.6, "GPT-4o 多模态估算"),
    (re.compile(r"^gpt-4o-mini", re.IGNORECASE), "13-70B", 0.6, "GPT-4o-mini 较小"),
    (re.compile(r"^gpt-3\.5", re.IGNORECASE), "13-70B", 0.5, "GPT-3.5 历史估算"),

    # === o1/o3/o4 推理系列 ===
    (re.compile(r"^o1([\-\.]|$)", re.IGNORECASE), ">200B", 0.7, "o1 推理模型估算"),
    (re.compile(r"^o1-preview", re.IGNORECASE), ">200B", 0.7, ""),
    (re.compile(r"^o1-mini", re.IGNORECASE), "50-200B", 0.6, "o1-mini 更小估算"),
    (re.compile(r"^o3([\-\.]|$)", re.IGNORECASE), ">200B", 0.7, ""),
    (re.compile(r"^o4([\-\.]|$)", re.IGNORECASE), ">200B", 0.7, ""),

    # === Claude 系列 ===
    (re.compile(r"^claude-?3?-?opus", re.IGNORECASE), ">200B", 0.7, "Claude Opus 系列"),
    (re.compile(r"^claude-opus", re.IGNORECASE), ">200B", 0.7, ""),
    (re.compile(r"^claude-3-opus", re.IGNORECASE), ">200B", 0.7, ""),
    (re.compile(r"^claude-3\.5-opus", re.IGNORECASE), ">200B", 0.7, ""),
    (re.compile(r"^claude-?3?-?sonnet", re.IGNORECASE), "50-200B", 0.6, "Claude Sonnet 估算"),
    (re.compile(r"^claude-sonnet", re.IGNORECASE), "50-200B", 0.6, ""),
    (re.compile(r"^claude-3\.5-sonnet", re.IGNORECASE), "50-200B", 0.6, ""),
    (re.compile(r"^claude-?3?-?haiku", re.IGNORECASE), "50-200B", 0.6, "Claude Haiku 估算"),
    (re.compile(r"^claude-haiku", re.IGNORECASE), "50-200B", 0.6, ""),

    # === DeepSeek 系列 ===
    (re.compile(r"^deepseek-v3", re.IGNORECASE), ">200B", 0.85, "DeepSeek-V3 = 671B MoE"),
    (re.compile(r"^deepseek-r1", re.IGNORECASE), ">200B", 0.85, "DeepSeek-R1 = 671B MoE"),
    (re.compile(r"^deepseek-v4-pro", re.IGNORECASE), ">200B", 0.8, "DeepSeek-V4 Pro 旗舰"),
    (re.compile(r"^deepseek-v4-flash", re.IGNORECASE), "50-200B", 0.7, "DeepSeek-V4 Flash 轻量"),
    (re.compile(r"^deepseek-v4", re.IGNORECASE), ">200B", 0.8, "DeepSeek-V4 系列"),
    (re.compile(r"^deepseek-v2", re.IGNORECASE), ">200B", 0.8, "DeepSeek-V2 = 236B MoE"),
    (re.compile(r"^deepseek-coder", re.IGNORECASE), "13-70B", 0.7, "DeepSeek Coder 估算"),

    # === Qwen 闭源 ===
    (re.compile(r"^qwen-max", re.IGNORECASE), ">200B", 0.8, "Qwen-Max 通义闭源旗舰"),
    (re.compile(r"^qwen-plus", re.IGNORECASE), "50-200B", 0.7, "Qwen-Plus 闭源"),
    (re.compile(r"^qwen-turbo", re.IGNORECASE), "13-70B", 0.6, "Qwen-Turbo 闭源"),

    # === Qwen3.5 (已知规格) ===
    (re.compile(r"^qwen3\.5-397b", re.IGNORECASE), ">200B", 0.9, "Qwen3.5-397B"),
    (re.compile(r"^qwen3\.5-122b", re.IGNORECASE), ">200B", 0.9, "Qwen3.5-122B"),
    (re.compile(r"^qwen3\.5-35b", re.IGNORECASE), "13-70B", 0.9, "Qwen3.5-35B"),
    (re.compile(r"^qwen3\.5-27b", re.IGNORECASE), "13-70B", 0.9, "Qwen3.5-27B"),

    # === Qwen3-Next ===
    (re.compile(r"^qwen3-next-80b", re.IGNORECASE), "70-200B", 0.9, "Qwen3-Next-80B-A3B"),
    (re.compile(r"^qwen3-next", re.IGNORECASE), "70-200B", 0.7, "Qwen3-Next 系列"),

    # === Mistral 系列 (估 + 已知) ===
    (re.compile(r"^mistral-large-3", re.IGNORECASE), "70-200B", 0.7, "Mistral Large 3 (估 70-200B)"),
    (re.compile(r"^Mistral-Large-Instruct", re.IGNORECASE), "70-200B", 0.8, "Mistral Large Instruct 2407"),
    (re.compile(r"^mistral-medium-3\.5", re.IGNORECASE), "13-70B", 0.7, "Mistral Medium 3.5"),
    (re.compile(r"^mistral-medium", re.IGNORECASE), "13-70B", 0.7, "Mistral Medium 系列"),
    (re.compile(r"^mistral-small-4", re.IGNORECASE), "<13B", 0.7, "Mistral Small 4"),
    (re.compile(r"^Mistral-Small-Instruct", re.IGNORECASE), "<13B", 0.8, "Mistral Small Instruct 2409"),
    (re.compile(r"^mistral-small", re.IGNORECASE), "<13B", 0.6, "Mistral Small 系列"),
    (re.compile(r"^mistral-7b", re.IGNORECASE), "13-70B", 0.9, "Mistral 7B"),
    (re.compile(r"^mistral-8b", re.IGNORECASE), "13-70B", 0.9, "Mistral 8B"),
    (re.compile(r"^mistral-large", re.IGNORECASE), "70-200B", 0.6, "Mistral Large 系列默认"),
    (re.compile(r"^codestral", re.IGNORECASE), "13-70B", 0.7, "Codestral 代码模型"),
    (re.compile(r"^devstral", re.IGNORECASE), "13-70B", 0.7, "Devstral 开发者模型"),
    (re.compile(r"^magistral-medium", re.IGNORECASE), "13-70B", 0.7, "Magistral Medium 推理"),

    # === Cohere Command / North ===
    (re.compile(r"^command-a-2", re.IGNORECASE), ">200B", 0.8, "Cohere Command A 2"),
    (re.compile(r"^command-a-reasoning", re.IGNORECASE), ">200B", 0.8, "Cohere Command A Reasoning"),
    (re.compile(r"^command-a-vision", re.IGNORECASE), ">200B", 0.8, "Cohere Command A Vision"),
    (re.compile(r"^command-a", re.IGNORECASE), ">200B", 0.7, "Cohere Command A"),
    (re.compile(r"^command-r-plus", re.IGNORECASE), ">200B", 0.7, "Cohere Command R+"),
    (re.compile(r"^command-r", re.IGNORECASE), "13-70B", 0.6, "Cohere Command R"),
    (re.compile(r"^north-mini", re.IGNORECASE), "13-70B", 0.7, "Cohere North Mini 估算"),
    (re.compile(r"^c4ai-command-r-plus", re.IGNORECASE), ">200B", 0.8, "Cohere Command R+"),

    # === Meta Llama 4 ===
    (re.compile(r"^llama-4-maverick", re.IGNORECASE), ">200B", 0.85, "Llama 4 Maverick 400B MoE"),
    (re.compile(r"^llama-4-scout", re.IGNORECASE), "70-200B", 0.7, "Llama 4 Scout 109B MoE (估算)"),
    (re.compile(r"^llama-4", re.IGNORECASE), ">200B", 0.7, "Llama 4 系列默认大型"),

    # === Microsoft Phi ===
    (re.compile(r"^phi-4", re.IGNORECASE), "13-70B", 0.8, "Phi-4 (估 14B MoE)"),
    (re.compile(r"^phi-3", re.IGNORECASE), "13-70B", 0.8, "Phi-3 (估 14B)"),
    (re.compile(r"^phi-2", re.IGNORECASE), "<13B", 0.8, "Phi-2 (2.7B)"),

    # === IBM Granite ===
    (re.compile(r"^granite-4", re.IGNORECASE), "13-70B", 0.7, "Granite 4 系列"),
    (re.compile(r"^granite-3", re.IGNORECASE), "13-70B", 0.7, "Granite 3 系列"),
    (re.compile(r"^granite-", re.IGNORECASE), "<13B", 0.6, "Granite 系列微"),

    # === NVIDIA Nemotron 全系列 ===
    (re.compile(r"^nemotron-3-ultra", re.IGNORECASE), "50-200B", 0.7, "Nemotron-3 Ultra"),
    (re.compile(r"^nemotron-3\.5", re.IGNORECASE), ">200B", 0.7, "Nemotron-3.5 系列"),
    (re.compile(r"^nemotron-3-super", re.IGNORECASE), ">200B", 0.7, "Nemotron-3 Super"),
    (re.compile(r"^nemotron-3-nano", re.IGNORECASE), "<13B", 0.7, "Nemotron Nano 迷你"),

    # === Reka ===
    (re.compile(r"^reka-flash", re.IGNORECASE), "13-70B", 0.7, "Reka Flash"),
    (re.compile(r"^reka-edge", re.IGNORECASE), "<13B", 0.7, "Reka Edge 边缘"),
    (re.compile(r"^reka-core", re.IGNORECASE), ">200B", 0.7, "Reka Core"),

    # === Poolside Laguna ===
    (re.compile(r"^poolside-?laguna-m", re.IGNORECASE), "13-70B", 0.6, "Poolside Laguna M"),
    (re.compile(r"^poolside-?laguna-xs", re.IGNORECASE), "<13B", 0.6, "Poolside Laguna XS"),
    (re.compile(r"^laguna-m", re.IGNORECASE), "13-70B", 0.6, "Poolside Laguna M"),
    (re.compile(r"^laguna-xs", re.IGNORECASE), "<13B", 0.6, "Poolside Laguna XS"),

    # === Stepfun ===
    (re.compile(r"^stepfun-?step-3\.7", re.IGNORECASE), "50-200B", 0.6, "Stepfun Step-3.7 Flash"),
    (re.compile(r"^step-?3\.5-flash", re.IGNORECASE), "50-200B", 0.6, "Step-3.5 Flash"),
    (re.compile(r"^step-?3\.7-flash", re.IGNORECASE), "50-200B", 0.6, "Step-3.7 Flash"),
    (re.compile(r"^step-?3", re.IGNORECASE), ">200B", 0.6, "Step-3 系列默认大型"),

    # === Xiaomi MiMo ===
    (re.compile(r"^mimo-v?2", re.IGNORECASE), "13-70B", 0.7, "MiMo V2 系列"),
    (re.compile(r"^mimo-v?2-flash", re.IGNORECASE), "13-70B", 0.7, "MiMo V2 Flash 轻量"),

    # === Agnes / Sarvam / Nex ===
    (re.compile(r"^agnes-2\.0", re.IGNORECASE), "13-70B", 0.6, "Agnes 2.0"),
    (re.compile(r"^agnes-1\.5", re.IGNORECASE), "<13B", 0.6, "Agnes 1.5"),
    (re.compile(r"^sarvam-m", re.IGNORECASE), "13-70B", 0.6, "Sarvam-M"),
    (re.compile(r"^nex-n2-pro", re.IGNORECASE), "13-70B", 0.6, "Nex-N2 Pro"),

    # === Shanghai AI Lab InternLM ===
    (re.compile(r"^Intern-S1-mini", re.IGNORECASE), "<13B", 0.8, "Intern-S1 Mini"),
    (re.compile(r"^Intern-S1", re.IGNORECASE), "70-200B", 0.7, "Intern-S1 (估 70-200B)"),
    (re.compile(r"^Intern-S2-Preview", re.IGNORECASE), "70-200B", 0.6, "Intern-S2 Preview"),

    # === Meituan LongCat ===
    (re.compile(r"^LongCat-Flash-Lite", re.IGNORECASE), "<13B", 0.6, "LongCat Flash Lite"),
    (re.compile(r"^LongCat", re.IGNORECASE), "13-70B", 0.6, "LongCat"),

    # === Media AI AntAngelMed ===
    (re.compile(r"^AntAngelMed", re.IGNORECASE), "13-70B", 0.6, "AntAngelMed 医疗"),

    # === Qwen Image/Edit 多模态 ===
    (re.compile(r"^Qwen-Image-Edit", re.IGNORECASE), "13-70B", 0.6, "Qwen Image Edit 多模态"),

    # === Moonshot Kimi K2 ===
    (re.compile(r"^kimi-k2", re.IGNORECASE), ">200B", 0.85, "Kimi K2 1T MoE (32B activated)"),

    # === MiniMax 老大自己模型 ===
    (re.compile(r"^minimax-m3", re.IGNORECASE), ">200B", 0.8, "MiniMax-M3 (老大旗舰)"),
    (re.compile(r"^minimax-m2", re.IGNORECASE), ">200B", 0.7, "MiniMax-M2 系列"),
    (re.compile(r"^MiniMax-M3", re.IGNORECASE), ">200B", 0.8, ""),
    (re.compile(r"^MiniMax-M1-80k", re.IGNORECASE), "70-200B", 0.8, "MiniMax-M1-80k (老大 M1)"),

    # === Doubao / ERNIE / Hunyuan / Spark / GLM ===
    (re.compile(r"^doubao-pro", re.IGNORECASE), ">200B", 0.7, ""),
    (re.compile(r"^doubao-lite", re.IGNORECASE), "13-70B", 0.6, ""),
    (re.compile(r"^ernie-4", re.IGNORECASE), ">200B", 0.7, ""),
    (re.compile(r"^ernie-3", re.IGNORECASE), "13-70B", 0.6, ""),
    (re.compile(r"^hunyuan-pro", re.IGNORECASE), ">200B", 0.7, ""),
    (re.compile(r"^hunyuan-standard", re.IGNORECASE), "50-200B", 0.6, ""),
    (re.compile(r"^spark-v?4", re.IGNORECASE), ">200B", 0.7, ""),
    (re.compile(r"^spark-v?3", re.IGNORECASE), "13-70B", 0.6, ""),
    (re.compile(r"^glm-5", re.IGNORECASE), ">200B", 0.8, "GLM-5 系列"),
    (re.compile(r"^glm-4\.6", re.IGNORECASE), ">200B", 0.7, ""),
    (re.compile(r"^glm-4", re.IGNORECASE), ">200B", 0.7, ""),
    (re.compile(r"^glm-3", re.IGNORECASE), "13-70B", 0.6, ""),

    # === SMR 内部 router 伪模型 (应该 unknown) ===
    (re.compile(r"^(auto|fusion|free-router|kilo-auto|bazaarlink-auto|compound-mini|compound|openrouter/free)$", re.IGNORECASE), "unknown", 1.0, "SMR 内部路由伪模型"),

    # === 短名/变体兜底 (B 阶段跳过, 规则兜底) ===
    (re.compile(r"^qwen3-coder-next", re.IGNORECASE), "70-200B", 0.5, "qwen3-coder-next 估算升级版"),
    (re.compile(r"^qwen3-coder$", re.IGNORECASE), "13-70B", 0.5, "qwen3-coder 短名估算"),
    (re.compile(r"^qwen3-coder\b", re.IGNORECASE), "13-70B", 0.5, "qwen3-coder 系列"),
    (re.compile(r"^nous-coder", re.IGNORECASE), "<13B", 0.5, "Nous Research coder 派生微调"),
    (re.compile(r"^big-pickle", re.IGNORECASE), "unknown", 1.0, "Playground 内部模型,无法查证"),
    (re.compile(r"^openrouter/free", re.IGNORECASE), "unknown", 1.0, "OpenRouter 顶层聚合器非模型"),
]



def normalize_model_id(model_id: str) -> str:
    if "/" in model_id:
        model_id = model_id.split("/", 1)[1]
    if model_id.endswith(":free") or model_id.endswith(":paid"):
        model_id = model_id.rsplit(":", 1)[0]
    return model_id


def estimate_one(model_id: str) -> dict | None:
    norm = normalize_model_id(model_id)
    for pattern, size_class, conf, note in ESTIMATE_RULES:
        if pattern.search(norm):
            return {
                "size_b": None,
                "size_class": size_class,
                "source": "estimate",
                "confidence": conf,
                "note": note or f"match {pattern.pattern}",
                "normalized_to": norm,
            }
    return None


def estimate_all(model_ids: list[str]) -> dict:
    estimated = []
    unknown = []
    for mid in model_ids:
        r = estimate_one(mid)
        if r:
            estimated.append({"model_id": mid, **r})
        else:
            unknown.append(mid)
    return {
        "estimated": estimated,
        "unknown": unknown,
        "stats": {
            "total": len(model_ids),
            "estimated": len(estimated),
            "unknown": len(unknown),
            "estimated_rate": len(estimated) / max(1, len(model_ids)),
        },
    }


def main():
    import argparse
    p = argparse.ArgumentParser(description="SMR model size estimator (C v2.1)")
    p.add_argument("--input", help="模型 ID 列表 JSON")
    p.add_argument("--test", action="store_true")
    args = p.parse_args()

    if args.test:
        test_ids = [
            "gemini-3.1-pro-preview", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite",
            "gemini-3.5-flash", "gemini-3-flash-preview", "gemini-2.5-pro",
            "command-a-2", "command-a-vision", "command-a-reasoning", "command-a",
            "mistral-large-3", "mistral-medium-3.5", "mistral-small-4",
            "phi-4-reasoning", "phi-3", "phi-2",
            "granite-4.0-h-micro", "granite-3",
            "nemotron-3-nano-omni-reasoning", "nemotron-3-ultra", "nemotron-3-super",
            "reka-flash", "reka-edge", "reka-core",
            "poolside-laguna-m.1", "poolside-laguna-xs.2",
            "stepfun-step-3.7-flash", "step-3.7-flash",
            "mimo-v2", "mimo-v2.5", "mimo-v2-flash",
            "north-mini-code",
            "agnes-2.0-flash", "agnes-1.5-flash",
            "sarvam-m",
            "qwen3.5-397b", "qwen3-next-80b", "qwen3-coder-next",
            "kimi-k2.6", "kimi-k2.7-code",
            "llama-4-maverick", "llama-4-scout",
            "minimax-m3", "MiniMax-M3", "MiniMax-M1-80k",
            "deepseek-v4-pro", "deepseek-v4-flash",
            "glm-5", "glm-5.1", "glm-5.2",
            "Auto", "fusion", "free-router", "compound",
            "Intern-S1", "Intern-S1-mini", "Intern-S2-Preview",
            "LongCat-Flash-Lite", "AntAngelMed", "Nex-N2-Pro",
            "Mistral-Large-Instruct-2407", "Mistral-Small-Instruct-2409",
            "qwen-image-edit",
            "完全未知的-model-id-xyz",
        ]
        print(f"{'model_id':<35} {'size_class':<10} {'conf':<6} {'note'}")
        print("-" * 90)
        ok = 0
        for mid in test_ids:
            r = estimate_one(mid)
            if r:
                print(f"{mid:<35} {r['size_class']:<10} {r['confidence']:<6} {r['note']}")
                ok += 1
            else:
                print(f"{mid:<35} {'unknown':<10} {0.0:<6} ⚠️ 完全未知")
        print(f"\n📊 兜底命中: {ok}/{len(test_ids)} ({ok/len(test_ids)*100:.1f}%)")
        return 0

    if not args.input:
        p.print_help()
        return 1

    ids = json.loads(Path(args.input).read_text())
    result = estimate_all(ids)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\n✅ 估算 {result['stats']['estimated']}/{result['stats']['total']} ({result['stats']['estimated_rate']*100:.1f}%)")
    print(f"❓ 仍未知: {len(result['unknown'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
