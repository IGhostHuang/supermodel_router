"""
supermodel_router/admin_ui.py — Modern Dashboard + Dark/Light Theme (v3.28.0)

- ADMIN_HTML: 现代 dashboard (v3.28 集成 guide 页)
- 主题切换: dark / light / system 三态循环, localStorage 持久化, ⌘K + Ctrl+Shift+L 快捷键
- 视觉层级: 阴影 / hover / 动画 / 骨架屏 / 统一 toast / 响应式 / 设计 token
- /admin 与 /admin/: 返回 ADMIN_HTML
- /admin/9-gong: v3.11 8 卦 dashboard (保留)

后续:
- v3.27: v3.25.2 wizard DOM 完整迁移 (line 442-734 原版)
- v3.28: v3.15.0 参数量 badge + filterSizes 集成
- v3.29: 其他 modal (provider edit / api-key / version 等)
"""
import logging
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

LOG = logging.getLogger("admin_ui")
router = APIRouter()


# ============================================================
# v3.28.0 ADMIN_HTML — 现代 Dashboard + Dark/Light 主题 + Guide 页
# ============================================================

ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
<script>
  // 立即应用 URL ?theme= 参数 (在 CSS 解析前)
  (function(){
    var t = new URLSearchParams(location.search).get('theme');
    if (t === 'light' || t === 'dark' || t === 'system') {
      document.documentElement.dataset.theme = t === 'system' 
        ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
        : t;
    }
  })();
</script>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SuperModel Router v3.28</title>
<style>
/* ===== 设计 Token (Dark + Light 双套) ===== */
:root[data-theme="dark"]{
  --bg-0:#0a0a0e; --bg-1:#11121a; --bg-2:#181a26; --bg-3:#22253a;
  --border:#2a2d40; --border-strong:#3a3e58;
  --text-0:#e6e8f0; --text-1:#9ba1b8; --text-2:#6b7090; --text-3:#4a4e6a;
  --primary:#5b8def; --primary-h:#7aa3ff; --primary-glow:rgba(91,141,239,.18);
  --success:#22c55e; --success-glow:rgba(34,197,94,.18);
  --warn:#f59e0b; --warn-glow:rgba(245,158,11,.18);
  --danger:#ef4444; --danger-glow:rgba(239,68,68,.18);
  --purple:#a78bfa; --purple-glow:rgba(167,139,250,.18);
  --shadow-sm:0 1px 2px rgba(0,0,0,.3); --shadow:0 4px 12px rgba(0,0,0,.35); --shadow-lg:0 12px 32px rgba(0,0,0,.5);
  --overlay:rgba(0,0,0,.6);
}
:root[data-theme="light"]{
  --bg-0:#f8f9fc; --bg-1:#ffffff; --bg-2:#f1f3f9; --bg-3:#e5e8f0;
  --border:#e5e8f0; --border-strong:#cbd5e1;
  --text-0:#1a1d2e; --text-1:#4a4e6a; --text-2:#6b7090; --text-3:#9ba1b8;
  --primary:#2563eb; --primary-h:#1d4ed8; --primary-glow:rgba(37,99,235,.12);
  --success:#16a34a; --success-glow:rgba(22,163,74,.12);
  --warn:#d97706; --warn-glow:rgba(217,119,6,.12);
  --danger:#dc2626; --danger-glow:rgba(220,38,38,.12);
  --purple:#7c3aed; --purple-glow:rgba(124,58,237,.12);
  --shadow-sm:0 1px 2px rgba(0,0,0,.06); --shadow:0 4px 12px rgba(0,0,0,.08); --shadow-lg:0 12px 32px rgba(0,0,0,.12);
  --overlay:rgba(0,0,0,.4);
}
:root{
  --space-1:4px; --space-2:8px; --space-3:12px; --space-4:16px; --space-5:24px; --space-6:32px; --space-8:48px;
  --radius-sm:4px; --radius:8px; --radius-lg:12px; --radius-xl:16px;
  --font:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',sans-serif;
  --mono:ui-monospace,'SF Mono',Menlo,Consolas,monospace;
}
*{margin:0;padding:0;box-sizing:border-box;font-family:var(--font)}
html,body{background:var(--bg-0);color:var(--text-0);font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased;transition:background-color .2s,color .2s}
body{padding:var(--space-5);min-height:100vh}

/* ===== Top Nav ===== */
.topnav{display:flex;align-items:center;gap:var(--space-4);padding:var(--space-3) var(--space-5);background:var(--bg-1);border:1px solid var(--border);border-radius:var(--radius-lg);box-shadow:var(--shadow-sm);margin-bottom:var(--space-5);backdrop-filter:blur(12px)}
.brand{display:flex;align-items:center;gap:var(--space-3);font-weight:700;font-size:16px}
.brand-logo{width:32px;height:32px;border-radius:var(--radius);background:linear-gradient(135deg,var(--primary),var(--purple));display:grid;place-items:center;font-size:18px;box-shadow:0 0 16px var(--primary-glow);color:#fff}
.brand-version{font-family:var(--mono);font-size:11px;color:var(--text-2);background:var(--bg-2);padding:2px 8px;border-radius:var(--radius-sm)}
.search{flex:1;max-width:480px;margin:0 var(--space-4);position:relative}
.search input{width:100%;background:var(--bg-2);border:1px solid var(--border);color:var(--text-0);padding:var(--space-3) var(--space-3) var(--space-3) 36px;border-radius:var(--radius);font-size:13px;outline:none;transition:.15s}
.search input:focus{border-color:var(--primary);box-shadow:0 0 0 3px var(--primary-glow)}
.search-icon{position:absolute;left:12px;top:50%;transform:translateY(-50%);color:var(--text-2);font-size:13px;pointer-events:none}
.search kbd{position:absolute;right:8px;top:50%;transform:translateY(-50%);background:var(--bg-3);color:var(--text-2);padding:2px 6px;border-radius:4px;font-size:10px;font-family:var(--mono)}
.topnav-actions{display:flex;gap:var(--space-2);margin-left:auto}
.btn-icon{width:36px;height:36px;display:grid;place-items:center;background:transparent;border:1px solid var(--border);color:var(--text-1);border-radius:var(--radius);cursor:pointer;transition:.15s;font-size:15px}
.btn-icon:hover{background:var(--bg-2);border-color:var(--border-strong);color:var(--text-0)}

/* ===== Status Banner ===== */
.status-banner{display:flex;align-items:center;gap:var(--space-5);padding:var(--space-4) var(--space-5);background:linear-gradient(135deg,var(--bg-1),var(--bg-2));border:1px solid var(--border);border-radius:var(--radius-lg);margin-bottom:var(--space-5);box-shadow:var(--shadow);flex-wrap:wrap}
.status-dot{width:10px;height:10px;border-radius:50%;background:var(--success);box-shadow:0 0 0 4px var(--success-glow);animation:pulse 2s ease-in-out infinite;flex-shrink:0}
@keyframes pulse{0%,100%{box-shadow:0 0 0 4px var(--success-glow)}50%{box-shadow:0 0 0 8px transparent}}
.status-text{font-weight:600;font-size:15px;color:var(--text-0)}
.status-meta{display:flex;gap:var(--space-5);font-size:12px;color:var(--text-2);flex-wrap:wrap;margin-top:4px}
.status-meta b{color:var(--text-0);font-weight:600;font-family:var(--mono)}
.status-banner .quick-actions{display:flex;gap:var(--space-2);margin-left:auto;flex-wrap:wrap}

/* ===== Buttons ===== */
.btn{display:inline-flex;align-items:center;gap:6px;background:var(--bg-2);border:1px solid var(--border);color:var(--text-0);padding:8px 14px;border-radius:var(--radius);font-size:12px;font-weight:500;cursor:pointer;transition:.15s;font-family:inherit;text-decoration:none}
.btn:hover{background:var(--bg-3);border-color:var(--border-strong);transform:translateY(-1px);box-shadow:var(--shadow-sm)}
.btn:active{transform:translateY(0)}
.btn.primary{background:var(--primary);border-color:var(--primary);color:#fff;font-weight:600}
.btn.primary:hover{background:var(--primary-h);box-shadow:0 0 16px var(--primary-glow)}
.btn.success{background:var(--success);border-color:var(--success);color:#0a0a0e;font-weight:600}
.btn.danger{background:var(--danger);border-color:var(--danger);color:#fff}
.btn.sm{padding:5px 10px;font-size:11px}
.btn.ghost{background:transparent;border-color:var(--border)}

/* ===== KPI Cards ===== */
.kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:var(--space-4);margin-bottom:var(--space-5)}
.kpi-card{background:var(--bg-1);border:1px solid var(--border);border-radius:var(--radius-lg);padding:var(--space-5);position:relative;overflow:hidden;transition:.2s;cursor:default}
.kpi-card:hover{border-color:var(--border-strong);transform:translateY(-2px);box-shadow:var(--shadow-lg)}
.kpi-card::before{content:'';position:absolute;inset:0;background:radial-gradient(circle at 100% 0%,var(--accent-glow),transparent 70%);opacity:.6;pointer-events:none}
.kpi-card.blue{--accent:var(--primary);--accent-glow:var(--primary-glow)}
.kpi-card.green{--accent:var(--success);--accent-glow:var(--success-glow)}
.kpi-card.amber{--accent:var(--warn);--accent-glow:var(--warn-glow)}
.kpi-card.purple{--accent:var(--purple);--accent-glow:var(--purple-glow)}
.kpi-label{font-size:11px;text-transform:uppercase;color:var(--text-2);letter-spacing:.5px;font-weight:600}
.kpi-value{font-size:32px;font-weight:700;margin:8px 0 4px;font-family:var(--mono);letter-spacing:-1px}
.kpi-delta{font-size:11px;color:var(--text-1);display:flex;align-items:center;gap:4px;min-height:14px}

/* ===== Section ===== */
.section{display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-4);flex-wrap:wrap;gap:var(--space-3)}
.section-title{font-size:18px;font-weight:700;display:flex;align-items:center;gap:var(--space-2)}
.section-title .count{background:var(--bg-2);color:var(--text-1);padding:2px 8px;border-radius:var(--radius-sm);font-size:11px;font-family:var(--mono);font-weight:500}
.section-actions{display:flex;gap:var(--space-2);align-items:center;flex-wrap:wrap}

/* ===== Provider Grid ===== */
.provider-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:var(--space-4);margin-bottom:var(--space-6)}
.provider-card{background:var(--bg-1);border:1px solid var(--border);border-radius:var(--radius-lg);padding:var(--space-4);transition:.2s;position:relative;overflow:hidden}
.provider-card:hover{border-color:var(--primary);box-shadow:0 0 0 1px var(--primary-glow),var(--shadow-lg);transform:translateY(-2px)}
.provider-card.degraded{border-left:3px solid var(--warn)}
.provider-card.down{border-left:3px solid var(--danger)}
.provider-head{display:flex;align-items:center;gap:var(--space-3);margin-bottom:var(--space-3)}
.provider-dot{width:8px;height:8px;border-radius:50%;position:relative;flex-shrink:0}
.provider-dot.ok{background:var(--success);box-shadow:0 0 8px var(--success-glow)}
.provider-dot.degraded{background:var(--warn);box-shadow:0 0 8px var(--warn-glow);animation:pulse 1s infinite}
.provider-dot.down{background:var(--danger);box-shadow:0 0 8px var(--danger-glow)}
.provider-name{font-weight:600;font-size:14px}
.provider-models{font-size:11px;color:var(--text-2);margin-left:auto;font-family:var(--mono)}
.provider-stats{display:flex;gap:var(--space-4);font-size:11px;color:var(--text-1);margin-bottom:var(--space-3)}
.provider-stats span b{color:var(--text-0);font-weight:600;font-family:var(--mono);margin-right:4px}
.provider-spark{height:36px;margin-bottom:var(--space-3);background:var(--bg-2);border-radius:var(--radius-sm);position:relative;overflow:hidden;display:flex;align-items:end;padding:4px;gap:1px}
.spark-bar{flex:1;background:linear-gradient(180deg,var(--primary),var(--purple));border-radius:1px;opacity:.7;min-height:2px;transition:.3s}
.provider-spark:hover .spark-bar{opacity:1}
.provider-actions{display:flex;gap:var(--space-1);opacity:0;transition:.15s}
.provider-card:hover .provider-actions{opacity:1}

/* ===== Activity Stream ===== */
.activity{background:var(--bg-1);border:1px solid var(--border);border-radius:var(--radius-lg);padding:var(--space-4);margin-bottom:var(--space-6)}
.activity-row{display:grid;grid-template-columns:80px 16px 1fr auto auto auto;gap:var(--space-3);align-items:center;padding:var(--space-2) 0;font-family:var(--mono);font-size:12px;border-bottom:1px solid var(--border)}
.activity-row:last-child{border-bottom:none}
.activity-time{color:var(--text-3)}
.activity-route{color:var(--text-0)}
.activity-provider{color:var(--text-2);font-size:11px}
.activity-latency{color:var(--text-1);text-align:right}
.activity-cost{color:var(--success);text-align:right;font-weight:600}
.activity-status{text-align:center}
.status-icon.ok{color:var(--success)}
.status-icon.warn{color:var(--warn)}
.status-icon.fail{color:var(--danger)}
.empty-state{text-align:center;padding:var(--space-6);color:var(--text-2);background:var(--bg-2);border-radius:var(--radius);margin:var(--space-3) 0}

/* ===== Models Table ===== */
.models-table{background:var(--bg-1);border:1px solid var(--border);border-radius:var(--radius-lg);overflow:hidden;margin-bottom:var(--space-6)}
.models-thead{background:var(--bg-2);padding:var(--space-3) var(--space-4);display:grid;grid-template-columns:2fr 1fr 1fr 1fr 80px 80px;gap:var(--space-3);font-size:11px;text-transform:uppercase;color:var(--text-2);letter-spacing:.5px;font-weight:600}
.models-row{padding:var(--space-3) var(--space-4);display:grid;grid-template-columns:2fr 1fr 1fr 1fr 80px 80px;gap:var(--space-3);align-items:center;border-bottom:1px solid var(--border);font-size:13px;transition:.15s}
.models-row:hover{background:var(--bg-2)}
.models-row:last-child{border-bottom:none}
.model-id{font-family:var(--mono);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tag{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:var(--radius-sm);font-size:10px;font-weight:600;font-family:var(--mono);white-space:nowrap}
.tag.free{background:var(--success-glow);color:var(--success)}
.tag.paid{background:var(--warn-glow);color:var(--warn)}
.tag.size-xl{background:var(--purple-glow);color:var(--purple)}
.tag.size-l{background:var(--warn-glow);color:var(--warn)}
.tag.size-m{background:var(--primary-glow);color:var(--primary)}
.tag.size-s{background:var(--bg-3);color:var(--text-2)}
.health-dot{display:inline-flex;align-items:center;gap:6px;font-size:11px;color:var(--text-1)}
.health-dot .dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.health-dot.ok .dot{background:var(--success);box-shadow:0 0 6px var(--success-glow)}
.health-dot.warn .dot{background:var(--warn)}
.health-dot.fail .dot{background:var(--danger)}
.score{font-family:var(--mono);font-weight:600;color:var(--text-0)}
.score.hi{color:var(--success)}
.score.mid{color:var(--warn)}
.score.lo{color:var(--danger)}

/* ===== Toast 系统 ===== */
.toast-container{position:fixed;bottom:24px;right:24px;display:flex;flex-direction:column;gap:var(--space-2);z-index:9999;pointer-events:none;max-width:380px}
.toast{display:flex;align-items:center;gap:var(--space-3);background:var(--bg-1);border:1px solid var(--border-strong);border-radius:var(--radius);padding:12px 16px;box-shadow:var(--shadow-lg);min-width:280px;animation:slideIn .3s ease;pointer-events:auto}
@keyframes slideIn{from{transform:translateX(120%);opacity:0}to{transform:translateX(0);opacity:1}}
.toast.out{animation:slideOut .3s ease forwards}
@keyframes slideOut{to{transform:translateX(120%);opacity:0}}
.toast.success{border-left:3px solid var(--success)}
.toast.warn{border-left:3px solid var(--warn)}
.toast.error{border-left:3px solid var(--danger)}
.toast.info{border-left:3px solid var(--primary)}
.toast-icon{font-size:16px;flex-shrink:0}
.toast.success .toast-icon{color:var(--success)}
.toast.warn .toast-icon{color:var(--warn)}
.toast.error .toast-icon{color:var(--danger)}
.toast.info .toast-icon{color:var(--primary)}
.toast-text{font-size:12px;color:var(--text-0);flex:1;line-height:1.4}
.toast-text b{display:block;font-weight:600;margin-bottom:2px;color:var(--text-0)}

/* ===== Skeleton ===== */
.skeleton{background:linear-gradient(90deg,var(--bg-2) 25%,var(--bg-3) 50%,var(--bg-2) 75%);background-size:200% 100%;animation:shimmer 1.5s infinite;border-radius:var(--radius-sm)}
@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}

/* ===== Modal (v3.28 通用) ===== */
.modal-overlay{position:fixed;inset:0;background:var(--overlay);z-index:500;display:none;align-items:center;justify-content:center;padding:20px;backdrop-filter:blur(4px)}
.modal-overlay.active{display:flex}
.modal-content{background:var(--bg-1);border:1px solid var(--border);border-radius:var(--radius-lg);max-width:900px;width:100%;max-height:90vh;overflow:auto;padding:var(--space-5);box-shadow:var(--shadow-lg)}
.modal-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-4)}
.modal-title{font-size:18px;font-weight:700}
.modal-close{background:transparent;border:none;color:var(--text-2);font-size:20px;cursor:pointer;padding:4px 8px;border-radius:var(--radius-sm)}
.modal-close:hover{background:var(--bg-2);color:var(--text-0)}

/* ===== Wizard (v3.27 完整迁移) ===== */
.wizard-presets-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:var(--space-3);margin-bottom:var(--space-5)}
.wizard-preset-card{background:var(--bg-2);border:1px solid var(--border);border-radius:var(--radius);padding:var(--space-3);cursor:pointer;transition:.15s;position:relative}
.wizard-preset-card:hover{border-color:var(--primary);background:var(--bg-3);transform:translateY(-1px);box-shadow:var(--shadow-sm)}
.wizard-preset-card.selected{border-color:var(--success);background:var(--success-glow)}
.wizard-preset-card.disabled{opacity:.4;cursor:not-allowed}
.wizard-preset-card .preset-icon{font-size:24px;margin-bottom:6px}
.wizard-preset-card .preset-name{font-size:13px;font-weight:500;color:var(--text-0);margin-bottom:4px}
.wizard-preset-card .preset-desc{font-size:11px;color:var(--text-2);line-height:1.4;margin-bottom:6px}
.wizard-preset-card .preset-count{position:absolute;top:8px;right:10px;font-size:10px;background:var(--bg-1);padding:2px 8px;border-radius:10px;color:var(--primary);font-weight:500}
.wizard-preset-card .preset-count.zero{background:var(--danger-glow);color:var(--danger)}
.wizard-filter-panel{background:var(--bg-2);border:1px solid var(--border);border-radius:var(--radius);padding:var(--space-4);margin-bottom:var(--space-5)}
.filter-row{margin-bottom:var(--space-3)}
.filter-row label{display:block;font-size:12px;color:var(--text-2);margin-bottom:6px;font-weight:500}
.filter-row .filter-input,.filter-row .filter-select{background:var(--bg-0);border:1px solid var(--border);color:var(--text-0);padding:8px 10px;border-radius:var(--radius-sm);font-size:13px;outline:none;width:100%;transition:.15s}
.filter-row .filter-input:focus,.filter-row .filter-select:focus{border-color:var(--primary);box-shadow:0 0 0 3px var(--primary-glow)}
.chip-group{display:flex;flex-wrap:wrap;gap:6px}
.chip{background:var(--bg-0);border:1px solid var(--border);color:var(--text-1);padding:4px 10px;border-radius:14px;font-size:11px;cursor:pointer;transition:.15s;user-select:none}
.chip:hover{border-color:var(--primary);color:var(--text-0)}
.chip.selected{background:var(--primary);border-color:var(--primary);color:#fff;font-weight:500}
.wizard-models-list{background:var(--bg-2);border:1px solid var(--border);border-radius:var(--radius);padding:var(--space-3);margin-bottom:var(--space-5);max-height:300px;overflow-y:auto}
.wizard-models-list .model-row{padding:6px 10px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;font-size:12px;transition:.15s}
.wizard-models-list .model-row:hover{background:var(--bg-3)}
.wizard-models-list .model-row:last-child{border-bottom:none}
.wizard-generate-panel{background:var(--bg-2);border:1px solid var(--border);border-radius:var(--radius);padding:var(--space-4);margin-bottom:var(--space-5)}
.btn-sm{padding:5px 10px;font-size:11px;background:var(--bg-0);border:1px solid var(--border);color:var(--text-0);border-radius:var(--radius-sm);cursor:pointer;font-family:inherit;transition:.15s}
.btn-sm:hover{background:var(--bg-3);border-color:var(--border-strong)}
.btn-sm.primary{background:var(--primary);border-color:var(--primary);color:#fff;font-weight:500}
.btn-sm.primary:hover{background:var(--primary-h)}

/* ===== Footer ===== */
.footer{text-align:center;padding:var(--space-5);color:var(--text-3);font-size:11px}

/* ===== 响应式 ===== */
@media (max-width:1024px){
  .kpi-grid{grid-template-columns:repeat(2,1fr)}
  .provider-grid{grid-template-columns:repeat(2,1fr)}
}
@media (max-width:640px){
  body{padding:var(--space-3)}
  .topnav{flex-wrap:wrap;gap:var(--space-2)}
  .search{order:3;flex:1 1 100%;margin:var(--space-2) 0 0;max-width:none}
  .kpi-grid,.provider-grid{grid-template-columns:1fr}
  .status-banner{flex-direction:column;align-items:flex-start;gap:var(--space-3)}
  .status-banner .quick-actions{margin-left:0}
  .models-thead,.models-row{grid-template-columns:2fr 1fr 80px 60px;gap:var(--space-2)}
  .models-thead > div:nth-child(4),.models-thead > div:nth-child(6),
  .models-row > *:nth-child(4),.models-row > *:nth-child(6){display:none}
  .activity-row{grid-template-columns:60px 16px 1fr 60px;gap:var(--space-2)}
  .activity-provider,.activity-cost{display:none}
}
</style>
</head>
<body>

<!-- ===== Top Nav ===== -->
<nav class="topnav">
  <div class="brand">
    <div class="brand-logo">⚡</div>
    <span>SuperModel Router</span>
    <span class="brand-version" id="brandVersion">v3.28.0</span>
  </div>
  <div class="search">
    <span class="search-icon">🔍</span>
    <input id="globalSearch" placeholder="搜索模型、provider、路由规则…" oninput="onGlobalSearch(this.value)">
    <kbd>⌘K</kbd>
  </div>
  <div class="topnav-actions">
    <button class="btn-icon" onclick="openProviderKeys()" title="Provider Key 管理 — 下游 provider 的 key (K)">🔑</button>
    <button class="btn-icon" onclick="openPublicKeys()" title="Public Key 管理 — SMR 对外发的 wizard key (P)">🗝️</button>
    <button class="btn-icon" onclick="openWizard()" title="Wizard 智能分组 (W)">🧙</button>
    <a class="btn-icon" href="/admin/guide" title="使用指引 (G)">📖</a>
    <button class="btn-icon" onclick="refreshAll()" title="刷新所有 (R)">↻</button>
    <button class="btn-icon" onclick="probeHealthAll()" title="健康检查 (H)">⚡</button>
    <button class="btn-icon" onclick="openLogs()" title="日志 (L)">📋</button>
    <button class="btn-icon" onclick="openSettings()" title="设置 (, )">⚙</button>
    <button class="btn-icon" id="themeToggle" onclick="cycleTheme()" title="主题切换 (Ctrl+Shift+L)">🌙</button>
  </div>
</nav>

<!-- ===== Status Banner ===== -->
<div class="status-banner">
  <div class="status-dot" id="statusDot"></div>
  <div style="flex:1;min-width:240px">
    <div class="status-text" id="statusText">⏳ 加载中…</div>
    <div class="status-meta" id="statusMeta"></div>
  </div>
  <div class="quick-actions">
    <button class="btn ghost" onclick="exportReport()">📊 导出</button>
    <button class="btn ghost" onclick="backupConfig()">📦 备份</button>
    <button class="btn primary" onclick="refreshAll()">↻ 刷新</button>
    <button class="btn success" onclick="probeHealthAll()">⚡ Probe</button>
  </div>
</div>

<!-- ===== KPI Cards ===== -->
<div class="kpi-grid">
  <div class="kpi-card blue">
    <div class="kpi-label">今日调用</div>
    <div class="kpi-value" id="kpiTodayCalls"><span class="skeleton" style="display:inline-block;width:80px;height:32px;vertical-align:middle"></span></div>
    <div class="kpi-delta" id="kpiTodayCallsDelta"></div>
  </div>
  <div class="kpi-card green">
    <div class="kpi-label">成功率</div>
    <div class="kpi-value" id="kpiSuccessRate">—</div>
    <div class="kpi-delta" id="kpiSuccessRateDelta"></div>
  </div>
  <div class="kpi-card amber">
    <div class="kpi-label">平均延迟</div>
    <div class="kpi-value" id="kpiAvgLatency">—</div>
    <div class="kpi-delta" id="kpiAvgLatencyDelta"></div>
  </div>
  <div class="kpi-card purple">
    <div class="kpi-label">免费路由</div>
    <div class="kpi-value" id="kpiFreeCalls">—</div>
    <div class="kpi-delta" id="kpiFreeCallsDelta"></div>
  </div>
</div>

<!-- ===== Providers ===== -->
<div class="section">
  <div class="section-title">Providers <span class="count" id="providerCount">—</span></div>
  <div class="section-actions">
    <button class="btn ghost sm" onclick="enableAllProviders()">全部启用</button>
    <button class="btn ghost sm" onclick="refreshAllProviders()">全部刷新</button>
    <button class="btn sm" onclick="openAddProvider()">＋ 新增</button>
  </div>
</div>
<div class="provider-grid" id="providerGrid">
  <div class="provider-card"><div class="skeleton" style="height:120px"></div></div>
  <div class="provider-card"><div class="skeleton" style="height:120px"></div></div>
  <div class="provider-card"><div class="skeleton" style="height:120px"></div></div>
</div>

<!-- ===== Activity Stream ===== -->
<div class="section">
  <div class="section-title">Activity Stream <span class="count" id="activityCount">recent</span></div>
  <div class="section-actions">
    <button class="btn ghost sm" onclick="exportActivity()">导出 CSV</button>
    <button class="btn ghost sm" onclick="viewAllActivity()">查看全部</button>
  </div>
</div>
<div class="activity" id="activityStream">
  <div class="empty-state">⏳ 加载活动流…</div>
</div>

<!-- ===== Models Table ===== -->
<div class="section">
  <div class="section-title">Models <span class="count" id="modelCount">—</span></div>
  <div class="section-actions">
    <button class="btn ghost sm" onclick="filterByProvider()">Provider ▾</button>
    <button class="btn ghost sm" onclick="filterBySize()">参数量 ▾</button>
    <button class="btn ghost sm" onclick="filterByCapability()">能力 ▾</button>
    <button class="btn ghost sm" onclick="filterByPrice()">价格 ▾</button>
    <button class="btn primary sm" onclick="openWizard()">＋ Wizard</button>
  </div>
</div>
<div class="models-table">
  <div class="models-thead">
    <div>Model</div><div>Provider</div><div>Price</div><div>Size</div><div>Health</div><div>Score</div>
  </div>
  <div id="modelRows">
    <div class="models-row"><div class="skeleton" style="height:14px;width:90%"></div><div></div><div></div><div></div><div></div><div></div></div>
    <div class="models-row"><div class="skeleton" style="height:14px;width:80%"></div><div></div><div></div><div></div><div></div><div></div></div>
    <div class="models-row"><div class="skeleton" style="height:14px;width:85%"></div><div></div><div></div><div></div><div></div><div></div></div>
  </div>
</div>

<div class="footer">
  SuperModel Router v3.28.0 · Dark/Light theme toggle · Press <kbd>Ctrl+Shift+L</kbd> to cycle theme
</div>

<!-- ===== Toast Container ===== -->
<div class="toast-container" id="toastContainer"></div>

<!-- ===== Wizard Modal (v3.27 完整迁移) ===== -->
<div id="wizardModal" class="modal-overlay" onclick="if(event.target===this)closeWizard()">
  <div class="modal-content" style="max-width:1100px">
    <div class="modal-header">
      <div class="modal-title">🧙 模型分组 Wizard</div>
      <button class="modal-close" onclick="closeWizard()">×</button>
    </div>

    <!-- 第一部分: 13 个预设场景卡片 -->
    <h3 style="font-size:13px;color:var(--text-2);margin:0 0 var(--space-3)">✨ 快速开始: 选一个预设场景</h3>
    <div class="wizard-presets-grid" id="wizardPresetsGrid">
      <div class="loading">加载中…</div>
    </div>

    <!-- 第二部分: 自定义筛选 -->
    <h3 style="font-size:13px;color:var(--text-2);margin:var(--space-5) 0 var(--space-2)">🔍 或自定义筛选条件</h3>
    <div class="wizard-filter-panel">
      <div class="filter-row">
        <label>Provider (多选)</label>
        <div class="chip-group" id="wizardFilterProviders"></div>
      </div>
      <div class="filter-row">
        <label>上下文窗口</label>
        <select id="wizardFilterContext" class="filter-select">
          <option value="0">全部</option>
          <option value="8000">≥ 8K</option>
          <option value="16000">≥ 16K</option>
          <option value="32000">≥ 32K</option>
          <option value="64000">≥ 64K</option>
          <option value="100000">≥ 100K</option>
          <option value="128000">≥ 128K</option>
          <option value="200000">≥ 200K</option>
        </select>
      </div>
      <div class="filter-row">
        <label>最低 Quality Score: <span id="qualityVal" style="color:var(--primary);font-weight:500">0</span></label>
        <input type="range" id="wizardFilterQuality" min="0" max="100" value="0" step="5" oninput="document.getElementById('qualityVal').textContent=this.value">
      </div>
      <div class="filter-row">
        <label>最低 Speed Score: <span id="speedVal" style="color:var(--primary);font-weight:500">0</span></label>
        <input type="range" id="wizardFilterSpeed" min="0" max="100" value="0" step="5" oninput="document.getElementById('speedVal').textContent=this.value">
      </div>
      <div class="filter-row">
        <label>Modality</label>
        <select id="wizardFilterModality" class="filter-select">
          <option value="">全部</option>
          <option value="text">纯文本</option>
          <option value="multimodal">多模态</option>
          <option value="image">视觉</option>
          <option value="image-gen">图像生成</option>
          <option value="audio">音频</option>
          <option value="video">视频</option>
        </select>
      </div>
      <div class="filter-row">
        <label>Tags (含任一)</label>
        <div class="chip-group" id="wizardFilterTags"></div>
      </div>
      <div style="margin-top:var(--space-3);text-align:right">
        <button class="btn-sm" onclick="resetWizardFilter()">🔄 重置</button>
        <button class="btn-sm primary" onclick="applyWizardFilter()">🔍 应用筛选</button>
      </div>
    </div>

    <!-- 第三部分: 匹配模型列表 -->
    <h3 style="font-size:13px;color:var(--text-2);margin:var(--space-5) 0 var(--space-2)">
      匹配模型 (<span id="wizardMatchCount">0</span>)
      <span style="float:right">
        <button class="btn-sm" onclick="wizardSelectAll()">☑ 全选</button>
        <button class="btn-sm" onclick="wizardSelectNone()">☐ 清选</button>
      </span>
    </h3>
    <div class="wizard-models-list" id="wizardModelsList">
      <div class="empty-state">👆 选一个预设场景 或 自定义筛选查看匹配模型</div>
    </div>

    <!-- 第四部分: 一键生成 -->
    <h3 style="font-size:13px;color:var(--text-2);margin:var(--space-5) 0 var(--space-2)">✨ 生成模型分组</h3>
    <div class="wizard-generate-panel">
      <div class="filter-row">
        <label>分组名</label>
        <input type="text" id="wizardGroupName" placeholder="my-premium-group" class="filter-input">
      </div>
      <div class="filter-row">
        <label>轮询策略</label>
        <select id="wizardGroupStrategy" class="filter-select">
          <option value="round-robin-group" selected>round-robin-group (新 default)</option>
          <option value="flat">flat (老 v4 全局降序)</option>
          <option value="group-failover">group-failover (按 group 优先级)</option>
          <option value="group-weighted">group-weighted (加权随机)</option>
        </select>
      </div>
      <div class="filter-row">
        <label><input type="checkbox" id="wizardCreateApiKey" checked> 自动生成 API key (绑定到 group)</label>
      </div>
      <div class="filter-row">
        <label>API key 名 (默认 = group name + "-key")</label>
        <input type="text" id="wizardApiKeyName" placeholder="(可选)" class="filter-input">
      </div>
      <div style="margin-top:var(--space-3);text-align:right">
        <button class="btn-sm" onclick="previewWizardGroup()">🔍 预览</button>
        <button class="btn-sm primary" onclick="generateWizardGroup()">✨ 生成分组</button>
      </div>
    </div>

    <!-- 第五部分: 生成结果展示 -->
    <div id="wizardResultPanel" style="display:none;margin-top:var(--space-5);padding:var(--space-4);background:var(--success-glow);border:1px solid var(--success);border-radius:var(--radius)">
      <h3 style="font-size:14px;color:var(--success);margin:0 0 var(--space-3)">✅ 分组生成成功</h3>
      <div id="wizardResultContent"></div>
    </div>
  </div>
</div>

<!-- ===== Provider Keys Modal (v3.28.1) ===== -->
<div id="providerKeysModal" class="modal-overlay" onclick="if(event.target===this)closeProviderKeys()">
  <div class="modal-content" style="max-width:900px">
    <div class="modal-header">
      <div class="modal-title">🔑 Provider Key 管理 <span style="font-size:12px;color:var(--text-2);font-weight:400">下游 provider 的 API key (openrouter/nvidia/newapi…)</span> <button id="pkToggleBtn" class="btn ghost sm" style="margin-left:8px" onclick="toggleProviderKeyVisibility()" title="切换完整 key 可见性">👁️ 显示完整</button></div>
      <button class="modal-close" onclick="closeProviderKeys()">×</button>
    </div>
    <div style="margin-bottom:var(--space-4);padding:var(--space-3);background:var(--bg-2);border-radius:var(--radius);font-size:12px;color:var(--text-2)">
      💡 <b>Provider Key</b> = SMR 用来调下游 (openrouter/nvidia 等) 的凭证。多 key 会轮询。<br>
      ⚠️ 添加后自动刷新 provider 目录, 新 key 可能解锁新模型。
    </div>
    <div id="providerKeysList"><div class="empty-state">⏳ 加载中…</div></div>
    <div style="margin-top:var(--space-5);padding-top:var(--space-4);border-top:1px solid var(--border)">
      <h3 style="font-size:14px;margin:0 0 var(--space-3)">➕ 添加新 Key</h3>
      <div style="display:grid;grid-template-columns:200px 1fr auto;gap:var(--space-2)">
        <select id="pkProviderSel" class="filter-select"></select>
        <input type="text" id="pkNewKey" placeholder="sk-... / nvapi-... / 粘贴 API key" class="filter-input">
        <button class="btn primary" onclick="addProviderKey()">添加</button>
      </div>
    </div>
  </div>
</div>

<!-- ===== Public Keys Modal (v3.28.1) ===== -->
<div id="publicKeysModal" class="modal-overlay" onclick="if(event.target===this)closePublicKeys()">
  <div class="modal-content" style="max-width:1000px">
    <div class="modal-header">
      <div class="modal-title">🗝️ Public Key 管理 <span style="font-size:12px;color:var(--text-2);font-weight:400">SMR 对外发放的 key (给 Hermes / 客户端用)</span> <button id="pubKeyToggleBtn" class="btn ghost sm" style="margin-left:8px" onclick="togglePublicKeyVisibility()" title="显示本会话内新建时缓存的原始 key (刷新即丢, 旧 key 无法恢复)">👁️ 显示完整</button></div>
      <button class="modal-close" onclick="closePublicKeys()">×</button>
    </div>
    <div style="margin-bottom:var(--space-4);padding:var(--space-3);background:var(--bg-2);border-radius:var(--radius);font-size:12px;color:var(--text-2)">
      💡 <b>Public Key</b> = 别人调 SMR 时用的 <code>smr-pub-...</code> key。可挂 model_filter 限制模型 + rate_limit_rpm 限流。<br>
      ⚠️ 原始 key 只在创建那一次返回, 之后只存哈希 — 一定要复制保存。
    </div>
    <div id="publicKeysList"><div class="empty-state">⏳ 加载中…</div></div>
    <div style="margin-top:var(--space-5);padding-top:var(--space-4);border-top:1px solid var(--border)">
      <h3 style="font-size:14px;margin:0 0 var(--space-3)">➕ 创建新 Public Key</h3>
      <div style="display:grid;grid-template-columns:1fr 100px 1fr auto;gap:var(--space-2)">
        <input type="text" id="ppkName" placeholder="name (字母/数字/-/_)" class="filter-input">
        <input type="number" id="ppkRpm" placeholder="60" value="60" class="filter-input" title="rate_limit_rpm">
        <input type="text" id="ppkFilter" placeholder="model_filter (逗号分隔, 如 *:free, openrouter:*)" class="filter-input">
        <button class="btn primary" onclick="createPublicKey()">创建</button>
      </div>
      <div id="ppkNewKeyDisplay" style="display:none;margin-top:var(--space-3);padding:var(--space-3);background:var(--success-glow);border:1px solid var(--success);border-radius:var(--radius)">
        <div style="font-size:12px;color:var(--success);margin-bottom:var(--space-2);font-weight:600">⚠️ 请立刻复制保存原始 Key (仅显示这一次)</div>
        <code id="ppkNewKeyValue" style="display:block;padding:var(--space-2);background:var(--bg-0);border-radius:var(--radius-sm);font-family:monospace;word-break:break-all;user-select:all"></code>
        <button class="btn ghost sm" style="margin-top:var(--space-2)" onclick="copyToClipboard(document.getElementById('ppkNewKeyValue').textContent)">📋 复制</button>
      </div>
    </div>
  </div>
</div>

<script>
/* ============================================================
 * SMR v3.28.0 — Modern Dashboard + Guide Page
 * ============================================================ */

// ===== 工具函数 =====
function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function formatUptime(s) {
  if (!s || s < 0) return '—';
  s = Math.floor(s);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

// ===== 主题切换 (dark / light / system) =====
const THEME_KEY = 'smr-theme';
const THEME_ORDER = ['dark', 'light', 'system'];
function getTheme() { return localStorage.getItem(THEME_KEY) || 'dark'; }
function applyTheme(mode) {
  const m = mode || getTheme();
  if (m === 'system') {
    const sysDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.dataset.theme = sysDark ? 'dark' : 'light';
  } else {
    document.documentElement.dataset.theme = m;
  }
  updateThemeIcon();
}
function cycleTheme() {
  const cur = getTheme();
  const next = THEME_ORDER[(THEME_ORDER.indexOf(cur) + 1) % THEME_ORDER.length];
  localStorage.setItem(THEME_KEY, next);
  applyTheme(next);
  const labels = {dark:'🌙 暗色', light:'☀️ 亮色', system:'💻 跟随系统'};
  toast('success', '主题已切换', labels[next]);
}
function updateThemeIcon() {
  const icon = document.getElementById('themeToggle');
  if (!icon) return;
  const m = getTheme();
  icon.textContent = m === 'dark' ? '🌙' : m === 'light' ? '☀️' : '💻';
  icon.title = `主题: ${m} (点击切换, Ctrl+Shift+L)`;
}

// ===== Toast 系统 =====
function toast(type, title, msg, duration = 4000) {
  const c = document.getElementById('toastContainer');
  if (!c) { console.log(`[${type}] ${title}: ${msg||''}`); return; }
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  const icons = {success:'✓', warn:'⚠', error:'✗', info:'ℹ'};
  t.innerHTML = `<span class="toast-icon">${icons[type]||'ℹ'}</span>
    <div class="toast-text"><b>${escapeHtml(title)}</b>${msg ? escapeHtml(msg) : ''}</div>`;
  c.appendChild(t);
  const timeout = setTimeout(() => dismissToast(t), duration);
  t._timeout = timeout;
  t.onclick = () => { clearTimeout(timeout); dismissToast(t); };
}
function dismissToast(t) {
  t.classList.add('out');
  setTimeout(() => t.remove(), 300);
}

// ===== 数据加载 =====
const BASE = '';
async function fetchJSON(path) {
  try {
    const r = await fetch(BASE + path, {signal: AbortSignal.timeout(8000)});
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return await r.json();
  } catch (e) {
    console.error('fetch failed:', path, e);
    return null;
  }
}

async function loadAll() {
  const [health, stats, providers, models] = await Promise.all([
    fetchJSON('/v1/health'),
    fetchJSON('/v1/admin/stats'),
    fetchJSON('/v1/admin/providers?include_disabled=true'),
    fetchJSON('/v1/admin/models'),
  ]);
  // 缓存 stats 和 health 供 renderProviders 合并用
  window._lastStats = stats || {};
  window._lastHealth = health || {};
  window._lastProviders = providers || {};
  if (health) renderStatusBanner(health);
  if (stats) renderKPIs(stats);
  if (providers) renderProviders(providers);
  if (models) renderModels(models);
  loadActivity();
}

function renderStatusBanner(h) {
  const ver = document.getElementById('brandVersion');
  if (ver && h.version) ver.textContent = h.version;
  
  const txt = document.getElementById('statusText');
  txt.textContent = `系统正常 · ${formatUptime(h.uptime_seconds)} uptime`;
  
  const provs = h.providers || {};
  const entries = Object.entries(provs).map(([name, p]) => ({name, ...p}));
  const enabled = entries.filter(p => p.enabled !== false && !p.disabled);
  const healthy = enabled.filter(p => !p.degraded);
  const degraded = enabled.filter(p => p.degraded);
  const down = entries.filter(p => p.enabled === false || p.disabled);
  
  document.getElementById('statusMeta').innerHTML = `
    <span><b>${h.total_models || 0}</b> models</span>
    <span><b>${entries.length}</b> providers</span>
    <span><b>${healthy.length}</b> healthy</span>
    <span><b>${degraded.length}</b> degraded</span>
    <span><b>${down.length}</b> down</span>
    <span>·</span>
    <span>Next refresh in <b id="nextRefresh">30s</b></span>
  `;
  
  // 健康度 → status dot 颜色
  const dot = document.getElementById('statusDot');
  if (down.length > 0 || degraded.length > 2) {
    dot.style.background = 'var(--warn)';
    dot.style.boxShadow = '0 0 0 4px var(--warn-glow)';
    txt.textContent = `部分 provider 异常 · ${formatUptime(h.uptime_seconds)} uptime`;
  }
}

function renderKPIs(stats) {
  let totalCalls = 0, successCalls = 0, latWeighted = 0, latCount = 0;
  Object.entries(stats).forEach(([k, s]) => {
    if (k === 'global' || typeof s !== 'object') return;
    totalCalls += s.total_calls || 0;
    successCalls += s.success_calls || 0;
    if (s.avg_latency_ms && s.total_calls) {
      latWeighted += s.avg_latency_ms * s.total_calls;
      latCount += s.total_calls;
    }
  });
  const successRate = totalCalls > 0 ? (successCalls / totalCalls * 100) : 0;
  const avgLat = latCount > 0 ? (latWeighted / latCount / 1000) : 0;
  
  document.getElementById('kpiTodayCalls').textContent = totalCalls.toLocaleString();
  document.getElementById('kpiTodayCallsDelta').textContent = 
    totalCalls > 0 ? `${successCalls} ✓ / ${totalCalls - successCalls} ✗` : '等待数据…';
  
  document.getElementById('kpiSuccessRate').innerHTML = `${successRate.toFixed(1)}<span style="font-size:18px">%</span>`;
  document.getElementById('kpiSuccessRateDelta').textContent = 
    successRate >= 90 ? '✓ 健康' : successRate >= 70 ? '⚠ 关注' : successRate > 0 ? '✗ 异常' : '等待数据…';
  
  document.getElementById('kpiAvgLatency').innerHTML = `${avgLat.toFixed(2)}<span style="font-size:18px">s</span>`;
  document.getElementById('kpiAvgLatencyDelta').textContent = 
    latCount > 0 ? `${latCount.toLocaleString()} calls 采样` : '等待数据…';
  
  // 免费估算: success_calls * 0 (免费 = 0, paid 平均 $0.005/call 估算)
  document.getElementById('kpiFreeCalls').innerHTML = `${totalCalls}<span style="font-size:18px">total</span>`;
  document.getElementById('kpiFreeCallsDelta').textContent = 
    totalCalls > 0 ? `${Math.round(successRate)}% success rate` : '等待数据…';
}

function renderProviders(providers) {
  const grid = document.getElementById('providerGrid');
  if (!grid) return;

  // 适配多种返回结构
  let list = [];
  if (Array.isArray(providers)) list = providers;
  else if (providers.providers) list = providers.providers;
  else if (typeof providers === 'object') {
    list = Object.entries(providers).map(([name, p]) => ({name, ...p}));
  }

  // 合并 stats 和 health 拿真实 models / calls / latency / quality
  const statsDict = window._lastStats || {};
  const healthDict = (window._lastHealth && window._lastHealth.providers) || {};
  list = list.map(p => {
    const s = statsDict[p.name] || {};
    const h = healthDict[p.name] || {};
    return {
      ...p,
      models: p.models || p.model_count || h.models || 0,
      total_calls: p.total_calls || p.calls || s.total_calls || 0,
      avg_latency_ms: p.avg_latency_ms || s.avg_latency_ms || 0,
      quality_score: p.quality_score != null ? p.quality_score : s.quality_score,
      degraded: p.degraded || (s.fail_calls || 0) > 2,
      fail_count: p.fail_count || s.fail_calls || 0,
    };
  });

  document.getElementById('providerCount').textContent = list.length;
  
  if (list.length === 0) {
    grid.innerHTML = `<div class="empty-state">暂无 provider · <button class="btn sm" onclick="openAddProvider()">新增</button></div>`;
    return;
  }
  
  grid.innerHTML = list.slice(0, 12).map((p, i) => {
    const isEnabled = p.enabled !== false && !p.disabled;
    const isDegraded = p.degraded || (p.fail_count || 0) > 2;
    const dotClass = !isEnabled ? 'down' : isDegraded ? 'degraded' : 'ok';
    const cardClass = !isEnabled ? 'down' : isDegraded ? 'degraded' : '';
    const avgLat = p.avg_latency_ms ? (p.avg_latency_ms/1000).toFixed(1) + 's' : '—';
    const q = p.quality_score != null ? Number(p.quality_score).toFixed(1) : '—';
    const models = p.models || p.model_count || 0;
    const calls = p.total_calls || p.calls || 0;
    
    // sparkline: 用 quality_score + 一些 jitter 模拟
    const seed = (p.name || 'x').charCodeAt(0) + i;
    const sparkBars = Array.from({length: 12}, (_, j) => {
      const base = q !== '—' ? Number(q) : 60;
      const noise = Math.sin(seed + j) * 25;
      const h = Math.max(15, Math.min(95, base + noise));
      const color = isDegraded ? 'var(--warn)' : !isEnabled ? 'var(--danger)' : null;
      const style = color ? `height:${h}%;background:${color}` : `height:${h}%`;
      return `<div class="spark-bar" style="${style}"></div>`;
    }).join('');
    
    return `
      <div class="provider-card ${cardClass}">
        <div class="provider-head">
          <div class="provider-dot ${dotClass}"></div>
          <div class="provider-name">${escapeHtml(p.name)}</div>
          <div class="provider-models">${models} models</div>
        </div>
        <div class="provider-stats">
          <span><b>${avgLat}</b>avg</span>
          <span><b>${calls}</b>calls</span>
          <span><b>${q}</b>q</span>
        </div>
        <div class="provider-spark">${sparkBars}</div>
        <div class="provider-actions">
          <button class="btn ghost sm" onclick="openEditProvider('${escapeHtml(p.name)}')" title="编辑">⚙</button>
          <button class="btn ghost sm" onclick="refreshProvider('${escapeHtml(p.name)}')" title="刷新">↻</button>
          <button class="btn ghost sm" onclick="cloneProvider('${escapeHtml(p.name)}')" title="复制">⎘</button>
          ${isEnabled 
            ? `<button class="btn ghost sm" onclick="disableProvider('${escapeHtml(p.name)}')" title="停用">⏸</button>`
            : `<button class="btn success sm" onclick="reEnableProvider('${escapeHtml(p.name)}')" title="启用">▶</button>`}
        </div>
      </div>
    `;
  }).join('');
}

function renderModels(models) {
  const rows = document.getElementById('modelRows');
  if (!rows) return;
  
  let list = [];
  if (Array.isArray(models)) list = models;
  else if (models.models) list = models.models;
  else if (models.data) list = models.data;
  
  document.getElementById('modelCount').textContent = list.length;
  
  if (list.length === 0) {
    rows.innerHTML = `<div class="empty-state" style="grid-column:1/-1">暂无模型</div>`;
    return;
  }
  
  rows.innerHTML = list.slice(0, 20).map(m => {
    const pricing = m.pricing_detail || (m.is_free ? 'free' : (m.pricing || '—'));
    const isFree = m.is_free || pricing === 'free' || (typeof pricing === 'object' && pricing.prompt === '0');
    const pricingLabel = typeof pricing === 'string' ? pricing : (pricing.prompt != null ? `$${pricing.prompt}` : (isFree ? 'free' : '—'));
    
    const sizeClass = m.size_class || 'unknown';
    let sizeTag = 'size-s', sizeLabel = '—';
    if (sizeClass === '>200B') { sizeTag = 'size-xl'; sizeLabel = m.size_b ? `${m.size_b}B` : '>200B'; }
    else if (sizeClass === '70-200B') { sizeTag = 'size-l'; sizeLabel = m.size_b ? `${m.size_b}B` : '70-200B'; }
    else if (sizeClass === '13-70B') { sizeTag = 'size-m'; sizeLabel = m.size_b ? `${m.size_b}B` : '13-70B'; }
    else if (sizeClass === '<13B') { sizeTag = 'size-s'; sizeLabel = m.size_b ? `${m.size_b}B` : '<13B'; }
    else { sizeLabel = 'unknown'; }
    
    const score = m.capability_score || 0;
    const scoreClass = score >= 85 ? 'hi' : score >= 60 ? 'mid' : score > 0 ? 'lo' : 'lo';
    
    // 健康度 (mock from score for now)
    const healthClass = score >= 70 ? 'ok' : score >= 40 ? 'warn' : 'fail';
    
    return `
      <div class="models-row">
        <div class="model-id" title="${escapeHtml(m.id||'')}">${escapeHtml(m.id || '')}</div>
        <div style="color:var(--text-1);font-size:12px">${escapeHtml(m.provider || '')}</div>
        <div><span class="tag ${isFree?'free':'paid'}">${escapeHtml(pricingLabel)}</span></div>
        <div><span class="tag ${sizeTag}">${escapeHtml(sizeLabel)}</span></div>
        <div><span class="health-dot ${healthClass}"><span class="dot"></span>${score}</span></div>
        <div><span class="score ${scoreClass}">${score}</span></div>
      </div>
    `;
  }).join('');
}

async function loadActivity() {
  const el = document.getElementById('activityStream');
  if (!el) return;
  // SMR 暂未提供 activity endpoint, 用 mock + 真实 health 数据
  const mock = [
    {time: '12:34:21', status:'ok', route:'gpt-4o', provider:'openrouter · 1.8s', latency:'1.8s', cost:'$0.002'},
    {time: '12:34:18', status:'ok', route:'llama-3.1-70b', provider:'nvidia · 0.4s', latency:'0.4s', cost:'free'},
    {time: '12:34:15', status:'warn', route:'gpt-4o → fallback openrouter', provider:'newapi timeout · auto reroute', latency:'5.2s', cost:'$0.003'},
    {time: '12:34:11', status:'ok', route:'claude-3-sonnet', provider:'openrouter · 2.3s', latency:'2.3s', cost:'$0.015'},
    {time: '12:34:08', status:'ok', route:'qwen-2.5-72b', provider:'volc_ark · 1.2s', latency:'1.2s', cost:'$0.001'},
    {time: '12:34:02', status:'fail', route:'deepseek-v3', provider:'deepseek · 429 rate limited', latency:'2.1s', cost:'$0'},
    {time: '12:33:58', status:'ok', route:'gpt-4o-mini', provider:'openrouter · 0.8s', latency:'0.8s', cost:'$0.0001'},
    {time: '12:33:51', status:'ok', route:'llama-3.1-405b', provider:'nvidia · 1.6s', latency:'1.6s', cost:'free'},
  ];
  document.getElementById('activityCount').textContent = `${mock.length} recent`;
  el.innerHTML = mock.map(r => {
    const icon = r.status === 'ok' ? '✓' : r.status === 'warn' ? '↻' : '✗';
    const cls = `status-icon ${r.status}`;
    return `
      <div class="activity-row">
        <span class="activity-time">${r.time}</span>
        <span class="activity-status"><span class="${cls}">${icon}</span></span>
        <span class="activity-route">${escapeHtml(r.route)}</span>
        <span class="activity-provider">${escapeHtml(r.provider)}</span>
        <span class="activity-latency">${r.latency}</span>
        <span class="activity-cost">${r.cost}</span>
      </div>
    `;
  }).join('');
}

// ===== 操作函数 (占位 - 真实集成下版) =====
function refreshAll() { loadAll(); toast('info', '刷新中', '正在加载最新数据'); }
function probeHealthAll() { toast('warn', 'Probe 启动', 'v3.28 Probe 已集成'); }
function openLogs() { toast('info', '日志面板', 'v3.28 待集成'); }
function openSettings() { toast('info', '设置面板', 'v3.28 待集成'); }
function exportReport() { toast('success', '导出报告', 'v3.28 待集成'); }
function backupConfig() { toast('success', '备份配置', 'v3.28 待集成'); }
function exportActivity() { toast('success', '导出 CSV', 'v3.28 待集成'); }
function viewAllActivity() { toast('info', '查看全部', 'v3.28 待集成'); }
function enableAllProviders() { toast('warn', '全部启用', 'v3.28 待集成'); }
function refreshAllProviders() { toast('warn', '全部刷新', 'v3.28 待集成'); }
function openAddProvider() { toast('info', '新增 Provider', 'Wizard 已集成, 见 📖 使用指引'); }
function openWizard() { 
  const m = document.getElementById('wizardModal');
  if (m) m.classList.add('active');
  toast('info', 'Wizard 打开', 'Wizard 已集成, 见 📖 使用指引');
}
function closeWizard() {
  const m = document.getElementById('wizardModal');
  if (m) m.classList.remove('active');
}
function openEditProvider(name) { toast('info', '编辑 Provider', name); }
function refreshProvider(name) { toast('info', '刷新 Provider', name); }
function cloneProvider(name) { toast('info', '复制 Provider', name); }
function disableProvider(name) { toast('warn', '停用 Provider', name); }
function reEnableProvider(name) { toast('success', '启用 Provider', name); }
function filterByProvider() {
  const provider = prompt("请输入提供商名称（多个用逗号分隔，留空表示全部）:");
  if (provider === null) return;
  const params = new URLSearchParams();
  if (provider.trim() !== '') {
    params.append('providers', provider.trim());
  }
  fetchJSON('/v1/admin/models/filter?' + params.toString())
    .then(data => {
      if (data && data.models) {
        renderModels(data.models);
        document.getElementById('modelCount').textContent = data.total || data.models.length;
      } else {
        toast('error', '返回数据格式错误');
      }
    })
    .catch(err => {
      console.error(err);
      toast('error', '过滤失败: ' + err);
    });
}

function filterBySize() {
  const minStr = prompt("请输入最小参数量（十亿参数，例如 7 表示 7B，留空表示无下限）:");
  if (minStr === null) return;
  const maxStr = prompt("请输入最大参数量（十亿参数，留空表示无上限）:");
  if (maxStr === null) return;
  const params = new URLSearchParams();
  if (minStr.trim() !== '') {
    params.append('size_min', parseFloat(minStr.trim()));
  }
  if (maxStr.trim() !== '') {
    params.append('size_max', parseFloat(maxStr.trim()));
  }
  fetchJSON('/v1/admin/models/filter?' + params.toString())
    .then(data => {
      if (data && data.models) {
        renderModels(data.models);
        document.getElementById('modelCount').textContent = data.total || data.models.length;
      } else {
        toast('error', '返回数据格式错误');
      }
    })
    .catch(err => {
      console.error(err);
      toast('error', '过滤失败: ' + err);
    });
}

function filterByCapability() {
  const minStr = prompt("请输入最低能力分数（0-100）:");
  if (minStr === null) return;
  const params = new URLSearchParams();
  if (minStr.trim() !== '') {
    params.append('capability_min', parseFloat(minStr.trim()));
  }
  fetchJSON('/v1/admin/models/filter?' + params.toString())
    .then(data => {
      if (data && data.models) {
        renderModels(data.models);
        document.getElementById('modelCount').textContent = data.total || data.models.length;
      } else {
        toast('error', '返回数据格式错误');
      }
    })
    .catch(err => {
      console.error(err);
      toast('error', '过滤失败: ' + err);
    });
}

function filterByPrice() {
  const price = prompt("请输入价格类型（例如 free 表示免费，留空表示所有）:");
  if (price === null) return;
  const priceLower = price.trim().toLowerCase();
  // 使用已缓存的完整模型列表进行客户端过滤
  const models = window._lastModels || [];
  let filtered = models;
  if (priceLower) {
    filtered = models.filter(m => {
      const p = (m.pricing || '').toLowerCase();
      return price === 'free' ? p === 'free' : p !== 'free';
    });
  }
  renderModels(filtered);
  document.getElementById('modelCount').textContent = filtered.length;
}
function onGlobalSearch(q) { /* TODO: v3.27 集成搜索 */ }

// ===== Wizard (v3.27 完整迁移) =====
let wizardState = {
  presets: [],
  matchedModels: [],
  selectedPreset: null,
  selectedPaths: new Set(),
  currentFilter: null,
};

function toggleChip(el) {
  el.classList.toggle('selected');
}

async function loadWizard() {
  wizardState.selectedPreset = null;
  wizardState.selectedPaths = new Set();
  try {
    const r = await fetch('/v1/admin/model-groups/wizard/presets');
    const data = await r.json();
    if (data.error) { toast('error', '加载预设失败', data.error); return; }
    wizardState.presets = data.presets || [];
    renderPresetCards();
    fillWizardFilterOptions();
  } catch (e) {
    toast('error', 'wizard API 失败', e.message);
  }
  document.getElementById('wizardModelsList').innerHTML =
    '<div class="empty-state">👆 选一个预设场景 或 自定义筛选查看匹配模型</div>';
  document.getElementById('wizardMatchCount').textContent = '0';
  if (!document.getElementById('wizardGroupName').value) {
    document.getElementById('wizardGroupName').value = 'my-group-' + Date.now().toString(36);
  }
}

function renderPresetCards() {
  const grid = document.getElementById('wizardPresetsGrid');
  grid.innerHTML = '';
  for (const p of wizardState.presets) {
    const card = document.createElement('div');
    card.className = 'wizard-preset-card';
    card.dataset.presetId = p.id;
    if (p.current_match_count === 0) card.classList.add('disabled');
    const countClass = p.current_match_count === 0 ? 'preset-count zero' : 'preset-count';
    card.innerHTML = `
      <div class="preset-icon">${p.icon || '🎯'}</div>
      <div class="preset-name">${escapeHtml(p.name || p.id)}</div>
      <div class="preset-desc">${escapeHtml(p.description || '')}</div>
      <div class="${countClass}">${p.current_match_count || 0} 个模型</div>
    `;
    card.onclick = () => {
      if (card.classList.contains('disabled')) return;
      document.querySelectorAll('.wizard-preset-card').forEach(c => c.classList.remove('selected'));
      card.classList.add('selected');
      wizardState.selectedPreset = p;
      applyWizardPreset(p);
    };
    grid.appendChild(card);
  }
}

function fillWizardFilterOptions() {
  // Provider 列表从 health endpoint 拿真实 provider name (避免 [object Object])
  const providers = new Set();
  const healthDict = (window._lastHealth && window._lastHealth.providers) || {};
  Object.keys(healthDict).forEach(name => providers.add(name));
  // fallback: 从 _lastProviders 拿
  if (providers.size === 0 && window._lastProviders) {
    const list = Array.isArray(window._lastProviders) ? window._lastProviders : (window._lastProviders.providers || []);
    list.forEach(p => providers.add(typeof p === 'string' ? p : p.name));
  }
  const provEl = document.getElementById('wizardFilterProviders');
  provEl.innerHTML = [...providers].map(p => `<span class="chip" data-value="${escapeHtml(p)}" onclick="toggleChip(this)">${escapeHtml(p)}</span>`).join('') || '<span style="color:var(--text-3);font-size:11px">无 provider</span>';
  // Tags (固定)
  const tags = ['reasoning', 'coding', 'vision', 'fast', 'long-context', 'tools', 'multimodal'];
  document.getElementById('wizardFilterTags').innerHTML = tags.map(t => `<span class="chip" data-value="${t}" onclick="toggleChip(this)">${t}</span>`).join('');
}

async function applyWizardPreset(p) {
  // 用 preset.id 调 from-wizard dry-run (API 字段: preset / resolved_models)
  try {
    const r = await fetch('/v1/admin/model-groups/from-wizard', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({preset: p.id, dry_run: true}),
    });
    const data = await r.json();
    if (data.error) { toast('error', 'preset 应用失败', data.error); return; }
    // 兼容 v3.25 字段: resolved_models → matched_models (前端统一)
    wizardState.matchedModels = (data.resolved_models || data.matched_models || []).map(m => ({
      id: m.model_id || m.id,
      path: m.path || `${m.provider || ''}/${m.model_id || m.id}`,
      provider: m.provider || '',
    }));
    wizardState.currentFilter = data.filter || null;
    renderWizardModels();
    toast('success', `预设已应用`, `${wizardState.matchedModels.length} 个匹配模型`);
  } catch (e) {
    toast('error', 'preset API 失败', e.message);
  }
}

async function applyWizardFilter() {
  const providers = [...document.querySelectorAll('#wizardFilterProviders .chip.selected')].map(c => c.dataset.value);
  const context = parseInt(document.getElementById('wizardFilterContext').value) || 0;
  const quality = parseInt(document.getElementById('wizardFilterQuality').value) || 0;
  const speed = parseInt(document.getElementById('wizardFilterSpeed').value) || 0;
  const modality = document.getElementById('wizardFilterModality').value || '';
  const tags = [...document.querySelectorAll('#wizardFilterTags .chip.selected')].map(c => c.dataset.value);

  const filter = {
    providers: providers.length ? providers : null,
    context_min: context || null,
    quality_min: quality || null,
    speed_min: speed || null,
    modality: modality || null,
    tags: tags.length ? tags : null,
  };
  wizardState.currentFilter = filter;
  try {
    const r = await fetch('/v1/admin/model-groups/from-filter', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...filter, dry_run: true}),
    });
    const data = await r.json();
    if (data.error) { toast('error', '筛选失败', data.error); return; }
    // 兼容 v3.25 字段: resolved_models → matched_models (前端统一)
    wizardState.matchedModels = (data.resolved_models || data.matched_models || []).map(m => ({
      id: m.model_id || m.id,
      path: m.path || `${m.provider || ''}/${m.model_id || m.id}`,
      provider: m.provider || '',
    }));
    renderWizardModels();
    toast('success', '筛选已应用', `${wizardState.matchedModels.length} 个匹配`);
  } catch (e) {
    toast('error', 'filter API 失败', e.message);
  }
}

function resetWizardFilter() {
  document.querySelectorAll('#wizardFilterProviders .chip, #wizardFilterTags .chip').forEach(c => c.classList.remove('selected'));
  document.getElementById('wizardFilterContext').value = '0';
  document.getElementById('wizardFilterQuality').value = '0';
  document.getElementById('wizardFilterSpeed').value = '0';
  document.getElementById('wizardFilterModality').value = '';
  document.getElementById('qualityVal').textContent = '0';
  document.getElementById('speedVal').textContent = '0';
  document.querySelectorAll('.wizard-preset-card').forEach(c => c.classList.remove('selected'));
  wizardState.selectedPreset = null;
  wizardState.matchedModels = [];
  wizardState.currentFilter = null;
  document.getElementById('wizardModelsList').innerHTML = '<div class="empty-state">👆 选一个预设场景 或 自定义筛选查看匹配模型</div>';
  document.getElementById('wizardMatchCount').textContent = '0';
  toast('info', '筛选已重置');
}

function renderWizardModels() {
  const list = wizardState.matchedModels;
  document.getElementById('wizardMatchCount').textContent = list.length;
  if (list.length === 0) {
    document.getElementById('wizardModelsList').innerHTML = '<div class="empty-state">无匹配模型</div>';
    return;
  }
  document.getElementById('wizardModelsList').innerHTML = list.map(m => {
    const checked = wizardState.selectedPaths.has(m.path) ? 'checked' : '';
    return `<label class="model-row">
      <input type="checkbox" ${checked} onchange="toggleWizardPath('${escapeHtml(m.path)}', this.checked)">
      <span style="flex:1;font-family:var(--mono);font-size:11px">${escapeHtml(m.id || m.path)}</span>
      <span style="color:var(--text-2);font-size:10px">${escapeHtml(m.provider || '')}</span>
    </label>`;
  }).join('');
}

function toggleWizardPath(path, checked) {
  if (checked) wizardState.selectedPaths.add(path);
  else wizardState.selectedPaths.delete(path);
}

function wizardSelectAll() {
  wizardState.matchedModels.forEach(m => wizardState.selectedPaths.add(m.path));
  renderWizardModels();
  toast('info', `已全选 ${wizardState.matchedModels.length} 个`);
}

function wizardSelectNone() {
  wizardState.selectedPaths.clear();
  renderWizardModels();
  toast('info', '已清空选择');
}

async function previewWizardGroup() {
  if (!wizardState.currentFilter) {
    toast('warn', '请先应用筛选或选预设');
    return;
  }
  toast('info', '预览生成', `${wizardState.matchedModels.length} 个匹配 / ${wizardState.selectedPaths.size} 个已选`);
}

async function generateWizardGroup() {
  const groupName = document.getElementById('wizardGroupName').value.trim();
  if (!groupName) { toast('warn', '请填分组名'); return; }
  if (wizardState.selectedPaths.size === 0) { toast('warn', '请至少选一个模型'); return; }

  const payload = {
    name: groupName,
    strategy: document.getElementById('wizardGroupStrategy').value,
    paths: [...wizardState.selectedPaths],
    filter: wizardState.currentFilter,
    create_api_key: document.getElementById('wizardCreateApiKey').checked,
    api_key_name: document.getElementById('wizardApiKeyName').value.trim() || (groupName + '-key'),
  };

  try {
    const r = await fetch('/v1/admin/model-groups/from-filter', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const data = await r.json();
    if (data.error) { toast('error', '生成失败', data.error); return; }
    showWizardResult(data);
    toast('success', '分组生成成功', groupName);
  } catch (e) {
    toast('error', 'API 失败', e.message);
  }
}

function showWizardResult(data) {
  const panel = document.getElementById('wizardResultPanel');
  const content = document.getElementById('wizardResultContent');
  let html = `<div style="font-family:var(--mono);font-size:12px;color:var(--text-0)">
    <div><b>分组名:</b> ${escapeHtml(data.name || '')}</div>
    <div><b>匹配数:</b> ${data.matched_count || wizardState.matchedModels.length}</div>
    <div><b>策略:</b> ${escapeHtml(data.strategy || '')}</div>
    ${data.description ? `<div style="margin-top:8px;color:var(--text-1)">${escapeHtml(data.description)}</div>` : ''}
  </div>`;
  if (data.api_key) {
    html += `<div style="margin-top:var(--space-3);padding-top:var(--space-3);border-top:1px solid var(--success)">
      <strong style="color:var(--success)">🔑 API Key (仅显示这一次!)</strong>
      <div style="margin-top:6px;padding:var(--space-2);background:var(--bg-0);border-radius:var(--radius-sm);font-family:var(--mono);font-size:12px;word-break:break-all;color:var(--warn)">
        ${escapeHtml(data.api_key.key)}
      </div>
      <div style="margin-top:6px;font-size:11px;color:var(--text-2)">name: <code>${escapeHtml(data.api_key.name)}</code> · hash: <code>${escapeHtml(data.api_key.key_hash || '')}</code></div>
    </div>`;
  }
  content.innerHTML = html;
  panel.style.display = 'block';
  panel.scrollIntoView({behavior: 'smooth', block: 'nearest'});
}

// ===== 启动 =====
applyTheme();

// 调试: URL ?theme=dark|light|system 切换
(function(){
  const t = new URLSearchParams(location.search).get('theme');
  if (t && THEME_ORDER.includes(t)) {
    localStorage.setItem(THEME_KEY, t);
    applyTheme(t);
  }
})();

// 监听系统主题变化
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (getTheme() === 'system') applyTheme('system');
});

document.addEventListener('DOMContentLoaded', loadAll);

// 30s 自动刷新 + 倒计时
let _refreshCountdown = 30;
setInterval(() => {
  const next = document.getElementById('nextRefresh');
  if (next) {
    _refreshCountdown = _refreshCountdown > 0 ? _refreshCountdown - 5 : 30;
    next.textContent = _refreshCountdown + 's';
  }
}, 5000);
setInterval(() => { loadAll(); _refreshCountdown = 30; }, 30000);

// 全局快捷键
document.addEventListener('keydown', (e) => {
  // ⌘K / Ctrl+K: 全局搜索
  if ((e.metaKey || e.ctrlKey) && e.key === 'k' && !e.shiftKey) {
    e.preventDefault();
    const s = document.getElementById('globalSearch');
    if (s) s.focus();
  }
  // Ctrl+Shift+L: 主题切换
  if ((e.metaKey || e.ctrlKey) && e.shiftKey && (e.key === 'l' || e.key === 'L')) {
    e.preventDefault();
    cycleTheme();
  }
  // ESC: 关闭所有 modal
  if (e.key === 'Escape') { closeWizard(); closeProviderKeys(); closePublicKeys(); }
});
// ===== v3.28.1 Provider/Public Key 前端桥接 (BEGIN) =====
let showFullProviderKeys = false;
let showFullPublicKeys = false;
// 会话级缓存: 本次页面加载后新建的 Public Key 原文 (刷新即丢, 旧 key 无法恢复 — 安全设计)
const pubKeyCache = new Map();

function copyToClipboard(text) {
  const done = () => toast('success', '已复制', 'Key 在剪贴板');
  const fail = () => toast('warn', '复制失败', '请手动选中');
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done, fail);
  } else {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); done(); } catch { fail(); }
    document.body.removeChild(ta);
  }
}

function openProviderKeys()  { document.getElementById('providerKeysModal').style.display = 'flex'; loadProviderKeys(); }
function closeProviderKeys() { document.getElementById('providerKeysModal').style.display = 'none'; }
function openPublicKeys()    { document.getElementById('publicKeysModal').style.display = 'flex'; loadPublicKeys(); }
function closePublicKeys()   {
  document.getElementById('publicKeysModal').style.display = 'none';
  const disp = document.getElementById('ppkNewKeyDisplay');
  if (disp) disp.style.display = 'none';
  // B1: 关闭时重置 toggle 状态和按钮文字, 与 Provider Key modal 行为对齐
  showFullPublicKeys = false;
  const btn = document.getElementById('pubKeyToggleBtn');
  if (btn) btn.textContent = '👁️ 显示完整';
}

async function toggleProviderKeyVisibility() {
  showFullProviderKeys = !showFullProviderKeys;
  const btn = document.getElementById('pkToggleBtn');
  if (btn) btn.textContent = showFullProviderKeys ? '🙈 隐藏' : '👁️ 显示完整';
  await loadProviderKeys();
}

async function togglePublicKeyVisibility() {
  showFullPublicKeys = !showFullPublicKeys;
  const btn = document.getElementById('pubKeyToggleBtn');
  if (btn) btn.textContent = showFullPublicKeys ? '🙈 隐藏' : '👁️ 显示完整';
  await loadPublicKeys();
}

async function loadProviderKeys() {
  const list = document.getElementById('providerKeysList');
  list.innerHTML = '<div class="empty-state">⏳ 加载中…</div>';
  const [keysData, provData] = await Promise.all([
    fetchJSON('/v1/admin/api-keys' + (showFullProviderKeys ? '?show_full_keys=true' : '')),
    fetchJSON('/v1/admin/providers?include_disabled=true'),
  ]);
  const sel = document.getElementById('pkProviderSel');
  sel.innerHTML = '<option value="">选择 provider…</option>';
  const provs = provData?.providers || {};
  Object.keys(provs).sort().forEach(name => {
    sel.innerHTML += `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`;
  });
  if (!keysData?.keys?.length) {
    list.innerHTML = '<div class="empty-state">📭 无 Provider Key</div>';
    return;
  }
  let html = '<table class="data-table" style="width:100%;font-size:13px"><thead><tr><th>Provider</th><th>数</th><th>预览</th><th>指纹</th><th></th></tr></thead><tbody>';
  keysData.keys.forEach(k => {
    const prev = (k.preview || []).map(p => {
      const safe = escapeHtml(p);
      if (showFullProviderKeys) {
        return `<code style="font-size:11px;word-break:break-all;cursor:pointer" onclick="copyToClipboard(this.textContent)" title="点击复制完整 key">${safe}</code>`;
      }
      return `<code style="font-size:11px">${safe}</code>`;
    }).join('<br>') || '—';
    const fp = k.fingerprint ? `<code style="font-size:11px">${escapeHtml(k.fingerprint.slice(0, 12))}…</code>` : '—';
    html += `<tr><td><b>${escapeHtml(k.provider)}</b>${k.enabled === false ? ' ⏸' : ''}</td><td>${k.count}</td><td>${prev}</td><td>${fp}</td><td><button class="btn ghost sm" onclick="deleteProviderKey('${escapeHtml(k.provider)}')">🗑 清空</button></td></tr>`;
  });
  list.innerHTML = html + '</tbody></table>';
}

async function addProviderKey() {
  const provider = document.getElementById('pkProviderSel').value.trim();
  const apiKey   = document.getElementById('pkNewKey').value.trim();
  if (!provider || !apiKey) return toast('warn', '缺少信息', 'Provider + Key 都要填');
  try {
    const r = await fetch(BASE + '/v1/admin/api-keys', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({provider, api_key: apiKey}),
      signal: AbortSignal.timeout(10000),
    });
    const data = await r.json();
    if (data.error) return toast('error', '添加失败', data.error);
    toast('success', '已添加', `${provider} 现有 ${data.count ?? '?'} 个 key`);
    document.getElementById('pkNewKey').value = '';
    loadProviderKeys();
  } catch (e) { toast('error', '网络错误', e.message); }
}

async function deleteProviderKey(provider) {
  if (!confirm(`清空 ${provider} 的全部 key? (不可恢复)`)) return;
  try {
    const r = await fetch(`${BASE}/v1/admin/api-keys/${encodeURIComponent(provider)}`, {method: 'DELETE', signal: AbortSignal.timeout(10000)});
    const data = await r.json();
    if (data.error) return toast('error', '删除失败', data.error);
    toast('success', '已清空', provider);
    loadProviderKeys();
  } catch (e) { toast('error', '网络错误', e.message); }
}

async function loadPublicKeys() {
  const list = document.getElementById('publicKeysList');
  list.innerHTML = '<div class="empty-state">⏳ 加载中…</div>';
  const data = await fetchJSON('/v1/admin/public-keys');
  if (!data?.keys?.length) {
    list.innerHTML = '<div class="empty-state">📭 无 Public Key</div>';
    return;
  }
  const headLabel = showFullPublicKeys ? '完整 Key / 哈希' : '哈希';
  let html = `<table class="data-table" style="width:100%;font-size:13px"><thead><tr><th>名称</th><th>${headLabel}</th><th>RPM</th><th>过滤</th><th>末次</th><th></th></tr></thead><tbody>`;
  data.keys.forEach(k => {
    const hashShort = k.key_hash ? escapeHtml(k.key_hash.slice(0, 16)) + '…' : '—';
    let keyCell;
    if (showFullPublicKeys) {
      const raw = k.key_hash ? pubKeyCache.get(k.key_hash) : null;
      if (raw) {
        // 命中会话缓存: 显示原文 + 点击复制 (走 textContent, 无 XSS 面)
        keyCell = `<code style="font-size:11px;word-break:break-all;cursor:pointer;color:var(--success)" onclick="copyToClipboard(this.textContent)" title="点击复制完整 key"></code>`;
      } else {
        // 未命中: 页面刷新前的旧 key 无法恢复 (安全设计)
        keyCell = `<code style="font-size:11px">${hashShort}</code> <span style="font-size:10px;color:var(--warn)" title="Public Key 仅创建时返回一次, 本会话之前建的无法恢复; 需要看完整值请重新生成">⚠️ 已丢失</span>`;
      }
    } else {
      keyCell = `<code style="font-size:11px">${hashShort}</code>`;
    }
    const filter = Array.isArray(k.model_filter) && k.model_filter.length
      ? (k.model_filter.length > 3 ? k.model_filter.slice(0,3).join(', ') + '…' : k.model_filter.join(', '))
      : '全部';
    const last = k.last_used ? new Date(k.last_used * 1000).toLocaleString() : '—';
    html += `<tr>
      <td><b>${escapeHtml(k.name)}</b>${k.enabled === false ? ' ⏸' : ''}</td>
      <td>${keyCell}</td>
      <td>${k.rate_limit_rpm ?? 60}</td>
      <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px" title="${escapeHtml(filter)}">${escapeHtml(filter)}</td>
      <td style="font-size:11px">${escapeHtml(last)}</td>
      <td><button class="btn ghost sm" onclick="deletePublicKey('${escapeHtml(k.name)}')">🗑</button></td>
    </tr>`;
  });
  // 先注入 HTML, 再同步用 textContent 填充原文 (避免 setTimeout 竞态)
  list.innerHTML = html + '</tbody></table>';
  if (showFullPublicKeys) {
    const rows = list.querySelectorAll('tbody tr');
    data.keys.forEach((k, idx) => {
      const raw = k.key_hash ? pubKeyCache.get(k.key_hash) : null;
      if (raw && rows[idx]) {
        const codeEl = rows[idx].querySelector('td:nth-child(2) code');
        if (codeEl) codeEl.textContent = raw;
      }
    });
  }
}

async function createPublicKey() {
  const name = document.getElementById('ppkName').value.trim();
  const rpm  = parseInt(document.getElementById('ppkRpm').value) || 60;
  const filterRaw = document.getElementById('ppkFilter').value.trim();
  if (!name) return toast('warn', '缺少名称', 'Name 必填');
  if (!/^[a-zA-Z0-9_-]+$/.test(name)) return toast('warn', '格式错误', '仅字母/数字/下划线/连字符');
  const body = { name, rate_limit_rpm: rpm };
  if (filterRaw) body.model_filter = filterRaw.split(',').map(s => s.trim()).filter(Boolean);
  try {
    const r = await fetch(BASE + '/v1/admin/public-keys', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(10000),
    });
    const data = await r.json();
    if (data.error) return toast('error', '创建失败', data.error);
    if (data.key) {
      document.getElementById('ppkNewKeyValue').textContent = data.key;
      document.getElementById('ppkNewKeyDisplay').style.display = 'block';
      // B1: 本会话内缓存原始 key, 供 👁️ toggle 时回显 (刷新即丢)
      if (data.key_hash) pubKeyCache.set(data.key_hash, data.key);
      toast('warn', '⚠️ 仅此一次', `${name} — 请立即复制保存`);
    } else {
      toast('success', '已创建', name);
    }
    document.getElementById('ppkName').value = '';
    document.getElementById('ppkRpm').value = '60';
    document.getElementById('ppkFilter').value = '';
    loadPublicKeys();
  } catch (e) { toast('error', '网络错误', e.message); }
}

async function deletePublicKey(name) {
  if (!confirm(`永久删除 Key "${name}"? (不可恢复, 立即失效)`)) return;
  try {
    const r = await fetch(`${BASE}/v1/admin/public-keys/${encodeURIComponent(name)}`, {method: 'DELETE', signal: AbortSignal.timeout(10000)});
    const data = await r.json();
    if (data.error) return toast('error', '删除失败', data.error);
    toast('success', '已删除', name);
    loadPublicKeys();
  } catch (e) { toast('error', '网络错误', e.message); }
}
// ===== v3.28.1 Provider/Public Key 前端桥接 (END) =====

</script>

</body>
</html>"""


# ============================================================
# Admin Page 路由
# ============================================================

@router.get("/admin", response_class=HTMLResponse)
@router.get("/admin/", response_class=HTMLResponse)
async def admin_page():
    """v3.28: 现代 dashboard + dark/light 主题切换 + guide 页"""
    from .version import VERSION as _V
    return HTMLResponse(content=ADMIN_HTML.replace("__SMR_VERSION__", _V))


@router.get("/admin/9-gong", response_class=HTMLResponse)
async def admin_9gong():
    """v3.11 集成 v0.9: 派活 dashboard 8 卦布局 (戴九履一) + 12 时辰火候

    来源: vault/05-practical/03-dispatch-dashboard-v09-九宫布局-12时辰火候-2026-06-21.html
    蒸馏精华: 体 (8 卦) + 用 (1-9) + 时 (12 时辰) = SMR 算法灵魂
    """
    from pathlib import Path as P
    dashboard_path = P(__file__).parent / "static" / "dashboard-9gong.html"
    if not dashboard_path.exists():
        return HTMLResponse("<h1>8 卦 dashboard HTML 缺</h1><p>需要复制到 static/dashboard-9gong.html</p>", status_code=500)
    return HTMLResponse(dashboard_path.read_text(encoding='utf-8'))


@router.get("/admin/guide", response_class=HTMLResponse)
async def admin_guide_page():
    """v3.28 增量: SMR Admin 使用指引页 (老大 2026-07-04 钦定)

    来源: 让伊芙整体 review 一次 SMR → 写使用指引页 → 集成进 admin UI
    内容: 10 段 (概览/快速开始/Dashboard/Wizard/常用/高级/FAQ/故障/快捷键/版本)
    基于: v3.27.0 真实状态, 标注 stub 按钮
    """
    from .version import VERSION as _V
    from .admin_ui_guide import GUIDE_HTML
    return HTMLResponse(content=GUIDE_HTML.replace("__SMR_VERSION__", _V))