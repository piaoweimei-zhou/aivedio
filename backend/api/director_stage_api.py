"""
导演工作台 — 阶段路由 API

提供生产阶段的发现和执行接口

核心改造：同步轮询 → 异步任务队列
- POST /execute → 立即返回 task_id (202 Accepted)
- GET /task/{task_id} → 轮询任务状态（轻量查询，连接瞬间释放）
- GET /execute-sync → 保留同步模式（向后兼容，仅用于快速文生图）
"""

import dataclasses
import logging
import os
from typing import Any, Dict, List, Optional
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from services.stage_service import get_stage_service
from services.gen_task_manager import get_gen_task_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/director/stages", tags=["导演工作台-阶段"])


# ==================== Request Models ====================

class ExecuteStageRequest(BaseModel):
    stage_id: str
    input_asset_ids: List[str]
    provider_id: str = ""
    params: Dict[str, Any] = {}
    async_mode: bool = True  # 默认异步模式


# ==================== Stage Endpoints ====================

@router.get("")
async def list_stages():
    """列出所有生产阶段"""
    svc = get_stage_service()
    return {"stages": svc.list_stages()}


@router.post("/execute")
async def execute_stage(request: ExecuteStageRequest):
    """执行生产阶段（异步模式：立即返回 task_id）

    默认异步模式 (async_mode=True)：
      - 立即返回 202 Accepted + task_id
      - 前端通过 GET /task/{task_id} 轮询状态
      - 适用于所有耗时操作（精修、视频、分镜等）

    同步模式 (async_mode=False)：
      - 阻塞等待结果返回
      - 仅适用于快速文生图（<10s）
    """
    # ⭐ Phase 4：参数校验中间件（类型/范围/必填）
    from services.param_validator import validate_stage_params
    validation_errors = validate_stage_params(request.stage_id, request.params)
    if validation_errors:
        logger.warning(f"[StageAPI] 参数校验失败 | stage={request.stage_id} | errors={validation_errors}")
        raise HTTPException(
            status_code=422,
            detail={"message": "参数校验失败", "errors": validation_errors},
        )

    if not request.async_mode:
        # 同步模式：直接执行并等待结果（向后兼容）
        return await _execute_sync(request)

    # 异步模式：创建任务，后台执行
    task_mgr = get_gen_task_manager()
    svc = get_stage_service()

    # 创建任务 — execute_fn 是一个闭包，捕获所有参数
    async def _do_execute():
        _input_ids = request.input_asset_ids
        logger.info(f"[StageAPI] 执行闭包 | stage={request.stage_id} | input_ids={_input_ids}")
        try:
            result = await svc.execute(
                stage_id=request.stage_id,
                input_asset_ids=_input_ids,
                provider_id=request.provider_id,
                params=request.params,
            )
            if not result.success:
                logger.error(
                    f"[StageAPI] 阶段执行失败 | stage={request.stage_id} | "
                    f"error={result.error} | elapsed_ms={result.elapsed_ms}"
                )
                # 阶段执行失败 → 任务标记为 failed（而非 completed），
                # 让前端能正确展示错误信息
                raise RuntimeError(result.error or "阶段执行失败")
            logger.info(
                f"[StageAPI] 闭包完成 | stage={request.stage_id} | "
                f"success={result.success} | error={result.error[:200] if result.error else ''} | "
                f"elapsed_ms={result.elapsed_ms}"
            )
            return result
        except Exception as e:
            logger.error(f"[StageAPI] 闭包异常 | stage={request.stage_id} | error={e}")
            # 重新抛出，让任务管理器将任务标记为 failed（而非 completed）
            raise

    task = await task_mgr.create_task(
        stage_id=request.stage_id,
        execute_fn=_do_execute,
    )

    # 提交执行（不阻塞）
    await task_mgr.submit_task(task.task_id)

    # 立即返回 task_id
    return JSONResponse(
        status_code=202,
        content={
            "task_id": task.task_id,
            "status": "running",
            "stage_id": request.stage_id,
            "message": "任务已提交，通过 GET /task/{task_id} 查询状态",
        }
    )


@router.get("/task/{task_id}")
async def get_task_status(task_id: str):
    """查询生成任务状态（轻量查询，连接瞬间释放）"""
    task_mgr = get_gen_task_manager()
    task = task_mgr.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    response = task.to_dict()

    # 如果任务完成，附带结果
    if task.status == "completed" and task.result:
        result = _safe_result(task.result)
        response["success"] = result.get("success", False)
        asset = result.get("asset")
        response["asset"] = {
            "asset_id": _safe_get(asset, "asset_id"),
            "asset_type": _safe_get(asset, "asset_type"),
            "content_type": _safe_get(asset, "content_type"),
            "name": _safe_get(asset, "name"),
            "urls": _safe_get(asset, "urls", []),
        } if asset and result.get("success") else None
        response["elapsed_ms"] = result.get("elapsed_ms", 0)

    if task.status == "failed":
        response["success"] = False
        response["error"] = task.error

    return response


@router.post("/task/{task_id}/cancel")
async def cancel_task(task_id: str):
    """取消待处理/运行中的生成任务（尽力而为）"""
    task_mgr = get_gen_task_manager()
    ok = await task_mgr.cancel_task(task_id)
    if not ok:
        task = task_mgr.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        return {
            "success": False,
            "task_id": task_id,
            "message": "任务已结束或无法取消",
            "status": task.status,
        }
    return {"success": True, "task_id": task_id, "status": "cancelled"}


# ==================== 辅助函数 ====================

def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
    """安全地从 dataclass 或 dict 中获取字段值"""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _safe_result(result: Any) -> dict:
    """将结果统一转换为 dict（兼容 dataclass 和 dict 两种格式）"""
    if result is None:
        return {}
    if isinstance(result, dict):
        return result
    if dataclasses.is_dataclass(result):
        return {f.name: getattr(result, f.name) for f in dataclasses.fields(result)}
    return {"_raw": str(result)}


@router.get("/tasks")
async def list_tasks(status: str = ""):
    """列出所有生成任务"""
    task_mgr = get_gen_task_manager()
    tasks = await task_mgr.list_tasks(status)
    return {
        "tasks": [t.to_dict() for t in tasks],
        "running": task_mgr.running_count,
        "pending": task_mgr.pending_count,
    }


@router.post("/resolve")
async def resolve_stages(input_types: List[str]):
    """根据输入类型查找可用阶段"""
    svc = get_stage_service()
    stages = svc.resolve(input_types)
    return {"stages": [
        {
            "stage_id": s.stage_id,
            "name": s.name,
            "output_type": s.output_type,
            "default_provider": s.default_provider,
        }
        for s in stages
    ]}


# ==================== 新增阶段专用接口 ====================

@router.get("/script/video-types")
async def list_video_types():
    """列出 AI 剧本支持的 6 种视频类型"""
    from services.stages.script_stage import list_video_types
    return {"video_types": list_video_types()}


@router.get("/graphic/types")
async def list_graphic_types():
    """列出图文生成支持的 6 种图文类型"""
    from services.stages.graphic_stage import list_graphic_types
    return {"graphic_types": list_graphic_types()}


@router.get("/tts/voices")
async def list_tts_voices():
    """列出 TTS 多角色音色库"""
    from services.stages.tts_utils import list_default_voices
    return {"voices": list_default_voices()}


@router.get("/script/{asset_id}")
async def get_script_content(asset_id: str):
    """获取剧本 JSON 内容"""
    import json
    from services.asset_service import get_asset_service
    from services.providers.provider_utils import output_file_from_url

    asset_svc = get_asset_service()
    asset = asset_svc.get(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="剧本资产不存在")
    if asset.asset_type != "script":
        raise HTTPException(status_code=400, detail=f"资产类型不是 script: {asset.asset_type}")

    # 从 URL 读取 JSON
    script_url = asset.urls[0] if asset.urls else asset.metadata.get("script_url", "")
    if not script_url:
        raise HTTPException(status_code=404, detail="剧本 URL 为空")

    # 本地文件
    local = output_file_from_url(script_url)
    if local and os.path.exists(local):
        with open(local, "r", encoding="utf-8") as f:
            return {"script": json.load(f), "asset": _asset_to_dict(asset)}

    # HTTP
    if script_url.startswith(("http://", "https://")):
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(script_url)
            resp.raise_for_status()
            return {"script": resp.json(), "asset": _asset_to_dict(asset)}

    raise HTTPException(status_code=404, detail=f"剧本文件不可访问: {script_url}")


@router.get("/screen/windows")
async def list_record_windows():
    """列出当前可录制的窗口（Windows 专用，返回窗口标题供前端选择）"""
    from services.stages.screen_record_stage import list_windows_async
    windows = await list_windows_async()
    return {"windows": windows}


def _asset_to_dict(asset) -> Dict[str, Any]:
    """资产转 dict（避免循环导入 asset_service 已有方法）"""
    import dataclasses
    if dataclasses.is_dataclass(asset):
        return {f.name: getattr(asset, f.name) for f in dataclasses.fields(asset)}
    return {"asset_id": getattr(asset, "asset_id", ""), "name": getattr(asset, "name", "")}


# ==================== 内部方法 ====================

async def _execute_sync(request: ExecuteStageRequest):
    """同步执行（向后兼容快速文生图）"""
    svc = get_stage_service()
    result = await svc.execute(
        stage_id=request.stage_id,
        input_asset_ids=request.input_asset_ids,
        provider_id=request.provider_id,
        params=request.params,
    )
    r = _safe_result(result)
    asset = r.get("asset")
    return {
        "success": r.get("success", False),
        "asset": {
            "asset_id": _safe_get(asset, "asset_id"),
            "asset_type": _safe_get(asset, "asset_type"),
            "content_type": _safe_get(asset, "content_type"),
            "name": _safe_get(asset, "name"),
            "urls": _safe_get(asset, "urls", []),
        } if asset and r.get("success") else None,
        "error": r.get("error"),
        "elapsed_ms": r.get("elapsed_ms", 0),
    }


# ⚠️ 通配路由必须声明在末尾：FastAPI 按声明顺序匹配。若 /{stage_id} 提前注册，
# 会抢先吞掉 /tasks、/script/video-types、/tts/voices 等单段静态路由，
# 导致它们永远匹配到「阶段不存在」(404)。具体路由优先，通配兜底。
@router.get("/{stage_id}")
async def get_stage(stage_id: str):
    """获取阶段详情"""
    svc = get_stage_service()
    stage_def = svc.get_stage_def(stage_id)
    if not stage_def:
        raise HTTPException(status_code=404, detail="阶段不存在")
    return {
        "stage_id": stage_def.stage_id,
        "name": stage_def.name,
        "input_types": stage_def.input_types,
        "input_content_types": stage_def.input_content_types,
        "output_type": stage_def.output_type,
        "default_provider": stage_def.default_provider,
        "supported_providers": stage_def.supported_providers,
        "description": stage_def.description,
    }
