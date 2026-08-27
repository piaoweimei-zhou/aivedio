"""统一运营状态聚合端点 /api/system/status。

透明化：把分散的「生产任务 / 运行态 / 门禁基线 / Git 状态」聚合成一个只读视图，
替代人工逐个接口 curl 排查。无副作用、无鉴权（仅内网运营视图）。
"""
# flake8: noqa: E501  # 文件含内联 HTML/CSS/JS，长行为字符串内容不可拆分
import logging
import os
import subprocess
import time
from collections import Counter

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from services.batch_task_service import get_batch_task_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/system", tags=["system"])

# 门禁基线（与 .github/workflows/ci.yml 对齐，人工同步；防"覆盖了但没卡住"的假闭环）
GATE_BASELINE = {
    "backend_coverage": 44,      # backend pytest --cov-fail-under=44（全量 --cov=. 权威实测 45% 取整留缓冲）
    "backend_lint": "0-error",   # backend/.flake8
    "frontend_lint": "0-warning",  # frontend npm run lint -- --max-warnings 0
    "creativeos_coverage": 90,   # creativeos --cov-fail-under=90（CI 实测 90.04%）
}

# 流量侧成本台账（CreativeOS ledger.jsonl；环境变量可覆盖路径，缺省指向默认安装位置）
COST_LEDGER_PATH = os.environ.get(
    "COST_LEDGER_PATH",
    r"D:\1\2\creativeos\data\costs\ledger.jsonl",
)

_comfy_cache = {"ts": 0.0, "ok": False}
_cost_cache = {"ts": 0.0, "data": None}


def _ops_key() -> str:
    """运营视图访问 key（DIRECTOR_OPS_KEY）。未配置 → 本地开发默认不鉴权。"""
    return os.environ.get("DIRECTOR_OPS_KEY", "")


def _require_ops(*candidates: str) -> None:
    """可选的运营视图鉴权：配置了 DIRECTOR_OPS_KEY 则要求任一 candidate 匹配。"""
    expected = _ops_key()
    if not expected:
        return
    if not any(c == expected for c in candidates if c):
        raise HTTPException(status_code=401, detail="invalid or missing ops key")


def _cost_summary() -> dict:
    """聚合流量侧成本台账（CreativeOS ledger.jsonl）→ dashboard ROI 视图。

    只读、容错：文件不存在/损坏/路径失效一律返回空聚合（绝不抛、绝不阻塞 status）。
    """
    now = time.time()
    if now - _cost_cache["ts"] < 30 and _cost_cache["data"] is not None:
        return _cost_cache["data"]
    empty = {
        "ledger_path": COST_LEDGER_PATH,
        "records": 0, "total_cost_usd": 0.0, "llm_cost_usd": 0.0,
        "video_cost_usd": 0.0, "total_calls": 0, "total_tokens": 0,
        "latest_ts": None, "by_provider": {},
    }
    data = dict(empty)
    try:
        if not os.path.exists(COST_LEDGER_PATH):
            data["error"] = "ledger 不存在（流量侧未生成成本记录）"
            _cost_cache.update(ts=now, data=data)
            return data
        import json
        rows = []
        with open(COST_LEDGER_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:  # noqa: BLE001 单行损坏跳过
                        continue
        data["records"] = len(rows)
        data["total_cost_usd"] = round(sum(r.get("total_cost_usd", 0) for r in rows), 2)
        data["llm_cost_usd"] = round(sum(r.get("llm_cost_usd", 0) for r in rows), 2)
        data["video_cost_usd"] = round(sum(r.get("video_cost_usd", 0) for r in rows), 2)
        data["total_calls"] = sum(r.get("total_calls", 0) for r in rows)
        data["total_tokens"] = sum(r.get("total_tokens", 0) for r in rows)
        data["latest_ts"] = rows[-1].get("ts") if rows else None
        prov = Counter()
        for r in rows:
            p = r.get("video_provider") or "unknown"
            prov[p] += r.get("video_cost_usd", 0)
        data["by_provider"] = dict(prov)
        data["over_50_warning"] = any(r.get("over_50_warning") for r in rows)
    except Exception as exc:  # noqa: BLE001 任何异常降级为空聚合
        data["error"] = f"成本台账读取失败: {exc}"
    _cost_cache.update(ts=now, data=data)
    return data


async def _comfy_alive(force: bool = False) -> bool:
    """ComfyUI 8188 可达性探测（30s TTL 缓存，避免 status 每次扫描都阻塞 2s）。"""
    now = time.time()
    if not force and now - _comfy_cache["ts"] < 30:
        return _comfy_cache["ok"]
    ok = False
    try:
        import aiohttp  # 延迟导入：仅运行时需要

        async with aiohttp.ClientSession() as sess:
            async with sess.get(
                "http://127.0.0.1:8188/system_stats",
                timeout=aiohttp.ClientTimeout(total=2),
            ) as resp:
                ok = resp.status == 200
    except Exception:  # noqa: BLE001 探测失败即视为不可达
        ok = False
    _comfy_cache.update(ts=now, ok=ok)
    return ok


def _git_state() -> dict:
    """HEAD commit / 分支 / 脏文件数（git 不可用时优雅降级，绝不抛）。"""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=3,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=3,
        )
        return {
            "head": head.stdout.strip() or None,
            "branch": branch.stdout.strip() or None,
            "dirty_files": sum(1 for line in dirty.stdout.splitlines() if line.strip()),
        }
    except Exception as exc:  # noqa: BLE001 非 git 目录/无 git 命令时降级
        return {"head": None, "branch": None, "dirty_files": None, "error": str(exc)}


@router.get("/status")
async def system_status(
    refresh: bool = Query(False, description="强制刷新 ComfyUI 探测（跳过缓存）"),
    key: str = Query("", description="运营视图访问 key（配置 DIRECTOR_OPS_KEY 时必填）"),
    x_api_key: str = Header("", alias="X-API-Key"),
) -> dict:
    """统一运营视图：任务统计 + 运行态 + 门禁基线 + 成本(ROI) + Git 状态。"""
    _require_ops(key, x_api_key)
    svc = get_batch_task_service()
    batches = await svc.list_batches()
    by_status = Counter(b.status for b in batches)
    recent = []
    for b in batches[:5]:
        meta = b.metadata or {}
        recent.append({
            "task_id": b.batch_id,
            "status": b.status,
            "progress": round(b.progress / 100.0, 2),
            "platform": meta.get("platform"),
            "dimension": meta.get("dimension"),
            "created_at": b.created_at,
            "error": b.error or None,
        })
    return {
        "service": "director",
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "runtime": {
            "pid": os.getpid(),
            "comfyui_alive": await _comfy_alive(refresh),
        },
        "tasks": {
            "total": len(batches),
            "by_status": dict(by_status),
            "recent": recent,
        },
        "gates": GATE_BASELINE,
        "cost": _cost_summary(),
        "git": _git_state(),
    }


_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Director 运营总览</title>
<style>
  :root {
    --bg0:#0b1020; --bg1:#111834; --card:rgba(255,255,255,0.045);
    --line:rgba(255,255,255,0.09); --txt:#e8ecff; --sub:#8b93b8;
    --green:#34d399; --blue:#60a5fa; --amber:#fbbf24; --red:#f87171; --gray:#94a3b8;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
    background:radial-gradient(1200px 600px at 20% -10%,#1a2350 0%,transparent 60%),
               radial-gradient(900px 500px at 90% 10%,#13203f 0%,transparent 55%),var(--bg0);
    color:var(--txt); min-height:100vh; padding:28px 32px;}
  .wrap{max-width:1180px;margin:0 auto}
  h1{font-size:22px;font-weight:700;letter-spacing:.5px;display:flex;align-items:center;gap:12px}
  h1 .dot{width:10px;height:10px;border-radius:50%;background:var(--green);box-shadow:0 0 12px var(--green)}
  .sub{color:var(--sub);font-size:13px;margin-top:4px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-top:22px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;
    backdrop-filter:blur(8px);transition:transform .15s}
  .card:hover{transform:translateY(-2px)}
  .card .label{color:var(--sub);font-size:12px;letter-spacing:.5px}
  .card .val{font-size:26px;font-weight:700;margin-top:6px}
  .card .hint{font-size:12px;color:var(--sub);margin-top:4px}
  .ok{color:var(--green)} .warn{color:var(--amber)} .bad{color:var(--red)} .info{color:var(--blue)}
  .row2{display:grid;grid-template-columns:1.1fr 1.9fr;gap:14px;margin-top:14px}
  @media(max-width:820px){.row2{grid-template-columns:1fr}}
  .donut-wrap{display:flex;align-items:center;gap:22px;height:100%}
  .legend{display:flex;flex-direction:column;gap:8px;font-size:13px}
  .legend .li{display:flex;align-items:center;gap:8px}
  .legend .sw{width:10px;height:10px;border-radius:3px}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th{color:var(--sub);font-weight:500;text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);font-size:12px}
  td{padding:8px 10px;border-bottom:1px solid rgba(255,255,255,0.04)}
  .badge{padding:2px 9px;border-radius:20px;font-size:11px;font-weight:600}
  .b-completed{background:rgba(52,211,153,.15);color:var(--green)}
  .b-pending,.b-queued{background:rgba(96,165,250,.15);color:var(--blue)}
  .b-running{background:rgba(251,191,36,.15);color:var(--amber)}
  .b-failed{background:rgba(248,113,113,.15);color:var(--red)}
  .b-cancelled{background:rgba(148,163,184,.15);color:var(--gray)}
  .foot{color:var(--sub);font-size:12px;margin-top:18px;opacity:.7}
  .err{background:rgba(248,113,113,.1);border:1px solid rgba(248,113,113,.3);color:var(--red);
    padding:14px;border-radius:12px;margin-top:20px}
</style>
</head>
<body>
<div class="wrap">
  <h1><span class="dot"></span>Director 运营总览</h1>
  <div class="sub" id="time">加载中…</div>

  <div class="grid">
    <div class="card"><div class="label">生产任务</div><div class="val info" id="total">–</div>
      <div class="hint" id="statusline">–</div></div>
    <div class="card"><div class="label">ComfyUI</div><div class="val" id="comfy">–</div>
      <div class="hint">127.0.0.1:8188 生成引擎</div></div>
    <div class="card"><div class="label">Backend 覆盖率</div><div class="val warn" id="cov">–</div>
      <div class="hint">CI ratchet 门禁 · 只升不降</div></div>
    <div class="card"><div class="label">CreativeOS 覆盖率</div><div class="val ok" id="covc">–</div>
      <div class="hint">内容端 ratchet 门禁</div></div>
    <div class="card"><div class="label">前端 lint</div><div class="val ok" id="lint">–</div>
      <div class="hint">--max-warnings 0 强制零噪音</div></div>
    <div class="card"><div class="label">Git HEAD</div><div class="val" id="git">–</div>
      <div class="hint" id="gitdirty">–</div></div>
    <div class="card"><div class="label">内容成本 (ROI)</div><div class="val" id="cost">–</div>
      <div class="hint" id="costdetail">–</div></div>
  </div>

  <div class="row2">
    <div class="card">
      <div class="label">任务状态分布</div>
      <div class="donut-wrap">
        <svg id="donut" width="150" height="150" viewBox="0 0 150 150"></svg>
        <div class="legend" id="legend"></div>
      </div>
    </div>
    <div class="card">
      <div class="label">最近任务</div>
      <table>
        <thead><tr><th>任务</th><th>状态</th><th>平台</th><th>维度</th><th>进度</th><th>错误</th></tr></thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
  </div>
  <div class="foot" id="foot">数据来自 /api/system/status · 每 30s 自动刷新 · 只读视图</div>
</div>
<script>
const C = { completed:'#34d399', pending:'#60a5fa', queued:'#60a5fa', running:'#fbbf24',
            failed:'#f87171', cancelled:'#94a3b8' };
async function load(){
  try{
    const r = await fetch('/api/system/status?refresh=1', {headers: apiHeaders()});
    const d = await r.json();
    document.getElementById('time').textContent = '更新于 ' + d.time + ' · PID ' + d.runtime.pid;
    const t = d.tasks;
    document.getElementById('total').textContent = t.total;
    document.getElementById('statusline').textContent =
      Object.entries(t.by_status).map(([k,v])=>k+':'+v).join('  ');
    const cf = document.getElementById('comfy');
    cf.textContent = d.runtime.comfyui_alive ? '运行中' : '离线';
    cf.className = 'val ' + (d.runtime.comfyui_alive ? 'ok' : 'bad');
    document.getElementById('cov').textContent = '≥'+d.gates.backend_coverage+'%';
    document.getElementById('covc').textContent = '≥'+d.gates.creativeos_coverage+'%';
    document.getElementById('lint').textContent = '0w / 0e';
    const g = d.git;
    document.getElementById('git').textContent = g.head ? '#'+g.head : 'n/a';
    const gd = document.getElementById('gitdirty');
    gd.textContent = g.dirty_files!=null ? (g.dirty_files+' 个脏文件') : 'git 不可用';
    gd.className = 'hint ' + ((g.dirty_files||0)>0?'warn':'ok');
    const c = d.cost || {};
    document.getElementById('cost').textContent = '$'+c.total_cost_usd;
    const cd = document.getElementById('costdetail');
    const prov = (c.by_provider||{});
    const provTxt = Object.entries(prov).map(([k,v])=>k+':$'+v.toFixed(2)).join(' ');
    cd.textContent = (c.records!=null ? c.records+' 笔 · LLM $'+c.llm_cost_usd+' · 视频 $'+c.video_cost_usd+(provTxt?' · '+provTxt:'') : '无成本数据');
    cd.className = 'hint ' + ((c.over_50_warning)?'bad':'');
    drawDonut(t.by_status, t.total);
    renderRows(t.recent);
  }catch(e){
    document.getElementById('rows').innerHTML = '<tr><td colspan="6" class="err">加载失败: '+e.message+'</td></tr>';
  }
}
function apiHeaders(){
  const up = new URLSearchParams(window.location.search);
  const k = up.get('key');
  return k ? {'X-API-Key': k} : {};
}
function drawDonut(by, total){
  const entries = Object.entries(by);
  const svg = document.getElementById('donut');
  svg.innerHTML=''; const legend=document.getElementById('legend'); legend.innerHTML='';
  const R=52, cx=75, cy=75, circ=2*Math.PI*R;
  let off=0;
  if(!total){ svg.innerHTML=''; return; }
  for(const [k,v] of entries){
    const frac = v/total;
    const col = C[k]||'#94a3b8';
    const seg = document.createElementNS('http://www.w3.org/2000/svg','circle');
    seg.setAttribute('cx',cx); seg.setAttribute('cy',cy); seg.setAttribute('r',R);
    seg.setAttribute('fill','none'); seg.setAttribute('stroke',col); seg.setAttribute('stroke-width','15');
    seg.setAttribute('stroke-dasharray',`${frac*circ} ${circ}`);
    seg.setAttribute('stroke-dashoffset',-off*circ);
    seg.setAttribute('transform',`rotate(-90 ${cx} ${cy})`);
    svg.appendChild(seg);
    off += frac;
    const li=document.createElement('div'); li.className='li';
    li.innerHTML=`<span class="sw" style="background:${col}"></span>${k} <b style="margin-left:auto">${v}</b>`;
    legend.appendChild(li);
  }
}
function renderRows(rows){
  const tb=document.getElementById('rows'); tb.innerHTML='';
  for(const t of rows){
    const tr=document.createElement('tr');
    const st=(t.status||'pending').toLowerCase();
    tr.innerHTML = `<td style="font-family:monospace;font-size:12px">${(t.task_id||'').slice(0,20)}</td>
      <td><span class="badge b-${st}">${t.status}</span></td>
      <td>${t.platform||'–'}</td><td>${t.dimension||'–'}</td>
      <td>${Math.round((t.progress||0)*100)}%</td>
      <td style="color:var(--red);font-size:12px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${t.error||''}</td>`;
    tb.appendChild(tr);
  }
}
load(); setInterval(load, 30000);
</script>
</body>
</html>
"""


@router.get("/dashboard", response_class=HTMLResponse)
async def system_dashboard(key: str = Query("", description="运营视图访问 key（配置 DIRECTOR_OPS_KEY 时必填）")) -> HTMLResponse:
    """可视化运营总览（自包含 HTML，fetch /api/system/status，30s 自动刷新）。"""
    _require_ops(key)
    return HTMLResponse(content=_DASHBOARD_HTML, status_code=200)
