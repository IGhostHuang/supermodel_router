# SMR (Super Model Router) 项目

多模型智能路由 + Fusion N+1 融合 + 免费模型自动发现 + 配置安全网 + Plan Mode + 智能自动组团 + 质量门控

> **当前版本**：v0.4.0  
> **最后更新**：2026-08-04  
> **状态**：核心功能完成 + v3性能优化 + v4智能组团与质量门控 + Phase 1/2 路线图模块实现

---

## 📁 项目结构

```
supermodel_router/              核心代码包
├── __init__.py                包入口 + 公共 API 导出 [v0.4 更新]
├── fusion_router.py           四算子融合路由引擎 [v4: run_auto + 质量门控集成]
├── fusion_composer.py         智能自动组团：分析→选算子→选模型→建计划 [v4 新增]
├── quality_gate.py            输出质量门控：静态+语义检查+基线兜底 [v4 新增]
├── free_auto_discovery.py     L1/L2/L3 免费模型发现调度器 [Pi-Lens 优化版]
├── git_checkpoint.py          配置快照 + 回滚管理器 [Phase 1 新增]
├── plan_mode.py               规划-确认-执行三阶段模式 [Phase 1 新增]
└── context_compressor.py      Token 分层压缩器 [Phase 2 新增]

smr-plugin-roadmap/             文档：Pi 插件化路线图
└── smr-plugin-roadmap.html    15 插件评估 + 3 阶段实施计划

smr-handover/                   文档：项目交接文档 [本项目入口]
└── smr-handover.html           完整交接文档（架构/时间线/模块/下一步）

supermodel_router.checkpoint_*/  代码快照（修改前备份，可回滚）
```

---

## 🚀 快速开始

### 1. 先读交接文档
**新会话接手第一件事：打开交接文档**

👉 [smr-handover/smr-handover.html](smr-handover/smr-handover.html)

里面包含：
- 项目概览与架构总览
- 开发时间线（做了什么、为什么做）
- 核心模块详解（关键类、入口函数）
- 文件清单与检查点
- 下一步方向（P0/P1/P2 优先级）
- 新会话快速上手指南

### 2. 验证环境

```bash
# 验证包导入
python -c "from supermodel_router import FusionRouter, FreeAutoDiscovery; print('OK')"
python -c "from supermodel_router import available_modules; print(available_modules())"

# v0.4 新增模块验证
python -c "from supermodel_router import AutoFusionComposer, QualityGate; print('v4 modules OK')"

# v0.3 模块验证
python -c "from supermodel_router import CheckpointManager, RoutingPlanner, ContextCompressor; print('Phase 1/2 modules OK')"
```

### 3. 读路线图（了解规划方向）

👉 [smr-plugin-roadmap/smr-plugin-roadmap.html](smr-plugin-roadmap/smr-plugin-roadmap.html)

---

## 🧩 核心模块

| 模块 | 职责 | 状态 | 路线图阶段 |
|------|------|------|-----------|
| `FusionRouter` | 四算子融合：Expert / Vote / Pipeline / Refine | ✅ 完成 + v3优化 + v4集成 | 核心 |
| `AutoFusionComposer` | 智能自动组团：分析prompt→选算子→选模型→建计划 | ✅ 完成 | v4 新增 |
| `QualityGate` | 输出质量门控：8项检查 + 基线模型兜底 | ✅ 完成 | v4 新增 |
| `FreeAutoDiscovery` | L1/L2/L3 免费模型自动发现调度器 | ✅ 框架完成 + 优化 | 核心 |
| `CheckpointManager` | 配置文件快照 + 原子回滚 + diff 对比 | ✅ 完成 | Phase 1 |
| `RoutingPlanner` | 规划-确认-执行三阶段 + 自动 checkpoint | ✅ 完成 | Phase 1 |
| `ContextCompressor` | 三层 Token 压缩：近期完整 + 中期摘要 + 远期关键词 | ✅ 完成 | Phase 2 |
| Engine 路由引擎 | 多供应商路由 + fallback 链 + 健康度过滤 | ⚠️ 待优化 | 核心 |
| Admin API / UI | 管理接口与监控界面 | ⚠️ 待优化 | 核心 |

---

## 🔧 工作方式

- **修改前先建 checkpoint**：复制 `supermodel_router/` 目录加时间戳后缀
- **回滚方法**：用 checkpoint 目录直接覆盖
- **质量标准**：docstring 覆盖率 ≥ 70%，返回类型注解 ≥ 90%
- **v0.3 新增**：使用 `CheckpointManager` 自动快照配置变更，`RoutingPlanner` 先规划后执行

---

## 📋 v0.4 新增功能

### v3: Fusion N+1 性能与稳定性优化

**fusion_router.py** 核心优化：
- 结果缓存：相同 (model, prompt_hash) 返回缓存响应（TTL 5min），零积分消耗
- 路由缓存：resolved routes 缓存 per model_path，避免重复 pick_chain
- 智能重试：错误分类 — rate-limit → 长退避，4xx → 不重试，timeout → 快速重试
- Vote 提前终止：first_ok/concat/majority 策略收集到 min_candidates 即返回
- Judge 容错：judge 模型失败时返回最佳候选，不中断整个 vote
- 模型去重：vote 中重复 model_ids 自动去重
- Pipeline history 裁剪：后续步骤使用裁剪后的历史，节省 token
- 自适应默认值：max_tokens=2048，timeout=90s（对齐 fusion 90s 约束）

### v4: 智能自动组团

**fusion_composer.py** — 无需预定义计划，动态自动组团：
- **Prompt 分析**（零 API 成本）：意图分类（code/math/creative/reasoning/factual/chat）、复杂度估算（simple/medium/complex）、领域检测、语言检测（zh/en/mixed）、Token 估算
- **算子选择**（规则驱动）：simple+factual → expert（最省）；medium+creative → vote（多元）；complex+reasoning → pipeline（逐步分解）
- **模型选择**（健康感知 + 能力匹配）：
  - 优先从 engine registry 获取可用模型，按健康度过滤（跳过 SKIP/BANNED/EXPIRED）
  - 按 prompt 意图评分模型（code → coder 模型，zh → CJK 友好模型，free → 优先用于 fan-out）
  - Vote 组确保 provider 多样性（避免同 provider groupthink）
  - 引擎不可用时回退到可配置模型池
- **动态 sizing**：复杂度越高，vote 模型数越多（2-4），pipeline 步骤越多
- **组合缓存**：相同 prompt 复用已组合的计划

### v4: 输出质量门控

**quality_gate.py** — 确保输出不低于基线模型（deepseek v4-flash）水平：
- **Layer 1 静态检查**（零 API 成本）：
  - 空值/过短检测
  - 截断检测（句末无适当闭合）
  - 重复检测（行重复 + 5-gram 重复）
  - 错误标记检测（[fusion_error] 等）
  - 乱码检测（非打印字符比例 + 特殊字符比例）
- **Layer 2 语义检查**（零 API 成本）：
  - 关键词相关性（CJK 字符级 + Latin 词级匹配）
  - 回答充分性（长度 vs prompt 复杂度）
  - 结构完整性（要求代码时检查代码块，要求列表时检查列表结构）
- **Layer 3 基线兜底**（1 次 API 调用，仅 Layer 1+2 失败时）：
  - 调用基线模型（deepseek v4-flash 等效）
  - 对比质量分数，返回更好的回答
- **关键失败一票否决**：空值/错误标记/缺失代码块 → score=0，直接触发兜底
- **质量统计**：累计评估次数、通过率、兜底率、平均分

### 集成入口

**FusionRouter.run_auto()** — 一键调用：
1. 自动分析 prompt → 组合 fusion 计划
2. 执行计划
3. 质量门控验证 → 不达标自动兜底
4. 返回质量保证的结果

```python
from supermodel_router import init_fusion_router, get_fusion_router

# 初始化（自动创建 composer + quality_gate）
await init_fusion_router()

router = get_fusion_router()
result = await router.run_auto("用Python实现快速排序", history=[])

print(result.answer)        # 质量保证的回答
print(result.success)       # 是否成功
print(result.cache_hits)    # 缓存命中数
# result.trace 包含 auto_compose + 执行 + quality_gate 完整链路
```

---

## 📋 v0.3 功能

### Phase 1: 配置安全网 + Plan Mode

**git_checkpoint.py** — 无 Git 依赖的文件系统快照：
- 原子创建：先写 `.tmp` 再 rename，半成品不可见
- 自动清理：超过 `max_checkpoints` 数量自动淘汰最旧
- diff 对比：支持 unchanged/modified/deleted/added 四种状态
- 异步安全：`asyncio.Lock` 保护并发操作

**plan_mode.py** — 规划-确认-执行三阶段模式：
- 支持 4 种变更类型：fusion_strategy / health_threshold / model_pool / custom
- 风险评估：LOW / MEDIUM / HIGH 三级
- 自动 checkpoint：执行前自动创建快照
- 双重回滚：checkpoint 优先 + 步骤级回滚兜底
- TTL 过期：10 分钟未确认自动过期

### Phase 2: Token 压缩

**context_compressor.py** — 三层分层压缩：
- Tier 1（近期）：最近 5 轮完整保留
- Tier 2（中期）：LLM 摘要，失败回退关键词
- Tier 3（远期）：纯关键词提取，零 LLM 成本
- 可插拔：默认关闭，`ENABLE_CONTEXT_COMPRESSOR=1` 启用

---

## 📋 下一步（P0 优先）

1. **部署验证**：Fusion N+1 端到端测试 — 使用 `run_auto()` 在 TRAE 中验证智能组团 + 质量门控
2. **模型池调优**：根据实际可用模型调整 `fusion_composer` 的 `_DEFAULT_MODEL_POOL`
3. **质量阈值调优**：根据实际使用反馈调整 `quality_gate` 的 `min_score`（当前 0.55）
4. **集成测试**：将 v4 模块接入 Docker 容器，验证端到端流程
5. **Admin API 扩展**：为 run_auto / quality_stats / composer_stats 暴露管理接口

---

*本文档用于新会话快速接手。详细内容请阅读交接文档。*
