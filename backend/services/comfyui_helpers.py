"""
ComfyUI 服务 — 共享工具与常量

模块级常量、内存/显存监控、参考图分析、视觉缓存、
工作流输入处理工具函数与数据类（ComfyUIConfig / ComfyUIGenResult / StoryboardStepResult）。
"""
from services.paths import GENERATED_DIR  # noqa: F401（再导出兼容）



"""
ComfyUI 服务
通过 HTTP API 调用本地 ComfyUI 节点生成图像
支持自动启动 + 连接重试
支持 Z-Image 瑶光版（文生图）和 Qwen Image Edit（图生图）
"""

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable, Awaitable, Tuple
from dataclasses import dataclass

import aiohttp

from services.workflow_builder import (
    build_comfyui_workflow,
    build_refinement_workflow,
    build_standardization_workflow,
    build_scene_multiangle_workflow,
    STORYBOARD_TEMPLATES,
    structured_prompt_to_comfyui_prompt,
    _resolve_comfyui_image,
)
from services.qwen_workflow import YAOGUANG_DEFAULT_NEGATIVE

logger = logging.getLogger(__name__)


def _get_ram_pct_safe() -> float:
    """安全获取系统内存使用百分比，失败返回 -1"""
    try:
        import psutil
        return psutil.virtual_memory().percent
    except Exception:
        return -1.0


def _crop_turnaround_to_front_view(
    input_dir: str,
    filename: str,
    trace_id: str = "",
) -> Optional[str]:
    """将三视图参考图裁剪为只保留正视图面板。

    典型三视图是3个面板水平并排（宽:高 ≈ 3:1）。
    裁剪中间1/3（通常是正面视图），保存为 {name}_single.png。

    Args:
        input_dir: ComfyUI input 目录的绝对路径
        filename: 原始文件名
        trace_id: 日志标识

    Returns:
        裁剪后的文件名，若裁剪失败返回 None
    """
    from PIL import Image

    input_path = os.path.join(input_dir, filename)
    if not os.path.exists(input_path):
        logger.warning(f"[TurnaroundCrop] [{trace_id}] 文件不存在: {input_path}")
        return None

    try:
        with Image.open(input_path) as img:
            img.load()
            w, h = img.size
            aspect = w / h if h > 0 else 0

            if aspect >= 2.5:
                # 水平三面板: 裁中间1/3
                left, right = w // 3, 2 * w // 3
                top, bottom = 0, h
                logger.info(
                    f"[TurnaroundCrop] [{trace_id}] 水平三视图 {w}x{h} → "
                    f"裁剪中间 [{left}:{right}, 0:{h}]"
                )
            elif aspect <= 1.0 / 2.5:
                # 垂直三面板: 裁中间1/3
                left, right = 0, w
                top, bottom = h // 3, 2 * h // 3
                logger.info(
                    f"[TurnaroundCrop] [{trace_id}] 垂直三视图 {w}x{h} → "
                    f"裁剪中间 [0:{w}, {top}:{bottom}]"
                )
            else:
                # 非标准比例: 保守处理，水平裁剪中间1/3
                left, right = w // 3, 2 * w // 3
                top, bottom = 0, h
                logger.info(
                    f"[TurnaroundCrop] [{trace_id}] 非标准宽高比 {aspect:.1f} "
                    f"{w}x{h} → 尝试水平居中裁剪"
                )

            cropped = img.crop((left, top, right, bottom))

        # ⭐ 缩放到工作流期望的尺寸（避免裁剪后尺寸过小导致生成质量下降）
        _TARGET_SIZE = 1024  # 工作流期望的长边尺寸
        cw, ch = cropped.size
        max_side = max(cw, ch)
        if max_side < _TARGET_SIZE * 0.8:  # 仅在尺寸明显偏小时缩放
            scale = _TARGET_SIZE / max_side
            new_w, new_h = int(cw * scale), int(ch * scale)
            # 确保尺寸为8的倍数（ComfyUI要求）
            new_w = (new_w // 8) * 8
            new_h = (new_h // 8) * 8
            cropped = cropped.resize((new_w, new_h), Image.LANCZOS)
            logger.info(
                f"[TurnaroundCrop] [{trace_id}] 缩放: {cw}x{ch} → {new_w}x{new_h}"
            )

        # 保存裁剪结果（在 with 块外，避免写入已关闭的图片）
        name, _ext = os.path.splitext(filename)
        cropped_fn = f"{name}_single.png"
        cropped_path = os.path.join(input_dir, cropped_fn)
        cropped.save(cropped_path, "PNG")

        logger.info(
            f"[TurnaroundCrop] [{trace_id}] 裁剪完成: {filename} → "
            f"{cropped_fn} ({cropped.width}x{cropped.height})"
        )
        # ⭐ Fix 5: 显式释放裁剪后的 PIL 对象（约 10-30MB）
        del cropped
        return cropped_fn

    except Exception as e:
        logger.error(f"[TurnaroundCrop] [{trace_id}] 裁剪失败: {filename} - {e}")
        return None


async def _analyze_reference_images(
    all_ref_items: List[Dict[str, Any]],
    backend_port: int = 18080,
    project_id: Optional[str] = None,
    progress_callback: Optional[Callable] = None,
) -> None:
    """用 VisionService 并行分析参考图的真实视觉内容，将描述写入 item['visual_desc']。

    Args:
        all_ref_items: 参考图条目列表（会被原地修改，添加 visual_desc 字段）
        backend_port: 后端 HTTP 服务端口，用于构造 llama.cpp 可访问的图片 URL
        project_id: 项目 ID，用于在项目目录中查找图片（优先于 ComfyUI output 目录）
        progress_callback: 进度回调，签名 cb(pct_or_msg, elapsed_sec)
    """
    if not all_ref_items:
        return

    _analyze_start = time.time()
    _ram_before = _get_ram_pct_safe()
    logger.info(
        f"[VisionAnalyze] 开始视觉分析 | refs={len(all_ref_items)} | project={project_id[:20] if project_id else 'N/A'} | RAM_before={_ram_before:.1f}%"
    )

    if progress_callback:
        try:
            progress_callback("🔍 正在准备分析参考图视觉内容...", 0)
        except Exception:
            pass

    # 过滤出有有效 URL 的条目，并构造 vision URL
    # 注意：llama.cpp 默认不支持 file:// 协议，必须使用 HTTP URL
    # ⭐ pipeline_id 参数让图片代理端点优先从项目目录读取，避免依赖 ComfyUI output 目录
    items_to_analyze: List[Dict[str, Any]] = []
    for item in all_ref_items:
        img_url = item.get("image_url") or item.get("url", "") or ""
        if not img_url:
            continue
        
        # 构造 HTTP URL（llama.cpp 需要可访问的完整 URL）
        if img_url.startswith("/"):
            base = f"http://127.0.0.1:{backend_port}{img_url}"
        elif img_url.startswith(("http://", "https://")):
            base = img_url
        else:
            # 裸文件名：使用 ComfyUI 图片代理端点
            base = f"http://127.0.0.1:{backend_port}/api/comfyui/image?filename={img_url}"

        # 追加 pipeline_id 参数，让图片端点优先搜索项目目录
        vision_url = base
        if project_id:
            sep = "&" if "?" in base else "?"
            vision_url = f"{base}{sep}pipeline_id={project_id}"

        item["_vision_url"] = vision_url
        logger.info(f"[VisionAnalyze] 图片路径: {vision_url[:120]}")
        items_to_analyze.append(item)

    if not items_to_analyze:
        return

    total = len(items_to_analyze)
    completed = [0]  # 用 list 包装以在闭包中修改

    try:
        from services.vision_service import get_vision_service
        vsvc = get_vision_service()

        async def _describe_one(item: Dict[str, Any]) -> None:
            vision_url = item.get("_vision_url", "")
            content_type = item.get("type", "character")
            try:
                visual_desc = await vsvc.describe(vision_url, asset_type=content_type)
                if visual_desc:
                    item["visual_desc"] = visual_desc
                    logger.info(
                        f"[ComfyUI] 参考图视觉分析完成 ({content_type}): {visual_desc[:80]}..."
                    )
                else:
                    logger.info(
                        f"[ComfyUI] 参考图视觉分析完成 ({content_type}): 无有效描述"
                    )
            except Exception as e:
                logger.warning(
                    f"[ComfyUI] 单张参考图视觉分析失败 ({content_type}, {vision_url[:60]}): {e}"
                )
            finally:
                item.pop("_vision_url", None)  # 清理临时字段
                completed[0] += 1
                if progress_callback:
                    try:
                        pct = int(completed[0] / total * 45)  # 0~45% 留给分析阶段
                        progress_callback(
                            f"🔍 参考图视觉分析: {completed[0]}/{total} ({asset_type})",
                            pct,
                        )
                    except Exception:
                        pass

        # 并行分析所有参考图（缩短总等待时间）
        await asyncio.gather(*[_describe_one(item) for item in items_to_analyze])

        _elapsed = time.time() - _analyze_start
        _ram_after = _get_ram_pct_safe()
        _success_count = sum(1 for item in items_to_analyze if item.get("visual_desc"))
        logger.info(
            f"[VisionAnalyze] 视觉分析完成 | analyzed={_success_count}/{total}"
            f" | elapsed={_elapsed:.1f}s | RAM={_ram_after:.1f}% (Δ={_ram_after - _ram_before:+.1f}%)"
        )

        if progress_callback:
            try:
                progress_callback(
                    f"✅ 参考图分析完成 ({completed[0]}/{total})，准备生成...",
                    45,
                )
            except Exception:
                pass
    except Exception as e:
        _elapsed = time.time() - _analyze_start
        logger.warning(
            f"[VisionAnalyze] 参考图视觉分析失败 | elapsed={_elapsed:.1f}s | error={e}"
        )
        for item in items_to_analyze:
            item.pop("_vision_url", None)
        if progress_callback:
            try:
                progress_callback(f"⚠️ 参考图分析部分失败，继续生成...", 30)
            except Exception:
                pass

# ═══════════════════════════════════════════════════════════════════
# Vision 分析缓存持久化（崩溃恢复 + 避免重复分析）
# ═══════════════════════════════════════════════════════════════════

VISION_CACHE_FILENAME = "vision_analysis_cache.json"


def _get_vision_cache_path(project_id: str) -> Optional[str]:
    """获取项目目录下的 Vision 分析缓存文件路径"""
    if not project_id or project_id == "unknown":
        return None
    try:
        from services.pipeline_manager import get_pipeline_manager
        mgr = get_pipeline_manager()
        project = mgr.get_project(project_id)
        if project:
            folder_path = project.get("folder_path", "")
            if folder_path and os.path.isdir(folder_path):
                return os.path.join(folder_path, VISION_CACHE_FILENAME)
    except Exception as e:
        logger.warning(f"[VisionCache] 获取缓存路径失败: {e}")
    return None


def _load_vision_cache(project_id: str) -> Optional[Dict[str, str]]:
    """从项目目录加载之前的 Vision 分析结果

    Returns:
        {img_url: visual_desc} 字典，如果缓存不存在或无效则返回 None
    """
    cache_path = _get_vision_cache_path(project_id)
    if not cache_path or not os.path.isfile(cache_path):
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cache = json.load(f)
        if isinstance(cache, dict) and cache:
            logger.info(
                f"[VisionCache] 加载缓存成功 | project={project_id[:20]}"
                f" | entries={len(cache)}"
            )
            return cache
    except Exception as e:
        logger.warning(f"[VisionCache] 加载缓存失败: {e}")
    return None


def _save_vision_cache(project_id: str, items: List[Dict[str, Any]]) -> bool:
    """将 Vision 分析结果保存到项目目录 JSON

    Args:
        project_id: 项目ID
        items: 已分析的参考图条目（包含 image_url/url + visual_desc）

    Returns:
        True 如果保存成功
    """
    cache_path = _get_vision_cache_path(project_id)
    if not cache_path:
        return False
    try:
        cache: Dict[str, str] = {}
        for item in items:
            img_url = item.get("image_url") or item.get("url", "") or ""
            visual_desc = item.get("visual_desc", "")
            if img_url and visual_desc:
                cache[img_url] = visual_desc

        if not cache:
            logger.info("[VisionCache] 无有效分析结果，跳过保存")
            return False

        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

        logger.info(
            f"[VisionCache] 保存缓存成功 | project={project_id[:20]}"
            f" | entries={len(cache)}"
        )
        return True
    except Exception as e:
        logger.warning(f"[VisionCache] 保存缓存失败: {e}")
        return False


def _apply_vision_cache(
    items: List[Dict[str, Any]],
    cache: Dict[str, str],
) -> int:
    """将缓存的 visual_desc 应用到参考图条目上

    Args:
        items: 参考图条目列表（原地修改）
        cache: {img_url: visual_desc} 缓存

    Returns:
        成功应用缓存的条目数
    """
    applied = 0
    for item in items:
        if item.get("visual_desc"):
            continue  # 已有描述，跳过
        img_url = item.get("image_url") or item.get("url", "") or ""
        cached_desc = cache.get(img_url, "")
        if cached_desc:
            item["visual_desc"] = cached_desc
            applied += 1
            logger.info(
                f"[VisionCache] 命中缓存: {img_url[:60]}"
                f" → {cached_desc[:50]}..."
            )
    if applied:
        logger.info(f"[VisionCache] 应用缓存: {applied}/{len(items)} 条目命中")
    return applied


def _collect_all_reference_urls(
    reference_items: List[Dict[str, Any]],
    shots: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """收集所有需要分析的参考图（去重）。

    Args:
        reference_items: 共享参考图列表（所有分镜共用）
        shots: 分镜列表（每个分镜可能有独立参考图），可选

    Returns:
        去重后的参考图条目列表（按 image_url 去重）
    """
    seen: set = set()
    unique: List[Dict[str, Any]] = []

    def _add(item: Dict[str, Any]):
        img_url = item.get("image_url") or item.get("url", "") or ""
        if not img_url or img_url in seen:
            return
        seen.add(img_url)
        unique.append(dict(item))

    for item in (reference_items or []):
        _add(item)

    if shots:
        for shot in shots:
            shot_refs = shot.get("reference_items") or shot.get("references") or []
            for item in shot_refs:
                _add(item)

    logger.info(
        f"[VisionPreAnalyze] 收集参考图: {len(reference_items or [])} 共享 + "
        f"{len(shots or [])} 分镜 → {len(unique)} 唯一参考图"
    )
    return unique


# ============================================================
# 配置
# ============================================================

from services.comfyui.config import COMFYUI_DIR, COMFYUI_BASE_URL as _COMFYUI_BASE_URL

COMFYUI_BASE_URL = _COMFYUI_BASE_URL
POLL_INTERVAL = 0.5

MAX_POLL_TIME = 600  # 默认 10 分钟
# 按任务类型的超时时间（秒）
TASK_TIMEOUTS = {
    'generate': 1800,
    'refine': 600,
    'standardize_3': 600,
    'standardize_6': 1200,
    'storyboard': 900,
    'yaoguang': 180,
    'video': 1800,  # LTX-2.3 视频生成 30 分钟
    'tts': 300,  # TTS 音频生成 5 分钟
}

# ComfyUI 自动启动配置（从 comfyui.config 复用，单一来源）
# ⭐ 修复 P3：原代码重复调用 _detect_comfyui_dir() 和 os.environ.get("COMFYUI_PYTHON")
# 改为直接从 services.comfyui.config 引用模块级常量，避免配置不一致风险
from services.comfyui.config import COMFYUI_DIR, COMFYUI_PYTHON
COMFYUI_SCRIPT = "main.py"
COMFYUI_START_TIMEOUT = 60  # 秒

# 内存/显存监控配置（从 comfyui.config 复用，单一来源）
from services.comfyui.config import (
    MEMORY_HIGH_THRESHOLD,
    VRAM_HIGH_THRESHOLD,
    MEMORY_CHECK_INTERVAL,
)
MAX_CACHE_SIZE = int(os.environ.get("COMFYUI_CACHE_SIZE", 10))  # 最大缓存图片数（⭐ V2: 降至10，因已移除内存缓存，此值仅作安全上限）

# 进程管理开关：线上部署设为 true，由 Supervisor/Systemd 管理 ComfyUI 进程
DISABLE_PROCESS_MANAGEMENT = os.environ.get("DISABLE_PROCESS_MANAGEMENT", "false").lower() in ("true", "1", "yes")

# 持久化生成图片目录（不受 ComfyUI output 清理影响）
# GENERATED_DIR 由 services.paths 提供（T7 收敛）


def _mem_log(label: str, context: str = "") -> float:
    """⭐ 核心内存/显存追踪日志 — 仅在关键步骤打印，格式统一便于 grep
    
    输出格式: [MEM] 标签 | RAM=xx% | VRAM=xx% | Python=xxxMB | 子进程=xxxMB | 上下文
    """
    import psutil
    ram_pct = psutil.virtual_memory().percent
    # Python 进程自身内存
    proc = psutil.Process()
    proc_mem_mb = proc.memory_info().rss / 1024 / 1024
    # 子进程内存（ComfyUI、llama.cpp 等）
    children_mb = 0
    try:
        for child in proc.children(recursive=True):
            try:
                children_mb += child.memory_info().rss / 1024 / 1024
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception:
        pass
    # ⭐ 显存占用（通过 nvidia-smi 获取，失败则显示 N/A）
    vram_pct_str = "N/A"
    try:
        import subprocess as _sp
        _vram_result = _sp.run(
            ['nvidia-smi', '--query-gpu=memory.used,memory.total', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=3,
        )
        if _vram_result.returncode == 0 and _vram_result.stdout.strip():
            _parts = _vram_result.stdout.strip().split(',')
            if len(_parts) == 2:
                _used = int(_parts[0].strip())
                _total = int(_parts[1].strip())
                _vram_pct = (_used / _total * 100) if _total > 0 else 0
                vram_pct_str = f"{_vram_pct:.0f}%({_used}/{_total}MB)"
    except Exception:
        pass
    ctx = f" | {context}" if context else ""
    logger.info(
        f"[MEM] {label} | RAM={ram_pct:.1f}% | VRAM={vram_pct_str}"
        f" | Python={proc_mem_mb:.0f}MB | 子进程={children_mb:.0f}MB{ctx}"
    )
    return ram_pct


@dataclass
class ComfyUIConfig:
    """ComfyUI 服务配置"""
    base_url: str = COMFYUI_BASE_URL
    comfyui_dir: str = COMFYUI_DIR or os.path.expanduser("~/ComfyUI")
    output_dir: str = ""
    timeout: int = 300
    default_width: int = 1080
    default_height: int = 1920
    default_steps: int = 25
    default_cfg: float = 2.0

    def __post_init__(self):
        if not self.output_dir:
            self.output_dir = os.path.join(self.comfyui_dir, "output")


@dataclass
@dataclass
class ComfyUIGenResult:
    """生成结果"""
    image_url: str
    filename: str
    images: List[str] = None  # 多图片输出时的所有图片 URL（场景多角度专用）
    filenames: List[str] = None  # 多图片输出时的所有文件名
    prompt_id: str = ""
    elapsed_ms: int = 0
    seed: int = 0
    prompt: Optional[str] = None
    prompt_sections: Optional[Dict[str, str]] = None
    frame_prompts: List[str] = None  # 每帧独立提示词（场景多角度专用）
    ref_items: Optional[List[Dict[str, str]]] = None  # 参考图列表（含 visual_desc，分镜专用）

    def __post_init__(self):
        if self.images is None:
            self.images = [self.image_url] if self.image_url else []
        if self.filenames is None:
            self.filenames = [self.filename] if self.filename else []

    def get(self, key: str, default: Any = None) -> Any:
        """dict-like 兼容访问，方便 API 层提取字段"""
        return getattr(self, key, default)


# ⭐ V1.3: 分镜步骤结果数据类
@dataclass
class StoryboardStepResult:
    """单个融合步骤的结果"""
    step_index: int
    step_name: str
    filename: str
    elapsed_ms: int
    error: Optional[str] = None


# ⭐ V5.0: Fish 融合 1 步进度分配（总范围 50~100%）
STORYBOARD_PROGRESS_MAP = {
    "visual_analysis_start": 0,
    "visual_analysis_end": 45,
    # 1步 Fish 融合
    "1step_fusion": (50, 100),
}


def _update_workflow_input(wf: Dict[str, Any], current_image: str, task_id: str = "") -> Dict[str, Any]:
    """将前一步产物注入到下一步工作流的输入节点中。
    
    优先覆盖节点 11（Fish 融合场景槽位），如不存在则扫描所有 LoadImage
    节点并将第一个设为 current_image。
    current_image 是上一步 ComfyUI 输出的文件名。
    
    ⭐ 关键：必须将文件从 output 目录复制到 input 目录，
    因为 ComfyUI LoadImage 节点只从 input 目录加载图片。
    
    ⭐ 竞态修复：复制时添加 task_id 前缀避免并发任务覆盖同名文件。
    工作流中使用带前缀的唯一文件名，避免竞态冲突。
    """
    # 提取纯文件名（去除可能的路径）
    current_image = os.path.basename(current_image)
    logger.info(f"[V2] _update_workflow_input: current_image={current_image}, task_id={task_id}")
    
    # ⭐ 竞态修复：如果提供了 task_id，为文件名添加唯一前缀避免并发覆盖
    if task_id and current_image:
        name, ext = os.path.splitext(current_image)
        unique_image = f"{name}_{task_id[:8]}{ext}"
    else:
        unique_image = current_image
    
    # ⭐ 确保文件在 ComfyUI input 目录中（LoadImage 节点只读 input 目录）
    output_dir = os.path.join(COMFYUI_DIR, "output")
    input_dir = os.path.join(COMFYUI_DIR, "input")
    output_path = os.path.join(output_dir, current_image)
    input_path = os.path.join(input_dir, unique_image)
    
    if not os.path.exists(input_path):
        if os.path.exists(output_path):
            try:
                os.makedirs(input_dir, exist_ok=True)
                shutil.copy2(output_path, input_path)
                logger.info(
                    f"[V2] 复制图片 output→input: {current_image} → {unique_image}"
                    f" | {os.path.getsize(output_path)} bytes"
                )
            except Exception as copy_err:
                logger.warning(
                    f"[V2] 复制图片失败(output→input): {current_image}"
                    f" | error={copy_err} | 尝试用 HTTP fallback"
                )
        else:
            logger.warning(
                f"[V2] 图片不在 output 目录: {output_path}"
                f" | 可能已被清理或来自其他来源"
            )
    else:
        logger.info(f"[V2] 图片已在 input 目录: {unique_image}")
    
    # ⭐ 使用唯一文件名替代原始文件名，避免并发竞态
    current_image = unique_image
    
    # 优先使用 Fish 融合模板的第二个 LoadImage 节点（场景槽位）
    from services.workflow_builder import find_node_by_class_type
    load_nodes = find_node_by_class_type(wf, 'LoadImage')
    load_nodes.sort(key=lambda x: x[0])
    
    if len(load_nodes) >= 2:
        # 第二个 LoadImage 节点 = 场景槽位
        wf[load_nodes[1][0]]['inputs']['image'] = current_image
        logger.info(f"[V2] 已更新节点{load_nodes[1][0]}图片: {current_image}")
    elif len(load_nodes) >= 1:
        wf[load_nodes[0][0]]['inputs']['image'] = current_image
        logger.info(f"[V2] _update_workflow_input 回退到节点 {load_nodes[0][0]}")
    else:
        logger.warning("[V2] _update_workflow_input 未找到任何 LoadImage 节点，工作流可能异常")
        return wf
    
    # ⭐ 修复其他 LoadImage 节点中未解析的占位符值
    # Fish 融合模板中第一个和第三个 LoadImage 可能包含占位符 "stepN_output"
    for nid, ndata in load_nodes:
        if nid == load_nodes[1][0] if len(load_nodes) >= 2 else load_nodes[0][0]:
            continue  # 跳过已更新的节点
        val = ndata['inputs'].get('image', '')
        # 检测未解析的占位符：不含图片扩展名 或 包含 "_output" 模式
        if val and (
            not val.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
            or "_output" in val
        ):
            wf[nid]['inputs']['image'] = current_image
            logger.info(
                f"[V2] 已修复节点{nid}占位符: \"{val}\" → {current_image}"
            )
    
    return wf


def _get_step_progress_range(step_index: int, total_steps: int) -> Tuple[int, int]:
    """计算第 step_index 步的进度范围（50%~100%）

    ⭐ V5.0: Fish 融合只有 1 步，直接返回 (50, 100)
    """
    return STORYBOARD_PROGRESS_MAP.get("1step_fusion", (50, 100))


def _extract_clip_text(workflow: dict) -> str:
    """从工作流中提取 CLIPTextEncode 节点的正向提示词文本"""
    for nid, ndata in workflow.items():
        if isinstance(ndata, dict) and ndata.get("class_type") == "CLIPTextEncode":
            text = ndata.get("inputs", {}).get("text", "")
            if text:
                return str(text)
    return ""
