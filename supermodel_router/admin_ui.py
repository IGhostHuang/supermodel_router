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
.modal-bg.show{display:flex}
.modal{background:#1a1a24;border-radius:10px;padding:20px;max-width:600px;width:90%;max-height:80vh;overflow:auto}
.modal h3{margin-bottom:12px;font-size:16px}
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
</style>
</head>
<body>
<h1>⚡ Model Router v3</h1>
<div class="toolbar">
  <button class="btn" onclick="refresh()">🔄 刷新</button>
  <button class="btn-sm" onclick="reloadConfig()">重载配置</button>
  <button class="btn-sm" onclick="loadModels()">获取模型</button>
  <button class="btn" onclick="openAddProvider()">➕ 添加 Provider</button>
  <button class="btn-sm" onclick="openClassifier()">⚙️ Tier Bonus</button>
  <button class="btn-sm" onclick="openServer()">🔧 修改配置</button>
  <button class="btn-sm" onclick="openConfigBackups()">📜 配置历史</button>
</div>

<!-- 状态栏 -->
<div class="status-bar" id="statusBar">
  <div class="stat-card"><div class="label">Providers</div><div class="value" id="statProviders">-</div></div>
  <div class="stat-card"><div class="label">Models</div><div class="value" id="statModels">-</div></div>
  <div class="stat-card"><div class="label">Uptime</div><div class="value" id="statUptime">-</div></div>
  <div class="stat-card"><div class="label">Route Mode</div><div class="value" id="statMode">多模态</div></div>
</div>

<!-- 模态分布 -->
<h2>模态分布</h2>
<div class="modality-grid" id="modalityGrid"><div class="loading">加载中...</div></div>

<!-- Provider 状态 -->
<h2>Providers</h2>
<div class="provider-grid" id="providerGrid"><div class="loading">加载中...</div></div>

<!-- 模型列表 (带分类信息) -->
<h2>Models <span style="font-size:12px;color:#666" id="modelCount"></span></h2>
<div class="modality-filter" id="modalityFilter"></div>
<div id="modelSection">
<table><thead><tr><th>Model</th><th>Provider</th><th>分类</th><th>能力分</th></tr></thead><tbody id="modelTable"></tbody></table>
</div>

<!-- Toast -->
<div class="toast" id="toast"></div>

<script>
const BASE = '';
let filterModality = '';

function toast(msg, ok=true){
  const t=document.getElementById('toast');
  t.textContent=(ok?'✅ ':'❌ ')+msg;
  t.style.borderColor=ok?'#0d3b1e':'#3b0d0d';
  t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),2500);
}
async function api(path, opts={}){
  const r=await fetch(BASE+path,{headers:{'Accept':'application/json'},...opts});
  return r.json();
}
async function refresh(){
  const [h,m,r,s,mo]=await Promise.all([
    api('/v1/health'),
    api('/v1/models'),
    api('/v1/admin/routes'),
    api('/v1/admin/stats'),
    api('/v1/admin/modalities'),
  ]);
  renderHealth(h);
  renderProviders(h);
  renderModalities(mo);
  renderModelFilter();
  renderModels(m);
  renderStats(s);
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
function renderHealth(h){
  if(!h)return;
  document.getElementById('statProviders').textContent=Object.keys(h.providers||{}).length;
  document.getElementById('statModels').textContent=h.total_models;
  document.getElementById('statUptime').textContent=Math.floor(h.uptime_seconds/60)+'m';
}
function renderProviders(h){
  const g=document.getElementById('providerGrid');
  const ps=h.providers||{};
  if(Object.keys(ps).length===0){
    g.innerHTML='<div style="color:#666">No providers configured</div>';
    return;
  }
  g.innerHTML=Object.entries(ps).map(([name,p])=>{
    let cls='badge-ok',label='OK';
    if(p.degraded){cls='badge-degraded';label='Degraded';}
    return `<div class="provider-card">
      <div style="flex:1">
        <div class="provider-name">${name}</div>
        <div style="font-size:11px;color:#666;margin-top:4px">${p.base_url}</div>
      </div>
      <div style="display:flex;align-items:center;gap:10px">
        <span class="provider-badge ${cls}">${label}</span>
        <span style="font-size:11px;color:#666">${p.models} models · fail ${p.fail_count}</span>
        <button class="btn-sm danger" onclick="deleteProvider('${name}')">删除</button>
      </div>
    </div>`;
  }).join('');
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
}
function renderModels(data){
  const t=document.getElementById('modelTable');
  const models=(data?.data||[]).filter(m=>!filterModality||m.modality===filterModality);
  document.getElementById('modelCount').textContent=`(${models.length})`;
  if(models.length===0){
    t.innerHTML='<tr><td colspan="4" style="color:#666;text-align:center;padding:20px">无模型</td></tr>';
    return;
  }
  t.innerHTML=models.map(m=>{
    const sc=m.capability_score||0;
    const pct=Math.min(sc,100);
    const color=sc>=80?'#4ade80':sc>=50?'#fbbf24':'#f87171';
    return `<tr>
      <td>${m.id}</td>
      <td><span class="provider-tag">${m.provider||'?'}</span></td>
      <td><span class="modality-tag ${renderModalityClass(m.modality)}">${m.modality_display||m.modality||'?'}</span></td>
      <td><span class="score-bar" style="width:${pct*0.7}px;background:${color}"></span>${sc}</td>
    </tr>`;
  }).join('');
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
  const name = document.getElementById('provName').value.trim();
  const base_url = document.getElementById('provUrl').value.trim();
  const api_keys_raw = document.getElementById('provKeys').value.trim();
  const mode = document.getElementById('provMode').value;
  const pattern = document.getElementById('provPattern').value.trim();
  const include_raw = document.getElementById('provInclude').value.trim();
  const max_concurrent = parseInt(document.getElementById('provMax').value) || 3;

  if (!name || !base_url || !api_keys_raw) {
    toast('请填写 name / base_url / api_keys', false);
    return;
  }
  const api_keys = api_keys_raw.split('\n').map(s=>s.trim()).filter(Boolean);
  const include = include_raw ? include_raw.split('\n').map(s=>s.trim()).filter(Boolean) : [];
  const model_rules = {mode};
  if (mode === 'pattern' && pattern) model_rules.pattern = pattern;
  if (mode === 'include' && include.length) model_rules.include = include;

  const r = await api('/v1/admin/providers', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      name,
      config: { base_url, api_keys, model_rules, max_concurrent, enabled: true },
    }),
  });
  if (r.error) { toast(r.error, false); return; }
  toast(`Provider '${name}' added (${r.config?.model_rules?.mode || 'all'})`);
  closeAddProvider();
  document.getElementById('provName').value = '';
  document.getElementById('provUrl').value = '';
  document.getElementById('provKeys').value = '';
  document.getElementById('provPattern').value = '';
  document.getElementById('provInclude').value = '';
  refresh();
}

async function deleteProvider(name){
  if (!confirm(`确定删除 provider '${name}' 吗?\n(配置会从 config.yaml 移除)`)) return;
  const r = await api('/v1/admin/providers/' + encodeURIComponent(name), {method: 'DELETE'});
  if (r.error) { toast(r.error, false); return; }
  toast(`Provider '${name}' removed`);
  refresh();
}

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
  const r = await api('/v1/admin/classifier', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  if (r.error) { toast(r.error, false); return; }
  toast(`已更新: ${(r.updated||[]).join(', ')}`);
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
  document.getElementById('rtStrategy').value = rt.strategy || 'quality_weighted';
  document.getElementById('rtFailover').value = rt.failover_threshold || 3;
  document.getElementById('rtRecovery').value = rt.recovery_interval || 300;
  document.getElementById('rtMaxRetry').value = rt.max_retry || 2;
  document.getElementById('rtFirstToken').value = rt.first_token_timeout_ms || 10000;
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

<!-- Add Provider Modal -->
<div class="modal-bg" id="addProvModal">
  <div class="modal">
    <h3>➕ 添加自定义 Provider</h3>
    <div class="text-muted">支持任何 OpenAI 兼容 API (OpenAI / Azure / 自建 / 中转 / newapi 等)</div>
    <label>Provider 名称 *</label>
    <input id="provName" placeholder="myopenai">
    <label>Base URL * <span class="text-muted">(去掉 /v1 后缀, e.g. https://api.openai.com)</span></label>
    <input id="provUrl" placeholder="https://api.openai.com">
    <label>API Keys * <span class="text-muted">(一行一个, 自动轮询)</span></label>
    <textarea id="provKeys" placeholder="sk-xxx&#10;sk-yyy"></textarea>
    <label>Model Filter Mode</label>
    <select id="provMode" onchange="document.getElementById('patternField').style.display=this.value==='pattern'?'block':'none';document.getElementById('includeField').style.display=this.value==='include'?'block':'none'">
      <option value="all">all (全部模型)</option>
      <option value="pattern">pattern (正则匹配)</option>
      <option value="include">include (白名单)</option>
    </select>
    <div id="patternField" style="display:none">
      <label>Pattern (正则)</label>
      <input id="provPattern" placeholder=".*-free$|.*free.*">
    </div>
    <div id="includeField" style="display:none">
      <label>Include (白名单, 一行一个)</label>
      <textarea id="provInclude" placeholder="gpt-4o&#10;gpt-4-turbo"></textarea>
    </div>
    <label>Max Concurrent Slots</label>
    <input id="provMax" type="number" value="3" min="1" max="20">
    <div class="row">
      <button class="btn" onclick="submitAddProvider()">添加</button>
      <button class="btn-sm" onclick="closeAddProvider()">取消</button>
    </div>
  </div>
</div>

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
    <label>Strategy (路由策略)</label>
    <select id="rtStrategy">
      <option value="quality_weighted">quality_weighted (按质量评分)</option>
      <option value="round-robin">round-robin (轮询)</option>
      <option value="failover">failover (故障切换)</option>
    </select>
    <label>Failover Threshold (连续失败次数触发 degraded)</label>
    <input id="rtFailover" type="number" min="1" value="3">
    <label>Recovery Interval (degraded 自动恢复间隔, 秒)</label>
    <input id="rtRecovery" type="number" min="10" value="300">
    <label>Max Retry (单请求最大重试)</label>
    <input id="rtMaxRetry" type="number" min="0" max="10" value="2">
    <label>First Token Timeout (首个 token 超时, ms)</label>
    <input id="rtFirstToken" type="number" min="1000" value="10000">

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

</body>
</html>"""


@router.get("/admin", response_class=HTMLResponse)
@router.get("/admin/", response_class=HTMLResponse)
async def admin_page():
    return HTMLResponse(content=ADMIN_HTML)
