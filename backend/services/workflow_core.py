"""
ComfyUI 工作流构建器 — 核心构建函数

文生图/图生图/精修/标准化等主流程工作流构建。
"""

from services.workflow_helpers import (
    ADDITIONAL_LORAS,
    BASE_WORKFLOW,
    CINEMATIC_WORKFLOW,
    PROP_WORKFLOW,
    YAOGUANG_DEFAULT_NEGATIVE,
    _REFINE_LORA_STRENGTH,
    _REFINE_SCALE_LENGTH,
    _detect_age_in_prompt,
    _resolve_comfyui_image,
    find_first_node_by_class_type,
    find_first_node_by_class_type_contains,
    find_node_by_class_type,
)

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

logger = logging.getLogger(__name__)

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
