

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
GENERATED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "generated")


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


class ComfyUIService:
    """ComfyUI 服务（含自动启动、空闲自停、健康检查、显存协调）"""

    def __init__(self, config: Optional[ComfyUIConfig] = None):
        self.config = config or ComfyUIConfig()
        self._process: Optional[subprocess.Popen] = None
        self._comfyui_log_f = None  # ⭐ Fix 10: ComfyUI 日志文件句柄
        self._image_cache: OrderedDict = OrderedDict()  # 支持 LRU 淘汰
        self._last_used: float = 0
        self._idle_shutdown_task: Optional[asyncio.Task] = None
        self._health_check_task: Optional[asyncio.Task] = None  # 健康检查后台任务
        # 并发控制：最多 2 个并发生成
        self._semaphore = asyncio.Semaphore(2)
        # 按模型类型分别计数（SDXL/Z-Image vs Qwen），不同模型可共存无需互相重启
        self._model_generation_count: Dict[str, int] = {"sd": 0, "qwen": 0}
        # ⭐ 活跃生成标志：防止空闲定时器在生成进行中误杀 ComfyUI
        self._active_generation: bool = False
        self._max_generations_before_restart: Dict[str, int] = {"sd": 3, "qwen": 3}
        # 防重入标志：防止多个协程并发执行 stop + restart
        self._restart_in_progress: bool = False
        # ComfyUI 重启事件回调（用于 WS 广播）
        self._restart_callbacks: List[Callable[[str, int], Awaitable[None]]] = []
        # 预估重启等待时间（秒）
        self._estimated_restart_secs: int = 15
        # ⭐ 共享 aiohttp session（复用连接，减少内存碎片）
        self._http_session: Optional[aiohttp.ClientSession] = None

        # ── 子模块实例（P2 拆分：委托职责到独立模块）──────────
        from services.comfyui.client import ComfyUIClient
        from services.comfyui.process_manager import ComfyUIProcessManager
        from services.comfyui.file_handler import ComfyUIFileHandler

        self._client = ComfyUIClient(
            base_url=self.config.base_url,
            output_dir=self.config.output_dir,
        )
        self._process_mgr = ComfyUIProcessManager(
            comfyui_dir=COMFYUI_DIR,
            base_url=self.config.base_url,
            check_alive_fn=self._check_alive,
            on_restart=None,
        )
        self._file_handler = ComfyUIFileHandler(
            comfyui_dir=COMFYUI_DIR or "",
            output_dir=self.config.output_dir,
            base_url=self.config.base_url,
            http_session_fn=self._get_http_session,
        )

    def reset_generation_count(self, model_family: str = None):
        """⭐ Fix 3: 重置生成计数，防止跨管线阶段误触 ComfyUI 重启
        
        问题：_model_generation_count 在 ComfyUIService 单例中永不重置。
        前一轮管线执行 5 次 Qwen（概念+精修），新管线第一次分镜时
        count=6 > max_gen=5，_ensure_clean_state 触发不必要的 ComfyUI 重启
        → 双进程争抢显存 → 系统 RAM 爆。
        
        调用时机：每个阶段入口处调用。
        """
        if model_family:
            old = self._model_generation_count.get(model_family, 0)
            self._model_generation_count[model_family] = 0
            logger.info(f"[Fix3] reset_generation_count | model={model_family} | {old}→0")
        else:
            logger.info(f"[Fix3] reset_generation_count | all | {self._model_generation_count}→{{'sd':0,'qwen':0}}")
            self._model_generation_count = {"sd": 0, "qwen": 0}

    def set_restart_callback(self, cb: Callable[[str, int], Awaitable[None]]):
        """注册重启事件回调，在 ComfyUI 重启时广播 status + estimated_secs"""
        self._restart_callbacks.append(cb)

    def clear_restart_callbacks(self):
        """清除所有重启回调"""
        self._restart_callbacks.clear()
        self._process_mgr.clear_restart_callbacks()

    # ── 子模块访问属性（P2 拆分）───────────────────────────────

    @property
    def client(self):
        """ComfyUI HTTP 客户端子模块"""
        return self._client

    @property
    def process_manager(self):
        """ComfyUI 进程管理子模块"""
        return self._process_mgr

    @property
    def file_handler(self):
        """ComfyUI 文件处理子模块"""
        return self._file_handler

    async def _notify_restart(self, status: str = "restarting", estimated_secs: int = 15):
        """通知所有注册回调：ComfyUI 正在重启"""
        for cb in self._restart_callbacks:
            try:
                await cb(status, estimated_secs)
            except Exception as e:
                logger.debug(f"[ComfyUI] 重启回调执行失败: {e}")

    def _get_http_session(self) -> aiohttp.ClientSession:
        """获取共享 aiohttp session（复用 client 的 session，避免重复创建）"""
        # 复用 ComfyUIClient 的 session（统一连接池管理）
        if self._client is not None:
            client_session = self._client.get_http_session()
            if client_session is not None and not client_session.closed:
                return client_session
        # 兜底：client 未初始化时自建（仅用于启动前的早期请求）
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()
        return self._http_session

    async def _close_http_session(self):
        """关闭共享 aiohttp session（由 client 统一管理，此处仅关闭兜底 session）"""
        if self._http_session and not self._http_session.closed:
            try:
                await self._http_session.close()
            except Exception:
                pass
            self._http_session = None
        # client 的 session 由 client 自行关闭（在 stop() 中处理）

    # ── 输出文件持久化 ────────────────────────────────────────

    async def _persist_output_files(self, filenames: List[str]) -> None:
        """将 ComfyUI output 目录的生成图片复制到持久化目录

        避免 ComfyUI output 定期清理导致图片丢失。
        持久化目录由 GENERATED_DIR 定义，在 main.py 中也挂载了静态文件服务。
        """
        global GENERATED_DIR
        if not filenames or not GENERATED_DIR:
            return
        from urllib.parse import urlparse, parse_qs

        os.makedirs(GENERATED_DIR, exist_ok=True)
        copied = 0
        for fname in filenames:
            if not fname:
                continue
            # 处理 URL 格式：/api/comfyui/image?filename=xxx.png → xxx.png
            parsed = urlparse(fname if "?" in fname else f"?filename={fname}")
            params = parse_qs(parsed.query)
            actual_name = params.get("filename", [None])[0] or fname
            actual_name = os.path.basename(actual_name)
            if not actual_name:
                continue

            src = os.path.join(self.config.output_dir, actual_name)
            dst = os.path.join(GENERATED_DIR, actual_name)
            if os.path.isfile(src) and (not os.path.isfile(dst) or os.path.getsize(src) != os.path.getsize(dst)):
                try:
                    shutil.copy2(src, dst)
                    copied += 1
                    logger.debug(f"[Persist] 已持久化 | {actual_name}")
                except OSError as e:
                    logger.warning(f"[Persist] 复制失败: {actual_name} | {e}")
            elif not os.path.isfile(dst):
                # 本地文件不存在，通过 HTTP 从 ComfyUI /view 回退拉取
                try:
                    import aiohttp
                    from services.comfyui.config import COMFYUI_BASE_URL
                    comfyui_base = COMFYUI_BASE_URL
                    view_url = f"{comfyui_base}/view?filename={actual_name}&type=output"
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                        async with session.get(view_url) as resp:
                            if resp.status == 200:
                                data = await resp.read()
                                with open(dst, "wb") as f:
                                    f.write(data)
                                copied += 1
                                logger.debug(f"[Persist] HTTP 回退持久化 | {actual_name}")
                except Exception as e:
                    logger.warning(f"[Persist] HTTP 回退失败: {actual_name} | {e}")
        if copied > 0:
            logger.info(f"[Persist] 本次持久化 {copied}/{len(filenames)} 个文件")

    # ── 输出文件定期清理 ──────────────────────────────────────

    async def start_output_cleanup_task(self, interval_hours: int = 6):
        """启动输出文件定期清理后台任务

        Args:
            interval_hours: 清理间隔（小时），默认 6 小时
        """
        async def _cleanup_loop():
            while True:
                await asyncio.sleep(interval_hours * 3600)
                try:
                    self._file_handler.cleanup_old_output_files()
                except Exception as e:
                    logger.warning(f"[ComfyUI] 输出文件定期清理失败: {e}")

        self._output_cleanup_task = asyncio.create_task(_cleanup_loop())
        logger.info(f"[ComfyUI] 输出文件定期清理已启动 | interval={interval_hours}h")

    async def stop_output_cleanup_task(self):
        """停止输出文件定期清理后台任务"""
        task = getattr(self, '_output_cleanup_task', None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.info("[ComfyUI] 输出文件定期清理已停止")

    async def _release_vram_for_comfyui(self):
        """
        为 ComfyUI 释放显存：停止 llama.cpp
        16GB 显存无法同时运行 llama + ComfyUI，必须交替。
        """
        try:
            from services.process_manager import get_llm_manager
            llm_mgr = get_llm_manager()
            if llm_mgr.is_running:
                _ram = _get_ram_pct_safe()
                logger.info(
                    f"[VRAM] 停止 llama.cpp → 为 ComfyUI 释放显存"
                    f" | RAM_before={_ram:.1f}%"
                )
                await llm_mgr.stop_for_comfyui()
                _ram_after = _get_ram_pct_safe()
                logger.info(
                    f"[VRAM] llama.cpp 已停止，显存已释放给 ComfyUI | RAM_after={_ram_after:.1f}%"
                )
            else:
                logger.info("[VRAM] llama.cpp 未在运行，无需停止（显存已归 ComfyUI）")
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"[VRAM] 释放显存时出错: {e}")

    def _release_vram_for_llama(self):
        """
        ⭐ 为 llama.cpp VL 模型释放显存：停止 ComfyUI
        16GB 显存无法同时运行 llama + ComfyUI，必须交替。
        在视觉分析前调用，确保 Qwen3VL-8B 有足够显存。
        """
        if self._process is not None and self._process.poll() is None:
            _ram = _get_ram_pct_safe()
            logger.info(
                f"[VRAM] 停止 ComfyUI → 为 llama.cpp (Qwen3VL-8B) 释放显存"
                f" | RAM_before={_ram:.1f}% | ComfyUI_PID={self._process.pid}"
            )
            # ⭐ 标记 session 需要重建（同步方法无法 await close）
            self._http_session = None
            self.stop()
            _ram_after = _get_ram_pct_safe()
            logger.info(
                f"[VRAM] ComfyUI 已停止，显存已释放给 llama.cpp | RAM_after={_ram_after:.1f}%"
            )
        else:
            logger.info("[VRAM] ComfyUI 未在运行，无需停止（llama.cpp 可直接使用显存）")

    async def _pre_analyze_references(
        self,
        all_ref_items: List[Dict[str, Any]],
        project_id: Optional[str] = None,
        force_reanalyze: bool = False,
        progress_callback: Optional[Callable] = None,
    ) -> bool:
        """统一预分析所有参考图（带缓存 + 崩溃恢复）。

        流程：
        1. 尝试从项目目录 JSON 加载缓存
        2. 缓存命中 → 直接应用 visual_desc（跳过分析）
        3. 缓存未命中：
           a. 停止 ComfyUI（如运行中）释放显存
           b. 并行分析所有唯一参考图
           c. 结果写入 all_ref_items 的 visual_desc 字段
           d. 保存到项目目录 JSON（崩溃后可二次加载）

        Args:
            all_ref_items: 参考图列表（原地修改，添加 visual_desc）
            project_id: 项目 ID
            force_reanalyze: 强制重新分析（忽略缓存）
            progress_callback: 进度回调

        Returns:
            True 如果分析成功完成（或缓存命中）
        """
        if not all_ref_items:
            logger.info("[VisionPreAnalyze] 无参考图需要分析")
            return True

        total = len(all_ref_items)

        # ═══════════════════════════════════════════════════════════════
        # Step 1: 尝试加载缓存
        # ═══════════════════════════════════════════════════════════════
        items_to_analyze = list(all_ref_items)  # 待分析子集
        if not force_reanalyze:
            cache = _load_vision_cache(project_id) if project_id else None
            if cache:
                applied = _apply_vision_cache(all_ref_items, cache)
                remaining = total - applied
                if remaining == 0:
                    logger.info(
                        f"[VisionPreAnalyze] 全部命中缓存 ({total}/{total})，跳过分析"
                    )
                    return True
                # 部分命中：只需要分析未缓存的
                items_to_analyze = [item for item in all_ref_items if not item.get("visual_desc")]
                logger.info(
                    f"[VisionPreAnalyze] 部分命中: {applied}/{total} 缓存，"
                    f"剩余 {len(items_to_analyze)} 待分析"
                )
            else:
                logger.info(f"[VisionPreAnalyze] 无缓存，需分析 {total} 张参考图")
        else:
            logger.info(f"[VisionPreAnalyze] 强制重新分析 {total} 张参考图")

        if not items_to_analyze:
            return True

        # ═══════════════════════════════════════════════════════════════
        # Step 2: 停止 ComfyUI，释放显存给 llama.cpp VL
        # ═══════════════════════════════════════════════════════════════
        if progress_callback:
            try:
                progress_callback("🧹 停止生成引擎，准备视觉分析...", 0)
            except Exception:
                pass
        self._release_vram_for_llama()

        # ═══════════════════════════════════════════════════════════════
        # Step 3: 并行视觉分析（仅分析未缓存条目）
        # ═══════════════════════════════════════════════════════════════
        if progress_callback:
            try:
                progress_callback(f"🔍 开始分析 {len(items_to_analyze)} 张参考图...", 2)
            except Exception:
                pass

        await _analyze_reference_images(
            items_to_analyze,
            project_id=project_id,
            progress_callback=progress_callback,
        )

        # ═══════════════════════════════════════════════════════════════
        # Step 4: 保存缓存到项目目录 JSON（合并旧缓存 + 新分析结果）
        # ═══════════════════════════════════════════════════════════════
        if project_id:
            # 保存全部条目（含缓存命中的 + 新分析的）
            _save_vision_cache(project_id, all_ref_items)

        analyzed = sum(1 for item in items_to_analyze if item.get("visual_desc"))
        total_with_desc = sum(1 for item in all_ref_items if item.get("visual_desc"))
        logger.info(
            f"[VisionPreAnalyze] 分析完成: new={analyzed}/{len(items_to_analyze)}"
            f" | total_with_desc={total_with_desc}/{total}"
        )
        return total_with_desc > 0

    def _get_system_memory_usage(self) -> float:
        """获取系统内存使用百分比（基于 psutil）"""
        try:
            import psutil
            return psutil.virtual_memory().percent
        except ImportError:
            logger.warning("[ComfyUI] psutil 未安装，使用 WMIC 回退")
            try:
                if os.name == 'nt':
                    import subprocess as sp
                    result = sp.run(
                        ['wmic', 'OS', 'get', 'FreePhysicalMemory,TotalVisibleMemorySize', '/format:csv'],
                        capture_output=True, text=True, timeout=5
                    )
                    lines = [l.strip() for l in result.stdout.strip().split('\n') if l.strip()]
                    if len(lines) > 1:
                        parts = lines[1].split(',')
                        if len(parts) >= 3:
                            free = float(parts[1])
                            total = float(parts[2])
                            if total > 0:
                                return ((total - free) / total) * 100
                else:
                    with open('/proc/meminfo', 'r') as f:
                        lines = f.readlines()
                        mem = {}
                        for line in lines:
                            parts = line.split()
                            if len(parts) >= 2:
                                mem[parts[0]] = int(parts[1])
                        if 'MemTotal' in mem and 'MemAvailable' in mem:
                            used = mem['MemTotal'] - mem['MemAvailable']
                            return (used / mem['MemTotal']) * 100
            except Exception as e:
                logger.warning(f"[ComfyUI] WMIC/proc 内存获取失败: {e}")
            return 0.0
        except Exception as e:
            logger.warning(f"[ComfyUI] 获取系统内存使用率失败: {e}")
            return 0.0

    async def _get_vram_usage(self) -> float:
        """获取 ComfyUI 显存使用百分比
        优先通过 system_stats API，回退到 nvidia-smi 命令行
        """
        # 优先 ComfyUI API
        try:
            session = self._get_http_session()
            async with session.get(
                f"{self.config.base_url}/system_stats",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if "devices" in data and len(data["devices"]) > 0:
                        device = data["devices"][0]
                        vram_total = device.get("vram_total", 0)
                        vram_free = device.get("vram_free", 0)
                        if vram_total > 0:
                            vram_used = vram_total - vram_free
                            return (vram_used / vram_total) * 100
                    # 旧版格式
                    if "vram_total" in data and data["vram_total"] > 0:
                        vram_used = data.get("vram_used", 0)
                        return (vram_used / data["vram_total"]) * 100
        except Exception as e:
            logger.debug(f"[ComfyUI] system_stats VRAM 获取失败，走 nvidia-smi: {e}")

        # 回退：nvidia-smi 命令行
        try:
            import subprocess as sp
            result = sp.run(
                ['nvidia-smi', '--query-gpu=memory.used,memory.total', '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if not line.strip():
                        continue
                    parts = line.split(',')
                    if len(parts) >= 2:
                        used = float(parts[0].strip())
                        total = float(parts[1].strip())
                        if total > 0:
                            pct = (used / total) * 100
                            logger.debug(f"[ComfyUI] nvidia-smi VRAM: {used}/{total} MB ({pct:.1f}%)")
                            return pct
        except FileNotFoundError:
            logger.debug("[ComfyUI] nvidia-smi 不可用，跳过 VRAM 监测")
        except Exception as e:
            logger.warning(f"[ComfyUI] nvidia-smi 失败: {e}")

        return -1.0

    async def check_and_release_memory(self) -> bool:
        """
        检查内存和显存使用情况，如果过高则自动释放资源。
        返回 True 表示进行了释放操作。
        """
        released = False
        mem_percent = self._get_system_memory_usage()
        vram_percent = await self._get_vram_usage()

        logger.info(f"[ComfyUI] 内存使用: {mem_percent:.1f}%, 显存使用: {vram_percent:.1f}%")

        if mem_percent > MEMORY_HIGH_THRESHOLD:
            logger.warning(f"[ComfyUI] 内存使用率 {mem_percent:.1f}% 超过阈值 {MEMORY_HIGH_THRESHOLD}%，清理图片缓存...")
            self.clear_image_cache()
            # ⭐ 强制 Python 回收内存
            import gc
            gc.collect()
            # 等待 2 秒让 OS 回收内存
            await asyncio.sleep(2)
            mem_after = self._get_system_memory_usage()
            logger.info(f"[ComfyUI] gc.collect() 后内存: {mem_after:.1f}% (释放了 {mem_percent - mem_after:.1f}%)")
            # ⭐ GC 后内存仍 >95%，重启 ComfyUI 释放显存+内存
            if mem_after > 95:
                logger.warning(f"[ComfyUI] GC 后内存仍为 {mem_after:.1f}%，重启 ComfyUI 释放资源")
                await self._close_http_session()
                self.stop()
                # ⭐ 等待进程完全退出，内存释放后再启动
                await asyncio.sleep(3)
                gc.collect()
                await asyncio.sleep(2)
                await self.ensure_running()
                await asyncio.sleep(3)
                gc.collect()
                mem_after_restart = self._get_system_memory_usage()
                logger.info(f"[ComfyUI] 重启后内存: {mem_after_restart:.1f}%")
            released = True

        if vram_percent > VRAM_HIGH_THRESHOLD:
            logger.warning(f"[ComfyUI] 显存使用率 {vram_percent:.1f}% 超过阈值 {VRAM_HIGH_THRESHOLD}%，将通过重启释放 VRAM")
            await self._notify_restart("restarting", 20)
            await self._close_http_session()  # ⭐ 异步关闭 session
            self.stop()
            await self.ensure_running()
            await self._notify_restart("ready", 0)
            self._model_generation_count = {"sd": 0, "qwen": 0}
            released = True

        return released

    async def _quick_release_vram(self, unload_models: bool = False):
        """
        快速释放显存（调用 ComfyUI /free 端点，不重启进程）
        适用于流程切换时的轻量级清理

        Args:
            unload_models: 是否卸载模型（True=彻底释放显存，False=仅释放缓存）
        """
        # 先检查显存使用率，低于阈值则跳过
        vram_pct = await self._get_vram_usage()
        VRAM_QUICK_RELEASE_THRESHOLD = 70  # 仅在 VRAM>70% 时释放，避免不必要的等待
        if not unload_models and 0 <= vram_pct < VRAM_QUICK_RELEASE_THRESHOLD:
            logger.debug(f"[ComfyUI] 显存充足 ({vram_pct:.1f}%)，跳过 /free")
            return

        try:
            await self._client.free_vram(unload_models=unload_models)
        except Exception as e:
            logger.debug(f"[ComfyUI] /free 调用失败（可能不支持）: {e}")
            return

        # 等待显存释放完成（最多 3 秒）
        for _ in range(3):
            await asyncio.sleep(1)
            vram_pct = await self._get_vram_usage()
            if vram_pct >= 0 and vram_pct < 60:
                logger.info(f"[ComfyUI] 显存已释放: {vram_pct:.1f}%")
                return
        logger.info("[ComfyUI] /free 后显存仍较高，但不阻塞继续执行")

    async def _ensure_clean_state(self, model_family: str = "qwen"):
        """
        生成前确保 ComfyUI 处于干净状态。
        智能判断是否需要重启：
        1. 按模型类型分别计数
        2. 达到阈值时检查显存使用
        3. 显存充足则跳过重启，不足则重启
        
        Args:
            model_family: 模型类型 "sd" (Z-Image/瑶光) 或 "qwen" (Qwen Image Edit)
        """
        # 通过环境变量配置连续生成阈值
        # Qwen模型更大，默认5次（从3次提高，减少不必要的重启，每次重启耗时30~60s）
        # SD模型默认8次
        default_max = {"sd": 8, "qwen": 5}
        env_key = f"COMFYUI_MAX_{model_family.upper()}_GENERATIONS"
        max_gen = int(os.environ.get(env_key, default_max.get(model_family, 5)))
        
        count = self._model_generation_count.get(model_family, 0) + 1
        self._model_generation_count[model_family] = count

        logger.info(f"[ComfyUI] {model_family} 第 {count}/{max_gen} 次连续生成")

        if count >= max_gen:
            # 智能判断：先检查显存使用情况
            vram_percent = await self._get_vram_usage()
            
            if vram_percent >= 0 and vram_percent < 80:
                # 显存充足（<80%），仅释放缓存，不卸载模型（避免下次生成重载延迟）
                logger.info(f"[ComfyUI] 显存充足 ({vram_percent:.1f}%)，释放缓存")
                await self._quick_release_vram(unload_models=False)
                self._model_generation_count[model_family] = 0
                return
            
            # 显存不足或获取失败，执行重启
            if DISABLE_PROCESS_MANAGEMENT:
                logger.warning(
                    f"[ComfyUI] 已连续生成 {count} 次，显存使用率 {vram_percent:.1f}%，"
                    "但 DISABLE_PROCESS_MANAGEMENT=True 跳过重启"
                )
                self._model_generation_count[model_family] = 0
                return

            logger.warning(f"[ComfyUI] 已连续生成 {count} 次，显存使用率 {vram_percent:.1f}%，重启释放 VRAM")
            await self._notify_restart("restarting", 15)
            await self._close_http_session()  # ⭐ 异步关闭 session，避免 stop() 中的同步关闭问题
            self.stop()
            # ⭐ 等待进程完全退出，内存释放后再启动新进程
            await asyncio.sleep(3)
            import gc
            gc.collect()
            await asyncio.sleep(2)
            _mem_log("重启ComfyUI(内存已释放)", f"model={model_family}")
            await self.ensure_running()
            await self._notify_restart("ready", 0)
            self._model_generation_count[model_family] = 0
            logger.info("[ComfyUI] 重启完成，VRAM 已释放")

    async def ensure_running(self) -> bool:
        """
        确保 ComfyUI 正在运行。
        如果不在运行且配置了 COMFYUI_DIR，自动启动。
        使用 _restart_in_progress 标志防止竞态重入。
        当 DISABLE_PROCESS_MANAGEMENT=True 时，跳过自动启动，仅检查是否在线。
        """
        _boot_t0 = time.time()

        if self._restart_in_progress:
            logger.debug("[ComfyUI] 启动正在进行中，等待完成...")
            for _ in range(60):  # ⭐ V6.0: 最多等 60s（2分钟安全上限）
                await asyncio.sleep(2)
                if await self._check_alive():
                    return True
                if not self._restart_in_progress:
                    break
            # 等待超时，直接尝试启动（不再被动等待）
            logger.warning("[ComfyUI] 等待启动完成超时(60s)，强制重置标志后重新启动")
            self._restart_in_progress = False

        if await self._check_alive():
            _boot_elapsed = (time.time() - _boot_t0) * 1000
            logger.info(f"[BOOT] ensure_running | running=True | elapsed={_boot_elapsed:.0f}ms")
            return True

        # 进程管理被禁用时，仅检查在线状态，不自动启动
        if DISABLE_PROCESS_MANAGEMENT:
            logger.warning(
                "[ComfyUI] 服务不在运行，DISABLE_PROCESS_MANAGEMENT=True 跳过自动启动"
                "（由外部 Supervisor/Systemd 管理）"
            )
            return False

        logger.warning("[ComfyUI] 服务不在运行")
        if not COMFYUI_DIR:
            logger.warning(
                "[ComfyUI] 未配置 COMFYUI_DIR，请设置环境变量或手动启动"
            )
            return False

        result = await self._start_process()
        _boot_elapsed = (time.time() - _boot_t0) * 1000
        logger.info(f"[BOOT] ensure_running | running={result} | elapsed={_boot_elapsed:.0f}ms")
        return result

    async def _check_alive(self) -> bool:
        """检查 ComfyUI 是否在线（委托到 client 子模块）"""
        return await self._client.check_alive()

    async def _start_process(self) -> bool:
        """启动 ComfyUI（python main.py）"""
        _mem_log("ComfyUI启动前", "即将启动ComfyUI进程")
        # 先检查是否已在启动中（防重入）
        if self._restart_in_progress:
            logger.info("[ComfyUI] 启动正在进行中，跳过重复启动")
            return False

        # 先清理已有进程和端口占用
        self._restart_in_progress = True
        self._stop_process()
        self._kill_process_on_port(8188)

        # 环境变量：启用 CUDA 扩展段 + 其他优化
        env = os.environ.copy()
        env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

        cmd = [
            COMFYUI_PYTHON, COMFYUI_SCRIPT,
            "--use-sage-attention",
            "--bf16-unet",
            "--fast",
        ]
        logger.info(f"[ComfyUI] 启动: {COMFYUI_DIR}> {' '.join(cmd)}")

        # ⭐ Fix 10: 将 ComfyUI stdout/stderr 重定向到日志文件
        # 之前 DEVNULL 丢弃了模型加载失败、CUDA OOM 等关键诊断信息
        _comfyui_log_path = os.path.join(COMFYUI_DIR, "comfyui_backend.log")
        try:
            self._comfyui_log_f = open(_comfyui_log_path, "a", encoding="utf-8")
            _stdout = self._comfyui_log_f
            _stderr = subprocess.STDOUT  # stderr 合并到 stdout
            logger.info(f"[ComfyUI] 日志输出: {_comfyui_log_path}")
        except Exception:
            _stdout = subprocess.DEVNULL
            _stderr = subprocess.DEVNULL
            self._comfyui_log_f = None

        try:
            self._process = subprocess.Popen(
                cmd,
                cwd=COMFYUI_DIR,
                env=env,
                stdout=_stdout,
                stderr=_stderr,
               # stdin=subprocess.DEVNULL,  # ⭐ Fix: 关闭 stdin 防止子进程卡住
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except FileNotFoundError as e:
            logger.error(f"[ComfyUI] 启动失败（找不到 Python）: {e}")
            self._restart_in_progress = False
            return False
        except Exception as e:
            logger.error(f"[ComfyUI] 启动失败: {e}")
            self._restart_in_progress = False
            return False

        # 等待就绪
        logger.info(f"[ComfyUI] 等待就绪（最长 {COMFYUI_START_TIMEOUT}s）...")
        for _ in range(COMFYUI_START_TIMEOUT):
            await asyncio.sleep(2)
            # 使用局部变量避免 TOCTOU 竞态条件
            proc = self._process
            if proc is None:
                logger.error("[ComfyUI] 进程引用已丢失（被其他协程停止），终止启动")
                self._restart_in_progress = False
                return False
            if proc.poll() is not None:
                logger.error(
                    f"[ComfyUI] 进程已退出，返回码: {proc.returncode}"
                )
                self._process = None
                self._restart_in_progress = False
                return False
            if await self._check_alive():
                logger.info("[ComfyUI] 就绪")
                _mem_log("ComfyUI就绪", "ComfyUI进程已启动并响应")
                self._restart_in_progress = False
                self._start_health_check()  # 启动健康检查任务
                return True

        logger.error(f"[ComfyUI] 启动超时")
        self._stop_process()
        self._restart_in_progress = False
        return False

    def stop(self):
        """停止 ComfyUI 进程，释放显存"""
        if DISABLE_PROCESS_MANAGEMENT:
            logger.info("[ComfyUI] DISABLE_PROCESS_MANAGEMENT=True，跳过停止进程")
            return
        self._stop_process()
        # 无论如何，强制清理端口上的残余进程
        self._kill_process_on_port(8188)
        # ⭐ Fix 10: 关闭 ComfyUI 日志文件句柄
        if self._comfyui_log_f:
            try:
                self._comfyui_log_f.close()
            except Exception:
                pass
            self._comfyui_log_f = None
        # 取消健康检查任务
        if self._health_check_task and not self._health_check_task.done():
            self._health_check_task.cancel()
            self._health_check_task = None
        # ⭐ 关闭共享 HTTP session（ComfyUI 重启后旧 session 不可用）
        # 注意：stop() 是同步方法，不能 await，标记为需要关闭
        if self._http_session and not self._http_session.closed:
            # 同步关闭不可用，标记为需要重建
            try:
                # 尝试同步关闭（aiohttp 不推荐但可以工作）
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # 在运行中的 loop 上不能同步 close，标记重建
                        self._http_session = None
                    else:
                        loop.run_until_complete(self._http_session.close())
                        self._http_session = None
                except RuntimeError:
                    self._http_session = None
            except Exception:
                self._http_session = None
        logger.info("[ComfyUI] 已停止，显存已释放")

    def _stop_process(self):
        """停止 ComfyUI 进程（含子进程树）
        
        ⭐ Fix 8: Windows 上 proc.terminate() = TerminateProcess()，只杀主进程，
        不杀子进程树。ComfyUI 的 model loader、CUDA workers、onnx runtime 后台线程
        全变成孤儿进程，继续消耗系统内存和显存。多轮重启后累积数十个孤儿 Python 进程，
        这是 64GB 内存被占满的最主要根因。
        
        修复：Windows 平台直接执行 taskkill /F /T 杀进程树。
        """
        if self._process is not None:
            try:
                proc = self._process
                if sys.platform == "win32":
                    # Windows: terminate() 不杀子进程，必须用 taskkill /T
                    import subprocess as sp
                    result = sp.run(
                        f'taskkill /F /T /PID {proc.pid}',
                        capture_output=True, shell=True, timeout=5,
                        encoding='gbk', errors='replace',  # Windows 中文环境
                    )
                    logger.info(
                        f"[WINDOWS] taskkill /T PID={proc.pid}"
                        f" | stdout={result.stdout.strip()}"
                        f" | stderr={result.stderr.strip()}"
                    )
                else:
                    # Linux/macOS: 先 SIGTERM，再 SIGKILL 进程组
                    import os as _os
                    try:
                        _os.killpg(_os.getpgid(proc.pid), signal.SIGTERM)
                    except (ProcessLookupError, PermissionError):
                        proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except Exception:
                        pass
                    if proc.poll() is None:
                        try:
                            _os.killpg(_os.getpgid(proc.pid), signal.SIGKILL)
                        except (ProcessLookupError, PermissionError):
                            proc.kill()
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

    @staticmethod
    def _kill_process_on_port(port: int):
        """强制释放指定端口（Windows），防止端口占用导致重启失败
        
        ⭐ Fix 8 配套: 使用 taskkill /F /T /PID 杀进程树，避免孤儿进程残留。
        """
        import subprocess as sp  # 避免与 aiohttp 的 subprocess 混淆
        try:
            # 查找占用该端口的 PID
            result = sp.run(
                f'netstat -ano | findstr ":{port} "',
                capture_output=True, shell=True, timeout=5,
                encoding='gbk', errors='replace',  # Windows 中文环境用 gbk
            )
            if not result.stdout.strip():
                return
            seen = set()
            for line in result.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 5 and (parts[3] or '').endswith(f':{port}'):
                    pid = parts[-1]
                    if pid not in seen:
                        seen.add(pid)
                        # ⭐ Fix 8: /T 杀进程树，避免孤儿进程
                        sp.run(f'taskkill /F /T /PID {pid}', capture_output=True, shell=True, timeout=5,
                               encoding='gbk', errors='replace')
                        logger.info(f"[WINDOWS] 已释放端口 {port} (PID={pid}, 含子进程树)")
        except Exception as e:
            logger.warning(f"[ComfyUI] 释放端口 {port} 失败: {e}")

    def _mark_generation_active(self):
        """标记活跃生成开始，防止空闲定时器误杀"""
        self._active_generation = True
        self._last_used = time.time()  # 刷新使用时间（双重保护）

    def _mark_generation_complete(self):
        """标记活跃生成结束"""
        self._active_generation = False
        self._last_used = time.time()
        self._schedule_idle_shutdown()

    def _schedule_idle_shutdown(self):
        """
        调度空闲自停：30 分钟未使用后自动停止 ComfyUI 释放显存，
        使得后续 LLM 调用有足够 VRAM。
        ⭐ 如果当前有活跃生成，跳过停止。
        ⭐ DISABLE_PROCESS_MANAGEMENT=True 时跳过空闲自停。
        """
        if DISABLE_PROCESS_MANAGEMENT:
            return

        if self._idle_shutdown_task and not self._idle_shutdown_task.done():
            self._idle_shutdown_task.cancel()

        async def _idle_check():
            await asyncio.sleep(1800)  # 30 分钟空闲超时（原5分钟，避免生成间隔被误杀）
            if self._process is not None and self._last_used > 0:
                idle_secs = time.time() - self._last_used
                if idle_secs >= 1795:
                    if self._active_generation:
                        logger.info(
                            "[ComfyUI] 空闲定时器触发但存在活跃生成，跳过停止"
                            f" | idle={idle_secs:.0f}s | 重新调度30分钟检查"
                        )
                        self._schedule_idle_shutdown()  # 重新调度
                        return
                    logger.info("[ComfyUI] 空闲超时（30分钟），停止 ComfyUI 释放显存")
                    await self._close_http_session()
                    self.stop()

        self._idle_shutdown_task = asyncio.ensure_future(_idle_check())

    def _start_health_check(self):
        """
        启动健康检查后台任务：每 10 秒检查一次 ComfyUI 是否健康，
        不健康时自动重启。
        ⭐ DISABLE_PROCESS_MANAGEMENT=True 时跳过健康检查（由外部管理）。
        """
        if DISABLE_PROCESS_MANAGEMENT:
            logger.info("[ComfyUI] DISABLE_PROCESS_MANAGEMENT=True，跳过健康检查")
            return

        if self._health_check_task and not self._health_check_task.done():
            self._health_check_task.cancel()

        async def _health_check_loop():
            fail_count = 0
            while True:
                await asyncio.sleep(10)  # 每 10 秒检查一次
                if self._process is None:
                    continue  # 进程未启动，不检查
                if self._restart_in_progress:
                    continue  # 正在启动中，跳过检查避免竞态
                try:
                    if await self._check_alive():
                        fail_count = 0
                    else:
                        fail_count += 1
                        logger.warning(f"[ComfyUI] 健康检查失败 {fail_count}/3")
                        if fail_count >= 3:
                            logger.error("[ComfyUI] 连续 3 次健康检查失败，自动重启...")
                            await self._close_http_session()
                            self.stop()
                            await self.ensure_running()
                            fail_count = 0
                except Exception as e:
                    logger.warning(f"[ComfyUI] 健康检查异常: {e}")

        self._health_check_task = asyncio.ensure_future(_health_check_loop())
        logger.info("[ComfyUI] 健康检查任务已启动")

    async def _cache_image(self, filename: str):
        """确保图片存在于磁盘（委托到 file_handler 子模块）"""
        await self._file_handler.cache_image(filename)

    async def _ensure_image_in_input_dir(self, image_url: str, project_id: Optional[str] = None) -> str:
        """确保参考图像存在于 ComfyUI input 目录中（委托到 file_handler 子模块）"""
        return await self._file_handler.ensure_image_in_input_dir(image_url, project_id)

    def get_cached_image(self, filename: str) -> Optional[bytes]:
        """获取缓存的图片数据（委托到 file_handler 子模块）"""
        return self._file_handler.get_cached_image(filename)

    def clear_image_cache(self):
        """清理内存中的图片缓存（委托到 file_handler 子模块）"""
        self._file_handler.clear_image_cache()

    async def _normalize_reference_images(self, ref_items: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """检测并统一参考图的尺寸比例（委托到 file_handler 子模块）"""
        return await self._file_handler.normalize_reference_images(ref_items)

    async def _generate_scene_prompts(
        self,
        concept_prompt_json: Optional[dict] = None,
        refined_prompt: str = "",
        user_scene_desc: str = "",
    ) -> Dict[str, Any]:
        """调用 DeepSeek 从文生图+精修优化提示词生成场景多角度提示词"""
        from services.prompt_service import get_prompt_service
        psvc = get_prompt_service()
        return await psvc.generate_scene_prompts(
            concept_prompt_json=concept_prompt_json,
            refined_prompt=refined_prompt,
            user_scene_desc=user_scene_desc,
        )

    async def check_health(self) -> Dict[str, Any]:
        """检查 ComfyUI 是否在线（委托到 client 子模块）"""
        return await self._client.check_health()

    async def get_queue_progress(self, prompt_id: str) -> Dict[str, Any]:
        """查询 ComfyUI 队列中指定 prompt 的生成进度（委托到 client 子模块）"""
        return await self._client.get_queue_progress(prompt_id)

    async def generate(
        self,
        prompt_json: dict,
        custom_text: str = "",
        negative_prompt: str = "",
        width: Optional[int] = None,
        height: Optional[int] = None,
        seed: Optional[int] = None,
        steps: Optional[int] = None,
        cfg: Optional[float] = None,  # ⭐ 修复 A1：新增 cfg 参数
        progress_callback: Optional[Callable] = None,
        reference_image: str = "",
        project_id: Optional[str] = None,
        asset_tag: Optional[str] = None,
        workflow_type: str = "yaoguang",  # yaoguang | qwen_refinement | qwen_standardization
        content_type: str = "",
    ) -> ComfyUIGenResult:
        """
        通过 ComfyUI 生成图像（自动等待服务就绪）

        Args:
            prompt_json: 结构化提示词
            custom_text: 自定义文本
            negative_prompt: 负向提示词
            width: 图像宽度
            height: 图像高度
            seed: 随机种子
            steps: 采样步数
            cfg: CFG 强度（Control Free Guidance）
            progress_callback: 进度回调函数
            reference_image: 参考图路径（图生图模式）
            workflow_type: 工作流类型（yaoguang/qwen_refinement/qwen_standardization）

        Returns:
            ComfyUIGenResult: 生成结果
        """
        # 并发控制：通过 _queue_prompt_with_retry 的 _semaphore 统一限制
        # （移除 _generate_lock，避免与 _semaphore 双重串行化）
        return await self._generate_impl(
            prompt_json=prompt_json,
            custom_text=custom_text,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            seed=seed,
            steps=steps,
            cfg=cfg,
            progress_callback=progress_callback,
            reference_image=reference_image,
            project_id=project_id,
            asset_tag=asset_tag,
            workflow_type=workflow_type,
            content_type=content_type,
        )

    async def _generate_impl(
        self,
        prompt_json: dict,
        custom_text: str = "",
        negative_prompt: str = "",
        width: Optional[int] = None,
        height: Optional[int] = None,
        seed: Optional[int] = None,
        steps: Optional[int] = None,
        cfg: Optional[float] = None,  # ⭐ 修复 A1：新增 cfg 参数
        progress_callback: Optional[Callable] = None,
        reference_image: str = "",
        project_id: Optional[str] = None,
        asset_tag: Optional[str] = None,
        workflow_type: str = "yaoguang",
        content_type: str = "",
    ) -> ComfyUIGenResult:
        """generate() 的实际实现（已获取互斥锁）"""
        self._mark_generation_active()
        # ⭐ Fix 3: 概念探索阶段入口，重置 sd 计数
        self.reset_generation_count("sd")
        # 0. 释放显存 + 检查内存/显存使用情况
        logger.info(f"[ComfyUI] generate() 入口 | workflow={workflow_type} | ref={reference_image[:30] if reference_image else 'none'}")
        await self._release_vram_for_comfyui()
        logger.info(f"[ComfyUI] generate() VRAM释放完成")
        await self.check_and_release_memory()
        logger.info(f"[ComfyUI] generate() 内存检查完成")

        # 1. 确保 ComfyUI 在运行
        ready = await self.ensure_running()
        logger.info(f"[ComfyUI] generate() ensure_running={ready}")
        if not ready:
            raise RuntimeError(
                "ComfyUI 不可用。请确保 ComfyUI 在 localhost:8188 运行，"
                "或设置 COMFYUI_DIR 环境变量让系统自动启动。"
            )

        actual_seed = seed or int(time.time() * 1000) % (2**31)

        # 2. 检查连续生成次数，必要时重启释放 VRAM（按模型类型分别计数）
        model_family = "sd" if workflow_type in (None, "", "yaoguang") else "qwen"
        await self._ensure_clean_state(model_family)

        # 预解析参考图（qwen 工作流需要 input 目录下的文件）
        resolved_image = reference_image
        if workflow_type in ("qwen_refinement", "qwen_standardization") and reference_image:
            resolved_image = await self._ensure_image_in_input_dir(reference_image)

        # 4. 根据工作流类型构建工作流
        if workflow_type == "qwen_refinement":
            # Qwen精修模式（单图编辑）
            prefix = f"{ (project_id or 'unknown')[-6:] }_{ asset_tag or 'refine' }"
            workflow, opt_prompt, prompt_sections = build_refinement_workflow(
                reference_image=resolved_image,
                role_desc=custom_text,
                seed=actual_seed,
                filename_prefix=prefix,
            )
            logger.info(f"[ComfyUI] 使用 Qwen 精修工作流")

        elif workflow_type == "qwen_standardization":
            # Qwen标准化模式（多视图生成）
            workflow, _, _ = build_standardization_workflow(
                reference_image=resolved_image,
                views=3,
                character_name=custom_text or "角色",
                seed=actual_seed,
                filename_prefix=f"{ (project_id or 'unknown')[-6:] }_{ asset_tag or 'std' }",
            )
            logger.info(f"[ComfyUI] 使用 Qwen 标准化工作流（3视图）")

        else:
            # 默认使用 Z-Image 瑶光版（文生图）
            positive_text = structured_prompt_to_comfyui_prompt(
                prompt_json, custom_text
            )
            neg_text = negative_prompt or YAOGUANG_DEFAULT_NEGATIVE

            # 打印最终发给 ComfyUI 的 prompt 文本，方便排查模型不按类型生成的问题
            logger.info(f"[ComfyUI] 正向提示词文本: {positive_text[:200]}")
            # 选择工作流模板：character 用影视级（25步/AuraFlow），scene 用标准版
            # prop 用道具专用版（+SeedVR2超分管线）
            # steps/cfg 使用 workflow_builder 函数的默认值（影视级=25/2，标准=8/1）
            if content_type in ("prop", "scene"):
                _workflow_type = "prop"
                # 道具/场景工作流：不传默认尺寸，让模板自带的尺寸生效
                _wf_width = width
                _wf_height = height
            else:
                _workflow_type = "cinematic" if content_type in ("character", "") else "standard"
                _wf_width = width or self.config.default_width
                _wf_height = height or self.config.default_height
            workflow = build_comfyui_workflow(
                positive_prompt=positive_text,
                negative_prompt=neg_text,
                width=_wf_width,
                height=_wf_height,
                seed=actual_seed,
                steps=steps,  # ⭐ 修复 A1：传递 steps 到工作流
                cfg=cfg,      # ⭐ 修复 A1：传递 cfg 到工作流
                reference_image=reference_image,
                content_type=content_type,
                workflow=_workflow_type,
            )
            # 打印工作流中最终的 CLIP 提示词，方便排查生成与预期不符的问题
            clip_text = _extract_clip_text(workflow)
            if clip_text:
                logger.info(f"[ComfyUI] 最终 CLIP 正向提示词: {clip_text[:300]}")
            logger.info(f"[ComfyUI] 使用 Z-Image 瑶光工作流")

        # ⭐ 断裂点3修复：ParamInjector 补漏注入
        # build_comfyui_workflow 已手写注入核心参数，此处用 ParamInjector 做二次校验+补漏
        # 确保 schema 中定义的所有参数都被注入，不依赖手写逻辑的完整性
        try:
            from services.workflow_params import inject_workflow_params
            schema_name = "文生图影视级" if _workflow_type == "cinematic" else None
            if schema_name:
                user_params = {
                    "prompt": positive_text,
                    "negative": neg_text,
                    "width": _wf_width,
                    "height": _wf_height,
                    "steps": steps,
                    "cfg": cfg,
                    "seed": actual_seed,
                }
                # 过滤 None 值（避免覆盖工作流模板默认值）
                user_params = {k: v for k, v in user_params.items() if v is not None}
                _, injected = inject_workflow_params(schema_name, workflow, user_params)
                if injected:
                    logger.info(f"[ComfyUI] ParamInjector 补漏注入: {list(injected.keys())}")
        except Exception as pie:
            logger.warning(f"[ComfyUI] ParamInjector 补漏失败（不影响主流程）: {pie}")

        # 3. 提交到 ComfyUI（含重试）
        start_time = time.time()
        prompt_id = await self._queue_prompt_with_retry(workflow)

        logger.info(
            f"[ComfyUI] 已提交: prompt_id={prompt_id[:8]}..., "
            f"seed={actual_seed}, workflow={workflow_type}"
        )

        # 4. 等待生成完成（带进度回调）
        filenames = await self._wait_for_completion(prompt_id, progress_callback, task_type='generate')
        if not filenames:
            raise RuntimeError("ComfyUI 生成完成但未返回任何图片文件")
        filename = filenames[0]
        total_elapsed = int((time.time() - start_time) * 1000)

        # 5. 缓存所有图片到内存（ComfyUI 停止后仍可访问）
        for fn in filenames:
            await self._cache_image(fn)

        # 6. 构建图像 URL（通过后端代理，避免 CSP 阻止）
        pipe_param = f"&pipeline_id={project_id}" if project_id else ""
        image_url = f"/api/comfyui/image?filename={filename}{pipe_param}"
        image_urls = [f"/api/comfyui/image?filename={fn}{pipe_param}" for fn in filenames]

        logger.info(
            f"[ComfyUI] 完成: prompt_id={prompt_id[:8]}..., "
            f"elapsed={total_elapsed}ms, filenames={filenames}"
        )

        # 7. 记录使用时间并调度空闲自停
        self._mark_generation_complete()

        return ComfyUIGenResult(
            image_url=image_url,
            filename=filename,
            images=image_urls,
            filenames=filenames,
            prompt_id=prompt_id,
            elapsed_ms=total_elapsed,
            seed=actual_seed,
        )

    async def refine_image(
        self,
        reference_image: str,
        role_desc: str = "",
        scene_desc: str = "",
        prop_desc: str = "",
        refinement_desc: str = "",  # 用户自定义的精修指令（如"增强面部细节"）
        lock_elements: Optional[List[str]] = None,
        seed: Optional[int] = None,
        progress_callback: Optional[Callable] = None,
        full_prompt: Optional[str] = None,  # 直接使用完整 5 段式提示词，跳过重建
        project_id: Optional[str] = None,
        asset_tag: Optional[str] = None,
        width: Optional[int] = None,   # 图像宽度（可选，覆盖工作流默认值）
        height: Optional[int] = None,  # 图像高度（可选，覆盖工作流默认值）
        content_type: str = "",        # 内容类型（character/scene/prop/""），驱动 LoRA 强度和缩放尺寸
    ) -> ComfyUIGenResult:
        """
        精修阶段：单图编辑模式（基于Qwen Image Edit）

        Args:
            reference_image: 参考图像路径
            role_desc: 角色描述
            scene_desc: 场景描述
            prop_desc: 道具描述
            refinement_desc: 用户自定义的精修指令（如"增强面部细节，让眼睛更有神采"）
            lock_elements: 需要锁定的元素列表
            seed: 随机种子
            progress_callback: 进度回调函数
            full_prompt: 直接使用的完整 5 段式提示词（不为空时跳过 format_qwen_prompt 重建）

        Returns:
            ComfyUIGenResult: 生成结果
        """
        _t0 = time.time()
        logger.info(f"[ComfyUI][精修] 方法入口 | ref={reference_image[:30] if reference_image else 'none'} | asset={asset_tag}")
        # ⭐ Fix 3: 精修阶段入口，重置 qwen 计数
        self.reset_generation_count("qwen")
        # 0. 释放显存 + 检查内存/显存使用情况
        await self._release_vram_for_comfyui()
        await self.check_and_release_memory()

        # 1. 确保 ComfyUI 在运行
        ready = await self.ensure_running()
        if not ready:
            raise RuntimeError(
                "ComfyUI 不可用。请确保 ComfyUI 在 localhost:8188 运行，"
                "或设置 COMFYUI_DIR 环境变量让系统自动启动。"
            )

        actual_seed = seed or int(time.time() * 1000) % (2**31)

        # 2. 检查连续生成次数，必要时重启释放 VRAM（Qwen 模型）
        await self._ensure_clean_state("qwen")

        # 3. 解析参考图：将 URL 转为 ComfyUI input 目录下的本地文件名
        resolved_image = await self._ensure_image_in_input_dir(reference_image)

        # 4. 构建精修提示词
        #    ─────────────────────────────────────────────────────
        #    Fisher 配置：简洁自然语言提示词，直接传给 Qwen VL
        #    Qwen VL 对简单指令理解远优于复杂的5段式结构化格式
        #    denoise=1 + ReferenceLatent 天然保证一致性，无需反复强调"保持不变"
        #    ─────────────────────────────────────────────────────
        #    优先级（从高到低）：
        #    1. 用户直接输入的 refinement_desc → 直接使用
        #    2. full_prompt 已含编辑指令 → 直接使用
        #    3. role_desc/scene_desc/prop_desc → 拼接为简洁自然语言
        #    4. 无任何描述 → 使用默认精修指令
        #    ─────────────────────────────────────────────────────
        _original_full_prompt = full_prompt  # 保存原始输入，全身扩展检测用

        if refinement_desc and refinement_desc.strip():
            # ★ 优先级1：用户直接输入的精修指令
            full_prompt = refinement_desc.strip()
            logger.info(f"[ComfyUI] 使用用户精修指令 | {full_prompt[:100]}...")

        elif full_prompt:
            # ★ 优先级2：full_prompt 直接使用
            #   Fisher 配置下不需要 DeepSeek 优化为编辑指令
            #   简洁自然语言直接喂给 Qwen VL 效果最好
            logger.info(f"[ComfyUI] 使用 full_prompt | {full_prompt[:80]}...")

        # ★ 优先级3 & 4 由 build_refinement_workflow 内部处理
        #   将 role_desc/scene_desc/prop_desc 拼接为简洁自然语言

        # 3.5 检测全身扩展意图（半身→全身）
        expand_full_body = False
        _check_text = (full_prompt or "") + (_original_full_prompt or "") + (role_desc or "") + (refinement_desc or "") + (scene_desc or "")
        _full_body_kw = [
            '全身', '全身像', '全貌', '从头到脚', '完整身体', '完整全身',
            '全身照', '站立全身', '正面全身', '全身站立',
            '下半身', '腿部', '腿', '鞋子', '脚', '小腿', '大腿', '膝盖',
            '露出全身', '展示全身', '全身图',
            '向下扩展', '画布扩展', '扩展画面', '生成下半身', '补全身体',
            'full body', 'full-body',
        ]
        if any(kw in _check_text for kw in _full_body_kw):
            expand_full_body = True
            # 构建专用全身扩展指令（简洁自然语言，Fisher 风格）
            body_desc = role_desc or refinement_desc or scene_desc or ""
            full_prompt = self._build_full_body_expansion_prompt(body_desc)
            logger.info(
                f"[ComfyUI] 全身扩展模式 | Fisher配置(denoise=1) | "
                f"prompt: {full_prompt[:100]}..."
            )
        else:
            logger.info(f"[ComfyUI] 精修阶段 - 标准单图编辑模式")

        # 3.6 全身扩展预处理：Python 侧填充参考图到底部（替代 ComfyUI letterbox 黑边）
        #     ImageScaleByAspectRatio V2 的 letterbox 会在上下两端加黑边，
        #     ReferenceLatent 锚定后模型把黑边当"图像内容"保留 → 无法 outpainting。
        #     解决：用 nude 镜像填充仅在底部延伸，让模型看到自然过渡。
        if expand_full_body:
            padded_filename = await self._prepare_fullbody_reference(resolved_image, project_id or "")
            # 使用填充后的图片作为参考图（宽高已为 9:16，无需节点169再处理）
            resolved_image = padded_filename

        # 4. 构建精修工作流（同时获取优化提示词）
        prefix = f"{ (project_id or 'unknown')[-6:] }_{ asset_tag or 'refine' }"
        workflow, opt_prompt, prompt_sections = build_refinement_workflow(
            reference_image=resolved_image,
            role_desc=role_desc,
            scene_desc=scene_desc,
            prop_desc=prop_desc,
            lock_elements=lock_elements,
            seed=actual_seed,
            filename_prefix=prefix,
            full_prompt=full_prompt,
            expand_full_body=expand_full_body,
            width=width,
            height=height,
            content_type=content_type,
        )

        logger.info(f"[ComfyUI] 精修阶段 - {'全身扩展模式' if expand_full_body else '标准单图编辑模式'} | prompt开头: {full_prompt[:120] if full_prompt else '(空)'}...")

        # 3. 提交到 ComfyUI
        start_time = time.time()
        prompt_id = await self._queue_prompt_with_retry(workflow)

        # 4. 等待生成完成
        filenames = await self._wait_for_completion(prompt_id, progress_callback, task_type='refine')
        if not filenames:
            raise RuntimeError("ComfyUI 精修生成完成但未返回任何图片文件")
        filename = filenames[0]
        total_elapsed = int((time.time() - start_time) * 1000)

        # 5. 缓存所有图片
        for fn in filenames:
            await self._cache_image(fn)

        # 6. 构建图像 URL
        pipe_param = f"&pipeline_id={project_id}" if project_id else ""
        image_url = f"/api/comfyui/image?filename={filename}{pipe_param}"
        image_urls = [f"/api/comfyui/image?filename={fn}{pipe_param}" for fn in filenames]

        logger.info(
            f"[ComfyUI] 精修完成: elapsed={total_elapsed}ms, filename={filename}"
        )

        # 7. 记录使用时间
        self._mark_generation_complete()
        logger.info(f"[ComfyUI][精修] 方法完成 | asset={asset_tag} | total_elapsed={time.time()-_t0:.1f}s | comfyui_elapsed={total_elapsed}ms")

        return ComfyUIGenResult(
            image_url=image_url,
            filename=filename,
            images=image_urls,
            filenames=filenames,
            prompt_id=prompt_id,
            elapsed_ms=total_elapsed,
            seed=actual_seed,
            prompt=opt_prompt,
            prompt_sections=prompt_sections,
        )

    async def _prepare_fullbody_reference(
        self, image_filename: str, project_id: Optional[str] = None
    ) -> str:
        """
        预处理参考图：底部填充到 9:16（替代 ComfyUI 内部 letterbox 黑边方案）
        
        核心问题：ImageScaleByAspectRatio V2 的 fit="letterbox" 
        会在上下两端各加黑边，ReferenceLatent 锚定后模型将黑边视为"图像内容"保留。
        
        解决方案：在 Python 侧用 PIL+numpy 仅在底部填充，填充区域用镜像+渐变
        模拟自然场景延伸，避免纯黑边被模型保留。
        
        Args:
            image_filename: ComfyUI input 目录下的参考图文件名
            project_id: 项目 ID（预留）
        
        Returns:
            填充后图片在 ComfyUI input 目录下的文件名
        """
        import numpy as np
        from PIL import Image
        
        input_dir = os.path.join(self.config.comfyui_dir, "input")
        source_path = os.path.join(input_dir, image_filename)
        
        if not os.path.exists(source_path):
            logger.warning(f"[ComfyUI][全身扩展] 参考图不存在: {source_path}，跳过预处理")
            return image_filename
        
        # ⭐ Fix 6: 使用 with 语句确保文件句柄正确关闭
        with Image.open(source_path) as _img:
            img = _img.convert("RGB")
            w, h = img.size
        
        # 目标: 9:16 竖屏 (宽高比 9:16)
        target_w = w
        target_h = int(w * 16 / 9)
        target_h = (target_h // 8) * 8  # 对齐 8 的倍数
        
        if target_h <= h:
            logger.info(f"[ComfyUI][全身扩展] 图片已足够 ({w}x{h}), target={target_w}x{target_h}, 无需填充")
            del img  # ⭐ Fix 6: 显式释放 PIL 对象
            return image_filename
        
        pad_bottom = target_h - h
        arr = np.array(img, dtype=np.float32)
        del img  # ⭐ Fix 6: numpy 数组已创建，释放 PIL 对象
        
        # 填充策略：MirrorPad 底部区域 → 模拟场景向下延伸
        # 取底部 1/4 区域（或至少 32px）做镜像翻转作为填充内容
        mirror_height = max(32, h // 4)
        mirror_strip = arr[h - mirror_height:h, :, :]  # (mirror_height, w, 3)
        flipped = mirror_strip[::-1, :, :]  # 垂直翻转
        
        # 用 tile 方式填满 pad_bottom 高度
        repeats = (pad_bottom // mirror_height) + 1
        fill_arr = np.tile(flipped, (repeats, 1, 1))[:pad_bottom, :, :]
        
        # 底部渐隐：越往下越暗（防止镜像痕迹明显）
        fade = np.linspace(1.0, 0.15, pad_bottom, dtype=np.float32).reshape(-1, 1, 1)
        fill_arr = fill_arr * fade + 30 * (1 - fade)  # 趋向深色
        
        # 拼接：原图 + 填充
        padded_arr = np.concatenate([arr, fill_arr.astype(np.uint8)], axis=0)
        
        # 保存
        stem = Path(image_filename).stem
        padded_filename = f"{stem}_fullbody_916.png"
        padded_path = os.path.join(input_dir, padded_filename)
        Image.fromarray(padded_arr.astype(np.uint8)).save(padded_path, "PNG")
        del arr, fill_arr, padded_arr  # ⭐ Fix 6: 释放中间 numpy 数组（约 3-10MB）
        
        logger.info(
            f"[ComfyUI][全身扩展] 预处理完成: {image_filename} → {padded_filename}"
            f" ({w}x{h} → {target_w}x{target_h}, 底部+{pad_bottom}px 镜像填充)"
        )
        return padded_filename

    def _build_full_body_expansion_prompt(self, source_desc: str = "") -> str:
        """
        构建全身扩展编辑指令（半身→全身 outpainting）
        
        Fisher 配置：简洁自然语言提示词。
        Qwen VL 对简单指令理解远优于冗长的约束列表。
        denoise=1 + ReferenceLatent 天然保证上半身一致性，无需反复强调"保持不变"。
        
        Args:
            source_desc: 角色/场景描述文本，用于提取风格参考
        
        Returns:
            编辑指令字符串
        """
        # 从描述中提取角色特征，附加到提示词
        feature_hint = ""
        if source_desc:
            clean = source_desc.replace("基于参考图的", "").replace("基于参考图", "").strip()
            clean = clean.rstrip("，。、；：")
            if clean:
                feature_hint = f"，风格：{clean[:60]}"
        
        return f"人物全身像，正面对摄像机，能看到鞋子{feature_hint}"

    async def standardize_views(
        self,
        reference_image: str,
        views: int = 3,
        view_names: Optional[List[str]] = None,  # 自定义视图名称列表
        asset_name: str = "",                    # 资产名称（场景名/角色名，用于文件名和兜底标签）
        seed: Optional[int] = None,
        progress_callback: Optional[Callable] = None,
        view_type: Optional[str] = "",
        full_prompt: Optional[str] = None,
        role_desc: Optional[str] = "",
        scene_dna: Optional[str] = "",
        project_id: Optional[str] = None,
        asset_tag: Optional[str] = None,
        concept_prompt_json: Optional[dict] = None,
        refined_prompt: str = "",
        width: Optional[int] = None,            # ⭐ 图像宽度（可选，覆盖工作流默认值）
        height: Optional[int] = None,           # ⭐ 图像高度（可选，覆盖工作流默认值）
    ) -> ComfyUIGenResult:
        """
        标准化阶段：多视图生成（基于Qwen Image Edit融合模式）

        Args:
            reference_image: 参考图像路径
            views: 视图数量（3或6）
            view_names: 自定义视图名称列表（如 ["正面视图", "侧面45度", "背面视图"]）
            asset_name: 资产名称（场景名/角色名，用于文件名标识）
            seed: 随机种子
            progress_callback: 进度回调函数
            view_type: 视图类型 character/scene/prop
            full_prompt: 直接使用的完整提示词
            role_desc: 用户输入描述（用于 DeepSeek 优化）
            scene_dna: 场景DNA（从文生图提示词提取，仅 scene 类型用）

        Returns:
            ComfyUIGenResult: 生成结果
        """
        _t0 = time.time()
        logger.info(f"[ComfyUI][标准化] 方法入口 | ref={reference_image[:30] if reference_image else 'none'} | views={views} | type={view_type} | asset={asset_name}")
        # ⭐ Fix 3: 标准化阶段入口，重置 qwen 计数
        self.reset_generation_count("qwen")
        # 0. 智能显存管理：_ensure_clean_state 内部已包含显存检查和重启逻辑
        # 不再单独调用 _release_vram_for_comfyui 和 check_and_release_memory，
        # 避免与 _ensure_clean_state 重复触发 ComfyUI 重启（每次重启耗时 30~60s）
        # 仅在 llama.cpp 确实运行时才停止它
        try:
            from services.process_manager import get_llm_manager
            llm_mgr = get_llm_manager()
            if llm_mgr.is_running:
                await self._release_vram_for_comfyui()
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"[VRAM] 释放显存时出错: {e}")

        # 1. 确保 ComfyUI 在运行
        ready = await self.ensure_running()
        if not ready:
            raise RuntimeError(
                "ComfyUI 不可用。请确保 ComfyUI 在 localhost:8188 运行，"
                "或设置 COMFYUI_DIR 环境变量让系统自动启动。"
            )

        actual_seed = seed or int(time.time() * 1000) % (2**31)

        # 2. 检查连续生成次数，必要时重启释放 VRAM（Qwen 模型）
        # _ensure_clean_state 内部会智能判断：显存充足则只调用 /api/free，不足才重启
        await self._ensure_clean_state("qwen")

        # 3. 解析参考图：将 URL 转为 ComfyUI input 目录下的本地文件名
        resolved_image = await self._ensure_image_in_input_dir(reference_image)

        # 4. 优化标准化提示词（通过 DeepSeek），根据 view_names 生成动态提示词
        opt_prompt = ""
        prompt_sections = {}
        
        # 标准化阶段始终使用本地模板提示词，确保三视图布局正确
        # 不再使用 DeepSeek，因为它可能会丢失三视图布局描述
        full_prompt = None  # 强制使用本地模板提示词
        
        logger.info(f"[ComfyUI] 标准化视图配置 | views={views} | view_names={view_names} | view_type={view_type} | asset_name='{asset_name}' | role_desc='{role_desc[:80] if role_desc else None}' | scene_dna='{scene_dna[:80] if scene_dna else None}'")

        # 4. 构建标准化工作流（多视图生成）
        # 场景走专用多角度工作流（双通道约束），角色/道具走标准单图编辑（在一张图中生成三视图）
        if view_type == 'scene':
            # 场景标准化也禁用 DeepSeek，避免幻觉
            # 使用本地模板生成多角度提示词
            # 优先级（从具体到通用）：
            # 1. concept_prompt_json 中的场景描述（每个资产独立，最准确）
            # 2. scene_dna（项目级合并场景描述，回退使用）
            # 3. role_desc（调用方传入的实际场景描述，_execute_standardize_stage 中构建的 scene_user_prompt）
            # 4. asset_name（资产名称）
            # 5. 兜底"场景"
            scene_label = "场景"

            # 从 concept_prompt_json 中提取当前资产的场景描述（最准确，避免用合并的 scene_dna）
            concept_scene_desc = ""
            if concept_prompt_json and isinstance(concept_prompt_json, dict):
                concept_scene_desc = concept_prompt_json.get("scene") or concept_prompt_json.get("description") or ""

            if concept_scene_desc and concept_scene_desc.strip():
                scene_label = concept_scene_desc.strip()[:80]
                logger.info(f"[ComfyUI] 场景标准化 | 使用 concept_prompt_json 的场景描述: '{scene_label[:60]}'")
            elif scene_dna and scene_dna.strip():
                scene_label = scene_dna.strip()[:80]
                # 兜底：scene_dna 可能包含多个场景的合并描述（以；分隔）
                # 尝试根据 asset_name 提取匹配的段落
                if asset_name and '；' in scene_dna:
                    parts = [p.strip() for p in scene_dna.split('；') if p.strip()]
                    matched = [p for p in parts if asset_name in p or any(kw in p for kw in [asset_name[:4]] if len(asset_name) > 2)]
                    if matched:
                        scene_label = matched[0][:80]
                        logger.info(f"[ComfyUI] 场景标准化 | 从 scene_dna 中匹配到段落: '{scene_label[:60]}'")
                logger.info(f"[ComfyUI] 场景标准化 | 使用 scene_dna 作为场景标签: '{scene_label[:60]}'")
            elif role_desc and role_desc.strip() and role_desc.strip() not in ("角色", "场景", ""):
                # 关键修复：role_desc 包含 _execute_standardize_stage 中精心构建的 scene_user_prompt
                # （如"哥特式黑暗城堡，阴云密布的天空..."），必须回退到这里！
                scene_label = role_desc.strip()[:80]
                logger.info(f"[ComfyUI] 场景标准化 | 使用 role_desc(scene_user_prompt) 作为场景标签: '{scene_label[:60]}'")
            elif asset_name and asset_name not in ("角色", "场景", ""):
                scene_label = asset_name[:80]
                logger.info(f"[ComfyUI] 场景标准化 | 使用 asset_name 作为场景标签: '{scene_label[:60]}'")
            else:
                logger.info(f"[ComfyUI] 场景标准化 | 使用默认场景标签: '{scene_label}' | scene_dna='{scene_dna[:60] if scene_dna else None}' | concept_scene='{concept_scene_desc[:60] if concept_scene_desc else None}' | role_desc='{role_desc[:60] if role_desc else None}' | asset_name='{asset_name}'")

            # 生成英文场景标签（用于 instruction，避免英文指令中混入中文）
            scene_label_en = "scene shown in the reference image"

            # 本地模板：生成6个标准角度的提示词（详细中文，参考原始多场景工作流风格）
            # 包含具体场景标签（scene_label），确保生成的多角度图片与原始场景保持一致
            _angle_names = [
                "wide angle",
                "front medium shot",
                "left 45 degree view",
                "right 45 degree view",
                "close-up",
                "top-down 90 degree view",
            ]
            _angle_descs = [
                "广角全景，展示完整场景空间",
                "正面中景，标准构图",
                "左侧45度斜侧，增加空间纵深",
                "右侧45度斜侧，对称展示",
                "特写镜头，聚焦核心区域",
                "正上方90度俯视，展示平面布局",
            ]
            local_frame_prompts = [
                f"{scene_label} | Scene：{desc}。仅改变视角，场景内容与参考图完全一致，严禁添加人物或新物体。"
                for desc in _angle_descs
            ]
            # 每帧独立的 instruction（只指定角度，保留规则由 workflow_builder 自动追加）
            _frame_instructions = [
                f"Generate a {angle} view of the scene shown in the reference image."
                for angle in _angle_names
            ]
            
            # 将资产名称 sanitize 后加入文件名便于识别
            safe_name = re.sub(r'[\\/:*?"<>|]', '_', asset_name[:32]) if asset_name else 'unknown'

            # 逐帧提交：ComfyUI batch模式下 promptLine 只输出第一行
            # 改为循环6次，每次使用一行提示词
            all_scene_filenames = []
            all_scene_image_urls = []
            scene_start = time.time()
            for frame_idx, frame_prompt in enumerate(local_frame_prompts):
                frame_seed = actual_seed + frame_idx  # 每帧不同seed
                frame_instruction = _frame_instructions[frame_idx] if frame_idx < len(_frame_instructions) else _frame_instructions[0]
                frame_workflow = build_scene_multiangle_workflow(
                    reference_image=resolved_image,
                    scene_dna=scene_label,
                    per_frame_prompts=[frame_prompt],  # 只传当前帧
                    instruction=frame_instruction,
                    seed=frame_seed,
                    filename_prefix=f"{ (project_id or 'unknown')[-6:] }_{ safe_name }_{ asset_tag or 'std' }_{frame_idx+1}of6",
                )
                prompt_id = await self._queue_prompt_with_retry(frame_workflow)
                filenames = await self._wait_for_completion(prompt_id, progress_callback, task_type='standardize_3')
                if filenames:
                    fn = filenames[0]
                    await self._cache_image(fn)
                    all_scene_filenames.append(fn)
                    all_scene_image_urls.append(f"/api/comfyui/image?filename={fn}&pipeline_id={project_id or ''}")
                    # ⭐ 不在此处保存到项目文件夹，由 pipeline_executor._save_stage_images 统一保存
                    # 避免重复下载+重复磁盘写入（每帧图片被写2次 → 只写1次）
                    logger.debug(f"[ComfyUI] 场景多角度 第{frame_idx+1}/6帧完成: {fn}")
                    # ⭐ 每帧生成后释放显存，避免6帧连续生成累积 OOM
                    if frame_idx < len(local_frame_prompts) - 1:  # 最后一帧不需要释放
                        await self._quick_release_vram()
                if progress_callback:
                    try:
                        progress_callback(f"帧 {frame_idx+1}/6 完成", int((frame_idx + 1) / 6 * 100))
                    except Exception:
                        pass

            total_elapsed = int((time.time() - scene_start) * 1000)
            filename = all_scene_filenames[0] if all_scene_filenames else ""
            image_url = all_scene_image_urls[0] if all_scene_image_urls else ""
            # 构建场景多角度 URL 列表，跳过空文件名
            scene_image_urls = [f"/api/comfyui/image?filename={fn}&pipeline_id={project_id or ''}" for fn in all_scene_filenames if fn]
            # result.prompt 只保留简短摘要（避免前端每张图都显示6帧拼接的长文本）
            # 每帧的独立提示词通过 frame_prompts 字段传递
            opt_prompt = f"场景多角度: 6帧 ({scene_label})"
            prompt_sections = {"scene_dna": scene_label}
            logger.info(f"[ComfyUI] 场景标准化完成: 6角度, elapsed={total_elapsed}ms")
            logger.info(f"[ComfyUI][标准化] 场景方法完成 | asset={asset_name} | total_elapsed={time.time()-_t0:.1f}s | comfyui_elapsed={total_elapsed}ms")

            self._mark_generation_complete()
            return ComfyUIGenResult(
                image_url=image_url,
                filename=filename,
                images=all_scene_image_urls,
                filenames=all_scene_filenames,
                prompt_id="",
                elapsed_ms=total_elapsed,
                seed=actual_seed,
                prompt=opt_prompt,
                prompt_sections=prompt_sections,
                frame_prompts=local_frame_prompts,
            )

        safe_name = re.sub(r'[\\/:*?"<>|]', '_', asset_name[:32]) if asset_name else 'unknown'
        workflow, opt_prompt, prompt_sections = build_standardization_workflow(
            reference_image=resolved_image,
            views=views,
            character_name=asset_name,
            seed=actual_seed,
            full_prompt=full_prompt,
            filename_prefix=f"{ (project_id or 'unknown')[-6:] }_{ safe_name }_{ asset_tag or 'std' }",
            view_type=view_type or 'character',
            role_desc=role_desc,  # 传递优化后的描述
            width=width,   # ⭐ 传递自定义尺寸
            height=height,  # ⭐ 传递自定义尺寸
        )
        logger.info(f"[ComfyUI] 标准化阶段 - {views}视图生成 | width={width} | height={height} | view_type={view_type}")

        # 调试：记录工作流关键节点参数，排查 "name 'w' is not defined" 错误
        _debug_nodes = {"177": "LoadImage", "169": "ImageScale", "180": "TextEncode", "174": "KSampler", "500": "ImageScale(out)"}
        for _nid, _ntitle in _debug_nodes.items():
            if _nid in workflow:
                _inputs = workflow[_nid].get("inputs", {})
                logger.info(f"[ComfyUI][调试] 节点{_nid}({_ntitle}): class={workflow[_nid].get('class_type','?')} | inputs_keys={list(_inputs.keys())}")

        # 5. 提交到 ComfyUI
        start_time = time.time()
        prompt_id = await self._queue_prompt_with_retry(workflow)

        # 6. 等待生成完成
        std_timeout = 'standardize_6' if views >= 6 else 'standardize_3'
        filenames = await self._wait_for_completion(prompt_id, progress_callback, task_type=std_timeout)
        if not filenames:
            raise RuntimeError("ComfyUI 标准化生成完成但未返回任何图片文件")
        filename = filenames[0]
        total_elapsed = int((time.time() - start_time) * 1000)

        # 7. 缓存所有图片
        for fn in filenames:
            await self._cache_image(fn)

        # 8. 构建图像 URL
        pipe_param = f"&pipeline_id={project_id}" if project_id else ""
        image_url = f"/api/comfyui/image?filename={filename}{pipe_param}"
        image_urls = [f"/api/comfyui/image?filename={fn}{pipe_param}" for fn in filenames]

        logger.info(
            f"[ComfyUI] 标准化完成: {views}视图, elapsed={total_elapsed}ms, filenames={filenames}"
        )

        # 9. 记录使用时间
        self._mark_generation_complete()
        logger.info(f"[ComfyUI][标准化] 角色道具方法完成 | asset={asset_name} | views={views} | total_elapsed={time.time()-_t0:.1f}s | comfyui_elapsed={total_elapsed}ms")

        return ComfyUIGenResult(
            image_url=image_url,
            filename=filename,
            images=image_urls,
            filenames=filenames,
            prompt_id=prompt_id,
            elapsed_ms=total_elapsed,
            seed=actual_seed,
            prompt=opt_prompt or full_prompt,
            prompt_sections=prompt_sections,
        )

    async def generate_video(
        self,
        prompt: str = "",
        reference_image: str = "",
        workflow_file: str = "LTX2.3导演2.json",
        width: int = 1280,
        height: int = 720,
        frame_count: int = 97,
        frame_rate: int = 24,
        seed: Optional[int] = None,
        project_id: Optional[str] = None,
        asset_tag: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
        **kwargs,
    ) -> ComfyUIGenResult:
        """
        视频生成 — 调用 LTX-2.3 导演工作流（支持timeline分段+TTS音频）

        Args:
            prompt: 视频提示词（global_prompt + local_prompts）
            reference_image: 参考图 URL（首帧引导图）
            workflow_file: ComfyUI 工作流 JSON 文件名（位于 workflows/ 目录）
            width/height: 视频分辨率
            frame_count: 生成帧数（97帧 ≈ 4秒 @24fps）
            frame_rate: 帧率
            seed: 随机种子
            kwargs:
                narration: 旁白文本（自动TTS生成音频并注入）
                narration_voice: TTS语音名称（默认zh-CN-XiaoxiaoNeural）
                segments: 剧本分镜列表 [{prompt, narration, duration_sec}, ...]
        """
        import random
        from pathlib import Path
        from services.workflow_builder import find_node_by_class_type

        start = time.time()
        _mem_log("视频生成开始", f"workflow={workflow_file} ref={reference_image[:50] if reference_image else 'none'}")

        # 1. 加载工作流
        workflow_dir = Path(__file__).parent.parent.parent / "workflows"
        workflow_path = workflow_dir / workflow_file

        if not workflow_path.exists():
            # 尝试项目根 workflows 目录（集中常量）
            from services.comfyui.config import WORKFLOWS_DIR
            workflow_path = Path(WORKFLOWS_DIR) / workflow_file
        if not workflow_path.exists():
            raise FileNotFoundError(f"视频工作流不存在: {workflow_file}")

        with open(workflow_path, "r", encoding="utf-8") as f:
            workflow = json.load(f)

        logger.info(f"[ComfyUI] 加载视频工作流 | file={workflow_file} | nodes={len(workflow)}")

        # 2. 设置随机种子
        actual_seed = seed if seed is not None else random.randint(0, 2**48 - 1)
        noise_nodes = find_node_by_class_type(workflow, "RandomNoise")
        if noise_nodes:
            workflow[noise_nodes[0][0]]["inputs"]["noise_seed"] = actual_seed
            logger.info(f"[ComfyUI] 视频种子 | seed={actual_seed}")

        # 3. 提示词处理：支持 PromptRelayEncode / LTXDirector 两种节点
        # - PromptRelayEncode: 标准节点，local_prompts 用 | 分隔
        # - LTXDirector: WhatDreamsCost 自定义节点，内部也调用 _encode_relay，
        #   同样需要 local_prompts 用 | 分隔
        #   当 local_prompts 为空时，自动从 timeline_data.segments[*].prompt 提取
        # 注意：LTXDirectorGuide 是 guide 图像应用节点，不需要 local_prompts
        local_prompts_override = kwargs.get("local_prompts", "")
        global_prompt_override = kwargs.get("global_prompt", "")

        # 查找所有需要 local_prompts 的节点类型
        _PROMPT_NODE_TYPES = ("PromptRelayEncode", "LTXDirector")
        prompt_nodes = []
        for ntype in _PROMPT_NODE_TYPES:
            prompt_nodes.extend(find_node_by_class_type(workflow, ntype))

        for node_id, node_data in prompt_nodes:
            inputs = node_data.get("inputs", {})
            # 覆盖 global_prompt（仅当显式传入）
            if global_prompt_override:
                inputs["global_prompt"] = global_prompt_override
            # 覆盖 local_prompts（仅当显式传入非空值）
            if local_prompts_override:
                inputs["local_prompts"] = local_prompts_override

            # 安全检查：如果 local_prompts 为空，尝试从 timeline_data 提取
            current_local = str(inputs.get("local_prompts", "")).strip()
            if not current_local:
                timeline_str = inputs.get("timeline_data", "")
                if timeline_str:
                    try:
                        tdata = json.loads(timeline_str)
                        segments = tdata.get("segments", [])
                        seg_prompts = [s.get("prompt", "").strip() for s in segments if s.get("prompt", "").strip()]
                        if seg_prompts:
                            inputs["local_prompts"] = " | ".join(seg_prompts)
                            logger.info(f"[ComfyUI] 从 timeline_data 提取 local_prompts | node={node_id} | segments={len(seg_prompts)}")
                    except (json.JSONDecodeError, AttributeError) as e:
                        logger.warning(f"[ComfyUI] timeline_data 解析失败 | node={node_id} | error={e}")

            # 安全检查：如果 local_prompts 仍为空，报错避免 ComfyUI 崩溃
            final_local = str(inputs.get("local_prompts", "")).strip()
            if not final_local:
                logger.error(f"[ComfyUI] local_prompts 为空！node={node_id} class={node_data.get('class_type')}")
            else:
                seg_count = len([p for p in final_local.split("|") if p.strip()])
                logger.info(f"[ComfyUI] 视频提示词 | node={node_id} | global={str(inputs.get('global_prompt',''))[:60]}... | local_segs={seg_count}")

            # LTXDirector特有：segment_lengths 和 guide_strength 必须与 local_prompts 数量一致
            if node_data.get("class_type") == "LTXDirector" and final_local:
                seg_count = len([p for p in final_local.split("|") if p.strip()])
                # 从timeline_data.segments提取每段长度
                timeline_str = inputs.get("timeline_data", "")
                seg_lengths = []
                if timeline_str:
                    try:
                        tdata = json.loads(timeline_str)
                        seg_lengths = [s.get("length", 48) for s in tdata.get("segments", [])]
                    except (json.JSONDecodeError, AttributeError):
                        pass
                # 如果timeline段数与prompt段数不匹配，用默认长度补齐
                if len(seg_lengths) != seg_count:
                    avg_len = sum(seg_lengths) // len(seg_lengths) if seg_lengths else 48
                    seg_lengths = seg_lengths[:seg_count] if len(seg_lengths) > seg_count else seg_lengths + [avg_len] * (seg_count - len(seg_lengths))
                inputs["segment_lengths"] = ",".join(str(l) for l in seg_lengths)
                inputs["guide_strength"] = ",".join(["1.00"] * seg_count)
                # 同步duration_frames和duration_seconds
                total_frames = sum(seg_lengths)
                inputs["duration_frames"] = total_frames
                inputs["duration_seconds"] = round(total_frames / frame_rate, 3)
                # 分辨率（必须是32的倍数）
                w = (width // 32) * 32
                h = (height // 32) * 32
                inputs["custom_width"] = w
                inputs["custom_height"] = h
                # 注意：不修改epsilon/img_compression等LTXDirector参数
                # 蒸馏模型参数是专门优化的，随意修改会降低质量
                logger.info(f"[ComfyUI] LTXDirector同步 | seg_lengths={inputs['segment_lengths']} | total={total_frames}f | {width}x{height}")

        # 3.4 质量参数：蒸馏模型(cfg=1, steps=8)是优化值，不修改
        # 只有非蒸馏模型（如MSR工作流）才需要调整cfg和steps

        # 3.5 TTS音频注入（LTXDirector支持audioSegments）
        narration = kwargs.get("narration", "")
        narration_voice = kwargs.get("narration_voice", "zh-CN-XiaoxiaoNeural")
        segments_script = kwargs.get("segments", [])  # [{prompt, narration, duration_sec}, ...]

        # 查找LTXDirector节点
        director_nodes = find_node_by_class_type(workflow, "LTXDirector")
        if director_nodes and (narration or segments_script):
            director_nid, director_ndata = director_nodes[0]
            director_inputs = director_ndata.get("inputs", {})
            timeline_str = director_inputs.get("timeline_data", "")
            if timeline_str:
                try:
                    tdata = json.loads(timeline_str)
                    audio_segments = tdata.get("audioSegments", [])
                    existing_timeline_segs = tdata.get("segments", [])
                    
                    # 确定要生成的音频
                    audio_items = []
                    if segments_script:
                        # 从剧本分镜生成
                        for i, seg in enumerate(segments_script):
                            if seg.get("narration"):
                                audio_items.append({
                                    "narration": seg["narration"],
                                    "seg_index": min(i, len(existing_timeline_segs) - 1),
                                })
                    elif narration:
                        # 单段旁白：放到第一段
                        audio_items.append({"narration": narration, "seg_index": 0})
                    
                    # 生成TTS音频并注入
                    if audio_items:
                        import uuid
                        from services.comfyui.config import COMFYUI_INPUT_DIR
                        comfyui_input = Path(COMFYUI_INPUT_DIR) if COMFYUI_INPUT_DIR else (Path(COMFYUI_DIR) / "input" if COMFYUI_DIR else None)
                        if comfyui_input is None:
                            logger.warning("[TTS] ComfyUI input 目录不可用，跳过音频注入")
                        else:
                            for item in audio_items:
                                try:
                                    tts_result = await self._generate_tts_flac(
                                        text=item["narration"],
                                        voice=narration_voice,
                                        output_dir=comfyui_input,
                                    )
                                    if tts_result:
                                        audio_file, waveform_peaks = tts_result
                                        seg_idx = item["seg_index"]
                                        seg_info = existing_timeline_segs[seg_idx] if seg_idx < len(existing_timeline_segs) else {}
                                        seg_start = seg_info.get("start", 0)
                                        seg_length = seg_info.get("length", 48)

                                        audio_segments.append({
                                            "id": uuid.uuid4().hex[:16],
                                            "type": "audio",
                                            "start": seg_start,
                                            "length": seg_length,
                                            "trimStart": 0,
                                            "audioDurationFrames": seg_length,
                                            "audioFile": audio_file,
                                            "fileName": audio_file,
                                            "waveformPeaks": waveform_peaks,
                                        })
                                        logger.info(f"[ComfyUI] TTS音频注入 | text={item['narration'][:30]}... | file={audio_file}")
                                except Exception as e:
                                    logger.warning(f"[ComfyUI] TTS生成失败: {e}")
                        
                        # 写回timeline_data
                        tdata["audioSegments"] = audio_segments
                        director_inputs["timeline_data"] = json.dumps(tdata, ensure_ascii=False)
                        director_inputs["use_custom_audio"] = True
                        logger.info(f"[ComfyUI] timeline_data音频注入完成 | audio_segs={len(audio_segments)}")
                        
                except (json.JSONDecodeError, AttributeError) as e:
                    logger.warning(f"[ComfyUI] TTS注入timeline_data失败: {e}")

        # 4. 分辨率和帧数：LTXDirector工作流已在上面的同步逻辑中处理
        # 对于非LTXDirector工作流（如旧版MSR），仍使用INTConstant覆盖
        if not director_nodes:
            # ⭐ 修复 Deep Issue 2：改用 node_id 白名单（值匹配脆弱，易误覆盖）
            # 实测所有 INTConstant 的 _meta.title 都是 "INT Constant"，title 匹配无效
            # MSR 工作流已知节点：43=width 44=height 50=total_length
            MSR_NODE_WIDTH = "43"
            MSR_NODE_HEIGHT = "44"
            MSR_NODE_TOTAL_LENGTH = "50"
            if width is not None and width != 1280:
                if MSR_NODE_WIDTH in workflow and workflow[MSR_NODE_WIDTH].get("class_type") == "INTConstant":
                    workflow[MSR_NODE_WIDTH]["inputs"]["value"] = width
                    logger.info(f"[ComfyUI] 宽度覆盖 | 节点{MSR_NODE_WIDTH} → {width}")
            if height is not None and height != 720:
                if MSR_NODE_HEIGHT in workflow and workflow[MSR_NODE_HEIGHT].get("class_type") == "INTConstant":
                    workflow[MSR_NODE_HEIGHT]["inputs"]["value"] = height
                    logger.info(f"[ComfyUI] 高度覆盖 | 节点{MSR_NODE_HEIGHT} → {height}")
            if frame_count is not None and frame_count != 97:
                if MSR_NODE_TOTAL_LENGTH in workflow and workflow[MSR_NODE_TOTAL_LENGTH].get("class_type") == "INTConstant":
                    workflow[MSR_NODE_TOTAL_LENGTH]["inputs"]["value"] = frame_count
                    logger.info(f"[ComfyUI] 帧数覆盖 | 节点{MSR_NODE_TOTAL_LENGTH} → {frame_count}")

        # 5. 设置帧率（保持原值 24fps）
        if frame_rate != 24:
            ltxv_cond_nodes = find_node_by_class_type(workflow, "LTXVConditioning")
            if ltxv_cond_nodes:
                workflow[ltxv_cond_nodes[0][0]]["inputs"]["frame_rate"] = frame_rate
            create_video_nodes = find_node_by_class_type(workflow, "CreateVideo")
            if create_video_nodes:
                workflow[create_video_nodes[0][0]]["inputs"]["fps"] = frame_rate

        # 6. 设置参考图（LoadImage 节点）
        # 统一处理：先替换缺失文件，再注入参考图
        reference_images = kwargs.get("reference_images", {}) or {}
        comfyui_input = Path(COMFYUI_DIR) / "input" if COMFYUI_DIR else None

        load_image_nodes = [
            (nid, n) for nid, n in workflow.items()
            if n.get("class_type") == "LoadImage"
        ]

        if load_image_nodes and comfyui_input:
            # 6a. 先检查并替换所有缺失的 LoadImage 文件
            placeholder_name = ""
            missing_files = []
            for nid, node in load_image_nodes:
                orig_file = node["inputs"].get("image", "")
                if orig_file and not (comfyui_input / orig_file).exists():
                    missing_files.append((nid, orig_file))

            if missing_files:
                # 创建占位图（如果还没有）
                placeholder_name = "_director_placeholder.png"
                placeholder_path = comfyui_input / placeholder_name
                if not placeholder_path.exists():
                    try:
                        from PIL import Image
                        img = Image.new("RGB", (64, 64), (200, 200, 200))
                        img.save(str(placeholder_path))
                        logger.info(f"[ComfyUI] 创建占位图: {placeholder_path}")
                    except Exception as e:
                        logger.warning(f"[ComfyUI] 占位图创建失败: {e}")
                        placeholder_name = "blank64.png"  # fallback

                for nid, orig_file in missing_files:
                    workflow[nid]["inputs"]["image"] = placeholder_name
                    logger.info(f"[ComfyUI] 缺失文件替换 | {orig_file} → {placeholder_name}")

            # 6b. 注入参考图（覆盖占位图或已有文件）
            if reference_images or reference_image:
                ref_cache: Dict[str, str] = {}

                async def _resolve_ref(url: str) -> str:
                    if url in ref_cache:
                        return ref_cache[url]
                    fname = ""
                    if "?filename=" in url:
                        comfyui_fname = url.split("?filename=")[-1].split("&")[0]
                        try:
                            fname = await self._copy_output_to_input(comfyui_fname)
                        except Exception as e:
                            logger.warning(f"[ComfyUI] 参考图复制失败: {e}")
                    else:
                        # 如果是 ComfyUI input 目录中已有的文件名，直接返回
                        if comfyui_input and (comfyui_input / url).exists():
                            fname = url
                        else:
                            try:
                                fname = await self._download_to_input(url)
                            except Exception as e:
                                logger.warning(f"[ComfyUI] 参考图下载失败: {e}")
                    ref_cache[url] = fname
                    return fname

                replaced = []
                for nid, node in load_image_nodes:
                    orig_file = node["inputs"].get("image", "")
                    # 模式a：多角色注入 - 按原文件名匹配
                    if orig_file in reference_images:
                        input_fname = await _resolve_ref(reference_images[orig_file])
                        if input_fname:
                            workflow[nid]["inputs"]["image"] = input_fname
                            replaced.append(f"{nid}({orig_file}→{input_fname})")
                        continue
                    # 模式b：单图占位 - 替换所有缺失文件或占位图
                    if reference_image:
                        current_file = node["inputs"].get("image", "")
                        # 替换条件：原文件缺失，或当前是占位图
                        is_missing = current_file in (placeholder_name, "blank64.png")
                        orig_missing = orig_file and not (comfyui_input / orig_file).exists()
                        if is_missing or orig_missing:
                            resolved = await _resolve_ref(reference_image)
                            if resolved:
                                workflow[nid]["inputs"]["image"] = resolved
                                replaced.append(f"{nid}({current_file}→{resolved})")

                if replaced:
                    logger.info(f"[ComfyUI] 视频参考图注入 | {replaced}")

        # 7. 提交工作流
        prompt_id = await self._queue_prompt_with_retry(workflow)
        logger.info(f"[ComfyUI] 视频工作流已提交 | prompt_id={prompt_id}")

        # 8. 等待完成（视频生成耗时较长，task_type=video 在 _wait_for_completion 中有对应超时）
        filenames = await self._wait_for_completion(
            prompt_id=prompt_id,
            task_type="video",
            progress_callback=progress_callback,
        )

        elapsed_ms = int((time.time() - start) * 1000)

        # 9. 视频文件在 /history 的 outputs 中，SaveVideo 节点输出在 "gifs" 或 "videos" 字段
        # _wait_for_prompt 返回的是 images 字段，视频需要单独查 history
        video_url = ""
        video_filename = filenames[0] if filenames else ""

        # 尝试从 history 获取视频文件
        try:
            session = self._get_http_session()
            async with session.get(
                f"{self.config.base_url}/history/{prompt_id}",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    history = data.get(prompt_id, {})
                    outputs = history.get("outputs", {})
                    for node_id, node_output in outputs.items():
                        # SaveVideo 节点输出在 "gifs" 字段
                        gifs = node_output.get("gifs", [])
                        for g in gifs:
                            video_filename = g.get("filename", video_filename)
                            break
                        # 也检查 videos 字段
                        videos = node_output.get("videos", [])
                        for v in videos:
                            video_filename = v.get("filename", video_filename)
                            break
        except Exception as e:
            logger.warning(f"[ComfyUI] 获取视频文件名失败: {e}")

        if video_filename:
            video_url = f"/api/comfyui/image?filename={video_filename}"
            # 如果是视频格式，可能需要不同的端点
            if video_filename.endswith((".mp4", ".webm", ".avi", ".mov")):
                video_url = f"{self.config.base_url}/view?filename={video_filename}"

        logger.info(
            f"[ComfyUI] 视频生成完成 | file={video_filename} | "
            f"elapsed={elapsed_ms}ms | url={video_url[:80]}"
        )

        return ComfyUIGenResult(
            image_url=video_url,
            filename=video_filename,
            images=[video_url] if video_url else [],
            filenames=[video_filename] if video_filename else [],
            prompt_id=prompt_id,
            elapsed_ms=elapsed_ms,
            seed=actual_seed,
            prompt=prompt,
        )

    async def generate_long_video(
        self,
        prompt: str = "",
        reference_image: str = "",
        reference_images: Optional[Dict[str, str]] = None,
        segment_prompts: Optional[List[str]] = None,
        workflow_file: str = "LTX-2.3_MSR_sample_workflow_V2.json",
        segment_count: int = 4,
        segment_seconds: int = 15,
        frame_rate: int = 24,
        width: int = 1280,
        height: int = 720,
        project_id: Optional[str] = None,
        asset_tag: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
        # TTS 配音相关参数
        tts_audios: Optional[List[str]] = None,
        tts_mode: str = "replace",
        tts_volume: float = 1.0,
        bgm_url: str = "",
        bgm_volume: float = 0.2,
        **kwargs,
    ) -> ComfyUIGenResult:
        """
        分段生成 + 拼接长视频（推荐方案）

        - 串行生成 N 个 segment_seconds 秒片段
        - 每段可注入不同的参考图（reference_images）和故事情节（segment_prompts）
        - 用 ffmpeg concat 拼接成最终长视频

        Args:
            reference_images: 多角色参考图字典，键为工作流原文件名
                示例：{"2.jpg": "主角URL", "1.jpg": "配角URL", "bg.png": "场景URL"}
            segment_prompts: 每段的 local_prompts（故事情节），长度应等于 segment_count
                示例：["女人走来", "两人对视", "开始对话", "并肩离去"]
            tts_audios: 每段对应的 TTS 音频 URL 列表（可选）
                长度应等于 segment_count；为空则不混入 TTS
            tts_mode: 'replace' TTS替代原音频 | 'overlay' TTS叠加原音频（仅当原视频有音频时生效）
            tts_volume: TTS 音量 0.0-1.0
            bgm_url: 背景音乐 URL（可选），将整段混入最终视频
            bgm_volume: BGM 音量 0.0-1.0
        """
        import asyncio
        import subprocess
        import random as _random
        from pathlib import Path

        start = time.time()
        total_frames = segment_seconds * frame_rate

        # 如果没有提供 segment_prompts，使用工作流原值（每段相同故事）
        use_segment_prompts = bool(segment_prompts) and len(segment_prompts) >= segment_count

        logger.info(
            f"[ComfyUI] 长视频分段生成 | segments={segment_count} "
            f"× {segment_seconds}s = {segment_count * segment_seconds}s | "
            f"独立故事情节={'是' if use_segment_prompts else '否(工作流原值)'} | "
            f"多角色参考图={'是' if reference_images else '否'}"
        )

        segment_urls: List[str] = []
        segment_filenames: List[str] = []
        segment_seeds: List[int] = []

        # 1. 串行生成每个片段
        for i in range(segment_count):
            seg_start = time.time()
            seg_seed = _random.randint(0, 2**48 - 1)
            segment_seeds.append(seg_seed)

            # 该段的故事情节（local_prompts）
            seg_local_prompts = ""
            if use_segment_prompts:
                seg_local_prompts = segment_prompts[i]
                logger.info(
                    f"[ComfyUI] 生成片段 {i+1}/{segment_count} | seed={seg_seed} | "
                    f"故事: {seg_local_prompts[:50]}..."
                )
            else:
                logger.info(
                    f"[ComfyUI] 生成片段 {i+1}/{segment_count} | seed={seg_seed} | "
                    f"故事: 工作流原值"
                )

            # 进度回调：整体进度 = 已完成片段/总片段 + 当前片段进度
            def seg_progress(frac: float, _cb_i: int = i):
                if progress_callback:
                    overall = int((_cb_i + frac) / segment_count * 100)
                    try:
                        progress_callback(f"片段 {_cb_i+1}/{segment_count}", overall)
                    except Exception:
                        pass

            # 构建该段的 kwargs：注入 reference_images 和该段 local_prompts
            seg_kwargs = dict(kwargs)
            if reference_images:
                seg_kwargs["reference_images"] = reference_images
            if seg_local_prompts:
                seg_kwargs["local_prompts"] = seg_local_prompts

            seg_result = await self.generate_video(
                prompt=prompt,
                reference_image=reference_image,
                workflow_file=workflow_file,
                width=width,
                height=height,
                frame_count=total_frames,
                frame_rate=frame_rate,
                seed=seg_seed,
                project_id=project_id,
                asset_tag=f"{asset_tag}_seg{i+1}" if asset_tag else f"seg{i+1}",
                progress_callback=seg_progress,
                **seg_kwargs,
            )

            if not seg_result.image_url:
                logger.error(f"[ComfyUI] 片段 {i+1} 生成失败")
                raise RuntimeError(f"片段 {i+1}/{segment_count} 生成失败")

            segment_urls.append(seg_result.image_url)
            segment_filenames.append(seg_result.filename)
            seg_elapsed = int(time.time() - seg_start)
            logger.info(
                f"[ComfyUI] 片段 {i+1} 完成 | file={seg_result.filename} "
                f"耗时={seg_elapsed}s"
            )

        # 2. 用 ffmpeg 拼接
        logger.info(f"[ComfyUI] 开始拼接 {segment_count} 个片段")

        # 准备 concat 列表文件
        tmp_dir = Path(tempfile.gettempdir()) / "director_long_video"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        list_file = tmp_dir / f"concat_{int(time.time())}.txt"

        # 下载每个片段到本地（ffmpeg concat 需要本地文件）
        local_segment_files: List[str] = []
        session = self._get_http_session()
        try:
            for i, url in enumerate(segment_urls):
                # 从 URL 提取 ComfyUI 文件名
                if "?filename=" in url:
                    fname = url.split("?filename=")[-1].split("&")[0]
                elif "/view?filename=" in url:
                    fname = url.split("/view?filename=")[-1].split("&")[0]
                else:
                    fname = f"seg_{i}.mp4"

                # 从 ComfyUI /view 下载
                view_url = f"{self.config.base_url}/view?filename={fname}&type=output"
                local_path = tmp_dir / f"seg_{i:02d}_{fname}"
                async with session.get(view_url) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"下载片段{i+1}失败: HTTP {resp.status}")
                    data = await resp.read()
                    local_path.write_bytes(data)
                local_segment_files.append(str(local_path))
                logger.info(f"[ComfyUI] 已下载片段 {i+1} | {local_path.name}")

            # 写入 concat 列表（使用绝对路径，转义反斜杠）
            with open(list_file, "w", encoding="utf-8") as f:
                for local_path in local_segment_files:
                    # Windows 路径反斜杠转义
                    escaped = local_path.replace("\\", "/")
                    f.write(f"file '{escaped}'\n")

            # 拼接输出
            final_filename = f"longvideo_{int(time.time())}.mp4"
            output_dir = Path(COMFYUI_DIR) / "output" if COMFYUI_DIR else tmp_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / final_filename

            # 检测每个片段的音频流
            def _has_audio_stream(file_path: str) -> bool:
                try:
                    probe = subprocess.run(
                        ["ffmpeg", "-i", file_path, "-hide_banner"],
                        capture_output=True, text=True, timeout=30,
                        encoding="utf-8", errors="replace",
                    )
                    return "Audio:" in (proc.stderr if False else probe.stderr)
                except Exception:
                    return False

            def _probe_audio_codec(file_path: str) -> str:
                """返回音频编码名（如 aac/opus），无音频返回空串"""
                try:
                    probe = subprocess.run(
                        ["ffprobe", "-v", "error", "-select_streams", "a:0",
                         "-show_entries", "stream=codec_name", "-of", "csv=p=0",
                         file_path],
                        capture_output=True, text=True, timeout=30,
                        encoding="utf-8", errors="replace",
                    )
                    return (probe.stdout or "").strip()
                except Exception:
                    return ""

            seg_has_audio = any(_probe_audio_codec(f) for f in local_segment_files)
            logger.info(f"[ComfyUI] 片段音频检测 | 任一片段含音频={seg_has_audio}")

            # 优先尝试 concat copy（视频流不变，音频流也 copy）
            # 若任一片段无音频或 copy 失败，则使用重编码 + 强制音频规范化
            use_reencode = not seg_has_audio
            if not use_reencode:
                cmd = [
                    "ffmpeg", "-y",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", str(list_file),
                    "-c", "copy",
                    "-movflags", "+faststart",
                    str(output_path),
                ]
                logger.info(f"[ComfyUI] ffmpeg 拼接(copy) | cmd={' '.join(cmd[:8])}...")
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=300,
                    encoding="utf-8", errors="replace",
                )
                if proc.returncode != 0:
                    logger.warning(f"[ComfyUI] copy 拼接失败，回退重编码 | stderr={proc.stderr[-300:]}")
                    use_reencode = True

            if use_reencode:
                # 重编码：视频 libx264，音频 aac；若输入无音频则注入静音轨道
                logger.info("[ComfyUI] 使用重编码拼接（保证音频流）")
                if seg_has_audio:
                    cmd_reencode = [
                        "ffmpeg", "-y",
                        "-f", "concat",
                        "-safe", "0",
                        "-i", str(list_file),
                        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                        "-c:a", "aac", "-b:a", "128k",
                        "-movflags", "+faststart",
                        str(output_path),
                    ]
                else:
                    # 输入无音频：用第一个片段做视频源 + 合成静音音频轨道
                    # 时长对齐视频流；后续可由用户叠加 BGM/TTS
                    cmd_reencode = [
                        "ffmpeg", "-y",
                        "-f", "concat", "-safe", "0",
                        "-i", str(list_file),
                        "-f", "lavfi", "-t", "0", "-i", "anullsrc=r=44100:cl=stereo",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                        "-c:a", "aac", "-b:a", "128k",
                        "-shortest",
                        "-movflags", "+faststart",
                        str(output_path),
                    ]
                logger.info(f"[ComfyUI] ffmpeg 拼接(reencode) | cmd={' '.join(cmd_reencode[:8])}...")
                proc = subprocess.run(
                    cmd_reencode, capture_output=True, text=True, timeout=900,
                    encoding="utf-8", errors="replace",
                )
                if proc.returncode != 0:
                    raise RuntimeError(f"ffmpeg 拼接失败: {proc.stderr[-500:]}")

            # 验证输出文件确实有音频流
            out_audio = _probe_audio_codec(str(output_path))
            logger.info(f"[ComfyUI] 拼接完成 | output={output_path} | size={output_path.stat().st_size//1024}KB | audio={out_audio or '无'}")

            # ===== TTS 配音混音 =====
            # 如果提供了 TTS 音频，按段对齐并混入视频
            use_tts = bool(tts_audios) and len(tts_audios) >= segment_count
            if use_tts:
                try:
                    import aiohttp as _aiohttp_tts
                    import urllib.request as _urlreq

                    logger.info(
                        f"[ComfyUI-TTS] 开始混音 | tts_mode={tts_mode} | "
                        f"tts_volume={tts_volume} | bgm={'是' if bgm_url else '否'}"
                    )

                    # 1. 下载每段 TTS 音频到临时目录
                    tts_local_files: List[str] = []
                    tts_session = self._get_http_session()
                    for i, tts_url in enumerate(tts_audios[:segment_count]):
                        if not tts_url:
                            tts_local_files.append("")
                            continue
                        tts_filename = f"tts_seg{i+1}_{int(time.time())}.flac"
                        tts_path = tmp_dir / tts_filename
                        try:
                            async with tts_session.get(
                                tts_url, timeout=_aiohttp_tts.ClientTimeout(total=60)
                            ) as tts_resp:
                                if tts_resp.status != 200:
                                    logger.warning(f"[ComfyUI-TTS] 段{i+1}下载失败 status={tts_resp.status}")
                                    tts_local_files.append("")
                                    continue
                                tts_data = await tts_resp.read()
                            tts_path.write_bytes(tts_data)
                            tts_local_files.append(str(tts_path))
                            logger.info(f"[ComfyUI-TTS] 段{i+1}已下载 | size={len(tts_data)//1024}KB")
                        except Exception as tts_e:
                            logger.warning(f"[ComfyUI-TTS] 段{i+1}下载异常: {tts_e}")
                            tts_local_files.append("")

                    # 2. 把每段 TTS 音频按段时长对齐，合并成一个完整音轨
                    merged_audio_path = tmp_dir / f"tts_merged_{int(time.time())}.flac"
                    seg_duration = segment_seconds  # 每段视频时长（秒）

                    # 用 ffmpeg 把每段 TTS 拼到对应时间点（不足补静音，超长截断）
                    filter_parts = []
                    inputs = []
                    valid_tts_count = 0
                    for i, tts_local in enumerate(tts_local_files):
                        if not tts_local:
                            # 该段无 TTS，用对应时长的静音
                            inputs += ["-f", "lavfi", "-t", str(seg_duration), "-i", "anullsrc=r=44100:cl=stereo"]
                        else:
                            inputs += ["-i", tts_local]
                        # 对该段做：apad 补齐到 seg_duration，然后 atrim 截断
                        # 如果是静音源，已经正好 seg_duration
                        filter_parts.append(f"[{i}:a]atrim=0:{seg_duration},asetpts=PTS-STARTPTS,apad=whole_dur={seg_duration},atrim=0:{seg_duration},asetpts=PTS-STARTPTS[a{i}]")
                        valid_tts_count += 1

                    # 合并所有段
                    concat_filter = "".join(f"[a{i}]" for i in range(valid_tts_count))
                    filter_complex = ";".join(filter_parts) + f";{concat_filter}concat=n={valid_tts_count}:v=0:a=1[out]"

                    cmd_merge = [
                        "ffmpeg", "-y",
                        *inputs,
                        "-filter_complex", filter_complex,
                        "-map", "[out]",
                        "-c:a", "flac",
                        str(merged_audio_path),
                    ]
                    logger.info(f"[ComfyUI-TTS] 合并 TTS 段 | cmd={' '.join(cmd_merge[:6])}...")
                    proc_merge = subprocess.run(
                        cmd_merge, capture_output=True, text=True, timeout=300,
                        encoding="utf-8", errors="replace",
                    )
                    if proc_merge.returncode != 0:
                        logger.warning(f"[ComfyUI-TTS] 合并失败，跳过 TTS | stderr={proc_merge.stderr[-300:]}")
                        use_tts = False
                    else:
                        logger.info(f"[ComfyUI-TTS] TTS 合并完成 | file={merged_audio_path.name}")

                    # 3. 把合并后的 TTS 音轨混入最终视频
                    if use_tts and merged_audio_path.exists():
                        mixed_path = output_dir / f"longvideo_tts_{int(time.time())}.mp4"
                        if tts_mode == "replace" or not out_audio:
                            # 替换原音频：直接用 TTS 作为最终音轨
                            audio_filter = f"[1:a]volume={tts_volume}[tts]"
                            map_args = ["-map", "0:v", "-map", "[tts]"]
                        else:
                            # 叠加原音频：原音频 + TTS 混音
                            audio_filter = (
                                f"[0:a]volume=1.0[orig];"
                                f"[1:a]volume={tts_volume}[tts];"
                                f"[orig][tts]amix=inputs=2:duration=first:dropout_transition=0[mix]"
                            )
                            map_args = ["-map", "0:v", "-map", "[mix]"]

                        # 如果有 BGM，再叠加一层
                        bgm_inputs = []
                        bgm_filter = ""
                        if bgm_url:
                            try:
                                bgm_local = tmp_dir / f"bgm_{int(time.time())}.flac"
                                async with tts_session.get(
                                    bgm_url, timeout=_aiohttp_tts.ClientTimeout(total=60)
                                ) as bgm_resp:
                                    if bgm_resp.status == 200:
                                        bgm_data = await bgm_resp.read()
                                        bgm_local.write_bytes(bgm_data)
                                        bgm_inputs = ["-i", str(bgm_local)]
                                        # 把 BGM 循环到视频时长，再与前述 mix 混音
                                        if "mix" in audio_filter:
                                            bgm_filter = f";[2:a]aloop=loop=-1:size=2e9,volume={bgm_volume}[bgm];[mix][bgm]amix=inputs=2:duration=first:dropout_transition=0[final]"
                                            map_args = ["-map", "0:v", "-map", "[final]"]
                                        else:
                                            # replace 模式 + BGM
                                            bgm_filter = f";[2:a]aloop=loop=-1:size=2e9,volume={bgm_volume}[bgm];[tts][bgm]amix=inputs=2:duration=first:dropout_transition=0[final]"
                                            map_args = ["-map", "0:v", "-map", "[final]"]
                            except Exception as bgm_e:
                                logger.warning(f"[ComfyUI-TTS] BGM 下载失败: {bgm_e}")

                        cmd_mix = [
                            "ffmpeg", "-y",
                            "-i", str(output_path),
                            "-i", str(merged_audio_path),
                            *bgm_inputs,
                            "-filter_complex", audio_filter + bgm_filter,
                            *map_args,
                            "-c:v", "copy",
                            "-c:a", "aac", "-b:a", "192k",
                            "-shortest",
                            "-movflags", "+faststart",
                            str(mixed_path),
                        ]
                        logger.info(f"[ComfyUI-TTS] 混音 | mode={tts_mode} | bgm={'是' if bgm_url else '否'}")
                        proc_mix = subprocess.run(
                            cmd_mix, capture_output=True, text=True, timeout=600,
                            encoding="utf-8", errors="replace",
                        )
                        if proc_mix.returncode == 0 and mixed_path.exists():
                            # 替换输出文件
                            try:
                                output_path.unlink(missing_ok=True)
                            except Exception:
                                pass
                            output_path = mixed_path
                            final_filename = mixed_path.name
                            logger.info(f"[ComfyUI-TTS] 混音完成 | file={final_filename} | size={mixed_path.stat().st_size//1024}KB")
                        else:
                            logger.warning(f"[ComfyUI-TTS] 混音失败，保留原视频 | stderr={proc_mix.stderr[-300:]}")

                        # 清理 TTS 临时文件
                        try:
                            merged_audio_path.unlink(missing_ok=True)
                            for f in tts_local_files:
                                if f:
                                    Path(f).unlink(missing_ok=True)
                        except Exception:
                            pass
                except Exception as tts_outer_e:
                    logger.warning(f"[ComfyUI-TTS] TTS 混音整体失败，保留原视频 | error={tts_outer_e}")

            # 清理临时文件
            try:
                list_file.unlink(missing_ok=True)
                for f in local_segment_files:
                    Path(f).unlink(missing_ok=True)
            except Exception:
                pass

            # 上传最终视频到 ComfyUI output（如果不在）
            final_url = f"{self.config.base_url}/view?filename={final_filename}"
            elapsed_ms = int((time.time() - start) * 1000)

            logger.info(
                f"[ComfyUI] 长视频生成完成 | 总时长={segment_count * segment_seconds}s "
                f"| 耗时={elapsed_ms//1000}s | seeds={segment_seeds}"
            )

            return ComfyUIGenResult(
                image_url=final_url,
                filename=final_filename,
                images=[final_url],
                filenames=[final_filename],
                prompt_id="",
                elapsed_ms=elapsed_ms,
                seed=segment_seeds[0],
                prompt=prompt,
            )
        finally:
            pass

    async def generate_tts_audio(
        self,
        text: str,
        mode: str = "voice_design",
        voice_description: str = "",
        ref_audio_url: str = "",
        ref_text: str = "",
        language: str = "Auto",
        project_id: Optional[str] = None,
        asset_tag: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
    ) -> ComfyUIGenResult:
        """
        生成 TTS 音频（基于 Qwen3-TTS 工作流）

        Args:
            text: 要合成的文本（必填）
            mode: 'voice_design' 音色设计 | 'voice_clone' 音色克隆
            voice_description: 音色设计模式的音色描述（如"清脆童声，8岁女童"）
            ref_audio_url: 音色克隆模式的参考音频 URL（必填，克隆模式）
            ref_text: 参考音频对应的文本（可选，空则由 Whisper 自动识别）
            language: 语言，默认 Auto

        Returns:
            ComfyUIGenResult，image_url 字段为音频文件 URL，filename 为音频文件名
        """
        from pathlib import Path

        start = time.time()

        if not text or not text.strip():
            raise ValueError("TTS 文本不能为空")

        # 1. 选择并加载工作流
        workflow_file = (
            "Qwen3+TTS+音色设计.json" if mode == "voice_design"
            else "Qwen3+TTS+音频克隆.json"
        )
        workflow_dir = Path(__file__).parent.parent.parent / "workflows"
        workflow_path = workflow_dir / workflow_file
        if not workflow_path.exists():
            from services.comfyui.config import WORKFLOWS_DIR
            workflow_path = Path(WORKFLOWS_DIR) / workflow_file
        if not workflow_path.exists():
            raise FileNotFoundError(f"TTS 工作流不存在: {workflow_file}")

        with open(workflow_path, "r", encoding="utf-8") as f:
            workflow = json.load(f)

        logger.info(
            f"[ComfyUI-TTS] 加载工作流 | mode={mode} | file={workflow_file} | "
            f"text_len={len(text)} | tag={asset_tag}"
        )

        # 2. 修改节点参数
        if mode == "voice_design":
            # 节点25: JWString - 要合成的文本
            if "25" in workflow:
                workflow["25"]["inputs"]["text"] = text
            # 节点26: PrimitiveStringMultiline - 音色描述
            if "26" in workflow:
                workflow["26"]["inputs"]["value"] = voice_description or "成年女性，温柔亲切，语速适中"
            # 节点22: TDQwen3TTSVoiceDesign - 语言
            if "22" in workflow:
                workflow["22"]["inputs"]["language"] = language
        else:
            # voice_clone 模式
            # 节点31: JWString - 要合成的文本
            if "31" in workflow:
                workflow["31"]["inputs"]["text"] = text
            # 节点27: TDQwen3TTSVoiceClone - 语言
            if "27" in workflow:
                workflow["27"]["inputs"]["language"] = language
            # 节点17: LoadAudio - 参考音频
            if not ref_audio_url:
                raise ValueError("音色克隆模式必须提供 ref_audio_url")
            ref_filename = await self._download_audio_to_input(ref_audio_url)
            if "17" in workflow:
                workflow["17"]["inputs"]["audio"] = ref_filename
                workflow["17"]["inputs"]["audioUI"] = (
                    f"/api/view?filename={ref_filename}&type=input&subfolder=&rand=0.5"
                )

        # 3. 提交工作流
        try:
            prompt_id = await self._queue_prompt_with_retry(workflow)
        except Exception as e:
            logger.error(f"[ComfyUI-TTS] 提交失败 | error={e}")
            raise

        # 4. 等待完成
        output_filenames = await self._wait_for_completion(
            prompt_id, progress_callback, task_type="tts"
        )

        if not output_filenames:
            raise RuntimeError("TTS 生成失败：无输出文件")

        # 5. 找到音频文件（SaveAudio 输出 .flac 或 .wav）
        audio_filename = None
        for fname in output_filenames:
            lower = fname.lower()
            if lower.endswith(('.flac', '.wav', '.mp3', '.ogg', '.m4a')):
                audio_filename = fname
                break
        if not audio_filename:
            audio_filename = output_filenames[0]

        audio_url = f"{self.config.base_url}/view?filename={audio_filename}&type=output"
        elapsed_ms = int((time.time() - start) * 1000)

        logger.info(
            f"[ComfyUI-TTS] 生成完成 | file={audio_filename} | 耗时={elapsed_ms//1000}s"
        )

        return ComfyUIGenResult(
            image_url=audio_url,
            filename=audio_filename,
            images=[audio_url],
            filenames=[audio_filename],
            prompt_id=prompt_id,
            elapsed_ms=elapsed_ms,
            seed=0,
            prompt=text,
        )

    async def _download_audio_to_input(self, url: str) -> str:
        """下载 URL 音频到 ComfyUI input 目录，返回文件名"""
        import aiohttp
        from pathlib import Path
        import hashlib

        if not url:
            return ""

        # 如果是 ComfyUI 内部 URL（/view?filename=xxx），直接提取文件名
        if "/view?" in url and "filename=" in url:
            try:
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(url)
                params = parse_qs(parsed.query)
                fname = params.get("filename", [None])[0]
                if fname:
                    if COMFYUI_DIR:
                        input_dir = Path(COMFYUI_DIR) / "input"
                        if (input_dir / fname).exists():
                            logger.info(f"[ComfyUI-TTS] 参考音频已在 input | file={fname}")
                            return fname
            except Exception:
                pass

        # 外部 URL：下载后上传
        try:
            session = self._get_http_session()
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"下载参考音频失败: HTTP {resp.status}")
                audio_data = await resp.read()

            url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
            suffix = ".flac"
            lower = url.lower().split("?")[0]
            for ext in ['.flac', '.wav', '.mp3', '.ogg', '.m4a']:
                if lower.endswith(ext):
                    suffix = ext
                    break
            filename = f"tts_ref_{url_hash}{suffix}"

            form = aiohttp.FormData()
            form.add_field("image", audio_data, filename=filename, content_type="audio/flac")
            async with session.post(
                f"{self.config.base_url}/upload/image",
                data=form,
            ) as upload_resp:
                if upload_resp.status == 200:
                    result = await upload_resp.json()
                    uploaded_name = result.get("name", filename)
                    logger.info(f"[ComfyUI-TTS] 参考音频已上传 | file={uploaded_name} | size={len(audio_data)//1024}KB")
                    return uploaded_name
                else:
                    err_text = await upload_resp.text()
                    raise RuntimeError(f"上传参考音频失败: {upload_resp.status} {err_text[:200]}")
        except Exception as e:
            logger.error(f"[ComfyUI-TTS] 下载参考音频失败 | url={url} | error={e}")
            raise

    async def _generate_tts_flac(self, text: str, voice: str = "zh-CN-XiaoxiaoNeural", output_dir: Path = None) -> Optional[tuple]:
        """生成TTS语音并转为FLAC格式，返回 (filename, waveform_peaks) 或 None"""
        import uuid
        import subprocess
        
        if not output_dir:
            from services.comfyui.config import COMFYUI_INPUT_DIR
            output_dir = Path(COMFYUI_INPUT_DIR) if COMFYUI_INPUT_DIR else Path("ComfyUI/input")
        
        try:
            import edge_tts
            import tempfile
            
            audio_file = f"tts_{uuid.uuid4().hex[:8]}.flac"
            mp3_path = output_dir / f"_tts_temp_{uuid.uuid4().hex[:8]}.mp3"
            flac_path = output_dir / audio_file
            
            # 生成mp3
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(mp3_path))
            
            # 转flac
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", str(mp3_path), "-c:a", "flac", "-f", "flac", str(flac_path)],
                capture_output=True, timeout=30
            )
            mp3_path.unlink(missing_ok=True)
            
            if not flac_path.exists():
                logger.warning(f"[ComfyUI] TTS flac转换失败")
                return None
            
            # 计算波形峰值
            waveform_peaks = self._compute_waveform_peaks(flac_path)
            
            logger.info(f"[ComfyUI] TTS生成成功 | text={text[:30]}... | file={audio_file}")
            return (audio_file, waveform_peaks)
            
        except Exception as e:
            logger.warning(f"[ComfyUI] TTS生成异常: {e}")
            return None

    def _compute_waveform_peaks(self, flac_path: Path, num_peaks: int = 256) -> list:
        """计算音频波形峰值"""
        import subprocess
        try:
            import numpy as np
            result = subprocess.run(
                ["ffmpeg", "-i", str(flac_path), "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                capture_output=True, timeout=30
            )
            if not result.stdout:
                return [0.0] * num_peaks
            samples = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0
            chunk_size = max(len(samples) // num_peaks, 1)
            peaks = []
            for i in range(num_peaks):
                start = i * chunk_size
                end = min(start + chunk_size, len(samples))
                if start < len(samples):
                    peaks.append(float(np.max(np.abs(samples[start:end]))))
                else:
                    peaks.append(0.0)
            return peaks
        except Exception:
            return [0.0] * num_peaks

    async def _copy_output_to_input(self, comfyui_filename: str) -> str:
        """将 ComfyUI output 目录的图片复制到 input 目录（供 LoadImage 使用）"""
        import aiohttp

        session = self._get_http_session()
        # 1. 从 ComfyUI /view 下载 output 图片
        view_url = f"{self.config.base_url}/view?filename={comfyui_filename}&type=output"
        async with session.get(view_url) as resp:
            if resp.status != 200:
                logger.warning(f"[ComfyUI] 获取 output 图片失败 | status={resp.status} | url={view_url}")
                return ""
            img_data = await resp.read()

        # 2. 上传到 ComfyUI input 目录
        form = aiohttp.FormData()
        form.add_field("image", img_data, filename=comfyui_filename, content_type="image/png")
        async with session.post(
            f"{self.config.base_url}/upload/image",
            data=form,
        ) as upload_resp:
            if upload_resp.status == 200:
                result = await upload_resp.json()
                uploaded_name = result.get("name", comfyui_filename)
                logger.info(f"[ComfyUI] 参考图已上传到 input | src={comfyui_filename} -> input={uploaded_name}")
                return uploaded_name
            else:
                logger.warning(f"[ComfyUI] 上传到 input 失败 | status={upload_resp.status}")
                return ""

    async def _download_to_input(self, url: str) -> str:
        """下载 URL 图片到 ComfyUI input 目录"""
        import aiohttp
        from pathlib import Path
        import hashlib

        # 生成唯一文件名
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        # 从 URL 或 query 参数提取原始文件名
        orig_name = url.split("?filename=")[-1].split("&")[0] if "?filename=" in url else f"ref_{url_hash}.png"
        if not orig_name.endswith((".png", ".jpg", ".jpeg", ".webp")):
            orig_name = f"ref_{url_hash}.png"

        # ComfyUI input 目录
        input_dir = Path(self.config.base_url.replace("http://", "").replace("https://", ""))
        # 通常 ComfyUI input 目录在安装目录下
        # 这里用 API 上传
        try:
            # 如果是本地 URL，直接复制
            if url.startswith("/api/"):
                full_url = f"{self.config.base_url}{url}" if not url.startswith("http") else url
            else:
                full_url = url

            session = self._get_http_session()
            async with session.get(full_url) as resp:
                if resp.status == 200:
                    img_data = await resp.read()
                    # 上传到 ComfyUI input
                    form = aiohttp.FormData()
                    form.add_field("image", img_data, filename=orig_name, content_type="image/png")
                    async with session.post(
                        f"{self.config.base_url}/upload/image",
                        data=form,
                    ) as upload_resp:
                        if upload_resp.status == 200:
                            result = await upload_resp.json()
                            return result.get("name", orig_name)
        except Exception as e:
            logger.warning(f"[ComfyUI] 下载参考图失败: {e}")
        return ""

    async def generate_storyboard(
        self,
        reference_images: Dict[str, str] = None,  # {"character": url, "scene": url, "prop": url}
        prompt_text: str = "",
        seed: Optional[int] = None,
        progress_callback: Optional[Callable] = None,
        character_desc: str = "",
        scene_desc: str = "",
        prop_desc: str = "",
        full_prompt: Optional[str] = None,
        reference_items: Optional[List[Dict[str, str]]] = None,  # 多参考图列表
        reference_labels: Optional[List[Dict[str, str]]] = None,  # 保留接口兼容，不再使用
        shot_id: Optional[str] = None,
        project_id: Optional[str] = None,
        asset_tag: Optional[str] = None,
        enable_resume: bool = False,
        denoise: float = 1.0,   # Fish 融合固定 denoise=1，保留参数兼容接口
        cfg: float = 1.0,       # Fish 融合固定 cfg=1，保留参数兼容接口
        character_count: int = 1,
        fusion_mode: str = "3img",
        previous_shot_url: str = "",
        width: Optional[int] = None,
        height: Optional[int] = None,
        template: Optional[str] = None,  # ⭐ V6.0: 分镜模板类型 (costume_change/multi_frame/panorama/pose_transfer)
        per_frame_prompts: Optional[List[str]] = None,  # ⭐ V6.0: 多帧分镜的每帧提示词
        pose_reference_image: str = "",  # ⭐ V6.0: 姿态迁移的参考图
        **kwargs,  # 透传额外参数到 storyboard_generation_v2
    ) -> ComfyUIGenResult:
        """
        分镜阶段：支持多模板工作流。

        ⭐ V6.0: 根据 template 参数路由到不同模板：
        - "costume_change": 分镜换装（Fish融合, 3图输入）
        - "multi_frame": 多帧分镜（next-scene LoRA, 逐帧生成）
        - "panorama": 全景图（单图输入, 全景视角）
        - "pose_transfer": 姿态迁移（人物图+姿态参考图）
        - None/默认: 兼容旧版 Fish 融合

        Args:
            reference_images: 3张固定参考图 {"character": url, "scene": url, "prop": url}
            prompt_text: 分镜文本指令
            seed: 随机种子
            progress_callback: 进度回调函数
            full_prompt: 直接使用的完整提示词
            reference_items: 多参考图条目列表
            project_id: 项目 ID
            character_count: 角色数量
            fusion_mode: "2img" 两图融合 | "3img" 三图融合
            previous_shot_url: 基于融合图重新生成

        Returns:
            ComfyUIGenResult: 生成结果
        """
        _t0 = time.time()
        logger.info(f"[ComfyUI][分镜] 方法入口 | shot={shot_id} | project={project_id} | fusion={fusion_mode}")
        _mem_log("分镜入口", f"shot={shot_id} project={project_id}")
        self._mark_generation_active()

        # 解析参考图（需要已解析的文件名）
        all_ref_items = reference_items or []
        if not all_ref_items and reference_images:
            for key in ("character", "scene", "prop"):
                url = (reference_images or {}).get(key, "")
                if url:
                    all_ref_items.append({"type": key, "url": url, "name": key, "desc": ""})

        for item in all_ref_items:
            url = item.get("image_url") or item.get("url", "")
            if url:
                resolved = await self._ensure_image_in_input_dir(url, project_id=project_id or "")
                item["resolved"] = resolved
                _mem_log("参考图解析", f"type={item.get('type','?')} resolved={resolved}")

        # 构建 type → filename 映射
        workflow_refs: Dict[str, str] = {}
        for item in all_ref_items:
            resolved = item.get("resolved", "")
            if resolved:
                item_type = item.get("type", "")
                if item_type and item_type not in workflow_refs:
                    workflow_refs[item_type] = resolved
                elif item_type and item_type in workflow_refs:
                    suffix = 2
                    while f"{item_type}{suffix}" in workflow_refs:
                        suffix += 1
                    workflow_refs[f"{item_type}{suffix}"] = resolved

        logger.info(
            f"[ComfyUI] generate_storyboard → template={template or 'Fish融合'}"
            f" | chars={character_count}"
            f" | refs={len(all_ref_items)}, workflow_refs={list(workflow_refs.keys())}"
        )
        return await self.storyboard_generation_v2(
            project_id=project_id or "unknown",
            prompt_text=full_prompt or prompt_text,
            reference_images=workflow_refs,
            reference_items=all_ref_items,
            character_count=character_count,
            seed=seed,
            progress_callback=progress_callback,
            fusion_mode=fusion_mode,
            previous_shot_url=previous_shot_url,
            width=width,
            height=height,
            shot_id=shot_id,
            template=template,
            per_frame_prompts=per_frame_prompts,
            pose_reference_image=pose_reference_image,
            **kwargs,  # 透传额外参数到 build_storyboard_workflow_v2
        )

    # ============================================================
    # ⭐ V5.0: storyboard_generation_v2 — Fish 融合 1 步直出
    # ============================================================

    async def storyboard_generation_v2(
        self,
        project_id: str,
        prompt_text: str,
        reference_images: Dict[str, str],
        reference_items: List[Dict[str, Any]],
        character_count: int = 1,
        seed: Optional[int] = None,
        progress_callback: Optional[Callable] = None,
        fusion_mode: str = "3img",
        previous_shot_url: str = "",
        width: Optional[int] = None,
        height: Optional[int] = None,
        shot_id: Optional[str] = None,
        template: Optional[str] = None,  # ⭐ V6.0: 分镜模板类型
        per_frame_prompts: Optional[List[str]] = None,  # ⭐ V6.0: 多帧分镜每帧提示词
        pose_reference_image: str = "",  # ⭐ V6.0: 姿态迁移参考图
        **kwargs,
    ) -> ComfyUIGenResult:
        """分镜生成：支持多模板

        ⭐ V6.0: 根据 template 参数路由到不同模板：
        - "costume_change": 分镜换装（Fish融合, 3图输入）
        - "multi_frame": 多帧分镜（next-scene LoRA, 逐帧生成）
        - "panorama": 全景图（单图输入, 全景视角）
        - "pose_transfer": 姿态迁移（人物图+姿态参考图）
        - None/默认: 兼容旧版 Fish 融合

        Args:
            project_id: 项目ID
            prompt_text: 分镜指令
            reference_images: 参考图片字典 {"character": fn, "scene": fn, ...}
            reference_items: 参考图条目列表
            character_count: 角色数量
            seed: 随机种子
            progress_callback: 进度回调 (msg, pct) → None
            fusion_mode: "2img" 两图融合 | "3img" 三图融合
            previous_shot_url: 基于融合图重新生成
            width: 图像宽度（可选，覆盖工作流默认值）
            height: 图像高度（可选，覆盖工作流默认值）
            template: 模板类型 (costume_change/multi_frame/panorama/pose_transfer)
            per_frame_prompts: 多帧分镜的每帧提示词列表
            pose_reference_image: 姿态迁移的参考图路径
        """
        import secrets
        from services.structured_logging import get_trace_id

        # 复用全局 trace_id（由 batch_task_service 设置），无则生成临时 id
        trace_id = get_trace_id()[:12] if get_trace_id() else secrets.token_hex(6)
        actual_seed = seed or secrets.randbelow(2**31)
        # ⭐ Fix 3: 分镜阶段入口，重置 sd 计数
        self.reset_generation_count("sd")
        # ⭐ V6.0: 步数由模板决定，默认1步
        total_steps = 1
        step_results: List[StoryboardStepResult] = []
        
        _mem_log("V2入口", f"trace={trace_id} chars={character_count}")

        logger.info(
            f"[StoryboardV2] [{trace_id}] 开始分镜生成"
            f" | chars={character_count}, steps={total_steps}"
            f" | seed={actual_seed}"
            f" | refs={ {k: v for k, v in (reference_images or {}).items()} }"
        )

        try:
            _t0 = time.time()
            all_ref_items = list(reference_items)

            # ═══════════════════════════════════════════════════════════
            # Phase 1: Vision Analysis — ⭐ V3.0 已禁用
            # ═══════════════════════════════════════════════════════════
            # 视觉分析产出被 DeepSeek 转为结构化标签后 Qwen 无法正确解析，
            # 对融合质量无帮助且耗时 1.5~3 分钟（含 ComfyUI 启停）。
            # 改用 V3.0 固定增强提示词（含尺度/透视/光影指令）替代。
            logger.info(
                f"[StoryboardV2] [{trace_id}] ⭐ V3.0 跳过视觉分析阶段"
                f" | 使用固定增强提示词替代"
            )
            if progress_callback:
                progress_callback("⚡ 跳过视觉分析，直接进入融合...", 40)

            # ═══════════════════════════════════════════════════════════
            # 1.4 三视图参考图检测与裁剪（在 Phase-2 之前）
            # ═══════════════════════════════════════════════════════════
            # 根因：三视图参考图作为像素直接输入 Qwen Image Edit 模型，
            # 视觉信号强度远超文本约束 → 必须从像素层面裁剪掉多余面板
            # ⭐ 3视图模板的输入是单张概念图，不需要裁剪
            # ⭐ V3.0 视觉分析已禁用，desc/visual_desc 为空，改为从图片本身检测
            TURNAROUND_PATTERNS = [
                "三张照片拼接", "正面.*侧身.*背面", "正面.*背面.*侧身",
                "不同角度展示", "多视角", "三视图", "三根造型相似",
                "三个视角", "多角度视图", "正面照.*侧面照.*背面照",
                "正面、侧面、背面", "正面、背面、侧面",
            ]
            input_dir = os.path.join(self.config.comfyui_dir, "input")
            cropped_count = 0
            for item in (all_ref_items or []):
                # 3视图模板跳过裁剪
                if template == "3view":
                    continue

                # 优先从 desc/visual_desc 检测（V3.0 已禁用，通常为空）
                desc = (item.get("visual_desc", "") or item.get("desc", "")).lower()
                # fallback: 从 item type/role 检测
                item_type_hint = (item.get("type", "") + " " + item.get("role", "")).lower()
                is_turnaround = any(
                    re.search(pat, desc, re.IGNORECASE)
                    for pat in TURNAROUND_PATTERNS
                )
                # 如果 desc 为空，尝试从图片宽高比检测（三视图通常是宽图）
                if not is_turnaround and not desc:
                    resolved_fn = item.get("resolved", "")
                    if resolved_fn:
                        try:
                            from PIL import Image
                            img_path = os.path.join(input_dir, resolved_fn)
                            if os.path.exists(img_path):
                                with Image.open(img_path) as img:
                                    w, h = img.size
                                    # 三视图拼接图通常宽高比 > 2.5
                                    if w > h * 2.5:
                                        is_turnaround = True
                                        logger.info(
                                            f"[StoryboardV2] [{trace_id}] "
                                            f"检测到宽图(可能为三视图) | {w}x{h} | ratio={w/h:.2f}"
                                        )
                        except Exception as e:
                            logger.debug(f"[StoryboardV2] 图片宽高比检测失败: {e}")

                if not is_turnaround:
                    continue

                item_type = item.get("type", "unknown")
                logger.warning(
                    f"[StoryboardV2] [{trace_id}] ⚠️ 检测到三视图参考图"
                    f" type={item_type}，将裁剪为单视图面板"
                )

                # 查找该 item 对应的已解析文件名
                resolved_fn = item.get("resolved", "")
                if not resolved_fn:
                    # fallback: 遍历 reference_images 找匹配的 type
                    for key, ref_fn in reference_images.items():
                        if key == item_type or key.startswith(item_type):
                            # 验证文件存在
                            test_path = os.path.join(input_dir, ref_fn)
                            if os.path.exists(test_path):
                                resolved_fn = ref_fn
                                break

                if resolved_fn:
                    cropped_fn = _crop_turnaround_to_front_view(
                        input_dir, resolved_fn, trace_id
                    )
                    if cropped_fn:
                        # ⭐ 更新 reference_images（构建工作流时使用）
                        for key, ref_fn in list(reference_images.items()):
                            if ref_fn == resolved_fn:
                                reference_images[key] = cropped_fn
                                logger.info(
                                    f"[StoryboardV2] [{trace_id}] "
                                    f"reference_images['{key}']: {resolved_fn} → {cropped_fn}"
                                )
                                break
                        # ⭐ 更新 item.resolved（后续引用）
                        item["resolved"] = cropped_fn
                        cropped_count += 1
                else:
                    logger.warning(
                        f"[StoryboardV2] [{trace_id}] 无法找到 type={item_type} "
                        f"的已解析文件，跳过裁剪"
                    )

            if cropped_count > 0:
                logger.warning(
                    f"[StoryboardV2] [{trace_id}] ⚠️ 三视图裁剪完成: "
                    f"{cropped_count} 张参考图已替换为单视图面板"
                )
                if progress_callback:
                    progress_callback(
                        f"✂️ 已裁剪 {cropped_count} 张三视图参考（保留正视图面板）...",
                        42,
                    )

            # ═══════════════════════════════════════════════════════════
            # Phase 2: Generation（ComfyUI 独占显存）
            # ═══════════════════════════════════════════════════════════
            # 2. 停止 llama.cpp，释放显存
            logger.info(
                f"[StoryboardV2] [{trace_id}] Phase-2 开始: 图像生成 (ComfyUI 独占)"
            )
            _mem_log("停止llama前", f"trace={trace_id}")
            await self._release_vram_for_comfyui()
            _mem_log("停止llama后", f"trace={trace_id}")
            
            # ⭐ V6.0 优化：如果 ComfyUI 已在运行，直接复用，不重启
            # 旧逻辑：每次都 stop → sleep(5s) → ensure_running(加载模型30-60s)
            # 新逻辑：只在内存不足时才重启，否则直接使用
            comfyui_alive = await self._check_alive()
            _ram_now = _get_ram_pct_safe()
            
            if comfyui_alive and _ram_now < 95:
                # ComfyUI 在运行且内存充足 → 直接复用，跳过重启
                logger.info(
                    f"[StoryboardV2] [{trace_id}] ComfyUI 已在运行且内存充足"
                    f" | RAM={_ram_now:.1f}% | 跳过重启，直接复用"
                )
                _mem_log("ComfyUI复用(跳过重启)", f"trace={trace_id} RAM={_ram_now:.1f}%")
            elif self._process is not None:
                # ComfyUI 在运行但内存紧张(RAM>=95%) → 停止后重启
                logger.info(
                    f"[StoryboardV2] [{trace_id}] 内存紧张 RAM={_ram_now:.1f}%，"
                    f"停止 ComfyUI 释放内存后重启"
                )
                _mem_log("停止ComfyUI前(释放内存)", f"trace={trace_id} RAM={_ram_now:.1f}%")
                await self._close_http_session()
                self.stop()
                await asyncio.sleep(3)
                import gc
                gc.collect()
                await asyncio.sleep(2)
                _ram_after_stop = _get_ram_pct_safe()
                _mem_log("停止ComfyUI后(内存已释放)", f"trace={trace_id} RAM={_ram_after_stop:.1f}% freed={_ram_now - _ram_after_stop:.1f}%")
                logger.info(
                    f"[StoryboardV2] [{trace_id}] ComfyUI 已停止，内存释放"
                    f" | RAM: {_ram_now:.1f}% → {_ram_after_stop:.1f}%"
                    f" | 释放了 {_ram_now - _ram_after_stop:.1f}%"
                )
            
            if progress_callback:
                progress_callback("⚡ 启动生成引擎...", 45)

            # 3. 确保 ComfyUI 运行（如果已在运行则秒级返回）
            _mem_log("启动ComfyUI前", f"trace={trace_id}")
            ready = await self.ensure_running()
            _mem_log("启动ComfyUI后", f"trace={trace_id} ready={ready}")
            if not ready:
                raise RuntimeError(
                    "ComfyUI 不可用。请确保 ComfyUI 在 localhost:8188 运行，"
                    "或设置 COMFYUI_DIR 环境变量让系统自动启动。"
                )
            logger.info(
                f"[StoryboardV2] [{trace_id}] ComfyUI 就绪，开始生成工作流"
                f" | RAM={_get_ram_pct_safe():.1f}%"
            )
            # ⭐ 超分模板使用 SeedVR2 模型（sd 族），不需要 qwen 清理
            # 三视图/其他模板使用 Qwen Image Edit 模型（qwen 族）
            # 提取类(姿态/线稿/深度图/三合一)和超分模板使用 sd 模型族，其他使用 qwen
            _sd_templates = {"upscale", "pose_extraction", "lineart_extraction", "depth_map", "extract_all"}
            await self._ensure_clean_state("sd" if template in _sd_templates else "qwen")

            # 1.5 DeepSeek 优化提示词 — ⭐ V5.0 永久禁用
            # V6.0 模板系统直接使用用户 prompt
            optimized_prompt = prompt_text
            logger.info(
                f"[StoryboardV2] [{trace_id}] ⭐ V6.0: 直接使用用户提示词"
                f"（长度={len(prompt_text)}）| template={template}"
            )
            if progress_callback:
                progress_callback("⚡ 分镜生成: 使用用户提示词...", 48)

            # 2. 构建工作流列表
            from services.workflow_builder import build_storyboard_workflow_v2
            logger.info(f"[StoryboardV2] [{trace_id}] 构建工作流 | template={template} | refs={reference_images}")
            workflows, step_names, metadata = build_storyboard_workflow_v2(
                reference_images=reference_images,
                prompt_text=optimized_prompt,
                seed=actual_seed,
                filename_prefix=kwargs.pop("filename_prefix", f"{project_id[-6:]}_storyboard"),
                character_count=character_count,
                fusion_mode=fusion_mode,
                previous_shot_url=previous_shot_url,
                width=width,
                height=height,
                template=template,
                per_frame_prompts=per_frame_prompts,
                pose_reference_image=pose_reference_image,
                **kwargs,  # 透传额外参数到 build_storyboard_workflow_v2
            )

            # ⭐ V5.0: 记录每个 step 的 Fish 融合节点10/11/12初始赋值
            for si, (wf, sname) in enumerate(zip(workflows, step_names)):
                # 诊断日志：读取 LoadImage 和 TextEncode 节点的参数
                from services.workflow_builder import find_node_by_class_type, find_first_node_by_class_type_contains
                _diag_loads = find_node_by_class_type(wf, 'LoadImage') if isinstance(wf, dict) else []
                _diag_loads.sort(key=lambda x: x[0])
                node10 = _diag_loads[0][1]['inputs'].get('image', 'N/A') if len(_diag_loads) >= 1 else 'N/A'
                node11 = _diag_loads[1][1]['inputs'].get('image', 'N/A') if len(_diag_loads) >= 2 else 'N/A'
                node12 = _diag_loads[2][1]['inputs'].get('image', 'N/A') if len(_diag_loads) >= 3 else 'N/A'
                node22_prompt = ""
                _nid_enc, _ndata_enc = find_first_node_by_class_type_contains(wf, 'QwenImageEditPlusAdvance') if isinstance(wf, dict) else (None, None)
                if _nid_enc and _ndata_enc:
                    node22_prompt = _ndata_enc.get('inputs', {}).get('prompt', '')[:60]
                logger.info(
                    f"[StoryboardV2] [{trace_id}] Step{si+1} '{sname}' 初始赋值: "
                    f"10(图1/角色)={node10}, 11(图2/场景)={node11}, 12(图3/道具)={node12}"
                    f" | prompt={node22_prompt}..."
                )


            # 3. 逐步骤执行（进度: 50%~100%）
            step_count = len(workflows)
            current_image = None
            all_filenames: List[str] = []  # ⭐ V6.0: 收集所有步骤的输出文件名

            logger.info(
                f"[StoryboardV2] [{trace_id}] 开始逐步骤执行"
                f" | total_steps={step_count}"
                f" | step_names={step_names}"
            )

            # ⭐ 系统RAM安全检查：防止OOM导致ComfyUI崩溃
            sys_ram = self._get_system_memory_usage()
            if sys_ram > 95:
                # ⭐ 先尝试 GC + 等待内存释放，而不是直接放弃
                logger.warning(
                    f"[StoryboardV2] [{trace_id}] 系统RAM使用率 {sys_ram:.1f}% 超过95%，"
                    f"尝试GC回收 + 等待内存释放..."
                )
                import gc
                gc.collect()
                self.clear_image_cache()
                # 等待最多30秒让内存释放
                for _wait_i in range(6):
                    await asyncio.sleep(5)
                    sys_ram = self._get_system_memory_usage()
                    logger.info(f"[StoryboardV2] [{trace_id}] 等待内存释放... RAM={sys_ram:.1f}%")
                    if sys_ram <= 92:
                        break
                if sys_ram > 95:
                    # ⭐ GC 无效时，尝试重启 ComfyUI 释放显存+内存（比直接放弃更好）
                    logger.warning(
                        f"[StoryboardV2] [{trace_id}] GC 后内存仍为 {sys_ram:.1f}%，"
                        f"尝试重启 ComfyUI 释放资源..."
                    )
                    try:
                        await self._close_http_session()
                        self.stop()
                        await self.ensure_running()
                        await asyncio.sleep(3)
                        gc.collect()
                        sys_ram = self._get_system_memory_usage()
                        logger.info(f"[StoryboardV2] [{trace_id}] 重启后内存 RAM={sys_ram:.1f}%")
                        if sys_ram > 95:
                            logger.critical(
                                f"[StoryboardV2] [{trace_id}] 重启后内存仍为 {sys_ram:.1f}%，放弃分镜生成"
                            )
                            self._mark_generation_complete()
                            raise RuntimeError(
                                f"系统内存不足（{sys_ram:.1f}%），无法执行分镜生成。"
                                f"请关闭其他程序后重试。"
                            )
                    except RuntimeError:
                        raise
                    except Exception as _restart_err:
                        logger.error(f"[StoryboardV2] [{trace_id}] 重启失败: {_restart_err}")
                        self._mark_generation_complete()
                        raise RuntimeError(
                            f"系统内存不足（{sys_ram:.1f}%），重启 ComfyUI 失败。"
                            f"请关闭其他程序后重试。"
                        )
            elif sys_ram > 90:
                logger.warning(
                    f"[StoryboardV2] [{trace_id}] 系统RAM使用率较高 ({sys_ram:.1f}%)，"
                    f"执行gc回收 + 清理缓存"
                )
                import gc
                gc.collect()
                self.clear_image_cache()


            for i, (wf, step_name) in enumerate(zip(workflows, step_names), 1):
                ng_start, ng_end = _get_step_progress_range(i, step_count)

                if progress_callback:
                    progress_callback(
                        f"🔄 Step{i}/{step_count}: {step_name} (denoise=1.0, cfg=1.0)",
                        ng_start,
                    )

                step_start = time.time()
                _mem_log(f"Step{i}开始", f"trace={trace_id} name={step_name}")

                # ⭐ Step1 融合步骤 — 记录初始节点文件状态
                if i == 1:
                    try:
                        _input_dir = os.path.join(self.config.comfyui_dir, "input")
                        _s1_parts = []
                        for _nid in ("10", "11", "12"):
                            _fname = wf.get(_nid, {}).get("inputs", {}).get("image", "")
                            if isinstance(_fname, str) and _fname:
                                _fpath = os.path.join(_input_dir, _fname)
                                _exists = os.path.exists(_fpath)
                                _sz = os.path.getsize(_fpath) if _exists else 0
                                _s1_parts.append(f"节点{_nid}={_fname}(存在={_exists},大小={_sz}B)")
                        logger.info(
                            f"[StoryboardV2] [{trace_id}] Step{i} 融合步骤 初始文件: " + ", ".join(_s1_parts)
                        )
                    except Exception as diag_err:
                        logger.warning(f"[StoryboardV2] [{trace_id}] Step{i} 诊断记录异常(非致命): {diag_err}")

                # ⭐ 多步骤时：前一步产物作为输入，但保留场景参考
                # ⭐ BUG#7 修复：分层渲染的 A/B 组是独立工作流，不应链式注入
                if current_image and i > 1 and template != "layered_render":
                    # 保存场景文件名，更新工作流后恢复
                    from services.workflow_builder import find_node_by_class_type
                    load_nodes = find_node_by_class_type(wf, 'LoadImage')
                    load_nodes.sort(key=lambda x: x[0])
                    scene_file = ""
                    if len(load_nodes) >= 2:
                        scene_file = wf[load_nodes[1][0]]['inputs'].get('image', '')
                    _update_workflow_input(wf, current_image, task_id=trace_id)
                    if scene_file and len(load_nodes) >= 2:
                        wf[load_nodes[1][0]]['inputs']['image'] = scene_file
                    # ⭐ 诊断：检查文件状态
                    input_dir = os.path.join(self.config.comfyui_dir, "input")
                    def _check_file(fname):
                        if not fname:
                            return (False, 0)
                        path = os.path.join(input_dir, fname)
                        if os.path.exists(path):
                            return (True, os.path.getsize(path))
                        return (False, 0)
                    scene_ok, scene_sz = _check_file(scene_file)
                    char_file = wf.get("10", {}).get("inputs", {}).get("image", "")
                    char_ok, char_sz = _check_file(char_file)
                    cur_ok, cur_sz = _check_file(current_image) if current_image else (False, 0)
                    logger.info(
                        f"[StoryboardV2] [{trace_id}] Step{i} 融合步骤 文件状态: "
                        f"节点11(场景)={scene_file} 存在={scene_ok} 大小={scene_sz}B, "
                        f"节点10(角色)={char_file} 存在={char_ok} 大小={char_sz}B, "
                        f"current_image={current_image} 存在={cur_ok} 大小={cur_sz}B"
                    )

                # ⭐ 提交前记录关键节点参数
                try:
                    _nid_enc2, _ndata_enc2 = find_first_node_by_class_type_contains(wf, 'QwenImageEditPlusAdvance') if isinstance(wf, dict) else (None, None)
                    if _nid_enc2 and _ndata_enc2:
                        _p = _ndata_enc2.get('inputs', {}).get('prompt', '')
                        logger.info(f"[StoryboardV2] [{trace_id}] Step{i} 节点{_nid_enc2}(prompt)={_p[:80]}")
                    # 查找 KSampler 节点（Fish 模板为节点6，通用查找）
                    for _ks_nid, _ks_node in wf.items():
                        if isinstance(_ks_node, dict) and _ks_node.get("class_type") == "KSampler":
                            _ks = _ks_node["inputs"]
                            logger.info(
                                f"[StoryboardV2] [{trace_id}] Step{i} 节点{_ks_nid}(KSampler)="
                                f"denoise={_ks.get('denoise')}, cfg={_ks.get('cfg')}, steps={_ks.get('steps')}, "
                                f"sampler={_ks.get('sampler_name')}, scheduler={_ks.get('scheduler')}"
                            )
                            break
                    # ⭐ 提交前重新检查所有输入图文件状态
                    _input_dir = os.path.join(self.config.comfyui_dir, "input")
                    _precheck_parts = []
                    for _nid in ("10", "11", "12"):
                        _fname = wf.get(_nid, {}).get("inputs", {}).get("image", "")
                        if isinstance(_fname, str) and _fname:
                            _fpath = os.path.join(_input_dir, _fname)
                            _exists = os.path.exists(_fpath)
                            _sz = os.path.getsize(_fpath) if _exists else 0
                            _precheck_parts.append(f"节点{_nid}={_fname}(存在={_exists},大小={_sz}B)")
                    logger.info(
                        f"[StoryboardV2] [{trace_id}] Step{i} 提交前图片检查: " + ", ".join(_precheck_parts)
                    )
                except Exception as diag_err:
                    logger.warning(f"[StoryboardV2] [{trace_id}] Step{i} 提交前诊断异常(非致命): {diag_err}")

                # 步骤执行（单次，重试由 DagExecutor._run_single_step 统一控制）
                logger.info(f"[StoryboardV2] [{trace_id}] Step{i} 准备提交工作流 | template={template} | nodes={len(wf)} | wf_type={type(wf).__name__}")
                _mem_log(f"Step{i}提交前", f"trace={trace_id} step={step_name}")
                try:
                    prompt_id = await self._queue_prompt_with_retry(wf)
                    filenames = await self._wait_for_completion(
                        prompt_id,
                        progress_callback=(
                            lambda msg, pct: progress_callback(
                                msg,
                                int(ng_start + (pct * (ng_end - ng_start)) / 100),
                            )
                        ) if progress_callback else None,
                        task_type='storyboard',
                    )

                    if not filenames:
                        raise RuntimeError(f"Step{i} {step_name} 无输出文件")

                    # 3视图/全景图模板有多个SaveImage（中间图+最终拼接），取最终拼接图为主图
                    if template == "panorama":
                        # 记录所有输出文件，方便排查拼接图缺失问题
                        logger.info(
                            f"[StoryboardV2] [{trace_id}] 全景图输出文件列表: {filenames}"
                        )
                        if len(filenames) > 1:
                            final_files = [f for f in filenames if os.path.basename(f).startswith("panorama_final_")]
                            if final_files:
                                current_image = final_files[0]
                                logger.info(f"[StoryboardV2] [{trace_id}] 全景图选择最终拼接 | file={current_image}")
                            else:
                                current_image = filenames[-1]
                                logger.warning(
                                    f"[StoryboardV2] [{trace_id}] 全景图未找到panorama_final_文件，"
                                    f"回退到最后一个输出 | file={current_image}"
                                )
                        else:
                            current_image = filenames[0]
                            logger.warning(
                                f"[StoryboardV2] [{trace_id}] 全景图仅有1个输出文件，"
                                f"拼接节点可能未执行 | file={current_image}"
                            )
                    elif template == "3view" and len(filenames) > 1:
                        current_image = filenames[-1]
                    else:
                        current_image = filenames[0]
                    all_filenames.extend(filenames)  # ⭐ V6.0: 收集所有输出文件
                    step_elapsed = int((time.time() - step_start) * 1000)
                    _mem_log(f"Step{i}完成", f"trace={trace_id} file={current_image} elapsed={step_elapsed}ms")
                    step_results.append(StoryboardStepResult(
                        step_index=i, step_name=step_name,
                        filename=current_image, elapsed_ms=step_elapsed,
                    ))

                    # 持久化中间结果
                    await self._save_step_intermediate(
                        project_id=project_id,
                        trace_id=trace_id,
                        step_index=i,
                        step_name=step_name,
                        image_filename=current_image,
                        metadata={
                            "elapsed_ms": step_elapsed,
                            "denoise": 1.0,
                            "cfg": 1.0,
                        },
                    )

                    # 检查输出文件大小
                    _output_path = os.path.join(
                        self.config.output_dir, current_image
                    ) if current_image else ""
                    _output_sz = os.path.getsize(_output_path) if _output_path and os.path.exists(_output_path) else 0
                    logger.info(
                        f"[StoryboardV2] [{trace_id}] Step{i}/{step_count} 完成"
                        f" | {step_name} | elapsed={step_elapsed}ms"
                        f" | file={current_image} | 大小={_output_sz}B"
                    )
                    if progress_callback:
                        progress_callback(f"✅ Step{i}/{step_count}: {step_name} 完成", ng_end)
                except Exception as step_err:
                    logger.error(
                        f"[StoryboardV2] [{trace_id}] Step{i} 失败"
                        f" | error={step_err}（重试由 DagExecutor 控制）"
                    )
                    raise

                # ⭐ V1.4: 步骤间释放显存 + VRAM 检查（防 Qwen 模型 OOM）
                if current_image and i < step_count:
                    vram_before = await self._get_vram_usage()
                    await self._quick_release_vram()
                    vram_after = await self._get_vram_usage()
                    logger.info(
                        f"[StoryboardV2] [{trace_id}] Step{i} VRAM: "
                        f"{vram_before:.1f}%→{vram_after:.1f}%"
                    )
                    # ⭐ V1.6: 累积保护 — 每 3 步或 VRAM > 85% 时执行深度清理（重启 ComfyUI）
                    VRAM_CRITICAL = 85
                    CLEANUP_INTERVAL_STEPS = 3
                    if vram_after > VRAM_CRITICAL or (i > 0 and i % CLEANUP_INTERVAL_STEPS == 0):
                        logger.warning(
                            f"[StoryboardV2] [{trace_id}] 触发深度VRAM清理"
                            f" | step={i}, vram={vram_after:.1f}%"
                            + (f", 超过{VRAM_CRITICAL}%阈值" if vram_after > VRAM_CRITICAL else f", 每{CLEANUP_INTERVAL_STEPS}步例行")
                        )
                        if progress_callback:
                            progress_callback(
                                f"🔄 深度清理显存 ({vram_after:.0f}%)，重启引擎...",
                                ng_end,
                            )
                        # 深度清理：重启 ComfyUI 彻底释放 VRAM
                        await self._notify_restart("restarting", 20)
                        await self._close_http_session()
                        self.stop()
                        # ⭐ 等待进程完全退出，内存释放后再启动
                        await asyncio.sleep(3)
                        import gc as _gc
                        _gc.collect()
                        await asyncio.sleep(2)
                        _mem_log(f"Step{i}深度清理后", f"trace={trace_id}")
                        await self.ensure_running()
                        await self._notify_restart("ready", 0)
                        vram_after = await self._get_vram_usage()
                        logger.info(
                            f"[StoryboardV2] [{trace_id}] 深度清理完成"
                            f" | vram={vram_after:.1f}%"
                        )
                    elif vram_after > 85:
                        logger.warning(
                            f"[StoryboardV2] [{trace_id}] ⚠️ VRAM 偏高（{vram_after:.1f}%），"
                            f"Qwen 模型可能存在 OOM 风险 | step={i}"
                        )
                        if progress_callback:
                            progress_callback(
                                f"⚠️ 显存占用 {vram_after:.0f}%，OOM 风险...",
                                ng_end,
                            )
                    elif progress_callback:
                        progress_callback(
                            f"🧹 显存 {vram_before:.0f}%→{vram_after:.0f}%，准备下一步...",
                            ng_end,
                        )

                # ⭐ 刷新活跃时间，防止长时间多步生成中空闲定时器误杀
                self._last_used = time.time()

            # 4. 构建输出
            image_url = f"/api/comfyui/image?filename={current_image}"

            # 构建 enriched_ref_items
            enriched_ref_items = []
            for item in all_ref_items:
                enriched_ref_items.append({
                    "type": item.get("type", ""),
                    "url": item.get("image_url") or item.get("url", ""),
                    "name": item.get("name", ""),
                    "desc": item.get("desc", ""),
                    "visual_desc": item.get("visual_desc", ""),
                })

            # ⭐ 标记生成完成，开始空闲定时器
            self._mark_generation_complete()

            # ⭐ 生成完成后立即清理内存缓存和临时对象，防止多帧累积
            self.clear_image_cache()
            import gc
            gc.collect()
            _ram_after_gc = self._get_system_memory_usage()
            logger.info(
                f"[ComfyUI][分镜] 方法完成 | shot={shot_id}"
                f" | total_elapsed={time.time()-_t0:.1f}s | steps={len(step_results)}"
                f" | RAM_after_gc={_ram_after_gc:.1f}%"
            )

            # ⭐ V6.0: 从 all_filenames 构建多图 URL 列表
            all_image_urls = [
                f"/api/comfyui/image?filename={fn}" for fn in all_filenames
            ] if all_filenames else ([image_url] if image_url else [])

            return ComfyUIGenResult(
                image_url=image_url,
                filename=current_image,
                images=all_image_urls,  # ⭐ V6.0: 所有输出图片（多帧分镜/全景多角度）
                filenames=all_filenames,  # ⭐ V6.0: 所有输出文件名
                prompt_id=trace_id,
                elapsed_ms=sum(sr.elapsed_ms for sr in step_results),
                seed=actual_seed,
                prompt=optimized_prompt,
                prompt_sections={"fusion": optimized_prompt},
                ref_items=enriched_ref_items,
            )

        except Exception as e:
            self._mark_generation_complete()
            # ⭐ 异常时也清理缓存，防止内存泄漏
            self.clear_image_cache()
            import gc
            gc.collect()
            logger.error(
                f"[StoryboardV2] [{trace_id}] 分镜生成失败"
                f" | error={e} | completed_steps={len(step_results)}"
            )
            raise

    # ═══════════════════════════════════════════════════════════════════
    # ⭐ V6.0: 全景图/姿态迁移统一走 generate_storyboard(template=...) 路由
    # 不再提供独立的 generate_panorama / generate_pose_transfer 方法
    # 调用方式：generate_storyboard(template="panorama") 或 generate_storyboard(template="pose_transfer")
    # ═══════════════════════════════════════════════════════════════════

    # ═══════════════════════════════════════════════════════════════════
    # ⭐ V1.5 Phase4: 批量生成分镜
    # ═══════════════════════════════════════════════════════════════════

    async def batch_generate_storyboard(
        self,
        project_id: str,
        shots: List[Dict[str, Any]],
        reference_images: Dict[str, str],
        reference_items: List[Dict[str, Any]],
        character_count: int = 1,
        preset_name: Optional[str] = None,
        seed: Optional[int] = None,
        progress_callback: Optional[Callable] = None,
        **kwargs,
    ) -> List[ComfyUIGenResult]:
        """批量生成多个分镜帧

        ⭐ V5.0: Fish 融合 1 步直出，先统一预分析参考图，再逐帧生成。

        Args:
            project_id: 项目ID
            shots: 分镜列表，每个元素 {"name": str, "prompt": str, "character_ids": [...], ...}
            reference_images: 共享参考图字典
            reference_items: 参考图条目列表
            character_count: 角色数量
            preset_name: 预设名称（保留接口兼容，V5.0 不使用）
            seed: 随机种子
            progress_callback: 全局进度回调 (msg, pct) → None

        Returns:
            每帧的生成结果列表
        """
        total_shots = len(shots)
        results: List[ComfyUIGenResult] = []

        logger.info(
            f"[StoryboardBatch] 批量生成开始 | shots={total_shots}"
            f" | chars={character_count}, preset={preset_name}"
        )

        # ═══════════════════════════════════════════════════════════════
        # ⭐ Phase 0: 统一预分析所有参考图（一次 Vision，N 帧复用）
        # ═══════════════════════════════════════════════════════════════
        # 合并共享参考图 + 各分镜独立参考图（去重）
        unique_refs = _collect_all_reference_urls(
            reference_items=reference_items,
            shots=shots,
        )

        if progress_callback:
            try:
                progress_callback(
                    f"🔍 批量预分析: {len(unique_refs)} 张参考图 (缓存优先)...",
                    0,
                )
            except Exception:
                pass

        # 统一预分析（自动缓存 + 崩溃恢复）
        # 注意：此调用会停止 ComfyUI（如运行中），分析后不自动重启
        # storyboard_generation_v2 会在 Phase 2 自动启动 ComfyUI
        await self._pre_analyze_references(
            unique_refs,
            project_id=project_id,
            progress_callback=(
                lambda msg, pct: progress_callback(
                    f"🔍 {msg}", max(0, min(5, int(pct * 0.05)))
                ) if progress_callback else None
            ),
        )

        # 将分析结果同步回原始 reference_items（供 storyboard_generation_v2 复用）
        # unique_refs 和 reference_items 可能指向不同 dict 对象，需要同步 visual_desc
        _sync_map: Dict[str, str] = {}
        for ref in unique_refs:
            url = ref.get("image_url") or ref.get("url", "") or ""
            vd = ref.get("visual_desc", "")
            if url and vd:
                _sync_map[url] = vd

        for ref in reference_items:
            url = ref.get("image_url") or ref.get("url", "") or ""
            if url and url in _sync_map and not ref.get("visual_desc"):
                ref["visual_desc"] = _sync_map[url]

        cached_count = sum(1 for r in unique_refs if r.get("visual_desc"))
        if progress_callback:
            try:
                progress_callback(
                    f"✅ 参考图预分析完成 ({cached_count}/{len(unique_refs)})，开始批量生成...",
                    5,
                )
            except Exception:
                pass

        logger.info(
            f"[StoryboardBatch] 预分析完成 | analyzed={cached_count}/{len(unique_refs)}"
            f" | cache_synced={len(_sync_map)}"
        )

        # ═══════════════════════════════════════════════════════════════
        # Phase 1-N: 逐帧生成（复用预分析的 visual_desc）
        # ═══════════════════════════════════════════════════════════════
        for shot_idx, shot in enumerate(shots):
            shot_name = shot.get("name", f"分镜{shot_idx+1}")
            shot_prompt = shot.get("prompt", "")

            if progress_callback:
                batch_pct = int((shot_idx / total_shots) * 100)
                progress_callback(f"🎬 批量生成 ({shot_idx+1}/{total_shots}): {shot_name}", batch_pct)

            # ⭐ 每帧生成前标记活跃状态
            self._mark_generation_active()

            shot_kwargs = {
                "project_id": project_id,
                "prompt_text": shot_prompt,
                "reference_images": reference_images,
                "reference_items": reference_items,
                "character_count": character_count,
                "seed": seed + shot_idx if seed else None,
            }
            # ⭐ V6.0: 传递模板参数
            if shot.get("template"):
                shot_kwargs["template"] = shot["template"]
            if shot.get("per_frame_prompts"):
                shot_kwargs["per_frame_prompts"] = shot["per_frame_prompts"]
            if shot.get("pose_reference_image"):
                shot_kwargs["pose_reference_image"] = shot["pose_reference_image"]

            result = await self.storyboard_generation_v2(
                **shot_kwargs,
                progress_callback=(
                    lambda msg, pct: progress_callback(
                        f"({shot_idx+1}/{total_shots}) {msg}",
                        int((shot_idx * 100 + pct) / total_shots),
                    )
                ) if progress_callback else None,
                **kwargs,
            )
            results.append(result)
            logger.info(f"[StoryboardBatch] {shot_name} 完成 | file={result.filename}")

        if progress_callback:
            progress_callback(f"✅ 批量生成完成 ({total_shots} 帧)", 100)

        logger.info(f"[StoryboardBatch] 批量生成完成 | total_shots={total_shots}")
        return results

    # ═══════════════════════════════════════════════════════════════════
    # ⭐ V1.5 Phase5: L3 流程级自动恢复 + 中间结果持久化
    # ═══════════════════════════════════════════════════════════════════

    def _get_intermediates_dir(self, project_id: str, trace_id: str) -> Path:
        """获取中间结果保存目录"""
        intermediates_dir = (
            Path(__file__).parent.parent.parent
            / "data"
            / "storyboard_intermediates"
            / project_id[-8:]
            / trace_id
        )
        intermediates_dir.mkdir(parents=True, exist_ok=True)
        return intermediates_dir

    async def _save_step_intermediate(
        self,
        project_id: str,
        trace_id: str,
        step_index: int,
        step_name: str,
        image_filename: str,
        metadata: Dict[str, Any],
    ) -> str:
        """持久化单步中间结果到磁盘
        
        Args:
            project_id: 项目ID
            trace_id: 本次生成追踪ID
            step_index: 步骤序号
            step_name: 步骤显示名
            image_filename: ComfyUI 输出的图片文件名
            metadata: 步骤元数据 (denoise, cfg, elapsed_ms 等)
        
        Returns:
            保存的中间文件路径
        """
        intermediates_dir = self._get_intermediates_dir(project_id, trace_id)

        # 保存元数据 JSON
        meta_path = intermediates_dir / f"step{step_index:02d}_{step_name}.json"
        meta_data = {
            "step_index": step_index,
            "step_name": step_name,
            "image_filename": image_filename,
            "timestamp": time.time(),
            **metadata,
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta_data, f, ensure_ascii=False, indent=2)

        # 拷贝图片文件（如果存在）
        comfyui_input = Path(self.config.output_dir) if hasattr(self.config, "output_dir") else Path("comfyui/output")
        src_path = comfyui_input / image_filename if not Path(image_filename).is_absolute() else Path(image_filename)
        dst_path = intermediates_dir / f"step{step_index:02d}_{step_name}.png"
        if src_path.exists():
            shutil.copy2(src_path, dst_path)
            logger.debug(f"[Intermediates] 已保存: {dst_path}")
        else:
            logger.warning(f"[Intermediates] 源文件不存在: {src_path}")

        return str(dst_path)

    async def _resume_from_checkpoint(
        self,
        project_id: str,
        trace_id: str,
        total_steps: int,
    ) -> Tuple[Optional[str], List[StoryboardStepResult], int]:
        """从断点恢复生成流程
        
        检查中间目录中已完成的步骤，返回最后完成的图片和结果列表。
        
        Args:
            project_id: 项目ID
            trace_id: 本次生成追踪ID
            total_steps: 总步骤数
        
        Returns:
            (last_image: Optional[str], completed_results: List[StoryboardStepResult], resume_from: int)
            resume_from = 0 表示从头开始（无检查点）
        """
        intermediates_dir = self._get_intermediates_dir(project_id, trace_id)
        if not intermediates_dir.exists():
            return None, [], 0

        # 扫描已完成的步骤
        completed_steps: List[StoryboardStepResult] = []
        last_image = None
        max_completed = 0

        for step_idx in range(1, total_steps + 1):
            meta_files = list(intermediates_dir.glob(f"step{step_idx:02d}_*.json"))
            if meta_files:
                with open(meta_files[0], "r", encoding="utf-8") as f:
                    meta = json.load(f)
                completed_steps.append(StoryboardStepResult(
                    step_index=meta["step_index"],
                    step_name=meta["step_name"],
                    filename=meta["image_filename"],
                    elapsed_ms=meta.get("elapsed_ms", 0),
                ))
                last_image = meta["image_filename"]
                max_completed = max(max_completed, step_idx)

        if max_completed > 0:
            logger.info(
                f"[Resume] [{trace_id}] 发现检查点: {max_completed}/{total_steps} 步已完成"
                f" | last_image={last_image}"
            )

        return last_image, completed_steps, max_completed

    async def _queue_prompt_with_retry(self, workflow: dict) -> str:
        """提交工作流到 ComfyUI（带并发控制信号量，最多 2 个并发生成）"""
        async with self._semaphore:
            logger.debug(f"[ComfyUI] 获取并发生成许可")
            return await self._queue_prompt_with_retry_impl(workflow)

    async def _queue_prompt_with_retry_impl(self, workflow: dict) -> str:
        """提交工作流到 ComfyUI（先快速释放显存，再检查队列，3 次重试）"""
        _mem_log("提交工作流前", f"nodes={len(workflow)}")
        # 每次提交前快速释放显存
        try:
            await self._quick_release_vram()
        except Exception:
            pass
        
        # 提交前检查队列是否有堆积
        try:
            q_session = self._get_http_session()
            async with q_session.get(
                f"{self.config.base_url}/queue",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    running = len(data.get("queue_running", []))
                    pending = len(data.get("queue_pending", []))
                    if pending > 2:
                        logger.warning(
                            f"[ComfyUI] 队列堆积 ({pending} pending, {running} running)，"
                            "清空中..."
                        )
                        async with q_session.post(
                            f"{self.config.base_url}/queue",
                            json={"clear": True},
                        ) as clear_resp:
                            pass
        except Exception:
            pass

        last_error = None
        for attempt in range(3):
            try:
                return await self._queue_prompt(workflow)
            except (aiohttp.ClientConnectorError, ConnectionRefusedError) as e:
                last_error = e
                wait = 2 ** attempt
                logger.warning(
                    f"[ComfyUI] 连接失败 (attempt {attempt + 1}), "
                    f"{wait}s 后重试: {e}"
                )
                await asyncio.sleep(wait)
                # 重试前再检查一下 ComfyUI
                if not await self._check_alive():
                    await self.ensure_running()
            except RuntimeError as e:
                last_error = e
                if attempt < 2:
                    wait = 2 ** attempt
                    logger.warning(f"[ComfyUI] 提交失败 (attempt {attempt + 1}), {wait}s 后重试: {e}")
                    await asyncio.sleep(wait)
                    # 检测 HTTP 500 错误，强制重启恢复内部状态
                    if "500" in str(e) or "Server got itself" in str(e):
                        logger.error("[ComfyUI] 检测到 HTTP 500 错误，强制重启恢复...")
                        await self._close_http_session()
                        self.stop()
                        await self.ensure_running()
                    elif not await self._check_alive():
                        await self.ensure_running()
                else:
                    raise
        last_msg = str(last_error) if last_error else "未知错误"
        raise RuntimeError(
            f"ComfyUI 提交失败（已重试3次）: {last_msg}"
        )

    @staticmethod
    def _strip_workflow_meta(workflow: dict) -> dict:
        """深拷贝工作流并剥离可能引起自定义节点崩溃的 _meta / _comment 字段
        
        同时剥离工作流顶层的非节点键（如 _meta、_comment），
        避免被 ComfyUI 当作节点 ID 解析导致 missing_node_type 错误。
        """
        cleaned = {}
        for nid, ndata in workflow.items():
            # 跳过顶层非节点键（以下划线开头且值不是标准节点 dict）
            if nid.startswith("_") and not (isinstance(ndata, dict) and "class_type" in ndata):
                continue
            if not isinstance(ndata, dict):
                cleaned[nid] = ndata
                continue
            cleaned[nid] = {k: v for k, v in ndata.items() if k not in ("_meta", "_comment")}
        return cleaned

    async def _queue_prompt(self, workflow: dict) -> str:
        """提交工作流到 ComfyUI"""
        # 剥离 _meta / _comment（某些自定义节点遇到这些字段会崩溃）
        workflow = self._strip_workflow_meta(workflow)

        # 提交前检查关键节点的图片值（Fish 融合模板节点11 = 场景槽位）
        if "11" in workflow:
            _img = workflow["11"].get("inputs", {}).get("image", "")
            logger.info(f"[ComfyUI] 提交前节点11(场景)图片: {_img}")
        payload = {"prompt": workflow}

        session = self._get_http_session()
        async with session.post(
            f"{self.config.base_url}/prompt",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error(f"[ComfyUI] 提交失败 | status={resp.status} | body={text[:500]}")
                raise RuntimeError(
                    f"ComfyUI 提交失败 ({resp.status}): {text[:300]}"
                )
            data = await resp.json()
            # 检查 node_errors（ComfyUI 验证失败时返回 200 但包含 node_errors）
            node_errors = data.get("node_errors", {})
            if node_errors:
                error_details = []
                for nid, errs in node_errors.items():
                    if isinstance(errs, dict) and "errors" in errs:
                        for e in errs["errors"]:
                            msg = e.get("message", str(e)) if isinstance(e, dict) else str(e)
                            error_details.append(f"node {nid}: {msg}")
                    else:
                        error_details.append(f"node {nid}: {errs}")
                error_summary = "; ".join(error_details[:5])
                logger.error(f"[ComfyUI] 工作流验证失败 | node_errors={node_errors}")
                raise RuntimeError(f"ComfyUI 工作流验证失败: {error_summary}")
            prompt_id = data.get("prompt_id", "")
            if not prompt_id:
                logger.error(f"[ComfyUI] 提交返回空 prompt_id | data={data}")
                raise RuntimeError(f"ComfyUI 提交返回空 prompt_id: {str(data)[:300]}")
            logger.info(f"[ComfyUI] 工作流已提交 | prompt_id={prompt_id}")
            return prompt_id

    async def _wait_for_completion(
        self, prompt_id: str, progress_callback: Optional[callable] = None,
        task_type: str = 'generate',
    ) -> List[str]:
        """
        等待 ComfyUI 生成完成并获取所有输出文件名（含多图片场景）。
        带进度回调，自动恢复 ComfyUI 崩溃。
        增加错误检测：如果 ComfyUI 返回执行错误，立即抛出。
        
        Args:
            task_type: 任务类型，用于设置不同的超时时间
                       'generate'=300s / 'refine'=600s / 'standardize_3'=600s
                       'standardize_6'=1200s / 'storyboard'=900s
        
        Returns:
            所有输出文件的文件名列表（优先非 temp 文件）
        """
        _mem_log("等待生成开始", f"prompt={prompt_id[:8]} task={task_type} timeout={TASK_TIMEOUTS.get(task_type, MAX_POLL_TIME)}s")
        max_time = TASK_TIMEOUTS.get(task_type, MAX_POLL_TIME)
        elapsed = 0
        consecutive_failures = 0
        last_queue_log = -30  # 每 30s 打印一次队列状态
        logger.info(
            f"[ComfyUI] 开始等待生成完成 | prompt_id={prompt_id}"
            f" | task_type={task_type} | timeout={max_time}s"
        )
        while elapsed < max_time:
            try:
                # 先查历史（生成完成后）
                session = self._get_http_session()
                url = f"{self.config.base_url}/history/{prompt_id}"
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        consecutive_failures = 0  # 连接成功则重置计数
                        try:
                            data = await resp.json()
                        except (json.JSONDecodeError, ValueError) as json_err:
                            logger.warning(
                                f"[ComfyUI] history JSON解析失败 (t={elapsed}s): {json_err}"
                            )
                            data = None
                        if data:
                            history = data.get(prompt_id, {})
                            # 检测执行错误 — ComfyUI history 格式：
                            #   {"status": {"status_str": "error", "completed": bool, "messages": [[event, data], ...]}}
                            #   messages 中每个元素是 [event_name, {exception_message, exception_type, ...}]
                            status_info = history.get("status", {})
                            if isinstance(status_info, dict):
                                status_str = status_info.get("status_str", "")
                                status_messages = status_info.get("messages", [])
                                if status_str == "error":
                                    error_msgs = []
                                    for msg in status_messages[:5]:
                                        if isinstance(msg, (list, tuple)) and len(msg) >= 2:
                                            event_name, msg_data = msg[0], msg[1]
                                            if isinstance(msg_data, dict):
                                                exc_msg = msg_data.get("exception_message", "")
                                                exc_type = msg_data.get("exception_type", "")
                                                node_id = msg_data.get("node_id", "")
                                                node_type = msg_data.get("node_type", "")
                                                error_msgs.append(f"[{node_type}#{node_id}] {exc_type}: {exc_msg}"[:200])
                                            else:
                                                error_msgs.append(str(msg_data)[:200])
                                        else:
                                            error_msgs.append(str(msg)[:200])
                                    if not error_msgs:
                                        error_msgs = ["unknown error"]
                                    logger.error(f"[ComfyUI] 执行错误详情 | status={status_str} | messages={status_messages[:3]}")
                                    raise RuntimeError(
                                        f"ComfyUI 执行错误: {'; '.join(error_msgs)}"
                                    )
                            # 兼容旧版 errors 字段
                            errors = history.get("errors", [])
                            if errors:
                                error_msgs = [str(e)[:200] for e in errors[:5]]
                                logger.error(f"[ComfyUI] 执行错误详情(errors字段) | errors={errors[:5]}")
                                raise RuntimeError(
                                    f"ComfyUI 执行错误: {'; '.join(error_msgs)}"
                                )
                            # 检测节点错误状态
                            outputs = history.get("outputs", {})
                            # 收集所有 SaveImage 节点的输出（跳过 PreviewImage 的 temp 文件）
                            all_filenames: List[str] = []
                            temp_filenames: List[str] = []
                            for node_id, node_output in outputs.items():
                                images = node_output.get("images", [])
                                for img in images:
                                    fname = img.get("filename", "")
                                    if not fname.startswith("ComfyUI_temp"):
                                        all_filenames.append(fname)
                                    elif not temp_filenames:
                                        # 兜底：只保留第一个 temp 文件名
                                        temp_filenames.append(fname)
                            if all_filenames:
                                _mem_log("等待生成完成", f"prompt={prompt_id[:8]} files={all_filenames} elapsed={elapsed}s")
                                if progress_callback:
                                    try:
                                        progress_callback(f"生成完成 ({elapsed}s)", 100)
                                    except Exception:
                                        pass
                                # 持久化到独立目录
                                await self._persist_output_files(all_filenames)
                                return all_filenames
                            if temp_filenames:
                                if progress_callback:
                                    try:
                                        progress_callback(f"生成完成 ({elapsed}s)", 100)
                                    except Exception:
                                        pass
                                # 持久化到独立目录
                                await self._persist_output_files(temp_filenames)
                                return temp_filenames

                # 还在生成中，查队列获取进度
                consecutive_failures = 0
                if progress_callback:
                    try:
                        prog = await self.get_queue_progress(prompt_id)
                        if prog.get("in_queue"):
                            progress_callback(f"队列处理中 ({elapsed}s)", prog["progress"])
                        elif elapsed >= 5:
                            estimated_pct = min(int(elapsed / 60 * 100), 99)
                            progress_callback(f"等待中 ({elapsed}s)", estimated_pct)
                    except Exception:
                        pass

                # 每 30s 打印一次队列状态（方便排障）
                if elapsed - last_queue_log >= 30:
                    last_queue_log = elapsed
                    try:
                        q_session = self._get_http_session()
                        async with q_session.get(
                            f"{self.config.base_url}/queue",
                            timeout=aiohttp.ClientTimeout(total=5),
                        ) as qresp:
                            if qresp.status == 200:
                                qdata = await qresp.json()
                                logger.debug(
                                    f"[ComfyUI] 队列状态 (t={elapsed}s): "
                                    f"running={len(qdata.get('queue_running', []))}, "
                                    f"pending={len(qdata.get('queue_pending', []))}"
                                )
                    except Exception as qe:
                        logger.warning(f"[ComfyUI] 查询队列失败 (t={elapsed}s): {qe}")

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                consecutive_failures += 1
                logger.warning(f"[ComfyUI] 轮询失败 ({consecutive_failures}x): {e}")
                # 连续失败 5 次（~2.5s）后检查 ComfyUI 是否还活着
                # 用 _check_alive() 区分"模型加载中暂时断连"和"真正崩溃"
                # - /history 超时但 _check_alive 成功 = 临时波动，继续等
                # - _check_alive 也失败 = ComfyUI 崩溃，立即重启
                if consecutive_failures >= 5:
                    alive = await self._check_alive()
                    if not alive:
                        logger.warning(
                            f"[ComfyUI] ComfyUI 确认为崩溃状态（{consecutive_failures}x 失败, "
                            f"{elapsed:.0f}s），尝试重启..."
                        )
                        await self._close_http_session()  # ⭐ 关闭旧 session，重启后自动创建新的
                        self._kill_process_on_port(8188)
                        await self.ensure_running()
                        # ⭐ 重启后 prompt_id 已丢失，新实例不认旧 ID，必须立即失败
                        raise RuntimeError(
                            f"ComfyUI 在生成过程中崩溃并已自动重启，当前任务（prompt_id={prompt_id[:8]}）"
                            f"已丢失，请重新发起生成"
                        )
                    else:
                        logger.info(
                            f"[ComfyUI] ComfyUI 仍在线（{consecutive_failures}x 暂时波动），继续等待..."
                        )

            # 自适应轮询：
            # - 前 10s：0.5s（快速反馈，适合文生图等短任务）
            # - 10-30s：1.0s
            # - 30-60s：2.0s
            # - 60s+：5.0s（长任务如视频，减少无效请求）
            # - 连接失败时：退避到 max 5s
            if consecutive_failures > 0:
                poll_interval = min(POLL_INTERVAL * (1.5 ** consecutive_failures), 5.0)
            elif elapsed < 10:
                poll_interval = POLL_INTERVAL  # 0.5s
            elif elapsed < 30:
                poll_interval = 1.0
            elif elapsed < 60:
                poll_interval = 2.0
            else:
                poll_interval = 5.0
            
            # ⭐ 每 30 秒打印一次内存状态，便于追踪内存泄漏
            _elapsed_int = int(elapsed)
            if _elapsed_int > 0 and _elapsed_int % 30 == 0 and (_elapsed_int - 30) < poll_interval + 1:
                _mem_log("轮询中", f"prompt={prompt_id[:8]} elapsed={_elapsed_int}s")
            
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise TimeoutError(
            f"ComfyUI 生成超时 ({max_time}s, task={task_type})，prompt_id={prompt_id[:8]}"
        )

    def get_available_models(self) -> List[Dict[str, Any]]:
        """获取可用模型信息"""
        return [
            {
                "id": "comfyui_yaoguang",
                "name": "Z-Image 瑶光版",
                "provider": "comfyui",
                "description": "本地ComfyUI + Z-Image瑶光LoRA，超真实细节增强，8步出图",
                "width": 1080,
                "height": 1920,
                "steps": 8,
            },
            {
                "id": "qwen_refinement",
                "name": "Qwen 精修",
                "provider": "comfyui",
                "description": "Qwen Image Edit单图编辑模式，用于角色/场景/道具精修定妆",
                "width": 1536,
                "height": 1024,
                "steps": 20,
            },
            {
                "id": "qwen_standardization",
                "name": "Qwen 标准化",
                "provider": "comfyui",
                "description": "Qwen Image Edit多图融合模式，用于3视图/6视图标准化生成",
                "width": 1536,
                "height": 512,
                "steps": 20,
            },
        ]


# ============================================================
# 全局单例
# ============================================================

_comfyui_service: Optional[ComfyUIService] = None


def get_comfyui_service() -> ComfyUIService:
    global _comfyui_service
    if _comfyui_service is None:
        _comfyui_service = ComfyUIService()
    return _comfyui_service
