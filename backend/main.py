#!/usr/bin/env python3
"""
导演工作台（Director's Workbench）— 后端入口

独立部署版本。仅包含导演工作台所需的路由和服务。
"""

from services.paths import GENERATED_DIR, UPLOADS_DIR, PIPELINES_DIR

import asyncio
import os
import platform
import sys

# 环境一致性守卫：声明支持的 Python 版本，避免旧版本环境静默运行导致行为漂移
if sys.version_info < (3, 13):
    raise RuntimeError(
        f"导演工作台要求 Python >= 3.13（当前 {platform.python_version()}）。"
        "请使用 backend/.venv-test 或安装 Python 3.13 后重建虚拟环境。"
    )

# Windows 中文编码修复
if platform.system() == "Windows":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUTF8"] = "1"
    import locale

    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass

from dotenv import load_dotenv

load_dotenv()

if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# 初始化结构化日志
from services.structured_logging import init_logging, get_logger  # noqa: E402

init_logging()

# 抑制 watchfiles 刷屏日志
import logging  # noqa: E402

logging.getLogger("watchfiles").setLevel(logging.WARNING)

logger = get_logger("main")

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期"""
    logger.info("[Director] 导演工作台启动")

    # 注册任务完成后的显存释放回调
    from services.gen_task_manager import get_gen_task_manager
    from services.comfyui_service import get_comfyui_service

    task_mgr = get_gen_task_manager()
    comfyui_svc = get_comfyui_service()

    async def _on_task_done(task):
        """任务完成后释放显存缓存（不卸载模型，避免影响并发任务）"""
        try:
            # 仅释放缓存，不卸载模型
            # 模型卸载由空闲自停机制（30分钟无活动）处理
            await comfyui_svc._quick_release_vram(unload_models=False)
        except Exception as e:
            logger.debug(f"[Director] 任务完成后显存释放失败: {e}")

    task_mgr.register_done_callback(_on_task_done)

    # 启动输出文件定期清理
    await comfyui_svc.start_output_cleanup_task(interval_hours=6)

    yield

    # 关闭清理任务
    await comfyui_svc.stop_output_cleanup_task()
    # 取消存活的 MSR 后台任务（fire-and-forget 治理：避免关闭时残留任务继续写库）
    from api.canvas_api import shutdown_msr_tasks

    await shutdown_msr_tasks()
    logger.info("[Director] 导演工作台关闭")


app = FastAPI(
    title="导演工作台 (Director's Workbench)",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
# 注意：allow_origins=["*"] 与 allow_credentials=True 不能同时使用，
# 浏览器会拒绝携带凭证的跨域请求。这里通过环境变量配置允许的来源，
# 默认允许本机开发端口；如需生产部署，设置 CORS_ALLOWED_ORIGINS 环境变量。
import os as _os  # noqa: E402

_default_origins = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
]
_cors_env = _os.getenv("CORS_ALLOWED_ORIGINS", "")
if _cors_env:
    _default_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_default_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# WebSocket 端点（实时进度推送）
# ============================================================
from services.ws_service import handle_batch_ws  # noqa: E402


@app.websocket("/api/ws/batches/{batch_id}")
async def batch_ws_endpoint(websocket, batch_id: str):
    """批量任务实时进度 WebSocket

    客户端连接后自动接收该 batch_id 的事件：
    - batch_started / batch_completed / batch_failed
    - step_started / step_completed / step_failed / step_skipped
    - progress（completed/total/percent）

    客户端可发送 "ping" 保持连接，服务端回复 "pong"。
    """
    await handle_batch_ws(websocket, batch_id)


# ============================================================
# 画布实时变更（WebSocket）
# ============================================================


@app.websocket("/api/ws/canvas")
async def canvas_ws_endpoint(websocket: WebSocket, canvas_id: str = ""):
    """画布实时变更推送 WebSocket

    客户端连接后订阅 pipeline 频道，实时接收画布变更事件：
      {"type": "canvas_update", "canvas_id": "...", "action": "...", "data": {...}}
    事件由 CanvasService._broadcast() 触发。
    可发送 "ping" 保活，服务端回复 "pong"。
    """
    import uuid
    from core.ws_manager import get_ws_manager as get_canvas_ws

    manager = get_canvas_ws()
    conn_id = f"canvas_{uuid.uuid4().hex[:12]}"
    try:
        await manager.accept(websocket, conn_id, channel="pipeline")
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text('{"type": "pong"}')
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        manager.disconnect(conn_id)


# ============================================================
# 路由注册
# ============================================================

from api.director_asset_api import router as director_asset_router  # noqa: E402
from api.director_stage_api import router as director_stage_router  # noqa: E402
from api.director_provider_api import router as director_provider_router  # noqa: E402
from api.canvas_api import router as canvas_router  # noqa: E402
from api.infinite_canvas_api import router as infinite_canvas_router  # noqa: E402
from api.project_api import router as project_router  # noqa: E402
from api.batch_api import router as batch_router  # noqa: E402
from api.workflow_template_api import router as workflow_template_router  # noqa: E402
from api.preset_api import router as preset_router  # noqa: E402
from api.prompt_api import router as prompt_router  # noqa: E402
from api.contract_api import router as contract_router  # noqa: E402
from api.system_api import router as system_router  # noqa: E402

_routers = [
    director_asset_router,
    director_stage_router,
    director_provider_router,
    canvas_router,
    infinite_canvas_router,
    project_router,
    batch_router,
    workflow_template_router,
    preset_router,
    prompt_router,
    contract_router,
    system_router,
]
for r in _routers:
    app.include_router(r)
    logger.info(f"[Director] 路由注册: {r.prefix}")

# ============================================================
# ComfyUI 图片代理端点
# ============================================================
# 持久化生成图片目录（不受 ComfyUI output 清理影响）
# GENERATED_DIR 由 services.paths 提供（T7 收敛）
os.makedirs(GENERATED_DIR, exist_ok=True)
logger.info(f"[Director] 持久化图片目录: {GENERATED_DIR}")


@app.get("/api/comfyui/image")
async def serve_comfyui_image(filename: str = "", pipeline_id: str = "", subfolder: str = ""):
    """从 ComfyUI output / 持久化目录 提供图片文件

    subfolder: 持久化目录下的相对子目录（如 {project}/{stage}），用于分层资产访问。
    """
    if not filename:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="filename 参数必填")
    # 安全检查：防止路径遍历
    safe_name = os.path.basename(filename)
    if safe_name != filename or ".." in filename:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="非法文件名")
    # subfolder 同样做路径遍历防护
    safe_sub = ""
    if subfolder:
        safe_sub = subfolder.replace("\\", "/").strip("/")
        if ".." in safe_sub or safe_sub.startswith("/"):
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail="非法子目录")
    # 搜索路径（按优先级）
    from services.comfyui_service import COMFYUI_DIR

    _upload_dir = UPLOADS_DIR
    search_dirs = [
        (
            os.path.join(GENERATED_DIR, safe_sub) if safe_sub else GENERATED_DIR
        ),  # 1. 持久化目录（优先）
        _upload_dir,  # 2. 上传目录（含 canvas 上传）
        os.path.join(COMFYUI_DIR, "output") if COMFYUI_DIR else "",  # 3. ComfyUI output
    ]
    if pipeline_id:
        search_dirs.append(os.path.join(PIPELINES_DIR, pipeline_id))
    for d in search_dirs:
        if not d:
            continue
        fpath = os.path.join(d, safe_name)
        if os.path.isfile(fpath):
            # 根据扩展名自动判断媒体类型
            ext = os.path.splitext(safe_name)[1].lower()
            media_map = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".gif": "image/gif",
                ".bmp": "image/bmp",
                ".mp4": "video/mp4",
                ".webm": "video/webm",
                ".mov": "video/quicktime",
            }
            return FileResponse(fpath, media_type=media_map.get(ext, "application/octet-stream"))
    # 兼容旧 URL：持久化目录递归查找（存量资产迁移到子目录后仍可访问）
    if not safe_sub:
        for root, _dirs, files in os.walk(GENERATED_DIR):
            if safe_name in files:
                fpath = os.path.join(root, safe_name)
                ext = os.path.splitext(safe_name)[1].lower()
                media_map = {
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".webp": "image/webp",
                    ".gif": "image/gif",
                    ".bmp": "image/bmp",
                    ".mp4": "video/mp4",
                    ".webm": "video/webm",
                    ".mov": "video/quicktime",
                }
                return FileResponse(
                    fpath, media_type=media_map.get(ext, "application/octet-stream")
                )  # noqa: E501

    # 4. 回退：通过 HTTP 从 ComfyUI /view 端点代理拉取（适用于远程 ComfyUI）
    import aiohttp
    from fastapi.responses import Response
    from services.comfyui.config import COMFYUI_BASE_URL

    comfyui_base = COMFYUI_BASE_URL
    view_url = f"{comfyui_base}/view?filename={safe_name}&type=output"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(view_url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    # 同步写入持久化目录，下次直接命中
                    try:
                        os.makedirs(GENERATED_DIR, exist_ok=True)
                        with open(os.path.join(GENERATED_DIR, safe_name), "wb") as f:
                            f.write(data)
                    except Exception as e:
                        logger.warning(f"[ImageProxy] 持久化写入失败: {e}")
                    ext = os.path.splitext(safe_name)[1].lower()
                    media_map = {
                        ".png": "image/png",
                        ".jpg": "image/jpeg",
                        ".jpeg": "image/jpeg",
                        ".webp": "image/webp",
                        ".gif": "image/gif",
                        ".bmp": "image/bmp",
                        ".mp4": "video/mp4",
                        ".webm": "video/webm",
                        ".mov": "video/quicktime",
                    }
                    return Response(
                        content=data, media_type=media_map.get(ext, "application/octet-stream")
                    )  # noqa: E501
    except Exception as e:
        logger.warning(f"[ImageProxy] ComfyUI /view 代理失败: {e}")

    from fastapi import HTTPException

    raise HTTPException(status_code=404, detail=f"文件不存在: {safe_name}")


# ============================================================
# 静态文件服务
# ============================================================

# 上传文件静态服务（更具体的路径必须先挂载，否则被 /static/director 拦截）
_upload_dir = UPLOADS_DIR
os.makedirs(_upload_dir, exist_ok=True)
app.mount("/static/director/uploads", StaticFiles(directory=_upload_dir), name="uploads_static")
logger.info(f"[Director] 上传目录: {_upload_dir}")

_director_static = os.path.join(os.path.dirname(__file__), "static", "director")
os.makedirs(_director_static, exist_ok=True)
app.mount("/static/director", StaticFiles(directory=_director_static), name="director_static")
logger.info(f"[Director] 静态文件: {_director_static}")

# 兼容旧版路径：canvas.css 引用 /static/vendor/ 等
_static_root = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(_static_root, exist_ok=True)
app.mount("/static", StaticFiles(directory=_static_root), name="static_root")
logger.info(f"[Director] 静态根目录: {_static_root}")

# canvas.js 引用 /assets/uploads 路径，映射到上传目录
_upload_dir_for_assets = UPLOADS_DIR
os.makedirs(_upload_dir_for_assets, exist_ok=True)
app.mount("/assets/uploads", StaticFiles(directory=_upload_dir_for_assets), name="assets_uploads")

# 生成产物静态服务（/output/ → backend/output/）
from services.paths import OUTPUT_DIR as _GEN_OUTPUT_DIR  # noqa: E402

for _cat in ("output", "script", "graphic", "temp"):
    _cat_dir = os.path.join(_GEN_OUTPUT_DIR, _cat)
    os.makedirs(_cat_dir, exist_ok=True)
app.mount("/output", StaticFiles(directory=_GEN_OUTPUT_DIR), name="output_static")
logger.info(f"[Director] 生成产物目录: {_GEN_OUTPUT_DIR}")

# ============================================================
# 健康检查
# ============================================================


@app.get("/health")
async def health():
    return {"status": "ok", "service": "director-workbench", "version": "1.0.0"}


# ============================================================
# React 前端（frontend-director 构建产物）
# ============================================================
# 挂载 React 前端到根路径，启用 PresetsPage/PromptsPage/WorkflowTemplatesPage 等页面
# 注意：必须在所有 API 路由和 /static、/output 等静态路径之后挂载，避免被根路径拦截
_react_dist = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend-director", "dist")
if os.path.isdir(_react_dist):

    # 挂载 React 静态资源（/assets/ → frontend-director/dist/assets/）
    _react_assets = os.path.join(_react_dist, "assets")
    if os.path.isdir(_react_assets):
        app.mount("/assets", StaticFiles(directory=_react_assets), name="react_assets")
        logger.info(f"[Director] React 静态资源: {_react_assets}")

    # 根路径及所有未匹配的非 API 路径返回 React index.html（SPA 路由）
    @app.get("/{full_path:path}")
    async def react_spa(full_path: str):
        # 排除 API、静态文件、输出文件等路径
        if full_path.startswith(("api/", "static/", "output/", "assets/")) or full_path in (
            "health",
            "favicon.ico",
        ):
            raise HTTPException(status_code=404, detail="Not Found")
        index_path = os.path.join(_react_dist, "index.html")
        if os.path.isfile(index_path):
            return FileResponse(index_path)
        raise HTTPException(status_code=404, detail="React 前端未构建")

    logger.info(f"[Director] React 前端已挂载: {_react_dist}")
else:
    logger.warning(
        f"[Director] React 前端构建产物不存在: {_react_dist}（请运行 cd frontend-director && npm run build）"
    )  # noqa: E501


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("DIRECTOR_PORT", "8000"))
    logger.info(f"[Director] 启动服务 | port={port}")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=True,
        reload_dirs=["api", "services"],
        log_level="warning",
    )  # noqa: E501
