"""
supermodel_router/admin_ui.py — Dashboard HTML + admin_page 路由 (v3.2.0 拆分)

- ADMIN_HTML: 完整 dashboard HTML/CSS/JS 字符串 (v3.2.0 加 📜 配置历史 tab)
- /admin 与 /admin/: 返回 ADMIN_HTML
"""
import logging
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

LOG = logging.getLogger("admin_ui")
router = APIRouter()


# ============================================================
# 管理 API
# ============================================================

ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Model Router v3 Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
body{background:#0f0f13;color:#e0e0e0;padding:20px;max-width:1200px;margin:0 auto}
h1{font-size:24px;margin-bottom:20px}
h2{font-size:16px;margin:20px 0 10px;color:#888}
.status-bar{display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap}
.stat-card{background:#1a1a24;border-radius:8px;padding:14px 18px;min-width:140px;flex:1}
.stat-card .label{font-size:11px;color:#666;text-transform:uppercase}
.stat-card .value{font-size:22px;font-weight:600;margin-top:4px}
.uptime{color:#888;font-size:13px}
.provider-grid{display:grid;gap:10px}
.provider-card{background:#1a1a24;border-radius:8px;padding:14px 18px;display:flex;justify-content:space-between;align-items:center}
.provider-name{font-weight:600;font-size:15px}
.provider-badge{font-size:11px;padding:3px 8px;border-radius:4px}
.badge-ok{background:#0d3b1e;color:#4ade80}
.badge-degraded{background:#3b1d0d;color:#fbbf24}
.badge-down{background:#3b0d0d;color:#f87171}
.modality-grid{display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap}
.modality-card{background:#1a1a24;border-radius:8px;padding:12px 16px;min-width:100px;text-align:center;flex:1}
.modality-card .emoji{font-size:24px;margin-bottom:4px}
.modality-card .count{font-size:18px;font-weight:600}
.modality-card .label{font-size:11px;color:#666}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 10px;color:#666;border-bottom:1px solid #222}
td{padding:8px 10px;border-bottom:1px solid #1a1a1a}
tr:hover td{background:#1a1a24}
.provider-tag{display:inline-block;font-size:11px;padding:2px 6px;border-radius:3px;background:#1e293b;color:#94a3b8;margin-right:4px}
.modality-tag{display:inline-block;font-size:11px;padding:2px 6px;border-radius:3px;margin-right:4px;font-weight:500}
.modality-text-only{background:#1e293b;color:#94a3b8}
.modality-multimodal{background:#1a1a3b;color:#818cf8}
.modality-image-gen{background:#1a3b1a;color:#4ade80}
.modality-video-gen{background:#3b1a3b;color:#c084fc}
.modality-audio-gen{background:#3b2a1a;color:#fbbf24}

/* v3.6.0: Keys 视图表格 ──────────────────────── */
.kv-table{width:100%;border-collapse:collapse;margin-top:10px}
.kv-table th,.kv-table td{padding:8px 12px;text-align:left;border-bottom:1px solid #2a3a4a;font-size:13px}
.kv-table th{background:#1a2530;color:#9ca3af;font-weight:600;text-transform:uppercase;font-size:11px}
.kv-table tr:hover td{background:#1a2530}
.kv-table .actions button{margin-right:4px}
.kv-table code{background:#0f1a24;padding:2px 6px;border-radius:3px;font-size:11px;font-family:'SF Mono',Consolas,monospace;color:#4ade80}
.kv-table code.fp{color:#fbbf24}
.badge-green{background:#1a4a2a;color:#4ade80;padding:2px 8px;border-radius:3px;font-size:11px;font-weight:600}
.badge-gray{background:#3a3a3a;color:#888;padding:2px 8px;border-radius:3px;font-size:11px;font-weight:600}
.btn-danger{background:#7a2a2a!important;border-color:#a04040!important}
.btn-danger:hover{background:#a04040!important}
.score-bar{display:inline-block;height:6px;border-radius:3px;background:#2563eb;margin-right:6px;vertical-align:middle}
.btn{background:#2563eb;color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:13px}
.btn:hover{background:#1d4ed8}
.btn-sm{background:#1a1a24;color:#e0e0e0;border:1px solid #333;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:11px}
.btn-sm:hover{background:#333}
.toolbar{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
.toast{position:fixed;bottom:20px;right:20px;background:#1a1a24;border:1px solid #333;padding:12px 20px;border-radius:8px;font-size:13px;display:none;z-index:999}
.toast.show{display:block}
.hidden{display:none!important}
.loading{color:#666;text-align:center;padding:30px}
.modality-filter{display:flex;gap:4px;margin-bottom:8px;flex-wrap:wrap}
.modality-filter button{padding:4px 10px;border-radius:12px;border:1px solid #333;background:transparent;color:#888;cursor:pointer;font-size:12px}
.modality-filter button.active{background:#2563eb;color:#fff;border-color:#2563eb}
.modal-bg{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.6);display:none;align-items:center;justify-content:center;z-index:100}
.modal-bg.show,.modal-bg.open{display:flex}
.modal{background:#1a1a24;border-radius:10px;padding:20px;max-width:600px;width:90%;max-height:80vh;overflow:auto}
.modal h3{margin-bottom:12px;font-size:16px}
.modal-overlay{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:10000}
.modal-overlay .modal{background:#1a1a24;border-radius:10px;padding:24px;max-width:650px;width:90%;max-height:85vh;overflow:auto;border:1px solid #333;box-shadow:0 8px 32px rgba(0,0,0,0.5)}

/* v3.10.0 (Phase L): Wizard 样式 */
.wizard-presets-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px;margin-bottom:20px}
.wizard-preset-card{background:#0f0f13;border:1px solid #1a1a24;border-radius:8px;padding:14px;cursor:pointer;transition:all .15s ease;position:relative}
.wizard-preset-card:hover{border-color:#5b8def;background:#1a1a24;transform:translateY(-1px);box-shadow:0 4px 12px rgba(91,141,239,0.15)}
.wizard-preset-card.selected{border-color:#4ade80;background:#0f1f0f}
.wizard-preset-card.disabled{opacity:0.4;cursor:not-allowed}
.wizard-preset-card .preset-icon{font-size:24px;margin-bottom:6px}
.wizard-preset-card .preset-name{font-size:13px;font-weight:500;color:#e0e0e0;margin-bottom:4px}
.wizard-preset-card .preset-desc{font-size:11px;color:#888;line-height:1.4;margin-bottom:6px}
.wizard-preset-card .preset-count{position:absolute;top:8px;right:10px;font-size:10px;background:#1a1a24;padding:2px 8px;border-radius:10px;color:#5b8def;font-weight:500}
.wizard-preset-card .preset-count.zero{background:#2a1a1a;color:#dc2626}
.wizard-filter-panel{background:#0f0f13;border:1px solid #1a1a24;border-radius:8px;padding:16px;margin-bottom:20px}
.filter-row{margin-bottom:12px}
.filter-row label{display:block;font-size:12px;color:#888;margin-bottom:6px}
.filter-row .filter-input,.filter-row .filter-select{background:#0a0a0d;border:1px solid #333;color:#e0e0e0;padding:8px 12px;border-radius:6px;width:100%;font-size:13px}
.filter-row input[type="range"]{width:60%;display:inline-block;vertical-align:middle}
.chip-group{display:flex;flex-wrap:wrap;gap:6px}
.chip{background:#1a1a24;border:1px solid #333;color:#a0a0b0;padding:5px 12px;border-radius:14px;font-size:11px;cursor:pointer;transition:all .12s ease;user-select:none}
.chip:hover{border-color:#5b8def;color:#e0e0e0}
.chip.selected{background:#5b8def;color:#fff;border-color:#5b8def}
.wizard-models-list{background:#0f0f13;border:1px solid #1a1a24;border-radius:8px;padding:12px;max-height:420px;overflow-y:auto;margin-bottom:20px}
.wizard-model-row{display:flex;align-items:center;padding:10px 12px;border-radius:6px;transition:background .12s ease;border:1px solid transparent;margin-bottom:4px}
.wizard-model-row:hover{background:#1a1a24}
.wizard-model-row.selected{border-color:#4ade80;background:#0f1f0f}
.wizard-model-row input[type="checkbox"]{margin-right:12px;cursor:pointer;width:16px;height:16px}
.wizard-model-row .model-info{flex:1;min-width:0}
.wizard-model-row .model-path{font-size:13px;font-weight:500;color:#e0e0e0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.wizard-model-row .model-meta{font-size:10px;color:#888;margin-top:3px;display:flex;gap:10px;flex-wrap:wrap}
.wizard-model-row .meta-chip{background:#1a1a24;padding:1px 6px;border-radius:8px;font-size:10px}
.wizard-model-row .meta-chip.tag{background:#2a2440;color:#a78bfa}
.wizard-generate-panel{background:#0f0f13;border:1px solid #1a1a24;border-radius:8px;padding:16px}
.empty-state{padding:40px 20px;text-align:center;color:#666;font-size:13px}
.wizard-models-list .empty-state{padding:20px}
.version-info{margin:16px 0}
.version-row{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #222}
.version-row:last-child{border-bottom:none}
.version-row strong{font-size:14px}
.version-release-notes{margin-top:16px;padding-top:12px;border-top:1px solid #333}
.version-release-notes h3{font-size:14px;color:#888;margin-bottom:8px}
.version-release-notes pre{background:#0d0d12;padding:12px;border-radius:6px;font-size:12px;white-space:pre-wrap;line-height:1.6;max-height:200px;overflow-y:auto}
.version-upgrade{margin-top:16px;padding:12px;background:#1a1a24;border-radius:6px;border-left:3px solid #ff9800}
.version-upgrade pre{font-size:12px}
.modal label{display:block;font-size:12px;color:#888;margin-top:10px;margin-bottom:4px}
.modal input,.modal select,.modal textarea{width:100%;background:#0f0f13;border:1px solid #333;color:#e0e0e0;padding:8px 10px;border-radius:4px;font-size:13px;font-family:inherit}
.modal textarea{min-height:80px;font-family:ui-monospace,monospace;font-size:12px}
.modal .row{display:flex;gap:10px;margin-top:14px}
.modal .btn{flex:1}
.danger{background:#dc2626}
.danger:hover{background:#b91c1c}
.text-muted{color:#666;font-size:11px}
.kv-edit{display:flex;gap:6px;margin-bottom:6px;align-items:center}
.kv-edit input{flex:1}
.kv-edit button{padding:4px 8px}

/* v3.6 UI/UX ──────────────────────── */
.section-header{display:flex;justify-content:space-between;align-items:center;margin:18px 0 12px}
.provider-toolbar{display:flex;gap:8px;align-items:center}
.filter-select{background:#0f0f13;border:1px solid #333;color:#e0e0e0;padding:6px 10px;border-radius:4px;font-size:12px;cursor:pointer}
.filter-select:hover{border-color:#555}
.filter-select:focus{outline:none;border-color:#5b8def}
.provider-card{transition:all .2s ease,box-shadow .2s ease}
.provider-card:hover{background:#1f1f2e;box-shadow:0 4px 16px rgba(91,141,239,0.12);transform:translateY(-1px)}
.provider-card.disabled{opacity:0.55;background:#0f0f13}
.provider-card.disabled:hover{transform:none;box-shadow:none}
.provider-info{flex:1;min-width:0}
.provider-name{font-weight:600;font-size:14px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.provider-tag{background:#0f0f13;border:1px solid #333;padding:1px 6px;border-radius:3px;font-size:10px;color:#94a3b8;font-weight:normal}
.provider-tag[title*="内置"]{border-color:#5b8def;color:#5b8def}
.provider-url{font-size:11px;color:#666;margin-top:4px;font-family:ui-monospace,monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.provider-meta{display:flex;gap:12px;margin-top:6px;font-size:11px;color:#888}
.provider-actions{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.provider-badge{font-size:10px;padding:2px 8px;border-radius:10px;font-weight:500}
.badge-ok{background:#0d3d24;color:#4ade80}
.badge-disabled{background:#3a1a1a;color:#f87171}
.badge-degraded{background:#3d2e0d;color:#fbbf24}
.empty-state{text-align:center;padding:30px 20px;color:#666;background:#0f0f13;border:1px dashed #333;border-radius:8px;margin:10px 0}
.btn-sm.primary{background:#1e40af;color:#e0e0e0;border-color:#1e40af}
.btn-sm.primary:hover{background:#2563eb;border-color:#2563eb}
.btn-sm:disabled{opacity:0.5;cursor:not-allowed}
.pattern-builder{background:#0f0f13;padding:10px;border-radius:4px;border:1px solid #333}
.pattern-row{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.pattern-row:last-child{margin-bottom:0}
.pattern-op{flex:0 0 60px;font-size:12px;color:#5b8def;font-weight:500}
.pattern-row input{flex:1;background:#1a1a24;border:1px solid #333;color:#e0e0e0;padding:5px 8px;border-radius:3px;font-size:12px}
.pattern-hint{margin-top:8px;font-size:11px;color:#888}
.pattern-hint code{background:#0d0d12;padding:2px 6px;border-radius:3px;font-family:ui-monospace,monospace;color:#4ade80;font-size:11px;display:inline-block;max-width:100%;overflow-x:auto}
.pattern-raw{margin-top:6px;font-size:11px;color:#888}
.pattern-raw summary{cursor:pointer;color:#5b8def}
.pattern-raw input{width:100%;background:#0d0d12;border:1px solid #333;color:#e0e0e0;padding:5px 8px;border-radius:3px;font-size:12px;margin-top:4px;font-family:ui-monospace,monospace}
.route-item{display:flex;justify-content:space-between;align-items:center;padding:6px 10px;background:#0f0f13;border-radius:4px;margin-bottom:4px;font-size:12px;font-family:ui-monospace,monospace}
.route-path{color:#e0e0e0}
.route-pricing{font-size:10px;padding:1px 6px;border-radius:3px;font-weight:500;background:#1a1a24}
.group-card{background:linear-gradient(135deg,#111827 0%,#0f172a 100%);border:1px solid #263244;border-radius:12px;padding:14px 16px;margin-bottom:10px;box-shadow:0 8px 22px rgba(0,0,0,.22);transition:transform .16s ease,border-color .16s ease,box-shadow .16s ease}
.group-card:hover{transform:translateY(-1px);border-color:#5b8def;box-shadow:0 12px 30px rgba(91,141,239,.12)}
.group-card-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
.group-title{font-size:15px;font-weight:700;color:#e5e7eb;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.group-count{display:inline-flex;align-items:center;gap:4px;background:#172554;color:#93c5fd;border:1px solid #1d4ed8;padding:3px 9px;border-radius:999px;font-size:11px;font-weight:700}
.group-desc{font-size:12px;color:#94a3b8;margin-top:5px;max-width:760px;line-height:1.5}
.group-patterns{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.group-patterns code,.group-samples code{background:#020617;border:1px solid #1f2937;color:#bfdbfe;padding:2px 6px;border-radius:5px;font-size:11px}
.group-samples{margin-top:9px;font-size:12px;color:#94a3b8;display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.group-actions{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}
.modal-bg.show .modal,.modal-bg.open .modal{animation:modalPop .16s ease-out}
@keyframes modalPop{from{opacity:.6;transform:translateY(8px) scale(.98)}to{opacity:1;transform:none}}
/* v3.6 动效: toast 滑入 */
.toast{transition:all .3s ease}
/* v3.6 模态动效 */
.modal-bg{transition:opacity .2s ease}
.modal{transition:transform .2s ease}

/* v3.14.0: key 弹窗 + 复制按钮 + 警告框 (修"无法复制" + UI 强化) */
.key-notice-box{background:linear-gradient(135deg,#3b0d0d 0%,#1a1a24 100%);border:1px solid #ef4444;border-radius:8px;padding:12px 16px;margin-bottom:14px;display:flex;align-items:flex-start;gap:10px}
.key-notice-icon{font-size:24px;line-height:1}
.key-notice-title{color:#fca5a5;font-weight:600;margin-bottom:2px;font-size:14px}
.key-notice-desc{color:#fecaca;font-size:12px;line-height:1.5}
.key-display-box{background:#020617;border:1px solid #334155;border-radius:6px;padding:14px 16px;margin:8px 0;font-family:ui-monospace,'SF Mono','Monaco',monospace;font-size:14px;color:#4ade80;word-break:break-all;user-select:all;cursor:text;line-height:1.6}
.key-display-box:focus{outline:2px solid #5b8def;outline-offset:2px}
.key-action-row{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
.btn-copy{background:#1e40af;color:#fff;border:1px solid #3b5bdb;padding:9px 18px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:6px;transition:all .15s ease}
.btn-copy:hover{background:#2563eb;transform:translateY(-1px);box-shadow:0 4px 12px rgba(37,99,235,.4)}
.btn-copy:active{transform:translateY(0)}
.btn-copy.copied{background:#0d3d24;border-color:#4ade80;color:#4ade80}
.btn-download{background:#0f172a;color:#94a3b8;border:1px solid #334155;padding:9px 14px;border-radius:6px;font-size:13px;cursor:pointer;display:inline-flex;align-items:center;gap:6px;transition:all .15s ease}
.btn-download:hover{background:#1e293b;color:#e0e0e0}
.rpm-shortcut-group{display:flex;gap:4px;margin-top:4px}
.rpm-shortcut{padding:5px 10px;font-size:11px;border-radius:5px;background:#0f172a;border:1px solid #334155;color:#94a3b8;cursor:pointer;transition:all .12s ease;font-family:inherit}
.rpm-shortcut:hover{background:#1e293b;color:#e0e0e0;border-color:#475569}
.rpm-shortcut.active{background:#1e3a8a;color:#bfdbfe;border-color:#3b5bdb}
.rpm-shortcut.unlimited{background:#0d3d24;color:#4ade80;border-color:#0d3d24}
.rpm-shortcut.unlimited:hover{background:#14532d;color:#86efac}
.rpm-hint{font-size:11px;margin-top:4px;color:#94a3b8;display:flex;align-items:center;gap:4px}
.provider-tag{font-size:10px;padding:2px 8px;background:#1e3a8a;color:#bfdbfe;border-radius:10px;font-weight:500}

/* v3.15.0: 模型健康度 badge (老大 2026-06-24 钦定) */
.health-badge{font-size:10px;padding:2px 7px;border-radius:10px;font-weight:600;cursor:help;display:inline-flex;align-items:center;gap:3px;white-space:nowrap}
.health-healthy{background:#0d3d24;color:#4ade80}
.health-degraded{background:#3d2e0d;color:#fbbf24}
.health-skip{background:#3a1a1a;color:#f87171;animation:healthSkipPulse 2s ease-in-out infinite}
.health-half-open{background:#0d2e3d;color:#5b8def;animation:healthProbePulse 1.5s ease-in-out infinite}
@keyframes healthSkipPulse{0%,100%{opacity:1}50%{opacity:.65}}
@keyframes healthProbePulse{0%,100%{opacity:1}50%{opacity:.7}}
.health-summary-bar{display:flex;gap:6px;margin:8px 0 12px;flex-wrap:wrap;align-items:center;font-size:12px}
.health-summary-item{display:inline-flex;align-items:center;gap:4px;padding:4px 10px;background:#0f0f13;border:1px solid #333;border-radius:12px}
.health-summary-item .count{font-weight:600}
.health-summary-item.healthy{color:#4ade80;border-color:#0d3d24}
.health-summary-item.degraded{color:#fbbf24;border-color:#3d2e0d}
.health-summary-item.skip{color:#f87171;border-color:#3a1a1a}
.health-summary-item.half_open{color:#5b8def;border-color:#0d2e3d}
.health-tooltip{font-family:monospace;font-size:11px;line-height:1.5}

/* v3.16.0: provider 自动禁用警告 + 健康度 mini summary */
.provider-health-warn{background:linear-gradient(135deg,#3b0d0d 0%,#1a1a24 100%);border:1px solid #ef4444;border-radius:8px;padding:10px 14px;margin:8px 0;font-size:12px;line-height:1.5}
.provider-health-warn .warn-title{color:#fca5a5;font-weight:600;margin-bottom:4px;display:flex;align-items:center;gap:6px}
.provider-health-warn .warn-reason{color:#fecaca;font-family:monospace;font-size:11px;word-break:break-word}
.provider-health-soon{background:linear-gradient(135deg,#3d2e0d 0%,#1a1a24 100%);border:1px solid #fbbf24;border-radius:8px;padding:8px 12px;margin:8px 0;font-size:12px;color:#fde68a}
.provider-health-mini{display:flex;gap:4px;margin-top:4px;font-size:10px;align-items:center}
.provider-health-mini .mini-chip{padding:2px 6px;border-radius:8px;font-weight:600}
.provider-health-mini .mini-chip.h{background:#0d3d24;color:#4ade80}
.provider-health-mini .mini-chip.d{background:#3d2e0d;color:#fbbf24}
.provider-health-mini .mini-chip.s{background:#3a1a1a;color:#f87171}
.provider-health-mini .mini-chip.ho{background:#0d2e3d;color:#5b8def}

/* v3.18.0: 配额耗尽警告 + 续费一键清卡片 */
.quota-card{background:linear-gradient(135deg,#3b3d0d 0%,#1a1a24 100%);border:1px solid #facc15;border-radius:8px;padding:12px 16px;margin:10px 0;font-size:12px;line-height:1.6;animation:quotaPulse 4s ease-in-out infinite}
@keyframes quotaPulse{0%,100%{border-color:#facc15}50%{border-color:#fde047}}
.quota-card .quota-title{color:#fde047;font-weight:600;margin-bottom:8px;display:flex;align-items:center;gap:6px;font-size:13px}
.quota-card .quota-summary{color:#fef9c3;margin-bottom:8px}
.quota-card .quota-list{margin-top:8px;border-top:1px dashed #fbbf24;padding-top:8px}
.quota-card .quota-row{display:flex;align-items:center;gap:8px;padding:4px 0;font-family:monospace;font-size:11px;color:#fde68a;border-bottom:1px solid #2d2d1a}
.quota-card .quota-row:last-child{border-bottom:none}
.quota-card .quota-type{display:inline-block;padding:1px 6px;border-radius:6px;font-size:10px;font-weight:600;background:#3d2e0d;color:#fde047}
.quota-card .quota-type.monthly{background:#3a1a1a;color:#fca5a5}
.quota-card .quota-type.weekly{background:#3d2e0d;color:#fdba74}
.quota-card .quota-type.daily{background:#3d3a0d;color:#fde047}
.quota-card .quota-type.token_plan{background:#0d2e3d;color:#7dd3fc}
.quota-card .quota-type.balance{background:#2e0d3d;color:#d8b4fe}
.quota-card .quota-path{flex:1;word-break:break-all;color:#fef9c3}
.quota-card .quota-remaining{color:#fbbf24;font-weight:600;min-width:60px;text-align:right}
.quota-card .quota-actions{display:flex;gap:4px}
.quota-card .quota-empty{color:#4ade80;font-style:italic}

/* v3.14.0: 模型列表 toolbar + filter bar + 改进空状态 */
.models-toolbar{display:flex;gap:10px;align-items:center;margin:12px 0 14px;flex-wrap:wrap}
.models-search{flex:1;min-width:220px;background:#0f0f13;border:1px solid #333;color:#e0e0e0;padding:9px 14px;border-radius:8px;font-size:13px;font-family:inherit;transition:border-color .15s ease,box-shadow .15s ease}
.models-search::placeholder{color:#666}
.models-search:focus{outline:none;border-color:#5b8def;box-shadow:0 0 0 3px rgba(91,141,239,.15)}
.models-sort{display:flex;gap:0;align-items:stretch;background:#0f0f13;border:1px solid #333;border-radius:8px;overflow:hidden}
.models-select{background:transparent;border:none;border-right:1px solid #333;color:#e0e0e0;padding:9px 12px;font-size:12px;cursor:pointer;font-family:inherit;min-width:110px}
.models-select:focus{outline:none;background:#1a1a24}
.models-sort-btn{background:transparent;border:none;color:#5b8def;padding:9px 14px;font-size:12px;cursor:pointer;font-weight:600;transition:background .12s ease}
.models-sort-btn:hover{background:#1a1a24}
.models-reset-btn{background:transparent;border:1px solid #333;color:#888;padding:9px 14px;border-radius:8px;font-size:12px;transition:all .12s ease}
.models-reset-btn:hover{border-color:#5b8def;color:#5b8def;background:rgba(91,141,239,.08)}
.models-filter-bar{display:flex;flex-direction:column;gap:10px;margin-bottom:16px;padding:14px 16px;background:#0f0f13;border:1px solid #1a1a24;border-radius:10px}
.filter-group{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.filter-label{font-size:11px;color:#888;font-weight:600;text-transform:uppercase;letter-spacing:.5px;min-width:60px;display:inline-flex;align-items:center;gap:6px}
.filter-clear{color:#666;cursor:pointer;font-size:14px;line-height:1;padding:0 4px;border-radius:4px;transition:all .12s ease;display:inline-block}
.filter-clear:hover{color:#f87171;background:rgba(248,113,113,.1)}
.provider-filter{display:flex;gap:4px;flex-wrap:wrap}
.provider-filter .chip{padding:4px 10px}
.provider-filter .chip.selected{background:#5b8def;color:#fff;border-color:#5b8def}
/* 改进空状态 (v3.14.0) */
.empty-state{padding:40px 20px;text-align:center;color:#666;font-size:13px;background:#0f0f13;border:1px dashed #333;border-radius:10px;margin:10px 0}
.empty-state .empty-icon{font-size:36px;margin-bottom:8px;opacity:.6}
.empty-state .empty-title{color:#a0a0b0;font-size:14px;font-weight:600;margin-bottom:4px}
.empty-state .empty-desc{color:#666;font-size:12px;margin-bottom:12px;line-height:1.5}
.empty-state .empty-action{margin-top:8px}

/* v3.6.0 左侧 sidebar 导航 ──────────────────────── */
body{display:flex;gap:0;padding:0;max-width:none;min-height:100vh;background:#0a0a0e}
.sidebar{width:220px;background:#0f0f13;border-right:1px solid #1a1a24;padding:18px 0;position:fixed;top:0;left:0;bottom:0;overflow-y:auto;z-index:50}
.sidebar h1{font-size:15px;margin:0 18px 18px;padding-bottom:14px;border-bottom:1px solid #1a1a24;color:#5b8def;display:flex;align-items:center;gap:8px}
.sidebar h1 .logo{font-size:18px}
.nav-item{display:flex;align-items:center;gap:10px;padding:10px 18px;color:#a0a0b0;cursor:pointer;font-size:13px;border-left:3px solid transparent;transition:all .15s ease;user-select:none}
.nav-item:hover{background:#1a1a24;color:#e0e0e0}
.nav-item.active{background:#1a1a24;color:#5b8def;border-left-color:#5b8def;font-weight:500}
.nav-item .icon{font-size:16px;width:20px;text-align:center}
.nav-item .badge{background:#dc2626;color:#fff;font-size:10px;padding:1px 6px;border-radius:10px;margin-left:auto}
.sidebar-footer{position:absolute;bottom:0;left:0;right:0;padding:14px 18px;border-top:1px solid #1a1a24;font-size:11px;color:#666}
.sidebar-footer .ver{color:#4ade80;cursor:pointer}
.sidebar-footer .ver:hover{text-decoration:underline}

.main{flex:1;margin-left:220px;padding:24px 28px;max-width:1400px}
.view{display:none}
.view.active{display:block}
.view-title{font-size:20px;margin:0 0 18px;display:flex;align-items:center;gap:10px}
.view-subtitle{color:#666;font-size:12px;margin:-12px 0 18px}

/* v3.6.0 分页 ──────────────────────── */
.pagination{display:flex;align-items:center;gap:8px;margin:14px 0;flex-wrap:wrap;font-size:12px}
.pagination .info{color:#888}
.pagination button{background:#1a1a24;border:1px solid #333;color:#e0e0e0;padding:5px 12px;border-radius:4px;cursor:pointer;font-size:12px;min-width:32px}
.pagination button:hover:not(:disabled){background:#333;border-color:#5b8def}
.pagination button:disabled{opacity:0.4;cursor:not-allowed}
.pagination button.active{background:#2563eb;border-color:#2563eb;color:#fff}
.pagination .page-jump{display:flex;align-items:center;gap:4px;color:#888}
.pagination .page-jump input{width:50px;background:#0f0f13;border:1px solid #333;color:#e0e0e0;padding:4px 8px;border-radius:3px;font-size:12px;text-align:center}
.pagination select{background:#0f0f13;border:1px solid #333;color:#e0e0e0;padding:5px 8px;border-radius:3px;font-size:12px;cursor:pointer}

/* v3.6.0 Stats 卡片扩展 ──────────────────────── */
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-bottom:20px}
.stats-grid .stat-card{background:#1a1a24;border-radius:8px;padding:16px 18px;min-width:0}
.stats-grid .stat-card .label{font-size:11px;color:#666;text-transform:uppercase;letter-spacing:0.5px}
.stats-grid .stat-card .value{font-size:24px;font-weight:600;margin-top:6px;color:#e0e0e0}
.stats-grid .stat-card .value.good{color:#4ade80}
.stats-grid .stat-card .value.warn{color:#fbbf24}
.stats-grid .stat-card .value.bad{color:#f87171}
.stats-grid .stat-card .delta{font-size:11px;color:#888;margin-top:4px}
.provider-stats-table{font-size:12px}
.provider-stats-table td{font-family:ui-monospace,monospace}
</style>
</head>
<body>
<!-- 左侧 sidebar nav (v3.6.0) -->
<div class="sidebar">
  <h1><span class="logo">⚡</span> SMR v__SMR_VERSION__</h1>
  <div class="nav-item active" data-view="dashboard" onclick="showView('dashboard')">
    <span class="icon">📊</span> 仪表盘
  </div>
  <div class="nav-item" data-view="providers" onclick="showView('providers')">
    <span class="icon">🔌</span> 服务商管理 <span id="provNavBadge" class="badge" style="display:none">0</span>
  </div>
  <div class="nav-item" data-view="models" onclick="showView('models')">
    <span class="icon">🤖</span> 模型列表
  </div>
  <div class="nav-item" data-view="keys" onclick="showView('keys')">
    <span class="icon">🔑</span> API 密钥
  </div>
  <div class="nav-item" data-view="stats" onclick="showView('stats')">
    <span class="icon">📈</span> 用量统计
  </div>
  <div class="nav-item" data-view="classifier" onclick="showView('classifier')">
    <span class="icon">⚙️</span> 分类器
  </div>
  <div class="nav-item" data-view="config" onclick="showView('config')">
    <span class="icon">🔧</span> 服务配置
  </div>
  <div class="nav-item" data-view="history" onclick="showView('history')">
    <span class="icon">📜</span> 配置历史
  </div>
  <div class="nav-item" data-view="version" onclick="showView('version')">
    <span class="icon">🔔</span> 版本
  </div>
  <div class="nav-item" data-view="publicapi" onclick="showView('publicapi')">
    <span class="icon">🌐</span> 对外 API
  </div>
  <div class="nav-item" data-view="modelgroups" onclick="showView('modelgroups')">
    <span class="icon">🏷️</span> 模型分组
  </div>
  <div class="nav-item" data-view="wizard" onclick="showView('wizard')">
    <span class="icon">🧙</span> 分组向导 <span class="badge" style="background:#f59e0b">NEW</span>
  </div>
  <div class="sidebar-footer">
    <div>运行时间 <span id="navUptime" class="uptime">-</span></div>
    <div>版本 <span class="ver" id="navVer" onclick="showView('version')">-</span></div>
  </div>
</div>

<!-- 主内容区 -->
<div class="main">

<!-- 视图: Dashboard -->
<div class="view active" id="view-dashboard">
  <h2 class="view-title">📊 仪表盘</h2>
  <p class="view-subtitle">SMR 整体状态速览</p>
  <div class="stats-grid" id="dashboardStats"><div class="loading">加载中...</div></div>
  <h2 style="font-size:14px;color:#888;margin:20px 0 10px">模态分布</h2>
  <div class="modality-grid" id="modalityGrid"><div class="loading">加载中...</div></div>
  <h2 style="font-size:14px;color:#888;margin:20px 0 10px">最近 Provider</h2>
  <div class="provider-grid" id="providerGridRecent"><div class="loading">加载中...</div></div>
</div>

<!-- 视图: Providers -->
<div class="view" id="view-providers">
  <h2 class="view-title">🔌 服务商管理</h2>
  <p class="view-subtitle">添加 / 编辑 / 启用 / 停用 / 复制 / 导入 / 导出</p>
  <div class="section-header">
    <div class="provider-toolbar">
      <select id="provFilter" onchange="refreshProviders()" class="filter-select">
        <option value="all">全部</option>
        <option value="enabled">✓ 启用</option>
        <option value="disabled">⏸ 停用</option>
        <option value="known">⭐ 内置</option>
        <option value="unknown">🆕 自定义</option>
      </select>
      <button class="btn-sm" onclick="refreshProviders()">🔄 刷新</button>
    </div>
    <div class="provider-toolbar">
      <button class="btn-sm" onclick="exportProviders()">📤 导出</button>
      <button class="btn-sm" onclick="document.getElementById('importFileInput').click()">📥 导入</button>
      <input type="file" id="importFileInput" style="display:none" accept=".yaml,.yml,.json" onchange="importProviders(event)">
      <button class="btn-sm primary" onclick="openAddProvider()">➕ 添加</button>
    </div>
  </div>
  <div class="provider-grid" id="providerGrid"><div class="loading">加载中...</div></div>
</div>

<!-- 视图: Models (v3.14.0: 多维筛选 + 排序 + 搜索) -->
<div class="view" id="view-models">
  <h2 class="view-title">🤖 模型列表 <span style="font-size:13px;color:#666" id="modelCount"></span></h2>
  <p class="view-subtitle">多维筛选 + 实时搜索 + 自定义排序</p>
  <div class="models-toolbar">
    <input type="text" id="modelSearch" class="models-search" placeholder="🔍 搜索 model id / provider..." oninput="setSearch(this.value)">
    <div class="models-sort">
      <select id="modelSortBy" class="models-select" onchange="setSortBy(this.value)" title="排序字段">
        <option value="capability">按能力分</option>
        <option value="context">按上下文</option>
        <option value="name">按模型名</option>
        <option value="price">按价格</option>
      </select>
      <button id="sortOrderBtn" class="models-sort-btn" onclick="toggleSortOrder()" title="切换升降序">↓ 降序</button>
    </div>
    <button class="btn-sm models-reset-btn" onclick="resetAllModelFilters()" title="清空所有筛选">↺ 重置</button>
  </div>
  <div class="models-filter-bar">
    <div class="filter-group">
      <span class="filter-label">分类</span>
      <div class="modality-filter" id="modalityFilter"></div>
    </div>
    <div class="filter-group">
      <span class="filter-label">Provider <span class="filter-clear" onclick="clearProviderFilter()">×</span></span>
      <div class="provider-filter" id="providerFilter"></div>
    </div>
  </div>
  <div id="modelSection">
    <table><thead><tr><th>Model</th><th>Provider</th><th>分类</th><th>价格</th><th>能力分</th><th>🏥 健康度</th></tr></thead><tbody id="modelTable"></tbody></table>
  </div>
  <div class="pagination" id="modelPagination"></div>
</div>

<!-- 视图: API Keys (v3.6.0 新) -->
<div class="view" id="view-keys">
  <h2 class="view-title">🔑 API 密钥管理</h2>
  <p class="view-subtitle">独立管理各 provider 的 API key (脱敏指纹显示)</p>
  <div id="keysList"><div class="loading">加载中...</div></div>
</div>

<!-- 视图: Usage Stats (v3.6.0 新真数据) -->
<div class="view" id="view-stats">
  <h2 class="view-title">📈 用量统计</h2>
  <p class="view-subtitle">真实使用量数据 (来自 /v1/admin/stats)</p>
  <div class="stats-grid" id="statsSummary"><div class="loading">加载中...</div></div>
  <h3 style="font-size:14px;color:#888;margin:20px 0 10px">按 Provider 拆分</h3>
  <table class="provider-stats-table">
    <thead><tr><th>Provider</th><th>总请求</th><th>成功</th><th>失败</th><th>今日</th><th>今日 token</th><th>平均延迟</th><th>首 token</th></tr></thead>
    <tbody id="providerStatsTable"><tr><td colspan="8" style="text-align:center;color:#666;padding:20px">暂无数据</td></tr></tbody>
  </table>
  <h3 style="font-size:14px;color:#888;margin:20px 0 10px">上下文桥接 (ContextBridge)</h3>
  <div id="contextBridgeStats" class="text-muted" style="padding:12px;background:#0f0f13;border-radius:6px;font-family:ui-monospace,monospace;font-size:12px">加载中...</div>
</div>

<!-- 视图: Classifier -->
<div class="view" id="view-classifier">
  <h2 class="view-title">⚙️ Tier Bonus & 自定义关键词</h2>
  <p class="view-subtitle">模型评分规则 (内置 + 用户覆盖)</p>
  <button class="btn" onclick="openClassifier()">✏️ 编辑 Classifier</button>
</div>

<!-- 视图: Server Config -->
<div class="view" id="view-config">
  <h2 class="view-title">🔧 服务配置 & 路由策略</h2>
  <p class="view-subtitle">监听端口 + 鉴权 + 路由策略</p>
  <button class="btn" onclick="openServer()">✏️ 修改配置</button>
  <div class="row" style="margin-top:12px">
    <button class="btn-sm" onclick="reloadConfig()">🔄 重载配置</button>
    <button class="btn-sm" onclick="loadModels()">📥 获取模型</button>
  </div>
</div>

<!-- 视图: Config History -->
<div class="view" id="view-history">
  <h2 class="view-title">📜 配置历史</h2>
  <p class="view-subtitle">自动备份的 config.yaml 历史 (保留 50 个)</p>
  <button class="btn" onclick="openConfigBackups()">📜 查看历史</button>
</div>

<!-- 视图: Version -->
<div class="view" id="view-version">
  <h2 class="view-title">🔔 版本信息</h2>
  <p class="view-subtitle">当前版本 + GitHub release 检查</p>
  <div class="stats-grid" id="versionGrid"><div class="loading">加载中...</div></div>
</div>

<!-- 视图: 对外 API (v3.7.0 新增) -->
<div class="view" id="view-publicapi">
  <h2 class="view-title">🌐 对外 API 多 Key 管理</h2>
  <p class="view-subtitle">租户 Key · 速率限制 · 模型白名单 · 用量追踪</p>
  <div class="section-header">
    <div class="provider-toolbar">
      <button class="btn-sm primary" onclick="openCreatePublicKey()">➕ 创建 KEY</button>
      <button class="btn-sm" onclick="renderPublicApiView()">🔄 刷新</button>
    </div>
  </div>
  <div class="stats-grid" id="publicApiSummary"><div class="loading">加载中...</div></div>
  <h3 style="font-size:14px;color:#888;margin:20px 0 10px">所有 KEY</h3>
  <div id="publicKeyList"><div class="loading">加载中...</div></div>

  <div style="margin-top:30px;padding:16px;background:#0f0f13;border-radius:8px;font-size:12px;color:#888">
    <strong>💡 对外 API 使用说明</strong><br>
    • 创建后只显示一次原 KEY，之后只能重新生成<br>
    • 每个 KEY 独立追踪 total/success/fail/tokens<br>
    • 速率限制: sliding window 60s 内 rpm 计数<br>
    • 模型白名单: 空 = 全部允许, 否则只允许列表内<br>
    • 客户端调用: <code>Authorization: Bearer ***</code> 即可
  </div>
</div>

<!-- 视图: 模型分组 (v3.9.0 新增) -->
<div class="view" id="view-modelgroups">
  <h2 class="view-title">🏷️ 模型分组管理</h2>
  <p class="view-subtitle">正则跨 provider 拉取模型分组 · 给对外 API 白名单 + 轮询规则用</p>
  <div class="section-header">
    <div class="provider-toolbar">
      <button class="btn-sm primary" onclick="openCreateModelGroup()">➕ 创建分组</button>
      <button class="btn-sm" onclick="renderModelGroupsView()">🔄 刷新</button>
    </div>
  </div>
  <div class="stats-grid" id="modelGroupsSummary"><div class="loading">加载中...</div></div>
  <h3 style="font-size:14px;color:#888;margin:20px 0 10px">所有分组</h3>
  <div id="modelGroupsList"><div class="loading">加载中...</div></div>

  <div style="margin-top:30px;padding:16px;background:#0f0f13;border-radius:8px;font-size:12px;color:#888">
    <strong>💡 模型分组使用说明</strong><br>
    • <strong>patterns</strong>: 正则列表, 跨所有 provider 的 model_id 模糊匹配<br>
    • e.g. <code>["claude-3-5.*", "claude-3-haiku.*"]</code> → 所有 Claude 3.5 + 3-haiku<br>
    • <strong>resolved_models</strong>: 当前 provider 已知 model 中匹配上的列表<br>
    • 创建分组后, 对外 API 白名单可用 <code>group:&lt;name&gt;</code> 引用<br>
    • 轮询规则 (Phase H) 按 group 维度做 round-robin / failover / weighted
  </div>
</div>

<!-- 视图: 模型分组向导 (v3.10.0 新增) -->
<div class="view" id="view-wizard">
  <h2 class="view-title">🧙 模型分组向导</h2>
  <p class="view-subtitle">选预设场景或自定义筛选条件 · 一键生成模型分组 + API key</p>

  <!-- 第一部分: 13 个预设场景卡片 -->
  <h3 style="font-size:13px;color:#888;margin:20px 0 10px">✨ 快速开始: 选一个预设场景</h3>
  <div class="wizard-presets-grid" id="wizardPresetsGrid">
    <div class="loading">加载中...</div>
  </div>

  <!-- 第二部分: 自定义筛选 -->
  <h3 style="font-size:13px;color:#888;margin:30px 0 10px">🔍 或自定义筛选条件</h3>
  <div class="wizard-filter-panel">
    <div class="filter-row">
      <label>Provider (多选)</label>
      <div class="chip-group" id="wizardFilterProviders">
        <span class="chip" data-value="openrouter" onclick="toggleChip(this)">openrouter</span>
        <span class="chip" data-value="newapi" onclick="toggleChip(this)">newapi</span>
        <span class="chip" data-value="mock_a" onclick="toggleChip(this)">mock_a</span>
        <span class="chip" data-value="mock_b" onclick="toggleChip(this)">mock_b</span>
      </div>
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
      <label>最低 Quality Score</label>
      <input type="range" id="wizardFilterQuality" min="0" max="100" value="0" step="5" oninput="document.getElementById('qualityVal').textContent=this.value">
      <span id="qualityVal" style="color:#5b8def;font-weight:500;margin-left:8px">0</span>
    </div>
    <div class="filter-row">
      <label>最低 Speed Score</label>
      <input type="range" id="wizardFilterSpeed" min="0" max="100" value="0" step="5" oninput="document.getElementById('speedVal').textContent=this.value">
      <span id="speedVal" style="color:#5b8def;font-weight:500;margin-left:8px">0</span>
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
      <div class="chip-group" id="wizardFilterTags">
        <span class="chip" data-value="reasoning" onclick="toggleChip(this)">reasoning</span>
        <span class="chip" data-value="coding" onclick="toggleChip(this)">coding</span>
        <span class="chip" data-value="vision" onclick="toggleChip(this)">vision</span>
        <span class="chip" data-value="fast" onclick="toggleChip(this)">fast</span>
        <span class="chip" data-value="long-context" onclick="toggleChip(this)">long-context</span>
        <span class="chip" data-value="tools" onclick="toggleChip(this)">tools</span>
        <span class="chip" data-value="multimodal" onclick="toggleChip(this)">multimodal</span>
      </div>
    </div>
    <div style="margin-top:14px;text-align:right">
      <button class="btn-sm" onclick="resetWizardFilter()">🔄 重置</button>
      <button class="btn-sm primary" onclick="applyWizardFilter()">🔍 应用筛选</button>
    </div>
  </div>

  <!-- 第三部分: 匹配模型列表 (批量勾选) -->
  <h3 style="font-size:13px;color:#888;margin:30px 0 10px">
    匹配模型 (<span id="wizardMatchCount">0</span>)
    <span style="float:right">
      <button class="btn-sm" onclick="wizardSelectAll()">☑ 全选</button>
      <button class="btn-sm" onclick="wizardSelectNone()">☐ 清选</button>
    </span>
  </h3>
  <div class="wizard-models-list" id="wizardModelsList">
    <div class="empty-state">应用筛选或选预设场景查看匹配模型</div>
  </div>

  <!-- 第四部分: 一键生成 -->
  <h3 style="font-size:13px;color:#888;margin:30px 0 10px">✨ 生成模型分组</h3>
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
      <label>
        <input type="checkbox" id="wizardCreateApiKey" checked>
        自动生成 API key (绑定到 group)
      </label>
    </div>
    <div class="filter-row">
      <label>API key 名 (默认 = group name + "-key")</label>
      <input type="text" id="wizardApiKeyName" placeholder="(可选)" class="filter-input">
    </div>
    <div style="margin-top:14px;text-align:right">
      <button class="btn-sm" onclick="previewWizardGroup()">🔍 预览</button>
      <button class="btn-sm primary" onclick="generateWizardGroup()">✨ 生成分组</button>
    </div>
  </div>

  <!-- 第五部分: 生成结果展示 -->
  <div id="wizardResultPanel" style="display:none;margin-top:24px;padding:16px;background:#0f1f0f;border:1px solid #4ade80;border-radius:8px">
    <h3 style="font-size:14px;color:#4ade80;margin:0 0 12px">✅ 分组生成成功</h3>
    <div id="wizardResultContent"></div>
  </div>
</div>

<script>
// ============================================================
// v3.10.0 (Phase L + M): 模型分组向导器 JS
// ============================================================

let wizardState = {
  presets: [],          // [{id, name, icon, description, current_match_count, ...}]
  matchedModels: [],    // 当前匹配 models [{id, provider, path, modality, ...}]
  selectedPreset: null, // null = 自定义筛选
  selectedPaths: new Set(),  // 用户勾选的 model path
  currentFilter: null,  // 当前应用的 filter (dict)
};

function toggleChip(el) {
  el.classList.toggle('selected');
}

async function loadWizard() {
  wizardState.selectedPreset = null;
  wizardState.selectedPaths = new Set();
  // 加载 13 preset
  const r = await fetch('/v1/admin/model-groups/wizard/presets');
  const data = await r.json();
  if (data.error) { toast('❌ ' + data.error); return; }
  wizardState.presets = data.presets;
  renderPresetCards();
  // 初始化空列表
  document.getElementById('wizardModelsList').innerHTML =
    '<div class="empty-state">👆 选一个预设场景 或 自定义筛选查看匹配模型</div>';
  document.getElementById('wizardMatchCount').textContent = '0';
  // 自动分组名
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
      <div class="preset-icon">${p.icon}</div>
      <div class="preset-name">${escapeHtml(p.name)}</div>
      <div class="preset-desc">${escapeHtml(p.description)}</div>
      <div class="${countClass}">${p.current_match_count} models</div>
    `;
    if (p.current_match_count > 0) {
      card.onclick = () => selectPreset(p.id);
    }
    grid.appendChild(card);
  }
}

async function selectPreset(presetId) {
  wizardState.selectedPreset = presetId;
  // 高亮卡片
  document.querySelectorAll('.wizard-preset-card').forEach(c => {
    c.classList.toggle('selected', c.dataset.presetId === presetId);
  });
  // 自动应用 preset 的 filter
  const preset = wizardState.presets.find(p => p.id === presetId);
  if (!preset) return;
  await applyFilterToBackend(preset.filter);
  // 自动填分组名
  if (!document.getElementById('wizardGroupName').value || document.getElementById('wizardGroupName').value.startsWith('my-group-')) {
    document.getElementById('wizardGroupName').value = presetId + '-' + Date.now().toString(36).slice(-4);
  }
}

function applyWizardFilter() {
  // 从 UI 收集 filter
  const providers = Array.from(document.querySelectorAll('#wizardFilterProviders .chip.selected')).map(c => c.dataset.value);
  const contextMin = parseInt(document.getElementById('wizardFilterContext').value) || 0;
  const qualityMin = parseFloat(document.getElementById('wizardFilterQuality').value) || 0;
  const speedMin = parseFloat(document.getElementById('wizardFilterSpeed').value) || 0;
  const modality = document.getElementById('wizardFilterModality').value || '';
  const tagsAny = Array.from(document.querySelectorAll('#wizardFilterTags .chip.selected')).map(c => c.dataset.value);

  const filter = {};
  if (providers.length > 0) filter.providers = providers;
  if (contextMin > 0) filter.context_min = contextMin;
  if (qualityMin > 0) filter.quality_min = qualityMin;
  if (speedMin > 0) filter.speed_min = speedMin;
  if (modality) filter.modality = modality;
  if (tagsAny.length > 0) filter.tags_any = tagsAny;

  wizardState.selectedPreset = null;
  document.querySelectorAll('.wizard-preset-card').forEach(c => c.classList.remove('selected'));
  applyFilterToBackend(filter);
}

function resetWizardFilter() {
  document.querySelectorAll('#wizardFilterProviders .chip, #wizardFilterTags .chip').forEach(c => c.classList.remove('selected'));
  document.getElementById('wizardFilterContext').value = '0';
  document.getElementById('wizardFilterQuality').value = '0';
  document.getElementById('wizardSpeed') || 0;
  document.getElementById('wizardFilterSpeed').value = '0';
  document.getElementById('wizardFilterModality').value = '';
  document.getElementById('qualityVal').textContent = '0';
  document.getElementById('speedVal').textContent = '0';
}

async function applyFilterToBackend(filter) {
  wizardState.currentFilter = filter;
  const params = new URLSearchParams();
  if (filter.providers) params.set('providers', filter.providers.join(','));
  if (filter.context_min) params.set('context_min', filter.context_min);
  if (filter.quality_min) params.set('quality_min', filter.quality_min);
  if (filter.speed_min) params.set('speed_min', filter.speed_min);
  if (filter.reasoning_min) params.set('reasoning_min', filter.reasoning_min);
  if (filter.modality) params.set('modality', filter.modality);
  if (filter.tags_any) params.set('tags_any', filter.tags_any.join(','));

  const r = await fetch('/v1/admin/models/filter?' + params.toString());
  const data = await r.json();
  if (data.error) { toast('❌ ' + data.error); return; }
  wizardState.matchedModels = data.models;
  document.getElementById('wizardMatchCount').textContent = data.total;
  renderMatchedModels();
}

function renderMatchedModels() {
  const list = document.getElementById('wizardModelsList');
  if (wizardState.matchedModels.length === 0) {
    list.innerHTML = '<div class="empty-state">😢 没匹配到 model, 试试放宽条件</div>';
    return;
  }
  list.innerHTML = '';
  for (const m of wizardState.matchedModels) {
    const row = document.createElement('div');
    row.className = 'wizard-model-row';
    if (wizardState.selectedPaths.has(m.path)) row.classList.add('selected');
    const metaChips = [];
    if (m.context_window > 0) metaChips.push(`<span class="meta-chip">ctx:${(m.context_window/1000).toFixed(0)}K</span>`);
    if (m.quality_score > 0) metaChips.push(`<span class="meta-chip">q:${m.quality_score}</span>`);
    if (m.speed_score > 0) metaChips.push(`<span class="meta-chip">s:${m.speed_score}</span>`);
    if (m.reasoning_score > 0) metaChips.push(`<span class="meta-chip">r:${m.reasoning_score}</span>`);
    for (const t of (m.tags || []).slice(0, 3)) {
      metaChips.push(`<span class="meta-chip tag">#${escapeHtml(t)}</span>`);
    }
    row.innerHTML = `
      <input type="checkbox" ${wizardState.selectedPaths.has(m.path) ? 'checked' : ''}>
      <div class="model-info">
        <div class="model-path">${escapeHtml(m.path)}</div>
        <div class="model-meta">${metaChips.join(' ')}</div>
      </div>
    `;
    const cb = row.querySelector('input');
    cb.onclick = (e) => e.stopPropagation();
    cb.onchange = () => {
      if (cb.checked) wizardState.selectedPaths.add(m.path);
      else wizardState.selectedPaths.delete(m.path);
      row.classList.toggle('selected', cb.checked);
    };
    row.onclick = () => { cb.checked = !cb.checked; cb.onchange(); };
    list.appendChild(row);
  }
}

function wizardSelectAll() {
  for (const m of wizardState.matchedModels) wizardState.selectedPaths.add(m.path);
  renderMatchedModels();
}

function wizardSelectNone() {
  wizardState.selectedPaths.clear();
  renderMatchedModels();
}

async function previewWizardGroup() {
  if (!wizardState.currentFilter) {
    toast('⚠️ 先应用筛选或选预设场景');
    return;
  }
  const r = await fetch('/v1/admin/models/filter?' + new URLSearchParams(wizardState.currentFilter).toString());
  const data = await r.json();
  toast(`👀 预览: 将匹配 ${data.total} 个 model, 创建 1 个 group${document.getElementById('wizardCreateApiKey').checked ? ' + 1 个 API key' : ''}`);
}

async function generateWizardGroup() {
  if (!wizardState.currentFilter) {
    toast('⚠️ 先应用筛选或选预设场景');
    return;
  }
  const name = document.getElementById('wizardGroupName').value.trim();
  if (!name) { toast('⚠️ 填分组名'); return; }
  const strategy = document.getElementById('wizardGroupStrategy').value;
  const createKey = document.getElementById('wizardCreateApiKey').checked;
  const keyName = document.getElementById('wizardApiKeyName').value.trim();

  // 合并筛选 + 手动选择 (如果有勾选, 用 paths 限制)
  const filter = {...wizardState.currentFilter};
  if (wizardState.selectedPaths.size > 0 && wizardState.selectedPaths.size < wizardState.matchedModels.length) {
    // 用户手动选了子集 → 用 selectedPaths 限制 (但 UI 不支持直接 path filter, 用 tags_any 模拟: 不行)
    // 改: 限制 patterns 用 selectedPaths 里的 model id
    filter._manual_selection = Array.from(wizardState.selectedPaths);
  }

  let endpoint, body;
  if (wizardState.selectedPreset) {
    endpoint = '/v1/admin/model-groups/from-wizard';
    body = {preset: wizardState.selectedPreset, name, strategy, create_api_key: createKey};
    if (keyName) body.api_key_name = keyName;
  } else {
    endpoint = '/v1/admin/model-groups/from-filter';
    body = {name, filter: wizardState.currentFilter, strategy, create_api_key: createKey};
    if (keyName) body.api_key_name = keyName;
    // 手动选择: 传 patterns
    if (filter._manual_selection && filter._manual_selection.length > 0) {
      body.patterns = filter._manual_selection.map(p => `.*${p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}.*`);
      delete body.filter._manual_selection;
    }
  }

  const r = await fetch(endpoint, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  });
  const data = await r.json();
  if (data.error) { toast('❌ ' + data.error); return; }

  // 展示结果
  showWizardResult(data);
  // 提示
  let msg = `✅ 已创建 group '${data.group.name}' (${data.resolved_count} models)`;
  if (data.api_key) msg += ` + API key '${data.api_key.name}'`;
  toast(msg);
  // 刷新 modelgroups view 缓存
  lastModelGroups = null;
}

function showWizardResult(data) {
  const panel = document.getElementById('wizardResultPanel');
  const content = document.getElementById('wizardResultContent');
  let html = `
    <div style="margin-bottom:8px"><strong>分组名:</strong> <code>${escapeHtml(data.group.name)}</code></div>
    <div style="margin-bottom:8px"><strong>匹配 model 数:</strong> ${data.resolved_count}</div>
    <div style="margin-bottom:8px"><strong>Sample models:</strong> ${data.matched_samples.map(s => `<code>${escapeHtml(s)}</code>`).join(', ')}</div>
    <div style="margin-bottom:8px"><strong>说明:</strong> ${escapeHtml(data.group.description || '')}</div>
  `;
  if (data.api_key) {
    html += `
      <div style="margin-top:14px;padding-top:14px;border-top:1px solid #4ade80">
        <strong style="color:#4ade80">🔑 API Key (仅显示这一次!)</strong>
        <div style="margin-top:6px;padding:10px;background:#0a0a0d;border-radius:6px;font-family:monospace;font-size:12px;word-break:break-all;color:#fbbf24">
          ${escapeHtml(data.api_key.key)}
        </div>
        <div style="margin-top:6px;font-size:11px;color:#888">name: <code>${escapeHtml(data.api_key.name)}</code> · hash: <code>${escapeHtml(data.api_key.key_hash)}</code></div>
      </div>
    `;
  }
  content.innerHTML = html;
  panel.style.display = 'block';
  panel.scrollIntoView({behavior: 'smooth', block: 'nearest'});
}

function escapeHtml(s) {
  if (s === undefined || s === null) return '';
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
}
</script>

</div> <!-- .main -->

<!-- Toast -->
<div class="toast" id="toast"></div>

<script>
const BASE = '';
let filterModality = '';
let filterProviders = new Set();   // v3.14.0: Provider 多选筛选
let filterSearch = '';              // v3.14.0: 搜索框 (model id / provider)
// v3.15.0: 模型健康度 (从 /v1/admin/model-health 拉, 5min 缓存)
let lastModelHealth = {};           // path → health record
let lastModelHealthFetch = 0;       // 上次拉取时间戳 (秒)
let modelHealthSummary = null;       // summary {total_models, by_state}
// v3.16.0: provider 健康度 (从 /v1/admin/provider-health 拉)
let lastProviderHealth = {providers: [], summary: {}, config: {}};
let lastQuotaStatus = {summary: {total_quota_models: 0, by_type: {}}, quota_models: []};  // v3.18.0
let sortBy = 'capability';          // v3.14.0: capability / context / name / price
let sortOrder = 'desc';             // v3.14.0: asc / desc

// v3.6.0: 视图状态
let currentView = 'dashboard';
let currentPage = 0;
let PAGE_SIZE = 20;                 // v3.14.0: 改 const → let 支持每页大小切换
let lastModelsData = null;
let lastStatsData = null;
let lastVersionData = null;

function showView(view){
  currentView = view;
  // 切换 nav active
  document.querySelectorAll('.nav-item').forEach(n => {
    n.classList.toggle('active', n.dataset.view === view);
  });
  // 切换 view
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  const el = document.getElementById('view-' + view);
  if(el) el.classList.add('active');
  // 切到特定视图时, 拉对应数据
  if(view === 'stats') renderStatsView();
  else if(view === 'keys') renderKeysView();
  else if(view === 'version') renderVersionView();
  else if(view === 'publicapi') renderPublicApiView();
  else if(view === 'modelgroups') renderModelGroupsView();
  else if(view === 'wizard') loadWizard();
  else if(view === 'models' && lastModelsData){ renderModels(); }
}

// v3.6.0: API Keys 独立管理视图 (Phase G)
async function renderKeysView(){
  const container = document.getElementById('keysList');
  if(!container) return;
  container.innerHTML = '<div class="loading">加载中...</div>';
  const data = await api('/v1/admin/api-keys').catch(e=>({keys:[], error: e.message}));
  if(data.error){
    container.innerHTML = `<div class="empty-state"><p>❌ 加载失败: ${data.error}</p></div>`;
    return;
  }
  const items = data.keys || [];
  if(items.length === 0){
    container.innerHTML = '<div class="empty-state"><p>暂无 provider</p></div>';
    return;
  }
  let html = `<table class="kv-table">
    <thead><tr>
      <th>Provider</th>
      <th>Key 数量</th>
      <th>预览 (脱敏)</th>
      <th>联合指纹 (sha256)</th>
      <th>状态</th>
      <th>操作</th>
    </tr></thead><tbody>`;
  for(const it of items){
    const previewHtml = it.preview.length > 0
      ? it.preview.map(p => `<code>${p}</code>`).join('<br>')
      : '<span class="text-muted">(无)</span>';
    html += `<tr>
      <td><span class="provider-tag">${it.provider}</span></td>
      <td><strong>${it.count}</strong></td>
      <td>${previewHtml}</td>
      <td><code class="fp">${it.fingerprint || '-'}</code></td>
      <td>${it.enabled ? '<span class="badge-green">启用</span>' : '<span class="badge-gray">停用</span>'}</td>
      <td class="actions">
        <button class="btn-sm" onclick="addKeyPrompt('${it.provider}')">➕ 加 key</button>
        <button class="btn-sm btn-danger" onclick="clearKeysConfirm('${it.provider}', ${it.count})">🗑️ 清空</button>
      </td>
    </tr>`;
  }
  html += '</tbody></table>';
  container.innerHTML = html;
}

async function addKeyPrompt(provider){
  const key = prompt(`为 provider "${provider}" 添加新的 API key:\n\n(注意: 添加后会立即写盘 + 触发模型列表刷新)`);
  if(!key) return;
  const r = await fetch(BASE+'/v1/admin/api-keys', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({provider, api_key: key})
  });
  const d = await r.json().catch(()=>({error:'parse failed'}));
  if(d.ok){
    toast(`✅ added: fp=${d.added_fingerprint}, total=${d.count}`);
    renderKeysView();
  } else {
    toast(d.error || 'add failed', false);
  }
}

async function clearKeysConfirm(provider, count){
  if(!confirm(`确认清空 provider "${provider}" 的全部 ${count} 个 API key?\n\n(警告: 清空后该 provider 无法访问, 需重新添加 key)`)) return;
  const r = await fetch(BASE+`/v1/admin/api-keys/${provider}`, {method:'DELETE'});
  const d = await r.json().catch(()=>({error:'parse failed'}));
  if(d.ok){
    toast(`✅ cleared: ${provider}`);
    renderKeysView();
  } else {
    toast(d.error || 'clear failed', false);
  }
}

async function removeKeyByIndex(provider, idx){
  if(!confirm(`删除 provider "${provider}" 的第 ${idx+1} 个 key?`)) return;
  const r = await fetch(BASE+`/v1/admin/api-keys/${provider}?key_index=${idx}`, {method:'DELETE'});
  const d = await r.json().catch(()=>({error:'parse failed'}));
  if(d.ok){
    toast(`✅ removed: fp=${d.removed_fingerprint}, remaining=${d.remaining}`);
    renderKeysView();
  } else {
    toast(d.error || 'remove failed', false);
  }
}

function toast(msg, ok=true, kind=null){
  const t=document.getElementById('toast');
  // kind: 'success' (默认) | 'error' | 'warning' | 'info'
  const _kind = kind || (ok ? 'success' : 'error');
  const icon = {success:'✅', error:'❌', warning:'⚠️', info:'ℹ️'}[_kind] || '✅';
  t.textContent = icon + ' ' + msg;
  const color = {success:'#0d3b1e', error:'#3b0d0d', warning:'#3d2e0d', info:'#0d2e3d'}[_kind];
  t.style.borderColor = color;
  t.style.background = _kind === 'warning' ? 'linear-gradient(135deg, #3d2e0d 0%, #1a1a24 100%)'
                       : _kind === 'info' ? 'linear-gradient(135deg, #0d2e3d 0%, #1a1a24 100%)'
                       : t.style.background || '#1a1a24';
  t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),2500);
}
async function api(path, opts={}){
  const r=await fetch(BASE+path,{headers:{'Accept':'application/json'},...opts});
  if(!r.ok && r.status !== 404){
    let err={}; try{err=await r.json();}catch(e){}
    if(err.error) throw new Error(err.error);
  }
  return r.json();
}
async function refresh(){
  const [h,m,r,s,mo,prov,v,mh,ph,q]=await Promise.all([
    api('/v1/health').catch(e=>({error:e.message})),
    api('/v1/admin/models').catch(e=>({models:[], total:0})),
    api('/v1/admin/routes').catch(e=>({routes:[]})),
    api('/v1/admin/stats').catch(e=>({})),
    api('/v1/admin/modalities').catch(e=>({})),
    api('/v1/admin/providers').catch(e=>({providers:[]})),
    api('/v1/admin/version').catch(e=>({current:{version:'3.6.0'}})),
    api('/v1/admin/model-health').catch(e=>({health:{}, summary:{total_models:0,by_state:{healthy:0,degraded:0,skip:0,half_open:0}}})),
    api('/v1/admin/provider-health').catch(e=>({providers:[], summary:{}, config:{}})),  // v3.16.0
    api('/v1/admin/quota/status').catch(e=>({summary:{total_quota_models:0,by_type:{}}, quota_models:[]})),  // v3.18.0
  ]);
  processModelHealth(mh);  // v3.15.0: 健康度 cache + summary bar
  lastProviderHealth = ph || lastProviderHealth;  // v3.16.0: provider 健康度
  lastQuotaStatus = q || lastQuotaStatus;  // v3.18.0: 配额状态
  renderHealth(h);
  renderQuotaCard();  // v3.18.0: 配额卡片
  renderDashboard(h, m, s, mo, prov);
  renderModalities(mo);
  renderRoutesData(r);
  processModelHealth(mh);  // v3.15.0: 健康度 cache + summary bar
  // 切到 providers 视图时单独刷, 这里不刷避免与 provFilter 冲突
  if(currentView === 'providers'){
    renderProviders(prov);
  }
  renderVersion(v);
  lastVersionData = v;
  lastStatsData = s;
  lastModelsData = m;
  // v3.14.0: Provider 筛选 chip 动态生成 (基于当前 data)
  renderProviderFilter();
  // models 视图: 立即渲染第一页
  if(currentView === 'models'){
    renderModels();
  }
  // 同步 sidebar 底部版本
  const verEl = document.getElementById('navVer');
  if(verEl && v.current?.version) verEl.textContent = 'v'+v.current.version;
}
async function reloadConfig(){
  const r=await api('/v1/admin/config/reload',{method:'POST'});
  toast(r.ok?'配置已重载':'重载失败',r.ok);
  refresh();
}
async function loadModels(){
  const r=await api('/v1/admin/refresh',{method:'POST'});
  const n=Object.values(r.providers||{}).reduce((a,b)=>a+(b.models||0),0);
  toast(`已获取 ${n} 个模型`);
  refresh();
}
// R56 实战坑修法 (老大 6/22 钦定): 每点一下按钮都触发版本检测 = 真凶
// = checkVersion 没限频, 切到 version 视图 = toast 每次都弹
// 修法: 加 lastVersionToastTime 60s 限频, 60s 内同 update 不重复 toast
let lastVersionToastTime = 0;
let lastVersionToastTag = null;
async function checkVersion(forceCheck=false){
  const v=await api('/v1/admin/version'+`?force_check=${forceCheck}`);
  renderVersion(v, forceCheck);
}
// R56 实战坑修法: function 接收 forceCheck 参数, 60s 限频用
function renderVersion(v, forceCheck=false){
  const cur=v.current||{};
  const latest=v.latest_release||null;
  const verText=cur.version||v.current||'-';
  const verEl=document.getElementById('statVersion');
  if(verEl){
    verEl.innerHTML=v.has_update
      ? `<span style="color:#ff9800">⬆ ${verText}</span>`
      : `<span style="color:#4caf50">✓ ${verText}</span>`;
  }
  // R56 实战坑修法 (老大 6/22 钦定): 每点一下按钮都触发版本检测 = 真凶
  // = toast 没限频, 切到 version 视图 = toast 每次都弹
  // 修法: 60s 限频 + tag 匹配, 同 update 不重复 toast
  if(v.has_update && latest){
    const now = Date.now();
    const tag = latest.tag||latest.name||'';
    if (now - lastVersionToastTime > 60000 || lastVersionToastTag !== tag) {
      toast(`发现新版本 ${tag}`, true);
      lastVersionToastTime = now;
      lastVersionToastTag = tag;
    }
  }
  // 弹窗
  let html=`<div class="modal-overlay" id="versionModal" onclick="this.remove()">
    <div class="modal" onclick="event.stopPropagation()">
      <h2>🔔 版本信息</h2>
      <div class="version-info">
        <div class="version-row"><span>当前版本</span><strong>${cur.version||'-'}</strong></div>
        <div class="version-row"><span>构建日期</span><strong>${cur.build_date||v.build_date||'-'}</strong></div>
        <div class="version-row"><span>最新版本</span><strong>${latest?latest.tag:'-'}</strong></div>
        <div class="version-row"><span>发布日期</span><strong>${latest&&latest.published_at?new Date(latest.published_at).toLocaleDateString('zh-CN'):'-'}</strong></div>
        <div class="version-row"><span>可更新</span><strong style="color:${v.has_update?'#ff9800':'#4caf50'}">${v.has_update?'是 ⬆':'否 ✓'}</strong></div>
      </div>`;
  if(latest && latest.body){
    const desc=latest.body.length>500?latest.body.slice(0,500)+'...':latest.body;
    html+=`<div class="version-release-notes"><h3>Release Notes</h3><pre>${desc}</pre></div>`;
  }
  if(v.has_update && latest){
    html+=`<div class="version-upgrade">
      <p style="color:#ff9800;margin-bottom:8px">⬆ 有新版本可用，升级命令：</p>
      <pre style="background:#1a1a2e;padding:12px;border-radius:6px;overflow-x:auto">${latest.url?'<a href="'+latest.url+'" target="_blank" style="color:#60a5fa">查看 Release</a> 或运行: docker pull ghcr.io/IGhostHuang/supermodel_router:'+latest.tag+' && docker compose up -d':'pip install --upgrade supermodel_router'}</pre>
    </div>`;
  }
  html+=`<div style="text-align:right;margin-top:16px">
        <button class="btn-sm" onclick="checkVersion(true)">🔄 重新检查</button>
        <button class="btn" onclick="document.getElementById('versionModal').remove()">关闭</button>
      </div>
    </div>
  </div>`;
  // remove old modal if exists
  document.getElementById('versionModal')?.remove();
  document.body.insertAdjacentHTML('beforeend',html);
}
function renderHealth(h){
  if(!h) return;
  const upEl = document.getElementById('navUptime');
  if(upEl && h.uptime_seconds != null) upEl.textContent = Math.floor(h.uptime_seconds/60)+'m';
  const verEl = document.getElementById('navVer');
  if(verEl && h.version) verEl.textContent = 'v'+h.version;
}

// v3.6.0: routes 数据 (不渲染, 留给 stats 视图备用)
function renderRoutesData(r){
  // 当前 dashboard 暂不展示, 留接口给将来
  // (Phase E 简化: routes 在 providers 视图统计 "最近添加" 里隐含)
  if(r && r.routes && r.routes.length > 0){
    document.title = `SMR · ${r.routes.length} routes`;
  }
}
async function renderProviders(h){
  // v3.6: h 来自 /v1/admin/providers 列表, 含 enabled 字段
  const g=document.getElementById('providerGrid');
  const filterEl=document.getElementById('provFilter');
  const filter=filterEl?filterEl.value:'all';
  // h 是数组形式 (admin_providers_list 返回) 或 dict (旧 /v1/health)
  let arr;
  if(Array.isArray(h)){
    arr=h;
  } else if(h && h.providers && Array.isArray(h.providers)){
    arr=h.providers;
  } else {
    // 兜底: 从 health endpoint 拿 dict
    const ps=(h&&h.providers)||{};
    arr=Object.entries(ps).map(([name,p])=>({name,enabled:true,base_url:p.base_url,key_count:0,model_rules:{mode:'all'},max_concurrent:3,models:p.models,fail_count:p.fail_count,degraded:p.degraded}));
  }
  // 筛选
  if(filter==='enabled') arr=arr.filter(p=>p.enabled!==false);
  else if(filter==='disabled') arr=arr.filter(p=>p.enabled===false);
  else if(filter==='known') arr=arr.filter(p=>p.is_known);
  else if(filter==='unknown') arr=arr.filter(p=>!p.is_known);
  if(arr.length===0){
    g.innerHTML='<div class="empty-state">📭 没有匹配的 provider</div>';
    return;
  }
  g.innerHTML=arr.map(p=>{
    const enabled=p.enabled!==false;
    const isKnown=p.is_known;
    const badge=enabled?'<span class="provider-badge badge-ok">✓ 启用</span>':'<span class="provider-badge badge-disabled">⏸ 停用</span>';
    const knownTag=isKnown?'<span class="provider-tag" title="内置已知 provider, base_url 自动补全">⭐ known</span>':'';
    const mode=p.model_rules?.mode||'all';
    const modeTag=`<span class="provider-tag">${mode}</span>`;
    // v3.16.0: provider 健康度摘要 (找 cache)
    const ph = (lastProviderHealth.providers||[]).find(x => x.provider === p.name) || {};
    const ms = ph.model_states || {};
    const healthMini = (ms.total||0) > 0
      ? `<div class="provider-health-mini">`
        + `<span class="mini-chip h">✅ ${ms.healthy||0}</span>`
        + `<span class="mini-chip d">⚠️ ${ms.degraded||0}</span>`
        + `<span class="mini-chip s">🚫 ${ms.skip||0}</span>`
        + `<span class="mini-chip ho">⏳ ${ms.half_open||0}</span>`
        + `</div>`
      : '';
    // 自动禁用警告 (如果有 disabled_reason)
    let autoDisableWarn = '';
    if(!enabled && ph.disabled_reason){
      autoDisableWarn = `<div class="provider-health-warn">
        <div class="warn-title">🚫 自动禁用 (v3.16.0 健康度检测)</div>
        <div class="warn-reason">${escapeHtml(ph.disabled_reason)}</div>
        ${ph.disabled_at ? `<div class="warn-reason" style="margin-top:4px;opacity:0.7">禁用时间: ${new Date(ph.disabled_at*1000).toLocaleString('zh-CN')}</div>` : ''}
      </div>`;
    } else if(ph.will_disable_in && ph.will_disable_in > 0){
      autoDisableWarn = `<div class="provider-health-soon">⚠️ 即将自动禁用: 还有 ${Math.round(ph.will_disable_in/3600)} 小时 (所有 model SKIP ${Math.round(ph.oldest_skip_age_seconds/86400)} 天 / 阈值 ${Math.round((lastProviderHealth.config?.provider_disable_threshold_seconds||604800)/86400)} 天)</div>`;
    }
    return `<div class="provider-card ${enabled?'':'disabled'}">
      <div class="provider-info">
        <div class="provider-name">${p.name}${knownTag}${modeTag}</div>
        <div class="provider-url" title="${p.base_url}">${p.base_url}</div>
        <div class="provider-meta">
          <span title="API key 数量">🔑 ${p.key_count||0}</span>
          <span title="并发槽数">⚡ ${p.max_concurrent||3}</span>
          ${p.key_fingerprint?`<span title="key 指纹">${p.key_fingerprint}</span>`:''}
        </div>
        ${healthMini}
        ${autoDisableWarn}
      </div>
      <div class="provider-actions">
        ${badge}
        <button class="btn-sm" onclick="refreshProvider('${p.name}')" title="针对性获取该 provider 的模型">🔄 刷新</button>
        <button class="btn-sm" onclick="openEditProvider('${p.name}')" title="编辑配置">✏️ 编辑</button>
        <button class="btn-sm" onclick="cloneProvider('${p.name}')" title="复制为新 provider">📋 复制</button>
        ${enabled
          ? `<button class="btn-sm" onclick="disableProvider('${p.name}')">⏸ 停用</button>`
          : `<button class="btn-sm primary" onclick="reEnableProvider('${p.name}')" title="v3.16.0: re-enable 同时清该 provider 所有 model health">▶ 启用</button>`}
        ${!enabled
          ? `<button class="btn-sm danger" onclick="hardDeleteProvider('${p.name}')" title="彻底删除 (仅对已停用)">🗑️ 删除</button>`
          : ''}
      </div>
    </div>`;
  }).join('');
}

async function reEnableProvider(name){
  // v3.16.0: 用新端点 /v1/admin/provider-health/re-enable/{name} (清 health + re-enable)
  if(!confirm(`▶ Re-enable provider '${name}'?\n\n会清该 provider 所有 model 健康度记录, 下次调用会重新探测 (HELPFUL if 之前是自动禁用)。`)) return;
  const r = await api('/v1/admin/provider-health/re-enable/'+encodeURIComponent(name), {method:'POST'});
  if(r.error){ toast('❌ '+r.error, false); return; }
  toast(`✅ '${name}' 已 re-enable, 清了 ${r.models_reset} 个 model health`);
  await refresh();
}

// v3.6.0: Dashboard 渲染
function renderDashboard(h, m, s, mo, prov){
  // 顶部 4 个 stat 卡片 (SMR 总览)
  const providers = prov?.providers || [];
  const enabledCount = providers.filter(p=>p.enabled!==false).length;
  const disabledCount = providers.length - enabledCount;
  const totalModels = (m?.data || m?.models || []).length;
  const freeModels = (m?.data || m?.models || []).filter(x=>x.pricing_type==='free' || x.pricing==='free' || x.pricing_type==='limited_free' || x.pricing==='limited_free' || x.is_free).length;

  // Stats 汇总
  let totalCalls=0, successCalls=0, failCalls=0, dailyTokens=0, latencySum=0, latencyCount=0;
  if(s && typeof s==='object' && !Array.isArray(s)){
    for(const [k,v] of Object.entries(s)){
      if(v && typeof v==='object' && 'total_calls' in v){
        totalCalls += v.total_calls||0;
        successCalls += v.success_calls||0;
        failCalls += v.fail_calls||0;
        dailyTokens += v.daily_tokens||0;
        if(v.avg_latency_ms>0){ latencySum += v.avg_latency_ms; latencyCount++; }
      }
    }
  }
  const successRate = totalCalls > 0 ? (successCalls/totalCalls*100) : 0;
  const avgLat = latencyCount>0 ? Math.round(latencySum/latencyCount) : 0;

  const g = document.getElementById('dashboardStats');
  g.innerHTML = `
    <div class="stat-card"><div class="label">Providers</div><div class="value">${providers.length}</div><div class="delta">启用 ${enabledCount} · 停用 ${disabledCount}</div></div>
    <div class="stat-card"><div class="label">Models</div><div class="value">${totalModels}</div><div class="delta">免费 ${freeModels}</div></div>
    <div class="stat-card"><div class="label">总请求</div><div class="value">${totalCalls.toLocaleString()}</div><div class="delta">成功 ${successCalls.toLocaleString()}</div></div>
    <div class="stat-card"><div class="label">成功率</div><div class="value ${successRate>=95?'good':successRate>=80?'warn':'bad'}">${successRate.toFixed(1)}%</div><div class="delta">失败 ${failCalls.toLocaleString()}</div></div>
    <div class="stat-card"><div class="label">平均延迟</div><div class="value">${avgLat}ms</div><div class="delta">${latencyCount} providers</div></div>
    <div class="stat-card"><div class="label">今日 token</div><div class="value">${dailyTokens.toLocaleString()}</div><div class="delta">运行 ${h?.uptime_seconds ? Math.floor(h.uptime_seconds/60)+'m' : '-'}</div></div>
    <div class="stat-card"><div class="label">SMR 版本</div><div class="value" id="statVersion">-</div><div class="delta">v3.7.0</div></div>
  `;
  // v3.7.0: 暴露 version 到 dashboard stat card (修复"version 加载中")
  const verEl = document.getElementById('statVersion');
  if(verEl){
    verEl.innerHTML = `<span style="color:#4caf50">✓ ${lastVersionData?.current?.version || '3.6.0'}</span>`;
  }
  // sidebar 底部 uptime
  const upEl = document.getElementById('navUptime');
  if(upEl && h?.uptime_seconds != null) upEl.textContent = Math.floor(h.uptime_seconds/60)+'m';
  // Recent providers (前 4 个)
  const rpg = document.getElementById('providerGridRecent');
  if(rpg){
    const recent = providers.slice(0,4);
    if(recent.length === 0){ rpg.innerHTML = '<div class="empty-state">暂无 provider</div>'; return; }
    rpg.innerHTML = recent.map(p=>{
      const enabled=p.enabled!==false;
      const badge=enabled?'<span class="provider-badge badge-ok">✓</span>':'<span class="provider-badge badge-disabled">⏸</span>';
      return `<div class="provider-card ${enabled?'':'disabled'}">
        <div class="provider-info">
          <div class="provider-name">${p.name} ${badge}</div>
          <div class="provider-url">${p.base_url}</div>
        </div>
        <div class="provider-actions">
          <span class="text-muted">${p.key_count||0} keys</span>
        </div>
      </div>`;
    }).join('');
  }
  // sidebar provider badge (停用数量)
  const pb = document.getElementById('provNavBadge');
  if(pb){
    if(disabledCount > 0){ pb.style.display='inline'; pb.textContent = disabledCount; }
    else { pb.style.display='none'; }
  }
}


function getPricingInfo(m){
  const raw = String(m?.pricing_type || m?.pricing || '').toLowerCase();
  const detail = m?.pricing_detail || {};
  const detailPricing = String(detail.pricing || '').toLowerCase();
  const pricing = detailPricing || raw || (m?.is_free === true ? 'free' : 'paid');
  const desc = detail.description || (pricing === 'free' ? '免费模型' : pricing === 'limited_free' ? '有免费额度，超额后按平台规则计费' : '收费模型');
  if(pricing === 'limited_free'){
    const quota = detail.quota || {};
    const quotaText = quota.free_daily ? `每日 ${quota.free_daily} ${quota.unit || ''} 免费额度` : '有限免费额度';
    const resetText = quota.reset ? ` · ${quota.reset} 重置` : '';
    return {pricing:'free', color:'#4ade80', bg:'rgba(74,222,128,.12)', text:'免费', sub:'', desc:`${desc} · ${quotaText}${resetText}`};
  }
  if(pricing === 'free'){
    return {pricing:'free', color:'#4ade80', bg:'rgba(74,222,128,.12)', text:'免费', sub:'', desc};
  }
  return {pricing:'paid', color:'#fbbf24', bg:'rgba(251,191,36,.12)', text:'收费', sub:'', desc};
}
function renderPricingBadge(m){
  const pi = getPricingInfo(m);
  const sub = pi.sub ? `<span style="font-size:10px;opacity:.78;margin-left:4px;font-weight:600">${escapeHtml(pi.sub)}</span>` : '';
  return `<span class="pricing-badge pricing-${pi.pricing}" title="${escapeHtml(pi.desc)}" style="display:inline-flex;align-items:center;color:${pi.color};background:${pi.bg};border:1px solid ${pi.color}55;border-radius:8px;padding:4px 8px;font-weight:700;line-height:1.15">${pi.text}${sub}</span>`;
}

// ============================================================
// v3.15.0: 模型健康度 badge + summary bar (老大 2026-06-24 钦定)
// ============================================================

function processModelHealth(mhResp){
  // refresh() 收到 /v1/admin/model-health 响应 → 缓存 + 更新 summary
  if(!mhResp) return;
  lastModelHealth = mhResp.health || {};
  modelHealthSummary = mhResp.summary || null;
  lastModelHealthFetch = Date.now() / 1000;
  renderHealthSummaryBar();
  renderQuotaCard();  // v3.18.0: 同时刷新 quota card (跟 health 同步, 避免 toolbar 渲染时序问题)
}

function renderHealthBadge(m){
  // m.provider + m.id = path (跟 server 端一致)
  const path = `${m.provider||'?'}/${m.id}`;
  const h = lastModelHealth[path];
  if(!h){
    // 没数据 → 灰色 "无记录" (此 model 从未被路由过)
    return `<span class="health-badge" style="background:#1a1a24;color:#666" title="${escapeHtml(path)}\n尚未被路由调用, 无健康度数据">— 未调用</span>`;
  }
  const state = h.state || 'healthy';
  const map = {
    healthy: { cls:'health-healthy', icon:'✅', label:'健康', extra:`成功率 ${h.rolling_success_rate?.toFixed?.(0) ?? '?'}% · EWMA ${h.ewma_latency_ms?.toFixed?.(0) ?? 0}ms` },
    degraded: { cls:'health-degraded', icon:'⚠️', label:'降级', extra:`连续失败 ${h.consecutive_fails} · 成功率 ${h.rolling_success_rate?.toFixed?.(0) ?? '?'}%` },
    skip: { cls:'health-skip', icon:'🚫', label:'跳过', extra:`冷却中 ${h.skip_remaining_seconds?.toFixed?.(0) ?? 0}s · 第 ${h.skip_count} 次` },
    half_open: { cls:'health-half-open', icon:'⏳', label:'探测', extra:`后台 probe 中 · 上次 ${h.last_probe_success ? '成功' : '失败'}` },
  };
  const info = map[state] || map.healthy;
  const tooltip = `${escapeHtml(path)}\n${info.label}: ${info.extra}\n总调用 ${h.total_calls} · 成功 ${h.total_success} · 失败 ${h.total_fail}`;
  return `<span class="health-badge ${info.cls}" title="${escapeHtml(tooltip)}">${info.icon} ${info.label}</span>`;
}

function renderHealthSummaryBar(){
  // 在 toolbar 上方插入一行 summary (4 chip: 健康/降级/跳过/探测)
  let bar = document.getElementById('healthSummaryBar');
  if(!bar){
    const tb = document.querySelector('.models-toolbar');
    if(!tb) return;
    bar = document.createElement('div');
    bar.id = 'healthSummaryBar';
    bar.className = 'health-summary-bar';
    tb.parentNode.insertBefore(bar, tb.nextSibling);
  }
  const summary = modelHealthSummary || {total_models:0, by_state:{healthy:0, degraded:0, skip:0, half_open:0}};
  const bs = summary.by_state || {};
  bar.innerHTML = `
    <span style="color:#94a3b8;font-weight:600">🏥 健康度总览</span>
    <span class="health-summary-item healthy">✅ <span class="count">${bs.healthy||0}</span> 健康</span>
    <span class="health-summary-item degraded">⚠️ <span class="count">${bs.degraded||0}</span> 降级</span>
    <span class="health-summary-item skip">🚫 <span class="count">${bs.skip||0}</span> 跳过</span>
    <span class="health-summary-item half_open">⏳ <span class="count">${bs.half_open||0}</span> 探测中</span>
    <span style="color:#666;margin-left:auto">共 ${summary.total_models} 个 model · 5min 自动刷新</span>
    <button class="btn-sm" onclick="triggerProbeAll()" title="触发批量 probe (扫描 SKIP/HALF_OPEN)">🔄 立即探测</button>
  `;
}

async function triggerProbeAll(){
  const r = await api('/v1/admin/model-health/probe-all', {method:'POST'});
  if(r.error){ toast('❌ '+r.error, false); return; }
  toast('🔄 已触发批量 probe, 30s 内查结果');
  // 30s 后强制刷新一次
  setTimeout(()=>refresh(), 30000);
}

// v3.18.0: 配额卡片 (Quota Exhaustion Card)
// - 列表所有 quota_skip_until > 0 的 model
// - 每行带 [一键清] 按钮 → POST /v1/admin/quota/recover/{path}
// - 0 个时显示绿色 "无配额耗尽"
function renderQuotaCard(){
  const models = lastQuotaStatus.quota_models || [];
  let card = document.getElementById('quotaCard');
  if(!card){
    const tb = document.querySelector('.models-toolbar');
    if(!tb) return;
    card = document.createElement('div');
    card.id = 'quotaCard';
    const bar = document.getElementById('healthSummaryBar');
    // 插在 healthSummaryBar 之后 (顶部显眼位置)
    (bar || tb).parentNode.insertBefore(card, (bar || tb).nextSibling);
  }
  if(models.length === 0){
    card.innerHTML = `<div class="quota-card" style="border-color:#16a34a;background:linear-gradient(135deg,#0d3d24 0%,#1a1a24 100%);animation:none">
      <div class="quota-title" style="color:#4ade80">🟢 配额状态正常</div>
      <div class="quota-empty">无配额耗尽 model · 所有 provider 可用</div>
    </div>`;
    return;
  }
  const byType = lastQuotaStatus.summary?.by_type || {};
  const typeChips = Object.entries(byType)
    .map(([t, n]) => `<span class="quota-type ${t}">${t}: ${n}</span>`)
    .join(' ');
  const rows = models.map(m => `
    <div class="quota-row">
      <span class="quota-type ${m.quota_type}">${m.quota_type}</span>
      <span class="quota-path">${escapeHtml(m.path)}</span>
      <span class="quota-remaining">${escapeHtml(m.remaining_human || '')}</span>
      <span class="quota-actions">
        <button class="btn-sm primary" onclick="quotaRecover('${encodeURIComponent(m.path)}', '${m.quota_type}')" title="续费后 / 手动判定恢复 → 清掉 quota_skip_until + 立即可路由">💳 已续费</button>
      </span>
    </div>
  `).join('');
  card.innerHTML = `<div class="quota-card">
    <div class="quota-title">🪫 配额耗尽警告 · ${models.length} 个 model 待续费</div>
    <div class="quota-summary">${typeChips || '未知类型'} · 续费后点 [💳 已续费] 按钮一键清</div>
    <div class="quota-list">${rows}</div>
  </div>`;
}

async function quotaRecover(pathEncoded, quotaType){
  const path = decodeURIComponent(pathEncoded);
  if(!confirm(`确定已续费 [${quotaType}] ${path}?\n\n将清掉 quota_skip_until + quota_type, 立即重新可路由. 撤销不会自动恢复.`)){
    return;
  }
  try {
    const r = await api('/v1/admin/quota/recover/' + encodeURIComponent(path), {method:'POST'});
    if(r.error){ toast('❌ '+r.error, false); return; }
    toast(`✅ ${path} 配额已清 (was ${r.old_quota_type})`, true);
    refresh();  // 刷新 quota 列表
  } catch(e){
    toast('❌ recover failed: '+e.message, false);
  }
}

function escapeHtml(s){
  return String(s||'').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}

// v3.6.0: 模型分页 (v3.14.0: 多维筛选 + 排序 + 搜索)
function getFilteredSortedModels(){
  if(!lastModelsData) return [];
  const all = (lastModelsData?.data || lastModelsData?.models || []);
  return all
    .filter(m => !filterModality || m.modality === filterModality)
    .filter(m => filterProviders.size === 0 || filterProviders.has(m.provider))
    .filter(m => {
      if(!filterSearch) return true;
      const q = filterSearch.toLowerCase();
      return (m.id || '').toLowerCase().includes(q) || (m.provider || '').toLowerCase().includes(q);
    })
    .sort((a, b) => {
      const dir = sortOrder === 'asc' ? 1 : -1;
      const av = getSortValue(a, sortBy);
      const bv = getSortValue(b, sortBy);
      if(typeof av === 'string') return av.localeCompare(bv) * dir;
      return ((av||0) - (bv||0)) * dir;
    });
}
function getSortValue(m, key){
  if(key === 'capability') return m.capability_score || 0;
  if(key === 'context') return m.context_window || 0;
  if(key === 'name') return (m.id || '').toLowerCase();
  if(key === 'price'){
    // price 排: free=0, cheap=1, standard=2, premium=3 (按价位升序便宜优先)
    const pricing = m.pricing_tier || (m.pricing || '').toLowerCase() || '';
    if(pricing.includes('free')) return 0;
    if(pricing.includes('cheap') || pricing.includes('low')) return 1;
    if(pricing.includes('standard')) return 2;
    if(pricing.includes('premium') || pricing.includes('high')) return 3;
    return 2; // 默认 standard
  }
  return 0;
}
function renderModels(data){
  // 兼容 data 可能未传 (renderModels() 无参调用)
  if(data) lastModelsData = data;
  const t=document.getElementById('modelTable');
  if(!t) return;
  const all = getFilteredSortedModels();
  const totalPages = Math.max(1, Math.ceil(all.length/PAGE_SIZE));
  if(currentPage >= totalPages) currentPage = totalPages - 1;
  const start = currentPage * PAGE_SIZE;
  const pageModels = all.slice(start, start + PAGE_SIZE);
  // 头部计数
  const total = (lastModelsData?.data || lastModelsData?.models || []).length;
  const matched = all.length;
  const countEl = document.getElementById('modelCount');
  if(countEl){
    const pageInfo = totalPages > 1 ? ` · 第 ${currentPage+1}/${totalPages} 页` : '';
    countEl.textContent = matched === total
      ? `(共 ${total} 个)`
      : `(匹配 ${matched} / ${total}${pageInfo})`;
  }
  // 渲染表格
  if(pageModels.length === 0){
    const hasFilter = filterModality || filterProviders.size > 0 || filterSearch;
    t.innerHTML = `<tr><td colspan="6">
      <div class="empty-state">
        <div class="empty-icon">🔍</div>
        <div class="empty-title">${hasFilter ? '没找到匹配的模型' : '暂无模型'}</div>
        <div class="empty-desc">${hasFilter ? '试试清空筛选条件或换一个搜索词' : '请检查 SMR 配置和上游 provider 连接'}</div>
        ${hasFilter ? '<div class="empty-action"><button class="btn-sm primary" onclick="resetAllModelFilters()">↺ 清空筛选</button></div>' : ''}
      </div>
    </td></tr>`;
    document.getElementById('modelPagination').innerHTML = matched > 0 ? '' : '';
    return;
  }
  t.innerHTML=pageModels.map(m=>{
    const sc=m.capability_score||0;
    const pct=Math.min(sc,100);
    const color=sc>=80?'#4ade80':sc>=50?'#fbbf24':'#f87171';
    const priceBadge = renderPricingBadge(m);
    const healthBadge = renderHealthBadge(m);
    return `<tr>
      <td>${m.id}</td>
      <td><span class="provider-tag">${m.provider||'?'}</span></td>
      <td><span class="modality-tag ${renderModalityClass(m.modality)}">${m.modality_display||m.modality||'?'}</span></td>
      <td>${priceBadge}</td>
      <td><span class="score-bar" style="width:${pct*0.7}px;background:${color}"></span>${sc}</td>
      <td>${healthBadge}</td>
    </tr>`;
  }).join('');
  renderPagination(all.length);
}
function renderPagination(total){
  const totalPages = Math.max(1, Math.ceil(total/PAGE_SIZE));
  const pg = document.getElementById('modelPagination');
  if(!pg) return;
  if(totalPages <= 1){ pg.innerHTML = `<span class="info">共 ${total} 条</span> <select onchange="changePageSize(this.value)"><option value="10" ${PAGE_SIZE==10?'selected':''}>10/页</option><option value="20" ${PAGE_SIZE==20?'selected':''}>20/页</option><option value="50" ${PAGE_SIZE==50?'selected':''}>50/页</option><option value="100" ${PAGE_SIZE==100?'selected':''}>100/页</option></select>`; return; }
  let html = `<span class="info">共 ${total} 条</span>`;
  html += `<button onclick="goPage(0)" ${currentPage===0?'disabled':''}>« 首页</button>`;
  html += `<button onclick="goPage(${currentPage-1})" ${currentPage===0?'disabled':''}>‹ 上一页</button>`;
  const maxBtns = 7;
  let startBtn = Math.max(0, currentPage - 3);
  let endBtn = Math.min(totalPages-1, startBtn + maxBtns - 1);
  startBtn = Math.max(0, endBtn - maxBtns + 1);
  for(let i=startBtn; i<=endBtn; i++){
    html += `<button class="${i===currentPage?'active':''}" onclick="goPage(${i})">${i+1}</button>`;
  }
  html += `<button onclick="goPage(${currentPage+1})" ${currentPage>=totalPages-1?'disabled':''}>下一页 ›</button>`;
  html += `<button onclick="goPage(${totalPages-1})" ${currentPage>=totalPages-1?'disabled':''}>末页 »</button>`;
  html += `<span class="page-jump">跳到 <input type="number" min="1" max="${totalPages}" id="pageJump" value="${currentPage+1}"> / ${totalPages} 页</span>`;
  html += `<select onchange="changePageSize(this.value)"><option value="10" ${PAGE_SIZE==10?'selected':''}>10/页</option><option value="20" ${PAGE_SIZE==20?'selected':''}>20/页</option><option value="50" ${PAGE_SIZE==50?'selected':''}>50/页</option><option value="100" ${PAGE_SIZE==100?'selected':''}>100/页</option></select>`;
  pg.innerHTML = html;
  const inp = document.getElementById('pageJump');
  if(inp){ inp.onkeydown = (e)=>{ if(e.key==='Enter'){ const v=parseInt(inp.value); if(v>=1 && v<=totalPages){ goPage(v-1); } } }; }
}
function goPage(p){
  if(!lastModelsData) return;
  const all = getFilteredSortedModels();
  const totalPages = Math.max(1, Math.ceil(all.length/PAGE_SIZE));
  currentPage = Math.max(0, Math.min(totalPages-1, p));
  renderModels();
  document.getElementById('modelSection').scrollIntoView({behavior:'smooth', block:'start'});
}
function changePageSize(n){
  const newSize = parseInt(n);
  if(!lastModelsData) return;
  // 保持当前第一个可见项 (重算页码)
  const all = getFilteredSortedModels();
  const firstVisible = currentPage * PAGE_SIZE;
  PAGE_SIZE = newSize;
  currentPage = Math.floor(firstVisible / newSize);
  renderModels();
}

// v3.14.0: 筛选/排序/搜索交互
function setSearch(v){
  filterSearch = v;
  currentPage = 0;
  renderModels();
}
function setSortBy(v){
  sortBy = v;
  renderModels();
}
function toggleSortOrder(){
  sortOrder = sortOrder === 'asc' ? 'desc' : 'asc';
  const btn = document.getElementById('sortOrderBtn');
  if(btn) btn.textContent = sortOrder === 'asc' ? '↑ 升序' : '↓ 降序';
  renderModels();
}
function toggleProviderFilter(name){
  if(filterProviders.has(name)) filterProviders.delete(name);
  else filterProviders.add(name);
  renderProviderFilter();
  currentPage = 0;
  renderModels();
}
function clearProviderFilter(){
  filterProviders.clear();
  renderProviderFilter();
  currentPage = 0;
  renderModels();
}
function resetAllModelFilters(){
  filterModality = '';
  filterProviders.clear();
  filterSearch = '';
  sortBy = 'capability';
  sortOrder = 'desc';
  const searchEl = document.getElementById('modelSearch');
  if(searchEl) searchEl.value = '';
  const sortByEl = document.getElementById('modelSortBy');
  if(sortByEl) sortByEl.value = 'capability';
  const sortBtn = document.getElementById('sortOrderBtn');
  if(sortBtn) sortBtn.textContent = '↓ 降序';
  currentPage = 0;
  renderModelFilter();
  renderProviderFilter();
  renderModels();
}
function renderProviderFilter(){
  const f = document.getElementById('providerFilter');
  if(!f || !lastModelsData) return;
  const all = lastModelsData?.data || lastModelsData?.models || [];
  const providers = [...new Set(all.map(m => m.provider).filter(Boolean))].sort();
  if(providers.length === 0){
    f.innerHTML = '<span style="color:#666;font-size:11px">暂无 provider</span>';
    return;
  }
  f.innerHTML = providers.map(p =>
    `<span class="chip ${filterProviders.has(p)?'selected':''}" onclick="toggleProviderFilter('${escapeHtml(p)}')">${escapeHtml(p)}</span>`
  ).join('');
}

// v3.6.0: Stats 视图 (从 /v1/admin/stats 拿真数据)
async function renderStatsView(){
  const s = await api('/v1/admin/stats').catch(e=>({}));
  const cb = await api('/v1/admin/context_bridge').catch(e=>({}));
  const cbStats = cb?.stats || cb || {};
  lastStatsData = s;
  // 汇总
  let totalCalls=0, successCalls=0, failCalls=0, dailyTokens=0, dailyCalls=0, latencySum=0, latencyCount=0, ftSum=0, ftCount=0;
  const rows = [];
  for(const [name,v] of Object.entries(s||{})){
    if(v && typeof v==='object' && 'total_calls' in v){
      totalCalls += v.total_calls||0;
      successCalls += v.success_calls||0;
      failCalls += v.fail_calls||0;
      dailyTokens += v.daily_tokens||0;
      dailyCalls += v.daily_calls||0;
      if(v.avg_latency_ms>0){ latencySum += v.avg_latency_ms; latencyCount++; }
      if(v.avg_first_token_ms>0){ ftSum += v.avg_first_token_ms; ftCount++; }
      rows.push({name, ...v, successRate: v.total_calls>0?(v.success_calls/v.total_calls*100):0});
    }
  }
  const successRate = totalCalls>0?(successCalls/totalCalls*100):0;
  const avgLat = latencyCount>0?Math.round(latencySum/latencyCount):0;
  const avgFt = ftCount>0?Math.round(ftSum/ftCount):0;

  // 4 汇总卡片
  const sg = document.getElementById('statsSummary');
  if(sg){
    sg.innerHTML = `
      <div class="stat-card"><div class="label">总请求</div><div class="value">${totalCalls.toLocaleString()}</div><div class="delta">成功 ${successCalls.toLocaleString()} / 失败 ${failCalls.toLocaleString()}</div></div>
      <div class="stat-card"><div class="label">成功率</div><div class="value ${successRate>=95?'good':successRate>=80?'warn':'bad'}">${successRate.toFixed(1)}%</div><div class="delta">${successRate>=95?'✅ 健康':successRate>=80?'⚠️ 关注':'❌ 异常'}</div></div>
      <div class="stat-card"><div class="label">平均延迟</div><div class="value">${avgLat}ms</div><div class="delta">首 token ${avgFt}ms</div></div>
      <div class="stat-card"><div class="label">今日</div><div class="value">${dailyCalls.toLocaleString()}</div><div class="delta">${dailyTokens.toLocaleString()} tokens</div></div>
    `;
  }
  // provider 表
  const tb = document.getElementById('providerStatsTable');
  if(tb){
    if(rows.length === 0){
      tb.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#666;padding:20px">暂无 stats 数据 (调用一次 /v1/chat/completions 后出现)</td></tr>';
    } else {
      rows.sort((a,b)=>(b.total_calls||0)-(a.total_calls||0));
      tb.innerHTML = rows.map(r=>{
        const srColor = r.successRate>=95?'#4ade80':r.successRate>=80?'#fbbf24':'#f87171';
        return `<tr>
          <td><span class="provider-tag">${r.name}</span></td>
          <td>${(r.total_calls||0).toLocaleString()}</td>
          <td>${(r.success_calls||0).toLocaleString()}</td>
          <td style="color:${r.fail_calls>0?'#f87171':'#666'}">${(r.fail_calls||0).toLocaleString()}</td>
          <td>${(r.daily_calls||0).toLocaleString()}</td>
          <td>${(r.daily_tokens||0).toLocaleString()}</td>
          <td>${r.avg_latency_ms||0}ms</td>
          <td>${r.avg_first_token_ms||0}ms</td>
        </tr>`;
      }).join('');
    }
  }
  // ContextBridge stats
  const cbg = document.getElementById('contextBridgeStats');
  if(cbg){
    if(cbStats && Object.keys(cbStats).length > 0){
      cbg.textContent = JSON.stringify(cbStats, null, 2);
    } else {
      cbg.textContent = '(暂无数据 — 调用一次带切链的请求后出现)';
    }
  }
}

// v3.6.0: API Keys 视图 (Phase G 实现)
async function renderKeysView(){
  const g = document.getElementById('keysList');
  if(!g) return;
  g.innerHTML = '<div class="loading">加载中...</div>';
  const r = await api('/v1/admin/api-keys').catch(e=>({error:e.message, keys:[]}));
  if(r.error || !r.keys){
    g.innerHTML = `<div class="empty-state">🔑 API Key 管理 API 暂不可用<br><span class="text-muted">${r.error||'请确认 SMR v3.6.0+ 已部署'}</span></div>`;
    return;
  }
  if(r.keys.length === 0){
    g.innerHTML = '<div class="empty-state">🔑 暂无 API keys<br><span class="text-muted">去 Providers 视图添加 provider + key 后, 这里会显示脱敏指纹</span></div>';
    return;
  }
  g.innerHTML = r.keys.map(k=>{
    return `<div class="provider-card" style="cursor:default">
      <div class="provider-info">
        <div class="provider-name">${k.provider} <span class="provider-tag">${k.count} keys</span></div>
        <div class="provider-url">指纹: ${k.fingerprint||'-'}</div>
        <div class="provider-meta"><span>添加于 ${k.created_at||'-'}</span></div>
      </div>
      <div class="provider-actions">
        <button class="btn-sm" onclick="addApiKey('${k.provider}')">➕ 加 key</button>
        <button class="btn-sm danger" onclick="deleteApiKey('${k.provider}')">🗑️ 清空</button>
      </div>
    </div>`;
  }).join('');
}
async function addApiKey(provider){
  const key = prompt(`为 provider "${provider}" 添加新的 API key:\n(留空取消)`);
  if(!key) return;
  const r = await api('/v1/admin/api-keys', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({provider, api_key: key})
  }).catch(e=>({error:e.message}));
  if(r.error){ toast(r.error, false); return; }
  toast(`✅ 已为 ${provider} 添加 key`);
  renderKeysView();
}
async function deleteApiKey(provider){
  if(!confirm(`⚠️ 清空 provider "${provider}" 的所有 API keys?`)) return;
  const r = await api('/v1/admin/api-keys/'+encodeURIComponent(provider), {method:'DELETE'}).catch(e=>({error:e.message}));
  if(r.error){ toast(r.error, false); return; }
  toast(`🗑️ ${provider} keys 已清空`);
  renderKeysView();
  refresh();
}

// v3.6.0: Version 视图 (修复 GitHub 404 噪音)
async function renderVersionView(){
  const v = await api('/v1/admin/version').catch(e=>({current:{version:'3.6.0'}, error:e.message}));
  lastVersionData = v;
  renderVersion(v);
  // 额外: 渲染 version grid
  const g = document.getElementById('versionGrid');
  if(!g) return;
  const cur=v.current||{};
  const latest=v.latest_release||null;
  // v3.6.0: latest 为 null (GitHub 404) 不显示错误, 改为 "未配置"
  const latestTag = latest?.tag || '未配置';
  const latestDate = latest?.published_at ? new Date(latest.published_at).toLocaleDateString('zh-CN') : '-';
  const updateStatus = v.has_update ? '⬆ 有更新' : '✓ 最新';
  const updateColor = v.has_update ? 'warn' : 'good';
  // 如果 latest_release 缺失 (GitHub 404 / 无 token), 不当 bug
  const releaseNotes = latest?.body ? latest.body.slice(0, 500) : null;
  g.innerHTML = `
    <div class="stat-card"><div class="label">当前版本</div><div class="value good">v${cur.version||'3.6.0'}</div><div class="delta">${cur.build_date||''}</div></div>
    <div class="stat-card"><div class="label">最新版本</div><div class="value">${latestTag}</div><div class="delta">${latestDate}</div></div>
    <div class="stat-card"><div class="label">状态</div><div class="value ${updateColor}">${updateStatus}</div><div class="delta">${v.error?'GitHub 检查失败 (非阻塞)':''}</div></div>
    <div class="stat-card"><div class="label">构建</div><div class="value">${cur.title||'SMR'}</div><div class="delta">${v.has_update && latest ? `<a href="${latest.url}" target="_blank" style="color:#5b8def">查看 Release</a>` : '无需更新'}</div></div>
  `;
  // release notes
  if(releaseNotes){
    g.insertAdjacentHTML('afterend', `<div class="empty-state" style="text-align:left;white-space:pre-wrap;margin-top:14px"><strong>Release Notes</strong>\n\n${releaseNotes}</div>`);
  }
}
function renderModalities(data){
  const g=document.getElementById('modalityGrid');
  if(!data||Object.keys(data).length===0){
    g.innerHTML='<div style="color:#666">暂无分类数据</div>';
    return;
  }
  const emoji={'text-only':'📝','multimodal':'🖼️','image-gen':'🎨','video-gen':'🎬','audio-gen':'🎵','embedding':'📊'};
  g.innerHTML=Object.entries(data).map(([mod,cnt])=>{
    const e=emoji[mod]||'❓';
    return `<div class="modality-card"><div class="emoji">${e}</div><div class="count">${cnt}</div><div class="label">${mod}</div></div>`;
  }).join('');
}
function renderModelFilter(){
  const f=document.getElementById('modalityFilter');
  const emoji={'text-only':'📝','multimodal':'🖼️','image-gen':'🎨','video-gen':'🎬','audio-gen':'🎵','embedding':'📊','':'🌐 全部'};
  f.innerHTML=Object.entries(emoji).map(([mod,e])=>
    `<button class="${filterModality===mod?'active':''}" onclick="setFilter('${mod}')">${e} ${mod||'全部'}</button>`
  ).join('');
}
function setFilter(mod){filterModality=mod;renderModelFilter();refresh();}
function renderModalityClass(modality){
  const cls={'text-only':'modality-text-only','multimodal':'modality-multimodal',
    'image-gen':'modality-image-gen','video-gen':'modality-video-gen','audio-gen':'modality-audio-gen'};
  return cls[modality]||'modality-text-only';
}// v3.6.0: 复制 provider (Phase F)
async function cloneProvider(name){
  const newName = prompt(`复制 provider "${name}" 为新 provider.\n请输入新名称:`, name+'_copy');
  if(!newName || newName === name) return;
  const r = await api('/v1/admin/providers/'+encodeURIComponent(name)+'/clone', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({new_name: newName})
  }).catch(e=>({error:e.message}));
  if(r.error){ toast(r.error, false); return; }
  toast(`📋 已复制为 "${newName}"`);
  await refreshProviders();
  await refresh();
}

// v3.6.0: 导出 provider 配置 (Phase F)
async function exportProviders(){
  const r = await api('/v1/admin/providers/export').catch(e=>({error:e.message}));
  if(r.error || !r.providers){
    toast(r.error || '导出失败', false);
    return;
  }
  const data = JSON.stringify(r, null, 2);
  const blob = new Blob([data], {type: 'application/json'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `smr-providers-${new Date().toISOString().slice(0,10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
  toast(`✅ 已导出 ${r.providers.length} 个 provider 配置`);
}

// v3.6.0: 导入 provider 配置 (Phase F)
async function importProviders(event){
  const file = event.target.files[0];
  if(!file) return;
  const text = await file.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch(e){
    // 尝试 yaml
    toast('❌ 仅支持 JSON 格式 (导出的格式)', false);
    event.target.value = '';
    return;
  }
  if(!data.providers || !Array.isArray(data.providers)){
    toast('❌ 文件格式错误: 缺少 providers 数组', false);
    event.target.value = '';
    return;
  }
  if(!confirm(`确认导入 ${data.providers.length} 个 provider?`)) {
    event.target.value = '';
    return;
  }
  const r = await api('/v1/admin/providers/import', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(data)
  }).catch(e=>({error:e.message}));
  event.target.value = '';
  if(r.error){ toast(r.error, false); return; }
  toast(`✅ 导入完成: 新增 ${r.added||0} · 跳过 ${r.skipped||0} · 失败 ${r.failed||0}`);
  await refreshProviders();
  await refresh();
}

refresh();

// ============================================================
// 自定义 Provider 管理
// ============================================================

let currentClassifier = null;

async function openAddProvider(){
  document.getElementById('addProvModal').classList.add('show');
}
function closeAddProvider(){
  document.getElementById('addProvModal').classList.remove('show');
}
async function submitAddProvider(){
  const name=document.getElementById('provName').value.trim();
  const base_url=document.getElementById('provUrl').value.trim();
  const api_keys_raw=document.getElementById('provKeys').value.trim();
  const mode=document.getElementById('provMode').value;
  const pattern=document.getElementById('provPattern').value.trim();
  const include_raw=document.getElementById('provInclude').value.trim();
  const exclude_raw=document.getElementById('provExclude')?.value.trim() || '';
  const max_concurrent=parseInt(document.getElementById('provMax').value)||3;
  if (!name || !base_url || !api_keys_raw) {
    toast('请填写 name / base_url / api_keys', false);
    return;
  }
  const api_keys = api_keys_raw.split('\n').map(s=>s.trim()).filter(Boolean);
  const include = include_raw ? include_raw.split('\n').map(s=>s.trim()).filter(Boolean) : [];
  const exclude = exclude_raw ? exclude_raw.split('\n').map(s=>s.trim()).filter(Boolean) : [];
  const model_rules = {mode};
  if (mode === 'pattern' && pattern) model_rules.pattern = pattern;
  if (mode === 'include' && include.length) model_rules.include = include;
  if (exclude.length) model_rules.exclude = exclude;

  const btn=event.target;
  btn.disabled=true; btn.textContent='添加中...';
  const r = await api('/v1/admin/providers', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      name,
      config: { base_url, api_keys, model_rules, max_concurrent, enabled: true },
    }),
  });
  btn.disabled=false; btn.textContent='添加';
  if (r.error) { toast(r.error, false); return; }
  const normHint = r.config?.base_url && r.config.base_url !== base_url
    ? ` (已自动补全为 ${r.config.base_url})` : '';
  toast(`✅ Provider '${name}' added${normHint}`, true);
  closeAddProvider();
  // v3.6: 自动刷新 (不靠 refresh() 整体, 单独刷 providers + routes)
  await refreshProviders();
  await refresh();
  await renderRoutes();
}

async function enableProvider(name){
  if (!confirm(`启用 provider '${name}'?`)) return;
  const r = await api('/v1/admin/providers/'+encodeURIComponent(name)+'/enable', {method:'POST'});
  if (r.error) { toast(r.error, false); return; }
  toast(`▶ '${name}' 已启用`);
  await refreshProviders();
  await refresh();
  await renderRoutes();
}

async function disableProvider(name){
  if (!confirm(`停用 provider '${name}'?\n(配置保留, 路由立即移除, 可重新启用)`)) return;
  const r = await api('/v1/admin/providers/'+encodeURIComponent(name)+'/disable', {method:'POST'});
  if (r.error) { toast(r.error, false); return; }
  toast(`⏸ '${name}' 已停用`);
  await refreshProviders();
  await refresh();
  await renderRoutes();
}

async function refreshProvider(name){
  const btn=event?.target;
  const origText=btn?.textContent;
  if(btn){btn.disabled=true; btn.textContent='⏳ 拉取中...';}
  const r = await api('/v1/admin/providers/'+encodeURIComponent(name)+'/refresh', {method:'POST'});
  if(btn){btn.disabled=false; btn.textContent=origText;}
  if (r.error) { toast(r.error, false); return; }
  toast(`🔄 '${name}' 正在拉取模型... ${r.hint||''}`);
  // 2s 后自动刷新列表 (等 async refresh 完成)
  setTimeout(async()=>{ await refreshProviders(); await refresh(); }, 2000);
}

async function hardDeleteProvider(name){
  if (!confirm(`⚠️ 彻底删除 provider '${name}'?\n\n配置从 config.yaml 永久移除, 不可恢复!\n\n(只有已停用的 provider 才能删除)`)) return;
  const r = await api('/v1/admin/providers/'+encodeURIComponent(name)+'?force=true', {method:'DELETE'});
  if (r.error) { toast(r.error, false); return; }
  toast(`🗑️ '${name}' 已彻底删除`);
  await refreshProviders();
  await refresh();
  await renderRoutes();
}

async function deleteProvider(name){
  // v3.6: 软删除 (默认走这个, 用户能恢复)
  if (!confirm(`停用 provider '${name}'?\n(配置保留, 可在筛选栏"停用"里找到并重新启用或彻底删除)`)) return;
  const r = await api('/v1/admin/providers/' + encodeURIComponent(name), {method: 'DELETE'});
  if (r.error) { toast(r.error, false); return; }
  toast(`⏸ '${name}' 已停用 (可恢复)`);
  await refreshProviders();
  await refresh();
  await renderRoutes();
}

// v3.6: 单独刷 providers 列表 (不刷其他)
async function refreshProviders(){
  const r = await api('/v1/admin/providers');
  if (r && r.providers) {
    await renderProviders(r);
  }
}

// v3.6: 渲染 routes 列表 (用新格式)
async function renderRoutes(){
  const g=document.getElementById('routesGrid');
  if(!g) return;
  const r = await api('/v1/admin/routes');
  if(!r || !r.routes){ g.innerHTML='<div class="empty-state">暂无路由</div>'; return; }
  if(r.routes.length===0){ g.innerHTML='<div class="empty-state">暂无路由</div>'; return; }
  // r.routes 现在是对象数组 [{route, provider, model, pricing}]
  if(typeof r.routes[0]==='string'){
    // 旧格式
    g.innerHTML=r.routes.map(rt=>`<div class="route-item">${rt}</div>`).join('');
  } else {
    g.innerHTML=r.routes.map(rt=>{
      const pi = getPricingInfo(rt);
      return `<div class="route-item">
        <span class="route-path">${rt.route}</span>
        <span class="route-pricing" title="${escapeHtml(pi.desc)}" style="color:${pi.color}">${pi.text}</span>
      </div>`;
    }).join('');
  }
}

// v3.6: 编辑 provider 模态框
async function openEditProvider(name){
  const r = await api('/v1/admin/providers');
  const p = r.providers?.find(x=>x.name===name);
  if(!p){toast('找不到 provider '+name, false); return;}
  // 复用 add modal, 但填入现有值 + 改标题 + 改 submit handler
  document.getElementById('addProviderTitle').textContent='✏️ 编辑 Provider: '+name;
  document.getElementById('provName').value=name;
  document.getElementById('provName').disabled=true;
  document.getElementById('provUrl').value=p.base_url;
  document.getElementById('provKeys').value='';  // 不显示 key, 让用户重新填 (空 = 不改)
  document.getElementById('provMode').value=p.model_rules?.mode||'all';
  document.getElementById('provPattern').value=p.model_rules?.pattern||'';
  document.getElementById('provInclude').value=(p.model_rules?.include||[]).join('\n');
  document.getElementById('provExclude').value=(p.model_rules?.exclude||[]).join('\n');
  document.getElementById('provMax').value=p.max_concurrent||3;
  // 显示/隐藏 mode 子字段
  document.getElementById('patternField').style.display=p.model_rules?.mode==='pattern'?'block':'none';
  document.getElementById('includeField').style.display=p.model_rules?.mode==='include'?'block':'none';
  document.getElementById('excludeField').style.display=(p.model_rules?.exclude||[]).length>0?'block':'none';
  // 改 submit 按钮: 调 PUT 而非 POST
  const btn=document.getElementById('addProviderBtn');
  btn.textContent='保存修改';
  btn.onclick=async()=>await submitEditProvider(name);
  document.getElementById('addProviderModal').classList.add('open');
}

async function submitEditProvider(name){
  const base_url=document.getElementById('provUrl').value.trim();
  const api_keys_raw=document.getElementById('provKeys').value.trim();
  const mode=document.getElementById('provMode').value;
  const pattern=document.getElementById('provPattern').value.trim();
  const include_raw=document.getElementById('provInclude').value.trim();
  const exclude_raw=document.getElementById('provExclude')?.value.trim() || '';
  const max_concurrent=parseInt(document.getElementById('provMax').value)||3;
  if (!base_url) { toast('请填写 base_url', false); return; }
  const pcfg = { base_url, max_concurrent, enabled: true, model_rules: {mode} };
  if(mode==='pattern' && pattern) pcfg.model_rules.pattern = pattern;
  if(mode==='include') {
    const inc = include_raw ? include_raw.split('\n').map(s=>s.trim()).filter(Boolean) : [];
    pcfg.model_rules.include = inc;
  }
  if(exclude_raw) {
    pcfg.model_rules.exclude = exclude_raw.split('\n').map(s=>s.trim()).filter(Boolean);
  }
  if(api_keys_raw) {
    pcfg.api_keys = api_keys_raw.split('\n').map(s=>s.trim()).filter(Boolean);
  }
  const btn=event.target;
  btn.disabled=true; btn.textContent='保存中...';
  const r = await api('/v1/admin/providers/'+encodeURIComponent(name), {
    method:'PUT', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({config: pcfg})
  });
  btn.disabled=false; btn.textContent='保存修改';
  if (r.error) { toast(r.error, false); return; }
  toast(`✅ '${name}' 已更新 (base_url=${r.config?.base_url})`);
  closeAddProvider();
  await refreshProviders();
  await refresh();
  await renderRoutes();
}

function closeAddProvider(){
    document.getElementById('addProviderModal').classList.remove('open');
    // v3.6: 重置 submit handler 给 add 用
    const btn=document.getElementById('addProviderBtn');
    if(btn){ btn.textContent='添加'; btn.onclick=null; btn.onclick=submitAddProvider; }
    const nameInput=document.getElementById('provName');
    if(nameInput) nameInput.disabled=false;
    document.getElementById('addProviderTitle').textContent='➕ 添加 Provider';
    // 清空表单
    ['provName','provUrl','provKeys','provPattern','provInclude','provExclude','provUrlPreset'].forEach(id=>{
      const el=document.getElementById(id); if(el) el.value='';
    });
  }


// ============================================================
// 对外 API (per-tenant key) 管理 — v3.7.0
// ============================================================

async function renderPublicApiView(){
  const summaryEl = document.getElementById('publicApiSummary');
  const listEl = document.getElementById('publicKeyList');
  if(summaryEl) summaryEl.innerHTML = '<div class="loading">加载中...</div>';
  if(listEl) listEl.innerHTML = '<div class="loading">加载中...</div>';
  const r = await api('/v1/admin/public-keys/usage').catch(e=>({error:e.message}));
  if(r.error){
    if(summaryEl) summaryEl.innerHTML = `<div class="empty-state">❌ ${r.error}</div>`;
    return;
  }
  lastPublicKeys = r.keys || [];  // 给 editPublicKey 用
  const enabledCount = r.enabled_keys || 0;
  const totalKeys = r.total_keys || 0;
  let totalCalls = 0, totalTokens = 0, totalSuccess = 0;
  for(const k of (r.keys||[])){
    const u = k.usage || {};
    totalCalls += u.total_calls || 0;
    totalTokens += u.tokens || 0;
    totalSuccess += u.success_calls || 0;
  }
  const successRate = totalCalls > 0 ? (totalSuccess/totalCalls*100).toFixed(1) : 0;
  if(summaryEl){
    summaryEl.innerHTML = `
      <div class="stat-card"><div class="label">总 Key 数</div><div class="value">${totalKeys}</div><div class="delta">启用 ${enabledCount}</div></div>
      <div class="stat-card"><div class="label">总请求</div><div class="value">${totalCalls.toLocaleString()}</div><div class="delta">来自对外 API</div></div>
      <div class="stat-card"><div class="label">总 Token</div><div class="value">${totalTokens.toLocaleString()}</div><div class="delta">成功 ${totalSuccess.toLocaleString()}</div></div>
      <div class="stat-card"><div class="label">成功率</div><div class="value ${successRate>=95?'good':successRate>=80?'warn':'bad'}">${successRate}%</div></div>
    `;
  }
  if(!listEl) return;
  if((r.keys||[]).length === 0){
    listEl.innerHTML = '<div class="empty-state">🌐 暂无对外 API key, 点 "➕ 创建 Key" 开始</div>';
    return;
  }
  listEl.innerHTML = (r.keys||[]).map(k => {
    const enabled = k.enabled !== false;
    const u = k.usage || {};
    const lastUsed = u.last_used ? new Date(u.last_used*1000).toLocaleString('zh-CN') : '未使用';
    const rateLimit = k.rate_limit_rpm > 0
      ? `<span class="provider-tag" style="background:#1e3a8a;color:#bfdbfe">🚦 ${k.rate_limit_rpm} rpm</span>`
      : `<span class="provider-badge badge-ok" style="font-size:11px">∞ 不限速</span>`;
    const models = (k.model_filter||[]).length === 0 ? '<span class="text-muted">全部</span>' : (k.model_filter||[]).slice(0,3).map(m=>`<span class="provider-tag">${m}</span>`).join('') + ((k.model_filter||[]).length>3?` <span class="text-muted">+${k.model_filter.length-3}</span>`:'');
    return `<div class="provider-card ${enabled?'':'disabled'}" style="flex-direction:column;align-items:stretch">
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <div>
          <div class="provider-name">${k.name} ${enabled?'<span class="provider-badge badge-ok">✓ 启用</span>':'<span class="provider-badge badge-disabled">⏸ 停用</span>'}</div>
          <div class="provider-url" title="${k.key_hash}">🔐 哈希 ${k.key_hash}</div>
          <div class="provider-meta">
            <span>🚦 ${rateLimit}</span>
            <span>📊 ${u.total_calls||0} 次 · ${u.success_calls||0} 成功</span>
            <span>🪙 ${(u.tokens||0).toLocaleString()} tokens</span>
            <span>🕐 ${lastUsed}</span>
          </div>
          <div class="provider-meta" style="margin-top:4px">模型白名单: ${models}</div>
          ${k.note?`<div class="text-muted" style="margin-top:4px;font-size:12px">📝 ${k.note}</div>`:''}
        </div>
        <div class="provider-actions" style="flex-direction:column;gap:4px">
          <button class="btn-sm" onclick="editPublicKey('${k.name}')" title="改速率限制 / 白名单 / 备注">✏️ 编辑</button>
          ${enabled
            ? `<button class="btn-sm" onclick="togglePublicKey('${k.name}', false)">⏸ 停用</button>`
            : `<button class="btn-sm primary" onclick="togglePublicKey('${k.name}', true)">▶ 启用</button>`}
          <button class="btn-sm" onclick="resetPublicKeyUsage('${k.name}')" title="清零用量计数">🔄 重置用量</button>
          <button class="btn-sm" onclick="showUsageByModel('${k.name}')" title="查看按 model 分组的用量统计">📊 按 model</button>
          <button class="btn-sm danger" onclick="deletePublicKey('${k.name}')">🗑️ 删除</button>
        </div>
      </div>
    </div>`;
  }).join('');
}

let lastPublicKeys = null;
let lastModelGroups = null;  // 缓存 model-groups (给白名单编辑器用)

// ============================================================
// v3.9.0: inline modal 替代 prompt 弹窗 (老大 16:55 拍)
// 白名单编辑器支持:
//   - text 形式 (老): openai/gpt-4o, *:free
//   - @provider 形式 (新): @openrouter 整 provider
//   - group:xxx 形式 (新): group:claude 整分组
//   - 智能建议: 输入时联想现有 model
// ============================================================

async function loadModelGroupsCache(force=false){
  if(lastModelGroups && !force) return lastModelGroups;
  const r = await api('/v1/admin/model-groups');
  lastModelGroups = r.groups || [];
  return lastModelGroups;
}

async function loadProvidersCache(){
  const r = await api('/v1/admin/providers');
  return r.providers || [];
}

let allModelsCache = null;
async function loadAllModelsCache(){
  if(allModelsCache) return allModelsCache;
  try {
    const r = await api('/v1/admin/models');
    allModelsCache = r.models || r.data || [];
  } catch(e) {
    allModelsCache = [];
  }
  return allModelsCache;
}

function openCreatePublicKey(){
  showPublicKeyModal(null);
}

function editPublicKey(name){
  const k = lastPublicKeys?.find?.(x=>x.name===name);
  if(!k){ toast('❌ 找不到 key '+name, false); return; }
  showPublicKeyModal(k);
}

async function showPublicKeyModal(existing){
  const isEdit = !!existing;
  const name = existing?.name || '';
  const rpm = existing?.rate_limit_rpm ?? 60;
  const modelFilter = existing?.model_filter || [];
  const note = existing?.note || '';
  const enabled = existing?.enabled !== false;

  const [groups, providers, allModels] = await Promise.all([
    loadModelGroupsCache(), loadProvidersCache(), loadAllModelsCache()
  ]);
  const enabledProviders = providers.filter(p => p.enabled).map(p => p.name);

  const html = `
<div class="modal-bg show" id="publicKeyModal" onclick="if(event.target===this)closePublicKeyModal()">
  <div class="modal" style="max-width:780px;max-height:90vh;overflow-y:auto">
    <h3>${isEdit ? '✏️ 编辑 Key: '+name : '➕ 创建对外 API Key'}</h3>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px">
      <div>
        <label>名称 * <span class="text-muted">(字母数字/-/_)</span></label>
        <input id="pkName" value="${name.replace(/"/g,'&quot;')}" ${isEdit?'disabled':''} placeholder="e.g. user-alice" ${isEdit?'':'autofocus'}>
      </div>
      <div>
        <label>速率限制 (rpm) <span class="text-muted">(0 = 不限)</span></label>
        <input id="pkRpm" type="number" min="0" value="${rpm}" oninput="updateRpmHint()">
        <div class="rpm-shortcut-group">
          <button type="button" class="rpm-shortcut unlimited" onclick="setRpmValue(0)">∞ 不限</button>
          <button type="button" class="rpm-shortcut" onclick="setRpmValue(60)">60</button>
          <button type="button" class="rpm-shortcut" onclick="setRpmValue(300)">300</button>
          <button type="button" class="rpm-shortcut" onclick="setRpmValue(1000)">1000</button>
        </div>
        <div class="rpm-hint" id="pkRpmHint">💡 0=不限速 &gt;0=每分钟最大请求数</div>
      </div>
    </div>
    <label style="margin-top:10px;display:block">状态</label>
    <select id="pkEnabled" style="width:200px">
      <option value="true" ${enabled?'selected':''}>✅ 启用</option>
      <option value="false" ${!enabled?'selected':''}>⏸ 停用</option>
    </select>
    <h4 style="margin-top:16px;font-size:14px;color:#94a3b8">🔓 模型白名单
      <span class="text-muted" style="font-weight:normal;font-size:12px">空 = 全部允许</span>
    </h4>
    <div id="pkChips" style="min-height:32px;padding:8px;background:#0f172a;border-radius:6px;margin-bottom:8px;display:flex;flex-wrap:wrap;gap:6px"></div>
    <div style="position:relative;margin-bottom:12px">
      <input id="pkAddModel" placeholder="输入 model / @provider / group:名, Enter 添加" style="font-family:monospace">
      <div id="pkSuggestions" style="position:absolute;top:100%;left:0;right:0;background:#1e293b;border:1px solid #334155;border-radius:4px;max-height:200px;overflow-y:auto;display:none;z-index:10"></div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px">
      <div>
        <div class="text-muted" style="font-size:12px;margin-bottom:4px">📦 整 provider (一键全开):</div>
        <div id="pkProviders" style="display:flex;flex-direction:column;gap:4px;max-height:140px;overflow-y:auto;background:#0f172a;padding:8px;border-radius:4px">
          ${enabledProviders.length===0 ? '<div class="text-muted" style="font-size:12px">暂无可用 provider</div>' :
            enabledProviders.map(p => `<label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px"><input type="checkbox" data-pk-shortcut="@${p}"><span>@${p}</span></label>`).join('')}
        </div>
      </div>
      <div>
        <div class="text-muted" style="font-size:12px;margin-bottom:4px">🏷️ 整 model group (一键全开):</div>
        <div id="pkGroups" style="display:flex;flex-direction:column;gap:4px;max-height:140px;overflow-y:auto;background:#0f172a;padding:8px;border-radius:4px">
          ${groups.length===0 ? '<div class="text-muted" style="font-size:12px">暂无 model group, 点左侧导航 🏷️ 模型分组 创建</div>' :
            groups.map(g => `<label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px" title="${(g.patterns||[]).join(', ')}"><input type="checkbox" data-pk-shortcut="group:${g.name}" ${g.enabled?'':'disabled'}><span>group:${g.name}</span><span class="text-muted" style="font-size:11px">(${g.model_count||0})</span></label>`).join('')}
        </div>
      </div>
    </div>
    <label style="margin-top:14px;display:block">备注</label>
    <input id="pkNote" value="${note.replace(/"/g,'&quot;')}" placeholder="e.g. echo R21 测试 key">
    <div class="row" style="margin-top:16px;justify-content:flex-end;gap:8px">
      <button class="btn-sm" onclick="closePublicKeyModal()">取消</button>
      <button class="btn primary" onclick="submitPublicKeyModal(${isEdit?'true':'false'})">${isEdit ? '💾 保存' : '➕ 创建'}</button>
    </div>
  </div>
</div>`;
  const div = document.createElement('div');
  div.innerHTML = html;
  document.body.appendChild(div.firstElementChild);

  window._pkCurrentFilter = [...modelFilter];
  renderPkChips();

  const input = document.getElementById('pkAddModel');
  const sugDiv = document.getElementById('pkSuggestions');
  input.addEventListener('input', () => {
    const q = input.value.trim().toLowerCase();
    if(!q){ sugDiv.style.display='none'; return; }
    const matches = allModels
      .filter(m => (m.id||'').toLowerCase().includes(q) || (m.provider||'').toLowerCase().includes(q))
      .slice(0, 10)
      .map(m => `<div class="pk-sug-item" data-full="${(m.provider+'/'+m.id).replace(/"/g,'&quot;')}" style="padding:6px 10px;cursor:pointer;font-family:monospace;font-size:12px;border-bottom:1px solid #334155">${m.provider}/${m.id}</div>`).join('');
    sugDiv.innerHTML = matches || '<div class="text-muted" style="padding:8px;font-size:12px">无匹配 model</div>';
    sugDiv.style.display = 'block';
  });
  input.addEventListener('keydown', (e) => {
    if(e.key === 'Enter'){
      e.preventDefault();
      const v = input.value.trim();
      if(v && !window._pkCurrentFilter.includes(v)){ window._pkCurrentFilter.push(v); renderPkChips(); }
      input.value = '';
      sugDiv.style.display = 'none';
    } else if(e.key === 'Escape'){
      sugDiv.style.display = 'none';
    }
  });
  sugDiv.addEventListener('click', (e) => {
    const item = e.target.closest('.pk-sug-item');
    if(!item) return;
    const full = item.dataset.full;
    if(full && !window._pkCurrentFilter.includes(full)){ window._pkCurrentFilter.push(full); renderPkChips(); }
    input.value = '';
    sugDiv.style.display = 'none';
  });
  document.addEventListener('click', (e) => {
    if(!input.contains(e.target) && !sugDiv.contains(e.target)){ sugDiv.style.display = 'none'; }
  });

  document.querySelectorAll('[data-pk-shortcut]').forEach(cb => {
    cb.addEventListener('change', (e) => {
      const v = e.target.dataset.pkShortcut;
      if(e.target.checked){
        if(!window._pkCurrentFilter.includes(v)) window._pkCurrentFilter.push(v);
      } else {
        window._pkCurrentFilter = window._pkCurrentFilter.filter(x => x !== v);
      }
      renderPkChips();
    });
    if(window._pkCurrentFilter.includes(cb.dataset.pkShortcut)) cb.checked = true;
  });
}

function renderPkChips(){
  const div = document.getElementById('pkChips');
  if(!div) return;
  if(!window._pkCurrentFilter || window._pkCurrentFilter.length === 0){
    div.innerHTML = '<span class="text-muted" style="font-size:12px;padding:6px">空 = 全部模型允许 (无限制)</span>';
    return;
  }
  div.innerHTML = window._pkCurrentFilter.map(m => {
    let badge = '🔹', color = '#64748b';
    if(m.startsWith('@')){ badge = '📦'; color = '#0ea5e9'; }
    else if(m.startsWith('group:')){ badge = '🏷️'; color = '#a855f7'; }
    else if(m.includes('*')){ badge = '✨'; color = '#f59e0b'; }
    return `<span style="background:${color};color:#fff;padding:3px 8px;border-radius:12px;font-size:12px;display:inline-flex;align-items:center;gap:4px;font-family:monospace">${badge} ${m.replace(/</g,'&lt;')}<span style="cursor:pointer;margin-left:4px;opacity:0.7" onclick="removePkChip('${m.replace(/'/g,"\\'")}')">×</span></span>`;
  }).join('');
}

function removePkChip(m){
  window._pkCurrentFilter = window._pkCurrentFilter.filter(x => x !== m);
  document.querySelectorAll(`[data-pk-shortcut="${m.replace(/"/g,'\\"')}"]`).forEach(cb => cb.checked = false);
  renderPkChips();
}

// ============================================================
// v3.14.0: rpm 快捷按钮 + key 创建成功 modal (修"无法复制" + "rpm=0 不生效")
// ============================================================

function setRpmValue(val){
  const el = document.getElementById('pkRpm');
  if(!el) return;
  el.value = val;
  updateRpmHint();
  // 视觉反馈: input 边框闪一下
  el.style.transition = 'border-color .3s ease, box-shadow .3s ease';
  el.style.borderColor = val === 0 ? '#4ade80' : '#5b8def';
  el.style.boxShadow = `0 0 0 3px ${val === 0 ? 'rgba(74,222,128,.25)' : 'rgba(91,141,239,.25)'}`;
  setTimeout(() => { el.style.borderColor = ''; el.style.boxShadow = ''; }, 600);
}

function updateRpmHint(){
  const el = document.getElementById('pkRpm');
  const hint = document.getElementById('pkRpmHint');
  if(!el || !hint) return;
  const v = parseInt(el.value, 10);
  if(isNaN(v)){
    hint.innerHTML = '💡 输入数字,0=不限速';
    hint.style.color = '#94a3b8';
  } else if(v === 0){
    hint.innerHTML = '✅ <strong style="color:#4ade80">不限速</strong> — 适合内部高吞吐客户端';
    hint.style.color = '#4ade80';
  } else if(v < 10){
    hint.innerHTML = `⚠️ <strong style="color:#fbbf24">${v} rpm</strong> — 较严格,每 ${(60/v).toFixed(1)}s 才允许 1 个请求`;
    hint.style.color = '#fbbf24';
  } else if(v < 100){
    hint.innerHTML = `🚦 <strong style="color:#bfdbfe">${v} rpm</strong> — 标准限频`;
    hint.style.color = '#bfdbfe';
  } else {
    hint.innerHTML = `🚀 <strong style="color:#bfdbfe">${v} rpm</strong> — 高频,${v} 次/分 ≈ ${(v/60).toFixed(1)} 次/秒`;
    hint.style.color = '#bfdbfe';
  }
}

async function copyToClipboardText(text, btn){
  try {
    if(navigator.clipboard && navigator.clipboard.writeText){
      await navigator.clipboard.writeText(text);
    } else {
      // fallback: 旧浏览器用 textarea
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    if(btn){
      const orig = btn.innerHTML;
      btn.classList.add('copied');
      btn.innerHTML = '✅ 已复制到剪贴板';
      setTimeout(() => { btn.classList.remove('copied'); btn.innerHTML = orig; }, 1800);
    }
    return true;
  } catch(e){
    if(btn) btn.innerHTML = '❌ 复制失败,请手动选择文字';
    return false;
  }
}

function downloadKeyTxt(r){
  const content = `# SMR 对外 API Key - ${r.name}\n# 创建时间: ${new Date().toLocaleString('zh-CN')}\n# 速率限制: ${r.rate_limit_rpm} rpm\n# 白名单: ${(r.model_filter||[]).join(', ') || '全部'}\n# 备注: ${r.note || '(无)'}\n\n原 Key (妥善保存,关闭后无法再次查看):\n${r.key}\n\n哈希:\n${r.key_hash}\n`;
  const blob = new Blob([content], {type: 'text/plain;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `smr-key-${r.name}-${new Date().toISOString().slice(0,10)}.txt`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function showKeyCreatedModal(r){
  // 移除旧 modal (如果存在)
  const old = document.getElementById('keyCreatedModal');
  if(old) old.remove();

  const html = `
<div class="modal-bg show" id="keyCreatedModal" onclick="if(event.target===this)document.getElementById('keyCreatedModal').remove()">
  <div class="modal" style="max-width:640px" onclick="event.stopPropagation()">
    <h3 style="display:flex;align-items:center;gap:8px">
      <span style="font-size:24px">🎉</span>
      <span>Key '${r.name}' 已创建</span>
    </h3>
    <div class="key-notice-box">
      <div class="key-notice-icon">⚠️</div>
      <div>
        <div class="key-notice-title">原 key 仅显示一次,关闭后将无法再次查看</div>
        <div class="key-notice-desc">请立即保存到本地密码管理器 / .env / 密钥库。<br>若丢失,只能删除重建。</div>
      </div>
    </div>
    <div style="margin-top:8px;font-size:12px;color:#94a3b8;display:flex;justify-content:space-between;align-items:center">
      <span>🔑 原 Key (点击文字全选)</span>
      <span class="text-muted" id="keyAutoCopyHint">📋 尝试自动复制中...</span>
    </div>
    <div class="key-display-box" id="keyDisplayBox" tabindex="0">${r.key}</div>
    <div class="key-action-row">
      <button class="btn-copy" id="keyCopyBtn" onclick="copyToClipboardText(document.getElementById('keyDisplayBox').textContent, this)">
        📋 一键复制 Key
      </button>
      <button class="btn-download" onclick="downloadKeyTxt({name:'${r.name.replace(/'/g,"\\'")}',key:document.getElementById('keyDisplayBox').textContent,key_hash:'${r.key_hash}',rate_limit_rpm:${r.rate_limit_rpm},model_filter:${JSON.stringify(r.model_filter||[])},note:'${(r.note||'').replace(/'/g,"\\'")}'})">
        📥 下载 .txt 备份
      </button>
    </div>
    <details style="margin-top:14px;font-size:12px;color:#94a3b8">
      <summary style="cursor:pointer;user-select:none">📦 查看完整元数据</summary>
      <div style="background:#0f172a;padding:10px 12px;border-radius:6px;margin-top:8px;font-family:monospace;line-height:1.7">
        <div>🔐 哈希: <code style="color:#94a3b8">${r.key_hash}</code></div>
        <div>🚦 速率: <code style="color:${r.rate_limit_rpm > 0 ? '#bfdbfe' : '#4ade80'}">${r.rate_limit_rpm > 0 ? r.rate_limit_rpm + ' rpm' : '∞ 不限速'}</code></div>
        <div>🎯 白名单: <code style="color:#bfdbfe">${(r.model_filter||[]).join(', ') || '全部'}</code></div>
        <div>📝 备注: <code style="color:#94a3b8">${r.note || '(无)'}</code></div>
      </div>
    </details>
    <div class="row" style="margin-top:18px;justify-content:flex-end">
      <button class="btn primary" id="keyCloseBtn" onclick="document.getElementById('keyCreatedModal').remove()">✅ 我已保存,关闭</button>
    </div>
  </div>
</div>`;
  const div = document.createElement('div');
  div.innerHTML = html;
  document.body.appendChild(div.firstElementChild);

  // 自动 focus + select key 文字
  const keyBox = document.getElementById('keyDisplayBox');
  setTimeout(() => { keyBox.focus(); selectText(keyBox); }, 50);

  // 尝试自动复制
  setTimeout(async () => {
    const hint = document.getElementById('keyAutoCopyHint');
    const ok = await copyToClipboardText(r.key, null);
    if(hint) hint.innerHTML = ok ? '✅ 已自动复制到剪贴板' : '⚠️ 自动复制失败,请手动点 📋 按钮';
    if(ok) toast('✅ Key 已自动复制到剪贴板 (请立即粘贴保存)', true, 'info');
  }, 200);

  // Esc 关闭 + Enter 关闭
  const escHandler = (e) => {
    if(e.key === 'Escape'){
      const m = document.getElementById('keyCreatedModal');
      if(m) m.remove();
      document.removeEventListener('keydown', escHandler);
    }
  };
  document.addEventListener('keydown', escHandler);
}

function selectText(el){
  const range = document.createRange();
  range.selectNodeContents(el);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
}

function closePublicKeyModal(){
  const m = document.getElementById('publicKeyModal');
  if(m) m.remove();
  window._pkCurrentFilter = null;
}

async function submitPublicKeyModal(isEdit){
  const name = document.getElementById('pkName').value.trim();
  if(!name){ toast('❌ 名称必填', false); return; }
  if(!/^[a-zA-Z0-9_-]+$/.test(name)){ toast('❌ 名称只能含字母数字/-/_', false); return; }
  // v3.14.0 修: parseInt(...) || 60 短路 0=60 (老大反馈"rpm=0 不生效"), 用显式 NaN 校验
  const _rpmRaw = document.getElementById('pkRpm').value.trim();
  const rpm = _rpmRaw === '' ? 60 : Math.max(0, parseInt(_rpmRaw, 10) || 0);
  const enabled = document.getElementById('pkEnabled').value === 'true';
  const note = document.getElementById('pkNote').value;
  const modelFilter = window._pkCurrentFilter || [];

  if(isEdit){
    const r = await api('/v1/admin/public-keys/'+encodeURIComponent(name), {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({rate_limit_rpm: rpm, model_filter: modelFilter, note, enabled}),
    });
    if(r.error){ toast('❌ '+r.error, false); return; }
    toast(`✅ '${name}' 已更新`);
    closePublicKeyModal();
    await renderPublicApiView();
  } else {
    const r = await api('/v1/admin/public-keys', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, rate_limit_rpm: rpm, model_filter: modelFilter, note}),
    });
    if(r.error){ toast('❌ '+r.error, false); return; }
    // v3.14.0 修: 原生 alert 文字不可复制, 改用自定义 modal (可复制 + 自动剪贴板 + .txt 下载)
    closePublicKeyModal();
    showKeyCreatedModal(r);
    await renderPublicApiView();
  }
}

async function togglePublicKey(name, enabled){
  const r = await api('/v1/admin/public-keys/'+encodeURIComponent(name), {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({enabled}),
  });
  if(r.error){ toast(r.error, false); return; }
  toast(`✅ '${name}' 已${enabled?'启用':'停用'}`);
  await renderPublicApiView();
}

async function deletePublicKey(name){
  if(!confirm(`⚠️ 确认删除 '${name}'?\n此操作不可撤销!`)) return;
  const r = await api('/v1/admin/public-keys/'+encodeURIComponent(name), {method: 'DELETE'});
  if(r.error){ toast(r.error, false); return; }
  toast(`🗑️ '${name}' 已删除`);
  await renderPublicApiView();
}

async function resetPublicKeyUsage(name){
  if(!confirm(`重置 '${name}' 的用量计数?\n(只清零 total/success/fail/tokens, key 本身不变)`)) return;
  const r = await api('/v1/admin/public-keys/'+encodeURIComponent(name)+'/reset', {method: 'POST'});
  if(r.error){ toast(r.error, false); return; }
  toast(`🔄 '${name}' 用量已清零`);
  await renderPublicApiView();
}

// ============================================================
// v3.9.0: 模型分组管理 (model_groups)
// ============================================================

async function renderModelGroupsView(){
  await loadModelGroupsCache(true);
  const groups = lastModelGroups || [];
  const total = groups.length;
  const enabled = groups.filter(g => g.enabled !== false).length;
  const totalModels = groups.reduce((s, g) => s + Number(g.model_count || 0), 0);
  const summaryEl = document.getElementById('modelGroupsSummary');
  if(summaryEl){
    summaryEl.innerHTML = `
      <div class="stat-card"><div class="label">总分组</div><div class="value">${total}</div><div class="delta">group:* 白名单可引用</div></div>
      <div class="stat-card"><div class="label">已启用</div><div class="value">${enabled}</div><div class="delta">停用 ${total-enabled}</div></div>
      <div class="stat-card"><div class="label">匹配模型</div><div class="value">${totalModels}</div><div class="delta">按每个分组实时解析</div></div>
    `;
  }
  const listEl = document.getElementById('modelGroupsList');
  if(!listEl) return;
  if(groups.length === 0){
    listEl.innerHTML = '<div class="empty-state">🏷️ 暂无模型分组，点击上方「➕ 创建分组」开始；例如 pattern 写 <code>qwen.*</code> 或 <code>openai/.*gpt.*</code></div>';
    return;
  }
  listEl.innerHTML = groups.map(g => {
    const safeName = escapeHtml(g.name);
    const jsName = JSON.stringify(g.name || '');
    const count = Number(g.model_count || 0);
    const patterns = (g.patterns || []).map(p => `<code>${escapeHtml(p)}</code>`).join(' ');
    const samples = (g.resolved_sample || []).slice(0, 5).map(m => `<code>${escapeHtml(m)}</code>`).join(' ');
    const more = count > 5 ? `<span class="text-muted">+${count - 5}</span>` : '';
    const statusBadge = g.enabled !== false
      ? '<span class="provider-badge badge-ok">启用</span>'
      : '<span class="provider-badge badge-disabled">停用</span>';
    return `<div class="group-card">
      <div class="group-card-head">
        <div>
          <div class="group-title"><span>🏷️ group:${safeName}</span>${statusBadge}<span class="group-count">${count} models</span></div>
          <div class="group-desc">${escapeHtml(g.description || '未填写描述')}</div>
        </div>
        <div class="group-actions">
          <button class="btn-sm" onclick='showResolvedGroup(${jsName})' title="查看当前匹配的所有 model">🔍 解析</button>
          <button class="btn-sm" onclick='openEditModelGroup(${jsName})'>✏️ 编辑</button>
          <button class="btn-sm danger" onclick='deleteModelGroup(${jsName})'>🗑️ 删除</button>
        </div>
      </div>
      <div class="group-patterns"><span class="text-muted">patterns:</span> ${patterns || '<span class="text-muted">空</span>'}</div>
      <div class="group-samples"><span class="text-muted">样本:</span> ${samples || '<span class="text-muted">无匹配模型</span>'}${more}</div>
    </div>`;
  }).join('');
}

function openCreateModelGroup(){
  showModelGroupModal(null);
}

async function openEditModelGroup(name){
  const g = (lastModelGroups || []).find(x => x.name === name);
  if(!g){ toast('❌ 找不到分组 '+name, false); return; }
  showModelGroupModal(g);
}

async function showModelGroupModal(existing){
  const isEdit = !!existing;
  const name = existing?.name || '';
  const patterns = existing?.patterns || [];
  const description = existing?.description || '';
  const enabled = existing?.enabled !== false;
  const html = `
<div class="modal-bg show" id="modelGroupModal" onclick="if(event.target===this)closeModelGroupModal()">
  <div class="modal" style="max-width:600px">
    <h3>${isEdit ? '✏️ 编辑分组: '+escapeHtml(name) : '➕ 创建模型分组'}</h3>
    <label>名称 * <span class="text-muted">(字母数字/-/_)</span></label>
    <input id="mgName" value="${name.replace(/"/g,'&quot;')}" ${isEdit?'disabled':''} placeholder="例如 qwen-free 或 long-context">
    <label style="margin-top:10px;display:block">patterns (正则列表, 1 行 1 个) *</label>
    <textarea id="mgPatterns" rows="5" style="width:100%;font-family:monospace;font-size:13px"
              placeholder="qwen.*&#10;openai/.*gpt.*&#10;.*:free$">${patterns.join('\n').replace(/</g,'&lt;')}</textarea>
    <label style="margin-top:10px;display:block">描述</label>
    <input id="mgDesc" value="${description.replace(/"/g,'&quot;')}" placeholder="例如 Qwen 系列 / 免费模型 / 长上下文模型">
    <label style="margin-top:10px;display:block">状态</label>
    <select id="mgEnabled" style="width:200px">
      <option value="true" ${enabled?'selected':''}>✅ 启用</option>
      <option value="false" ${!enabled?'selected':''}>⏸ 停用</option>
    </select>
    <div class="row" style="margin-top:16px;justify-content:flex-end;gap:8px">
      <button class="btn-sm" onclick="closeModelGroupModal()">取消</button>
      <button class="btn primary" onclick="submitModelGroupModal(${isEdit?'true':'false'})">${isEdit ? '💾 保存' : '➕ 创建'}</button>
    </div>
  </div>
</div>`;
  const div = document.createElement('div');
  div.innerHTML = html;
  document.body.appendChild(div.firstElementChild);
}

function closeModelGroupModal(){
  const m = document.getElementById('modelGroupModal');
  if(m) m.remove();
}

async function submitModelGroupModal(isEdit){
  const name = document.getElementById('mgName').value.trim();
  if(!name){ toast('❌ 名称必填', false); return; }
  if(!/^[a-zA-Z0-9_-]+$/.test(name)){ toast('❌ 名称只能含字母数字/-/_', false); return; }
  const patterns = document.getElementById('mgPatterns').value.split('\n').map(s=>s.trim()).filter(Boolean);
  if(patterns.length === 0){ toast('❌ patterns 至少 1 个', false); return; }
  const description = document.getElementById('mgDesc').value;
  const enabled = document.getElementById('mgEnabled').value === 'true';

  if(isEdit){
    const r = await api('/v1/admin/model-groups/'+encodeURIComponent(name), {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({patterns, description, enabled}),
    });
    if(r.error){ toast('❌ '+r.error, false); return; }
    toast(`✅ group '${name}' 已更新`);
  } else {
    const r = await api('/v1/admin/model-groups', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, patterns, description, enabled}),
    });
    if(r.error){ toast('❌ '+r.error, false); return; }
    toast(`✅ group '${name}' 已创建 (匹配 ${(r.group&&r.group.model_count)||r.model_count||0} models)`);
  }
  lastModelGroups = null;  // 清缓存
  closeModelGroupModal();
  await renderModelGroupsView();
}

async function deleteModelGroup(name){
  if(!confirm(`⚠️ 确认删除 group '${name}'?\n使用 group:${name} 的白名单会立即失效!`)) return;
  const r = await api('/v1/admin/model-groups/'+encodeURIComponent(name), {method: 'DELETE'});
  if(r.error){ toast(r.error, false); return; }
  toast(`🗑️ group '${name}' 已删除`);
  lastModelGroups = null;
  await renderModelGroupsView();
}

async function showResolvedGroup(name){
  const g = (lastModelGroups || []).find(x => x.name === name);
  if(!g) return;
  const r = await api('/v1/admin/model-groups/'+encodeURIComponent(name)+'/resolve');
  const list = (r.resolved_models || []).map(m => `<code style="display:inline-block;background:#0f172a;padding:2px 6px;border-radius:3px;margin:2px;font-size:11px">${m.replace(/</g,'&lt;')}</code>`).join('');
  const html = `
<div class="modal-bg show" onclick="this.remove()">
  <div class="modal" style="max-width:700px" onclick="event.stopPropagation()">
    <h3>🔍 group:${escapeHtml(name)} 解析结果 (${(r.resolved_models||[]).length} models)</h3>
    <div style="max-height:60vh;overflow-y:auto;padding:8px;background:#0f172a;border-radius:6px">
      ${list || '<div class="text-muted">无匹配 model (检查 patterns 或 provider 是否已加载)</div>'}
    </div>
    <div class="row" style="margin-top:12px;justify-content:flex-end">
      <button class="btn-sm" onclick="this.closest('.modal-bg').remove()">关闭</button>
    </div>
  </div>
</div>`;
  const div = document.createElement('div');
  div.innerHTML = html;
  document.body.appendChild(div.firstElementChild);
}

// Inline onclick hooks: keep model-group buttons clickable even if the dashboard JS is
// later bundled/minified under stricter scope rules.
window.openCreateModelGroup = openCreateModelGroup;
window.openEditModelGroup = openEditModelGroup;
window.closeModelGroupModal = closeModelGroupModal;
window.submitModelGroupModal = submitModelGroupModal;
window.deleteModelGroup = deleteModelGroup;
window.showResolvedGroup = showResolvedGroup;

// ============================================================
// Tier Bonus / Classifier 管理
// ============================================================

async function openClassifier(){
  const r = await api('/v1/admin/classifier');
  currentClassifier = r;
  renderClassifier(r);
  document.getElementById('classifierModal').classList.add('show');
}
function closeClassifier(){
  document.getElementById('classifierModal').classList.remove('show');
}
function renderClassifier(data){
  const {configured, defaults} = data;
  // tier_bonus
  const tierMerged = {...defaults.tier_bonus, ...(configured.tier_bonus || {})};
  const tierDiv = document.getElementById('tierBonusEditor');
  tierDiv.innerHTML = '';
  for (const [k, v] of Object.entries(tierMerged)) {
    tierDiv.appendChild(makeKVEditor(k, v, 'tier_bonus'));
  }
  // custom_keywords
  const kwDiv = document.getElementById('customKwEditor');
  kwDiv.innerHTML = '';
  for (const [k, v] of Object.entries((configured.custom_keywords || {}))) {
    kwDiv.appendChild(makeKVEditor(k, v, 'custom_keywords'));
  }
  // modality_base_score
  const modMerged = {...defaults.modality_base_score, ...(configured.modality_base_score || {})};
  const modDiv = document.getElementById('modScoreEditor');
  modDiv.innerHTML = '';
  for (const [k, v] of Object.entries(modMerged)) {
    modDiv.appendChild(makeKVEditor(k, v, 'modality_base_score'));
  }
}
function makeKVEditor(key, value, group){
  const wrap = document.createElement('div');
  wrap.className = 'kv-edit';
  wrap.innerHTML = `
    <input type="text" value="${key.replace(/"/g,'&quot;')}" placeholder="keyword" style="flex:1">
    <input type="number" value="${value}" placeholder="score" style="width:100px">
    <button class="btn-sm danger" onclick="this.parentElement.remove()">×</button>
  `;
  wrap.dataset.group = group;
  return wrap;
}
function addKVEditor(group){
  const map = {
    'tier_bonus': 'tierBonusEditor',
    'custom_keywords': 'customKwEditor',
    'modality_base_score': 'modScoreEditor',
  };
  document.getElementById(map[group]).appendChild(makeKVEditor('', 0, group));
}
async function saveClassifier(){
  const collect = (divId) => {
    const out = {};
    document.querySelectorAll('#'+divId+' .kv-edit').forEach(row => {
      const [kInput, vInput] = row.querySelectorAll('input');
      const k = kInput.value.trim();
      const v = parseInt(vInput.value);
      if (k && !isNaN(v)) out[k] = v;
    });
    return out;
  };
  const payload = {
    tier_bonus: collect('tierBonusEditor'),
    custom_keywords: collect('customKwEditor'),
    modality_base_score: collect('modScoreEditor'),
  };
  // v3.7.0: 删行警告 — 计算将消失的 key (相对内置默认 + 当前配置)
  const orig = currentClassifier || {};
  const defaultsTier = orig.defaults?.tier_bonus || {};
  const configuredTier = orig.configured?.tier_bonus || {};
  const origTierKeys = new Set([...Object.keys(defaultsTier), ...Object.keys(configuredTier)]);
  const newTierKeys = new Set(Object.keys(payload.tier_bonus));
  const removedTier = [...origTierKeys].filter(k => !newTierKeys.has(k) && !(k in defaultsTier) || (k in configuredTier && !newTierKeys.has(k)));
  if (removedTier.length > 0) {
    const msg = `⚠️ 将删除 ${removedTier.length} 个 tier_bonus 自定义配置:\n${removedTier.map(k => `  - ${k}`).join('\n')}\n\n服务端会先自动备份 (24h 内可恢复), 确认继续?`;
    if (!confirm(msg)) return;
  }
  const r = await api('/v1/admin/classifier', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  if (r.error) { toast(r.error, false); return; }
  toast(`已更新: ${(r.updated||[]).join(', ')} · 备份 ${r.backup_id||'已自动'}`);
  closeClassifier();
  refresh();
}

// ============================================================
// Server / Routing 手动修改
// ============================================================

async function openServer(){
  const [srv, rt] = await Promise.all([
    api('/v1/admin/server'),
    api('/v1/admin/routing'),
  ]);
  document.getElementById('srvHost').value = srv.host || '0.0.0.0';
  document.getElementById('srvPort').value = srv.port || 6473;
  document.getElementById('srvApiKey').value = '';  // 不回显, 显式输入
  document.getElementById('srvApiKey').placeholder = srv.api_key ? '已设置 (留空不改)' : '可选, Bearer 鉴权';
  document.getElementById('rtStrategy').value = rt.strategy || 'flat';
  document.getElementById('rtFailover').value = rt.failover_threshold || 3;
  document.getElementById('rtRecovery').value = rt.recovery_interval || 300;
  document.getElementById('rtMaxRetry').value = rt.max_retry || 2;
  document.getElementById('rtFirstToken').value = rt.first_token_timeout_ms || 10000;
  // v3.9.0 (Phase H): group-based 轮询
  document.getElementById('rtGroupStrategy').value = rt.group_strategy || 'round-robin-group';
  document.getElementById('rtGroupWeights').value = JSON.stringify(rt.group_weights || {}, null, 2);
  document.getElementById('serverModal').classList.add('show');
}
function closeServer(){
  document.getElementById('serverModal').classList.remove('show');
}
async function saveServer(){
  const srvPayload = {};
  const host = document.getElementById('srvHost').value.trim();
  const port = parseInt(document.getElementById('srvPort').value);
  const apiKey = document.getElementById('srvApiKey').value;
  if (host) srvPayload.host = host;
  if (!isNaN(port) && port > 0) srvPayload.port = port;
  if (apiKey) srvPayload.api_key = apiKey;

  const rtPayload = {};
  const strategy = document.getElementById('rtStrategy').value;
  const failover = parseInt(document.getElementById('rtFailover').value);
  const recovery = parseInt(document.getElementById('rtRecovery').value);
  const maxRetry = parseInt(document.getElementById('rtMaxRetry').value);
  const firstToken = parseInt(document.getElementById('rtFirstToken').value);
  rtPayload.strategy = strategy;
  if (!isNaN(failover)) rtPayload.failover_threshold = failover;
  if (!isNaN(recovery)) rtPayload.recovery_interval = recovery;
  if (!isNaN(maxRetry)) rtPayload.max_retry = maxRetry;
  if (!isNaN(firstToken)) rtPayload.first_token_timeout_ms = firstToken;
  // v3.9.0 (Phase H): group-based 轮询
  rtPayload.group_strategy = document.getElementById('rtGroupStrategy').value;
  const weightsRaw = document.getElementById('rtGroupWeights').value.trim();
  if (weightsRaw) {
    try {
      rtPayload.group_weights = JSON.parse(weightsRaw);
      if (typeof rtPayload.group_weights !== 'object' || Array.isArray(rtPayload.group_weights)) {
        toast('Group Weights 必须是 JSON 对象 {"name": weight}', false);
        return;
      }
    } catch (e) {
      toast('Group Weights JSON 解析失败: ' + e.message, false);
      return;
    }
  } else {
    rtPayload.group_weights = {};
  }

  const r1 = await api('/v1/admin/server', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(srvPayload),
  });
  if (r1.error) { toast('Server: ' + r1.error, false); return; }

  const r2 = await api('/v1/admin/routing', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(rtPayload),
  });
  if (r2.error) { toast('Routing: ' + r2.error, false); return; }

  let msg = `Server: ${(r1.updated||[]).join(',') || '(no change)'} | Routing: ${(r2.updated||[]).join(',')}`;
  if (r1.restart_required) {
    msg += ' ⚠️ 需重启服务';
  }
  toast(msg, !r1.restart_required);
  closeServer();
  refresh();
}

// ---- v3.2.0: 配置历史 Modal ----
function openConfigBackups(){
  document.getElementById('configBackupsModal').classList.add('show');
  loadConfigBackups();
}
function closeConfigBackups(){
  document.getElementById('configBackupsModal').classList.remove('show');
}
async function loadConfigBackups(){
  const el = document.getElementById('configBackupsList');
  el.innerHTML = '<div class="text-muted">加载中...</div>';
  const r = await api('/v1/admin/config/backups');
  if (r.error) { el.innerHTML = `<div style="color:#f87171">❌ ${r.error}</div>`; return; }
  const backups = r.backups || [];
  if (!backups.length) {
    el.innerHTML = '<div class="text-muted">暂无备份 (每次写 config.yaml 会自动生成)</div>';
    return;
  }
  el.innerHTML = backups.map(b => {
    const ago = b.age_seconds < 60 ? `${Math.round(b.age_seconds)}s 前`
              : b.age_seconds < 3600 ? `${Math.round(b.age_seconds/60)}min 前`
              : b.age_seconds < 86400 ? `${Math.round(b.age_seconds/3600)}h 前`
              : `${Math.round(b.age_seconds/86400)}d 前`;
    return `
      <div style="display:flex;align-items:center;gap:10px;padding:8px;border-bottom:1px solid #333">
        <div style="flex:1">
          <div style="font-size:12px;color:#94a3b8">${b.name}</div>
          <div style="font-size:11px;color:#666">${b.mtime_iso} · ${ago} · ${(b.size_bytes/1024).toFixed(1)}KB</div>
        </div>
        <button class="btn-sm" onclick="restoreConfigBackup('${b.name}')">↩️ 回滚</button>
      </div>
    `;
  }).join('');
}
async function restoreConfigBackup(name){
  if (!confirm(`确认回滚到 ${name}?\n当前 config.yaml 会先备份再覆盖, 可再回滚一次。`)) return;
  const r = await api('/v1/admin/config/restore', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name}),
  });
  if (r.error) { toast('回滚失败: ' + r.error, false); return; }
  toast(`✅ 已回滚到 ${r.restored_from}`, true);
  closeConfigBackups();
  refresh();
}
</script>

<!-- Add/Edit Provider Modal -->
<div class="modal-bg" id="addProviderModal">
  <div class="modal" style="max-width:600px">
    <h3 id="addProviderTitle">➕ 添加自定义 Provider</h3>
    <div class="text-muted">支持任何 OpenAI 兼容 API (OpenAI / Azure / 自建 / 中转 / newapi 等)</div>
    <label>Provider 名称 * <span class="text-muted">(添加时必填, 编辑时禁用)</span></label>
    <input id="provName" placeholder="myopenai">
    <label>Base URL * <span class="text-muted">(选择平台或填自定义 URL, 知名 provider 自动补 https:// + /v1)</span></label>
    <select id="provUrlPreset" onchange="onPresetUrlChange()" style="margin-bottom:6px">
      <option value="">-- 选择平台 (可选) --</option>
      <option value="https://api.openai.com/v1">OpenAI (官方)</option>
      <option value="https://openrouter.ai/api/v1">OpenRouter</option>
      <option value="https://api.deepseek.com/v1">DeepSeek</option>
      <option value="https://generativelanguage.googleapis.com/v1beta/openai">Google Gemini (OpenAI 兼容)</option>
      <option value="https://api.anthropic.com/v1">Anthropic Claude (OpenAI 兼容)</option>
      <option value="https://api.mistral.ai/v1">Mistral AI</option>
      <option value="https://api.groq.com/openai/v1">Groq</option>
      <option value="https://api.together.xyz/v1">Together AI</option>
      <option value="https://api.fireworks.ai/inference/v1">Fireworks AI</option>
      <option value="https://api.perplexity.ai">Perplexity</option>
      <option value="https://dashscope.aliyuncs.com/compatible-mode/v1">阿里云 DashScope (Qwen)</option>
      <option value="https://ark.cn-beijing.volces.com/api/v3">字节火山方舟 (Ark)</option>
      <option value="https://api.moonshot.cn/v1">月之暗面 Moonshot</option>
      <option value="https://api.zhipuai.cn/v1">智谱 GLM</option>
      <option value="https://api.baichuan-ai.com/v1">百川</option>
      <option value="https://api.stepfun.com/v1">StepFun (阶跃星辰)</option>
      <option value="https://api.x.ai/v1">xAI (Grok)</option>
      <option value="https://api.cohere.ai/v1">Cohere</option>
      <option value="https://api.replicate.com/v1">Replicate</option>
      <option value="https://integrate.api.nvidia.com/v1">NVIDIA NIM</option>
      <option value="https://api.siliconflow.cn/v1">硅基流动 (SiliconFlow)</option>
      <option value="__custom__">⚙️ 自定义 URL (下面手动填)</option>
    </select>
    <input id="provUrl" placeholder="https://api.openai.com/v1" oninput="document.getElementById('provUrlPreset').value='__custom__'">
    <label>API Keys * <span class="text-muted">(一行一个, 自动轮询. 编辑时留空 = 不修改)</span></label>
    <textarea id="provKeys" placeholder="sk-xxx&#10;sk-yyy"></textarea>
    <label>Model Filter Mode</label>
    <select id="provMode" onchange="onModeChange()">
      <option value="all">all (全部模型)</option>
      <option value="pattern">pattern (正则匹配)</option>
      <option value="include">include (白名单)</option>
    </select>
    <div id="patternField" style="display:none">
      <label>Pattern (正则)</label>
      <div class="pattern-builder">
        <div class="pattern-row">
          <span class="pattern-op">包含</span>
          <input id="patternHas" placeholder="gpt-4 或 -free" oninput="syncPattern()">
        </div>
        <div class="pattern-row">
          <span class="pattern-op">不包含</span>
          <input id="patternNot" placeholder="vision 或 embed" oninput="syncPattern()">
        </div>
        <div class="pattern-hint">生成的正则: <code id="patternPreview">(留空)</code></div>
        <input id="provPattern" type="hidden">
        <details class="pattern-raw">
          <summary>编辑原始正则</summary>
          <input id="provPatternRaw" placeholder=".*-free$|.*free.*" oninput="document.getElementById('provPattern').value=this.value">
        </details>
      </div>
    </div>
    <div id="includeField" style="display:none">
      <label>Include (白名单, 一行一个)</label>
      <textarea id="provInclude" placeholder="gpt-4o&#10;gpt-4-turbo"></textarea>
    </div>
    <div id="excludeField" style="display:none">
      <label>Exclude (黑名单, 一行一个) <span class="text-muted">(永远从结果中排除, 优先于 include)</span></label>
      <textarea id="provExclude" placeholder="gpt-4-vision&#10;*-embed-*"></textarea>
    </div>
    <label>Max Concurrent Slots</label>
    <input id="provMax" type="number" value="3" min="1" max="20">
    <div class="row">
      <button class="btn" id="addProviderBtn" onclick="submitAddProvider()">添加</button>
      <button class="btn-sm" onclick="closeAddProvider()">取消</button>
    </div>
  </div>
</div>

<script>
// v3.6: 正则表达式友好生成
function syncPattern(){
  const has = document.getElementById('patternHas').value.trim();
  const not = document.getElementById('patternNot').value.trim();
  const parts = [];
  if(has) parts.push(has.split(/\s+/).filter(Boolean).map(s=>s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('.*'));
  if(not) parts.push(not.split(/\s+/).filter(Boolean).map(s=>'(?!.*'+s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')+').*').join(''));
  const pat = parts.length ? parts.join('|') : '';
  document.getElementById('provPattern').value = pat;
  document.getElementById('patternPreview').textContent = pat || '(留空 = 匹配所有)';
}
function onModeChange(){
  const m = document.getElementById('provMode').value;
  document.getElementById('patternField').style.display = m==='pattern' ? 'block' : 'none';
  document.getElementById('includeField').style.display = m==='include' ? 'block' : 'none';
  // exclude 总是显示 (它跟所有模式配合)
  document.getElementById('excludeField').style.display = m==='all' ? 'none' : 'block';
}
// v3.6.0: 打开 add modal (新的, 不带 edit)
function openAddProvider(){
  closeAddProvider();  // 重置
  document.getElementById('addProviderModal').classList.add('open');
}

// v3.6.0: 选预设 base_url → 自动填到 provUrl
function onPresetUrlChange(){
  const v = document.getElementById('provUrlPreset').value;
  if(v && v !== '__custom__'){
    document.getElementById('provUrl').value = v;
  }
}
</script>

<!-- Classifier Modal -->
<div class="modal-bg" id="classifierModal">
  <div class="modal" style="max-width:700px">
    <h3>⚙️ Tier Bonus & 自定义关键词</h3>
    <div class="text-muted">模型 ID 包含关键词时, 能力分 += 该值. tier_bonus 内置默认 + 用户覆盖, custom_keywords 全部用户自定义.</div>

    <h4 style="margin-top:14px;font-size:14px;color:#94a3b8">Tier Bonus (内置 + 用户覆盖)</h4>
    <div id="tierBonusEditor"></div>
    <button class="btn-sm" onclick="addKVEditor('tier_bonus')">+ 添加 tier</button>

    <h4 style="margin-top:14px;font-size:14px;color:#94a3b8">Custom Keywords (用户自定义, 累加)</h4>
    <div id="customKwEditor"></div>
    <button class="btn-sm" onclick="addKVEditor('custom_keywords')">+ 添加关键词</button>

    <h4 style="margin-top:14px;font-size:14px;color:#94a3b8">Modality Base Score (模态基类分)</h4>
    <div id="modScoreEditor"></div>
    <button class="btn-sm" onclick="addKVEditor('modality_base_score')">+ 添加模态</button>

    <div class="row">
      <button class="btn" onclick="saveClassifier()">保存</button>
      <button class="btn-sm" onclick="closeClassifier()">取消</button>
    </div>
  </div>
</div>

<!-- Server / Routing Modal -->
<div class="modal-bg" id="serverModal">
  <div class="modal" style="max-width:600px">
    <h3>🔧 修改服务配置</h3>
    <div class="text-muted">server 段: host / port (改 port 需重启) / api_key. routing 段: 路由策略参数.</div>

    <h4 style="margin-top:14px;font-size:14px;color:#94a3b8">Server</h4>
    <label>Host (监听地址)</label>
    <input id="srvHost" placeholder="0.0.0.0">
    <label>Port (监听端口, 默认 6473) <span class="text-muted">⚠️ 改完需重启</span></label>
    <input id="srvPort" type="number" min="1" max="65535" value="6473">
    <label>API Key <span class="text-muted">(Bearer 鉴权, 留空不改)</span></label>
    <input id="srvApiKey" type="password" placeholder="可选, Bearer 鉴权">

    <h4 style="margin-top:14px;font-size:14px;color:#94a3b8">Routing</h4>
    <label>Strategy (路由策略, v3.10.1: 仅 flat 是真实现, 其他 2 个历史选项已废)</label>
    <select id="rtStrategy">
      <option value="flat">flat (全局降序, v3.10.1 默认 — 老 v4 行为)</option>
      <option value="round-robin">round-robin (轮询, 已废, 走 flat)</option>
      <option value="failover">failover (故障切换, 已废, 走 flat)</option>
    </select>
    <label>Failover Threshold (连续失败次数触发 degraded)</label>
    <input id="rtFailover" type="number" min="1" value="3">
    <label>Recovery Interval (degraded 自动恢复间隔, 秒)</label>
    <input id="rtRecovery" type="number" min="10" value="300">
    <label>Max Retry (单请求最大重试)</label>
    <input id="rtMaxRetry" type="number" min="0" max="10" value="2">
    <label>First Token Timeout (首个 token 超时, ms)</label>
    <input id="rtFirstToken" type="number" min="1000" value="10000">
    <!-- v3.9.0 (Phase H): group-based 轮询策略 (跟 mgm.model_groups 协作) -->
    <h4 style="margin-top:18px;font-size:14px;color:#a78bfa">🆕 v3.9.0 Group-Based 轮询 (Phase H)</h4>
    <div class="text-muted" style="font-size:12px;margin-bottom:6px">
      按 model 分组 (mgm.model_groups) 决定候选链顺序, 跟上面的 strategy 字段叠加生效
    </div>
    <label>Group Strategy (group-based 轮询策略, 默认 round-robin-group)</label>
    <select id="rtGroupStrategy">
      <option value="round-robin-group">round-robin-group (按 group 桶间轮询 — 默认)</option>
      <option value="flat">flat (忽略 model_groups, 全局降序)</option>
      <option value="group-failover">group-failover (group A 全失败才 group B)</option>
      <option value="group-weighted">group-weighted (按 group_weights 加权随机)</option>
    </select>
    <label>Group Weights (JSON: {"group_name": weight, ...}, 用于 group-weighted)</label>
    <textarea id="rtGroupWeights" rows="3" placeholder='{"claude": 2.0, "gpt": 1.0}' style="font-family:monospace;font-size:12px"></textarea>

    <div class="row">
      <button class="btn" onclick="saveServer()">保存</button>
      <button class="btn-sm" onclick="closeServer()">取消</button>
    </div>
  </div>
</div>

<!-- Config Backups Modal (v3.2.0) -->
<div class="modal-bg" id="configBackupsModal">
  <div class="modal" style="max-width:700px">
    <h3>📜 配置历史 (v3.2.0)</h3>
    <div class="text-muted">每次写 config.yaml 前自动备份 (保留最近 50 个). 改错可一键回滚.</div>
    <div id="configBackupsList" style="margin-top:12px;max-height:400px;overflow-y:auto">
      <div class="text-muted">加载中...</div>
    </div>
    <div class="row" style="margin-top:14px">
      <button class="btn-sm" onclick="closeConfigBackups()">关闭</button>
    </div>
  </div>
</div>

<div id="usageByModelModal" class="modal-overlay" style="display:none">
  <div class="modal-content" style="max-width:700px">
    <div class="modal-header">
      <h3 id="usageByModelTitle">📊 按 model 分组的用量</h3>
      <button class="modal-close" onclick="closeUsageByModel()">✕</button>
    </div>
    <div class="modal-body">
      <div id="usageByModelStats" style="margin-bottom:14px"></div>
      <div id="usageByModelChart" style="margin-bottom:14px"></div>
      <div id="usageByModelList"><div class="loading">加载中...</div></div>
    </div>
    <div class="row" style="margin-top:14px">
      <button class="btn-sm" onclick="closeUsageByModel()">关闭</button>
    </div>
  </div>
</div>

<script>
// v3.9.0 (Phase G): 按 model 分组用量弹窗
async function showUsageByModel(name){
  const modal = document.getElementById('usageByModelModal');
  const titleEl = document.getElementById('usageByModelTitle');
  const statsEl = document.getElementById('usageByModelStats');
  const chartEl = document.getElementById('usageByModelChart');
  const listEl = document.getElementById('usageByModelList');
  if(!modal) return;
  titleEl.textContent = "📊 '" + name + "' 按 model 分组的用量";
  statsEl.innerHTML = '<div class="loading">加载中...</div>';
  chartEl.innerHTML = '';
  listEl.innerHTML = '<div class="loading">加载中...</div>';
  modal.style.display = 'flex';
  const r = await api('/v1/admin/public-keys/'+encodeURIComponent(name)+'/usage-by-model').catch(e=>({error:e.message}));
  if(r.error){
    statsEl.innerHTML = '<div class="empty-state">❌ ' + r.error + '</div>';
    listEl.innerHTML = '';
    return;
  }
  const byModel = r.by_model || {};
  const totalCalls = r.total_calls || 0;
  const lastUsed = r.last_used ? new Date(r.last_used*1000).toLocaleString('zh-CN') : '未使用';
  statsEl.innerHTML = '<div style="display:flex;gap:18px;font-size:13px">'
    + '<span>📈 总调用: <b>' + totalCalls + '</b></span>'
    + '<span>🎯 不同 model: <b>' + Object.keys(byModel).length + '</b></span>'
    + '<span>🕐 最近: <b>' + lastUsed + '</b></span>'
    + '</div>';
  if(Object.keys(byModel).length === 0){
    chartEl.innerHTML = '<div class="empty-state">暂无用量数据</div>';
    listEl.innerHTML = '';
    return;
  }
  const maxCount = Math.max.apply(null, Object.values(byModel));
  const colors = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#06b6d4', '#84cc16'];
  let chartHtml = '<div style="display:flex;flex-direction:column;gap:6px;padding:10px;background:#0f0f13;border-radius:8px">';
  let listHtml = '<h4 style="font-size:13px;color:#888;margin:12px 0 8px">详细列表</h4>'
    + '<div style="display:flex;flex-direction:column;gap:4px;max-height:240px;overflow-y:auto">';
  Object.entries(byModel).forEach(function(entry, idx){
    const model = entry[0], count = entry[1];
    const pct = maxCount > 0 ? (count / maxCount * 100).toFixed(1) : 0;
    const totalPct = totalCalls > 0 ? (count / totalCalls * 100).toFixed(1) : 0;
    const color = colors[idx % colors.length];
    chartHtml += '<div style="display:flex;align-items:center;gap:8px;font-size:12px">'
      + '<div style="min-width:160px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#ddd" title="' + model + '">' + model + '</div>'
      + '<div style="flex:1;background:#1f2937;border-radius:4px;height:18px;overflow:hidden;position:relative">'
      + '<div style="background:' + color + ';height:100%;width:' + pct + '%;transition:width 0.3s"></div>'
      + '<span style="position:absolute;left:8px;top:50%;transform:translateY(-50%);color:#fff;font-weight:600;font-size:11px">' + count + ' 次 (' + totalPct + '%)</span>'
      + '</div></div>';
    listHtml += '<div style="display:flex;justify-content:space-between;padding:6px 10px;background:#0f0f13;border-radius:4px;font-size:12px;border-left:3px solid ' + color + '">'
      + '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + model + '</span>'
      + '<span style="color:#888;margin-left:8px">' + count + ' 次</span>'
      + '</div>';
  });
  chartHtml += '</div>';
  listHtml += '</div>';
  chartEl.innerHTML = chartHtml;
  listEl.innerHTML = listHtml;
}

function closeUsageByModel(){
  const modal = document.getElementById('usageByModelModal');
  if(modal) modal.style.display = 'none';
}
</script>

</body>
</html>"""


@router.get("/admin", response_class=HTMLResponse)
@router.get("/admin/", response_class=HTMLResponse)
async def admin_page():
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
