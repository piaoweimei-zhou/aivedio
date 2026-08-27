"""统一运营状态聚合端点 /api/system/status。

透明化：把分散的「生产任务 / 运行态 / 门禁基线 / Git 状态」聚合成一个只读视图，
替代人工逐个接口 curl 排查。无副作用、无鉴权（仅内网运营视图）。
"""
import logging
import os
import subprocess
import time
from collections import Counter

from fastapi import APIRouter, Query

from services.batch_task_service import get_batch_task_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/system", tags=["system"])

# 门禁基线（与 .github/workflows/ci.yml 对齐，人工同步；防"覆盖了但没卡住"的假闭环）
GATE_BASELINE = {
    "backend_coverage": 40,      # backend pytest --cov-fail-under=40（ratchet 实测 40%）
    "backend_lint": "0-error",   # backend/.flake8
    "frontend_lint": "0-error",  # frontend npm run lint
    "creativeos_coverage": 90,   # creativeos --cov-fail-under=90（实测 94%）
}

_comfy_cache = {"ts": 0.0, "ok": False}


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
) -> dict:
    """统一运营视图：任务统计 + 运行态 + 门禁基线 + Git 状态。"""
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
        "git": _git_state(),
    }
