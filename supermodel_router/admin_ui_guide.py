"""
admin_ui_guide.py — SMR Admin UI 使用指引页 (v3.28 增量)

来源: 老大 2026-07-04 钦定"让伊芙整体 review 一次 SMR，写一个使用指引页，集成进去吧"
设计: 5 段独立静态页 (基于 v3.27.0 真实状态, 不写 v3.27 stub 按钮)
路由: GET /admin/guide  (跟 /admin/9-gong 同级独立)
Topnav: 📖 指引 链接, 跟 ↻ ⚡ 📋 ⚙ 🌙 并排
"""

from fastapi.responses import HTMLResponse

GUIDE_HTML = """<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SMR Admin 使用指引 · __SMR_VERSION__</title>
<style>
:root {
  --bg-1: #0f1419; --bg-2: #1a1f26; --bg-3: #232932;
  --text-1: #e6e6e6; --text-2: #9aa3ad; --text-3: #6b7480;
  --border: #2a3038; --primary: #4f9eff; --success: #2ecc71;
  --warn: #f39c12; --danger: #e74c3c; --purple: #9b59b6;
  --accent: #4f9eff;
  --space-1: 4px; --space-2: 8px; --space-3: 12px;
  --space-4: 16px; --space-5: 24px; --space-6: 32px;
  --radius-sm: 6px; --radius-md: 10px; --radius-lg: 14px;
  --shadow-sm: 0 1px 3px rgba(0,0,0,.3);
  --shadow-md: 0 4px 12px rgba(0,0,0,.25);
  --mono: ui-monospace, "JetBrains Mono", "Fira Code", Consolas, monospace;
}
[data-theme="light"] {
  --bg-1: #ffffff; --bg-2: #f5f7fa; --bg-3: #e9edf2;
  --text-1: #1a1f26; --text-2: #4a5260; --text-3: #6b7480;
  --border: #d9dde2; --shadow-sm: 0 1px 3px rgba(0,0,0,.08);
  --shadow-md: 0 4px 12px rgba(0,0,0,.06);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
       "Microsoft YaHei", sans-serif; background: var(--bg-1); color: var(--text-1);
       line-height: 1.65; padding: var(--space-5); }
a { color: var(--primary); text-decoration: none; }
a:hover { text-decoration: underline; }
code { font-family: var(--mono); background: var(--bg-2); padding: 2px 6px;
       border-radius: 4px; font-size: 0.9em; }
pre { background: var(--bg-2); padding: var(--space-4); border-radius: var(--radius-md);
      overflow-x: auto; border: 1px solid var(--border); margin: var(--space-3) 0; }
pre code { background: none; padding: 0; }
.container { max-width: 1100px; margin: 0 auto; }

/* ===== Top Nav (跟主 dashboard 一致) ===== */
.topnav { display: flex; align-items: center; gap: var(--space-4);
          padding: var(--space-3) var(--space-5); background: var(--bg-2);
          border: 1px solid var(--border); border-radius: var(--radius-lg);
          box-shadow: var(--shadow-sm); margin-bottom: var(--space-5); }
.brand { display: flex; align-items: center; gap: var(--space-2);
         font-weight: 700; font-size: 16px; }
.brand-logo { font-size: 20px; }
.brand-version { font-family: var(--mono); font-size: 11px; color: var(--text-3);
                 background: var(--bg-3); padding: 2px 6px; border-radius: 4px; }
.topnav-actions { display: flex; gap: var(--space-2); margin-left: auto; }
.btn-icon { background: var(--bg-3); border: 1px solid var(--border);
            color: var(--text-1); padding: 6px 12px; border-radius: var(--radius-sm);
            cursor: pointer; font-size: 14px; transition: all .15s; }
.btn-icon:hover { background: var(--primary); color: white; border-color: var(--primary); }

/* ===== Hero ===== */
.hero { background: linear-gradient(135deg, var(--bg-2), var(--bg-3));
        padding: var(--space-6); border-radius: var(--radius-lg);
        border: 1px solid var(--border); margin-bottom: var(--space-5); }
.hero h1 { font-size: 32px; margin-bottom: var(--space-3);
           background: linear-gradient(135deg, var(--primary), var(--purple));
           -webkit-background-clip: text; background-clip: text; color: transparent; }
.hero p { color: var(--text-2); font-size: 16px; max-width: 800px; }
.hero .meta { display: flex; gap: var(--space-3); margin-top: var(--space-4);
              flex-wrap: wrap; font-size: 13px; color: var(--text-3); }
.hero .meta span { background: var(--bg-1); padding: 4px 10px;
                   border-radius: 20px; border: 1px solid var(--border); }

/* ===== TOC ===== */
.toc { background: var(--bg-2); border: 1px solid var(--border);
       border-radius: var(--radius-lg); padding: var(--space-4);
       margin-bottom: var(--space-5); position: sticky; top: var(--space-3); z-index: 10; }
.toc-title { font-weight: 700; margin-bottom: var(--space-3);
             font-size: 13px; color: var(--text-3); text-transform: uppercase;
             letter-spacing: 0.05em; }
.toc ol { list-style: none; padding: 0; columns: 2; column-gap: var(--space-5); }
.toc li { padding: 4px 0; break-inside: avoid; }
.toc a { color: var(--text-2); font-size: 14px; }
.toc a:hover { color: var(--primary); }

/* ===== Sections ===== */
.section { background: var(--bg-2); border: 1px solid var(--border);
           border-radius: var(--radius-lg); padding: var(--space-5);
           margin-bottom: var(--space-4); }
.section h2 { font-size: 22px; margin-bottom: var(--space-3);
              padding-bottom: var(--space-3); border-bottom: 1px solid var(--border); }
.section h3 { font-size: 17px; margin: var(--space-4) 0 var(--space-2); color: var(--accent); }
.section p { margin-bottom: var(--space-3); color: var(--text-2); }
.section ul, .section ol { margin-left: var(--space-5); margin-bottom: var(--space-3); }
.section li { margin-bottom: var(--space-2); color: var(--text-2); }
.section li strong { color: var(--text-1); }

/* ===== Callout ===== */
.callout { padding: var(--space-3) var(--space-4); border-radius: var(--radius-md);
           margin: var(--space-3) 0; border-left: 4px solid var(--primary);
           background: var(--bg-3); }
.callout.warn { border-left-color: var(--warn); }
.callout.danger { border-left-color: var(--danger); }
.callout.success { border-left-color: var(--success); }
.callout .label { font-weight: 700; color: var(--text-1);
                  margin-bottom: var(--space-2); display: block; }

/* ===== Table ===== */
.feature-table { width: 100%; border-collapse: collapse; margin: var(--space-3) 0;
                 font-size: 14px; }
.feature-table th, .feature-table td { padding: 10px 12px;
                 border: 1px solid var(--border); text-align: left; }
.feature-table th { background: var(--bg-3); font-weight: 700; color: var(--text-1); }
.feature-table td { color: var(--text-2); }
.feature-table .ok { color: var(--success); font-weight: 600; }
.feature-table .stub { color: var(--warn); font-weight: 600; }
.feature-table .no { color: var(--danger); font-weight: 600; }

/* ===== Steps ===== */
.steps { counter-reset: step; }
.steps li { counter-increment: step; padding-left: var(--space-6); position: relative; }
.steps li::before { content: counter(step); position: absolute; left: 0; top: 2px;
                    width: 24px; height: 24px; background: var(--primary);
                    color: white; border-radius: 50%; font-size: 12px;
                    font-weight: 700; display: flex; align-items: center; justify-content: center; }

/* ===== Footer ===== */
.footer { text-align: center; color: var(--text-3); font-size: 13px;
          padding: var(--space-5); margin-top: var(--space-5); }
.footer a { color: var(--text-3); }

/* ===== Responsive ===== */
@media (max-width: 768px) {
  .toc ol { columns: 1; }
  body { padding: var(--space-3); }
  .hero { padding: var(--space-4); }
  .hero h1 { font-size: 24px; }
}
</style>
</head>
<body>
<div class="container">

<!-- ===== Top Nav (一致) ===== -->
<nav class="topnav">
  <div class="brand">
    <div class="brand-logo">⚡</div>
    <span>SuperModel Router</span>
    <span class="brand-version" id="brandVersion">__SMR_VERSION__</span>
  </div>
  <div class="topnav-actions">
    <a class="btn-icon" href="/admin" title="返回 Dashboard">↩ Dashboard</a>
    <a class="btn-icon" href="/admin/9-gong" title="九宫 Dashboard">🐾 九宫</a>
    <a class="btn-icon" href="/admin/api/v1/health" title="健康检查 API">💚 Health</a>
  </div>
</nav>

<!-- ===== Hero ===== -->
<div class="hero">
  <h1>📖 SMR Admin 使用指引</h1>
  <p>本页是 SuperModel Router Admin UI 的官方使用手册。基于 __SMR_VERSION__ 真实状态编写, 标注每个功能
    <strong style="color:var(--success)">✅ 可用</strong> /
    <strong style="color:var(--warn)">🟡 部分可用</strong> /
    <strong style="color:var(--danger)">❌ 待集成</strong>。
    适用于通过浏览器管理 LLM Provider、模型分组、配额和路由策略。</p>
  <div class="meta">
    <span>📌 __SMR_VERSION__</span>
    <span>🕐 更新于 2026-07-04</span>
    <span>🎯 面向: Admin / DevOps</span>
    <span>📂 <a href="/admin/api/v1/admin/version">API 版本</a></span>
  </div>
</div>

<!-- ===== TOC ===== -->
<div class="toc">
  <div class="toc-title">📋 目录</div>
  <ol>
    <li><a href="#intro">1. SMR 是什么</a></li>
    <li><a href="#quickstart">2. 快速开始 (3 步)</a></li>
    <li><a href="#dashboard">3. Dashboard 页面导览</a></li>
    <li><a href="#wizard">4. 模型分组 Wizard (核心)</a></li>
    <li><a href="#common">5. 常用操作</a></li>
    <li><a href="#advanced">6. 高级操作 (curl)</a></li>
    <li><a href="#faq">7. 常见问题 (FAQ)</a></li>
    <li><a href="#trouble">8. 故障速查</a></li>
    <li><a href="#shortcuts">9. 快捷键 & 主题</a></li>
    <li><a href="#version">10. 版本历史</a></li>
  </ol>
</div>

<!-- ===== Section 1 ===== -->
<div class="section" id="intro">
  <h2>1. SMR 是什么</h2>
  <p><strong>SuperModel Router (SMR)</strong> 是一个 LLM API 路由层, 把 OpenAI 兼容格式的请求
    按规则路由到最优 Provider/模型, 自带配额管理、健康检查、降级链、统计观测能力。</p>

  <h3>核心能力</h3>
  <ul>
    <li><strong>多 Provider 聚合</strong>: 统一接入 OpenAI / Anthropic / Gemini / Cohere / DeepSeek / 自定义端点</li>
    <li><strong>智能路由</strong>: 按价格 / 上下文 / 能力 / 健康度自动选最优模型</li>
    <li><strong>免费模型路由</strong>: 自动发现免费 tier 模型并优先使用 (节省成本)</li>
    <li><strong>健康探测</strong>: 主动 probe 模型可用性, 失败自动降级到 fallback</li>
    <li><strong>配额管理</strong>: 每日/分钟配额跟踪, 耗尽自动切 fallback</li>
    <li><strong>统计分析</strong>: 调用次数 / 成功率 / 延迟 / 成本 全链路可观测</li>
  </ul>

  <div class="callout">
    <span class="label">🎯 一句话定位</span>
    SMR = <strong>"LLM API 网关"</strong>, 帮你在多 Provider 间做最优路由, 屏蔽各家 API 差异, 给你一个统一接口。
  </div>
</div>

<!-- ===== Section 2 ===== -->
<div class="section" id="quickstart">
  <h2>2. 快速开始 (3 步)</h2>
  <p>假设你刚装好 SMR, 第一次访问 Admin UI。</p>
  <ol class="steps">
    <li><strong>打开 Admin UI</strong>: 浏览器访问 <code>http://localhost:8765/admin</code>
      (默认端口), 应看到深色 Dashboard + 4 个 KPI 卡片</li>
    <li><strong>添加第一个 Provider</strong>: 点 Models 段右上 <code>＋ Wizard</code> → 选一个
      preset (比如 "📚 长上下文") → 给分组起名 → 点 <code>生成分组</code>。系统会引导你填 API Key。</li>
    <li><strong>测一下路由</strong>: 在 Status Banner 点 <code>⚡ Probe</code> 或访问
      <code>http://localhost:8765/v1/health</code>, 看到所有模型绿色 = 通</li>
  </ol>
  <div class="callout success">
    <span class="label">✅ 完成!</span>
    你的 SMR 已经能路由 LLM 请求了。客户端代码无需改, 仍按 OpenAI 格式调用, base_url 指向 SMR 即可。
  </div>
</div>

<!-- ===== Section 3 ===== -->
<div class="section" id="dashboard">
  <h2>3. Dashboard 页面导览</h2>
  <p>Admin UI 主页 (/admin) 自上而下分 6 段:</p>

  <h3>3.1 Top Nav (顶部导航栏)</h3>
  <ul>
    <li><strong>品牌区</strong>: ⚡ SuperModel Router + 版本号</li>
    <li><strong>全局搜索 ⌘K</strong>: 输入即搜模型/Provider/路由规则</li>
    <li><strong>6 个操作按钮</strong>: ↻ 刷新 / ⚡ Probe / 📋 日志 / ⚙ 设置 / 🌙 主题 / (本页 📖)</li>
  </ul>

  <h3>3.2 Status Banner (状态条)</h3>
  <ul>
    <li>左侧状态点 (绿/黄/红) + 文字 ("✅ 12 个 Provider 正常" 等)</li>
    <li>右侧 4 个快捷按钮: 📊 导出 / 📦 备份 / ↻ 刷新 / ⚡ Probe</li>
  </ul>

  <h3>3.3 KPI 卡片 (4 张)</h3>
  <table class="feature-table">
    <tr><th>卡片</th><th>含义</th><th>颜色</th></tr>
    <tr><td>今日调用</td><td>累计调用次数 (含成功+失败)</td><td>蓝</td></tr>
    <tr><td>成功率</td><td>成功调用 / 总调用</td><td>绿</td></tr>
    <tr><td>平均延迟</td><td>所有模型响应时间均值 (ms)</td><td>黄</td></tr>
    <tr><td>免费路由</td><td>今日走免费模型的调用次数</td><td>紫</td></tr>
  </table>

  <h3>3.4 Providers 网格</h3>
  <p>每个 Provider 一张卡, 显示: 名称 / 模型数 / 调用数 / 平均延迟 / Quality Score / Sparkline。
    右上 3 按钮: 全部启用 / 全部刷新 / ＋ 新增 (走 Wizard)。</p>

  <h3>3.5 Activity Stream (活动流)</h3>
  <p>最近路由决策流: 时间戳 / 模型路径 / Provider / 延迟 / 成本。
    右上: 导出 CSV / 查看全部。</p>

  <h3>3.6 Models Table (模型表)</h3>
  <p>全量模型列表, 列: Model / Provider / Price / Size / Health / Score。
    筛选: Provider ▾ / 参数量 ▾ / 能力 ▾ / 价格 ▾ / ＋ Wizard。</p>
</div>

<!-- ===== Section 4 ===== -->
<div class="section" id="wizard">
  <h2>4. 模型分组 Wizard (核心功能)</h2>
  <p>Wizard 是 v3.27 集成进 Admin UI 的模型分组创建工具, 帮你在 1 分钟内建好一组模型。</p>

  <h3>4.1 13 个预设 (Preset)</h3>
  <table class="feature-table">
    <tr><th>Preset</th><th>适用场景</th><th>典型匹配</th></tr>
    <tr><td>📚 长上下文</td><td>长文档分析 / RAG</td><td>≥ 100K 上下文</td></tr>
    <tr><td>📖 32K</td><td>中等长文</td><td>32K-100K</td></tr>
    <tr><td>📕 200K</td><td>超长上下文</td><td>≥ 200K</td></tr>
    <tr><td>⚡ 快速</td><td>实时对话</td><td>低延迟优先</td></tr>
    <tr><td>⚖️ 速度质量</td><td>通用</td><td>延迟×质量平衡</td></tr>
    <tr><td>🎯 高质量</td><td>重要任务</td><td>Quality ≥ 8</td></tr>
    <tr><td>🏆 顶级</td><td>生产关键</td><td>Quality ≥ 9</td></tr>
    <tr><td>🎨 图像</td><td>文生图</td><td>image modality</td></tr>
    <tr><td>👁 视觉</td><td>多模态</td><td>vision input</td></tr>
    <tr><td>🌐 Any-to-Any</td><td>多模态生多模态</td><td>多模态输入输出</td></tr>
    <tr><td>🧠 强推理</td><td>复杂推理</td><td>reasoning model</td></tr>
    <tr><td>💻 代码</td><td>编程</td><td>code-tuned</td></tr>
    <tr><td>💰 性价比</td><td>省钱</td><td>Price × Quality 最优</td></tr>
  </table>

  <h3>4.2 自定义筛选</h3>
  <p>不满足 preset? 可自定筛选条件:</p>
  <ul>
    <li><strong>Provider 多选</strong>: 7 个真实 provider 可勾选</li>
    <li><strong>上下文窗口</strong>: 拖动滑块选区间</li>
    <li><strong>Quality Score</strong>: ≥ 几 (0-10)</li>
    <li><strong>Speed Score</strong>: ≥ 几 (0-10)</li>
    <li><strong>Modality</strong>: text / image / audio / video</li>
    <li><strong>Tags</strong>: 7 个标签多选 (reasoning / coding / multilingual / ...)</li>
  </ul>

  <h3>4.3 一键生成</h3>
  <p>选好 preset 或筛选条件后:</p>
  <ol>
    <li>点 <code>预览匹配</code> → 看哪些模型命中 (默认前 50)</li>
    <li>勾选要加入分组的模型 (可全选/全不选)</li>
    <li>填分组名 (必填) → 点 <code>生成分组</code></li>
    <li>生成成功会弹出 toast, 分组 ID + API Key (新建的话)</li>
  </ol>
</div>

<!-- ===== Section 5 ===== -->
<div class="section" id="common">
  <h2>5. 常用操作</h2>

  <h3>5.1 添加 Provider</h3>
  <p>暂未在 Admin UI 提供独立 "新增 Provider" 弹窗 (🟡 v3.27 stub, 但 Wizard 已能用)。
    <strong>临时方案</strong>: 通过 Wizard 创建分组时会引导填 API Key, 系统自动注册 Provider。</p>

  <h3>5.2 启用/停用 Provider</h3>
  <p>暂未提供 UI 入口 (🟡 v3.27 stub)。需用 curl:
  <pre><code>curl -X POST http://localhost:8765/admin/api/v1/admin/providers/&lt;name&gt;/disable
curl -X POST http://localhost:8765/admin/api/v1/admin/providers/&lt;name&gt;/enable</code></pre></p>

  <h3>5.3 健康探测</h3>
  <p>点 Status Banner <code>⚡ Probe</code> → 全量模型并发探测, 结果自动更新 Health 列。
    单模型探测: <code>POST /admin/api/v1/admin/model-health/probe/&lt;model_path&gt;</code></p>

  <h3>5.4 备份配置</h3>
  <p>点 Status Banner <code>📦 备份</code> (🟡 v3.27 stub)。
    后端: <code>GET /admin/api/v1/admin/config/backups</code> 列出, <code>POST /admin/api/v1/admin/config/restore</code> 恢复。</p>

  <h3>5.5 模型分组管理</h3>
  <p>Wizard 创建后, 分组会出现在 Models Table 顶部, 标 "📦 分组名"。
    路由时按分组内模型优先级 + 健康度自动选。</p>
</div>

<!-- ===== Section 6 ===== -->
<div class="section" id="advanced">
  <h2>6. 高级操作 (curl 直调)</h2>
  <p>以下功能 UI 暂未集成, 需用 curl:</p>

  <h3>6.1 配额管理</h3>
  <pre><code># 查看所有模型配额状态
curl http://localhost:8765/admin/api/v1/admin/quota/status | jq

# 手动重置某个模型配额
curl -X POST http://localhost:8765/admin/api/v1/admin/quota/recover/&lt;model_path&gt;

# 重置 free-models 配额
curl -X POST http://localhost:8765/admin/api/v1/admin/free-models/reset-quota</code></pre>

  <h3>6.2 模型管理</h3>
  <pre><code># 列出所有模型规则
curl http://localhost:8765/admin/api/v1/admin/model_rules | jq

# 列出模型别名
curl http://localhost:8765/admin/api/v1/admin/model-aliases | jq

# 触发模型发现 (从所有 provider 拉新模型)
curl -X POST http://localhost:8765/admin/api/v1/admin/model_discovery/trigger</code></pre>

  <h3>6.3 服务端配置</h3>
  <pre><code># 查看当前 routing 策略
curl http://localhost:8765/admin/api/v1/admin/routing | jq

# 修改 routing 策略
curl -X PUT http://localhost:8765/admin/api/v1/admin/routing \\
  -H "Content-Type: application/json" -d '{...}'

# 重新加载 config.yaml
curl -X POST http://localhost:8765/admin/api/v1/admin/config/reload</code></pre>

  <h3>6.4 升级</h3>
  <pre><code># 检查版本
curl http://localhost:8765/admin/api/v1/admin/version | jq

# 触发升级 (git mode)
curl -X POST http://localhost:8765/admin/api/v1/admin/upgrade -d '{"method":"git"}'</code></pre>

  <div class="callout warn">
    <span class="label">⚠️ 注意</span>
    curl 改 routing/penalty 等策略前, 务必备份 config.yaml。SMR 不会自动 rollback 错误改动。
  </div>
</div>

<!-- ===== Section 7 ===== -->
<div class="section" id="faq">
  <h2>7. 常见问题 (FAQ)</h2>

  <h3>Q1: Status Banner 一直显示 "⏳ 加载中" 怎么办?</h3>
  <p>→ 检查 SMR 服务是否正常: <code>systemctl status smr</code> 或 <code>docker ps</code>。
    正常则点 ↻ 刷新按钮, 异常看日志 <code>/root/projects/supermodel_router/logs/</code>。</p>

  <h3>Q2: KPI 卡片显示 "—" 是空?</h3>
  <p>→ 还没产生调用。客户端发一次 chat completion 请求后再看。</p>

  <h3>Q3: Wizard 里 preset 点完没反应?</h3>
  <p>→ 看浏览器 console 报错。常见原因: 模型发现还没跑, 先点 Models 表 ↻ 刷新触发 discovery。</p>

  <h3>Q4: 点 "📋 日志" "⚙ 设置" 按钮只弹 toast?</h3>
  <p>→ 这些是 v3.27 stub, UI 没集成。临时用 Section 6 的 curl 命令。
    完整集成在 <a href="#version">版本历史</a> 看进度。</p>

  <h3>Q5: 添加 Provider 后, Models 表没出现新模型?</h3>
  <p>→ 触发模型发现: <code>curl -X POST /admin/api/v1/admin/model_discovery/trigger</code>,
    或重启 SMR 服务。</p>

  <h3>Q6: 路由结果跟预期不一样?</h3>
  <p>→ 看 Activity Stream 找具体调用, 检查 routing 策略 (
    <code>curl /admin/api/v1/admin/routing</code>), 必要时调整 preset 或新建分组。</p>

  <h3>Q7: 怎么导出全量统计?</h3>
  <p>→ 🟡 UI 暂未集成。临时: Activity Stream 点 "导出 CSV" (单次) 或后端直接读
    <code>engine_stats.json</code> / <code>loop_engine_tick_*.json</code>。</p>

  <h3>Q8: 免费模型路由什么时候生效?</h3>
  <p>→ SMR 启动时自动拉 free_models.json, 默认所有请求优先走免费。
    关闭: <code>routing.prefer_free: false</code> in config.yaml。</p>
</div>

<!-- ===== Section 8 ===== -->
<div class="section" id="trouble">
  <h2>8. 故障速查</h2>
  <p>详细文档: <a href="/docs/TROUBLESHOOTING.md">docs/TROUBLESHOOTING.md</a>。
    这里列最常见的 3 个:</p>

  <h3>🔴 Provider 全部 down</h3>
  <p>→ 检查网络/代理, 看 Provider card 是否有红色 sparkline。手动 probe:
  <pre><code>curl -X POST http://localhost:8765/admin/api/v1/admin/model-health/probe-all</code></pre>
  </p>

  <h3>🟡 配额耗尽</h3>
  <p>→ <code>curl /admin/api/v1/admin/quota/status</code> 看哪个 provider 红了, 切 fallback 或等 reset:
  <pre><code>curl -X POST http://localhost:8765/admin/api/v1/admin/quota/recover/&lt;model_path&gt;</code></pre>
  </p>

  <h3>🟡 模型 404 / 不可用</h3>
  <p>→ 该 provider 下线了。disable 它或换 fallback:
  <pre><code>curl -X POST http://localhost:8765/admin/api/v1/admin/providers/&lt;name&gt;/disable</code></pre>
  </p>

  <div class="callout danger">
    <span class="label">🛑 紧急</span>
    SMR 完全起不来? 看 <code>/root/projects/supermodel_router/logs/smr.log</code> 最新 100 行,
    或 <code>journalctl -u smr -n 100 --no-pager</code>。80% 是 config.yaml 改坏了, 用
    <code>cp config.yaml.bak.20260628_113843 config.yaml</code> 恢复。
  </div>
</div>

<!-- ===== Section 9 ===== -->
<div class="section" id="shortcuts">
  <h2>9. 快捷键 & 主题</h2>

  <h3>键盘快捷键</h3>
  <table class="feature-table">
    <tr><th>按键</th><th>功能</th></tr>
    <tr><td><code>⌘ K</code> / <code>Ctrl K</code></td><td>聚焦全局搜索</td></tr>
    <tr><td><code>Ctrl Shift L</code></td><td>循环切换主题 (dark/light/system)</td></tr>
    <tr><td><code>R</code></td><td>刷新所有 (待集成)</td></tr>
    <tr><td><code>H</code></td><td>健康检查 (待集成)</td></tr>
    <tr><td><code>L</code></td><td>日志面板 (待集成)</td></tr>
  </table>

  <h3>主题切换</h3>
  <p>3 种: 🌙 Dark (默认) / ☀️ Light / 💻 System (跟随 OS)。
    偏好存 localStorage, URL 参数 <code>?theme=light</code> 即时生效。
    设计 token 集中管理 (8 颜色 + 8 间距 + 3 圆角 + 3 阴影)。</p>
</div>

<!-- ===== Section 10 ===== -->
<div class="section" id="version">
  <h2>10. 版本历史</h2>
  <table class="feature-table">
    <tr><th>版本</th><th>日期</th><th>亮点</th></tr>
    <tr><td>v3.28.0</td><td>2026-07-07</td><td>Admin UI toast 文案修复 + Provider 配置清理</td></tr>
    <tr><td>v3.27.0</td><td>2026-07-02</td><td>Wizard 完整集成 (5 段流程 / 13 preset / 自定义筛选)</td></tr>
    <tr><td>v3.26.0</td><td>2026-07-02</td><td>Admin UI/UX 重做 + Dark/Light 主题切换</td></tr>
    <tr><td>v3.25.x</td><td>2026-06-28</td><td>Wizard DOM 部分迁移</td></tr>
    <tr><td>v3.20.x</td><td>2026-06-24</td><td>Provider 价格模型校准</td></tr>
    <tr><td>v3.11.0</td><td>2026-06-21</td><td>九宫 Dashboard (易经火候时序)</td></tr>
    <tr><td>v3.10.0</td><td>2026-06-19</td><td>Loop Engine + Maker/Checker</td></tr>
  </table>

  <h3>未来增量 (TODO)</h3>
  <ul>
    <li>v3.29: 参数量 badge + 完整筛选集成</li>
    <li>v3.29: Provider edit / API key / usage / probe modal 完整迁移</li>
    <li>v3.30+: 日志面板 / 设置面板 / 导出 / 备份 UI 集成 (替代 stub)</li>
  </ul>

  <div class="callout">
    <span class="label">📝 文档维护</span>
    本页随 SMR 版本演进更新。发现内容跟实际不符? 提交 issue 或在 Admin UI 点
    <code>⚙ 设置</code> (v3.30 集成后)。
  </div>
</div>

<div class="footer">
  <p>SMR __SMR_VERSION__ · 📖 使用指引 ·
    <a href="/admin">↩ Dashboard</a> ·
    <a href="/admin/api/v1/admin/version">API</a> ·
    <a href="/docs/">📚 Docs</a></p>
  <p style="margin-top:8px;font-size:11px">老大 2026-07-04 钦定 · 伊芙 review · echo 起草 + 集成</p>
</div>

</div>
</body>
</html>"""


async def admin_guide_page():
    """v3.28 增量: SMR Admin 使用指引页

    路由: GET /admin/guide (跟 /admin/9-gong 同级独立路由)
    模板: GUIDE_HTML (10 段: 概览/快速开始/Dashboard/Wizard/常用/高级/FAQ/故障/快捷键/版本)
    """
    from .version import VERSION as _V
    return HTMLResponse(content=GUIDE_HTML.replace("__SMR_VERSION__", _V))