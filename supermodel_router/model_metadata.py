"""
supermodel_router/model_metadata.py — 模型元数据扩展 (v3.10.0 Phase I)

职责:
1. 加载 state/model_metadata.json (人工/AI 标注的静态元数据)
2. 加载 engine_stats.json (运行时 EWMA 算 quality / speed)
3. 自动推断 tags (context_window >= 100K → "long-context" 等)
4. 提供 metadata lookup 给 ModelRegistry

设计 (R41 O(1) 写):
- _static: Dict[path, MetadataDict]  (从 JSON 加载, immutable)
- _dynamic: Dict[path, MetadataDict] (EWMA 算, 异步刷盘)
- merge 顺序: 静态优先 > 动态 > 自动推断 (空缺时 fallback)

R42 向后兼容:
- 老 ModelInfo 没 quality_score 等字段, 默认 0.0 + [] + "none"
- metadata 文件不存在 → 退到自动推断 + EWMA
- engine_stats.json 不存在 → 全部用 0.0

R40 改前 backup:
- _save_dynamic_async 写盘前自动 cp 到 state/.backups/model_metadata-*.json
"""
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("model_metadata")

METADATA_FILE = "model_metadata.json"
BACKUP_DIR = ".backups"
BACKUP_RETAIN = 20


class ModelMetadataStore:
    """模型元数据存储: 静态 (JSON) + 动态 (EWMA) + 自动推断 tags"""

    def __init__(self, state_dir: str = "."):
        self._lock = threading.RLock()
        self._state_dir = Path(state_dir)
        self._meta_file = self._state_dir / METADATA_FILE
        self._backup_dir = self._state_dir / BACKUP_DIR
        self._static: Dict[str, Dict[str, Any]] = {}  # path → {quality, speed, reasoning, tags}
        self._dynamic: Dict[str, Dict[str, Any]] = {}  # path → {quality (EWMA), speed (EWMA)}
        self._load_static()

    def _load_static(self):
        """启动时读 metadata.json

        v3.10.1 修 BUG-005: 同时支持平铺 + 嵌套 schema (向后兼容)
        - 平铺 (v3.10.0 seed): {model_id: metadata, ...} 或 {_version, model_id: metadata, ...}
        - 嵌套: {_version, models: {model_id: metadata, ...}}
        自动检测: 顶层有 "models" key = 嵌套, 否则 = 平铺
        """
        if not self._meta_file.exists():
            LOG.info("ModelMetadataStore: no static file, starting empty")
            return
        try:
            data = json.loads(self._meta_file.read_text(encoding="utf-8"))
            # 检测 schema 类型
            if isinstance(data.get("models"), dict):
                # 嵌套: {"_version": ..., "models": {model_id: ...}}
                self._static = data["models"]
                LOG.info("ModelMetadataStore: loaded %d static entries (nested schema)", len(self._static))
            else:
                # 平铺: {model_id: ...} 或 {_version, model_id: ...}
                # 过滤 _version / _comment 元数据 key
                self._static = {
                    k: v for k, v in data.items()
                    if not k.startswith("_") and isinstance(v, dict)
                }
                LOG.info("ModelMetadataStore: loaded %d static entries (flat schema)", len(self._static))
        except Exception as e:
            LOG.warning("ModelMetadataStore: load failed (%s), starting empty", e)

    def get(self, path: str) -> Dict[str, Any]:
        """v3.10.0: 取单个 model 的 metadata (静态 > 动态 > 自动推断)

        返回: {quality_score, speed_score, reasoning_score, tags, source}
        """
        with self._lock:
            static = self._static.get(path, {})
            dynamic = self._dynamic.get(path, {})
            return {
                "quality_score": static.get("quality_score", dynamic.get("quality_score", 0.0)),
                "speed_score": static.get("speed_score", dynamic.get("speed_score", 0.0)),
                "reasoning_score": static.get("reasoning_score", 0.0),
                "tags": list(set(static.get("tags", []) + dynamic.get("tags", []))),
                "source": "static" if static else ("dynamic" if dynamic else "none"),
            }

    def get_bulk(self, paths: List[str]) -> Dict[str, Dict[str, Any]]:
        """v3.10.0: 批量取 (UI 列表渲染优化)"""
        return {p: self.get(p) for p in paths}

    def update_dynamic(self, path: str, quality: Optional[float] = None,
                       speed: Optional[float] = None, tags: Optional[List[str]] = None):
        """v3.10.0: 动态更新 (来自 engine_stats EWMA)

        不立即写盘, 由 _flush_debounced 异步刷
        """
        with self._lock:
            entry = self._dynamic.setdefault(path, {
                "quality_score": 0.0,
                "speed_score": 0.0,
                "tags": [],
                "last_updated": 0.0,
            })
            if quality is not None:
                # EWMA 平滑: alpha=0.3
                entry["quality_score"] = 0.3 * quality + 0.7 * entry["quality_score"]
            if speed is not None:
                entry["speed_score"] = 0.3 * speed + 0.7 * entry["speed_score"]
            if tags:
                for t in tags:
                    if t not in entry["tags"]:
                        entry["tags"].append(t)
            entry["last_updated"] = time.time()

    def load_engine_stats(self, engine_stats_file: str = "engine_stats.json"):
        """v3.10.0: 从 engine_stats.json 算 EWMA (启动时调一次)"""
        stats_path = self._state_dir / engine_stats_file if not Path(engine_stats_file).is_absolute() else Path(engine_stats_file)
        if not stats_path.exists():
            return
        try:
            data = json.loads(stats_path.read_text(encoding="utf-8"))
            # engine_stats 格式: {"providers": {"openrouter/openai/gpt-4o": {"success_rate": 0.95, "avg_latency_ms": 1200}, ...}}
            providers = data.get("providers", {})
            count = 0
            for path, stats in providers.items():
                sr = stats.get("success_rate", 0.0)
                lat = stats.get("avg_latency_ms", 0.0)
                if sr <= 0:
                    continue
                # quality_score: success_rate × 100 (0-100)
                quality = min(100.0, sr * 100.0)
                # speed_score: latency 越低越高. 100 = 1s, 50 = 3s, 0 = 10s+
                if lat > 0:
                    speed = max(0.0, 100.0 - (lat - 1000) / 100)
                else:
                    speed = 50.0
                self.update_dynamic(path, quality=quality, speed=speed)
                count += 1
            LOG.info("ModelMetadataStore: loaded %d dynamic entries from engine_stats", count)
        except Exception as e:
            LOG.warning("ModelMetadataStore: load_engine_stats failed (%s)", e)

    @staticmethod
    def auto_tags(context_window: int = 0, modality: str = "text") -> List[str]:
        """v3.10.0: 自动推断 tags (从 context_window + modality)

        规则:
        - context_window >= 128000 → "long-context"
        - context_window >= 32000 → "medium-context" (8K-32K 不加)
        - modality contains "image" + "gen" → "image-generation"
        - modality contains "image" → "vision"
        - modality = "multimodal" → "multimodal"
        - modality = "video" → "video"
        - modality = "audio" → "audio"
        """
        tags = []
        if context_window >= 128000:
            tags.append("long-context")
        elif context_window >= 32000:
            tags.append("medium-context")
        mod = (modality or "").lower()
        if "image" in mod and "gen" in mod:
            tags.append("image-generation")
        if "image" in mod or "vision" in mod:
            tags.append("vision")
        if mod == "multimodal" or "multimodal" in mod:
            tags.append("multimodal")
        if "video" in mod:
            tags.append("video")
        if "audio" in mod:
            tags.append("audio")
        return tags

    def merge_into_model(self, model_info, path: str) -> None:
        """v3.10.0: 把 metadata merge 进 ModelInfo 实例

        流程: 静态 metadata.get(path) → 自动推断 tags → 写入 model_info
        """
        meta = self.get(path)
        # 自动推断 tags (跟静态 tags 合并)
        auto = self.auto_tags(getattr(model_info, "context_window", 0),
                              getattr(model_info, "modality", "text"))
        merged_tags = list(set(meta["tags"] + auto))
        model_info.quality_score = meta["quality_score"]
        model_info.speed_score = meta["speed_score"]
        model_info.reasoning_score = meta["reasoning_score"]
        model_info.tags = merged_tags
        model_info.metadata_source = meta["source"] if meta["source"] != "none" else "auto"

    def merge_bulk(self, model_infos: list, path_func=None) -> int:
        """v3.10.0: 批量 merge (registry refresh 后调)

        path_func: callable(model_info) -> str (默认 provider/id)
        返回: 实际 merge 的 model 数
        """
        def default_path_func(m):
            return f"{m.provider}/{m.id}"
        resolver = path_func or default_path_func
        count = 0
        for m in model_infos:
            try:
                self.merge_into_model(m, resolver(m))
                count += 1
            except Exception as e:
                LOG.warning("ModelMetadataStore.merge_bulk skip %s: %s", m, e)
        return count

    def save_static(self):
        """v3.10.0: 写 metadata.json (R40 改前 backup)"""
        with self._lock:
            self._meta_file.parent.mkdir(parents=True, exist_ok=True)
            # R40: 写前 backup
            if self._meta_file.exists():
                self._backup_dir.mkdir(parents=True, exist_ok=True)
                backup = self._backup_dir / f"model_metadata-{int(time.time())}.json"
                backup.write_bytes(self._meta_file.read_bytes())
                # 清理老 backup
                backups = sorted(self._backup_dir.glob("model_metadata-*.json"))
                while len(backups) > BACKUP_RETAIN:
                    backups.pop(0).unlink()
            data = {"models": self._static, "updated_at": time.time()}
            tmp = self._meta_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._meta_file)
            LOG.info("ModelMetadataStore: saved %d static entries", len(self._static))

    def upsert_static(self, path: str, quality_score: Optional[float] = None,
                      speed_score: Optional[float] = None,
                      reasoning_score: Optional[float] = None,
                      tags: Optional[List[str]] = None):
        """v3.10.0: 增/改单条静态 metadata (admin API 调用)"""
        with self._lock:
            entry = self._static.setdefault(path, {
                "quality_score": 0.0,
                "speed_score": 0.0,
                "reasoning_score": 0.0,
                "tags": [],
            })
            if quality_score is not None:
                entry["quality_score"] = float(quality_score)
            if speed_score is not None:
                entry["speed_score"] = float(speed_score)
            if reasoning_score is not None:
                entry["reasoning_score"] = float(reasoning_score)
            if tags is not None:
                # 合并去重
                existing = set(entry.get("tags", []))
                existing.update(tags)
                entry["tags"] = sorted(existing)


# 全局单例 (跟 public_key_manager 平行)
_metadata_store: Optional[ModelMetadataStore] = None


def init_model_metadata_store(state_dir: str = ".") -> ModelMetadataStore:
    global _metadata_store
    # v3.10.1 修 BUG-005 第二层: state_dir 存在但 model_metadata.json 不在时
    # 也 fallback 到 ./state (Docker /app/state 可能是 sibling 沙盒遗留空目录)
    from pathlib import Path
    candidate = Path(state_dir)
    meta_file = candidate / "model_metadata.json"
    if not candidate.exists() or not meta_file.exists():
        local_fallback = Path("./state")
        if local_fallback.exists() and (local_fallback / "model_metadata.json").exists():
            LOG.info("ModelMetadataStore: state_dir=%s 缺 model_metadata.json, fallback 到 %s",
                     state_dir, local_fallback)
            state_dir = str(local_fallback)
    _metadata_store = ModelMetadataStore(state_dir=state_dir)
    _metadata_store.load_engine_stats()  # 启动时回填 EWMA
    LOG.info("ModelMetadataStore initialized: state_dir=%s, _static=%d",
             state_dir, len(_metadata_store._static))
    return _metadata_store


def get_model_metadata_store() -> Optional[ModelMetadataStore]:
    return _metadata_store
