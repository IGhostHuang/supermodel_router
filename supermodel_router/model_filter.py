"""
supermodel_router/model_filter.py — 多维度模型筛选引擎 (v3.10.0 Phase J)

设计:
- ModelFilter dataclass: 声明式筛选条件
- apply_filter(filter, models) -> List[ModelInfo]
- 支持 9 维筛选: provider / context range / quality min / speed min / reasoning min / size min/max / capability min / modality / tags (any/all)

R41 O(N) 筛选 (N = models 总数, 通常 < 500, 1ms 内完成)
R42 向后兼容: 没设的维度 = 不筛
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("model_filter")


@dataclass
class ModelFilter:
    """v3.10.0: 声明式模型筛选条件

    所有字段都是可选, 没设 = 不筛.
    """
    providers: Optional[List[str]] = None       # ["openrouter", "newapi"], None = 全部
    context_min: Optional[int] = None           # 最小 context_window (0 = 不限)
    context_max: Optional[int] = None           # 最大 context_window (None = 不限)
    quality_min: Optional[float] = None         # 最小 quality_score (0-100)
    speed_min: Optional[float] = None           # 最小 speed_score (0-100)
    reasoning_min: Optional[float] = None       # 最小 reasoning_score (0-100)
    size_min: Optional[float] = None            # 最小 size_b (十亿参数, 0 = 不限)
    size_max: Optional[float] = None            # 最大 size_b (None = 不限)
    capability_min: Optional[float] = None      # 最小 capability_score (0-100)
    modality: Optional[str] = None              # text / multimodal / image-gen / vision / 等
    tags_any: Optional[List[str]] = None        # OR 关系: 含任一 tag
    tags_all: Optional[List[str]] = None        # AND 关系: 必须含全部 tag
    exclude_tags: Optional[List[str]] = None    # 排除含任一 tag
    min_models: int = 0                         # 至少返回 N 个 (默认 0)

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None and v != 0 and v != []}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModelFilter":
        # 过滤未知字段
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        cleaned = {k: v for k, v in data.items() if k in valid}
        return cls(**cleaned)


def apply_filter(filter_: ModelFilter, models: List[Any]) -> List[Any]:
    """v3.10.0: 应用筛选条件到 model 列表

    规则:
    - providers: model.provider 必须在列表
    - context_min/max: model.context_window 在范围内 (0 = 未知, 默认通过)
    - quality/speed/reasoning min: model.xxx_score >= min (0 = 未知, 默认通过, 但 min > 0 时未知也失败)
    - size_min/max: model.size_b 在范围内 (0 = 未知, 默认通过, 但 min > 0 时未知也失败)
    - capability_min: model.capability_score >= min (0 = 未知, 同上)
    - modality: model.modality 包含 modality (子串匹配, 支持 "image" 匹配 "image-gen")
    - tags_any: model.tags 跟 tags_any 有交集
    - tags_all: model.tags 包含 tags_all 所有元素
    - exclude_tags: model.tags 跟 exclude_tags 无交集

    返回: 匹配的 model 列表 (保持原顺序)
    """
    if not filter_:
        return list(models)

    out = []
    for m in models:
        # 1. providers
        if filter_.providers is not None:
            if getattr(m, "provider", "") not in filter_.providers:
                continue
        # 2. context_min/max
        ctx = getattr(m, "context_window", 0) or 0
        if filter_.context_min is not None and ctx > 0 and ctx < filter_.context_min:
            continue
        if filter_.context_max is not None and ctx > 0 and ctx > filter_.context_max:
            continue
        # 3. quality_min (0 = 未知时, 默认通过; 但显式 >0 时未知也失败)
        if filter_.quality_min is not None and filter_.quality_min > 0:
            q = getattr(m, "quality_score", 0) or 0
            if q > 0 and q < filter_.quality_min:
                continue
        # 4. speed_min
        if filter_.speed_min is not None and filter_.speed_min > 0:
            s = getattr(m, "speed_score", 0) or 0
            if s > 0 and s < filter_.speed_min:
                continue
        # 5. reasoning_min
        if filter_.reasoning_min is not None and filter_.reasoning_min > 0:
            r = getattr(m, "reasoning_score", 0) or 0
            if r > 0 and r < filter_.reasoning_min:
                continue
        # 6. size_min/max
        sz = getattr(m, "size_b", 0) or 0
        if filter_.size_min is not None and filter_.size_min > 0:
            if sz == 0 or sz < filter_.size_min:
                continue
        if filter_.size_max is not None and filter_.size_max > 0:
            if sz == 0 or sz > filter_.size_max:
                continue
        # 7. capability_min
        cap = getattr(m, "capability_score", 0) or 0
        if filter_.capability_min is not None and filter_.capability_min > 0:
            if cap == 0 or cap < filter_.capability_min:
                continue
        # 8. modality (子串匹配)
        if filter_.modality:
            mod = (getattr(m, "modality", "") or "").lower()
            if filter_.modality.lower() not in mod:
                continue
        # 9. tags_any (OR)
        if filter_.tags_any:
            model_tags = set(getattr(m, "tags", []) or [])
            if not model_tags.intersection(filter_.tags_any):
                continue
        # 10. tags_all (AND)
        if filter_.tags_all:
            model_tags = set(getattr(m, "tags", []) or [])
            if not model_tags.issuperset(filter_.tags_all):
                continue
        # 11. exclude_tags
        if filter_.exclude_tags:
            model_tags = set(getattr(m, "tags", []) or [])
            if model_tags.intersection(filter_.exclude_tags):
                continue
        out.append(m)
    return out


def model_to_dict(m: Any, with_metadata: bool = True) -> Dict[str, Any]:
    """v3.10.0: ModelInfo → JSON dict (UI 列表渲染用)"""
    d = {
        "id": getattr(m, "id", ""),
        "provider": getattr(m, "provider", ""),
        "path": f"{getattr(m, 'provider', '')}/{getattr(m, 'id', '')}",
        "modality": getattr(m, "modality", ""),
        "modality_display": getattr(m, "modality_display", ""),
        "context_window": getattr(m, "context_window", 0),
        "capability_score": getattr(m, "capability_score", 0),
    }
    if with_metadata:
        d.update({
            "quality_score": getattr(m, "quality_score", 0),
            "speed_score": getattr(m, "speed_score", 0),
            "reasoning_score": getattr(m, "reasoning_score", 0),
            "size_b": getattr(m, "size_b", 0),
            "tags": getattr(m, "tags", []),
            "metadata_source": getattr(m, "metadata_source", "none"),
        })
    return d