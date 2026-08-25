"""
导演工作台 — 批量任务 API

提供批量任务编排和执行接口。

端点：
- POST   /api/director/batches           创建批量任务
- GET    /api/director/batches           列出批量任务
- GET    /api/director/batches/{id}      获取批量任务详情
- GET    /api/director/batches/{id}/dag  获取 DAG 结构
- POST   /api/director/batches/{id}/start        启动批量任务（支持 dry_run）
- POST   /api/director/batches/{id}/dry-run      预检（DAG结构+Provider可用性）
- POST   /api/director/batches/{id}/cancel       取消批量任务
- POST   /api/director/batches/{id}/retry        重试批量任务
- DELETE /api/director/batches/{id}      删除批量任务
"""

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.batch_task_service import get_batch_task_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/director/batches", tags=["导演工作台-批量任务"])


# ==================== Request Models ====================


class BatchStepRequest(BaseModel):
    step_id: str = ""
    stage_id: str
    name: str = ""
    input_asset_ids: List[str] = []
    input_from_steps: List[str] = []
    provider_id: str = ""
    params: Dict[str, Any] = {}
    max_retries: int = 0


class CreateBatchRequest(BaseModel):
    name: str
    steps: List[BatchStepRequest]
    project_id: str = ""
    stop_on_failure: bool = True
    auto_inherit_project: bool = True
    metadata: Dict[str, Any] = {}


class RetryBatchRequest(BaseModel):
    from_step: str = ""


# ==================== Endpoints ====================


@router.post("")
async def create_batch(request: CreateBatchRequest):
    """创建批量任务"""
    if not request.name.strip():
        raise HTTPException(status_code=400, detail="批量任务名称不能为空")
    if not request.steps:
        raise HTTPException(status_code=400, detail="批量任务至少需要一个步骤")

    svc = get_batch_task_service()
    steps_data = [s.model_dump() for s in request.steps]
    batch = svc.create(
        name=request.name.strip(),
        steps=steps_data,
        project_id=request.project_id,
        stop_on_failure=request.stop_on_failure,
        auto_inherit_project=request.auto_inherit_project,
        metadata=request.metadata,
    )
    logger.info(f"[BatchAPI] 创建批量任务 | id={batch.batch_id} | name={batch.name}")
    return {"success": True, "batch": batch.to_dict()}


@router.get("")
async def list_batches(
    status: str = Query(""),
    project_id: str = Query(""),
):
    """列出批量任务"""
    svc = get_batch_task_service()
    batches = await svc.list_batches(status=status, project_id=project_id)
    return {
        "success": True,
        "batches": [b.to_dict() for b in batches],
        "total": len(batches),
    }


@router.get("/{batch_id}")
async def get_batch(batch_id: str):
    """获取批量任务详情"""
    svc = get_batch_task_service()
    batch = await svc.get(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="批量任务不存在")
    return {"success": True, "batch": batch.to_dict()}


@router.post("/{batch_id}/start")
async def start_batch(
    batch_id: str, dry_run: bool = Query(False, description="预检模式：只检查不执行")
):
    """启动批量任务

    - dry_run=true: 只预检（DAG结构+Provider可用性），不执行
    """
    svc = get_batch_task_service()
    batch = await svc.get(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="批量任务不存在")
    if not dry_run and batch.status == "running":
        raise HTTPException(status_code=400, detail="批量任务已在运行")
    ok = await svc.start(batch_id, dry_run=dry_run)
    if not ok:
        detail = batch.error or "启动失败"
        raise HTTPException(status_code=400, detail=detail)
    if dry_run:
        return {"success": True, "message": "预检通过", "dry_run": True}
    return {"success": True, "message": "批量任务已启动", "engine": "dag"}


@router.get("/{batch_id}/dag")
async def get_batch_dag(batch_id: str):
    """获取批量任务的 DAG 结构（用于前端可视化）"""
    svc = get_batch_task_service()
    dag = await svc.get_dag(batch_id)
    if dag is None:
        raise HTTPException(status_code=404, detail="批量任务不存在")
    return {"success": True, "dag": dag}


@router.post("/{batch_id}/dry-run")
async def dry_run_batch(batch_id: str):
    """预检批量任务（DAG结构+Provider可用性），不执行"""
    svc = get_batch_task_service()
    batch = await svc.get(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="批量任务不存在")
    ok = await svc.start(batch_id, dry_run=True)
    if not ok:
        raise HTTPException(status_code=400, detail=batch.error or "预检失败")
    # 获取详细的 DAG 和 Provider 信息
    dag = await svc.get_dag(batch_id) or {}
    from services.provider_service import get_provider_service

    provider_svc = get_provider_service()
    check_result = provider_svc.pre_check_batch(batch.steps)
    return {
        "success": True,
        "message": "预检通过",
        "dag": dag,
        "providers": check_result,
    }


@router.post("/{batch_id}/cancel")
async def cancel_batch(batch_id: str):
    """取消批量任务"""
    svc = get_batch_task_service()
    ok = await svc.cancel(batch_id)
    if not ok:
        raise HTTPException(status_code=400, detail="取消失败（任务不存在或未运行）")
    return {"success": True, "message": "批量任务已取消"}


@router.post("/{batch_id}/retry")
async def retry_batch(batch_id: str, request: RetryBatchRequest):
    """重试批量任务"""
    svc = get_batch_task_service()
    ok = await svc.retry(batch_id, from_step=request.from_step)
    if not ok:
        raise HTTPException(status_code=400, detail="重试失败（任务不存在或运行中）")
    return {"success": True, "message": "批量任务已重新启动"}


@router.delete("/{batch_id}")
async def delete_batch(batch_id: str):
    """删除批量任务"""
    svc = get_batch_task_service()
    batch = await svc.get(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="批量任务不存在")
    if batch.status == "running":
        raise HTTPException(status_code=400, detail="运行中的任务不可删除")
    ok = await svc.delete(batch_id)
    if not ok:
        raise HTTPException(status_code=400, detail="删除失败")
    return {"success": True, "message": "批量任务已删除"}
