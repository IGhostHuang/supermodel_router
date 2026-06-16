"""
supermodel_router/app.py — FastAPI 主服务 v3 (模态感知路由)
"""
import json
import time
import logging
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, cast

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .config import config
from .models import ModelRegistry
from .engine import RouteEngine, proxy_chat_request, proxy_images_generations
from .detector import (
    detect_chat_input_modality,
    detect_chat_output_modality,
    detect_streaming,
    match_modality_for_request,
    detect_image_gen_params,
)
from .classifier import (
    TEXT_ONLY, MULTIMODAL, IMAGE_GEN, VIDEO_GEN, AUDIO_GEN,
    get_modality_display,
)

LOG = logging.getLogger("app")

# ---- 全局对象 ----
registry: ModelRegistry = None
engine: RouteEngine = None
_start_time: float = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global registry, engine, _start_time
    _start_time = time.time()

    registry = ModelRegistry(config)
    registry.build()
    registry.refresh_all()
    engine = RouteEngine(config, registry)

    config.start_watcher()

    # 定期刷新模型列表 (10min)
    refresh_interval = 600
    async def _periodic_refresh():
        while True:
            await asyncio.sleep(refresh_interval)
            try:
                registry.refresh_all()
            except Exception:
                LOG.exception("periodic refresh failed")
    asyncio.create_task(_periodic_refresh())

    LOG.info("Model Router v3 started: %d models across %d providers",
             len(registry.get_model_ids()), len(registry._providers))
    yield
    config.stop_watcher()


app = FastAPI(title="Model Router v3 — Any-to-Any", version="3.0.0", lifespan=lifespan)


# ============================================================
# OpenAI 兼容 API — 任意模态自动路由
# ============================================================

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI-compatible chat completions — 自动检测输入/输出类型, 按模态路由"""
    body = await request.json()
    requested_model = body.get("model", "auto")
    stream = detect_streaming(body)

    # 鉴权
    api_key = config.server.get("api_key", "")
    if api_key:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != api_key:
            return JSONResponse(
                {"error": {"message": "Invalid API key", "type": "auth_error"}},
                status_code=401,
            )

    # ── v3: 自动检测输入/输出类型 ──
    input_mod = detect_chat_input_modality(body)
    output_mod = detect_chat_output_modality(body)
    preferred_modalities = match_modality_for_request(input_mod, output_mod)

    LOG.debug("request: input=%s output=%s → modalities=%s model=%s",
              input_mod, output_mod, preferred_modalities, requested_model)

    # 路由 (带重试)
    max_retry = config.routing.get("max_retry", 2)
    backoff_ms = config.routing.get("retry_backoff_ms", [0, 500])
    last_error = None

    for attempt in range(max_retry + 1):
        route = engine.pick(requested_model, preferred_modalities=preferred_modalities)
        if not route:
            return JSONResponse(
                {"error": {"message": "No available models", "type": "routing_error"}},
                status_code=503,
            )

        t0 = time.time()
        try:
            if stream:
                agen = cast(AsyncGenerator, await proxy_chat_request(route, body, stream=True))
                async def _stream_generator(route=route, t0=t0):
                    try:
                        async for chunk in agen:
                            yield chunk
                        engine.record_success(route.provider_name, time.time() - t0)
                    except Exception as e:
                        engine.record_failure(route.provider_name)
                        LOG.exception("stream error to %s", route.full_model_path)
                        yield f'data: {json.dumps({"error": str(e)})}\n\n'
                return StreamingResponse(
                    _stream_generator(),
                    media_type="text/event-stream",
                )
            else:
                result = await proxy_chat_request(route, body, stream=False, timeout=300)
                assert isinstance(result, dict), f"expected dict, got {type(result)}"
                latency = time.time() - t0
                if "error" in result:
                    engine.record_failure(route.provider_name, route.model_id,
                                          result.get("error", {}).get("code", 0),
                                          result.get("error", {}).get("message", ""))
                    last_error = result
                    continue
                engine.record_success(route.provider_name, latency)
                result["_router"] = {
                    "provider": route.provider_name,
                    "model": route.model_id,
                    "full_path": route.full_model_path,
                    "latency_ms": round(latency * 1000, 1),
                    "input_modality": input_mod,
                    "output_modality": output_mod,
                }
                return JSONResponse(result)
        except httpx.TimeoutException:
            latency = time.time() - t0
            LOG.warning("timeout %s (attempt %d/%d, %.1fs)",
                        route.full_model_path, attempt + 1, max_retry + 1, latency)
            engine.record_failure(route.provider_name, route.model_id)
            last_error = {"error": {"message": "Upstream timeout", "type": "timeout"}}
        except Exception as e:
            LOG.exception("proxy error %s (attempt %d/%d)",
                          route.full_model_path, attempt + 1, max_retry + 1)
            engine.record_failure(route.provider_name, route.model_id)
            last_error = {"error": {"message": str(e), "type": "proxy_error"}}

        if attempt < max_retry:
            ms = backoff_ms[min(attempt, len(backoff_ms) - 1)]
            if ms > 0:
                await asyncio.sleep(ms / 1000)

    return JSONResponse(
        last_error or {"error": {"message": "All retries exhausted"}},
        status_code=502,
    )


@app.post("/v1/images/generations")
async def images_generations(request: Request):
    """图像生成 — 自动路由到生图模型分组"""
    body = await request.json()
    params = detect_image_gen_params(body)
    prompt = params["prompt"]
    if not prompt:
        return JSONResponse({"error": {"message": "prompt is required", "type": "invalid_request"}},
                            status_code=400)

    route = engine.pick("auto", preferred_modalities=[IMAGE_GEN])
    if not route:
        return JSONResponse(
            {"error": {"message": "No image generation models available", "type": "routing_error"}},
            status_code=503,
        )

    t0 = time.time()
    try:
        result = await proxy_images_generations(route, body, timeout=120)
        latency = time.time() - t0
        if "error" not in result:
            engine.record_success(route.provider_name, latency)
            result["_router"] = {
                "provider": route.provider_name,
                "model": route.model_id,
                "full_path": route.full_model_path,
                "latency_ms": round(latency * 1000, 1),
            }
            return JSONResponse(result)
        engine.record_failure(route.provider_name, route.model_id)
        return JSONResponse(result, status_code=502)
    except Exception as e:
        engine.record_failure(route.provider_name, route.model_id)
        return JSONResponse({"error": {"message": str(e), "type": "proxy_error"}},
                            status_code=502)


@app.post("/v1/images/edits")
async def images_edits(request: Request):
    """图片编辑 — 路由到生图模型"""
    # 简化: 转发相同
    return await images_generations(request)


@app.post("/v1/embeddings")
async def embeddings(request: Request):
    """文本嵌入 — 路由到 embedding 模型 (没有则 fallback)"""
    body = await request.json()
    requested_model = body.get("model", "auto")

    route = engine.pick(requested_model, preferred_modalities=["embedding", TEXT_ONLY])
    if not route:
        return JSONResponse(
            {"error": {"message": "No embedding models available", "type": "routing_error"}},
            status_code=503,
        )

    headers = {
        "Authorization": f"Bearer {route.api_key}",
        "Content-Type": "application/json",
    }
    payload = {**body, "model": route.model_id}
    url = f"{route.base_url.rstrip('/')}/embeddings"

    t0 = time.time()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=headers, timeout=30)
            latency = time.time() - t0
            if resp.status_code == 200:
                engine.record_success(route.provider_name, latency)
                result = resp.json()
                result["_router"] = {
                    "provider": route.provider_name,
                    "model": route.model_id,
                    "latency_ms": round(latency * 1000, 1),
                }
                return JSONResponse(result)
            engine.record_failure(route.provider_name, route.model_id, resp.status_code, resp.text)
            return JSONResponse(
                {"error": {"message": resp.text[:500], "type": f"http_{resp.status_code}"}},
                status_code=502,
            )
    except Exception as e:
        engine.record_failure(route.provider_name, route.model_id)
        return JSONResponse({"error": {"message": str(e), "type": "proxy_error"}},
                            status_code=502)


# ── 模型列表 (带分类) ──

@app.get("/v1/models")
async def list_models(provider: str | None = None, modality: str | None = None):
    """模型列表, 支持 ?provider= 和 ?modality= 过滤"""
    models = registry.get_models(provider)
    if modality:
        models = [m for m in models if m.modality == modality]

    return JSONResponse({
        "object": "list",
        "data": [
            {
                "id": m.id,
                "object": m.object,
                "created": m.created,
                "owned_by": m.owned_by,
                "provider": m.provider,
                "modality": m.modality,
                "modality_display": m.modality_display,
                "capability_score": m.capability_score,
                **m.extra,
            }
            for m in models
        ],
    })


@app.get("/v1/models/{model_id:path}")
async def get_model(model_id: str):
    models = registry.get_models()
    for m in models:
        if m.id == model_id:
            return JSONResponse({
                "id": m.id,
                "object": m.object,
                "created": m.created,
                "owned_by": m.owned_by,
                "provider": m.provider,
                "modality": m.modality,
                "modality_display": m.modality_display,
                "capability_score": m.capability_score,
            })
    return JSONResponse({"error": "Model not found"}, status_code=404)


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
</style>
</head>
<body>
<h1>⚡ Model Router v3</h1>
<div class="toolbar">
  <button class="btn" onclick="refresh()">🔄 刷新</button>
  <button class="btn-sm" onclick="reloadConfig()">重载配置</button>
  <button class="btn-sm" onclick="loadModels()">获取模型</button>
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
      <div>
        <div class="provider-name">${name}</div>
        <div style="font-size:11px;color:#666;margin-top:4px">${p.base_url}</div>
      </div>
      <div style="text-align:right">
        <div><span class="provider-badge ${cls}">${label}</span></div>
        <div style="font-size:11px;color:#666;margin-top:4px">${p.models} models · fail ${p.fail_count}</div>
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
</script>
</body>
</html>"""


@app.get("/admin", response_class=HTMLResponse)
@app.get("/admin/", response_class=HTMLResponse)
async def admin_page():
    return HTMLResponse(content=ADMIN_HTML)


@app.get("/v1/health")
async def health():
    return JSONResponse({
        "status": "ok",
        "version": "3.0.0",
        "uptime_seconds": round(time.time() - _start_time, 1),
        "total_models": len(registry.get_model_ids()),
        "providers": registry.get_state(),
    })


@app.get("/v1/admin/modalities")
async def admin_modalities():
    """各模态的模型数量分布"""
    return JSONResponse(registry.get_modality_counts())


@app.get("/v1/admin/routes")
async def admin_routes():
    return JSONResponse({
        "routes": registry.all_routes(),
        "total": len(registry.all_routes()),
    })


@app.get("/v1/admin/stats")
async def admin_stats():
    return JSONResponse(engine.get_stats())


@app.post("/v1/admin/refresh")
async def admin_refresh():
    registry.refresh_all()
    return JSONResponse({
        "ok": True,
        "providers": registry.get_state(),
    })


@app.post("/v1/admin/config/reload")
async def admin_config_reload():
    config.load()
    registry.build()
    registry.refresh_all()
    return JSONResponse({"ok": True})


@app.get("/v1/admin/config")
async def admin_config_get():
    import copy
    data = copy.deepcopy(config.data)
    for pname, pcfg in data.get("providers", {}).items():
        keys = pcfg.get("api_keys", [])
        pcfg["api_keys"] = [
            k[:8] + "..." + k[-4:] if len(k) > 12 else "***"
            for k in keys
        ]
    if data.get("server", {}).get("api_key"):
        data["server"]["api_key"] = "***"
    return JSONResponse(data)