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

# 平台画像（概念图比例 → I2V 尺寸 → 导出规格，P0 对齐各平台发布规范）
#  ratio:        video aspect_ratio
#  concept_size: concept stage 的 size（竖版 1080x1920 / 横版 1920x1080 / 4:5 1080x1440）
#  video_wh:     video 输出宽高（I2V 图生视频）
#  export:       export stage 导出分辨率
#  label:        导出文件名标签
PLATFORM_PROFILES: Dict[str, Dict[str, Any]] = {
    "douyin":     {"ratio": "9:16", "concept_size": "1080x1920",
                   "video_wh": (720, 1280), "export": "1080x1920", "label": "抖音"},
    "kuaishou":   {"ratio": "9:16", "concept_size": "1080x1920",
                   "video_wh": (720, 1280), "export": "1080x1920", "label": "快手"},
    "xiaohongshu": {"ratio": "3:4", "concept_size": "1080x1440",
                    "video_wh": (720, 960), "export": "1080x1440", "label": "小红书"},
    "bilibili":   {"ratio": "16:9", "concept_size": "1920x1080",
                   "video_wh": (1280, 720), "export": "1920x1080", "label": "B站"},
}
DEFAULT_PLATFORM = "douyin"

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


def _build_steps_from_spec(
    spec: ContentSpec,
) -> List[Dict[str, Any]]:
    """ContentSpec → director BatchStep 列表（薄映射，不改生产逻辑）。

    通用剧本展开（L1，自由组合）：
        script.type == "video_script_mixin" 且 script.acts[N] 非空时，
        按每幕 duration_s/visual_hint/narration 展开为"逐段视频"步骤
        ——任意段数 × 任意时长（5s/10s/3min…），时长 100% 来自契约输入。
    兼容：其余 script.type 保持单 step（script 整体传给对应 stage）。
    """
    stage_type = spec.script.get("type", "")
    if stage_type not in SUPPORTED_STAGE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported script.type '{stage_type}'; "
                   f"allowed={SUPPORTED_STAGE_TYPES}",
        )
    if spec.script.get("acts") and stage_type in ("video_script_mixin", "video_act"):
        return _build_script_video_steps(spec, spec.script["acts"])
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


def _build_script_video_steps(
    spec: ContentSpec,
    acts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """通用剧本展开：acts[N] → 前置概念图 + 逐段视频（任意段数/时长）。

    与前端一键成片 DAG 对齐（concept → video，baseline 已实测）：
        1) concept 场景概念图（comfyui 生成真实图片资产，作 video 输入）
        2) video（I2V 图生视频）segmented_oneclick 路径：
           segment_prompts + segment_durations + tts_texts → 逐段生成拼接。
    时长下限 4s 由 video_stage 强制（生成模型约束）。
    """
    params = spec.params or {}
    seg_prompts: List[str] = []
    seg_durations: List[float] = []
    tts_texts: List[str] = []

    for i, act in enumerate(acts):
        if not isinstance(act, dict):
            continue
        narration = str(act.get("narration") or "").strip()
        visual = str(act.get("visual_hint") or "").strip()
        prompt = visual or narration or f"第{i + 1}幕"
        raw_dur = act.get("duration_s") or act.get("duration_seconds")
        try:
            seg_durations.append(float(raw_dur) if raw_dur else 5.0)
        except (TypeError, ValueError):
            seg_durations.append(5.0)
        seg_prompts.append(prompt)
        tts_texts.append(narration)

    steps: List[Dict[str, Any]] = []

    # ---- 平台画像（决定 concept 比例 / video 尺寸 / 导出规格）----
    platform = (params.get("platform") or (spec.packaging or {}).get("platform")
                or DEFAULT_PLATFORM)
    if platform not in PLATFORM_PROFILES:
        platform = DEFAULT_PLATFORM
    prof = PLATFORM_PROFILES[platform]
    _cs = params.get("concept_size") or prof["concept_size"]
    concept_w, concept_h = (
        (int(x) for x in str(_cs).lower().split("x")) if isinstance(_cs, str) else _cs
    )

    # ---- 1) concept 场景概念图（真实图片资产，按平台比例，作为 video 的 I2V 输入）----
    topic = (
        (spec.packaging or {}).get("title")
        or params.get("prompt")
        or (seg_prompts[0] if seg_prompts else "")
        or "短视频"
    )
    s_concept_scene = "s1_concept_scene1"
    steps.append({
        "step_id": s_concept_scene,
        "stage_id": "concept",
        "name": f"概念图-{spec.content_id}",
        "provider_id": "comfyui",
        "params": {
            "prompt": (params.get("concept_prompt")
                       or (seg_prompts[0] if seg_prompts else topic)),
            "negative_prompt": "low quality, blurry, deformed, ugly",
            "content_type": "scene",
            "size": f"{concept_w}x{concept_h}",
        },
        "input_asset_ids": [],
        "input_from_steps": [],
        "max_retries": 0,
    })

    # ---- 2) video：I2V 图生视频（前置概念图作输入，平台尺寸）----
    _vs = params.get("video_size") or prof["video_wh"]
    v_w, v_h = (
        (int(x) for x in str(_vs).lower().split("x")) if isinstance(_vs, str) else _vs
    )
    video_params: Dict[str, Any] = {
        "prompt": params.get("prompt") or (seg_prompts[0] if seg_prompts else ""),
        "aspect_ratio": params.get("aspect_ratio") or prof["ratio"],
        "resolution": params.get("resolution", "720p"),
        "frame_rate": params.get("frame_rate") or params.get("fps") or 24,
        "width": params.get("width", v_w),
        "height": params.get("height", v_h),
        "segment_prompts": seg_prompts,
        "segment_durations": seg_durations,
        "segmented_oneclick": True,
        "tts_enabled": bool(any(tts_texts)),
        "tts_texts": [t for t in tts_texts if t],
        "tts_mode": params.get("tts_mode", "voice_design"),
        "reference_image_files": list(spec.assets),
    }
    video_params = {k: v for k, v in video_params.items() if v is not None}

    steps.append({
        "step_id": "s2_video",
        "stage_id": "video",
        "name": f"contract-video-{spec.content_id}",
        "provider_id": params.get("provider_id", "minimax_h3"),
        "params": video_params,
        "input_asset_ids": [],
        "input_from_steps": [s_concept_scene],
        "max_retries": 0,
    })

    # ---- 3) subtitle：字幕叠加（对齐一键成片默认流程，台词→时间轴自动估算）----
    s_subtitle = "s3_subtitle"
    steps.append({
        "step_id": s_subtitle,
        "stage_id": "subtitle",
        "name": "字幕叠加",
        "provider_id": "local",
        "params": {
            "subtitle_texts": [{"text": t} for t in tts_texts if t and t.strip()],
            "margin_v": params.get("subtitle_margin_v", "0.13"),
        },
        "input_asset_ids": [],
        "input_from_steps": ["s2_video"],
        "max_retries": 0,
    })

    # ---- 4) hook_overlay：钩子标题叠加（从 packaging 取标题）----
    s_hook = "s4_hook"
    hook_text = (params.get("hook_text")
                 or (spec.packaging or {}).get("hook")
                 or (spec.packaging or {}).get("title")
                 or (seg_prompts[0] if seg_prompts else ""))
    steps.append({
        "step_id": s_hook,
        "stage_id": "hook_overlay",
        "name": "钩子文案叠加",
        "provider_id": "local",
        "params": {
            "hook_text": hook_text,
            "sub_text": params.get("hook_sub_text", "关注我看后续"),
            "duration": params.get("hook_duration", 4),
            "position": params.get("hook_position", "bottom"),
            "margin": params.get("hook_margin"),
        },
        "input_asset_ids": [],
        "input_from_steps": [s_subtitle],
        "max_retries": 0,
    })

    # ---- 5) export：平台规格导出（默认开启，对齐默认成片流程）----
    export_res = params.get("export_resolution") or prof["export"]
    steps.append({
        "step_id": "s5_export",
        "stage_id": "export",
        "name": f"导出成片 {prof['label']}规格 ({export_res})",
        "provider_id": "local",
        "params": {
            "resolution": export_res,
            "format": "mp4",
            "codec": "libx264",
            "bitrate": "8M",
            "name": f"contract_{platform}_{spec.content_id}",
        },
        "input_asset_ids": [],
        "input_from_steps": [s_hook],
        "max_retries": 0,
    })
    return steps


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
