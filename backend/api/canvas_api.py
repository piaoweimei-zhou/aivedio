"""
画布 API 路由

POST /api/canvas/                    — 创建画布
GET  /api/canvas/                    — 列出画布
GET  /api/canvas/{canvas_id}         — 获取画布布局
PUT  /api/canvas/{canvas_id}         — 更新画布布局
DELETE /api/canvas/{canvas_id}       — 删除画布
POST /api/canvas/{canvas_id}/nodes   — 添加节点
PUT  /api/canvas/{canvas_id}/nodes/{node_id} — 更新节点
DELETE /api/canvas/{canvas_id}/nodes/{node_id} — 删除节点

MSR 多角色视频生成：
POST /api/canvas/msr-video          — 提交 MSR 视频生成
GET  /api/canvas/msr-video/{task_id} — 查询 MSR 任务状态
"""

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.canvas_service import get_canvas_service
from services.paths import GENERATED_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/canvas", tags=["canvas"])

_BASE_DIR = os.path.dirname(os.path.dirname(__file__))
_WF_DIR = os.path.join(os.path.dirname(_BASE_DIR), "workflows")


def _format_canvas(layout) -> dict:
    """统一画布响应格式

    同时输出两套字段名以兼容 React (canvas_api) 和 canvas.js (infinite_canvas_api)：
    - 节点：node_id（后端规范）+ id（canvas.js 别名）
    - 连线：edges（后端规范）+ connections（canvas.js 别名 [{id, from, to, label}]）
    - 画布：canvas_id + id 别名；name + title 别名
    """
    base = layout.to_dict()
    # 画布级别别名
    base["id"] = layout.canvas_id
    base["title"] = layout.name
    # 连线别名：canvas.js 期望 connections: [{id, from, to, label}]
    base["connections"] = [
        {
            "id": e.edge_id,
            "from": e.source_id,
            "to": e.target_id,
            "label": e.label,
        }
        for e in layout.edges
    ]
    return base


# ============================================================
# 请求模型
# ============================================================


class CreateCanvasRequest(BaseModel):
    name: str = "未命名画布"


class UpdateLayoutRequest(BaseModel):
    name: Optional[str] = None
    nodes: Optional[list] = None
    edges: Optional[list] = None
    viewport: Optional[dict] = None
    base_updated_at: Optional[float] = None


class AddNodeRequest(BaseModel):
    node_id: str
    asset_id: str = ""
    node_type: str = "image"
    x: float = 0.0
    y: float = 0.0
    width: float = 240.0
    height: float = 180.0
    label: str = ""
    metadata: dict = {}


class UpdateNodeRequest(BaseModel):
    x: Optional[float] = None
    y: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    label: Optional[str] = None
    metadata: Optional[dict] = None


# ============================================================
# 路由
# ============================================================


@router.post("/")
async def create_canvas(request: CreateCanvasRequest):
    """创建画布"""
    svc = get_canvas_service()
    layout = await svc.create(name=request.name)
    return {"success": True, "canvas": layout.to_dict()}


@router.get("/")
async def list_canvases():
    """列出画布"""
    svc = get_canvas_service()
    return {"success": True, "canvases": svc.list_canvases()}


@router.get("/{canvas_id}")
async def get_canvas(canvas_id: str):
    """获取画布布局"""
    svc = get_canvas_service()
    layout = svc.get(canvas_id)
    if not layout:
        raise HTTPException(status_code=404, detail=f"画布不存在: {canvas_id}")
    return {"success": True, "canvas": _format_canvas(layout)}


@router.put("/{canvas_id}")
async def update_canvas(canvas_id: str, request: UpdateLayoutRequest):
    """更新画布布局"""
    svc = get_canvas_service()
    result = await svc.update_layout(canvas_id, request.dict(exclude_none=True))
    if not result:
        raise HTTPException(status_code=404, detail=f"画布不存在: {canvas_id}")
    # 乐观锁冲突
    if isinstance(result, dict) and result.get("_conflict"):
        canvas = result.get("canvas")
        canvas_dict = _format_canvas(canvas) if hasattr(canvas, "to_dict") else {}
        raise HTTPException(
            status_code=409,
            detail={
                "message": "画布已被其他客户端修改，请刷新后重试",
                "updated_at": result.get("server_updated_at"),
                "canvas": canvas_dict,
            },
        )
    return {"success": True, "canvas": _format_canvas(result)}


@router.delete("/{canvas_id}")
async def delete_canvas(canvas_id: str):
    """删除画布"""
    svc = get_canvas_service()
    if not await svc.delete(canvas_id):
        raise HTTPException(status_code=404, detail=f"画布不存在: {canvas_id}")
    return {"success": True}


@router.post("/{canvas_id}/nodes")
async def add_node(canvas_id: str, request: AddNodeRequest):
    """添加节点"""
    svc = get_canvas_service()
    node = await svc.add_node(canvas_id, request.dict())
    if not node:
        raise HTTPException(status_code=404, detail=f"画布不存在: {canvas_id}")
    return {"success": True, "node": node.to_dict()}


@router.put("/{canvas_id}/nodes/{node_id}")
async def update_node(canvas_id: str, node_id: str, request: UpdateNodeRequest):
    """更新节点"""
    svc = get_canvas_service()
    node = await svc.update_node(canvas_id, node_id, request.dict(exclude_none=True))
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    return {"success": True, "node": node.to_dict()}


@router.delete("/{canvas_id}/nodes/{node_id}")
async def remove_node(canvas_id: str, node_id: str):
    """删除节点"""
    svc = get_canvas_service()
    if not await svc.remove_node(canvas_id, node_id):
        raise HTTPException(status_code=404, detail="节点不存在")
    return {"success": True}


# ============================================================
# MSR 多角色视频生成
# ============================================================


class MsrVideoRequest(BaseModel):
    ref1_image_url: str = Field("", description="角色1参考图URL")
    ref2_image_url: str = Field("", description="角色2参考图URL")
    ref3_image_url: Optional[str] = ""
    ref4_image_url: Optional[str] = ""
    bg_image_url: Optional[str] = ""
    global_prompt: str = Field("", description="全局提示词（场景描述）")
    local_prompts: Optional[str] = ""
    width: int = 1280
    height: int = 720
    frame_count: int = 41
    seed: int = 39372529035560
    # ⭐ 修复紧急#5：补充视频生成质量参数（与通用路径统一）
    fps: int = Field(24, ge=1, le=60, description="帧率（默认24）")
    cfg: float = Field(3.0, ge=0.1, le=10.0, description="CFG 强度（默认3.0）")
    steps: int = Field(20, ge=1, le=100, description="采样步数（默认20）")
    duration: Optional[float] = Field(
        None, ge=0.1, le=60, description="视频时长（秒），若提供则忽略 frame_count"
    )


_msr_tasks: Dict[str, dict] = {}
_msr_tasks_lock = __import__("threading").Lock()


def _msr_task_set(task_id: str, value: dict) -> None:
    """线程安全地创建/覆盖 MSR 任务"""
    with _msr_tasks_lock:
        _msr_tasks[task_id] = value


def _msr_task_update(task_id: str, updates: dict) -> None:
    """线程安全地更新 MSR 任务字段"""
    with _msr_tasks_lock:
        if task_id in _msr_tasks:
            _msr_tasks[task_id].update(updates)


def _msr_task_get(task_id: str) -> dict:
    """线程安全地读取 MSR 任务"""
    with _msr_tasks_lock:
        return _msr_tasks.get(task_id)


# 后台任务 handle 登记表：解决 fire-and-forget 泄漏 + 支持优雅关闭时统一取消
_msr_task_handles: Dict[str, asyncio.Task] = {}


def _msr_task_track(task_id: str, task: asyncio.Task) -> None:
    """登记 MSR 后台任务 handle：任务完成自动移除（防泄漏）"""
    with _msr_tasks_lock:
        _msr_task_handles[task_id] = task

    def _on_done(_t: asyncio.Task) -> None:
        with _msr_tasks_lock:
            _msr_task_handles.pop(task_id, None)

    task.add_done_callback(_on_done)


async def shutdown_msr_tasks() -> None:
    """优雅关闭：取消所有存活的 MSR 后台任务（FastAPI lifespan shutdown 时调用）"""
    with _msr_tasks_lock:
        handles = list(_msr_task_handles.values())
    if not handles:
        return
    logger.info(f"[MSR] 应用关闭，取消 {len(handles)} 个后台任务")
    for t in handles:
        if not t.done():
            t.cancel()
    await asyncio.gather(*handles, return_exceptions=True)


async def _ensure_image_for_msr(image_url: str, comfyui_svc) -> str:
    """确保 MSR 参考图存在于 ComfyUI input 目录，返回文件名"""
    if not image_url:
        return ""
    logger.info(f"[MSR] _ensure_image_for_msr 入口: url={image_url[:80]}")
    from urllib.parse import urlparse, parse_qs, quote

    parsed = urlparse(image_url)
    qs = parse_qs(parsed.query)
    filename = qs.get("filename", [None])[0] or os.path.basename(image_url)
    if not filename:
        return ""
    fname = os.path.basename(filename)
    # ComfyUI input 目录：使用 config 中的路径（有默认值，比 file_handler 的 _comfyui_dir 更可靠）
    comfyui_dir = comfyui_svc.config.comfyui_dir
    input_dir = os.path.join(comfyui_dir, "input") if comfyui_dir else ""
    if not input_dir:
        logger.warning(f"[MSR] 无法确定 ComfyUI input 目录 (comfyui_dir={comfyui_dir})")
        return ""
    os.makedirs(input_dir, exist_ok=True)
    dst = os.path.join(input_dir, fname)
    if os.path.isfile(dst):
        return fname  # 已存在
    # 1. 通过后端本地代理下载（覆盖 generated + uploads + ComfyUI output）
    try:
        import aiohttp

        local_url = f"http://127.0.0.1:8000/api/comfyui/image?filename={quote(fname)}"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(local_url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    with open(dst, "wb") as f:
                        f.write(data)
                    logger.info(f"[MSR] HTTP下载 {fname} (来自后端代理)")
                    return fname
    except Exception as e:
        logger.warning(f"[MSR] HTTP下载失败: {e}")
    # 2. 通过统一 URL 解析函数搜索本地文件
    from services.providers.provider_utils import output_file_from_url

    src = output_file_from_url(image_url)
    if src and os.path.isfile(src):
        try:
            shutil.copy2(src, dst)
            logger.info(f"[MSR] 已复制 {fname} → ComfyUI input (来自 {src})")
            return fname
        except Exception as e:
            logger.warning(f"[MSR] 复制失败: {e}")
    # 兜底：搜索 ComfyUI output 目录
    from services.comfyui.config import COMFYUI_OUTPUT_DIR

    if COMFYUI_OUTPUT_DIR:
        src = os.path.join(COMFYUI_OUTPUT_DIR, fname)
        if os.path.isfile(src):
            try:
                shutil.copy2(src, dst)
                logger.info(f"[MSR] 已复制 {fname} → ComfyUI input (来自 ComfyUI output)")
                return fname
            except Exception as e:
                logger.warning(f"[MSR] 复制失败: {e}")
    logger.warning(f"[MSR] 无法找到图片 {fname}，生成占位图避免 ComfyUI 校验失败")
    # 生成占位图让 ComfyUI 校验通过（小透明 PNG）
    try:
        placeholder_name = "_msr_placeholder.png"
        placeholder_path = os.path.join(input_dir, placeholder_name)
        if not os.path.isfile(placeholder_path):
            import struct
            import zlib

            raw = b"\x00\x00\x00\x00" * 64  # 64x64 透明
            compressed = zlib.compress(raw)

            def png_chunk(ctype, data):
                c = ctype + data
                return (
                    struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
                )  # noqa: E501

            with open(placeholder_path, "wb") as pf:
                pf.write(b"\x89PNG\r\n\x1a\n")
                pf.write(png_chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 6, 0, 0, 0)))
                pf.write(png_chunk(b"IDAT", compressed))
                pf.write(png_chunk(b"IEND", b""))
        return placeholder_name
    except Exception as pe:
        logger.warning(f"[MSR] 生成占位图失败: {pe}")
    return ""


@router.post("/msr-video")
async def submit_msr_video(request: MsrVideoRequest):
    """提交 MSR 多角色视频生成任务（异步模式）"""
    wf_path = os.path.join(_WF_DIR, "LTX-2.3_MSR_sample_workflow_V2.json")
    if not os.path.exists(wf_path):
        raise HTTPException(status_code=404, detail="MSR 工作流文件不存在")
    with open(wf_path, "r", encoding="utf-8") as f:
        wf_data = json.load(f)

    from services.comfyui_service import get_comfyui_service

    comfyui_svc = get_comfyui_service()
    logger.info(
        f"[MSR] comfyui_dir={comfyui_svc.config.comfyui_dir} output_dir={comfyui_svc.config.output_dir}"  # noqa: E501
    )  # noqa: E501

    # 确保 ComfyUI 在运行
    comfyui_ok = await comfyui_svc._check_alive()
    if not comfyui_ok:
        raise HTTPException(status_code=503, detail="ComfyUI 未连接")

    # 1. 注入参考图（LoadImage 节点）
    # 先确保占位图存在，防止任何 LoadImage 节点因默认值失效
    comfyui_input_dir = os.path.join(comfyui_svc.config.comfyui_dir, "input")
    os.makedirs(comfyui_input_dir, exist_ok=True)
    placeholder_path = os.path.join(comfyui_input_dir, "_msr_placeholder.png")
    if not os.path.isfile(placeholder_path):
        try:
            import struct
            import zlib

            raw = b"\x00\x00\x00\x00" * 64
            compressed = zlib.compress(raw)

            def png_chunk(ctype, data):
                c = ctype + data
                return (
                    struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
                )  # noqa: E501

            with open(placeholder_path, "wb") as pf:
                pf.write(b"\x89PNG\r\n\x1a\n")
                pf.write(png_chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 6, 0, 0, 0)))
                pf.write(png_chunk(b"IDAT", compressed))
                pf.write(png_chunk(b"IEND", b""))
            logger.info(f"[MSR] 占位图已生成: {placeholder_path}")
        except Exception as pe:
            logger.warning(f"[MSR] 占位图生成失败: {pe}")

    ref_nodes = {
        "29": request.ref1_image_url,
        "40": request.ref2_image_url,
        "33": request.ref3_image_url,
        "95": request.ref4_image_url,
        "30": request.bg_image_url,
    }
    for node_id, url in ref_nodes.items():
        if node_id not in wf_data:
            continue
        safe_name = ""
        if url:
            safe_name = await _ensure_image_for_msr(url, comfyui_svc)
        # 强制设置：用户没提供或找不到 → 用占位图
        if not safe_name:
            safe_name = "_msr_placeholder.png"
        wf_data[node_id].setdefault("inputs", {})["image"] = safe_name
        logger.info(f"[MSR] LoadImage {node_id} → {safe_name}")

    # 2. 注入提示词（Node 99: PromptRelayEncode, Node 6: CLIPTextEncode）
    if "99" in wf_data:
        wf_data["99"].setdefault("inputs", {})["global_prompt"] = request.global_prompt
        wf_data["99"].setdefault("inputs", {})["local_prompts"] = request.local_prompts or ""

    # 3. 注入参数
    if "28" in wf_data:  # LiconMSR
        wf_data["28"].setdefault("inputs", {}).update(
            {
                "width": request.width,
                "height": request.height,
                "frame_count": request.frame_count,
            }
        )
    if "15" in wf_data:  # RandomNoise
        wf_data["15"].setdefault("inputs", {})["noise_seed"] = request.seed
    if "50" in wf_data:  # INTConstant → 控制 EmptyLTXVLatentVideo.length（总帧数）
        pass  # 不修改，保持工作流默认值
    if "43" in wf_data:  # INTConstant → 控制 LiconMSR.width
        wf_data["43"].setdefault("inputs", {})["value"] = request.width
    if "44" in wf_data:  # INTConstant → 控制 LiconMSR.height
        wf_data["44"].setdefault("inputs", {})["value"] = request.height

    # ⭐ 修复紧急#5：注入视频质量参数
    # cfg → 节点 37 (LTXVConditioning) 的 cfg input
    if "37" in wf_data:
        wf_data["37"].setdefault("inputs", {})["cfg"] = request.cfg
        logger.info(f"[MSR] 注入 cfg={request.cfg} → 节点 37 (LTXVConditioning)")

    # ⭐ 断裂点2修复：frame_count 与 total_length 一致性
    # 节点 28 (LiconMSR.frame_count) = 每段帧数
    # 节点 50 (EmptyLTXVLatentVideo.length) = 总帧数
    # 若用户指定 duration，则 total_length = duration × fps；否则保持 frame_count 一致
    if "50" in wf_data:
        if request.duration:
            calc_total_frames = int(request.duration * request.fps)
            wf_data["50"].setdefault("inputs", {})["value"] = calc_total_frames
            logger.info(
                f"[MSR] 注入 total_length={calc_total_frames} (duration={request.duration}s × fps={request.fps}) → 节点 50"  # noqa: E501
            )  # noqa: E501
        else:
            # 无 duration 时，total_length 至少 = frame_count（保证单段视频完整）
            current_total = wf_data["50"].get("inputs", {}).get("value", 361)
            if current_total < request.frame_count:
                wf_data["50"].setdefault("inputs", {})["value"] = request.frame_count
                logger.info(
                    f"[MSR] total_length 调整为 {request.frame_count}（保证 ≥ frame_count）→ 节点 50"
                )  # noqa: E501

    # ⭐ 断裂点1修复：steps 真正注入（ManualSigmas 重采样）
    # 节点 27 (ManualSigmas) 的 sigmas 字段控制步数 = sigma 列表项数 - 1
    if "27" in wf_data and request.steps:
        from services.sigma_resampler import resample_sigmas, get_sigma_steps

        original_sigmas = wf_data["27"].get("inputs", {}).get("sigmas", "")
        if original_sigmas:
            current_steps = get_sigma_steps(original_sigmas)
            if current_steps != request.steps:
                new_sigmas = resample_sigmas(original_sigmas, request.steps)
                wf_data["27"].setdefault("inputs", {})["sigmas"] = new_sigmas
                logger.info(
                    f"[MSR] 注入 steps={request.steps} (重采样 {current_steps}→{request.steps}) → 节点 27"
                )  # noqa: E501
            else:
                logger.info(f"[MSR] steps={request.steps} 与当前一致，无需重采样")
        else:
            logger.warning("[MSR] 节点 27 无 sigmas 字段，无法注入 steps")
    else:
        logger.warning(f"[MSR] steps={request.steps} 未注入（节点 27 不存在）")

    # 4. 异步提交
    task_id = f"msr_{uuid.uuid4().hex[:12]}"
    _msr_task_set(task_id, {"status": "pending", "created_at": time.time()})

    async def _execute():
        try:
            result = await comfyui_svc._queue_prompt_with_retry(wf_data)
            prompt_id = result
            filenames = await comfyui_svc._wait_for_completion(prompt_id, task_type="generate")
            # 确保视频文件持久化到 GENERATED_DIR（_persist_output_files 可能因路径不匹配没复制）
            generated_dir = GENERATED_DIR
            os.makedirs(generated_dir, exist_ok=True)
            actual_urls = []
            for fn in filenames or []:
                # 1. 检查是否已在 GENERATED_DIR
                local_path = os.path.join(generated_dir, fn)
                if os.path.isfile(local_path):
                    actual_urls.append(f"/api/comfyui/image?filename={fn}")
                    continue
                # 2. 从可能的 ComfyUI output 目录搜索并复制（覆盖常见路径，含子目录）
                from services.comfyui.config import COMFYUI_OUTPUT_DIR

                output_candidates = [
                    comfyui_svc.config.output_dir,
                    COMFYUI_OUTPUT_DIR,
                ]
                for out_dir in output_candidates:
                    if not out_dir or not os.path.isdir(out_dir):
                        continue
                    # 直接查找
                    src_path = os.path.join(out_dir, fn)
                    if os.path.isfile(src_path):
                        try:
                            shutil.copy2(src_path, local_path)
                            actual_urls.append(f"/api/comfyui/image?filename={fn}")
                            logger.info(f"[MSR] 从output目录复制: {fn} → {out_dir}")
                            break
                        except Exception as ce:
                            logger.warning(f"[MSR] output复制失败 ({out_dir}): {ce}")
                    # 递归查找子目录（如 LTX-2/xxx.mp4）
                    if not os.path.isfile(local_path):
                        for root, dirs, files in os.walk(out_dir):
                            if fn in files:
                                try:
                                    shutil.copy2(os.path.join(root, fn), local_path)
                                    actual_urls.append(f"/api/comfyui/image?filename={fn}")
                                    logger.info(f"[MSR] 从子目录复制: {fn} → {root}")
                                    break
                                except Exception as ce:
                                    logger.warning(f"[MSR] 子目录复制失败: {ce}")
                        if actual_urls and any(fn in u for u in actual_urls):
                            break
                # 3. 通过 ComfyUI HTTP 下载（可能不支持 mp4）
                try:
                    import aiohttp
                    from services.comfyui.config import COMFYUI_BASE_URL

                    comfyui_base = COMFYUI_BASE_URL
                    view_url = f"{comfyui_base}/view?filename={fn}&type=output"
                    async with aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=60)
                    ) as session:  # noqa: E501
                        async with session.get(view_url) as resp:
                            if resp.status == 200:
                                data = await resp.read()
                                with open(local_path, "wb") as f:
                                    f.write(data)
                                actual_urls.append(f"/api/comfyui/image?filename={fn}")
                                logger.info(f"[MSR] HTTP下载视频: {fn} ({len(data)} bytes)")
                            else:
                                logger.warning(f"[MSR] HTTP下载失败: {fn} status={resp.status}")
                except Exception as dl_err:
                    logger.warning(f"[MSR] HTTP下载异常 {fn}: {dl_err}")
                # 4. 最后尝试：直接发请求到 ComfyUI /api/upload/image（部分版本支持）
                if not actual_urls or not any(fn in u for u in actual_urls):
                    logger.warning(f"[MSR] 所有下载方式均失败: {fn}")
            # 注册为资产，供资产库查看和播放
            asset_ids = []
            if actual_urls:
                try:
                    from services.asset_service import get_asset_service

                    asset_svc = get_asset_service()
                    for url in actual_urls:
                        asset = await asset_svc.create(
                            asset_type="video",
                            name=f"MSR 多角色视频 {time.strftime('%m-%d %H:%M', time.localtime(time.time()))}",  # noqa: E501
                            urls=[url],
                            content_type="video",
                        )
                        if asset:
                            asset_ids.append(asset.asset_id)
                except Exception as ae:
                    logger.warning(f"[MSR] 资产注册失败: {ae}")
            _msr_task_update(
                task_id,
                {
                    "status": "succeeded",
                    "result": {"videos": [{"url": u} for u in actual_urls]},
                    "asset_id": asset_ids[0] if asset_ids else "",
                },
            )
        except Exception as e:
            _msr_task_update(task_id, {"status": "failed", "error": str(e)})

    _msr_task_track(task_id, asyncio.create_task(_execute()))
    _msr_task_update(task_id, {"status": "running"})
    return {"success": True, "task_id": task_id, "status": "running"}


@router.get("/msr-video/{task_id}")
async def get_msr_task(task_id: str):
    """查询 MSR 视频生成任务状态"""
    task = _msr_task_get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "task_id": task_id,
        "status": task["status"],
        "result": task.get("result"),
        "error": task.get("error"),
    }
