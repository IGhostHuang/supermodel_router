"""
supermodel_router/app.py — FastAPI 主服务
"""
import json
import time
import logging
import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .config import config
from .models import ModelRegistry
from .engine import RouteEngine, proxy_request

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

    # 启动 config 热重载
    config.start_watcher()

    # 定期刷新模型列表
    refresh_interval = 600  # 10min
    async def _periodic_refresh():
        while True:
            await asyncio.sleep(refresh_interval)
            try:
                registry.refresh_all()
            except Exception:
                LOG.exception("periodic refresh failed")
    asyncio.create_task(_periodic_refresh())

    LOG.info("Model Router started: %d models", len(registry.get_model_ids()))
    yield
    config.stop_watcher()


app = FastAPI(
    title="Model Router",
    version="1.0.0",
    lifespan=lifespan,
)


# ============================================================
# OpenAI 兼容 API
# ============================================================

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI-compatible chat completions endpoint"""
    body = await request.json()
    requested_model = body.get("model", "auto")
    stream = body.get("stream", False)

    # 鉴权 (可选)
    api_key = config.server.get("api_key", "")
    if api_key:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != api_key:
            return JSONResponse(
                {"error": {"message": "Invalid API key", "type": "auth_error"}},
                status_code=401,
            )

    # 路由
    max_retry = config.routing.get("max_retry", 2)
    backoff_ms = config.routing.get("retry_backoff_ms", [0, 500])
    last_error = None

    for attempt in range(max_retry + 1):
        route = engine.pick(requested_model)
        if not route:
            return JSONResponse(
                {"error": {"message": "No available models", "type": "routing_error"}},
                status_code=503,
            )

        t0 = time.time()
        try:
            if stream:
                result = await proxy_request(route, body, stream=True)
                # SSE streaming
                async def _stream_generator():
                    try:
                        async for chunk in result:
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
                result = await proxy_request(route, body, stream=False, timeout=300)
                latency = time.time() - t0
                if "error" in result:
                    engine.record_failure(route.provider_name)
                    last_error = result
                    continue
                engine.record_success(route.provider_name, latency)
                result["_router"] = {
                    "provider": route.provider_name,
                    "model": route.model_id,
                    "full_path": route.full_model_path,
                    "latency_ms": round(latency * 1000, 1),
                }
                return JSONResponse(result)
        except httpx.TimeoutException:
            latency = time.time() - t0
            LOG.warning(
                "timeout %s (attempt %d/%d, %.1fs)",
                route.full_model_path, attempt + 1, max_retry + 1, latency,
            )
            engine.record_failure(route.provider_name)
            last_error = {"error": {"message": "Upstream timeout", "type": "timeout"}}
        except Exception as e:
            LOG.exception(
                "proxy error %s (attempt %d/%d)",
                route.full_model_path, attempt + 1, max_retry + 1,
            )
            engine.record_failure(route.provider_name)
            last_error = {"error": {"message": str(e), "type": "proxy_error"}}

        # backoff
        if attempt < max_retry:
            ms = backoff_ms[min(attempt, len(backoff_ms) - 1)]
            if ms > 0:
                await asyncio.sleep(ms / 1000)

    return JSONResponse(
        last_error or {"error": {"message": "All retries exhausted"}},
        status_code=502,
    )


@app.get("/v1/models")
async def list_models(provider: str | None = None):
    """OpenAI-compatible model listing, 支持 ?provider= 过滤"""
    models = registry.get_models(provider)
    return JSONResponse({
        "object": "list",
        "data": [
            {
                "id": m.id,
                "object": m.object,
                "created": m.created,
                "owned_by": m.owned_by,
                "provider": m.provider,
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
<title>Model Router Dashboard</title>
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
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 10px;color:#666;border-bottom:1px solid #222}
td{padding:8px 10px;border-bottom:1px solid #1a1a1a}
tr:hover td{background:#1a1a24}
.provider-tag{display:inline-block;font-size:11px;padding:2px 6px;border-radius:3px;background:#1e293b;color:#94a3b8;margin-right:4px}
.btn{background:#2563eb;color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:13px}
.btn:hover{background:#1d4ed8}
.btn-sm{background:#1a1a24;color:#e0e0e0;border:1px solid #333;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:11px}
.btn-sm:hover{background:#333}
.toolbar{display:flex;gap:8px;margin-bottom:16px}
.toast{position:fixed;bottom:20px;right:20px;background:#1a1a24;border:1px solid #333;padding:12px 20px;border-radius:8px;font-size:13px;display:none;z-index:999}
.toast.show{display:block}
.hidden{display:none!important}
.loading{color:#666;text-align:center;padding:30px}
</style>
</head>
<body>
<h1>⚡ Model Router</h1>
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
  <div class="stat-card"><div class="label">Status</div><div class="value" id="statStatus">-</div></div>
</div>

<!-- Provider 状态 -->
<h2>Providers</h2>
<div class="provider-grid" id="providerGrid"><div class="loading">加载中...</div></div>

<!-- 模型列表 -->
<h2>Models <span style="font-size:12px;color:#666" id="modelCount"></span></h2>
<div id="modelSection">
<table><thead><tr><th>Model</th><th>Provider</th></tr></thead><tbody id="modelTable"></tbody></table>
</div>

<!-- Toast -->
<div class="toast" id="toast"></div>

<script>
const BASE = '';
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
  const [h,m,r,s]=await Promise.all([
    api('/v1/health'),
    api('/v1/models'),
    api('/v1/admin/routes'),
    api('/v1/admin/stats'),
  ]);
  renderHealth(h);
  renderProviders(h);
  renderModels(m);
  renderRoutes(r);
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
  document.getElementById('statStatus').textContent=h.status;
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
function renderModels(data){
  const t=document.getElementById('modelTable');
  const models=data?.data||[];
  document.getElementById('modelCount').textContent=`(${models.length})`;
  if(models.length===0){
    t.innerHTML='<tr><td colspan="2" style="color:#666;text-align:center;padding:20px">尚未配置 API key / 无符合条件的模型</td></tr>';
    return;
  }
  t.innerHTML=models.map(m=>
    `<tr><td>${m.id}</td><td><span class="provider-tag">${m.provider||'?'}</span></td></tr>`
  ).join('');
}
function renderRoutes(data){
  // routes hidden for now, shown in models table
}
function renderStats(data){
  // could add a stats section later
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
        "uptime_seconds": round(time.time() - _start_time, 1),
        "total_models": len(registry.get_model_ids()),
        "providers": registry.get_state(),
    })


@app.get("/v1/admin/routes")
async def admin_routes():
    """查看所有路由: provider/model"""
    return JSONResponse({
        "routes": registry.all_routes(),
        "total": len(registry.all_routes()),
    })


@app.get("/v1/admin/stats")
async def admin_stats():
    return JSONResponse(engine.get_stats())


@app.post("/v1/admin/refresh")
async def admin_refresh():
    """手动触发模型刷新"""
    registry.refresh_all()
    return JSONResponse({
        "ok": True,
        "providers": registry.get_state(),
    })


@app.post("/v1/admin/config/reload")
async def admin_config_reload():
    """手动重载 config.yaml"""
    config.load()
    registry.build()
    registry.refresh_all()
    return JSONResponse({"ok": True})


@app.get("/v1/admin/config")
async def admin_config_get():
    """查看当前配置 (敏感字段遮罩)"""
    import copy
    data = copy.deepcopy(config.data)
    # 遮罩 api_key
    for pname, pcfg in data.get("providers", {}).items():
        keys = pcfg.get("api_keys", [])
        pcfg["api_keys"] = [
            k[:8] + "..." + k[-4:] if len(k) > 12 else "***"
            for k in keys
        ]
    if data.get("server", {}).get("api_key"):
        data["server"]["api_key"] = "***"
    return JSONResponse(data)
