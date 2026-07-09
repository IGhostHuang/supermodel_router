#!/usr/bin/env python3
"""get_models.py — 从 SMR admin API 拉取全量 model_id 列表"""

import httpx
import json
from pathlib import Path

def main():
    cache_file = Path(__file__).parent.parent / "data" / "model_ids.json"
    resp = httpx.get("http://localhost:6473/v1/admin/models", timeout=20)
    resp.raise_for_status()
    data = resp.json()
    models = data.get("models", data)
    if isinstance(models, list):
        ids = [m.get("id") if isinstance(m, dict) else m for m in models]
    else:
        ids = list(models.keys())
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(ids, f, ensure_ascii=False, indent=2)
    print(f"[OK] 已保存 {len(ids)} 个 model_id 到 {cache_file}")

if __name__ == "__main__":
    main()
