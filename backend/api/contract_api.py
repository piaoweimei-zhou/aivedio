"""
内容生产契约（Production Contract）适配器 — director 端薄适配器

角色：
    TrafficOS（流量侧）与 director（生产侧）之间唯一的接口层。
    本模块**不修改任何生产逻辑**，只做外部消费面适配：
      - API Key 鉴权（X-API-Key）
      - ContentSpec → director 内部 BatchTask 的映射（幂等）
      - 状态/产物查询、取消、能力声明

单一事实源（SSOT）：docs/01_规划/traffic_contract.openapi.yaml
    —— 接口与 schema 以该文件为准，本模块按它实现。

挂载（未默认挂载，避免影响现有测试/启动）：
    from api.contract_api import router as contract_router
    app.include_router(contract_router)   # prefix=/contract

安全：
    生产部署必须设置环境变量 CONTRACT_API_KEY（强随机值）。
    未设置时本模块使用默认开发密钥并打印告警（仅开发便利）。
"""

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from services.batch_task_service import get_batch_task_service

logger = logging.getLogger(__name__)

CONTRACT_PREFIX = "/contract"
router = APIRouter(prefix=CONTRACT_PREFIX, tags=["内容生产契约"])

# ==================== 常量 / 配置 ====================

CONTRACT_API_KEY_ENV = "CONTRACT_API_KEY"
_DEV_DEFAULT_KEY = "dev-contract-key-not-for-prod"

# script.type 允许值（对齐 director 真实 stage；P0 可改为从 stage 服务动态拉取）
SUPPORTED_STAGE_TYPES = [
    "video_script_mixin",
    "storyboard_batch",
    "video_act",
    "compose",
    "export",
]

# director 内部 BatchTask.status → 契约 TaskStatus 映射
_BATCH_TO_CONTRACT_STATUS = {
    "pending": "queued",
    "running": "running",
    "completed": "done",
    "failed": "failed",
    "cancelled": "cancelled",
}

# content_id → batch_id 幂等映射（内存态；真实部署可换 redis/DB。
# 骨架已利用 batch.metadata["content_id"] 持久化反查，重启后可恢复幂等语义）
_idem_index: Dict[str, str] = {}


# ==================== 鉴权 ====================

def _load_api_key() -> str:
    key = os.environ.get(CONTRACT_API_KEY_ENV, "")
    if not key:
        logger.warning(
            "[Contract] 未设置 %s，使用默认开发密钥（仅限本地开发）", CONTRACT_API_KEY_ENV
        )
        return _DEV_DEFAULT_KEY
    return key


def require_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> str:
    """契约鉴权依赖：X-API-Key 必须匹配配置密钥。"""
    expected = _load_api_key()
    if x_api_key is None or x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
    return x_api_key


# ==================== Schemas（对齐 openapi.yaml）====================

class ContentSpec(BaseModel):
    content_id: str = Field(..., description="幂等键")
    dimension: Optional[str] = Field(
        default=None, description="pure_content/knowledge/soft_ad"
    )
    monetizer: Optional[str] = Field(
        default=None,
        description="adshare/netdisk/xianyu/saas/resource/course/tool",
    )
    account_id: Optional[str] = None
    script: Dict[str, Any] = Field(..., description="生产输入，type 须在 capabilities 声明的范围内")
    assets: List[str] = Field(default_factory=list, description="参考素材公网 URL")
    packaging: Optional[Dict[str, Any]] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    callback_url: Optional[str] = None
    traffic_meta: Optional[Dict[str, Any]] = None
    # 非契约内部扩展：默认不自动启动（骨架 round-trip 验证安全；生产可置 True）
    auto_start: bool = False


class ProduceResponse(BaseModel):
    task_id: str
    content_id: str
    status: str
    duplicate: bool = False


class AssetInfo(BaseModel):
    asset_id: str
    type: str = "video"
    url: str = ""
    ttl_sec: int = 86400
    size_bytes: int = 0


class ErrorPayload(BaseModel):
    error_code: str = "UNKNOWN"
    message: str = ""
    failed_step_id: Optional[str] = None


class TaskDetail(BaseModel):
    task_id: str
    content_id: Optional[str] = None
    status: str
    progress: float = 0.0
    current_step: Optional[str] = None
    assets: List[AssetInfo] = Field(default_factory=list)
    error: Optional[ErrorPayload] = None
    traffic_meta: Optional[Dict[str, Any]] = None


class CancelResponse(BaseModel):
    task_id: str
    status: str
    cancelled: bool = False
    cancel_rejected: bool = False
    message: str = ""


class ClaimResponse(BaseModel):
    asset_id: str
    claimed: bool = True


class Capabilities(BaseModel):
    version: str = "1.0.0"
    supported_stage_types: List[str] = SUPPORTED_STAGE_TYPES
    params_defaults: Dict[str, Any] = {
        "resolution": "480p",
        "fps": 24,
        "duration_s": 5,
    }
    providers: List[str] = ["comfyui"]
    api: Dict[str, str] = {"version": "1.0.0", "min_trafficos": "0.1.0"}


# ==================== 内部工具 ====================

def _normalize_status(batch_status: str) -> str:
    return _BATCH_TO_CONTRACT_STATUS.get(batch_status, batch_status)


def _build_steps_from_spec(spec: ContentSpec) -> List[Dict[str, Any]]:
    """ContentSpec → director BatchStep 列表（薄映射，不改生产逻辑）。

    骨架实现：script.type 作为单个 stage_id，script 内容整体传入 params，
    由该 stage 自行消费。更复杂的"剧本→多幕→合成"编排属于 P0 细化，
    不在此骨架中预设（保持与 capabilities 声明的单一 type 对齐）。
    """
    stage_type = spec.script.get("type", "")
    if stage_type not in SUPPORTED_STAGE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported script.type '{stage_type}'; "
                   f"allowed={SUPPORTED_STAGE_TYPES}",
        )
    step: Dict[str, Any] = {
        "stage_id": stage_type,
        "name": f"contract-{spec.content_id}",
        "provider_id": "",
        "params": {
            "script": spec.script,
            "assets": spec.assets,
            "packaging": spec.packaging or {},
            **spec.params,
        },
        "max_retries": 0,
    }
    return [step]


def _asset_url(batch_id: str, asset_id: str) -> str:
    """产物签名 URL。骨架返回可定位路径，真实部署需替换为签名 URL 服务（TTL）。"""
    # TODO(P0): 接入 director 静态文件服务 + 签名 URL（ttl_sec 默认 86400）
    return f"/static/contract-assets/{batch_id}/{asset_id}"


# ==================== 接口实现 ====================

@router.post("/produce", response_model=ProduceResponse, dependencies=[Depends(require_api_key)])
async def produce(spec: ContentSpec) -> ProduceResponse:
    """提交内容规格，创建生产任务（幂等，按 content_id 去重）。"""
    svc = get_batch_task_service()

    # 幂等：先查已存在 content_id
    existing_batch_id = _idem_index.get(spec.content_id)
    if existing_batch_id is None:
        # 从已持久化的 batch metadata 反查（重启后恢复幂等）
        for b in await svc.list_batches():
            if (b.metadata or {}).get("content_id") == spec.content_id:
                existing_batch_id = b.batch_id
                _idem_index[spec.content_id] = existing_batch_id
                break
    if existing_batch_id:
        batch = await svc.get(existing_batch_id)
        if batch is not None:
            return ProduceResponse(
                task_id=batch.batch_id,
                content_id=spec.content_id,
                status=_normalize_status(batch.status),
                duplicate=True,
            )

    steps = _build_steps_from_spec(spec)
    metadata = {
        "content_id": spec.content_id,
        "dimension": spec.dimension,
        "monetizer": spec.monetizer,
        "account_id": spec.account_id,
        "callback_url": spec.callback_url,
        "traffic_meta": spec.traffic_meta or {},
        "contract_version": "1.0.0",
    }
    batch = svc.create(
        name=f"contract-{spec.content_id}",
        steps=steps,
        metadata=metadata,
    )
    _idem_index[spec.content_id] = batch.batch_id

    if spec.auto_start:
        # 异步启动，不阻塞响应；调用方经 GET 查询进度
        import asyncio
        asyncio.create_task(svc.start(batch.batch_id))

    logger.info(
        "[Contract] produce | content_id=%s task_id=%s auto_start=%s",
        spec.content_id, batch.batch_id, spec.auto_start,
    )
    return ProduceResponse(
        task_id=batch.batch_id,
        content_id=spec.content_id,
        status="queued",
        duplicate=False,
    )


@router.get(
    "/produce/{task_id}",
    response_model=TaskDetail,
    dependencies=[Depends(require_api_key)],
)
async def get_produce(task_id: str) -> TaskDetail:
    """查询任务进度/状态/产物/错误。"""
    svc = get_batch_task_service()
    batch = await svc.get(task_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")

    current_step = None
    if 0 <= batch.current_step_index < len(batch.steps):
        current_step = batch.steps[batch.current_step_index].stage_id

    error_payload = None
    if batch.error:
        error_payload = ErrorPayload(error_code="STAGE_FAILED", message=batch.error)

    # TODO(P0): status=done 时从产物目录扫描真实 assets（type/url/size）
    assets: List[AssetInfo] = []

    return TaskDetail(
        task_id=batch.batch_id,
        content_id=(batch.metadata or {}).get("content_id"),
        status=_normalize_status(batch.status),
        progress=batch.progress / 100.0,
        current_step=current_step,
        assets=assets,
        error=error_payload,
        traffic_meta=(batch.metadata or {}).get("traffic_meta"),
    )


@router.post(
    "/produce/{task_id}/cancel",
    response_model=CancelResponse,
    dependencies=[Depends(require_api_key)],
)
async def cancel_produce(task_id: str) -> CancelResponse:
    """取消任务（幂等）。

    对齐 director 真实能力：仅 running 状态可取消。
    queued（已创建未启动）返回 cancel_rejected=True —— 任务未运行，
    无需取消；终态（done/failed/cancelled）同样拒绝。
    """
    svc = get_batch_task_service()
    batch = await svc.get(task_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")

    if batch.status == "pending":
        return CancelResponse(
            task_id=task_id,
            status="queued",
            cancelled=False,
            cancel_rejected=True,
            message="task not started yet (queued); cancel only available while running",
        )
    if batch.status != "running":
        return CancelResponse(
            task_id=task_id,
            status=_normalize_status(batch.status),
            cancelled=False,
            cancel_rejected=True,
            message=f"task already in terminal state: {batch.status}",
        )
    ok = await svc.cancel(task_id)
    return CancelResponse(
        task_id=task_id,
        status="cancelled",
        cancelled=ok,
        message="cancelled" if ok else "cancel failed",
    )


@router.post(
    "/produce/{task_id}/assets/{asset_id}/claim",
    response_model=ClaimResponse,
    dependencies=[Depends(require_api_key)],
)
async def claim_asset(task_id: str, asset_id: str) -> ClaimResponse:
    """标记产物已由 TrafficOS 转存（延长保活/防提前清理）。"""
    # TODO(P0): 接入签名 URL 服务的保活/引用计数
    logger.info("[Contract] claim | task=%s asset=%s", task_id, asset_id)
    return ClaimResponse(asset_id=asset_id, claimed=True)


@router.get(
    "/capabilities",
    response_model=Capabilities,
    dependencies=[Depends(require_api_key)],
)
async def capabilities() -> Capabilities:
    """能力声明：TrafficOS 先拉此接口再构造 Content Spec。"""
    return Capabilities()


# 便于外部直接运行本模块做冒烟测试
if __name__ == "__main__":
    print("contract_api: 请通过 main.py include_router 挂载，或用 pytest 验证。")
