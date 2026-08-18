
"""
ComfyUI 工作流构建器
支持 Z-Image 瑶光版（文生图）和 Qwen Image Edit（图生图）
"""

import copy
import json
import logging
import os
import re
import time
import random
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from urllib.parse import urlparse, parse_qs

from services.workflow_storyboard_custom import (
    build_single_person_workflow,
    build_dual_person_workflow,
    build_local_multi_workflow,
    build_gpt_storyboard_workflow,
)

logger = logging.getLogger(__name__)


# ============================================================
# 节点查找工具 — 通过 class_type / _meta.title 定位节点，避免硬编码 ID
# ============================================================

def find_node_by_class_type(wf: dict, class_type: str) -> list:
    """通过 class_type 查找所有匹配的节点，返回 [(node_id, node_data), ...]"""
    results = []
    for nid, ndata in wf.items():
        if isinstance(ndata, dict) and ndata.get('class_type') == class_type:
            results.append((nid, ndata))
    return results


def find_node_by_title(wf: dict, title: str) -> list:
    """通过 _meta.title 查找所有匹配的节点，返回 [(node_id, node_data), ...]"""
    results = []
    for nid, ndata in wf.items():
        if isinstance(ndata, dict) and ndata.get('_meta', {}).get('title') == title:
            results.append((nid, ndata))
    return results


def find_first_node_by_class_type(wf: dict, class_type: str) -> tuple:
    """通过 class_type 查找第一个匹配的节点，返回 (node_id, node_data) 或 (None, None)"""
    results = find_node_by_class_type(wf, class_type)
    return results[0] if results else (None, None)


def find_first_node_by_class_type_contains(wf: dict, class_type_part: str) -> tuple:
    """通过 class_type 部分匹配查找第一个节点，返回 (node_id, node_data) 或 (None, None)"""
    for nid, ndata in wf.items():
        if isinstance(ndata, dict) and class_type_part in ndata.get('class_type', ''):
            return (nid, ndata)
    return (None, None)


def _infer_saveimage_type(wf: dict, save_nid: str, infer_map: dict) -> str:
    """根据 SaveImage 节点的上游链路推断输出类型

    遍历 SaveImage 的输入链路，查找上游节点的 class_type，
    与 infer_map 中的关键词匹配，返回最匹配的类型标签。

    Args:
        wf: 工作流 dict
        save_nid: SaveImage 节点 ID
        infer_map: {type_tag: [keyword_list]} 映射

    Returns:
        匹配的 type_tag，默认 "unknown"
    """
    MAX_DEPTH = 20  # 防止环形引用或极深链路导致无限遍历
    visited = set()
    queue = [(save_nid, 0)]  # (node_id, depth)

    while queue:
        nid, depth = queue.pop(0)
        if nid in visited or depth > MAX_DEPTH:
            continue
        visited.add(nid)

        ndata = wf.get(nid)
        if not isinstance(ndata, dict):
            continue

        class_type = ndata.get("class_type", "").lower()
        # 检查 class_type 是否匹配任一类型
        for type_tag, keywords in infer_map.items():
            if any(kw in class_type for kw in keywords):
                return type_tag

        # 特判：AIO_Preprocessor 等预处理节点，检查其 "preprocessor" 输入参数值
        # 因为 AIO_Preprocessor 的 class_type 不含具体类型关键词
        # (如 "lineart", "depth")，类型信息在 preprocessor 参数中
        preprocessor_val = ndata.get("inputs", {}).get("preprocessor", "")
        if preprocessor_val:
            preprocessor_lower = preprocessor_val.lower()
            for type_tag, keywords in infer_map.items():
                if any(kw in preprocessor_lower for kw in keywords):
                    return type_tag

        # 追溯输入链路
        for input_val in ndata.get("inputs", {}).values():
            if isinstance(input_val, list) and len(input_val) >= 2:
                upstream_nid = str(input_val[0])
                if upstream_nid in wf and upstream_nid not in visited:
                    queue.append((upstream_nid, depth + 1))

    return "unknown"


# ============================================================
# Z-Image 瑶光版基础工作流（从 JSON 加载）
# ============================================================
_WF_DIR = Path(__file__).parent.parent.parent / "workflows"

# 文生图工作流模板（支持多版本，按 content_type 切换）
_WF_STANDARD = _WF_DIR / "文生图.json"      # 旧标准版（8步/cfg=1）
_WF_CINEMATIC = _WF_DIR / "最终文生图.json"  # 最终优化版（25步/cfg=2/AuraFlow）
_WF_PROP = _WF_DIR / "最终道具工作流.json"   # 道具专用版（+SeedVR2超分）

def _load_workflow(file_path: Path, label: str = "") -> Dict[str, Any]:
    """安全加载工作流 JSON 文件"""
    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                wf = json.load(f)
            logger.info(f"[Workflow] 从 {file_path} 加载 ({len(wf)} 节点) {label}")
            return wf
        except Exception as e:
            logger.warning(f"[Workflow] 加载失败 {file_path}: {e}")
    else:
        logger.warning(f"[Workflow] {file_path} 不存在")
    return {}

# 向后兼容：保留 BASE_WORKFLOW 符号引用
BASE_WORKFLOW: Dict[str, Any] = _load_workflow(_WF_STANDARD, "(标准版)")
CINEMATIC_WORKFLOW: Dict[str, Any] = _load_workflow(_WF_CINEMATIC, "(影视级)")
PROP_WORKFLOW: Dict[str, Any] = _load_workflow(_WF_PROP, "(道具版)")

# 瑶光版负向提示词（推荐留空，使用 ConditioningZeroOut）
YAOGUANG_DEFAULT_NEGATIVE = ""

# 其他支持的附加 LoRA
ADDITIONAL_LORAS = {
    "moody": {
        "lora_name": "moodyPornMix_zitV10DPO.safetensors",
        "strength_model": 0.8,
    },
}

# ============================================================
# Qwen Image Edit 工作流支持（图生图）
# ============================================================

QWEN_WORKFLOW_FILE = Path(__file__).parent.parent.parent / "Qwen Image Edit - Remix AIO v2.0 全功能合集工作流 By：肥猴 (1).json"


def format_qwen_prompt(
    keep: str = "",
    change: str = "",
    maintain: str = "",
    avoid: str = "",
    fallback: str = "",
) -> str:
    """
    格式化5段式提示词
    
    Args:
        keep: [KEEP] 保留元素
        change: [CHANGE] 改变指令
        maintain: [MAINTAIN] 一致性保持
        avoid: [AVOID] 负面约束
        fallback: [FALLBACK] 冲突解决
    """
    sections = []
    
    if keep:
        sections.append(f"[KEEP]\n{keep}")
    if change:
        sections.append(f"[CHANGE]\n{change}")
    if maintain:
        sections.append(f"[MAINTAIN]\n{maintain}")
    if avoid:
        sections.append(f"[AVOID]\n{avoid}")
    if fallback:
        sections.append(f"[FALLBACK]\n{fallback}")
    
    return "\n\n".join(sections)


# ComfyUI 目录（统一从 config 模块读取）
from services.comfyui.config import COMFYUI_DIR as _COMFYUI_DIR, COMFYUI_OUTPUT_DIR as _COMFYUI_OUTPUT_DIR
_COMFYUI_INPUT_DIR = os.path.join(_COMFYUI_DIR, "input") if _COMFYUI_DIR else ""




def _resolve_comfyui_image(ref: str) -> str:
    """
    将参考图像 URL/路径解析为 ComfyUI LoadImage 可用的文件名。
    
    ComfyUI 的 LoadImage 节点只接受 input 目录下的文件名（不带路径），
    而生成的图片在 output 目录中。此函数会：
    1. 从 URL (如 /api/comfyui/image?filename=xxx.png) 中提取 filename
    2. 将文件从 output 目录复制到 input 目录
    3. 返回纯文件名
    
    Args:
        ref: 参考图像 URL 或本地路径
    
    Returns:
        ComfyUI input 目录下的文件名
    """
    if not ref:
        return ref

    # 从 URL 中提取 filename 参数
    filename = ref
    if '?' in ref or ref.startswith('/api/'):
        parsed = urlparse(ref)
        params = parse_qs(parsed.query)
        if 'filename' in params:
            filename = params['filename'][0]
        elif parsed.path:
            # 可能是 /api/comfyui/image/filename.png 格式
            filename = parsed.path.rsplit('/', 1)[-1]

    # 如果已经只是文件名（不含目录分隔符），无需复制
    if '/' not in filename and '\\' not in filename:
        # 确保文件在 input 目录中
        src = os.path.join(_COMFYUI_OUTPUT_DIR, filename)
        dst = os.path.join(_COMFYUI_INPUT_DIR, filename)
        if os.path.exists(src):
            # ⭐ 始终用 output 目录的最新文件覆盖 input 目录
            # 避免同名旧文件导致超分/精修使用了错误的图片
            need_copy = not os.path.exists(dst)
            if not need_copy:
                # 文件都存在，比较大小判断是否需要更新
                src_sz = os.path.getsize(src)
                dst_sz = os.path.getsize(dst)
                need_copy = src_sz != dst_sz
            if need_copy:
                try:
                    os.makedirs(_COMFYUI_INPUT_DIR, exist_ok=True)
                    shutil.copy2(src, dst)
                    logger.info(f"[Qwen] 复制/更新图片到 ComfyUI input: {filename}")
                except Exception as e:
                    logger.warning(f"[Qwen] 复制图片失败: {e}")
        elif os.path.exists(dst):
            # input 存在但 output 不存在（可能是上传的文件），直接使用
            pass
        else:
            logger.warning(f"[Qwen] output 目录未找到图片: {src}（将由上游 service 的缓存/HTTP fallback 处理）")
        return filename

    # 如果是完整本地路径，直接提取文件名
    fname = os.path.basename(filename)


_TEMPLATE_DIR = Path(__file__).parent.parent.parent / "workflows" / "templates"


def _resolve_template_asset(template_name: str, asset_type: str) -> str:
    """
    从 workflows/templates/ 加载模板文件并复制到 ComfyUI input 目录。
    
    Args:
        template_name: 如 "T01_双人正面对话"
        asset_type: "mask" / "depth_clean" / "pose"
    
    Returns:
        ComfyUI input 目录下的文件名，或空字符串（文件不存在时）
    """
    if asset_type == "mask":
        tmpl_filename = f"{template_name}_mask.png"
    elif asset_type == "depth_clean":
        tmpl_filename = f"{template_name}_depth_clean.png"
    elif asset_type == "pose":
        tmpl_filename = f"{template_name}_pose.png"
    else:
        return ""

    tmpl_path = _TEMPLATE_DIR / tmpl_filename
    if not tmpl_path.exists():
        logger.warning(f"[WorkflowBuilder][模板] 文件不存在: {tmpl_path}")
        return ""

    dst = os.path.join(_COMFYUI_INPUT_DIR, tmpl_filename)
    try:
        os.makedirs(_COMFYUI_INPUT_DIR, exist_ok=True)
        shutil.copy2(str(tmpl_path), dst)
        logger.info(f"[WorkflowBuilder][模板] 已加载 {asset_type}: {tmpl_filename}")
        return tmpl_filename
    except Exception as e:
        logger.warning(f"[WorkflowBuilder][模板] 复制失败: {e}")
        return ""
    src = os.path.dirname(os.path.abspath(filename))
    dst = os.path.join(_COMFYUI_INPUT_DIR, fname)
    if os.path.exists(os.path.join(src, fname)) and not os.path.exists(dst):
        try:
            os.makedirs(_COMFYUI_INPUT_DIR, exist_ok=True)
            shutil.copy2(os.path.join(src, fname), dst)
            logger.info(f"[Qwen] 复制图片到 ComfyUI input: {fname}")
        except Exception as e:
            logger.warning(f"[Qwen] 复制图片失败: {e}")
    return fname


# ── content_type 驱动的精修参数映射 ──
_REFINE_LORA_STRENGTH = {
    "character": 1.0,   "scene": 0.6,   "prop": 0.4,   "": 1.0,
}
_REFINE_SCALE_LENGTH = {
    "character": 1344,  "scene": 1344,  "prop": 1024,  "": 1344,
}


def build_qwen_workflow(
    mode: str,
    prompt_text: str,
    reference_images: Optional[List[str]] = None,
    seed: Optional[int] = None,
    filename_prefix: str = "QwenEdit",
    output_size: Optional[tuple] = None,  # (width, height) 如 (1024,1024)
    denoise: Optional[float] = None,      # 覆写去噪强度，如 0.8
    expand_full_body: bool = False,       # 全身扩展：动态覆写 169 节点的 aspect_ratio 为 9:16、denoise→0.55
    width: Optional[int] = None,          # 图像宽度（可选，覆盖工作流默认值）
    height: Optional[int] = None,         # 图像高度（可选，覆盖工作流默认值）
    content_type: str = "",               # 内容类型（character/scene/prop/""），驱动 LoRA 强度和缩放尺寸
) -> Dict[str, Any]:
    """
    加载精修工作流 JSON 并注入参数（与 Z-Image 文生图同格式）
    
    **所有编辑变体共用同一个工作流文件**，通过动态覆写节点参数实现：
    - 全身扩展: aspect_ratio=9:16 + denoise=0.55 + 缩放策略=shortest
    - 侧面图: denoise 更高 + prompt 改为侧转指令（无需新工作流）
    - 其他编辑: 调整 denoise/prompt 即可
    
    Args:
        mode: 'single_edit'（精修）或 'fusion'（标准化）
        prompt_text: 提示词/精修指令文本
        reference_images: 参考图像路径列表
        seed: 随机种子
        filename_prefix: 输出文件名前缀
        output_size: 输出缩放尺寸 (width, height)，如 (1024,1024)
        denoise: 覆写去噪强度（标准化三视图需要高 denoise 如 0.8）
        expand_full_body: 全身扩展模式 — 动态覆写 aspect_ratio + denoise + scale_to_side
        content_type: 内容类型（character/scene/prop/""），驱动 LoRA 强度和缩放尺寸
    
    Returns:
        ComfyUI API 格式工作流
    """
    _t0 = time.time()
    actual_seed = seed or random.randint(0, 2**31 - 1)
    ref_img = (reference_images or [''])[0]
    local_filename = _resolve_comfyui_image(ref_img)

    # 加载单图编辑工作流 JSON（用户从 ComfyUI 导出）
    wf_path = Path(__file__).parent.parent.parent / "workflows" / "精修优化.json"
    if not wf_path.exists():
        logger.warning(f"[Qwen] 单图编辑工作流文件不存在: {wf_path}，尝试旧版精修工作流")
        # 兼容旧版文件名
        old_path = Path(__file__).parent.parent.parent / "精修单图编辑.json"
        if old_path.exists():
            wf_path = old_path
        else:
            logger.warning(f"[Qwen] 工作流文件均不存在，使用内置标准工作流")
            return _build_fallback_workflow(prompt_text, local_filename, actual_seed, filename_prefix)

    with open(wf_path, 'r', encoding='utf-8') as f:
        wf = json.load(f)

    # 注入参考图（LoadImage 节点 — 仅传文件名，ComfyUI 在 input 目录查找）
    nid, ndata = find_first_node_by_class_type(wf, 'LoadImage')
    if nid and ndata:
        wf[nid]['inputs']['image'] = local_filename

    # 注入用户指令
    # Fisher 配置: TextEncodeQwenImageEditPlus 的 prompt 字段直接注入简单自然语言
    #   旧版兼容: PrimitiveStringMultiline / Advance 版 instruction 字段
    prompt_injected = False
    # 旧版兼容：PrimitiveStringMultiline
    nid, ndata = find_first_node_by_class_type(wf, 'PrimitiveStringMultiline')
    if nid and ndata and 'value' in ndata.get('inputs', {}):
        wf[nid]['inputs']['value'] = prompt_text
        prompt_injected = True
    # Fisher 版：TextEncodeQwenImageEditPlus，prompt 直接为字符串
    if not prompt_injected:
        nid, ndata = find_first_node_by_class_type_contains(wf, 'QwenImageEditPlusAdvance')
        if nid and ndata:
            prompt_field = ndata.get('inputs', {}).get('prompt')
            if isinstance(prompt_field, str):
                wf[nid]['inputs']['prompt'] = prompt_text
                prompt_injected = True
            elif isinstance(prompt_field, list):
                # prompt 通过链接来自其他节点（如 CR Prompt Text），已在上面注入
                pass
            # 旧版 Advance 编码器的 instruction 字段（Fisher 基础版无此字段，自动跳过）
            if isinstance(ndata.get('inputs', {}).get('instruction'), str):
                wf[nid]['inputs']['instruction'] = (
                    "Edit the image according to the user's prompt. "
                    "Only modify image pixels. Never generate, render, or overlay any text, "
                    "labels, watermarks, titles, captions, or annotations on the image. "
                    "The output must be a pure image with no visible text whatsoever."
                )

    # 设置种子（Seed 节点 — class_type 包含 "Seed"）
    nid, ndata = find_first_node_by_class_type_contains(wf, 'Seed')
    if nid and ndata and 'seed' in ndata.get('inputs', {}):
        wf[nid]['inputs']['seed'] = actual_seed

    # 设置输出前缀（SaveImage 节点）
    nid, ndata = find_first_node_by_class_type(wf, 'SaveImage')
    if nid and ndata and 'filename_prefix' in ndata.get('inputs', {}):
        wf[nid]['inputs']['filename_prefix'] = f"{filename_prefix}_{actual_seed}"

    # 覆写 denoise
    #   Fisher 配置默认 denoise=1（完全重生成 + ReferenceLatent 保证一致性）
    #   仅在显式指定 denoise 时覆写（如三视图等特殊场景）
    if denoise is not None:
        for ksid in ("174", "12", "44"):
            if ksid in wf and "denoise" in wf[ksid]["inputs"]:
                old_denoise = wf[ksid]["inputs"]["denoise"]
                wf[ksid]["inputs"]["denoise"] = denoise
                logger.info(f"[Qwen] 覆写 denoise {old_denoise}→{denoise} (node {ksid})")
                break
    # expand_full_body: Fisher 配置 denoise 已为 1，无需调整
    #   ReferenceLatent + denoise=1 天然支持 outpainting

    # ═══ 全身扩展：覆写 ImageScaleByAspectRatio 节点 ═══
    #   Fisher 配置: denoise=1 天然支持 outpainting，无需调 denoise
    #   Python 预处理已将图片填充到 9:16，节点只需 aspect_ratio=9:16 + fit=crop 直通
    if expand_full_body:
        nid, ndata = find_first_node_by_class_type_contains(wf, 'ImageScaleByAspectRatio')
        if nid and ndata:
            wf[nid]['inputs']['aspect_ratio'] = '9:16'
            wf[nid]['inputs']['fit'] = 'crop'
            wf[nid]['inputs']['scale_to_side'] = 'shortest'
            wf[nid]['inputs']['scale_to_length'] = 1024
            logger.info(
                f"[Qwen][全身扩展] 节点{nid}: 9:16 crop (图片已 Python 填充, denoise=1 天然支持 outpainting)"
            )
        else:
            logger.warning("[Qwen][全身扩展] 未找到 ImageScaleByAspectRatio 节点")

    # 如果指定了 width/height，注入到 ImageScaleByAspectRatio 节点
    # 优先级：width/height > expand_full_body > 工作流默认值
    if width and height:
        nid, ndata = find_first_node_by_class_type_contains(wf, 'ImageScaleByAspectRatio')
        if nid and ndata:
            # 根据 width/height 计算最接近的 aspect_ratio
            from math import gcd
            g = gcd(width, height)
            ratio_w, ratio_h = width // g, height // g
            # 简化比例到常见格式
            ratio_map = {(3,4): "3:4", (4,3): "4:3", (9,16): "9:16", (16,9): "16:9", (1,1): "1:1", (2,3): "2:3", (3,2): "3:2"}
            aspect_ratio = ratio_map.get((ratio_w, ratio_h), f"{ratio_w}:{ratio_h}")
            wf[nid]['inputs']['aspect_ratio'] = aspect_ratio
            wf[nid]['inputs']['scale_to_side'] = 'shortest'
            wf[nid]['inputs']['scale_to_length'] = max(width, height)
            wf[nid]['inputs']['fit'] = 'crop'
            logger.info(f"[Qwen] 覆写节点{nid}尺寸: {width}×{height}, aspect_ratio={aspect_ratio}")

    # 如果指定了 output_size，注入缩放节点（用于分镜等需要非默认尺寸的场景）
    nid_save, save_data = find_first_node_by_class_type(wf, 'SaveImage')
    # 找到输出图像的来源节点（SaveImage 的 images 输入）
    output_source_nid = None
    if nid_save and isinstance(wf[nid_save]['inputs'].get('images'), list) and len(wf[nid_save]['inputs']['images']) >= 2:
        output_source_nid = wf[nid_save]['inputs']['images'][0]

    if output_source_nid and nid_save and output_size:
        _out_w, _out_h = output_size
        scale_node_id = "500"
        if scale_node_id not in wf:
            # ⭐ 修复 C2：crop="center" 强制裁剪到目标尺寸，避免输出尺寸不可预测
            wf[scale_node_id] = {
                "inputs": {
                    "upscale_method": "lanczos",
                    "width": _out_w, "height": _out_h,
                    "crop": "center",  # 中心裁剪到精确尺寸
                    "image": [output_source_nid, 0],
                },
                "class_type": "ImageScale",
                "_meta": {"title": f"输出缩放({_out_w}×{_out_h})"},
            }
            wf[nid_save]['inputs']['images'][0] = scale_node_id
            # 更新其他引用该输出源节点的节点
            for nid, ndata in wf.items():
                if isinstance(ndata, dict):
                    for key, val in ndata.get('inputs', {}).items():
                        if (isinstance(val, list) and len(val) >= 2
                                and val[0] == output_source_nid
                                and nid != scale_node_id
                                and nid != nid_save):
                            wf[nid]['inputs'][key][0] = scale_node_id
            logger.info(f"[Qwen] 已注入输出缩放节点(500): {_out_w}×{_out_h} (center crop)")
    elif output_source_nid and nid_save and not output_size:
        # ⭐ 修复 C2：未指定 output_size 时，根据 content_type 设置默认尺寸
        # 避免输出尺寸完全不可预测
        default_size = (1024, 1024) if content_type == "prop" else (1344, 1344)
        scale_node_id = "500"
        if scale_node_id not in wf:
            wf[scale_node_id] = {
                "inputs": {
                    "upscale_method": "lanczos",
                    "width": default_size[0], "height": default_size[1],
                    "crop": "center",
                    "image": [output_source_nid, 0],
                },
                "class_type": "ImageScale",
                "_meta": {"title": f"输出缩放(默认 {default_size[0]}×{default_size[1]})"},
            }
            wf[nid_save]['inputs']['images'][0] = scale_node_id
            for nid, ndata in wf.items():
                if isinstance(ndata, dict):
                    for key, val in ndata.get('inputs', {}).items():
                        if (isinstance(val, list) and len(val) >= 2
                                and val[0] == output_source_nid
                                and nid != scale_node_id
                                and nid != nid_save):
                            wf[nid]['inputs'][key][0] = scale_node_id
            logger.info(f"[Qwen] 未指定 output_size，使用默认尺寸: {default_size[0]}×{default_size[1]} (center crop)")

    mode_tag = "[全身扩展]" if expand_full_body else ""
    logger.info(f"[Qwen]{mode_tag} 已加载工作流 ({wf_path.name}), seed={actual_seed}, ref={local_filename}, prompt_injected={prompt_injected}")

    # ── content_type 驱动的精修参数定制 ──────────────────────
    if content_type:
        # 1. LoRA 强度（节点 381: LoraLoaderModelOnly）
        for node_id, node_data in wf.items():
            if (isinstance(node_data, dict)
                    and node_data.get('class_type') == 'LoraLoaderModelOnly'
                    and 'strength_model' in node_data.get('inputs', {})):
                old = node_data['inputs']['strength_model']
                node_data['inputs']['strength_model'] = _REFINE_LORA_STRENGTH.get(content_type, 1.0)
                logger.info(f"[Qwen] content_type={content_type} → LoRA节点{node_id} strength_model {old}→{node_data['inputs']['strength_model']}")
                break
        # 2. 缩放尺寸（节点 169: ImageScaleByAspectRatio V2）
        for node_id, node_data in wf.items():
            if (isinstance(node_data, dict)
                    and 'ImageScaleByAspectRatio' in node_data.get('class_type', '')
                    and 'scale_to_length' in node_data.get('inputs', {})):
                old = node_data['inputs']['scale_to_length']
                node_data['inputs']['scale_to_length'] = _REFINE_SCALE_LENGTH.get(content_type, 1344)
                logger.info(f"[Qwen] content_type={content_type} → 缩放节点{node_id} scale_to_length {old}→{node_data['inputs']['scale_to_length']}")
                break

    logger.info(f"[WorkflowBuilder][Qwen] 构建完成 | elapsed={time.time()-_t0:.3f}s | nodes={len(wf)}")
    return wf


def build_scene_multiangle_workflow(
    reference_image: str,
    scene_dna: str = "",
    per_frame_prompts: Optional[List[str]] = None,
    seed: Optional[int] = None,
    filename_prefix: str = "BarScene_6Angle",
    instruction: Optional[str] = None,
) -> Dict[str, Any]:
    """
    构建场景多角度标准化工作流（双通道约束）
    
    使用 bar_scene_6angle_workflow.json，通过两个通道约束生成：
    - 通道1 (节点14 instruction): 全局场景 DNA + 保留规则
    - 通道2 (节点37 value): 每帧提示词（场景DNA前缀 + 镜头动作 + 禁止咒语）
    
    Args:
        reference_image: 参考图像路径
        scene_dna: 场景DNA文本（从文生图提示词提取的关键资产描述）
        per_frame_prompts: 每帧提示词列表（6 行，每行对应一个镜头）
        seed: 随机种子
        filename_prefix: 输出文件名前缀
        instruction: 直接使用的全局 instruction（由 DeepSeek 生成，优先于 scene_dna）
    
    Returns:
        ComfyUI API 格式工作流
    """
    _t0 = time.time()
    actual_seed = seed or random.randint(0, 2**31 - 1)
    ref_img = _resolve_comfyui_image(reference_image)

    wf_path = Path(__file__).parent.parent.parent / "原始多场景.json"
    if not wf_path.exists():
        # 降级到普通单图编辑工作流
        logger.warning("[SceneMultiAngle] 场景多角度工作流不存在，降级到单图编辑")
        return build_qwen_workflow(
            mode='single_edit',
            prompt_text=scene_dna or "生成场景的多角度视图",
            reference_images=[reference_image] if reference_image else None,
            seed=seed,
            filename_prefix=filename_prefix,
        )

    with open(wf_path, 'r', encoding='utf-8') as f:
        wf = json.load(f)

    # 注入参考图（LoadImage 节点）
    nid, ndata = find_first_node_by_class_type(wf, 'LoadImage')
    if nid and ndata:
        wf[nid]['inputs']['image'] = ref_img

    # 注入全局 instruction（QwenImageEditPlusAdvance 节点的 instruction 字段）
    # 使用 DeepSeek 生成的场景特定 instruction + 保留规则后缀
    preservation_rules = (
        "\n\nPRESERVATION RULES (MUST FOLLOW):\n"
        "- Every single object in the reference image must remain in EXACTLY the same quantity, position, material, and color.\n"
        "- Do NOT add, remove, or replace any object, furniture, decor, or architectural element.\n"
        "- Do NOT add text, banners, flags, signs, or any new objects.\n"
        "- Lighting direction and color temperature must remain identical.\n"
        "- Only the camera angle/perspective may change. Nothing else in the scene."
    )
    if instruction:
        final_instruction = instruction + preservation_rules
    else:
        final_instruction = (
            "Generate alternate camera angles for the scene. Preserve ALL objects exactly as they appear."
        ) + preservation_rules
    nid, ndata = find_first_node_by_class_type_contains(wf, 'QwenImageEditPlusAdvance')
    if nid and ndata and 'instruction' in ndata.get('inputs', {}):
        old_inst = ndata['inputs'].get('instruction', '(无)')[:60]
        wf[nid]['inputs']['instruction'] = final_instruction
        logger.info(f"[SceneMultiAngle] 已覆盖 instruction (原: {old_inst}...)")

    # 注入每帧提示词（PrimitiveStringMultiline 节点）
    # 优先使用外部传入的提示词（可能是本地模板或 DeepSeek）；兜底用通用中文提示词
    nid_psm, ndata_psm = find_first_node_by_class_type(wf, 'PrimitiveStringMultiline')
    if per_frame_prompts and len(per_frame_prompts) > 0:
        prompt_lines = "\n".join(per_frame_prompts)
        if nid_psm and ndata_psm:
            wf[nid_psm]['inputs']['value'] = prompt_lines
        logger.info(f"[SceneMultiAngle] 使用外部传入提示词 ({len(per_frame_prompts)}帧)")
    elif nid_psm and ndata_psm:
        default_lines = [
            "Scene：广角全景。仅改变视角，场景内容与参考图完全一致。",
            "Scene：正面中景。仅改变视角，场景内容与参考图完全一致。",
            "Scene：左侧45度斜侧。仅改变视角，场景内容与参考图完全一致。",
            "Scene：右侧45度斜侧。仅改变视角，场景内容与参考图完全一致。",
            "Scene：特写镜头。仅改变视角，场景内容与参考图完全一致。",
            "Scene：正上方90度俯视。仅改变视角，场景内容与参考图完全一致。",
        ]
        wf[nid_psm]['inputs']['value'] = "\n".join(default_lines)
        logger.info("[SceneMultiAngle] 无 DeepSeek prompts，使用兜底中文角度提示词")

    # 设置种子和 denoise（KSampler 节点）
    nid_ks, ks_data = find_first_node_by_class_type(wf, 'KSampler')
    if nid_ks and ks_data:
        wf[nid_ks]['inputs']['seed'] = actual_seed
        old_d = ks_data.get('inputs', {}).get('denoise', '?')
        wf[nid_ks]['inputs']['denoise'] = 0.95
        logger.info(f"[SceneMultiAngle] 覆写 denoise {old_d}→0.95 (node {nid_ks})")

    # 覆盖负向提示词（第二个 CLIPTextEncode 节点），增强禁止新增物体的约束
    clip_nodes = find_node_by_class_type(wf, 'CLIPTextEncode')
    if len(clip_nodes) >= 2:
        nid_neg = clip_nodes[1][0]
        if isinstance(wf[nid_neg]['inputs'].get('text'), str):
            wf[nid_neg]['inputs']['text'] = (
                "adding new objects, removing objects, extra objects, missing objects, "
                "new furniture, new decorations, banners, flags, text on walls, "
                "changed object count, changed object position, "
                "low quality, blurry, deformed, distorted perspective, "
                "style mismatch, color shift, changed lighting"
            )
            logger.info("[SceneMultiAngle] 已覆盖负向提示词（增强物体保留约束）")

    # 设置输出前缀（SaveImage 节点）
    nid_save, save_data = find_first_node_by_class_type(wf, 'SaveImage')
    if nid_save and save_data:
        wf[nid_save]['inputs']['filename_prefix'] = f"{filename_prefix}_{actual_seed}"

    # 每帧单行提示词，不需要 increment 遍历，使用 fixed 模式避免索引偏移
    nid_pl, pl_data = find_first_node_by_class_type(wf, 'easy promptLine')
    if nid_pl and pl_data:
        wf[nid_pl]['inputs']['control_after_generate'] = 'fixed'
        wf[nid_pl]['inputs']['start_index'] = 0
        logger.info("[SceneMultiAngle] 已设置 promptLine 遍历模式 (fixed，单帧模式)")

    # 标准化阶段输出1024×1024，无需额外缩放节点

    logger.info(f"[SceneMultiAngle] 已加载场景多角度工作流, seed={actual_seed}, ref={ref_img}")
    logger.info(f"[WorkflowBuilder][场景多角度] 构建完成 | elapsed={time.time()-_t0:.3f}s | nodes={len(wf)}")
    return wf


def _build_fallback_workflow(prompt_text: str, ref_img: str, seed: int, prefix: str) -> Dict[str, Any]:
    """兜底：使用标准 ComfyUI 内置节点构建 img2img 工作流"""
    wf: Dict[str, Any] = {}
    wf["1"] = {"class_type": "LoadImage", "inputs": {"image": ref_img}}
    wf["2"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["1", 0], "vae": ["3", 0]}}
    wf["3"] = {"class_type": "VAELoader", "inputs": {"vae_name": "zimge_ae.safetensors"}}
    wf["4"] = {"class_type": "CLIPTextEncode", "inputs": {"text": prompt_text, "clip": ["5", 0]}}
    wf["5"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "lumina2"}}
    wf["6"] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": seed, "steps": 20, "cfg": 3.5, "sampler_name": "euler",
            "scheduler": "normal", "denoise": 0.6,
            "model": ["7", 0], "positive": ["4", 0], "negative": ["8", 0],
            "latent_image": ["2", 0],
        },
    }
    wf["7"] = {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "zimge.safetensors"}}
    wf["8"] = {"class_type": "CLIPTextEncode", "inputs": {"text": "worst quality, low quality, blurry, ugly", "clip": ["5", 0]}}
    wf["9"] = {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 0]}}
    wf["10"] = {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": f"{prefix}_{seed}"}}
    logger.info(f"[Qwen] 使用兜底标准工作流, seed={seed}")
    return wf


def build_refinement_workflow(
    reference_image: str,
    role_desc: str = "",
    scene_desc: str = "",
    prop_desc: str = "",
    lock_elements: Optional[List[Dict[str, str]]] = None,
    seed: Optional[int] = None,
    full_prompt: Optional[str] = None,
    filename_prefix: str = "refinement",
    expand_full_body: bool = False,  # 全身扩展：共用同一工作流，仅覆写 aspect_ratio + denoise
    width: Optional[int] = None,   # 图像宽度（可选，覆盖工作流默认值）
    height: Optional[int] = None,  # 图像高度（可选，覆盖工作流默认值）
    content_type: str = "",        # 内容类型（character/scene/prop/""），驱动 LoRA 强度和缩放尺寸
) -> Tuple[Dict[str, Any], str, Dict[str, str]]:
    """
    构建精修阶段工作流（单图编辑模式）
    
    Fisher 配置: 简洁自然语言提示词 + denoise=1 + ReferenceLatent 保证一致性
    
    Args:
        reference_image: 参考图像路径
        role_desc: 角色描述
        scene_desc: 场景描述
        prop_desc: 道具描述
        lock_elements: 需要锁定的元素列表 [{element, description, priority}]
        seed: 随机种子
        full_prompt: 直接使用的完整提示词（跳过提示词构建）
        expand_full_body: 全身扩展模式（动态覆写参数，不创建新工作流）
    
    Returns:
        (workflow, prompt_text, prompt_sections) 元组
    """
    _t0 = time.time()
    prompt_sections = {}
    
    if full_prompt:
        # 直接使用提供的提示词（用户编辑后重新生成、或 DeepSeek 优化后）
        prompt = full_prompt
        # 兼容旧版5段式格式提取段落（新格式无需提取）
        for sec_key, sec_label in [("keep", "[KEEP]"), ("change", "[CHANGE]"),
                                    ("maintain", "[MAINTAIN]"), ("avoid", "[AVOID]"),
                                    ("fallback", "[FALLBACK]")]:
            import re
            m = re.search(rf'{re.escape(sec_label)}\s*\n(.+?)(?=\n\n\[|\Z)', prompt, re.DOTALL)
            if m:
                prompt_sections[sec_key] = m.group(1).strip()
        logger.info(f"[Qwen] 使用直接提供的提示词 ({len(prompt)} chars): {prompt[:80]}...")
    else:
        # Fisher 配置：简洁自然语言提示词（不用5段式结构化格式）
        # Qwen VL 模型对简单自然语言的响应远优于复杂结构化格式
        parts = []
        if role_desc:
            parts.append(role_desc)
        if scene_desc:
            parts.append(scene_desc)
        if prop_desc:
            parts.append(prop_desc)
        if lock_elements:
            lock_items = [le['element'] for le in lock_elements if isinstance(le, dict) and le.get('element')]
            if lock_items:
                parts.append(f"保持{'、'.join(lock_items)}不变")
        
        if parts:
            prompt = "，".join(parts)
        else:
            prompt = "优化图片细节，提升画质，保持风格一致"
        
        prompt_sections = {
            "change": prompt,
        }
        logger.info(f"[Qwen] 精修提示词已构建 ({len(prompt)} chars): {prompt[:80]}...")
    
    workflow = build_qwen_workflow(
        mode='single_edit',
        prompt_text=prompt,
        reference_images=[reference_image] if reference_image else None,
        seed=seed,
        filename_prefix=filename_prefix,
        expand_full_body=expand_full_body,
        width=width,
        height=height,
        content_type=content_type,
    )
    
    logger.info(f"[WorkflowBuilder][精修] 构建完成 | elapsed={time.time()-_t0:.3f}s | expand_full_body={expand_full_body}")
    return workflow, prompt, prompt_sections


def build_standardization_workflow(
    reference_image: str,
    views: int = 3,
    character_name: str = "角色",
    seed: Optional[int] = None,
    full_prompt: Optional[str] = None,
    filename_prefix: str = "standard",
    view_type: str = "character",
    role_desc: str = "",  # 新增：优化后的角色/道具描述
    width: Optional[int] = None,   # ⭐ 图像宽度（可选，覆盖默认尺寸）
    height: Optional[int] = None,  # ⭐ 图像高度（可选，覆盖默认尺寸）
) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
    """
    构建标准化阶段工作流（单图精修模式）
    
    使用 精修优化.json 对参考图做一次精修优化，输出单张高质量图片。
    标准化在当前设计中等同于第二轮精修，不做多视图拆分。
    
    Args:
        reference_image: 参考图像路径
        views: 保留参数，不使用
        character_name: 资产名称（如"女侠"/"宝剑"）
        seed: 随机种子
        full_prompt: DeepSeek 优化后的提示词
        filename_prefix: 输出文件名前缀
        view_type: 资产类型（character/prop），用于区分提示词语义
        role_desc: 优化后的角色/道具描述（从概念阶段传递过来）
    """
    if full_prompt:
        prompt = full_prompt
        logger.info(f"[Standardization] 使用 DeepSeek 优化提示词 ({len(prompt)} chars)")
    else:
        # 构建基础描述（优先使用优化后的描述）
        _t0 = time.time()
        asset_desc = role_desc if role_desc and len(role_desc) > 5 else character_name
        
        if view_type == 'character':
            # 纯视觉描述，禁止出现可能被渲染为文字标签的词汇
            prompt = (
                f"基于参考图的{asset_desc}，在同一张画布上并排生成三个视角的人物。"
                f"所有角色特征、服装、发型必须严格一致。"
                f"三个图水平排列等大：第一个面向观众，第二个原地侧转90度，第三个背对观众。"
                f"背景纯白，画面中绝对不准出现任何文字或数字。"
            )
        else:
            prompt = (
                f"基于参考图的{asset_desc}，在同一张画布上并排生成三个不同视角的视图。"
                f"所有材质、形状、颜色必须严格一致。"
                f"三个图水平排列等大：第一个展示正对方向，第二个展示旋转90度后的方向，第三个展示背对方向。"
                f"背景纯白，画面中绝对不准出现任何文字或数字。"
            )
        logger.info(f"[Standardization] 使用本地模板提示词（{view_type}三视图生成）| asset_desc='{asset_desc[:60]}'")

    _t0_wf = time.time()
    wf_result = build_qwen_workflow(
        mode='single_edit',
        prompt_text=prompt,
        reference_images=[reference_image] if reference_image else None,
        seed=seed,
        filename_prefix=f'{filename_prefix}_standard',
        denoise=0.95,  # 三视图需要极高 denoise 才能完全改变构图
        output_size=(width or 1024, height or 1024),  # ⭐ 支持自定义尺寸
    )
    logger.info(f"[WorkflowBuilder][标准化] 构建完成 | elapsed={time.time()-_t0_wf:.3f}s | nodes={len(wf_result)}")
    return wf_result, prompt, {'stage': 'standardization', 'views': views}


# ============================================================
# 原有 Z-Image 瑶光版工作流构建（文生图）
# ============================================================

# ============================================================
# 年龄感知能力
# ============================================================

def _detect_age_in_prompt(prompt: str):
    """
    从提示词中检测年龄信息，返回 (age_group, description)。

    age_group: child/teen/young/adult/elder/unknown
    description: 拼接在触发词中的年龄描述
    """
    # 1. 精确匹配 "X岁"
    m = re.search(r'(\d+)\s*岁', prompt)
    if m:
        age = int(m.group(1))
        if age <= 3:
            return ("child", "婴儿，可爱面孔，")
        elif age <= 10:
            return ("child", "儿童，可爱面容，")
        elif age <= 14:
            return ("teen", "少年，年少面孔，")
        elif age <= 18:
            return ("teen", "青少年，年轻面孔，")
        elif age <= 30:
            return ("young", "青年，年轻成人，")
        elif age <= 50:
            return ("adult", "中年成年人，成熟面容，")
        else:
            return ("elder", "中老年，成熟面孔，")

    # 2. 关键词匹配
    child_kw = ["小孩", "儿童", "宝宝", "婴儿", "萝莉", "正太", "小男孩", "小女孩", "小朋友", "孩童"]
    teen_kw = ["青少年", "少女", "少年", "男生", "女生"]
    adult_kw = ["美女", "帅哥", "男人", "女人", "女士", "先生", "大叔", "阿姨", "成年"]

    for kw in child_kw:
        if kw in prompt:
            return ("child", "儿童，可爱面容，")
    for kw in teen_kw:
        if kw in prompt:
            return ("teen", "青少年，年轻面孔，")
    for kw in adult_kw:
        if kw in prompt:
            return ("young", "青年，年轻成人，")

    return ("unknown", "")


def build_comfyui_workflow(
    positive_prompt: str,
    negative_prompt: str = "",
    width: Optional[int] = None,   # None=使用工作流模板自带的尺寸
    height: Optional[int] = None,  # None=使用工作流模板自带的尺寸
    seed: Optional[int] = None,
    steps: Optional[int] = None,   # None=使用工作流模板自带的步数
    cfg: Optional[float] = None,   # None=使用工作流模板自带的CFG
    additional_lora: Optional[str] = None,
    reference_image: str = "",
    content_type: str = "",
    workflow: str = "cinematic",  # "standard" | "cinematic"
    **kwargs,
) -> Dict[str, Any]:
    """
    从结构化提示词构建 Z-Image 瑶光版工作流（文生图为主）

    Args:
        positive_prompt: 正向提示词（纯文本）
        negative_prompt: 负向提示词
        width: 图像宽度
        height: 图像高度
        seed: 随机种子（None=自动）
        steps: 采样步数
        cfg: CFG 强度
        additional_lora: 附加 LoRA 名称（可选）
        reference_image: 参考图路径（图生图模式，使用瑶光自带图生图）
        content_type: 内容类型（character/scene/prop/""），驱动 UNet 切换和 LoRA 强度
        workflow: 工作流模板 "standard"（8步/标准）或 "cinematic"（25步/AuraFlow/影视级）

    Returns:
        dict: 完整的 ComfyUI 工作流 JSON
    """
    # 选择工作流模板
    # ⭐ 保存原始 workflow 类型字符串（line 969 后 workflow 变量会被覆盖为 dict）
    workflow_type = workflow
    is_prop_workflow = (workflow == "prop" and PROP_WORKFLOW)
    if is_prop_workflow:
        wf_template = PROP_WORKFLOW
        has_auraflow = True
    elif workflow == "cinematic" and CINEMATIC_WORKFLOW:
        wf_template = CINEMATIC_WORKFLOW
        has_auraflow = True
    else:
        wf_template = BASE_WORKFLOW if BASE_WORKFLOW else CINEMATIC_WORKFLOW
        has_auraflow = False
    # ⭐ 修复 P2：空工作流字典保护
    # _load_workflow 失败时返回 {}，下游节点索引会 KeyError 导致所有生成请求崩溃
    # 此处显式抛异常，让调用方感知配置问题而非静默失败
    if not wf_template:
        raise RuntimeError(
            f"工作流模板加载失败（空字典）：workflow={workflow_type} | "
            f"请检查 workflows/ 目录下的 JSON 文件是否存在或损坏"
        )
    workflow = copy.deepcopy(wf_template)

    # ⭐ 道具工作流快速路径：仅注入正面提示词和 seed，保持模板其余配置不变
    # 模板中提示词结构：KSampler → CLIPTextEncode(20) → PrimitiveStringMultiline(21)
    # 需要注入到 PrimitiveStringMultiline 的 value 字段，而非 CLIPTextEncode 的 text
    if is_prop_workflow:
        # 注入正面提示词到 PrimitiveStringMultiline 节点
        nid_ps, ps_data = find_first_node_by_class_type(workflow, 'PrimitiveStringMultiline')
        if nid_ps and ps_data:
            workflow[nid_ps]['inputs']['value'] = positive_prompt
            logger.info(f"[Workflow] 道具工作流注入正面提示词到节点{nid_ps}: {positive_prompt[:80]}...")
        else:
            # 降级：直接覆写 CLIPTextEncode 的 text
            nid_ks, ks_data = find_first_node_by_class_type(workflow, 'KSampler')
            if nid_ks and ks_data:
                pos_clip_id = str(ks_data['inputs']['positive'][0])
                if pos_clip_id in workflow:
                    workflow[pos_clip_id]['inputs']['text'] = positive_prompt
            logger.warning("[Workflow] 道具工作流未找到 PrimitiveStringMultiline，降级到 CLIPTextEncode")

        # 注入 seed
        nid_ks, ks_data = find_first_node_by_class_type(workflow, 'KSampler')
        if nid_ks and ks_data:
            if seed is not None:
                workflow[nid_ks]['inputs']['seed'] = seed
            else:
                workflow[nid_ks]['inputs']['seed'] = int(time.time() * 1000) % (2**63)

        # 设置 filename_prefix
        nid_save, _ = find_first_node_by_class_type(workflow, 'SaveImage')
        if nid_save:
            workflow[nid_save]['inputs']['filename_prefix'] = f"prop_{workflow[nid_ks]['inputs']['seed']}" if nid_ks else "prop"

        # 覆写图像尺寸（仅用户明确指定时，否则保持模板尺寸）
        if width is not None and height is not None:
            nid_empty, empty_data = find_first_node_by_class_type(workflow, 'EmptyLatentImage')
            if nid_empty and empty_data:
                workflow[nid_empty]['inputs']['width'] = width
                workflow[nid_empty]['inputs']['height'] = height
                logger.info(f"[Workflow] 道具工作流尺寸覆写: {width}×{height}")
        else:
            logger.info(f"[Workflow] 道具工作流保持模板尺寸不变")

        logger.info(f"[Workflow] 道具工作流快速路径完成 | seed={workflow[nid_ks]['inputs']['seed'] if nid_ks else '?'} | size={width}×{height} | 保持模板LoRA/负向提示词不变")
        return workflow

    # ── content_type 驱动的节点覆写 ──────────────────────────

    # UNet 切换 + 模型链管理（兼容 AuraFlow 和标准链）
    nid_ksampler, ks_data = find_first_node_by_class_type(workflow, 'KSampler')

    # 检测工作流中是否有 ModelSamplingAuraFlow 节点
    nid_aura, _ = find_first_node_by_class_type(workflow, 'ModelSamplingAuraFlow') if has_auraflow else (None, None)

    if content_type == "scene":
        # 场景：找到 moodyPornMix LoRA 节点，将 KSampler 的 model 输入指向它
        nid_lora_scene, _ = find_first_node_by_class_type_contains(workflow, 'LoraLoader')
        if nid_lora_scene and nid_ksampler:
            if nid_aura:
                # 有 AuraFlow：将 AuraFlow 的 model 输入指向 LoRA
                workflow[nid_aura]['inputs']['model'] = [nid_lora_scene, 0]
            else:
                workflow[nid_ksampler]['inputs']['model'] = [nid_lora_scene, 0]
    elif content_type in ("character", "prop", ""):
        # 角色/道具/默认：找到瑶光 LoRA 节点
        nid_lora_char, _ = find_first_node_by_class_type(workflow, 'LoraLoaderModelOnly')
        if nid_lora_char and nid_ksampler:
            if nid_aura:
                # 有 AuraFlow：保持链 KSampler → AuraFlow → LoRA → UNet
                # 只需确保 AuraFlow 的 model 输入指向 LoRA
                workflow[nid_aura]['inputs']['model'] = [nid_lora_char, 0]
            else:
                # 标准链：KSampler 直接指向 LoRA
                workflow[nid_ksampler]['inputs']['model'] = [nid_lora_char, 0]

    # LoRA 强度调节：通过 class_type 查找所有 LoRA 节点
    # 注意：LoRA 强度过高会覆盖用户提示词的语义
    # 降低 character 的强度，让基础模型更好地响应用户描述
    _LORA_STRENGTH = {
        "character": {"LoraLoaderModelOnly": 0.6, "LoraLoader": 0.3},
        "scene":     {"LoraLoaderModelOnly": 0.1, "LoraLoader": 0.45},
        "prop":      {"LoraLoaderModelOnly": 0.0, "LoraLoader": 0.45},
        "":          {"LoraLoaderModelOnly": 0.6, "LoraLoader": 0.3},
    }
    lora_cfg = _LORA_STRENGTH.get(content_type, _LORA_STRENGTH[""])
    for lora_type, strength in lora_cfg.items():
        for nid, ndata in find_node_by_class_type(workflow, lora_type):
            if 'strength_model' in ndata.get('inputs', {}):
                workflow[nid]['inputs']['strength_model'] = strength

    # ── 通用节点设置 ──────────────────────────────────────────

    # 1. 设置正向提示词 — 质量 + content_type 触发词 + 用户描述
    _QUALITY_PREFIX = "超高清写实摄影，杰作，最佳质量，8K UHD，raw photo，超高细节，锐利焦点。"

    # 根据用户提示词动态匹配年龄段，返回（正向年龄描述, 负向排除项）
    _age_trigger = _detect_age_in_prompt(positive_prompt)
    _NEGATIVE_AGE_MAP = {
        "child":   "adult face, mature, wrinkle, woman, man, beard, ",
        "teen":    "child, baby, elder, elderly, ",
        "young":   "child, baby, elder, elderly, ",
        "adult":   "child, baby, teenager, ",
        "elder":   "child, baby, young face, ",
        "unknown": "",
    }
    age_neg = _NEGATIVE_AGE_MAP.get(_age_trigger[0], "")

    _TRIGGER_BY_TYPE = {
        "character": f"全身站立从头到脚完整呈现，中心构图，{_age_trigger[1]}",
        "scene":     "广角视角，宏大场景，丰富环境细节，远处有山脉/城市，空无一人，",
        "prop":      "纯黑背景，工作室环形灯打光，微距摄影，极度锐利，边缘清晰，单一物体，",
        "":          "",
    }
    trigger = _TRIGGER_BY_TYPE.get(content_type, "")

    # ⭐ 修复 P0 #5：提示词语义冲突
    # 原：仅检测 trigger 完整字符串是否在 prompt 中（检测不到"近景"vs"全身站立"反义词）
    # 新：检测 prompt 是否已包含任何构图类关键词，若有则跳过强制构图 trigger
    if trigger and content_type == "character":
        # 用户已显式指定构图/景别时，不强制"全身站立"
        _SHOT_KEYWORDS = (
            "近景", "特写", "半身", "肖像", "头像", "胸像", "面部",
            "close-up", "portrait", "headshot", "bust", "face",
            "中景", "坐姿", "蹲", "奔跑", "跳跃", "动作",
        )
        _prompt_lower = positive_prompt.lower()
        if any(kw in positive_prompt or kw in _prompt_lower for kw in _SHOT_KEYWORDS):
            # 保留年龄触发词，移除构图强制词
            _age_only = _age_trigger[1].rstrip("，")
            trigger = f"{_age_only}，" if _age_only else ""
            logger.info(f"[WorkflowBuilder] 检测到景别关键词，跳过强制构图 trigger | prompt={positive_prompt[:40]}")
        elif trigger.rstrip("，") in positive_prompt:
            trigger = ""
    elif trigger and trigger.rstrip("，") in positive_prompt:
        trigger = ""

    # 2. 正负提示词注入 — 通过 KSampler 的连接追踪，避免依赖节点遍历顺序
    _COMMON_NEGATIVE = "close-up shot, portrait, headshot, bust, chest shot, "
    _NEGATIVE_BY_TYPE = {
        "scene": "symmetrical composition, tiled repetition, repetitive pattern, " + (negative_prompt or YAOGUANG_DEFAULT_NEGATIVE),
        "character": _COMMON_NEGATIVE + age_neg + (negative_prompt or YAOGUANG_DEFAULT_NEGATIVE),
        "prop": negative_prompt or YAOGUANG_DEFAULT_NEGATIVE,
        "": negative_prompt or YAOGUANG_DEFAULT_NEGATIVE,
    }
    # 从 KSampler 的 positive/negative 输入找到正确的 CLIPTextEncode 节点
    nid_ksampler, ks_data = find_first_node_by_class_type(workflow, 'KSampler')
    if nid_ksampler and ks_data:
        pos_clip_id = str(ks_data['inputs']['positive'][0])
        neg_clip_id = str(ks_data['inputs']['negative'][0])
        if pos_clip_id in workflow:
            workflow[pos_clip_id]['inputs']['text'] = f"{_QUALITY_PREFIX}{trigger}{positive_prompt}"
        if neg_clip_id in workflow:
            workflow[neg_clip_id]['inputs']['text'] = _NEGATIVE_BY_TYPE.get(content_type, negative_prompt or YAOGUANG_DEFAULT_NEGATIVE)
    else:
        # 降级：按遍历顺序（兼容旧工作流）
        nid_clip, clip_data = find_first_node_by_class_type(workflow, 'CLIPTextEncode')
        if nid_clip and clip_data:
            workflow[nid_clip]['inputs']['text'] = f"{_QUALITY_PREFIX}{trigger}{positive_prompt}"
        clip_nodes = find_node_by_class_type(workflow, 'CLIPTextEncode')
        if len(clip_nodes) >= 2:
            nid_neg = clip_nodes[1][0]
            workflow[nid_neg]['inputs']['text'] = _NEGATIVE_BY_TYPE.get(content_type, negative_prompt or YAOGUANG_DEFAULT_NEGATIVE)

    # 3. 设置图像尺寸（EmptyLatentImage 节点）
    # ⚠️ 仅在用户明确指定 width/height 时覆写，否则保持工作流模板默认值
    # （修复 C1：原代码无 None 检查，会写入 None 破坏工作流）
    if width is not None and height is not None:
        nid_empty, empty_data = find_first_node_by_class_type(workflow, 'EmptyLatentImage')
        if nid_empty and empty_data:
            workflow[nid_empty]['inputs']['width'] = width
            workflow[nid_empty]['inputs']['height'] = height
            logger.info(f"[Workflow] cinematic 工作流尺寸覆写: {width}×{height}")
        else:
            logger.warning(f"[Workflow] 未找到 EmptyLatentImage 节点，无法覆写尺寸")
    else:
        logger.info(f"[Workflow] cinematic 工作流保持模板尺寸不变")

    # 4. 设置种子 — 按 content_type 策略
    nid_ksampler, ks_data = find_first_node_by_class_type(workflow, 'KSampler')
    if nid_ksampler and ks_data:
        if seed is not None:
            workflow[nid_ksampler]['inputs']['seed'] = seed
        elif content_type == "scene":
            workflow[nid_ksampler]['inputs']['seed'] = random.randint(0, 2**63 - 1)
        else:
            workflow[nid_ksampler]['inputs']['seed'] = int(time.time() * 1000) % (2**63)
        if steps is not None:
            workflow[nid_ksampler]['inputs']['steps'] = steps
        if cfg is not None:
            workflow[nid_ksampler]['inputs']['cfg'] = cfg

    # 6. 图生图模式（参考图，Qwen-Image-2.5 / Z-Image 图生图）
    if reference_image:
        logger.info(f"[Workflow] Z-Image图生图模式，参考图: {reference_image}")
        # 动态查找 VAELoader 节点 ID
        nid_vae, _ = find_first_node_by_class_type(workflow, 'VAELoader')
        vae_ref = nid_vae if nid_vae else "11"  # 降级兼容旧工作流
        # 动态查找 KSampler 节点 ID（复用前面已查找的结果）
        nid_ksampler_img, _ = find_first_node_by_class_type(workflow, 'KSampler')
        ksampler_ref = nid_ksampler_img if nid_ksampler_img else "22"
        # 添加 LoadImage 节点（节点99）
        workflow["99"] = {
            "inputs": {"image": reference_image},
            "class_type": "LoadImage",
            "_meta": {"title": "参考图"},
        }
        # 添加 VAEEncode 节点（节点98），将 LoadImage 输出编码为 latent
        workflow["98"] = {
            "inputs": {
                "pixels": ["99", 0],
                "vae": [vae_ref, 0],
            },
            "class_type": "VAEEncode",
            "_meta": {"title": "VAE编码（参考图）"},
        }
        # 将 KSampler 的 latent_image 输入改为 VAEEncode 输出，而非 EmptyLatentImage
        workflow[ksampler_ref]["inputs"]["latent_image"] = ["98", 0]
        # 降低 denoise（图生图模式）
        workflow[ksampler_ref]["inputs"]["denoise"] = 0.85
        workflow["reference_image"] = reference_image
        logger.info(f"[Workflow] 图生图模式已激活（LoadImage→VAEEncode→KSampler, denoise=0.85）")

    # 7. 附加 LoRA（可选）
    if additional_lora and additional_lora in ADDITIONAL_LORAS:
        lora = ADDITIONAL_LORAS[additional_lora]
        pass

    # ⭐ 修复 P1：ParamInjector 兜底注入
    # 手写注入逻辑可能遗漏参数，此处用 ParamInjector 做二次校验
    # 仅对已注册 schema 的 workflow 类型生效；prop 路径节点结构不同，跳过
    try:
        from services.workflow_params import inject_workflow_params
        schema_map = {
            "cinematic": "文生图影视级",
            "standard": "文生图影视级",  # standard 复用同 schema（节点结构相似）
        }
        schema_name = schema_map.get(workflow_type) if not is_prop_workflow else None
        if schema_name:
            # ⭐ 修复 P0 #5：ParamInjector 兜底时排除 prompt/negative
            # 这两个字段已由上方手写注入处理（含 _QUALITY_PREFIX + trigger + 用户描述）
            # 若此处再用原始 positive_prompt 覆盖，会丢失质量前缀和触发词
            user_params = {
                "width": width,
                "height": height,
                "steps": steps,
                "cfg": cfg,
                "seed": seed,
            }
            # 过滤 None 值（避免覆盖工作流模板默认值）
            user_params = {k: v for k, v in user_params.items() if v is not None}
            _, injected = inject_workflow_params(schema_name, workflow, user_params)
            if injected:
                logger.info(f"[Workflow] ParamInjector 兜底注入 ({schema_name}): {list(injected.keys())}")
    except Exception as pie:
        # 兜底失败不影响主流程（手写注入已完成核心参数）
        logger.warning(f"[Workflow] ParamInjector 兜底失败（不影响主流程）: {pie}")

    return workflow


def structured_prompt_to_comfyui_prompt(
    prompt_json: dict,
    custom_text: str = "",
) -> str:
    """
    将结构化提示词 JSON 转为 ComfyUI 正向提示词文本（长句自然语言）。
    """
    if custom_text:
        return custom_text
    # 从 prompt_json 提取描述文本
    if isinstance(prompt_json, dict):
        desc = prompt_json.get("description", "")
        if desc:
            return desc
        # 拼接各字段
        parts = []
        for key in ("type", "subject", "scene", "style", "mood", "description"):
            val = prompt_json.get(key, "")
            if val:
                parts.append(str(val))
        if parts:
            return ", ".join(parts)
    return str(prompt_json) if prompt_json else ""








# ============================================================
# ⭐ V5.0: Fish 融合统一参数 — 无需动态策略函数
# ============================================================
# Fish 工作流双图/三图融合参数完全一致:
#   scheduler=beta57, cfg=1, LoRA=0.25, steps=4, denoise=1, sampler=euler_ancestral
# 所有动态策略函数（_get_lora_strength/_get_secondary_lora/_get_cfg_scale/_infer_pose_type）
# 已删除，参数在模板和 _build_character_fusion_step 中硬编码。


def _get_denoise_sequence() -> List[Tuple[float, str, float]]:
    """返回 Fish 融合步骤序列

    Fish 工作流始终为 1 步融合：
    scheduler=beta57, cfg=1, LoRA=0.25, steps=4, denoise=1
    """
    return [
        (1.0, "primary_char", 1.0),
    ]


# ============================================================
# ⭐ V1.3: 底层工具函数 — 工作流模板操作
# ============================================================

def _load_workflow_template(template_name: str) -> Dict[str, Any]:
    """从 workflows/ 目录加载 JSON 模板"""
    wf_dir = Path(__file__).parent.parent.parent / "workflows"
    wf_path = wf_dir / f"{template_name}.json"
    if not wf_path.exists():
        # 回退：依次尝试多个现有模板
        fallback_names = ["精修优化.json", "1人分镜.json", "GPT分镜.json", "最终文生图.json"]
        for fallback_name in fallback_names:
            fallback = wf_dir / fallback_name
            if fallback.exists():
                logger.info(f"[Workflow] 模板 {template_name}.json 不存在，回退到 {fallback_name}")
                with open(fallback, "r", encoding="utf-8") as f:
                    return json.load(f)
        raise FileNotFoundError(f"工作流模板不存在: {wf_path}")
    with open(wf_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _set_ksampler_params(
    wf: Dict[str, Any],
    denoise: float,
    cfg_scale: float,
    seed: int,
    steps: Optional[int] = None,  # ⭐ V1.11: 可选步数覆写（融合步骤需要更高 steps）
    scheduler: Optional[str] = None,  # ⭐ V4.0: 可选 scheduler 覆写（如 "beta57"）
) -> Dict[str, Any]:
    """覆盖 KSampler 节点的 denoise、cfg、seed、steps 和 scheduler"""
    # 节点 ID 因模板而异，查找第一个 KSampler 类型节点
    for node_id, node in wf.items():
        if isinstance(node, dict) and node.get("class_type") == "KSampler":
            if "denoise" in node.get("inputs", {}):
                old_d = node["inputs"].get("denoise", "?")
                node["inputs"]["denoise"] = denoise
                logger.info(f"[Workflow] KSampler({node_id}) denoise {old_d}→{denoise}")
            if "cfg" in node.get("inputs", {}):
                old_c = node["inputs"].get("cfg", "?")
                node["inputs"]["cfg"] = cfg_scale
                logger.info(f"[Workflow] KSampler({node_id}) cfg {old_c}→{cfg_scale}")
            if "seed" in node.get("inputs", {}):
                old_s = node["inputs"].get("seed", "?")
                if isinstance(old_s, (int, float)) or old_s == "?":
                    node["inputs"]["seed"] = seed
            # ⭐ V1.11: 融合步骤需要更多步数（模板默认4太少了）
            if steps is not None and "steps" in node.get("inputs", {}):
                old_st = node["inputs"].get("steps", "?")
                node["inputs"]["steps"] = steps
                logger.info(f"[Workflow] KSampler({node_id}) steps {old_st}→{steps}")
            # ⭐ V4.0: scheduler 覆写（参考工作流两图/三图融合使用 beta57）
            if scheduler is not None and "scheduler" in node.get("inputs", {}):
                old_sched = node["inputs"].get("scheduler", "?")
                node["inputs"]["scheduler"] = scheduler
                logger.info(f"[Workflow] KSampler({node_id}) scheduler {old_sched}→{scheduler}")
            return wf
    logger.warning("[Workflow] 未找到 KSampler 节点，denoise/cfg/seed 未注入")
    return wf


def _set_reference_image(
    wf: Dict[str, Any],
    node_id: str = "",
    image_path: str = "",
    class_type: str = "LoadImage",
) -> Dict[str, Any]:
    """覆盖 LoadImage 节点的图片路径

    优先使用 class_type 查找节点；如果指定了 node_id 则直接使用（向后兼容）
    """
    resolved = _resolve_comfyui_image(image_path or "")
    if node_id and node_id in wf and "inputs" in wf[node_id]:
        wf[node_id]["inputs"]["image"] = resolved
    else:
        nid, ndata = find_first_node_by_class_type(wf, class_type)
        if nid and ndata and 'image' in ndata.get('inputs', {}):
            wf[nid]['inputs']['image'] = resolved
    return wf


def _set_clip_text(
    wf: Dict[str, Any],
    node_id: str = "",
    text: str = "",
    class_type: str = "",
) -> Dict[str, Any]:
    """覆盖 CLIPTextEncode / QwenImageEditPlusAdvance 节点的文本

    优先使用 class_type 查找节点；如果指定了 node_id 则直接使用（向后兼容）
    ⚠️ CLIP语言兼容：如果底层SDXL CLIP不支持中文，此函数内部需先翻译
    """
    if node_id and node_id in wf and "inputs" in wf[node_id]:
        if "text" in wf[node_id]["inputs"]:
            wf[node_id]["inputs"]["text"] = text
        elif "prompt" in wf[node_id]["inputs"]:
            wf[node_id]["inputs"]["prompt"] = text
    elif class_type:
        nid, ndata = find_first_node_by_class_type_contains(wf, class_type)
        if nid and ndata:
            if "text" in ndata.get('inputs', {}):
                wf[nid]['inputs']['text'] = text
            elif "prompt" in ndata.get('inputs', {}):
                wf[nid]['inputs']['prompt'] = text
    return wf


def _set_filename_prefix(wf: Dict[str, Any], prefix: str) -> Dict[str, Any]:
    """覆盖全部 SaveImage 节点的 filename_prefix"""
    found = 0
    for node_id, node in wf.items():
        if isinstance(node, dict) and node.get("class_type") == "SaveImage":
            node["inputs"]["filename_prefix"] = prefix
            found += 1
    if found == 0:
        logger.warning("[Workflow] 未找到 SaveImage 节点")
    return wf


# ============================================================
# ⭐ V5.0: Fish 融合 — 仅保留 _detect_fusion_type 和 _build_character_fusion_step
# 已删除: _build_scene_optimization_step, _build_group_fusion_step,
#         _build_edge_fix_step, _build_global_optimization_step
# Fish 工作流只需 1 步融合，无场景优化/边缘修复/全局优化。


def _detect_fusion_type(
    reference_images: Dict[str, str],
) -> Dict[str, str]:
    """根据 reference_images 检测融合类型，返回图像槽位映射

    ⭐ V5.0: 不再自动生成复杂提示词，Fisher 用用户原始 prompt
    仅返回图像槽位分配信息。

    返回:
        {
            "type": "char_scene" | "char_char" | "char_prop" | "scene_prop",
            "image_1": str (图1槽位文件名, 节点10),
            "image_2": str (图2槽位文件名 = 场景, 节点11),
            "image_3": str (图3槽位文件名, 节点12),
        }
    """
    scene = reference_images.get("scene", "")
    char1 = reference_images.get("character", "")
    char2 = reference_images.get("character2", "")
    prop = reference_images.get("prop", "")

    has_char1 = bool(char1)
    has_char2 = bool(char2)
    has_scene = bool(scene)
    has_prop = bool(prop)

    result = {
        "image_2": scene,  # 图2(节点11) 永远是场景
    }

    if has_char1 and has_char2:
        result["type"] = "char_char"
        result["image_1"] = char1
        result["image_3"] = char2
    elif has_char1 and has_prop:
        result["type"] = "char_prop"
        result["image_1"] = char1
        result["image_3"] = prop
    elif has_char1 and has_scene:
        result["type"] = "char_scene"
        result["image_1"] = char1
        result["image_3"] = scene
    elif has_prop and has_scene:
        result["type"] = "scene_prop"
        result["image_1"] = prop
        result["image_3"] = scene
    else:
        # 兜底：只有场景或只有角色
        result["type"] = "char_scene"
        result["image_1"] = char1 or prop or scene
        result["image_3"] = scene or char1
        result["prompt"] = "让图1的内容融入图2和图3的环境里，保持原有特征不变。"

    return result


def _build_character_fusion_step(
    character_image: str,
    prompt_text: str,
    seed: int,
    filename_prefix: str,
    original_scene: str = "",
    fusion_info: Optional[Dict[str, str]] = None,
    fusion_mode: str = "3img",
    previous_shot_url: str = "",
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Dict[str, Any]:
    """构建融合步骤工作流 — 完全复刻 Fish 工作流节点结构

    ⭐ V5.0: 简化为 Fish 融合参数，直接使用用户 prompt：
    - scheduler=beta57, cfg=1, LoRA=0.25, steps=4, denoise=1, sampler=euler_ancestral
    - 不使用 DeepSeek 优化、不拼接位置控制提示词
    - 两图/三图融合参数完全一致，唯一区别：2图模式 image3=场景图

    LoadImage 节点映射（按 ID 排序）：
    - 第1个 = 主要元素（角色/道具）
    - 第2个 = 原始场景（环境参考）
    - 第3个 = 次要元素（角色B/道具/双重场景）
    """
    wf = _load_workflow_template("Fish融合")

    logger.info(
        f"[Workflow] Fish融合({fusion_mode}): "
        f"scheduler=beta57, cfg=1, lora=0.25, steps=4, denoise=1"
    )

    # Fish 模板已内置正确参数，只需覆写 seed 和 filename_prefix
    wf = _set_ksampler_params(wf, denoise=1.0, cfg_scale=1.0, seed=seed,
                               steps=4, scheduler="beta57")

    # 获取 LoadImage 节点（按 ID 排序）
    load_nodes = find_node_by_class_type(wf, 'LoadImage')
    load_nodes.sort(key=lambda x: x[0])
    # 便捷访问
    def _set_img(idx, img_path):
        if idx < len(load_nodes) and img_path:
            wf[load_nodes[idx][0]]['inputs']['image'] = img_path
    def _get_img(idx):
        if idx < len(load_nodes):
            return wf[load_nodes[idx][0]]['inputs'].get('image', '')
        return ''

    # ⭐ 图2(第2个 LoadImage) = 原始场景环境参考（永远不变）
    scene_file = _resolve_comfyui_image(original_scene or "")
    if scene_file:
        _set_img(1, scene_file)
    else:
        base_file = _resolve_comfyui_image(character_image or "")
        if base_file:
            _set_img(1, base_file)

    # 兜底值
    base_file = _resolve_comfyui_image(character_image or "")
    fallback_image = base_file or scene_file or ""

    # ⭐ 两图融合模式 — 图3槽位设为与图2相同
    if fusion_mode == "2img":
        img_10_file = fallback_image
        if fusion_info:
            img_10_file = _resolve_comfyui_image(fusion_info.get("image_1", "") or "") or fallback_image
        # 图1 = 主要元素
        _set_img(0, img_10_file)
        # 图3 = 与图2相同（场景），两图融合不需要第三张独立参考图
        if scene_file:
            _set_img(2, scene_file)
    # ⭐ 迭代模式 — 使用上次融合结果作为图1参考
    elif previous_shot_url:
        prev_file = _resolve_comfyui_image(previous_shot_url)
        _set_img(0, prev_file)
        logger.info(f"[Workflow] 迭代模式: 图1使用上次融合结果")
        if fusion_info:
            img_12 = _resolve_comfyui_image(fusion_info.get("image_3", "") or "")
            if img_12:
                _set_img(2, img_12)
            else:
                _set_img(2, scene_file or fallback_image)
        else:
            _set_img(2, scene_file or fallback_image)
    # ⭐ 三图融合默认路径 — 根据 fusion_info 分配图1 和 图3
    elif fusion_info:
        img_10 = _resolve_comfyui_image(fusion_info.get("image_1", "") or "")
        img_12 = _resolve_comfyui_image(fusion_info.get("image_3", "") or "")
        if img_10:
            _set_img(0, img_10)
        else:
            _set_img(0, fallback_image)
        if img_12:
            _set_img(2, img_12)
        else:
            _set_img(2, scene_file or fallback_image)
    else:
        # 无 fusion_info 时：图1=角色, 图3=场景（经典模式）
        char_file = _resolve_comfyui_image(character_image or "")
        if char_file:
            _set_img(0, char_file)
        else:
            _set_img(0, fallback_image)
        _set_img(2, scene_file or fallback_image)

    # ⭐ V5.0: 直接使用用户 prompt
    nid_enc, enc_data = find_first_node_by_class_type_contains(wf, 'QwenImageEditPlusAdvance')
    if nid_enc and enc_data and 'prompt' in enc_data.get('inputs', {}):
        wf[nid_enc]['inputs']['prompt'] = prompt_text
        logger.info(f"[Workflow] Fish融合prompt (type={fusion_info.get('type','?') if fusion_info else 'legacy'}): {prompt_text[:80]}")

    # ⭐ 两图融合模式：image3 连接设为与 image2 相同（复刻 Fish 两图融合）
    if fusion_mode == "2img" and nid_enc and enc_data:
        if "image3" in enc_data.get('inputs', {}):
            # 找到第2个 LoadImage 节点的 ID，作为 image3 的链接源
            if len(load_nodes) >= 2:
                wf[nid_enc]['inputs']['image3'] = [load_nodes[1][0], 0]

    # ⭐ 尺寸覆写：如果指定了 width/height，覆写 EmptyLatentImage 节点
    if width and height:
        nid_el, el_data = find_first_node_by_class_type(wf, 'EmptyLatentImage')
        if nid_el and el_data:
            wf[nid_el]['inputs']['width'] = width
            wf[nid_el]['inputs']['height'] = height
            logger.info(f"[Workflow] Fish融合尺寸覆写: {width}×{height}")

    wf = _set_filename_prefix(wf, f"{filename_prefix}_{seed}")
    return wf


# ============================================================
# ⭐ V6.0: 4个独立分镜模板 — 分镜换装 / 多帧分镜 / 全景图 / 姿态迁移
# ============================================================

def build_costume_change_workflow(
    reference_images: Dict[str, str],
    prompt_text: str,
    seed: Optional[int] = None,
    filename_prefix: str = "costume_change",
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    """分镜换装工作流 — 基于 Fish融合.json（UNETLoader + 3图输入）

    图1(10) = 角色, 图2(11) = 场景, 图3(12) = 服装/道具参考

    Args:
        reference_images: {"character": ..., "scene": ..., "prop": ...}
        prompt_text: 换装指令
        seed: 随机种子
        filename_prefix: 输出文件名前缀
        width: 图像宽度（可选）
        height: 图像高度（可选）

    Returns:
        (workflows列表, step_names列表, metadata字典)
    """
    _t0 = time.time()
    actual_seed = seed or random.randint(0, 2**31 - 1)

    wf = _load_workflow_template("Fish融合")

    # 注入参考图 — 通过 class_type 查找 LoadImage 节点
    char_file = _resolve_comfyui_image(reference_images.get("character", ""))
    scene_file = _resolve_comfyui_image(reference_images.get("scene", ""))
    prop_file = _resolve_comfyui_image(reference_images.get("prop", ""))

    load_nodes = find_node_by_class_type(wf, 'LoadImage')
    # 按节点 ID 排序，确保顺序稳定（节点 10=角色, 11=场景, 12=道具）
    load_nodes.sort(key=lambda x: x[0])
    if len(load_nodes) >= 1 and char_file:
        wf[load_nodes[0][0]]['inputs']['image'] = char_file
    if len(load_nodes) >= 2 and scene_file:
        wf[load_nodes[1][0]]['inputs']['image'] = scene_file
    if len(load_nodes) >= 3 and prop_file:
        wf[load_nodes[2][0]]['inputs']['image'] = prop_file
    elif len(load_nodes) >= 3 and scene_file:
        wf[load_nodes[2][0]]['inputs']['image'] = scene_file

    # 注入提示词 — 通过 class_type 查找 QwenImageEditPlusAdvance 节点
    nid, ndata = find_first_node_by_class_type_contains(wf, 'QwenImageEditPlusAdvance')
    if nid and ndata and 'prompt' in ndata.get('inputs', {}):
        wf[nid]['inputs']['prompt'] = prompt_text

    # 注入种子和参数
    wf = _set_ksampler_params(wf, denoise=1.0, cfg_scale=1.0, seed=actual_seed,
                               steps=4, scheduler="beta57")

    # 尺寸覆写（EmptyLatentImage 节点）
    if width and height:
        nid, ndata = find_first_node_by_class_type(wf, 'EmptyLatentImage')
        if nid and ndata:
            wf[nid]['inputs']['width'] = width
            wf[nid]['inputs']['height'] = height

    wf = _set_filename_prefix(wf, f"{filename_prefix}_{actual_seed}")

    metadata = {
        "template": "costume_change",
        "seed": actual_seed,
        "lora_strength": 0.25,
        "denoise": 1.0,
        "cfg": 1.0,
    }

    logger.info(f"[WorkflowBuilder][分镜换装] 构建完成 | elapsed={time.time()-_t0:.3f}s | nodes={len(wf)}")
    return [wf], ["分镜换装"], metadata


def build_multi_frame_workflow(
    reference_image: str,
    prompt_text: str,
    per_frame_prompts: Optional[List[str]] = None,
    seed: Optional[int] = None,
    filename_prefix: str = "multi_frame",
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    """多帧分镜工作流 — 基于 next-scene LoRA，单图输入 + 逐帧提示词

    使用 多帧分镜.json 模板，通过 next-scene LoRA 逐帧生成。
    节点布局（与 Fish融合.json 结构一致）：
      10: LoadImage（参考图）
      13: ImageScaleToTotalPixels → 16: GetImageSizeAndCount → 17: EmptySD3LatentImage
      18: CLIPLoader, 19: UNETLoader
      20: LoraLoader(Lightning), 21: LoraLoader(next-scene)
      22: ModelSamplingAuraFlow, 23: CFGNorm
      24: TextEncodeQwenImageEditPlus（正向）, 25: TextEncodeQwenImageEditPlus（负向）
      26: KSampler, 28: VAEDecode, 29: SaveImage
      27: VAELoader

    Args:
        reference_image: 参考图像路径
        prompt_text: 全局提示词（用作正向编码 prompt）
        per_frame_prompts: 每帧提示词列表（每行一个 Next Scene 指令）
        seed: 随机种子
        filename_prefix: 输出文件名前缀
        width: 图像宽度（可选）
        height: 图像高度（可选）

    Returns:
        (workflows列表, step_names列表, metadata字典)
    """
    _t0 = time.time()
    actual_seed = seed or random.randint(0, 2**31 - 1)

    wf = _load_workflow_template("多帧分镜")

    # 注入参考图（LoadImage 节点）
    ref_file = _resolve_comfyui_image(reference_image)
    nid, ndata = find_first_node_by_class_type(wf, 'LoadImage')
    if nid and ndata and ref_file:
        wf[nid]['inputs']['image'] = ref_file

    # 注入提示词（TextEncodeQwenImageEditPlus 正向编码）
    nid_enc, enc_data = find_first_node_by_class_type_contains(wf, 'QwenImageEditPlusAdvance')
    if nid_enc and enc_data:
        if per_frame_prompts and len(per_frame_prompts) > 0:
            wf[nid_enc]['inputs']['prompt'] = "\n".join(per_frame_prompts)
            logger.info(f"[WorkflowBuilder][多帧分镜] 注入 {len(per_frame_prompts)} 帧提示词")
        elif prompt_text:
            wf[nid_enc]['inputs']['prompt'] = prompt_text

    # 注入种子（KSampler 节点）
    nid_ks, ks_data = find_first_node_by_class_type(wf, 'KSampler')
    if nid_ks and ks_data:
        wf[nid_ks]['inputs']['seed'] = actual_seed

    # 尺寸覆写（EmptyLatentImage 节点）
    if width and height:
        nid, ndata = find_first_node_by_class_type(wf, 'EmptyLatentImage')
        if nid and ndata:
            wf[nid]['inputs']['width'] = width
            wf[nid]['inputs']['height'] = height

    # 设置输出前缀（SaveImage 节点）
    nid_save, save_data = find_first_node_by_class_type(wf, 'SaveImage')
    if nid_save and save_data:
        wf[nid_save]['inputs']['filename_prefix'] = f"{filename_prefix}_{actual_seed}"

    frame_count = len(per_frame_prompts) if per_frame_prompts else 1
    metadata = {
        "template": "multi_frame",
        "seed": actual_seed,
        "frame_count": frame_count,
        "next_scene_lora": True,
    }

    logger.info(f"[WorkflowBuilder][多帧分镜] 构建完成 | elapsed={time.time()-_t0:.3f}s | frames={frame_count} | nodes={len(wf)}")
    return [wf], ["多帧分镜"], metadata


def build_panorama_workflow(
    reference_image: str,
    prompt_text: str,
    seed: Optional[int] = None,
    filename_prefix: str = "panorama",
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    """全景图工作流 — 从场景图生成360度全景图

    使用 workflows/真正全景图.json 模板。

    模板关键节点:
      - LoadImage(538): 参考图
      - PrimitiveStringMultiline(540): 提示词
      - KSampler(446): 主生成
      - KSampler(263): inpainting 接缝修复
      - SaveImage(541): 最终拼接输出

    Args:
        reference_image: 参考图像路径
        prompt_text: 全景生成指令
        seed: 随机种子
        filename_prefix: 输出文件名前缀
        width: 图像宽度（可选，默认模板 2048）
        height: 图像高度（可选，默认模板 1024）

    Returns:
        (workflows列表, step_names列表, metadata字典)
    """
    _t0 = time.time()
    actual_seed = seed or random.randint(0, 2**31 - 1)

    wf = _load_workflow_template("真正全景图")

    # 注入参考图 → LoadImage(538)
    ref_file = _resolve_comfyui_image(reference_image)
    if ref_file and "538" in wf:
        wf["538"]["inputs"]["image"] = ref_file

    # 注入提示词 → PrimitiveStringMultiline(540)
    if prompt_text and "540" in wf:
        wf["540"]["inputs"]["value"] = prompt_text

    # 注入 seed → KSampler(446) 主生成 + KSampler(263) inpainting
    for ks_id in ["446", "263"]:
        if ks_id in wf and "seed" in wf[ks_id].get("inputs", {}):
            wf[ks_id]["inputs"]["seed"] = actual_seed

    # 尺寸覆写（EmptySD3LatentImage 节点 436）
    if width and height and "436" in wf:
        wf["436"]["inputs"]["width"] = width
        wf["436"]["inputs"]["height"] = height

    # SaveImage prefix 统一
    wf = _set_filename_prefix(wf, f"{filename_prefix}_{actual_seed}")

    # 最终拼接图 SaveImage(541) 使用特殊前缀，便于从多个输出中识别
    if "541" in wf:
        wf["541"]["inputs"]["filename_prefix"] = f"panorama_final_{actual_seed}"

    metadata = {
        "template": "panorama",
        "seed": actual_seed,
        "denoise": 1.0,
    }

    logger.info(f"[WorkflowBuilder][全景图] 构建完成 | elapsed={time.time()-_t0:.3f}s | nodes={len(wf)}")
    return [wf], ["全景图"], metadata


def build_pose_transfer_workflow(
    character_image: str,
    pose_reference_image: str,
    prompt_text: str,
    seed: Optional[int] = None,
    filename_prefix: str = "pose_transfer",
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    """姿态迁移工作流 — 人物图 + 姿态参考图，改变人物姿态

    使用 姿态迁移.json 模板。
    图1(10) = 人物, 图2(11) = 姿态参考

    Args:
        character_image: 人物图像路径
        pose_reference_image: 姿态参考图像路径
        prompt_text: 姿态迁移指令
        seed: 随机种子
        filename_prefix: 输出文件名前缀
        width: 图像宽度（可选）
        height: 图像高度（可选）

    Returns:
        (workflows列表, step_names列表, metadata字典)
    """
    _t0 = time.time()
    actual_seed = seed or random.randint(0, 2**31 - 1)

    wf = _load_workflow_template("姿态迁移")

    # 注入人物图和姿态参考图（LoadImage 节点，按 ID 排序确保顺序稳定）
    char_file = _resolve_comfyui_image(character_image)
    pose_file = _resolve_comfyui_image(pose_reference_image)

    load_nodes = find_node_by_class_type(wf, 'LoadImage')
    load_nodes.sort(key=lambda x: x[0])
    if len(load_nodes) >= 1 and char_file:
        wf[load_nodes[0][0]]['inputs']['image'] = char_file
    if len(load_nodes) >= 2 and pose_file:
        wf[load_nodes[1][0]]['inputs']['image'] = pose_file

    # 注入提示词（TextEncodeQwenImageEditPlus 节点）
    nid_enc, enc_data = find_first_node_by_class_type_contains(wf, 'QwenImageEditPlusAdvance')
    if nid_enc and enc_data and 'prompt' in enc_data.get('inputs', {}):
        wf[nid_enc]['inputs']['prompt'] = prompt_text

    # 注入种子和参数
    wf = _set_ksampler_params(wf, denoise=1.0, cfg_scale=1.0, seed=actual_seed,
                               steps=4, scheduler="beta57")

    # 尺寸覆写（EmptyLatentImage 节点）
    if width and height:
        nid, ndata = find_first_node_by_class_type(wf, 'EmptyLatentImage')
        if nid and ndata:
            wf[nid]['inputs']['width'] = width
            wf[nid]['inputs']['height'] = height

    wf = _set_filename_prefix(wf, f"{filename_prefix}_{actual_seed}")

    metadata = {
        "template": "pose_transfer",
        "seed": actual_seed,
        "lora_strength": 0.25,
        "denoise": 1.0,
        "cfg": 1.0,
    }

    logger.info(f"[WorkflowBuilder][姿态迁移] 构建完成 | elapsed={time.time()-_t0:.3f}s | nodes={len(wf)}")
    return [wf], ["姿态迁移"], metadata


def build_upscale_workflow(
    reference_image: str,
    seed: Optional[int] = None,
    filename_prefix: str = "upscale",
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    """超分放大工作流 — 使用 SeedVR2 模型放大单张图片

    使用 workflows/放大工作流.json 模板。
    通过 class_type 动态查找节点。

    Args:
        reference_image: 参考图像路径
        seed: 随机种子
        filename_prefix: 输出文件名前缀

    Returns:
        (workflows列表, step_names列表, metadata字典)
    """
    _t0 = time.time()
    actual_seed = seed or random.randint(0, 2**31 - 1)

    wf = _load_workflow_template("放大工作流")

    # 注入参考图（LoadImage 节点）
    ref_file = _resolve_comfyui_image(reference_image)
    nid, ndata = find_first_node_by_class_type(wf, 'LoadImage')
    if nid and ndata and ref_file:
        wf[nid]['inputs']['image'] = ref_file
        # 校验文件路径和大小
        src_path = os.path.join(_COMFYUI_OUTPUT_DIR, ref_file)
        dst_path = os.path.join(_COMFYUI_INPUT_DIR, ref_file)
        src_sz = os.path.getsize(src_path) if os.path.exists(src_path) else 0
        dst_sz = os.path.getsize(dst_path) if os.path.exists(dst_path) else 0
        import hashlib
        src_hash = ""
        dst_hash = ""
        if os.path.exists(src_path):
            with open(src_path, "rb") as f:
                src_hash = hashlib.md5(f.read()).hexdigest()[:12]
        if os.path.exists(dst_path):
            with open(dst_path, "rb") as f:
                dst_hash = hashlib.md5(f.read()).hexdigest()[:12]
        logger.info(
            f"[WorkflowBuilder][超分] LoadImage节点{nid} 注入图片: {ref_file}"
            f" | output({src_sz}B, md5={src_hash})"
            f" | input({dst_sz}B, md5={dst_hash})"
            f" | match={src_hash == dst_hash if src_hash and dst_hash else 'N/A'}"
        )
    elif not ref_file:
        logger.warning(f"[WorkflowBuilder][超分] 参考图路径为空: {reference_image}")

    # 注入种子（SeedVR2VideoUpscaler 节点）
    nid_seedvr, _ = find_first_node_by_class_type(wf, 'SeedVR2VideoUpscaler')
    if nid_seedvr:
        wf[nid_seedvr]['inputs']['seed'] = actual_seed

    wf = _set_filename_prefix(wf, f"{filename_prefix}_{actual_seed}")

    metadata = {
        "template": "upscale",
        "seed": actual_seed,
    }

    logger.info(f"[WorkflowBuilder][超分] 构建完成 | elapsed={time.time()-_t0:.3f}s | nodes={len(wf)}")

    # 打印完整工作流 JSON 用于比对（仅 debug 级别）
    logger.debug(f"[WorkflowBuilder][超分] 工作流详情: {json.dumps(wf, ensure_ascii=False, indent=2)}")
    return [wf], ["超分放大"], metadata


# ============================================================
# ⭐ V5.0: build_storyboard_workflow_v2 — Fish 融合 1 步直出
# ⭐ V6.0: 支持 template 参数路由到4个独立模板
# ============================================================

# 分镜模板类型枚举
STORYBOARD_TEMPLATES = [
    "costume_change", "multi_frame", "panorama", "pose_transfer",
    "upscale", "3view", "pose_extraction", "lineart_extraction", "depth_map",
    # ⭐ 4套定制分镜模板
    "single_person", "dual_person", "local_multi", "gpt_storyboard",
]


def build_3view_workflow(
    reference_image: str,
    seed: Optional[int] = None,
    filename_prefix: str = "3view",
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    """三视图工作流 — 从单张概念图生成正面/背面/侧面三视图

    使用 workflows/3视图.json 模板，模板中已内置各个视角的默认提示词。
    基于 Qwen Image Edit + multiple-angles LoRA 生成3个视角，
    最后拼接为一张总图。

    Args:
        reference_image: 参考图像路径（ComfyUI output/input 中的文件名或 URL）
        seed: 随机种子（3个 KSampler 共用同一 seed 保证一致性）
        filename_prefix: 输出文件名前缀

    Returns:
        (workflows列表, step_names列表, metadata字典)
    """
    _t0 = time.time()
    actual_seed = seed or random.randint(0, 2**31 - 1)

    wf = _load_workflow_template("3视图")

    # 注入参考图（LoadImage 节点）
    ref_file = _resolve_comfyui_image(reference_image)
    nid_load, _ = find_first_node_by_class_type(wf, 'LoadImage')
    if nid_load and ref_file:
        wf[nid_load]['inputs']['image'] = ref_file
        logger.info(f"[WorkflowBuilder][3视图] LoadImage节点{nid_load} 注入图片: {ref_file}")
    elif not ref_file:
        logger.warning(f"[WorkflowBuilder][3视图] 参考图路径为空: {reference_image}")

    # 注入 seed 到所有 KSampler 节点（3个视角共用同一 seed）
    ksampler_nodes = find_node_by_class_type(wf, 'KSampler')
    for nid, ndata in ksampler_nodes:
        old_seed = ndata['inputs'].get('seed', '?')
        ndata['inputs']['seed'] = actual_seed
        logger.info(f"[WorkflowBuilder][3视图] KSampler({nid}) seed {old_seed}→{actual_seed}")

    # 设置所有 SaveImage 节点的 filename_prefix
    save_nodes = find_node_by_class_type(wf, 'SaveImage')
    for nid, ndata in save_nodes:
        ndata['inputs']['filename_prefix'] = f"{filename_prefix}_{actual_seed}"
    logger.info(f"[WorkflowBuilder][3视图] 设置{len(save_nodes)}个SaveImage节点 prefix={filename_prefix}_{actual_seed}")

    metadata = {
        "template": "3view",
        "seed": actual_seed,
        "reference_image": ref_file,
        "view_count": 3,
    }

    logger.info(f"[WorkflowBuilder][3视图] 构建完成 | elapsed={time.time()-_t0:.3f}s | nodes={len(wf)} | ksamplers={len(ksampler_nodes)}")
    return [wf], ["三视图生成"], metadata


# 提取类工作流（姿态/线稿/深度图）
_EXTRACTION_TEMPLATES = {
    "pose_extraction": "姿态迁移骨骼图.json",
    "lineart_extraction": "lineart_extraction.json",
    "depth_map": "depth_map.json",
    "extract_all": "三个骨架图.json",
}

def build_extraction_workflow(
    reference_image: str,
    template: str,
    filename_prefix: str = "extraction",
    seed: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    """构建提取类工作流（姿态/线稿/深度图）"""
    wf_name = _EXTRACTION_TEMPLATES.get(template, "")
    if not wf_name:
        raise ValueError(f"未知提取模板: {template}")

    logger.info(f"[WorkflowBuilder][提取] 加载模板 | template={template} | wf_name={wf_name}")
    wf = _load_workflow_template(wf_name.replace(".json", ""))
    if not wf:
        raise FileNotFoundError(f"提取工作流模板不存在: {wf_name}")
    logger.info(f"[WorkflowBuilder][提取] 模板加载成功 | template={template} | nodes={len(wf)}")

    # 注入参考图到 LoadImage 节点
    ref_file = _resolve_comfyui_image(reference_image)
    nid_load, _ = find_first_node_by_class_type(wf, 'LoadImage')
    if nid_load and ref_file:
        wf[nid_load]['inputs']['image'] = ref_file
    elif not ref_file:
        logger.warning(f"[WorkflowBuilder][提取] 参考图路径为空: {reference_image}")

    # 设置种子
    actual_seed = seed or random.randint(0, 2**31 - 1)
    for nid, ndata in find_node_by_class_type(wf, 'KSampler'):
        ndata['inputs']['seed'] = actual_seed

    # 设置 SaveImage prefix — 根据上游节点类型智能识别，而非依赖遍历顺序
    if template == "extract_all":
        _infer_map = {
            "lineart": ["lineart", "line_art", "canny", "hed", "scribble"],
            "depth": ["depth", "midas", "zoe", "leres"],
            "pose": ["pose", "openpose", "dwpose", "dwpreprocessor", "keypoint", "sdpose"],
        }
        for nid, ndata in find_node_by_class_type(wf, 'SaveImage'):
            type_tag = _infer_saveimage_type(wf, nid, _infer_map)
            if filename_prefix and filename_prefix != "extraction":
                ndata['inputs']['filename_prefix'] = f"{filename_prefix}_{type_tag}"
            else:
                ndata['inputs']['filename_prefix'] = f"{type_tag}_{actual_seed}"
    else:
        for i, (nid, ndata) in enumerate(find_node_by_class_type(wf, 'SaveImage')):
            ndata['inputs']['filename_prefix'] = f"{filename_prefix}_{actual_seed}"

    metadata = {"template": template, "seed": actual_seed, "reference_image": ref_file}
    return [wf], [f"{template}提取"], metadata


def build_multi_person_workflow(
    char_a_image: str,
    char_b_image: str,
    mask_image: str = "",
    depth_image: str = "",
    pose_image: str = "",
    prompt_text: str = "",
    seed: Optional[int] = None,
    filename_prefix: str = "multi_person",
    use_controlnet: bool = True,
    template_name: str = "",
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    """多人分镜三元约束工作流 — 蒙版+深度图+OpenPose 三重约束

    使用 workflows/多人分镜三元约束.json 模板。
    模板关键节点:
      - LoadImage(1): 人物A
      - LoadImage(2): 人物B
      - LoadImage(3): 模板蒙版
      - LoadImage(4): 清场深度图
      - LoadImage(5): OpenPose骨架
      - PrimitiveStringMultiline(40): 提示词
      - ControlNetApply(52): 深度约束 (strength=0.8)
      - ControlNetApply(53): 姿态约束 (strength=0.45)
      - TextEncodeQwenImageEditPlusAdvance(60): 多人融合文本编码
      - KSampler(70): 4步闪电采样
      - SaveImage(72): 输出

    Args:
        char_a_image: 人物A图像路径
        char_b_image: 人物B图像路径
        mask_image: 蒙版图像路径（可选）
        depth_image: 深度图路径（可选，用于ControlNet约束）
        pose_image: OpenPose骨架图路径（可选，用于ControlNet约束）
        prompt_text: 提示词
        seed: 随机种子
        filename_prefix: 输出文件名前缀
        use_controlnet: 是否启用ControlNet约束（需要安装对应模型）
        template_name: 模板名称（如T01_双人正面对话，用于日志）

    Returns:
        (workflows列表, step_names列表, metadata字典)
    """
    _t0 = time.time()
    actual_seed = seed or random.randint(0, 2**31 - 1)

    wf = _load_workflow_template("多人分镜三元约束")

    # 注入人物A → LoadImage(1)
    char_a_file = _resolve_comfyui_image(char_a_image)
    if char_a_file and "1" in wf:
        wf["1"]["inputs"]["image"] = char_a_file

    # 注入人物B → LoadImage(2)
    char_b_file = _resolve_comfyui_image(char_b_image)
    if char_b_file and "2" in wf:
        wf["2"]["inputs"]["image"] = char_b_file

    # 注入蒙版 → LoadImage(3) — 未提供时从模板目录自动加载
    if not mask_image and template_name:
        mask_image = _resolve_template_asset(template_name, "mask")
    mask_file = _resolve_comfyui_image(mask_image) if mask_image else ""
    if mask_file and "3" in wf:
        wf["3"]["inputs"]["image"] = mask_file

    # 注入深度图 → LoadImage(4) — 未提供时从模板目录自动加载
    if not depth_image and template_name:
        depth_image = _resolve_template_asset(template_name, "depth_clean")
    depth_file = _resolve_comfyui_image(depth_image) if depth_image else ""
    if depth_file and "4" in wf:
        wf["4"]["inputs"]["image"] = depth_file

    # 注入OpenPose → LoadImage(5) — 未提供时从模板目录自动加载
    if not pose_image and template_name:
        pose_image = _resolve_template_asset(template_name, "pose")
    pose_file = _resolve_comfyui_image(pose_image) if pose_image else ""
    if pose_file and "5" in wf:
        wf["5"]["inputs"]["image"] = pose_file

    # 注入提示词 → PrimitiveStringMultiline(40)
    if prompt_text and "40" in wf:
        wf["40"]["inputs"]["value"] = prompt_text

    # 注入 seed → KSampler(70)
    if "70" in wf and "seed" in wf["70"].get("inputs", {}):
        wf["70"]["inputs"]["seed"] = actual_seed

    # ControlNet 开关：如果禁用或缺少深度/姿态图，移除ControlNet节点并重连
    skip_depth_cn = (not use_controlnet) or (not depth_file)
    skip_pose_cn = (not use_controlnet) or (not pose_file)

    if skip_depth_cn and skip_pose_cn:
        # 完全移除ControlNet，KSampler positive 直连 TextEncode(60)
        for cn_id in ["50", "51", "52", "53"]:
            wf.pop(cn_id, None)
        if "70" in wf:
            wf["70"]["inputs"]["positive"] = ["60", 0]
        logger.info(f"[WorkflowBuilder][多人分镜] ControlNet已禁用（缺少深度/姿态图或use_controlnet=False）")
    elif skip_depth_cn:
        # 仅移除深度ControlNet，姿态ControlNet直连TextEncode
        for cn_id in ["50", "52"]:
            wf.pop(cn_id, None)
        if "53" in wf:
            wf["53"]["inputs"]["conditioning"] = ["60", 0]
        logger.info(f"[WorkflowBuilder][多人分镜] 深度ControlNet已禁用（缺少深度图）")
    elif skip_pose_cn:
        # 仅移除姿态ControlNet，KSampler positive 连深度ControlNet输出
        for cn_id in ["51", "53"]:
            wf.pop(cn_id, None)
        if "70" in wf:
            wf["70"]["inputs"]["positive"] = ["52", 0]
        logger.info(f"[WorkflowBuilder][多人分镜] 姿态ControlNet已禁用（缺少姿态图）")

    # 设置输出前缀
    wf = _set_filename_prefix(wf, f"{filename_prefix}_{actual_seed}")

    metadata = {
        "template": "multi_person",
        "seed": actual_seed,
        "char_a": char_a_file,
        "char_b": char_b_file,
        "mask": mask_file,
        "depth": depth_file,
        "pose": pose_file,
        "use_controlnet": use_controlnet and (bool(depth_file) or bool(pose_file)),
        "template_name": template_name,
    }

    logger.info(
        f"[WorkflowBuilder][多人分镜] 构建完成 | elapsed={time.time()-_t0:.3f}s | "
        f"nodes={len(wf)} | template={template_name} | controlnet={metadata['use_controlnet']}"
    )
    return [wf], [f"多人分镜({template_name})" if template_name else "多人分镜"], metadata


def build_layered_render_workflow(
    char_a_image: str,
    char_b_image: str,
    char_c_image: str = "",
    char_d_image: str = "",
    mask_image: str = "",
    depth_image: str = "",
    prompt_a: str = "",
    prompt_b: str = "",
    seed: Optional[int] = None,
    filename_prefix: str = "layered_render",
    template_name: str = "",
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    """分层渲染工作流 — 4-5人场景分A/B组生成

    使用 workflows/分层渲染.json 模板（单组模板），分两步执行：
    Step 1: A组生成（人物1 + 人物2 + 深度图约束）
    Step 2: B组生成（人物3 + 人物4 + 深度图约束）

    模板关键节点:
      - LoadImage(100): 共享清场深度图
      - LoadImage(130/131): 人物1/2
      - PrimitiveStringMultiline(160): 提示词
      - ControlNetApply(171): 深度约束
      - KSampler(190): 采样器
      - SaveImage(200): 输出

    Args:
        char_a_image: A组人物1图像路径
        char_b_image: A组人物2图像路径
        char_c_image: B组人物1图像路径（可选）
        char_d_image: B组人物2图像路径（可选）
        mask_image: 完整蒙版图像路径（暂未使用，预留）
        depth_image: 共享清场深度图路径
        prompt_a: A组提示词
        prompt_b: B组提示词
        seed: 随机种子
        filename_prefix: 输出文件名前缀
        template_name: 模板名称（如T09_四人围坐）

    Returns:
        (workflows列表, step_names列表, metadata字典)
    """
    import copy
    _t0 = time.time()
    actual_seed = seed or random.randint(0, 2**31 - 1)

    # ── Step 1: A组工作流 ──
    wf_a = _load_workflow_template("分层渲染")

    # 注入A组人物
    char_a_file = _resolve_comfyui_image(char_a_image)
    if char_a_file and "130" in wf_a:
        wf_a["130"]["inputs"]["image"] = char_a_file

    char_b_file = _resolve_comfyui_image(char_b_image)
    if char_b_file and "131" in wf_a:
        wf_a["131"]["inputs"]["image"] = char_b_file

    # 注入深度图
    depth_file = _resolve_comfyui_image(depth_image) if depth_image else ""
    if depth_file and "100" in wf_a:
        wf_a["100"]["inputs"]["image"] = depth_file

    # 注入A组提示词
    if prompt_a and "160" in wf_a:
        wf_a["160"]["inputs"]["value"] = prompt_a

    # 注入 seed
    if "190" in wf_a and "seed" in wf_a["190"].get("inputs", {}):
        wf_a["190"]["inputs"]["seed"] = actual_seed

    # 设置A组输出前缀
    if "200" in wf_a:
        wf_a["200"]["inputs"]["filename_prefix"] = f"{filename_prefix}_layerA_{actual_seed}"

    # 如果没有深度图，移除深度ControlNet
    if not depth_file:
        if "171" in wf_a:
            # 将 positive 条件从 ControlNetApply 改为直接从 TextEncode 输出
            wf_a.pop("171", None)
            # KSampler 的 positive 需要直接连接到 TextEncode 输出
            if "190" in wf_a:
                wf_a["190"]["inputs"]["positive"] = ["170", 0]
        if "150" in wf_a:
            wf_a.pop("150", None)
        logger.info("[WorkflowBuilder][分层渲染] A组无深度图，移除深度ControlNet")

    workflows = [wf_a]
    step_names = [f"A组({template_name})" if template_name else "A组"]

    # ── Step 2: B组工作流（如果有B组人物） ──
    char_c_file = _resolve_comfyui_image(char_c_image) if char_c_image else ""
    char_d_file = _resolve_comfyui_image(char_d_image) if char_d_image else ""

    if char_c_file or char_d_file:
        wf_b = copy.deepcopy(_load_workflow_template("分层渲染"))

        # 注入B组人物到 130/131 节点
        if char_c_file and "130" in wf_b:
            wf_b["130"]["inputs"]["image"] = char_c_file
        if char_d_file and "131" in wf_b:
            wf_b["131"]["inputs"]["image"] = char_d_file

        # 注入深度图
        if depth_file and "100" in wf_b:
            wf_b["100"]["inputs"]["image"] = depth_file

        # 注入B组提示词
        if prompt_b and "160" in wf_b:
            wf_b["160"]["inputs"]["value"] = prompt_b

        # 注入 seed（B组用 seed+1 避免完全相同）
        if "190" in wf_b and "seed" in wf_b["190"].get("inputs", {}):
            wf_b["190"]["inputs"]["seed"] = actual_seed + 1

        # 设置B组输出前缀
        if "200" in wf_b:
            wf_b["200"]["inputs"]["filename_prefix"] = f"{filename_prefix}_layerB_{actual_seed}"

        # 如果没有深度图，移除深度ControlNet
        if not depth_file:
            if "171" in wf_b:
                wf_b.pop("171", None)
                if "190" in wf_b:
                    wf_b["190"]["inputs"]["positive"] = ["170", 0]
            if "150" in wf_b:
                wf_b.pop("150", None)

        workflows.append(wf_b)
        step_names.append(f"B组({template_name})" if template_name else "B组")

    metadata = {
        "template": "layered_render",
        "seed": actual_seed,
        "char_a": char_a_file,
        "char_b": char_b_file,
        "char_c": char_c_file,
        "char_d": char_d_file,
        "mask": mask_image,
        "depth": depth_file,
        "template_name": template_name,
        "steps": len(workflows),
    }

    logger.info(
        f"[WorkflowBuilder][分层渲染] 构建完成 | elapsed={time.time()-_t0:.3f}s | "
        f"steps={len(workflows)} | template={template_name}"
    )
    return workflows, step_names, metadata


def build_template_clean_workflow(
    reference_image: str,
    depth_image: str = "",
    filename_prefix: str = "template_clean",
    seed: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    """模板清场+蒙版生成工作流（简化版）

    仅执行 SAM2 人物检测 + 保存原始蒙版。
    蒙版后处理（收缩+羽化）和深度图清场（OpenCV inpaint）移至后端 stage。
    不再调用 Qwen Image Edit，节省 55s 生成时间和 9GB 显存。
    """
    _t0 = time.time()

    wf = _load_workflow_template("模板清场+蒙版")

    # 注入参考构图图到 LoadImage 节点
    ref_file = _resolve_comfyui_image(reference_image)
    load_nodes = find_node_by_class_type(wf, 'LoadImage')
    if load_nodes:
        nid, ndata = load_nodes[0]
        ndata['inputs']['image'] = ref_file
        logger.info(f"[TemplateClean] 参考图 → node {nid}: {ref_file}")

    # 简化工作流只有 1 个 SaveImage（mask_raw）
    for nid, ndata in find_node_by_class_type(wf, 'SaveImage'):
        ndata['inputs']['filename_prefix'] = f"{filename_prefix}_mask_raw"

    metadata = {
        "template": "template_clean",
        "reference": ref_file,
    }

    logger.info(
        f"[WorkflowBuilder][模板清场] 构建完成 | elapsed={time.time()-_t0:.3f}s | "
        f"ref={ref_file} | nodes={len(wf)}"
    )
    return [wf], ["模板清场+蒙版"], metadata


def build_template_pose_workflow(
    reference_image: str,
    filename_prefix: str = "template_pose",
    seed: Optional[int] = None,
    joint_radius: int = 5,
    line_width: int = 3,
    head_radius: int = 8,
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    """模板Pose优化工作流 — 从完整OpenPose骨架图生成简化7节点骨架

    输入：原始Pose骨架图
    输出：简化Pose图（只有头、肩、肘、胯、膝、脚）
    """
    _t0 = time.time()
    actual_seed = seed or random.randint(0, 2**31 - 1)

    wf = _load_workflow_template("模板Pose优化")

    # 注入参考Pose图到 LoadImage 节点
    ref_file = _resolve_comfyui_image(reference_image)
    load_nodes = find_node_by_class_type(wf, 'LoadImage')
    if load_nodes:
        nid, ndata = load_nodes[0]
        ndata['inputs']['image'] = ref_file
        logger.info(f"[TemplatePose] 参考Pose → node {nid}: {ref_file}")

    # 注入 SimplifiedPoseRenderer 参数
    for nid, ndata in wf.items():
        if isinstance(ndata, dict) and ndata.get('class_type') == 'SimplifiedPoseRenderer':
            ndata['inputs']['joint_radius'] = joint_radius
            ndata['inputs']['line_width'] = line_width
            ndata['inputs']['head_radius'] = head_radius

    # 设置 SaveImage filename_prefix
    for i, (nid, ndata) in enumerate(find_node_by_class_type(wf, 'SaveImage')):
        ndata['inputs']['filename_prefix'] = f"{filename_prefix}_pose"

    metadata = {
        "template": "template_pose",
        "seed": actual_seed,
        "reference": ref_file,
        "joint_radius": joint_radius,
        "line_width": line_width,
        "head_radius": head_radius,
    }

    logger.info(
        f"[WorkflowBuilder][模板Pose] 构建完成 | elapsed={time.time()-_t0:.3f}s | "
        f"ref={ref_file}"
    )
    return [wf], ["模板Pose优化"], metadata


def build_storyboard_workflow_v2(
    reference_images: Dict[str, str],
    prompt_text: str,
    seed: Optional[int] = None,
    filename_prefix: str = "storyboard",
    character_count: int = 1,
    fusion_mode: str = "3img",
    previous_shot_url: str = "",
    width: Optional[int] = None,
    height: Optional[int] = None,
    template: Optional[str] = None,
    per_frame_prompts: Optional[List[str]] = None,
    pose_reference_image: str = "",
    **kwargs,  # 透传额外参数到子工作流构建器
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    """分镜工作流构建器 — 支持模板路由

    ⭐ V6.0: 根据 template 参数路由到4个独立模板：
    - "costume_change": 分镜换装（Fish融合, 3图输入）
    - "multi_frame": 多帧分镜（next-scene LoRA, 逐帧生成）
    - "panorama": 全景图（单图输入, 全景视角）
    - "pose_transfer": 姿态迁移（人物图+姿态参考图）
    - None/默认: 兼容旧版 Fish 融合逻辑

    Args:
        reference_images: {"character": ..., "scene": ..., "character2": ..., "prop": ...}
        prompt_text: 分镜指令
        seed: 随机种子
        filename_prefix: 输出文件名前缀
        character_count: 角色数量
        fusion_mode: "2img" 两图融合 或 "3img" 三图融合
        previous_shot_url: 基于上次融合结果的迭代参考图
        width: 图像宽度（可选）
        height: 图像高度（可选）
        template: 模板类型（costume_change/multi_frame/panorama/pose_transfer）
        per_frame_prompts: 多帧分镜的每帧提示词列表
        pose_reference_image: 姿态迁移的参考图路径

    Returns:
        (workflows列表, step_names列表, metadata字典)
    """
    _t0 = time.time()
    actual_seed = seed or random.randint(0, 2**31 - 1)

    # ⭐ V6.0: 模板路由
    if template == "costume_change":
        return build_costume_change_workflow(
            reference_images=reference_images,
            prompt_text=prompt_text,
            seed=actual_seed,
            filename_prefix=filename_prefix,
            width=width,
            height=height,
        )
    elif template == "multi_frame":
        # 多帧分镜使用场景图作为参考
        ref_image = reference_images.get("scene", "") or reference_images.get("character", "")
        return build_multi_frame_workflow(
            reference_image=ref_image,
            prompt_text=prompt_text,
            per_frame_prompts=per_frame_prompts,
            seed=actual_seed,
            filename_prefix=filename_prefix,
            width=width,
            height=height,
        )
    elif template == "panorama":
        ref_image = reference_images.get("scene", "") or reference_images.get("character", "")
        return build_panorama_workflow(
            reference_image=ref_image,
            prompt_text=prompt_text,
            seed=actual_seed,
            filename_prefix=filename_prefix,
            width=width,
            height=height,
        )
    elif template == "pose_transfer":
        char_image = reference_images.get("character", "")
        pose_ref = pose_reference_image or reference_images.get("scene", "")
        return build_pose_transfer_workflow(
            character_image=char_image,
            pose_reference_image=pose_ref,
            prompt_text=prompt_text,
            seed=actual_seed,
            filename_prefix=filename_prefix,
            width=width,
            height=height,
        )
    elif template == "upscale":
        # 超分放大：从 reference_images 获取参考图
        ref_image = (reference_images.get("reference", "") or
                     reference_images.get("character", "") or
                     reference_images.get("scene", "") or "")
        return build_upscale_workflow(
            reference_image=ref_image,
            seed=actual_seed,
            filename_prefix=filename_prefix,
        )
    elif template == "3view":
        # 三视图：从 reference_images 获取参考图
        ref_image = (reference_images.get("reference", "") or
                     reference_images.get("character", "") or
                     reference_images.get("scene", "") or "")
        return build_3view_workflow(
            reference_image=ref_image,
            seed=actual_seed,
            filename_prefix=filename_prefix,
        )
    elif template in _EXTRACTION_TEMPLATES:
        # 提取类工作流（姿态/线稿/深度图/三合一）
        wf_file = _EXTRACTION_TEMPLATES.get(template, "")
        ref_image = (reference_images.get("reference", "") or
                     reference_images.get("character", "") or
                     reference_images.get("scene", "") or "")
        logger.info(f"[StoryboardV2] 路由到提取工作流 | template={template} | file={wf_file} | ref={ref_image}")
        return build_extraction_workflow(
            reference_image=ref_image,
            template=template,
            filename_prefix=filename_prefix,
            seed=actual_seed,
        )
    elif template == "multi_person":
        # 多人分镜三元约束：人物A + 人物B + 蒙版 + 深度图 + OpenPose
        char_a = reference_images.get("character", "")
        char_b = reference_images.get("character2", "")
        mask_img = reference_images.get("mask", "")
        depth_img = reference_images.get("depth", "")
        pose_img = reference_images.get("pose", "")
        tmpl_name = kwargs.get("template_name", "")
        logger.info(
            f"[StoryboardV2] 路由到多人分镜 | char_a={char_a} | char_b={char_b} | "
            f"mask={mask_img} | depth={depth_img} | pose={pose_img} | template={tmpl_name}"
        )
        return build_multi_person_workflow(
            char_a_image=char_a,
            char_b_image=char_b,
            mask_image=mask_img,
            depth_image=depth_img,
            pose_image=pose_img,
            prompt_text=prompt_text,
            seed=actual_seed,
            filename_prefix=filename_prefix,
            template_name=tmpl_name,
        )
    elif template == "layered_render":
        # 分层渲染：4-5人场景分A/B组生成+合成
        char_a = reference_images.get("character", "")
        char_b = reference_images.get("character2", "")
        char_c = reference_images.get("character3", "")
        char_d = reference_images.get("character4", "")
        mask_img = reference_images.get("mask", "")
        depth_img = reference_images.get("depth", "")
        tmpl_name = kwargs.get("template_name", "")
        prompt_a = kwargs.get("prompt_a", prompt_text)
        prompt_b = kwargs.get("prompt_b", "")
        logger.info(
            f"[StoryboardV2] 路由到分层渲染 | char_a={char_a} | char_b={char_b} | "
            f"char_c={char_c} | char_d={char_d} | mask={mask_img} | depth={depth_img}"
        )
        return build_layered_render_workflow(
            char_a_image=char_a,
            char_b_image=char_b,
            char_c_image=char_c,
            char_d_image=char_d,
            mask_image=mask_img,
            depth_image=depth_img,
            prompt_a=prompt_a,
            prompt_b=prompt_b,
            seed=actual_seed,
            filename_prefix=filename_prefix,
            template_name=tmpl_name,
        )
    elif template == "template_clean":
        # 模板清场+蒙版生成：SAM2识别人物 → 清场深度图 + 生成蒙版
        ref_image = (reference_images.get("reference", "") or
                     reference_images.get("character", "") or
                     reference_images.get("scene", "") or "")
        depth_image = reference_images.get("depth", "")
        logger.info(
            f"[StoryboardV2] 路由到模板清场 | ref={ref_image} | depth={depth_image}"
        )
        return build_template_clean_workflow(
            reference_image=ref_image,
            depth_image=depth_image,
            filename_prefix=filename_prefix,
            seed=actual_seed,
        )
    elif template == "template_pose":
        # 模板Pose优化：简化7节点骨架渲染
        ref_image = (reference_images.get("reference", "") or
                     reference_images.get("character", "") or
                     reference_images.get("scene", "") or "")
        logger.info(f"[StoryboardV2] 路由到模板Pose优化 | ref={ref_image}")
        return build_template_pose_workflow(
            reference_image=ref_image,
            filename_prefix=filename_prefix,
            seed=actual_seed,
            joint_radius=kwargs.get("joint_radius", 5),
            line_width=kwargs.get("line_width", 3),
            head_radius=kwargs.get("head_radius", 8),
        )

    # ⭐ 4套定制分镜模板
    elif template == "single_person":
        logger.info(f"[StoryboardV2] 路由到单人分镜")
        return build_single_person_workflow(
            reference_images=reference_images,
            prompt_text=prompt_text,
            seed=actual_seed,
            filename_prefix=filename_prefix,
            width=width,
            height=height,
        )
    elif template == "dual_person":
        logger.info(f"[StoryboardV2] 路由到双人融合分镜")
        return build_dual_person_workflow(
            reference_images=reference_images,
            prompt_text=prompt_text,
            seed=actual_seed,
            filename_prefix=filename_prefix,
            width=width,
            height=height,
        )
    elif template == "local_multi":
        logger.info(f"[StoryboardV2] 路由到本地多人分镜")
        return build_local_multi_workflow(
            reference_images=reference_images,
            prompt_text=prompt_text,
            seed=actual_seed,
            filename_prefix=filename_prefix,
            width=width,
            height=height,
        )
    elif template == "gpt_storyboard":
        logger.info(f"[StoryboardV2] 路由到GPT分镜")
        return build_gpt_storyboard_workflow(
            reference_images=reference_images,
            prompt_text=prompt_text,
            seed=actual_seed,
            filename_prefix=filename_prefix,
            width=width,
            height=height,
        )

    # ═══ 默认：走分镜换装模板（V6.0 删除旧版 Fish 融合逻辑） ═══
    logger.info(
        f"[StoryboardV2] 未指定模板，默认走 costume_change（分镜换装）"
    )
    return build_costume_change_workflow(
        reference_images=reference_images,
        prompt_text=prompt_text,
        seed=actual_seed,
        filename_prefix=filename_prefix,
        width=width,
        height=height,
    )


def get_workflow_node_summary(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """获取工作流节点摘要（用于调试）"""
    summary = {}
    for node_id, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type"):
            summary[node_id] = {
                "class": node["class_type"],
                "title": node.get("_meta", {}).get("title", ""),
            }
    return summary


if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.INFO)
    
    # 测试Qwen工作流构建
    try:
        wf = build_refinement_workflow(
            reference_image="test.png",
            role_desc="年轻女性，长发，古装",
            seed=12345
        )
        print(f"✅ Qwen精修工作流构建成功，节点数: {len(wf)}")
    except Exception as e:
        print(f"❌ Qwen工作流构建失败: {e}")

