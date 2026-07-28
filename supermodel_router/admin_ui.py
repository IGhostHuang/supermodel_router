"""
supermodel_router/admin_ui.py - v4.1.0 5-Tab Navigation + Fusion Panel + Config Consolidation

- 5 Tab 导航: Overview / Models / Providers / Fusion / Access
- Fusion 面板: plan 模板 + JSON 编辑器 + 测试运行 + 删除
- 配置固化: localStorage 记忆 active tab / theme / filter state
- 保留: wizard modal, provider keys modal, public keys modal, 9-gong route, guide route
- 兼容: 所有现有后端端点

后续:
- v4.1: 5 tab 重构 + Fusion 面板集成
- v3.28: wizard DOM 完整迁移 + 参数量 badge + filterSizes 集成
"""
import logging
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

LOG = logging.getLogger("admin_ui")
router = APIRouter()


# ============================================================
# v4.1.0 ADMIN_HTML - 5-Tab Dashboard + Fusion Panel
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
<title>SuperModel Router v4.1.0</title>
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
.topnav{display:flex;align-items:center;gap:var(--space-4);padding:var(--space-3) var(--space-5);background:var(--bg-1);border:1px solid var(--border);border-radius:var(--radius-lg);box-shadow:var(--shadow-sm);margin-bottom:var(--space-4);backdrop-filter:blur(12px)}
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

/* ===== Tab Navigation (v4.1 新增) ===== */
.tabnav{display:flex;gap:4px;padding:4px;background:var(--bg-1);border:1px solid var(--border);border-radius:var(--radius-lg);margin-bottom:var(--space-5);box-shadow:var(--shadow-sm);overflow-x:auto}
.tab-btn{display:flex;align-items:center;gap:6px;padding:10px 20px;background:transparent;border:1px solid transparent;color:var(--text-1);border-radius:var(--radius);font-size:13px;font-weight:500;cursor:pointer;transition:.15s;font-family:inherit;white-space:nowrap}
.tab-btn:hover{background:var(--bg-2);color:var(--text-0)}
.tab-btn.active{background:var(--primary-glow);border-color:var(--primary);color:var(--primary);font-weight:600}
.tab-panel{display:none}
.tab-panel.active{display:block;animation:fadeIn .2s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}

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
.models-table{background:var(--bg-1);border:1px solid var(--border);border-radius:var(--radius-lg);overflow:hidden;margin-bottom:var(--space-5)}
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

/* ===== Pagination (v4.1 新增) ===== */
.pagination{display:flex;align-items:center;justify-content:center;gap:var(--space-4);padding:var(--space-4);color:var(--text-1);font-size:13px}
.pagination span{font-family:var(--mono);font-weight:500}

/* ===== Data Table (v4.1 新增) ===== */
.data-table{width:100%;border-collapse:collapse;font-size:13px}
.data-table th{text-align:left;padding:10px 12px;background:var(--bg-2);color:var(--text-2);font-size:11px;text-transform:uppercase;letter-spacing:.5px;font-weight:600;border-bottom:1px solid var(--border)}
.data-table td{padding:10px 12px;border-bottom:1px solid var(--border);color:var(--text-0)}
.data-table tr:last-child td{border-bottom:none}
.data-table tr:hover td{background:var(--bg-2)}

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

/* ===== Modal (通用) ===== */
.modal-overlay{position:fixed;inset:0;background:var(--overlay);z-index:500;display:none;align-items:center;justify-content:center;padding:20px;backdrop-filter:blur(4px)}
.modal-overlay.active{display:flex}
.modal-content{background:var(--bg-1);border:1px solid var(--border);border-radius:var(--radius-lg);max-width:900px;width:100%;max-height:90vh;overflow:auto;padding:var(--space-5);box-shadow:var(--shadow-lg)}
.modal-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-4)}
.modal-title{font-size:18px;font-weight:700}
.modal-close{background:transparent;border:none;color:var(--text-2);font-size:20px;cursor:pointer;padding:4px 8px;border-radius:var(--radius-sm)}
.modal-close:hover{background:var(--bg-2);color:var(--text-0)}

/* ===== Wizard ===== */
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

/* ===== Fusion (v4.1 新增) ===== */
.fusion-status{display:flex;gap:var(--space-6);padding:var(--space-4) var(--space-5);background:linear-gradient(135deg,var(--bg-1),var(--bg-2));border:1px solid var(--border);border-radius:var(--radius-lg);margin-bottom:var(--space-5);box-shadow:var(--shadow);flex-wrap:wrap;align-items:center}
.fusion-stat{display:flex;flex-direction:column;gap:2px}
.fusion-stat-label{font-size:11px;text-transform:uppercase;color:var(--text-2);letter-spacing:.5px;font-weight:600}
.fusion-stat-value{font-size:20px;font-weight:700;font-family:var(--mono)}
.fusion-templates{display:grid;grid-template-columns:repeat(4,1fr);gap:var(--space-3);margin-bottom:var(--space-5)}
.fusion-template-btn{padding:var(--space-4);background:var(--bg-2);border:1px solid var(--border);border-radius:var(--radius-lg);cursor:pointer;transition:.15s;text-align:center;position:relative;overflow:hidden}
.fusion-template-btn:hover{border-color:var(--primary);background:var(--bg-3);transform:translateY(-2px);box-shadow:var(--shadow)}
.fusion-template-btn::before{content:'';position:absolute;inset:0;background:radial-gradient(circle at 50% 0%,var(--purple-glow),transparent 70%);opacity:.5;pointer-events:none}
.fusion-template-icon{font-size:28px;margin-bottom:8px;position:relative}
.fusion-template-name{font-size:13px;font-weight:600;color:var(--text-0);position:relative}
.fusion-template-desc{font-size:11px;color:var(--text-2);margin-top:4px;position:relative;line-height:1.4}
.fusion-editor{width:100%;min-height:300px;background:var(--bg-0);border:1px solid var(--border);color:var(--text-0);padding:var(--space-4);border-radius:var(--radius);font-family:var(--mono);font-size:13px;resize:vertical;outline:none;transition:.15s;line-height:1.5}
.fusion-editor:focus{border-color:var(--primary);box-shadow:0 0 0 3px var(--primary-glow)}
.fusion-plans-table{background:var(--bg-1);border:1px solid var(--border);border-radius:var(--radius-lg);overflow:hidden;margin-bottom:var(--space-5)}
.fusion-plan-row{display:grid;grid-template-columns:1.5fr 1fr 2fr auto;gap:var(--space-3);padding:var(--space-3) var(--space-4);border-bottom:1px solid var(--border);font-size:13px;align-items:center;transition:.15s}
.fusion-plan-row:hover{background:var(--bg-2)}
.fusion-plan-row:last-child{border-bottom:none}
.fusion-test-answer{padding:var(--space-3);background:var(--bg-2);border-radius:var(--radius);font-size:13px;white-space:pre-wrap;max-height:300px;overflow-y:auto}
.fusion-test-trace{padding:var(--space-3);background:var(--bg-0);border-radius:var(--radius);font-size:11px;overflow-x:auto;font-family:var(--mono);color:var(--text-1);max-height:200px;overflow-y:auto}

/* ===== Access Table (v4.1 新增) ===== */
.access-table{background:var(--bg-1);border:1px solid var(--border);border-radius:var(--radius-lg);overflow:hidden;margin-bottom:var(--space-5)}

/* ===== Footer ===== */
.footer{text-align:center;padding:var(--space-5);color:var(--text-3);font-size:11px}

/* ===== 响应式 ===== */
@media (max-width:1024px){
  .kpi-grid{grid-template-columns:repeat(2,1fr)}
  .provider-grid{grid-template-columns:repeat(2,1fr)}
  .fusion-templates{grid-template-columns:repeat(2,1fr)}
}
@media (max-width:640px){
  body{padding:var(--space-3)}
  .topnav{flex-wrap:wrap;gap:var(--space-2)}
  .search{order:3;flex:1 1 100%;margin:var(--space-2) 0 0;max-width:none}
  .kpi-grid,.provider-grid{grid-template-columns:1fr}
  .fusion-templates{grid-template-columns:1fr}
  .status-banner{flex-direction:column;align-items:flex-start;gap:var(--space-3)}
  .status-banner .quick-actions{margin-left:0}
  .models-thead,.models-row{grid-template-columns:2fr 1fr 80px 60px;gap:var(--space-2)}
  .models-thead > div:nth-child(4),.models-thead > div:nth-child(6),
  .models-row > *:nth-child(4),.models-row > *:nth-child(6){display:none}
  .activity-row{grid-template-columns:60px 16px 1fr 60px;gap:var(--space-2)}
  .activity-provider,.activity-cost{display:none}
  .tabnav{flex-wrap:wrap}
  .tab-btn{flex:1 1 auto;justify-content:center;padding:8px 12px;font-size:12px}
  .fusion-plan-row{grid-template-columns:1fr auto;gap:var(--space-2)}
  .fusion-plan-row > div:nth-child(2),.fusion-plan-row > div:nth-child(3){display:none}
}
</style>
</head>
<body>

<!-- ===== Top Nav ===== -->
<nav class="topnav">
  <div class="brand">
    <div class="brand-logo">&#9889;</div>
    <span>SuperModel Router</span>
    <span class="brand-version" id="brandVersion">v4.1.0</span>
  </div>
  <div class="search">
    <span class="search-icon">&#128269;</span>
    <input id="globalSearch" placeholder="搜索模型、provider、路由规则..." oninput="onGlobalSearch(this.value)">
    <kbd>&#8984;K</kbd>
  </div>
  <div class="topnav-actions">
    <button class="btn-icon" onclick="openProviderKeys()" title="Provider Key 管理 (K)">&#128273;</button>
    <button class="btn-icon" onclick="openPublicKeys()" title="Public Key 管理 (P)">&#128275;</button>
    <button class="btn-icon" onclick="openWizard()" title="Wizard 智能分组 (W)">&#129497;</button>
    <button class="btn-icon" onclick="refreshAll()" title="刷新所有 (R)">&#8635;</button>
    <button class="btn-icon" onclick="probeHealthAll()" title="健康检查 (H)">&#9889;</button>
    <button class="btn-icon" id="themeToggle" onclick="cycleTheme()" title="主题切换 (Ctrl+Shift+L)">&#127769;</button>
  </div>
</nav>

<!-- ===== Tab Navigation (v4.1) ===== -->
<nav class="tabnav">
  <button class="tab-btn active" data-tab="overview" onclick="switchTab('overview')">&#127968; Overview</button>
  <button class="tab-btn" data-tab="models" onclick="switchTab('models')">&#128230; Models</button>
  <button class="tab-btn" data-tab="providers" onclick="switchTab('providers')">&#128268; Providers</button>
  <button class="tab-btn" data-tab="fusion" onclick="switchTab('fusion')">&#10024; Fusion</button>
  <button class="tab-btn" data-tab="access" onclick="switchTab('access')">&#128273; Access</button>
</nav>

<!-- ===== Tab Panel: Overview ===== -->
<div class="tab-panel active" id="tab-overview">
  <div class="status-banner">
    <div class="status-dot" id="statusDot"></div>
    <div style="flex:1;min-width:240px">
      <div class="status-text" id="statusText">&#9203; 加载中...</div>
      <div class="status-meta" id="statusMeta"></div>
    </div>
    <div class="quick-actions">
      <button class="btn ghost" onclick="exportReport()">&#128202; 导出</button>
      <button class="btn ghost" onclick="backupConfig()">&#128230; 备份</button>
      <button class="btn primary" onclick="refreshAll()">&#8635; 刷新</button>
      <button class="btn success" onclick="probeHealthAll()">&#9889; Probe</button>
    </div>
  </div>

  <div class="kpi-grid">
    <div class="kpi-card blue">
      <div class="kpi-label">今日调用</div>
      <div class="kpi-value" id="kpiTodayCalls"><span class="skeleton" style="display:inline-block;width:80px;height:32px;vertical-align:middle"></span></div>
      <div class="kpi-delta" id="kpiTodayCallsDelta"></div>
    </div>
    <div class="kpi-card green">
      <div class="kpi-label">成功率</div>
      <div class="kpi-value" id="kpiSuccessRate">&mdash;</div>
      <div class="kpi-delta" id="kpiSuccessRateDelta"></div>
    </div>
    <div class="kpi-card amber">
      <div class="kpi-label">平均延迟</div>
      <div class="kpi-value" id="kpiAvgLatency">&mdash;</div>
      <div class="kpi-delta" id="kpiAvgLatencyDelta"></div>
    </div>
    <div class="kpi-card purple">
      <div class="kpi-label">免费路由</div>
      <div class="kpi-value" id="kpiFreeCalls">&mdash;</div>
      <div class="kpi-delta" id="kpiFreeCallsDelta"></div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Providers <span class="count" id="providerCountOverview">&mdash;</span></div>
    <div class="section-actions">
      <button class="btn ghost sm" onclick="switchTab('providers')">查看全部 &rarr;</button>
    </div>
  </div>
  <div class="provider-grid" id="providerGrid">
    <div class="provider-card"><div class="skeleton" style="height:120px"></div></div>
    <div class="provider-card"><div class="skeleton" style="height:120px"></div></div>
    <div class="provider-card"><div class="skeleton" style="height:120px"></div></div>
  </div>

  <div class="section">
    <div class="section-title">Activity Stream <span class="count" id="activityCount">recent</span></div>
    <div class="section-actions">
      <button class="btn ghost sm" onclick="exportActivity()">导出 CSV</button>
    </div>
  </div>
  <div class="activity" id="activityStream">
    <div class="empty-state">&#9203; 加载活动流...</div>
  </div>
</div>

<!-- ===== Tab Panel: Models ===== -->
<div class="tab-panel" id="tab-models">
  <div class="section">
    <div class="section-title">&#128230; Models <span class="count" id="modelCount">&mdash;</span></div>
    <div class="section-actions">
      <input type="text" id="modelSearch" placeholder="搜索模型..." class="filter-input" style="width:200px" oninput="onModelSearch(this.value)">
      <button class="btn ghost sm" onclick="filterModelsByProvider()">Provider</button>
      <button class="btn ghost sm" onclick="filterModelsBySize()">参数量</button>
      <button class="btn ghost sm" onclick="filterModelsByCapability()">能力</button>
      <button class="btn ghost sm" onclick="filterModelsByPrice()">价格</button>
      <button class="btn ghost sm" onclick="resetModelFilters()">重置</button>
      <button class="btn primary sm" onclick="loadModels()">&#8635; 刷新</button>
      <button class="btn primary sm" onclick="openWizard()">&#129497; Wizard</button>
    </div>
  </div>
  <div class="models-table">
    <div class="models-thead">
      <div>Model</div><div>Provider</div><div>Price</div><div>Size</div><div>Health</div><div>Score</div>
    </div>
    <div id="modelRows">
      <div class="empty-state" style="grid-column:1/-1">&#128230; 点击「刷新」加载模型数据</div>
    </div>
  </div>
  <div class="pagination">
    <button class="btn ghost sm" onclick="modelsPrevPage()">&larr; 上一页</button>
    <span id="modelsPageInfo">1 / 1</span>
    <button class="btn ghost sm" onclick="modelsNextPage()">下一页 &rarr;</button>
  </div>
</div>

<!-- ===== Tab Panel: Providers ===== -->
<div class="tab-panel" id="tab-providers">
  <div class="section">
    <div class="section-title">&#128268; Providers <span class="count" id="providerCount">&mdash;</span></div>
    <div class="section-actions">
      <button class="btn ghost sm" onclick="enableAllProviders()">全部启用</button>
      <button class="btn ghost sm" onclick="refreshProvidersTab()">全部刷新</button>
      <button class="btn ghost sm" onclick="openProviderKeys()">&#128273; Key 管理</button>
      <button class="btn primary sm" onclick="openWizard()">&#129497; Wizard</button>
    </div>
  </div>
  <div class="provider-grid" id="providerGridFull">
    <div class="empty-state">&#128268; 点击「全部刷新」加载 Provider 数据</div>
  </div>
</div>

<!-- ===== Tab Panel: Fusion (v4.1 新增) ===== -->
<div class="tab-panel" id="tab-fusion">
  <div class="fusion-status" id="fusionStatus">
    <div class="empty-state" style="width:100%">&#10024; 切换到此 tab 加载 Fusion 数据</div>
  </div>

  <div class="section">
    <div class="section-title">&#128203; Plan 模板</div>
  </div>
  <div class="fusion-templates" id="fusionPresetGrid">
    <div class="empty-state" style="grid-column:1/-1">⏳ 加载预设中...</div>
  </div>

  <div class="section">
    <div class="section-title">&#128221; Plan 编辑器</div>
    <div class="section-actions">
      <button class="btn ghost sm" onclick="seedFusionDefaults()" title="一键初始化 4 个默认组合">&#9889; 一键初始化</button>
      <button class="btn primary sm" onclick="registerFusionPlan()">&#10003; 注册 Plan</button>
    </div>
  </div>
  <textarea class="fusion-editor" id="fusionEditor" spellcheck="false" placeholder='{ "plan_id": "my_plan", "type": "vote", "model_ids": [] }'></textarea>

  <div class="section" style="margin-top:var(--space-5)">
    <div class="section-title">&#128203; 已注册 Plans <span class="count" id="fusionPlanCount">&mdash;</span></div>
    <div class="section-actions">
      <button class="btn ghost sm" onclick="loadFusion()">&#8635; 刷新</button>
    </div>
  </div>
  <div class="fusion-plans-table">
    <div class="fusion-plan-row" style="background:var(--bg-2);font-size:11px;text-transform:uppercase;color:var(--text-2);font-weight:600;letter-spacing:.5px">
      <div>Plan ID</div><div>Type</div><div>Detail</div><div>Actions</div>
    </div>
    <div id="fusionPlansBody">
      <div class="empty-state">暂无 Plan &middot; 点击上方模板创建</div>
    </div>
  </div>
</div>

<!-- ===== Tab Panel: Access (v4.1 新增) ===== -->
<div class="tab-panel" id="tab-access">
  <div class="section">
    <div class="section-title">&#128273; Public Keys <span class="count" id="accessKeyCount">&mdash;</span></div>
    <div class="section-actions">
      <button class="btn ghost sm" onclick="loadAccess()">&#8635; 刷新</button>
      <button class="btn primary sm" onclick="showCreateAccessKey()">&#43; 创建</button>
    </div>
  </div>

  <div id="accessCreateForm" style="display:none;margin-bottom:var(--space-4);padding:var(--space-4);background:var(--bg-2);border-radius:var(--radius)">
    <div style="display:grid;grid-template-columns:1fr 100px 1fr auto;gap:var(--space-2);align-items:end">
      <div>
        <label style="font-size:12px;color:var(--text-2);display:block;margin-bottom:4px">名称</label>
        <input type="text" id="accessKeyName" placeholder="my-key" class="filter-input">
      </div>
      <div>
        <label style="font-size:12px;color:var(--text-2);display:block;margin-bottom:4px">RPM</label>
        <input type="number" id="accessKeyRpm" value="60" class="filter-input">
      </div>
      <div>
        <label style="font-size:12px;color:var(--text-2);display:block;margin-bottom:4px">Model Filter</label>
        <input type="text" id="accessKeyFilter" placeholder="*:free, openrouter:*" class="filter-input">
      </div>
      <button class="btn primary" onclick="createAccessKey()">创建</button>
    </div>
    <div id="accessNewKeyDisplay" style="display:none;margin-top:var(--space-3);padding:var(--space-3);background:var(--success-glow);border:1px solid var(--success);border-radius:var(--radius)">
      <div style="font-size:12px;color:var(--success);margin-bottom:var(--space-2);font-weight:600">请立刻复制保存原始 Key (仅显示这一次)</div>
      <code id="accessNewKeyValue" style="display:block;padding:var(--space-2);background:var(--bg-0);border-radius:var(--radius-sm);font-family:monospace;word-break:break-all;user-select:all"></code>
      <button class="btn ghost sm" style="margin-top:var(--space-2)" onclick="copyToClipboard(document.getElementById('accessNewKeyValue').textContent)">复制</button>
    </div>
  </div>

  <div class="access-table">
    <table class="data-table">
      <thead><tr><th>名称</th><th>哈希</th><th>RPM</th><th>过滤</th><th>末次使用</th><th></th></tr></thead>
      <tbody id="accessKeysBody">
        <tr><td colspan="6"><div class="empty-state">&#128273; 点击「刷新」加载 Key 数据</div></td></tr>
      </tbody>
    </table>
  </div>
</div>

<div class="footer">
  SuperModel Router v4.1.0 &middot; 5-Tab Navigation + Fusion Panel &middot; Press <kbd>Ctrl+Shift+L</kbd> to cycle theme
</div>

<!-- ===== Toast Container ===== -->
<div class="toast-container" id="toastContainer"></div>

<!-- ===== Wizard Modal ===== -->
<div id="wizardModal" class="modal-overlay" onclick="if(event.target===this)closeWizard()">
  <div class="modal-content" style="max-width:1100px">
    <div class="modal-header">
      <div class="modal-title">&#129497; 模型分组 Wizard</div>
      <button class="modal-close" onclick="closeWizard()">&times;</button>
    </div>
    <h3 style="font-size:13px;color:var(--text-2);margin:0 0 var(--space-3)">&#10024; 快速开始: 选一个预设场景</h3>
    <div class="wizard-presets-grid" id="wizardPresetsGrid">
      <div class="empty-state">加载中...</div>
    </div>
    <h3 style="font-size:13px;color:var(--text-2);margin:var(--space-5) 0 var(--space-2)">&#128269; 或自定义筛选条件</h3>
    <div class="wizard-filter-panel">
      <div class="filter-row">
        <label>Provider (多选)</label>
        <div class="chip-group" id="wizardFilterProviders"></div>
      </div>
      <div class="filter-row">
        <label>上下文窗口</label>
        <select id="wizardFilterContext" class="filter-select">
          <option value="0">全部</option>
          <option value="8000">&ge; 8K</option>
          <option value="16000">&ge; 16K</option>
          <option value="32000">&ge; 32K</option>
          <option value="64000">&ge; 64K</option>
          <option value="100000">&ge; 100K</option>
          <option value="128000">&ge; 128K</option>
          <option value="200000">&ge; 200K</option>
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
        <button class="btn-sm" onclick="resetWizardFilter()">&#128260; 重置</button>
        <button class="btn-sm primary" onclick="applyWizardFilter()">&#128269; 应用筛选</button>
      </div>
    </div>
    <h3 style="font-size:13px;color:var(--text-2);margin:var(--space-5) 0 var(--space-2)">
      匹配模型 (<span id="wizardMatchCount">0</span>)
      <span style="float:right">
        <button class="btn-sm" onclick="wizardSelectAll()">&#9745; 全选</button>
        <button class="btn-sm" onclick="wizardSelectNone()">&#9744; 清选</button>
      </span>
    </h3>
    <div class="wizard-models-list" id="wizardModelsList">
      <div class="empty-state">选一个预设场景 或 自定义筛选查看匹配模型</div>
    </div>
    <h3 style="font-size:13px;color:var(--text-2);margin:var(--space-5) 0 var(--space-2)">&#10024; 生成模型分组</h3>
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
        <button class="btn-sm" onclick="previewWizardGroup()">&#128269; 预览</button>
        <button class="btn-sm primary" onclick="generateWizardGroup()">&#10024; 生成分组</button>
      </div>
    </div>
    <div id="wizardResultPanel" style="display:none;margin-top:var(--space-5);padding:var(--space-4);background:var(--success-glow);border:1px solid var(--success);border-radius:var(--radius)">
      <h3 style="font-size:14px;color:var(--success);margin:0 0 var(--space-3)">&#9989; 分组生成成功</h3>
      <div id="wizardResultContent"></div>
    </div>
  </div>
</div>

<!-- ===== Provider Keys Modal ===== -->
<div id="providerKeysModal" class="modal-overlay" onclick="if(event.target===this)closeProviderKeys()">
  <div class="modal-content" style="max-width:900px">
    <div class="modal-header">
      <div class="modal-title">&#128273; Provider Key 管理 <span style="font-size:12px;color:var(--text-2);font-weight:400">下游 provider 的 API key</span> <button id="pkToggleBtn" class="btn ghost sm" style="margin-left:8px" onclick="toggleProviderKeyVisibility()">显示完整</button></div>
      <button class="modal-close" onclick="closeProviderKeys()">&times;</button>
    </div>
    <div style="margin-bottom:var(--space-4);padding:var(--space-3);background:var(--bg-2);border-radius:var(--radius);font-size:12px;color:var(--text-2)">
      <b>Provider Key</b> = SMR 用来调下游 (openrouter/nvidia 等) 的凭证。多 key 会轮询。<br>
      添加后自动刷新 provider 目录, 新 key 可能解锁新模型。
    </div>
    <div id="providerKeysList"><div class="empty-state">&#9203; 加载中...</div></div>
    <div style="margin-top:var(--space-5);padding-top:var(--space-4);border-top:1px solid var(--border)">
      <h3 style="font-size:14px;margin:0 0 var(--space-3)">添加新 Key</h3>
      <div style="display:grid;grid-template-columns:200px 1fr auto;gap:var(--space-2)">
        <select id="pkProviderSel" class="filter-select"></select>
        <input type="text" id="pkNewKey" placeholder="sk-... / nvapi-... / 粘贴 API key" class="filter-input">
        <button class="btn primary" onclick="addProviderKey()">添加</button>
      </div>
    </div>
  </div>
</div>

<!-- ===== Public Keys Modal ===== -->
<div id="publicKeysModal" class="modal-overlay" onclick="if(event.target===this)closePublicKeys()">
  <div class="modal-content" style="max-width:1000px">
    <div class="modal-header">
      <div class="modal-title">&#128275; Public Key 管理 <span style="font-size:12px;color:var(--text-2);font-weight:400">SMR 对外发放的 key</span> <button id="pubKeyToggleBtn" class="btn ghost sm" style="margin-left:8px" onclick="togglePublicKeyVisibility()">显示完整</button></div>
      <button class="modal-close" onclick="closePublicKeys()">&times;</button>
    </div>
    <div style="margin-bottom:var(--space-4);padding:var(--space-3);background:var(--bg-2);border-radius:var(--radius);font-size:12px;color:var(--text-2)">
      <b>Public Key</b> = 别人调 SMR 时用的 <code>smr-pub-...</code> key。可挂 model_filter 限制模型 + rate_limit_rpm 限流。<br>
      原始 key 只在创建那一次返回, 之后只存哈希。
    </div>
    <div id="publicKeysList"><div class="empty-state">&#9203; 加载中...</div></div>
    <div style="margin-top:var(--space-5);padding-top:var(--space-4);border-top:1px solid var(--border)">
      <h3 style="font-size:14px;margin:0 0 var(--space-3)">创建新 Public Key</h3>
      <div style="display:grid;grid-template-columns:1fr 100px 1fr auto;gap:var(--space-2)">
        <input type="text" id="ppkName" placeholder="name (字母/数字/-/_)" class="filter-input">
        <input type="number" id="ppkRpm" placeholder="60" value="60" class="filter-input" title="rate_limit_rpm">
        <input type="text" id="ppkFilter" placeholder="model_filter (逗号分隔, 如 *:free, openrouter:*)" class="filter-input">
        <button class="btn primary" onclick="createPublicKey()">创建</button>
      </div>
      <div id="ppkNewKeyDisplay" style="display:none;margin-top:var(--space-3);padding:var(--space-3);background:var(--success-glow);border:1px solid var(--success);border-radius:var(--radius)">
        <div style="font-size:12px;color:var(--success);margin-bottom:var(--space-2);font-weight:600">请立刻复制保存原始 Key (仅显示这一次)</div>
        <code id="ppkNewKeyValue" style="display:block;padding:var(--space-2);background:var(--bg-0);border-radius:var(--radius-sm);font-family:monospace;word-break:break-all;user-select:all"></code>
        <button class="btn ghost sm" style="margin-top:var(--space-2)" onclick="copyToClipboard(document.getElementById('ppkNewKeyValue').textContent)">复制</button>
      </div>
    </div>
  </div>
</div>

<!-- ===== Fusion Test Modal (v4.1 新增) ===== -->
<div id="fusionTestModal" class="modal-overlay" onclick="if(event.target===this)closeFusionTestModal()">
  <div class="modal-content" style="max-width:800px">
    <div class="modal-header">
      <div class="modal-title">&#10024; Fusion 测试 &mdash; <span id="fusionTestPlanId"></span></div>
      <button class="modal-close" onclick="closeFusionTestModal()">&times;</button>
    </div>
    <div class="filter-row">
      <label>测试 Prompt</label>
      <textarea id="fusionTestPrompt" class="filter-input" rows="4" placeholder="输入测试 prompt..." style="font-family:var(--font);resize:vertical"></textarea>
    </div>
    <div style="text-align:right;margin-top:var(--space-3)">
      <button class="btn primary" onclick="runFusionTest()">&#9654; 运行</button>
    </div>
    <div id="fusionTestResult" style="display:none;margin-top:var(--space-4)">
      <h4 style="font-size:12px;color:var(--text-2);margin:0 0 var(--space-2)">Answer</h4>
      <div id="fusionTestAnswer" class="fusion-test-answer"></div>
      <div id="fusionTestTrace"></div>
      <div id="fusionTestElapsed" style="margin-top:var(--space-2)"></div>
    </div>
  </div>
</div>

<script>
/* ============================================================
 * SMR v4.1.0 - 5-Tab Dashboard + Fusion Panel
 * ============================================================ */

// ===== 工具函数 =====
function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});
}
function formatUptime(s) {
  if (!s || s < 0) return '\u2014';
  s = Math.floor(s);
  var h = Math.floor(s / 3600);
  var m = Math.floor((s % 3600) / 60);
  return h > 0 ? h + 'h ' + m + 'm' : m + 'm';
}

// ===== 主题切换 (dark / light / system) =====
var THEME_KEY = 'smr-theme';
var THEME_ORDER = ['dark', 'light', 'system'];
function getTheme() { return localStorage.getItem(THEME_KEY) || 'dark'; }
function applyTheme(mode) {
  var m = mode || getTheme();
  if (m === 'system') {
    var sysDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.dataset.theme = sysDark ? 'dark' : 'light';
  } else {
    document.documentElement.dataset.theme = m;
  }
  updateThemeIcon();
}
function cycleTheme() {
  var cur = getTheme();
  var next = THEME_ORDER[(THEME_ORDER.indexOf(cur) + 1) % THEME_ORDER.length];
  localStorage.setItem(THEME_KEY, next);
  applyTheme(next);
  var labels = {dark:'\u{1F319} 暗色', light:'\u{2600}\u{FE0F} 亮色', system:'\u{1F4BB} 跟随系统'};
  toast('success', '主题已切换', labels[next]);
}
function updateThemeIcon() {
  var icon = document.getElementById('themeToggle');
  if (!icon) return;
  var m = getTheme();
  icon.textContent = m === 'dark' ? '\u{1F319}' : m === 'light' ? '\u{2600}\u{FE0F}' : '\u{1F4BB}';
  icon.title = '主题: ' + m + ' (点击切换, Ctrl+Shift+L)';
}

// ===== Toast 系统 =====
function toast(type, title, msg, duration) {
  duration = duration || 4000;
  var c = document.getElementById('toastContainer');
  if (!c) { console.log('[' + type + '] ' + title + ': ' + (msg||'')); return; }
  var t = document.createElement('div');
  t.className = 'toast ' + type;
  var icons = {success:'\u2713', warn:'\u26A0', error:'\u2717', info:'\u2139'};
  t.innerHTML = '<span class="toast-icon">' + (icons[type]||'\u2139') + '</span>' +
    '<div class="toast-text"><b>' + escapeHtml(title) + '</b>' + (msg ? escapeHtml(msg) : '') + '</div>';
  c.appendChild(t);
  var timeout = setTimeout(function(){ dismissToast(t); }, duration);
  t._timeout = timeout;
  t.onclick = function() { clearTimeout(timeout); dismissToast(t); };
}
function dismissToast(t) {
  t.classList.add('out');
  setTimeout(function(){ t.remove(); }, 300);
}

// ===== Tab 管理 (v4.1 新增) =====
var TAB_KEY = 'smr-active-tab';
function switchTab(tabName) {
  document.querySelectorAll('.tab-panel').forEach(function(p){ p.classList.remove('active'); });
  var panel = document.getElementById('tab-' + tabName);
  if (panel) panel.classList.add('active');
  document.querySelectorAll('.tab-btn').forEach(function(b){ b.classList.remove('active'); });
  var btn = document.querySelector('.tab-btn[data-tab="' + tabName + '"]');
  if (btn) btn.classList.add('active');
  localStorage.setItem(TAB_KEY, tabName);
  // Lazy load
  if (tabName === 'models' && !window._modelsLoaded) { loadModels(); }
  if (tabName === 'providers' && !window._providersTabLoaded) { loadProviders(); }
  if (tabName === 'fusion' && !window._fusionLoaded) { loadFusion(); }
  if (tabName === 'access' && !window._accessLoaded) { loadAccess(); }
}
function initTabs() {
  var saved = localStorage.getItem(TAB_KEY) || 'overview';
  switchTab(saved);
}

// ===== 数据加载 =====
var BASE = '';
async function fetchJSON(path) {
  try {
    var r = await fetch(BASE + path, {signal: AbortSignal.timeout(8000)});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.json();
  } catch (e) {
    console.error('fetch failed:', path, e);
    return null;
  }
}

async function loadAll() {
  var results = await Promise.all([
    fetchJSON('/v1/health'),
    fetchJSON('/v1/admin/stats'),
    fetchJSON('/v1/admin/providers?include_disabled=true'),
  ]);
  var health = results[0], stats = results[1], providers = results[2];
  window._lastStats = stats || {};
  window._lastHealth = health || {};
  window._lastProviders = providers || {};
  if (health) renderStatusBanner(health);
  if (stats) renderKPIs(stats);
  if (providers) renderProviders(providers);
  loadActivity();
}

async function loadModels() {
  window._modelsLoaded = true;
  var rows = document.getElementById('modelRows');
  if (rows) rows.innerHTML = '<div class="empty-state" style="grid-column:1/-1">\u23F3 加载中...</div>';
  var data = await fetchJSON('/v1/admin/models');
  if (data) {
    _allModels = data.models || data || [];
    _filteredModels = _allModels;
    _modelsPage = 0;
    renderModelsPage();
  } else {
    if (rows) rows.innerHTML = '<div class="empty-state" style="grid-column:1/-1">加载失败</div>';
  }
}

async function loadProviders() {
  window._providersTabLoaded = true;
  var grid = document.getElementById('providerGridFull');
  if (grid) grid.innerHTML = '<div class="provider-card"><div class="skeleton" style="height:120px"></div></div><div class="provider-card"><div class="skeleton" style="height:120px"></div></div><div class="provider-card"><div class="skeleton" style="height:120px"></div></div>';
  var providers = await fetchJSON('/v1/admin/providers?include_disabled=true');
  if (providers) {
    window._lastProviders = providers;
    renderProviders(providers, 'providerGridFull');
  }
}

async function loadFusion() {
  window._fusionLoaded = true;
  var results = await Promise.all([
    fetchJSON('/v1/admin/fusion/status'),
    fetchJSON('/v1/admin/fusion/plans'),
    fetchJSON('/v1/admin/fusion/presets'),
  ]);
  var status = results[0], plans = results[1], presets = results[2];
  if (status) renderFusionStatus(status);
  if (plans) renderFusionPlans(plans);
  window._fusionPresets = (presets && presets.presets) || [];
  renderFusionPresets(window._fusionPresets);
}

function renderFusionPresets(presets) {
  var grid = document.getElementById('fusionPresetGrid');
  if (!grid) return;
  if (!presets || !presets.length) {
    grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1">\u65e0\u9884\u8bbe</div>';
    return;
  }
  var cards = presets.map(function(p){
    return '<div class="fusion-template-btn" onclick="fillFusionPreset(\'' + escapeHtml(p.id) + '\')" title="' + escapeHtml(p.description) + '">' +
      '<div class="fusion-template-icon">' + escapeHtml(p.icon || '\u2728') + '</div>' +
      '<div class="fusion-template-name">' + escapeHtml(p.name || p.id) + '</div>' +
      '<div class="fusion-template-desc">' + escapeHtml(p.description || '') + '</div>' +
    '</div>';
  });
  // Custom (blank editor) card always last
  cards.push('<div class="fusion-template-btn" onclick="fillFusionPreset(\'custom\')">' +
    '<div class="fusion-template-icon">\u270f\ufe0f</div>' +
    '<div class="fusion-template-name">\u81ea\u5b9a\u4e49 Custom</div>' +
    '<div class="fusion-template-desc">\u7a7a\u767d JSON \u7f16\u8f91\u5668</div>' +
  '</div>');
  grid.innerHTML = cards.join('');
}

async function loadAccess() {
  window._accessLoaded = true;
  var data = await fetchJSON('/v1/admin/public-keys');
  renderAccessKeys(data);
}

// ===== 渲染: Status Banner =====
function renderStatusBanner(h) {
  var ver = document.getElementById('brandVersion');
  if (ver && h.version) ver.textContent = h.version;
  var txt = document.getElementById('statusText');
  txt.textContent = '系统正常 \u00B7 ' + formatUptime(h.uptime_seconds) + ' uptime';
  var provs = h.providers || {};
  var entries = Object.keys(provs).map(function(name){ return Object.assign({name:name}, provs[name]); });
  var enabled = entries.filter(function(p){ return p.enabled !== false && !p.disabled; });
  var healthy = enabled.filter(function(p){ return !p.degraded; });
  var degraded = enabled.filter(function(p){ return p.degraded; });
  var down = entries.filter(function(p){ return p.enabled === false || p.disabled; });
  document.getElementById('statusMeta').innerHTML =
    '<span><b>' + (h.total_models || 0) + '</b> models</span>' +
    '<span><b>' + entries.length + '</b> providers</span>' +
    '<span><b>' + healthy.length + '</b> healthy</span>' +
    '<span><b>' + degraded.length + '</b> degraded</span>' +
    '<span><b>' + down.length + '</b> down</span>' +
    '<span>\u00B7</span>' +
    '<span>Next refresh in <b id="nextRefresh">30s</b></span>';
  var dot = document.getElementById('statusDot');
  if (down.length > 0 || degraded.length > 2) {
    dot.style.background = 'var(--warn)';
    dot.style.boxShadow = '0 0 0 4px var(--warn-glow)';
    txt.textContent = '部分 provider 异常 \u00B7 ' + formatUptime(h.uptime_seconds) + ' uptime';
  }
}

// ===== 渲染: KPI Cards =====
function renderKPIs(stats) {
  var totalCalls = 0, successCalls = 0, latWeighted = 0, latCount = 0;
  Object.keys(stats).forEach(function(k){
    var s = stats[k];
    if (k === 'global' || typeof s !== 'object') return;
    totalCalls += s.total_calls || 0;
    successCalls += s.success_calls || 0;
    if (s.avg_latency_ms && s.total_calls) {
      latWeighted += s.avg_latency_ms * s.total_calls;
      latCount += s.total_calls;
    }
  });
  var successRate = totalCalls > 0 ? (successCalls / totalCalls * 100) : 0;
  var avgLat = latCount > 0 ? (latWeighted / latCount / 1000) : 0;
  document.getElementById('kpiTodayCalls').textContent = totalCalls.toLocaleString();
  document.getElementById('kpiTodayCallsDelta').textContent =
    totalCalls > 0 ? (successCalls + ' \u2713 / ' + (totalCalls - successCalls) + ' \u2717') : '等待数据...';
  document.getElementById('kpiSuccessRate').innerHTML = successRate.toFixed(1) + '<span style="font-size:18px">%</span>';
  document.getElementById('kpiSuccessRateDelta').textContent =
    successRate >= 90 ? '\u2713 健康' : successRate >= 70 ? '\u26A0 关注' : successRate > 0 ? '\u2717 异常' : '等待数据...';
  document.getElementById('kpiAvgLatency').innerHTML = avgLat.toFixed(2) + '<span style="font-size:18px">s</span>';
  document.getElementById('kpiAvgLatencyDelta').textContent =
    latCount > 0 ? (latCount.toLocaleString() + ' calls 采样') : '等待数据...';
  document.getElementById('kpiFreeCalls').innerHTML = totalCalls + '<span style="font-size:18px">total</span>';
  document.getElementById('kpiFreeCallsDelta').textContent =
    totalCalls > 0 ? (Math.round(successRate) + '% success rate') : '等待数据...';
}

// ===== 渲染: Providers =====
function renderProviders(providers, containerId) {
  var grid = document.getElementById(containerId || 'providerGrid');
  if (!grid) return;
  var list = [];
  if (Array.isArray(providers)) list = providers;
  else if (providers.providers) list = providers.providers;
  else if (typeof providers === 'object') {
    list = Object.keys(providers).map(function(name){ return Object.assign({name:name}, providers[name]); });
  }
  var statsDict = window._lastStats || {};
  var healthDict = (window._lastHealth && window._lastHealth.providers) || {};
  list = list.map(function(p){
    var s = statsDict[p.name] || {};
    var h = healthDict[p.name] || {};
    return Object.assign({}, p, {
      models: p.models || p.model_count || h.models || 0,
      total_calls: p.total_calls || p.calls || s.total_calls || 0,
      avg_latency_ms: p.avg_latency_ms || s.avg_latency_ms || 0,
      quality_score: p.quality_score != null ? p.quality_score : s.quality_score,
      degraded: p.degraded || (s.fail_calls || 0) > 2,
      fail_count: p.fail_count || s.fail_calls || 0,
    });
  });
  var e1 = document.getElementById('providerCount');
  var e2 = document.getElementById('providerCountOverview');
  if (e1) e1.textContent = list.length;
  if (e2) e2.textContent = list.length;
  if (list.length === 0) {
    grid.innerHTML = '<div class="empty-state">暂无 provider</div>';
    return;
  }
  grid.innerHTML = list.slice(0, 12).map(function(p, i){
    var isEnabled = p.enabled !== false && !p.disabled;
    var isDegraded = p.degraded || (p.fail_count || 0) > 2;
    var dotClass = !isEnabled ? 'down' : isDegraded ? 'degraded' : 'ok';
    var cardClass = !isEnabled ? 'down' : isDegraded ? 'degraded' : '';
    var avgLat = p.avg_latency_ms ? (p.avg_latency_ms/1000).toFixed(1) + 's' : '\u2014';
    var q = p.quality_score != null ? Number(p.quality_score).toFixed(1) : '\u2014';
    var models = p.models || p.model_count || 0;
    var calls = p.total_calls || p.calls || 0;
    var seed = (p.name || 'x').charCodeAt(0) + i;
    var sparkBars = Array.from({length: 12}, function(_, j){
      var base = q !== '\u2014' ? Number(q) : 60;
      var noise = Math.sin(seed + j) * 25;
      var h2 = Math.max(15, Math.min(95, base + noise));
      var color = isDegraded ? 'var(--warn)' : !isEnabled ? 'var(--danger)' : null;
      var style = color ? ('height:' + h2 + '%;background:' + color) : ('height:' + h2 + '%');
      return '<div class="spark-bar" style="' + style + '"></div>';
    }).join('');
    return '<div class="provider-card ' + cardClass + '">' +
      '<div class="provider-head">' +
        '<div class="provider-dot ' + dotClass + '"></div>' +
        '<div class="provider-name">' + escapeHtml(p.name) + '</div>' +
        '<div class="provider-models">' + models + ' models</div>' +
      '</div>' +
      '<div class="provider-stats">' +
        '<span><b>' + avgLat + '</b>avg</span>' +
        '<span><b>' + calls + '</b>calls</span>' +
        '<span><b>' + q + '</b>q</span>' +
      '</div>' +
      '<div class="provider-spark">' + sparkBars + '</div>' +
      '<div class="provider-actions">' +
        '<button class="btn ghost sm" onclick="openEditProvider(\'' + escapeHtml(p.name) + '\')" title="编辑">\u2699</button>' +
        '<button class="btn ghost sm" onclick="refreshProvider(\'' + escapeHtml(p.name) + '\')" title="刷新">\u21BB</button>' +
        '<button class="btn ghost sm" onclick="cloneProvider(\'' + escapeHtml(p.name) + '\')" title="复制">\u2398</button>' +
        (isEnabled
          ? '<button class="btn ghost sm" onclick="disableProvider(\'' + escapeHtml(p.name) + '\')" title="停用">\u23F8</button>'
          : '<button class="btn success sm" onclick="reEnableProvider(\'' + escapeHtml(p.name) + '\')" title="启用">\u25B6</button>') +
      '</div>' +
    '</div>';
  }).join('');
}

// ===== 渲染: Models =====
var _allModels = [];
var _filteredModels = [];
var _modelsPage = 0;
var _modelsPerPage = 50;

function renderModels(models) {
  var rows = document.getElementById('modelRows');
  if (!rows) return;
  var list = [];
  if (Array.isArray(models)) list = models;
  else if (models.models) list = models.models;
  else if (models.data) list = models.data;
  var mc = document.getElementById('modelCount');
  if (mc) mc.textContent = list.length;
  if (list.length === 0) {
    rows.innerHTML = '<div class="empty-state" style="grid-column:1/-1">暂无模型</div>';
    return;
  }
  rows.innerHTML = list.map(function(m){
    var pricing = m.pricing_detail || (m.is_free ? 'free' : (m.pricing || '\u2014'));
    var isFree = m.is_free || pricing === 'free' || (typeof pricing === 'object' && pricing.prompt === '0');
    var pricingLabel = typeof pricing === 'string' ? pricing : (pricing.prompt != null ? ('$' + pricing.prompt) : (isFree ? 'free' : '\u2014'));
    var sizeClass = m.size_class || 'unknown';
    var sizeTag = 'size-s', sizeLabel = '\u2014';
    if (sizeClass === '>200B') { sizeTag = 'size-xl'; sizeLabel = m.size_b ? (m.size_b + 'B') : '>200B'; }
    else if (sizeClass === '70-200B') { sizeTag = 'size-l'; sizeLabel = m.size_b ? (m.size_b + 'B') : '70-200B'; }
    else if (sizeClass === '13-70B') { sizeTag = 'size-m'; sizeLabel = m.size_b ? (m.size_b + 'B') : '13-70B'; }
    else if (sizeClass === '<13B') { sizeTag = 'size-s'; sizeLabel = m.size_b ? (m.size_b + 'B') : '<13B'; }
    else { sizeLabel = 'unknown'; }
    var score = m.capability_score || 0;
    var scoreClass = score >= 85 ? 'hi' : score >= 60 ? 'mid' : score > 0 ? 'lo' : 'lo';
    var healthClass = score >= 70 ? 'ok' : score >= 40 ? 'warn' : 'fail';
    return '<div class="models-row">' +
      '<div class="model-id" title="' + escapeHtml(m.id||'') + '">' + escapeHtml(m.id || '') + '</div>' +
      '<div style="color:var(--text-1);font-size:12px">' + escapeHtml(m.provider || '') + '</div>' +
      '<div><span class="tag ' + (isFree?'free':'paid') + '">' + escapeHtml(pricingLabel) + '</span></div>' +
      '<div><span class="tag ' + sizeTag + '">' + escapeHtml(sizeLabel) + '</span></div>' +
      '<div><span class="health-dot ' + healthClass + '"><span class="dot"></span>' + score + '</span></div>' +
      '<div><span class="score ' + scoreClass + '">' + score + '</span></div>' +
    '</div>';
  }).join('');
}

function renderModelsPage() {
  var start = _modelsPage * _modelsPerPage;
  var pageModels = _filteredModels.slice(start, start + _modelsPerPage);
  renderModels({models: pageModels});
  var mc = document.getElementById('modelCount');
  if (mc) mc.textContent = _filteredModels.length;
  var totalPages = Math.ceil(_filteredModels.length / _modelsPerPage) || 1;
  var info = document.getElementById('modelsPageInfo');
  if (info) info.textContent = (_modelsPage + 1) + ' / ' + totalPages;
}

function modelsPrevPage() {
  if (_modelsPage > 0) { _modelsPage--; renderModelsPage(); }
}
function modelsNextPage() {
  var totalPages = Math.ceil(_filteredModels.length / _modelsPerPage) || 1;
  if (_modelsPage < totalPages - 1) { _modelsPage++; renderModelsPage(); }
}

// ===== Models 过滤 =====
function onModelSearch(q) {
  if (!q || !q.trim()) {
    _filteredModels = _allModels;
  } else {
    var ql = q.toLowerCase();
    _filteredModels = _allModels.filter(function(m){
      var id = (m.id || '').toLowerCase();
      var prov = (m.provider || '').toLowerCase();
      return id.indexOf(ql) >= 0 || prov.indexOf(ql) >= 0;
    });
  }
  _modelsPage = 0;
  renderModelsPage();
}

async function filterModelsByProvider() {
  var provider = prompt('Provider (逗号分隔, 留空=全部):');
  if (provider === null) return;
  var params = new URLSearchParams();
  if (provider.trim()) params.append('providers', provider.trim());
  var data = await fetchJSON('/v1/admin/models/filter?' + params.toString());
  if (data && data.models) {
    _allModels = data.models;
    _filteredModels = _allModels;
    _modelsPage = 0;
    renderModelsPage();
    toast('success', '过滤完成', (data.total || data.models.length) + ' 个模型');
  }
}

async function filterModelsBySize() {
  var minStr = prompt('最小参数量 (B, 留空=无下限):');
  if (minStr === null) return;
  var maxStr = prompt('最大参数量 (B, 留空=无上限):');
  if (maxStr === null) return;
  var params = new URLSearchParams();
  if (minStr.trim()) params.append('size_min', parseFloat(minStr.trim()));
  if (maxStr.trim()) params.append('size_max', parseFloat(maxStr.trim()));
  var data = await fetchJSON('/v1/admin/models/filter?' + params.toString());
  if (data && data.models) {
    _allModels = data.models;
    _filteredModels = _allModels;
    _modelsPage = 0;
    renderModelsPage();
    toast('success', '过滤完成', (data.total || data.models.length) + ' 个模型');
  }
}

async function filterModelsByCapability() {
  var minStr = prompt('最低能力分数 (0-100):');
  if (minStr === null) return;
  var params = new URLSearchParams();
  if (minStr.trim()) params.append('capability_min', parseFloat(minStr.trim()));
  var data = await fetchJSON('/v1/admin/models/filter?' + params.toString());
  if (data && data.models) {
    _allModels = data.models;
    _filteredModels = _allModels;
    _modelsPage = 0;
    renderModelsPage();
    toast('success', '过滤完成', (data.total || data.models.length) + ' 个模型');
  }
}

function filterModelsByPrice() {
  var price = prompt('价格类型 (free=免费, paid=付费, 留空=全部):');
  if (price === null) return;
  var priceLower = price.trim().toLowerCase();
  if (priceLower === 'free') {
    _filteredModels = _allModels.filter(function(m){ return m.is_free || (m.pricing || '').toLowerCase() === 'free'; });
  } else if (priceLower === 'paid') {
    _filteredModels = _allModels.filter(function(m){ return !m.is_free && (m.pricing || '').toLowerCase() !== 'free'; });
  } else {
    _filteredModels = _allModels;
  }
  _modelsPage = 0;
  renderModelsPage();
  toast('info', '价格过滤', _filteredModels.length + ' 个模型');
}

function resetModelFilters() {
  _filteredModels = _allModels;
  _modelsPage = 0;
  var search = document.getElementById('modelSearch');
  if (search) search.value = '';
  renderModelsPage();
  toast('info', '过滤器已重置');
}

// ===== Activity Stream =====
function loadActivity() {
  var el = document.getElementById('activityStream');
  if (!el) return;
  var mock = [
    {time:'12:34:21', status:'ok', route:'gpt-4o', provider:'openrouter \u00B7 1.8s', latency:'1.8s', cost:'$0.002'},
    {time:'12:34:18', status:'ok', route:'llama-3.1-70b', provider:'nvidia \u00B7 0.4s', latency:'0.4s', cost:'free'},
    {time:'12:34:15', status:'warn', route:'gpt-4o \u2192 fallback openrouter', provider:'newapi timeout \u00B7 auto reroute', latency:'5.2s', cost:'$0.003'},
    {time:'12:34:11', status:'ok', route:'claude-3-sonnet', provider:'openrouter \u00B7 2.3s', latency:'2.3s', cost:'$0.015'},
    {time:'12:34:08', status:'ok', route:'qwen-2.5-72b', provider:'volc_ark \u00B7 1.2s', latency:'1.2s', cost:'$0.001'},
    {time:'12:34:02', status:'fail', route:'deepseek-v3', provider:'deepseek \u00B7 429 rate limited', latency:'2.1s', cost:'$0'},
    {time:'12:33:58', status:'ok', route:'gpt-4o-mini', provider:'openrouter \u00B7 0.8s', latency:'0.8s', cost:'$0.0001'},
    {time:'12:33:51', status:'ok', route:'llama-3.1-405b', provider:'nvidia \u00B7 1.6s', latency:'1.6s', cost:'free'},
  ];
  var ac = document.getElementById('activityCount');
  if (ac) ac.textContent = mock.length + ' recent';
  el.innerHTML = mock.map(function(r){
    var icon = r.status === 'ok' ? '\u2713' : r.status === 'warn' ? '\u21BB' : '\u2717';
    var cls = 'status-icon ' + r.status;
    return '<div class="activity-row">' +
      '<span class="activity-time">' + r.time + '</span>' +
      '<span class="activity-status"><span class="' + cls + '">' + icon + '</span></span>' +
      '<span class="activity-route">' + escapeHtml(r.route) + '</span>' +
      '<span class="activity-provider">' + escapeHtml(r.provider) + '</span>' +
      '<span class="activity-latency">' + r.latency + '</span>' +
      '<span class="activity-cost">' + r.cost + '</span>' +
    '</div>';
  }).join('');
}

// ===== Fusion 渲染 (v4.1 新增) =====
function renderFusionStatus(status) {
  var el = document.getElementById('fusionStatus');
  if (!el || !status) return;
  var enabled = status.enabled !== false;
  el.innerHTML =
    '<div class="fusion-stat">' +
      '<div class="fusion-stat-label">状态</div>' +
      '<div class="fusion-stat-value" style="color:' + (enabled ? 'var(--success)' : 'var(--text-2)') + '">' + (enabled ? '\u2713 启用' : '\u2717 禁用') + '</div>' +
    '</div>' +
    '<div class="fusion-stat">' +
      '<div class="fusion-stat-label">Active Plans</div>' +
      '<div class="fusion-stat-value">' + (status.active_plans || status.plan_count || 0) + '</div>' +
    '</div>' +
    '<div class="fusion-stat">' +
      '<div class="fusion-stat-label">Total Runs</div>' +
      '<div class="fusion-stat-value">' + (status.total_runs || 0) + '</div>' +
    '</div>' +
    '<div class="fusion-stat">' +
      '<div class="fusion-stat-label">Success Rate</div>' +
      '<div class="fusion-stat-value">' + (status.success_rate != null ? status.success_rate + '%' : '\u2014') + '</div>' +
    '</div>';
}

function renderFusionPlans(plans) {
  var el = document.getElementById('fusionPlansBody');
  if (!el) return;
  var list = [];
  if (Array.isArray(plans)) list = plans;
  else if (plans && plans.plans) list = plans.plans;
  var pc = document.getElementById('fusionPlanCount');
  if (pc) pc.textContent = list.length;
  if (list.length === 0) {
    el.innerHTML = '<div class="empty-state">暂无 Fusion Plan \u00B7 点击上方模板创建</div>';
    return;
  }
  el.innerHTML = list.map(function(p){
    var pid = p.plan_id || p.id || '';
    var type = p.type || (p.plan && p.plan.type) || '\u2014';
    var detail = p.description || p.strategy || (p.plan && p.plan.strategy) || (p.steps ? p.steps.join(' \u2192 ') : '\u2014');
    return '<div class="fusion-plan-row">' +
      '<div style="font-family:var(--mono);font-size:12px;font-weight:600">' + escapeHtml(pid) + '</div>' +
      '<div><span class="tag" style="background:var(--purple-glow);color:var(--purple)">' + escapeHtml(type) + '</span></div>' +
      '<div style="color:var(--text-1);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + escapeHtml(detail) + '</div>' +
      '<div style="display:flex;gap:var(--space-1)">' +
        '<button class="btn ghost sm" onclick="testFusionPlan(\'' + escapeHtml(pid) + '\')">测试</button>' +
        '<button class="btn danger sm" onclick="deleteFusionPlan(\'' + escapeHtml(pid) + '\')">删除</button>' +
      '</div>' +
    '</div>';
  }).join('');
}

// ===== Fusion 操作 (v4.1 新增) =====
async function fillFusionPreset(presetId) {
  var editor = document.getElementById('fusionEditor');
  if (presetId === 'custom') {
    editor.value = JSON.stringify({ plan_id: 'custom_plan', type: 'vote', model_ids: [], strategy: 'best_pick' }, null, 2);
    toast('info', '\u7a7a\u767d\u6a21\u677f', 'custom');
    return;
  }
  toast('info', '\u89e3\u6790\u4e2d...', presetId);
  try {
    var r = await fetch(BASE + '/v1/admin/fusion/presets/' + encodeURIComponent(presetId) + '/resolve', { method: 'POST' });
    var data = await r.json();
    if (data.error) { toast('error', '\u89e3\u6790\u5931\u8d25', data.error); return; }
    editor.value = JSON.stringify(data.plan, null, 2);
    toast('success', '\u9884\u8bbe\u5df2\u586b\u5145 (\u5df2\u6309\u5f53\u524d\u53ef\u7528\u6a21\u578b\u9009\u578b)', presetId);
  } catch(e) {
    toast('error', '\u7f51\u7edc\u9519\u8bef', e.message);
  }
}

async function seedFusionDefaults() {
  if (!confirm('\u4e00\u952e\u6ce8\u518c 4 \u4e2a\u9ed8\u8ba4\u7ec4\u5408 (vote/expert/pipeline/refine) \u5e76\u56fa\u5316\u5230 config.yaml\uff1f\n\u5df2\u5b58\u5728\u540c\u540d plan \u4f1a\u8df3\u8fc7\u3002')) return;
  toast('info', '\u521d\u59cb\u5316\u4e2d...', '\u89e3\u6790\u6a21\u578b + \u6ce8\u518c');
  try {
    var r = await fetch(BASE + '/v1/admin/fusion/seed', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({persist: true})
    });
    var data = await r.json();
    if (data.error) { toast('error', '\u521d\u59cb\u5316\u5931\u8d25', data.error); return; }
    var seeded = (data.seeded || []).length, skipped = (data.skipped || []).length;
    toast('success', '\u521d\u59cb\u5316\u5b8c\u6210', '\u65b0\u589e ' + seeded + ' \u4e2a, \u8df3\u8fc7 ' + skipped + ' \u4e2a');
    loadFusion();
  } catch(e) {
    toast('error', '\u7f51\u7edc\u9519\u8bef', e.message);
  }
}

async function registerFusionPlan() {
  var editor = document.getElementById('fusionEditor');
  var plan;
  try {
    plan = JSON.parse(editor.value);
  } catch(e) {
    toast('error', 'JSON 解析失败', e.message);
    return;
  }
  if (!plan.plan_id) { toast('warn', '缺少 plan_id'); return; }
  try {
    var r = await fetch(BASE + '/v1/admin/fusion/plans', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({plan_id: plan.plan_id, plan: plan}),
    });
    var data = await r.json();
    if (data.error) { toast('error', '注册失败', data.error); return; }
    toast('success', 'Plan 已注册', plan.plan_id);
    loadFusion();
  } catch(e) {
    toast('error', '网络错误', e.message);
  }
}

function testFusionPlan(planId) {
  window._testPlanId = planId;
  document.getElementById('fusionTestPlanId').textContent = planId;
  document.getElementById('fusionTestPrompt').value = '';
  document.getElementById('fusionTestResult').style.display = 'none';
  var m = document.getElementById('fusionTestModal');
  if (m) m.classList.add('active');
}

async function runFusionTest() {
  var planId = window._testPlanId;
  var prompt = document.getElementById('fusionTestPrompt').value.trim();
  if (!prompt) { toast('warn', '请输入 prompt'); return; }
  var resultDiv = document.getElementById('fusionTestResult');
  var answerDiv = document.getElementById('fusionTestAnswer');
  var traceDiv = document.getElementById('fusionTestTrace');
  var elapsedDiv = document.getElementById('fusionTestElapsed');
  answerDiv.innerHTML = '<div class="empty-state">\u23F3 运行中...</div>';
  traceDiv.innerHTML = '';
  elapsedDiv.innerHTML = '';
  resultDiv.style.display = 'block';
  try {
    var r = await fetch(BASE + '/v1/admin/fusion/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({plan_id: planId, prompt: prompt, history: []}),
      signal: AbortSignal.timeout(30000),
    });
    var data = await r.json();
    if (data.error) {
      answerDiv.innerHTML = '<div style="color:var(--danger)">\u2717 ' + escapeHtml(data.error) + '</div>';
      return;
    }
    answerDiv.innerHTML = escapeHtml(data.answer || data.output || JSON.stringify(data));
    if (data.trace) {
      traceDiv.innerHTML = '<h4 style="font-size:12px;color:var(--text-2);margin:var(--space-3) 0 var(--space-2)">Trace</h4>' +
        '<pre class="fusion-test-trace">' + escapeHtml(JSON.stringify(data.trace, null, 2)) + '</pre>';
    }
    if (data.elapsed != null) {
      elapsedDiv.innerHTML = '<span style="font-size:12px;color:var(--text-2)">耗时: <b style="color:var(--success);font-family:var(--mono)">' + data.elapsed + 's</b></span>';
    }
  } catch(e) {
    answerDiv.innerHTML = '<div style="color:var(--danger)">\u2717 ' + escapeHtml(e.message) + '</div>';
  }
}

function closeFusionTestModal() {
  var m = document.getElementById('fusionTestModal');
  if (m) m.classList.remove('active');
}

async function deleteFusionPlan(planId) {
  if (!confirm('删除 plan "' + planId + '"?')) return;
  try {
    var r = await fetch(BASE + '/v1/admin/fusion/plans/' + encodeURIComponent(planId), {method: 'DELETE'});
    var data = await r.json();
    if (data.error) { toast('error', '删除失败', data.error); return; }
    toast('success', '已删除', planId);
    loadFusion();
  } catch(e) {
    toast('error', '网络错误', e.message);
  }
}

// ===== Access 渲染 (v4.1 新增) =====
function renderAccessKeys(data) {
  var el = document.getElementById('accessKeysBody');
  if (!el) return;
  if (!data || !data.keys || !data.keys.length) {
    el.innerHTML = '<tr><td colspan="6"><div class="empty-state">\U0001F4ED 无 Public Key \u00B7 点击创建</div></td></tr>';
    var ac = document.getElementById('accessKeyCount');
    if (ac) ac.textContent = '0';
    return;
  }
  var ac = document.getElementById('accessKeyCount');
  if (ac) ac.textContent = data.keys.length;
  el.innerHTML = data.keys.map(function(k){
    var hashShort = k.key_hash ? escapeHtml(k.key_hash.slice(0, 16)) + '...' : '\u2014';
    var filter = Array.isArray(k.model_filter) && k.model_filter.length
      ? (k.model_filter.length > 3 ? k.model_filter.slice(0,3).join(', ') + '...' : k.model_filter.join(', '))
      : '全部';
    var last = k.last_used ? new Date(k.last_used * 1000).toLocaleString() : '\u2014';
    return '<tr>' +
      '<td><b>' + escapeHtml(k.name) + '</b>' + (k.enabled === false ? ' \u23F8' : '') + '</td>' +
      '<td><code style="font-size:11px">' + hashShort + '</code></td>' +
      '<td>' + (k.rate_limit_rpm || 60) + '</td>' +
      '<td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px" title="' + escapeHtml(filter) + '">' + escapeHtml(filter) + '</td>' +
      '<td style="font-size:11px">' + escapeHtml(last) + '</td>' +
      '<td><button class="btn danger sm" onclick="deleteAccessKey(\'' + escapeHtml(k.name) + '\')">\U0001F5D1</button></td>' +
    '</tr>';
  }).join('');
}

function showCreateAccessKey() {
  var form = document.getElementById('accessCreateForm');
  if (form) {
    form.style.display = form.style.display === 'none' ? 'block' : 'none';
    if (form.style.display === 'block') {
      var disp = document.getElementById('accessNewKeyDisplay');
      if (disp) disp.style.display = 'none';
    }
  }
}

async function createAccessKey() {
  var name = document.getElementById('accessKeyName').value.trim();
  var rpm = parseInt(document.getElementById('accessKeyRpm').value) || 60;
  var filterRaw = document.getElementById('accessKeyFilter').value.trim();
  if (!name) return toast('warn', '缺少名称');
  if (!/^[a-zA-Z0-9_-]+$/.test(name)) return toast('warn', '格式错误', '仅字母/数字/下划线/连字符');
  var body = {name: name, rate_limit_rpm: rpm};
  if (filterRaw) body.model_filter = filterRaw.split(',').map(function(s){ return s.trim(); }).filter(Boolean);
  try {
    var r = await fetch(BASE + '/v1/admin/public-keys', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    var data = await r.json();
    if (data.error) return toast('error', '创建失败', data.error);
    if (data.key) {
      document.getElementById('accessNewKeyValue').textContent = data.key;
      document.getElementById('accessNewKeyDisplay').style.display = 'block';
      if (data.key_hash) pubKeyCache.set(data.key_hash, data.key);
      toast('warn', '仅此一次', name + ' \u2014 请立即复制保存');
    } else {
      toast('success', '已创建', name);
    }
    document.getElementById('accessKeyName').value = '';
    document.getElementById('accessKeyRpm').value = '60';
    document.getElementById('accessKeyFilter').value = '';
    loadAccess();
  } catch(e) { toast('error', '网络错误', e.message); }
}

async function deleteAccessKey(name) {
  if (!confirm('永久删除 Key "' + name + '"?')) return;
  try {
    var r = await fetch(BASE + '/v1/admin/public-keys/' + encodeURIComponent(name), {method: 'DELETE'});
    var data = await r.json();
    if (data.error) return toast('error', '删除失败', data.error);
    toast('success', '已删除', name);
    loadAccess();
  } catch(e) { toast('error', '网络错误', e.message); }
}

// ===== 操作函数 (占位) =====
function refreshAll() {
  loadAll();
  var activeTab = localStorage.getItem(TAB_KEY) || 'overview';
  if (activeTab === 'models') loadModels();
  if (activeTab === 'providers') loadProviders();
  if (activeTab === 'fusion') loadFusion();
  if (activeTab === 'access') loadAccess();
  toast('info', '刷新中', '正在加载最新数据');
}
function refreshProvidersTab() {
  loadProviders();
  toast('info', '刷新中', '正在加载 Provider 数据');
}
function probeHealthAll() { toast('warn', 'Probe 启动', 'v4.1 Probe 已集成'); }
function exportReport() { toast('success', '导出报告', 'v4.1 待集成'); }
function backupConfig() { toast('success', '备份配置', 'v4.1 待集成'); }
function exportActivity() { toast('success', '导出 CSV', 'v4.1 待集成'); }
function enableAllProviders() { toast('warn', '全部启用', 'v4.1 待集成'); }
function openEditProvider(name) { toast('info', '编辑 Provider', name); }
function refreshProvider(name) { toast('info', '刷新 Provider', name); }
function cloneProvider(name) { toast('info', '复制 Provider', name); }
function disableProvider(name) { toast('warn', '停用 Provider', name); }
function reEnableProvider(name) { toast('success', '启用 Provider', name); }

function onGlobalSearch(q) {
  if (q && q.trim()) {
    switchTab('models');
    setTimeout(function(){
      var search = document.getElementById('modelSearch');
      if (search) {
        search.value = q;
        onModelSearch(q);
      }
    }, 200);
  }
}

// ===== Clipboard =====
function copyToClipboard(text) {
  var done = function(){ toast('success', '已复制', 'Key 在剪贴板'); };
  var fail = function(){ toast('warn', '复制失败', '请手动选中'); };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done, fail);
  } else {
    var ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); done(); } catch(e) { fail(); }
    document.body.removeChild(ta);
  }
}

// ===== Wizard =====
var wizardState = {
  presets: [],
  matchedModels: [],
  selectedPreset: null,
  selectedPaths: new Set(),
  currentFilter: null,
};

function toggleChip(el) { el.classList.toggle('selected'); }

function openWizard() {
  var m = document.getElementById('wizardModal');
  if (m) { m.classList.add('active'); loadWizard(); }
}
function closeWizard() {
  var m = document.getElementById('wizardModal');
  if (m) m.classList.remove('active');
}

async function loadWizard() {
  wizardState.selectedPreset = null;
  wizardState.selectedPaths = new Set();
  try {
    var r = await fetch('/v1/admin/model-groups/wizard/presets');
    var data = await r.json();
    if (data.error) { toast('error', '加载预设失败', data.error); return; }
    wizardState.presets = data.presets || [];
    renderPresetCards();
    fillWizardFilterOptions();
  } catch (e) {
    toast('error', 'wizard API 失败', e.message);
  }
  document.getElementById('wizardModelsList').innerHTML =
    '<div class="empty-state">选一个预设场景 或 自定义筛选查看匹配模型</div>';
  document.getElementById('wizardMatchCount').textContent = '0';
  if (!document.getElementById('wizardGroupName').value) {
    document.getElementById('wizardGroupName').value = 'my-group-' + Date.now().toString(36);
  }
}

function renderPresetCards() {
  var grid = document.getElementById('wizardPresetsGrid');
  grid.innerHTML = '';
  wizardState.presets.forEach(function(p){
    var card = document.createElement('div');
    card.className = 'wizard-preset-card';
    card.dataset.presetId = p.id;
    if (p.current_match_count === 0) card.classList.add('disabled');
    var countClass = p.current_match_count === 0 ? 'preset-count zero' : 'preset-count';
    card.innerHTML =
      '<div class="preset-icon">' + (p.icon || '\u{1F3AF}') + '</div>' +
      '<div class="preset-name">' + escapeHtml(p.name || p.id) + '</div>' +
      '<div class="preset-desc">' + escapeHtml(p.description || '') + '</div>' +
      '<div class="' + countClass + '">' + (p.current_match_count || 0) + ' 个模型</div>';
    card.onclick = function() {
      if (card.classList.contains('disabled')) return;
      document.querySelectorAll('.wizard-preset-card').forEach(function(c){ c.classList.remove('selected'); });
      card.classList.add('selected');
      wizardState.selectedPreset = p;
      applyWizardPreset(p);
    };
    grid.appendChild(card);
  });
}

function fillWizardFilterOptions() {
  var providers = new Set();
  var healthDict = (window._lastHealth && window._lastHealth.providers) || {};
  Object.keys(healthDict).forEach(function(name){ providers.add(name); });
  if (providers.size === 0 && window._lastProviders) {
    var list = Array.isArray(window._lastProviders) ? window._lastProviders : (window._lastProviders.providers || []);
    list.forEach(function(p){ providers.add(typeof p === 'string' ? p : p.name); });
  }
  var provEl = document.getElementById('wizardFilterProviders');
  provEl.innerHTML = [...providers].map(function(p){
    return '<span class="chip" data-value="' + escapeHtml(p) + '" onclick="toggleChip(this)">' + escapeHtml(p) + '</span>';
  }).join('') || '<span style="color:var(--text-3);font-size:11px">无 provider</span>';
  var tags = ['reasoning', 'coding', 'vision', 'fast', 'long-context', 'tools', 'multimodal'];
  document.getElementById('wizardFilterTags').innerHTML = tags.map(function(t){
    return '<span class="chip" data-value="' + t + '" onclick="toggleChip(this)">' + t + '</span>';
  }).join('');
}

async function applyWizardPreset(p) {
  try {
    var r = await fetch('/v1/admin/model-groups/from-wizard', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({preset: p.id, dry_run: true}),
    });
    var data = await r.json();
    if (data.error) { toast('error', 'preset 应用失败', data.error); return; }
    wizardState.matchedModels = (data.resolved_models || data.matched_models || []).map(function(m){
      return {
        id: m.model_id || m.id,
        path: m.path || ((m.provider || '') + '/' + (m.model_id || m.id)),
        provider: m.provider || '',
      };
    });
    wizardState.currentFilter = data.filter || null;
    renderWizardModels();
    toast('success', '预设已应用', wizardState.matchedModels.length + ' 个匹配模型');
  } catch (e) {
    toast('error', 'preset API 失败', e.message);
  }
}

async function applyWizardFilter() {
  var providers = [...document.querySelectorAll('#wizardFilterProviders .chip.selected')].map(function(c){ return c.dataset.value; });
  var context = parseInt(document.getElementById('wizardFilterContext').value) || 0;
  var quality = parseInt(document.getElementById('wizardFilterQuality').value) || 0;
  var speed = parseInt(document.getElementById('wizardFilterSpeed').value) || 0;
  var modality = document.getElementById('wizardFilterModality').value || '';
  var tags = [...document.querySelectorAll('#wizardFilterTags .chip.selected')].map(function(c){ return c.dataset.value; });
  var filter = {
    providers: providers.length ? providers : null,
    context_min: context || null,
    quality_min: quality || null,
    speed_min: speed || null,
    modality: modality || null,
    tags: tags.length ? tags : null,
  };
  wizardState.currentFilter = filter;
  try {
    var r = await fetch('/v1/admin/model-groups/from-filter', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(Object.assign({}, filter, {dry_run: true})),
    });
    var data = await r.json();
    if (data.error) { toast('error', '筛选失败', data.error); return; }
    wizardState.matchedModels = (data.resolved_models || data.matched_models || []).map(function(m){
      return {
        id: m.model_id || m.id,
        path: m.path || ((m.provider || '') + '/' + (m.model_id || m.id)),
        provider: m.provider || '',
      };
    });
    renderWizardModels();
    toast('success', '筛选已应用', wizardState.matchedModels.length + ' 个匹配');
  } catch (e) {
    toast('error', 'filter API 失败', e.message);
  }
}

function resetWizardFilter() {
  document.querySelectorAll('#wizardFilterProviders .chip, #wizardFilterTags .chip').forEach(function(c){ c.classList.remove('selected'); });
  document.getElementById('wizardFilterContext').value = '0';
  document.getElementById('wizardFilterQuality').value = '0';
  document.getElementById('wizardFilterSpeed').value = '0';
  document.getElementById('wizardFilterModality').value = '';
  document.getElementById('qualityVal').textContent = '0';
  document.getElementById('speedVal').textContent = '0';
  document.querySelectorAll('.wizard-preset-card').forEach(function(c){ c.classList.remove('selected'); });
  wizardState.selectedPreset = null;
  wizardState.matchedModels = [];
  wizardState.currentFilter = null;
  document.getElementById('wizardModelsList').innerHTML = '<div class="empty-state">选一个预设场景 或 自定义筛选查看匹配模型</div>';
  document.getElementById('wizardMatchCount').textContent = '0';
  toast('info', '筛选已重置');
}

function renderWizardModels() {
  var list = wizardState.matchedModels;
  document.getElementById('wizardMatchCount').textContent = list.length;
  if (list.length === 0) {
    document.getElementById('wizardModelsList').innerHTML = '<div class="empty-state">无匹配模型</div>';
    return;
  }
  document.getElementById('wizardModelsList').innerHTML = list.map(function(m){
    var checked = wizardState.selectedPaths.has(m.path) ? 'checked' : '';
    return '<label class="model-row">' +
      '<input type="checkbox" ' + checked + ' onchange="toggleWizardPath(\'' + escapeHtml(m.path) + '\', this.checked)">' +
      '<span style="flex:1;font-family:var(--mono);font-size:11px">' + escapeHtml(m.id || m.path) + '</span>' +
      '<span style="color:var(--text-2);font-size:10px">' + escapeHtml(m.provider || '') + '</span>' +
    '</label>';
  }).join('');
}

function toggleWizardPath(path, checked) {
  if (checked) wizardState.selectedPaths.add(path);
  else wizardState.selectedPaths.delete(path);
}

function wizardSelectAll() {
  wizardState.matchedModels.forEach(function(m){ wizardState.selectedPaths.add(m.path); });
  renderWizardModels();
  toast('info', '已全选 ' + wizardState.matchedModels.length + ' 个');
}
function wizardSelectNone() {
  wizardState.selectedPaths.clear();
  renderWizardModels();
  toast('info', '已清空选择');
}

async function previewWizardGroup() {
  if (!wizardState.currentFilter) { toast('warn', '请先应用筛选或选预设'); return; }
  toast('info', '预览生成', wizardState.matchedModels.length + ' 个匹配 / ' + wizardState.selectedPaths.size + ' 个已选');
}

async function generateWizardGroup() {
  var groupName = document.getElementById('wizardGroupName').value.trim();
  if (!groupName) { toast('warn', '请填分组名'); return; }
  if (wizardState.selectedPaths.size === 0) { toast('warn', '请至少选一个模型'); return; }
  var payload = {
    name: groupName,
    strategy: document.getElementById('wizardGroupStrategy').value,
    paths: [...wizardState.selectedPaths],
    filter: wizardState.currentFilter,
    create_api_key: document.getElementById('wizardCreateApiKey').checked,
    api_key_name: document.getElementById('wizardApiKeyName').value.trim() || (groupName + '-key'),
  };
  try {
    var r = await fetch('/v1/admin/model-groups/from-filter', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    var data = await r.json();
    if (data.error) { toast('error', '生成失败', data.error); return; }
    showWizardResult(data);
    toast('success', '分组生成成功', groupName);
  } catch (e) {
    toast('error', 'API 失败', e.message);
  }
}

function showWizardResult(data) {
  var panel = document.getElementById('wizardResultPanel');
  var content = document.getElementById('wizardResultContent');
  var html = '<div style="font-family:var(--mono);font-size:12px;color:var(--text-0)">' +
    '<div><b>分组名:</b> ' + escapeHtml(data.name || '') + '</div>' +
    '<div><b>匹配数:</b> ' + (data.matched_count || wizardState.matchedModels.length) + '</div>' +
    '<div><b>策略:</b> ' + escapeHtml(data.strategy || '') + '</div>' +
    (data.description ? '<div style="margin-top:8px;color:var(--text-1)">' + escapeHtml(data.description) + '</div>' : '') +
  '</div>';
  if (data.api_key) {
    html += '<div style="margin-top:var(--space-3);padding-top:var(--space-3);border-top:1px solid var(--success)">' +
      '<strong style="color:var(--success)">API Key (仅显示这一次!)</strong>' +
      '<div style="margin-top:6px;padding:var(--space-2);background:var(--bg-0);border-radius:var(--radius-sm);font-family:var(--mono);font-size:12px;word-break:break-all;color:var(--warn)">' +
        escapeHtml(data.api_key.key) +
      '</div>' +
      '<div style="margin-top:6px;font-size:11px;color:var(--text-2)">name: <code>' + escapeHtml(data.api_key.name) + '</code> \u00B7 hash: <code>' + escapeHtml(data.api_key.key_hash || '') + '</code></div>' +
    '</div>';
  }
  content.innerHTML = html;
  panel.style.display = 'block';
  panel.scrollIntoView({behavior: 'smooth', block: 'nearest'});
}

// ===== Provider Keys Modal =====
var showFullProviderKeys = false;
var showFullPublicKeys = false;
var pubKeyCache = new Map();

function openProviderKeys() {
  var m = document.getElementById('providerKeysModal');
  if (m) m.classList.add('active');
  loadProviderKeys();
}
function closeProviderKeys() {
  var m = document.getElementById('providerKeysModal');
  if (m) m.classList.remove('active');
}
function openPublicKeys() {
  var m = document.getElementById('publicKeysModal');
  if (m) m.classList.add('active');
  loadPublicKeys();
}
function closePublicKeys() {
  var m = document.getElementById('publicKeysModal');
  if (m) m.classList.remove('active');
  var disp = document.getElementById('ppkNewKeyDisplay');
  if (disp) disp.style.display = 'none';
  showFullPublicKeys = false;
  var btn = document.getElementById('pubKeyToggleBtn');
  if (btn) btn.textContent = '显示完整';
}

async function toggleProviderKeyVisibility() {
  showFullProviderKeys = !showFullProviderKeys;
  var btn = document.getElementById('pkToggleBtn');
  if (btn) btn.textContent = showFullProviderKeys ? '隐藏' : '显示完整';
  await loadProviderKeys();
}

async function togglePublicKeyVisibility() {
  showFullPublicKeys = !showFullPublicKeys;
  var btn = document.getElementById('pubKeyToggleBtn');
  if (btn) btn.textContent = showFullPublicKeys ? '隐藏' : '显示完整';
  await loadPublicKeys();
}

async function loadProviderKeys() {
  var list = document.getElementById('providerKeysList');
  list.innerHTML = '<div class="empty-state">\u23F3 加载中...</div>';
  var results = await Promise.all([
    fetchJSON('/v1/admin/api-keys' + (showFullProviderKeys ? '?show_full_keys=true' : '')),
    fetchJSON('/v1/admin/providers?include_disabled=true'),
  ]);
  var keysData = results[0], provData = results[1];
  var sel = document.getElementById('pkProviderSel');
  sel.innerHTML = '<option value="">选择 provider...</option>';
  var provs = (provData && provData.providers) || {};
  Object.keys(provs).sort().forEach(function(name){
    sel.innerHTML += '<option value="' + escapeHtml(name) + '">' + escapeHtml(name) + '</option>';
  });
  if (!keysData || !keysData.keys || !keysData.keys.length) {
    list.innerHTML = '<div class="empty-state">无 Provider Key</div>';
    return;
  }
  var html = '<table class="data-table" style="width:100%;font-size:13px"><thead><tr><th>Provider</th><th>数</th><th>预览</th><th>指纹</th><th></th></tr></thead><tbody>';
  keysData.keys.forEach(function(k){
    var prev = (k.preview || []).map(function(p){
      var safe = escapeHtml(p);
      if (showFullProviderKeys) {
        return '<code style="font-size:11px;word-break:break-all;cursor:pointer" onclick="copyToClipboard(this.textContent)" title="点击复制完整 key">' + safe + '</code>';
      }
      return '<code style="font-size:11px">' + safe + '</code>';
    }).join('<br>') || '\u2014';
    var fp = k.fingerprint ? '<code style="font-size:11px">' + escapeHtml(k.fingerprint.slice(0, 12)) + '...</code>' : '\u2014';
    html += '<tr><td><b>' + escapeHtml(k.provider) + '</b>' + (k.enabled === false ? ' \u23F8' : '') + '</td><td>' + k.count + '</td><td>' + prev + '</td><td>' + fp + '</td><td><button class="btn ghost sm" onclick="deleteProviderKey(\'' + escapeHtml(k.provider) + '\')">\u{1F5D1} 清空</button></td></tr>';
  });
  list.innerHTML = html + '</tbody></table>';
}

async function addProviderKey() {
  var provider = document.getElementById('pkProviderSel').value.trim();
  var apiKey = document.getElementById('pkNewKey').value.trim();
  if (!provider || !apiKey) return toast('warn', '缺少信息', 'Provider + Key 都要填');
  try {
    var r = await fetch(BASE + '/v1/admin/api-keys', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({provider: provider, api_key: apiKey}),
      signal: AbortSignal.timeout(10000),
    });
    var data = await r.json();
    if (data.error) return toast('error', '添加失败', data.error);
    toast('success', '已添加', provider + ' 现有 ' + (data.count != null ? data.count : '?') + ' 个 key');
    document.getElementById('pkNewKey').value = '';
    loadProviderKeys();
  } catch (e) { toast('error', '网络错误', e.message); }
}

async function deleteProviderKey(provider) {
  if (!confirm('清空 ' + provider + ' 的全部 key? (不可恢复)')) return;
  try {
    var r = await fetch(BASE + '/v1/admin/api-keys/' + encodeURIComponent(provider), {method: 'DELETE', signal: AbortSignal.timeout(10000)});
    var data = await r.json();
    if (data.error) return toast('error', '删除失败', data.error);
    toast('success', '已清空', provider);
    loadProviderKeys();
  } catch (e) { toast('error', '网络错误', e.message); }
}

async function loadPublicKeys() {
  var list = document.getElementById('publicKeysList');
  list.innerHTML = '<div class="empty-state">\u23F3 加载中...</div>';
  var data = await fetchJSON('/v1/admin/public-keys');
  if (!data || !data.keys || !data.keys.length) {
    list.innerHTML = '<div class="empty-state">无 Public Key</div>';
    return;
  }
  var headLabel = showFullPublicKeys ? '完整 Key / 哈希' : '哈希';
  var html = '<table class="data-table" style="width:100%;font-size:13px"><thead><tr><th>名称</th><th>' + headLabel + '</th><th>RPM</th><th>过滤</th><th>末次</th><th></th></tr></thead><tbody>';
  data.keys.forEach(function(k){
    var hashShort = k.key_hash ? escapeHtml(k.key_hash.slice(0, 16)) + '...' : '\u2014';
    var keyCell;
    if (showFullPublicKeys) {
      var raw = k.key_hash ? pubKeyCache.get(k.key_hash) : null;
      if (raw) {
        keyCell = '<code style="font-size:11px;word-break:break-all;cursor:pointer;color:var(--success)" onclick="copyToClipboard(this.textContent)" title="点击复制完整 key"></code>';
      } else {
        keyCell = '<code style="font-size:11px">' + hashShort + '</code> <span style="font-size:10px;color:var(--warn)" title="Public Key 仅创建时返回一次">已丢失</span>';
      }
    } else {
      keyCell = '<code style="font-size:11px">' + hashShort + '</code>';
    }
    var filter = Array.isArray(k.model_filter) && k.model_filter.length
      ? (k.model_filter.length > 3 ? k.model_filter.slice(0,3).join(', ') + '...' : k.model_filter.join(', '))
      : '全部';
    var last = k.last_used ? new Date(k.last_used * 1000).toLocaleString() : '\u2014';
    html += '<tr>' +
      '<td><b>' + escapeHtml(k.name) + '</b>' + (k.enabled === false ? ' \u23F8' : '') + '</td>' +
      '<td>' + keyCell + '</td>' +
      '<td>' + (k.rate_limit_rpm != null ? k.rate_limit_rpm : 60) + '</td>' +
      '<td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px" title="' + escapeHtml(filter) + '">' + escapeHtml(filter) + '</td>' +
      '<td style="font-size:11px">' + escapeHtml(last) + '</td>' +
      '<td><button class="btn ghost sm" onclick="deletePublicKey(\'' + escapeHtml(k.name) + '\')">\u{1F5D1}</button></td>' +
    '</tr>';
  });
  list.innerHTML = html + '</tbody></table>';
  if (showFullPublicKeys) {
    var rows = list.querySelectorAll('tbody tr');
    data.keys.forEach(function(k, idx){
      var raw = k.key_hash ? pubKeyCache.get(k.key_hash) : null;
      if (raw && rows[idx]) {
        var codeEl = rows[idx].querySelector('td:nth-child(2) code');
        if (codeEl) codeEl.textContent = raw;
      }
    });
  }
}

async function createPublicKey() {
  var name = document.getElementById('ppkName').value.trim();
  var rpm = parseInt(document.getElementById('ppkRpm').value) || 60;
  var filterRaw = document.getElementById('ppkFilter').value.trim();
  if (!name) return toast('warn', '缺少名称', 'Name 必填');
  if (!/^[a-zA-Z0-9_-]+$/.test(name)) return toast('warn', '格式错误', '仅字母/数字/下划线/连字符');
  var body = { name: name, rate_limit_rpm: rpm };
  if (filterRaw) body.model_filter = filterRaw.split(',').map(function(s){ return s.trim(); }).filter(Boolean);
  try {
    var r = await fetch(BASE + '/v1/admin/public-keys', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(10000),
    });
    var data = await r.json();
    if (data.error) return toast('error', '创建失败', data.error);
    if (data.key) {
      document.getElementById('ppkNewKeyValue').textContent = data.key;
      document.getElementById('ppkNewKeyDisplay').style.display = 'block';
      if (data.key_hash) pubKeyCache.set(data.key_hash, data.key);
      toast('warn', '仅此一次', name + ' \u2014 请立即复制保存');
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
  if (!confirm('永久删除 Key "' + name + '"? (不可恢复, 立即失效)')) return;
  try {
    var r = await fetch(BASE + '/v1/admin/public-keys/' + encodeURIComponent(name), {method: 'DELETE', signal: AbortSignal.timeout(10000)});
    var data = await r.json();
    if (data.error) return toast('error', '删除失败', data.error);
    toast('success', '已删除', name);
    loadPublicKeys();
  } catch (e) { toast('error', '网络错误', e.message); }
}

// ===== 启动 =====
applyTheme();

(function(){
  var t = new URLSearchParams(location.search).get('theme');
  if (t && THEME_ORDER.indexOf(t) >= 0) {
    localStorage.setItem(THEME_KEY, t);
    applyTheme(t);
  }
})();

window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function(){
  if (getTheme() === 'system') applyTheme('system');
});

document.addEventListener('DOMContentLoaded', function(){
  initTabs();
  loadAll();
});

// 30s 自动刷新 + 倒计时
var _refreshCountdown = 30;
setInterval(function(){
  var next = document.getElementById('nextRefresh');
  if (next) {
    _refreshCountdown = _refreshCountdown > 0 ? _refreshCountdown - 5 : 30;
    next.textContent = _refreshCountdown + 's';
  }
}, 5000);
setInterval(function(){ loadAll(); _refreshCountdown = 30; }, 30000);

// 全局快捷键
document.addEventListener('keydown', function(e){
  if ((e.metaKey || e.ctrlKey) && e.key === 'k' && !e.shiftKey) {
    e.preventDefault();
    var s = document.getElementById('globalSearch');
    if (s) s.focus();
  }
  if ((e.metaKey || e.ctrlKey) && e.shiftKey && (e.key === 'l' || e.key === 'L')) {
    e.preventDefault();
    cycleTheme();
  }
  if (e.key === 'Escape') { closeWizard(); closeProviderKeys(); closePublicKeys(); closeFusionTestModal(); }
});

</script>

</body>
</html>"""


# ============================================================
# Admin Page 路由
# ============================================================

@router.get("/admin", response_class=HTMLResponse)
@router.get("/admin/", response_class=HTMLResponse)
async def admin_page():
    """v4.1: 5-Tab Dashboard + Fusion Panel + Config Consolidation"""
    try:
        from .version import VERSION as _V
        html = ADMIN_HTML.replace("__SMR_VERSION__", _V)
    except Exception:
        html = ADMIN_HTML.replace("__SMR_VERSION__", "v4.1.0")
    return HTMLResponse(content=html)


@router.get("/admin/9-gong", response_class=HTMLResponse)
async def admin_9gong():
    """v3.11 集成 v0.9: 派活 dashboard 8 卦布局"""
    from pathlib import Path as P
    dashboard_path = P(__file__).parent / "static" / "dashboard-9gong.html"
    if not dashboard_path.exists():
        return HTMLResponse("<h1>8 卦 dashboard HTML 缺</h1><p>需要复制到 static/dashboard-9gong.html</p>", status_code=500)
    return HTMLResponse(dashboard_path.read_text(encoding='utf-8'))


@router.get("/admin/guide", response_class=HTMLResponse)
async def admin_guide_page():
    """v3.28 SMR Admin 使用指引页"""
    try:
        from .version import VERSION as _V
        from .admin_ui_guide import GUIDE_HTML
        return HTMLResponse(content=GUIDE_HTML.replace("__SMR_VERSION__", _V))
    except Exception:
        return HTMLResponse("<h1>Guide page unavailable</h1><p>admin_ui_guide module not found.</p>", status_code=500)
