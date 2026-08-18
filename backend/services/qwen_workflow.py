"""
Qwen Image Edit - Remix AIO v2.0 工作流集成
支持多模式切换：文生图、单图编辑、局部重绘、扩图、多图融合
配置化版本 - 从 YAML 文件读取配置
"""

import json
import logging
import os
import random
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple, Union
from urllib.parse import urlparse, parse_qs

from .qwen_workflow_config import qwen_config
from .workflow_builder import find_first_node_by_class_type, find_first_node_by_class_type_contains

logger = logging.getLogger(__name__)

# 工作流模式定义（从配置加载）
class QwenEditMode:
    TEXT_TO_IMAGE = "text_to_image"      # 文生图
    SINGLE_EDIT = "single_edit"          # 单图编辑 ✅ 精修阶段
    INPAINT = "inpaint"                  # 局部重绘
    OUTPAINT = "outpaint"                # 扩图
    FUSION = "fusion"                    # 多图融合 ✅ 分镜/标准化阶段


def safe_format(template: str, **kwargs) -> str:
    """
    安全的模板格式化，缺失的变量会被空字符串替换
    
    支持两种模板格式：
    - {variable} 格式（使用 str.format_map）
    - $variable 格式（使用 string.Template）
    
    Args:
        template: 模板字符串
        **kwargs: 变量键值对
    
    Returns:
        格式化后的字符串
    """
    if not template:
        return ""
    
    try:
        # 优先尝试 {variable} 格式
        # 创建一个安全的映射，缺失的键返回空字符串
        safe_map = {k: v if v is not None else "" for k, v in kwargs.items()}
        
        # 使用 str.format_map 进行格式化
        result = template.format_map(safe_map)
        return result
    except KeyError as e:
        logger.debug(f"[Qwen] str.format_map 失败 ({e})，尝试 string.Template")
        try:
            # 回退到 $variable 格式
            import string
            # 将 {var} 格式转换为 $var 格式
            template_dollar = template.replace('{', '$').replace('}', '')
            template_obj = string.Template(template_dollar)
            return template_obj.safe_substitute(**kwargs)
        except Exception as e2:
            logger.warning(f"[Qwen] 模板格式化失败: {e2}，使用原始模板")
            return template
    except Exception as e:
        logger.warning(f"[Qwen] 模板格式化失败: {e}，使用原始模板")
        return template


# ComfyUI 目录（统一从 config 模块读取）
from services.comfyui.config import COMFYUI_DIR as _COMFYUI_DIR, COMFYUI_OUTPUT_DIR as _COMFYUI_OUTPUT_DIR, COMFYUI_INPUT_DIR as _COMFYUI_INPUT_DIR


def _resolve_comfyui_image(ref: str) -> str:
    """
    将参考图像 URL/路径解析为 ComfyUI LoadImage 可用的文件名
    
    Args:
        ref: 参考图像 URL 或本地路径
    
    Returns:
        ComfyUI input 目录下的文件名
    """
    if not ref:
        return ref

    filename = ref
    if '?' in ref or ref.startswith('/api/'):
        parsed = urlparse(ref)
        params = parse_qs(parsed.query)
        if 'filename' in params:
            filename = params['filename'][0]
        elif parsed.path:
            filename = parsed.path.rsplit('/', 1)[-1]

    if '/' not in filename and '\\' not in filename:
        src = os.path.join(_COMFYUI_OUTPUT_DIR, filename)
        dst = os.path.join(_COMFYUI_INPUT_DIR, filename)
        if os.path.exists(dst):
            return filename
        if os.path.exists(src):
            try:
                os.makedirs(_COMFYUI_INPUT_DIR, exist_ok=True)
                shutil.copy2(src, dst)
                logger.info(f"[Qwen] 复制图片到 ComfyUI input: {filename}")
            except Exception as e:
                logger.warning(f"[Qwen] 复制图片失败: {e}")
        else:
            logger.warning(f"[Qwen] output 目录未找到图片: {src}")
        return filename

    fname = os.path.basename(filename)
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


def load_qwen_workflow(filename: Optional[str] = None) -> Dict[str, Any]:
    """
    加载Qwen工作流模板
    
    Args:
        filename: 工作流文件名（可选，默认从配置读取）
    
    Returns:
        工作流字典
    
    Raises:
        FileNotFoundError: 工作流文件不存在
    """
    workflow_file = qwen_config.get_workflow_file_path(filename)
    
    if not workflow_file.exists():
        logger.error(f"[Qwen] 工作流文件不存在: {workflow_file}")
        raise FileNotFoundError(f"Qwen工作流文件不存在: {workflow_file}")
    
    try:
        with open(workflow_file, 'r', encoding='utf-8') as f:
            workflow = json.load(f)
        logger.info(f"[Qwen] 工作流已加载: {workflow_file.name}")
        return workflow
    except json.JSONDecodeError as e:
        logger.error(f"[Qwen] 工作流文件解析失败: {e}")
        raise


def format_qwen_prompt(
    keep: str = "",
    change: str = "",
    maintain: str = "",
    avoid: str = "",
    fallback: str = "",
    image_prefix: Optional[str] = None,
) -> str:
    """
    格式化5段式提示词
    
    Args:
        keep: [KEEP] 保留元素
        change: [CHANGE] 改变指令
        maintain: [MAINTAIN] 一致性保持
        avoid: [AVOID] 负面约束
        fallback: [FALLBACK] 冲突解决
        image_prefix: 多图前缀（如 "Image A"）
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
    
    prompt = "\n\n".join(sections)
    
    if image_prefix:
        prompt = f"[{image_prefix}]\n\n{prompt}"
    
    return prompt


# ── content_type 驱动的精修参数映射 ──
_REFINE_LORA_STRENGTH = {
    "character": 1.0,   # 角色：锁死五官/肤色一致性
    "scene":     0.6,   # 场景：允许光影突变
    "prop":      0.4,   # 道具：允许材质完全推翻
    "":          1.0,   # 默认拉满
}
_REFINE_SCALE_LENGTH = {
    "character": 1344,  # 角色：竖屏全身构图
    "scene":     1344,  # 场景：广角视野
    "prop":      1024,  # 道具：方形特写
    "":          1344,  # 默认
}


def build_qwen_workflow(
    mode: str,
    prompt_text: str,
    reference_images: Optional[List[str]] = None,
    seed: Optional[int] = None,
    filename_prefix: str = "QwenEdit",
    content_type: str = "",
) -> Dict[str, Any]:
    """
    加载精修工作流 JSON 并注入参数（与 Z-Image 文生图同格式）
    
    Args:
        mode: 'single_edit'（精修）或 'fusion'（标准化）
        prompt_text: 提示词/精修指令文本
        reference_images: 参考图像路径列表
        seed: 随机种子
        filename_prefix: 输出文件名前缀
        content_type: 内容类型（character/scene/prop/""），驱动 LoRA 强度和缩放尺寸
    
    Returns:
        ComfyUI API 格式工作流
    """
    actual_seed = seed or random.randint(0, 2**31 - 1)
    ref_img = (reference_images or [''])[0]
    local_filename = _resolve_comfyui_image(ref_img)

    # 尝试加载指定模式的工作流文件
    mode_workflow_map = {
        'single_edit': "精修优化.json",
        'fusion': "多场景.json",
    }
    
    wf_path = None
    if mode in mode_workflow_map:
        wf_path = qwen_config.get_workflow_file_path(mode_workflow_map[mode])
    
    # 如果没有找到模式对应的工作流，使用默认工作流
    if not wf_path or not wf_path.exists():
        logger.warning(f"[Qwen] 未找到模式 {mode} 的工作流文件，使用默认工作流")
        wf_path = qwen_config.get_workflow_file_path()
    
    if not wf_path.exists():
        logger.error(f"[Qwen] 工作流文件不存在: {wf_path}")
        raise FileNotFoundError(f"工作流文件不存在: {wf_path}")

    with open(wf_path, 'r', encoding='utf-8') as f:
        wf = json.load(f)

    # 查找并设置参考图像节点
    if local_filename:
        for node_id, node_data in wf.items():
            if isinstance(node_data, dict) and node_data.get('class_type') == 'LoadImage':
                if 'image' in node_data.get('inputs', {}):
                    wf[node_id]['inputs']['image'] = local_filename
                    logger.info(f"[Qwen] 设置参考图像: {local_filename}")
                    break

    # 设置种子（KSampler 节点）
    for node_id, node_data in wf.items():
        if isinstance(node_data, dict) and 'KSampler' in node_data.get('class_type', ''):
            if 'seed' in node_data.get('inputs', {}):
                wf[node_id]['inputs']['seed'] = actual_seed
                break

    # 设置提示词 — 根据工作流模式选择正确的节点
    prompt_injected = False
    if mode == 'fusion':
        # fusion 模式使用 多场景.json，其核心节点为 TextEncodeQwenImageEditPlusAdvance_lrzjason
        # 该节点有 instruction 字段作为主输入，CLIPTextEncode 节点仅用于负面提示词
        # 1) 设置 TextEncodeQwenImageEditPlusAdvance_lrzjason 的 instruction 字段
        for node_id, node_data in wf.items():
            if (isinstance(node_data, dict)
                    and 'QwenImageEditPlusAdvance' in node_data.get('class_type', '')
                    and 'instruction' in node_data.get('inputs', {})):
                wf[node_id]['inputs']['instruction'] = prompt_text
                prompt_injected = True
                logger.info(f"[Qwen] 设置 instruction (fusion 模式, node {node_id})")
                break
        # 2) 设置 PrimitiveStringMultiline 的 value 字段
        #    多场景.json 中 easy promptLine 会按行拆分生成多帧。
        #    对于非 scene 类型（character/prop），只需单帧，故展平为单行。
        for node_id, node_data in wf.items():
            if (isinstance(node_data, dict)
                    and node_data.get('class_type') == 'PrimitiveStringMultiline'
                    and 'value' in node_data.get('inputs', {})):
                # 展平：将多段式提示词合并为单行，避免 easy promptLine 拆成多帧
                flat_prompt = ' '.join(prompt_text.replace('\n\n', '\n').split('\n'))
                wf[node_id]['inputs']['value'] = flat_prompt
                logger.info(f"[Qwen] 设置单行提示词 (fusion 模式, node {node_id})")
                break
    else:
        # 其他模式（single_edit 等）：
        # 精修优化.json 的核心节点是 TextEncodeQwenImageEditPlusAdvance_lrzjason
        # 其 prompt 字段为用户精修指令，优先注入
        # 若不存在则回退到 CLIPTextEncode 节点
        for node_id, node_data in wf.items():
            if (isinstance(node_data, dict)
                    and 'QwenImageEditPlusAdvance' in node_data.get('class_type', '')
                    and 'prompt' in node_data.get('inputs', {})
                    and isinstance(node_data['inputs'].get('prompt'), str)):
                wf[node_id]['inputs']['prompt'] = prompt_text
                prompt_injected = True
                logger.info(f"[Qwen] 设置 prompt (single_edit 模式, node {node_id})")
                break
        if not prompt_injected:
            # 回退：使用标准的 CLIPTextEncode 节点
            for node_id, node_data in wf.items():
                if isinstance(node_data, dict) and 'CLIPTextEncode' in node_data.get('class_type', ''):
                    if 'text' in node_data.get('inputs', {}):
                        wf[node_id]['inputs']['text'] = prompt_text
                        prompt_injected = True
                        break

    # 设置输出前缀（SaveImage 节点）
    for node_id, node_data in wf.items():
        if isinstance(node_data, dict) and node_data.get('class_type') == 'SaveImage':
            if 'filename_prefix' in node_data.get('inputs', {}):
                wf[node_id]['inputs']['filename_prefix'] = f"{filename_prefix}_{actual_seed}"
                break

    # ── content_type 驱动的精修参数定制 ──────────────────────

    if content_type:
        # 1. LoRA 强度（LoraLoaderModelOnly 节点）
        _LORA_STRENGTH = {
            "character": 1.0,   # 锁死五官/肤色一致性
            "scene":     0.6,   # 允许光影突变
            "prop":      0.4,   # 允许材质完全推翻
            "":          1.0,   # 默认拉满
        }
        for node_id, node_data in wf.items():
            if (isinstance(node_data, dict)
                    and node_data.get('class_type') == 'LoraLoaderModelOnly'
                    and 'strength_model' in node_data.get('inputs', {})):
                old = node_data['inputs']['strength_model']
                node_data['inputs']['strength_model'] = _LORA_STRENGTH.get(content_type, 1.0)
                logger.info(f"[Qwen] content_type={content_type} → LoRA节点{node_id} strength_model {old}→{node_data['inputs']['strength_model']}")
                break

        # 2. 缩放尺寸（ImageScaleByAspectRatio 节点）
        _SCALE_LENGTH = {
            "character": 1344,  # 竖屏角色全身
            "scene":     1344,  # 横屏场景广角
            "prop":      1024,  # 方形道具特写
            "":          1344,  # 默认
        }
        for node_id, node_data in wf.items():
            if (isinstance(node_data, dict)
                    and 'ImageScaleByAspectRatio' in node_data.get('class_type', '')
                    and 'scale_to_length' in node_data.get('inputs', {})):
                old = node_data['inputs']['scale_to_length']
                node_data['inputs']['scale_to_length'] = _SCALE_LENGTH.get(content_type, 1344)
                logger.info(f"[Qwen] content_type={content_type} → 缩放节点{node_id} scale_to_length {old}→{node_data['inputs']['scale_to_length']}")
                break

        # 3. 种子策略（Seed 节点）
        #    角色/道具→固定种子（保持一致性），场景→随机种子
        if content_type == "scene":
            for node_id, node_data in wf.items():
                if (isinstance(node_data, dict)
                        and 'Seed' in node_data.get('class_type', '')
                        and 'seed' in node_data.get('inputs', {})):
                    new_seed = random.randint(0, 2**63 - 1)
                    node_data['inputs']['seed'] = new_seed
                    logger.info(f"[Qwen] content_type=scene → 种子节点{node_id} 强制随机 seed={new_seed}")
                    break

    logger.info(f"[Qwen] 已加载工作流 ({wf_path.name}), mode={mode}, seed={actual_seed}, ref={local_filename}, prompt_injected={prompt_injected}, content_type={content_type}")
    return wf


def build_qwen_edit_workflow(
    mode: str = QwenEditMode.SINGLE_EDIT,
    prompt_text: str = "",
    reference_images: Optional[List[str]] = None,
    seed: Optional[int] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    filename_prefix: Optional[str] = None,
) -> Dict[str, Any]:
    """
    构建Qwen编辑工作流（配置化版本）
    
    Args:
        mode: 编辑模式
        prompt_text: 提示词文本（支持5段式）
        reference_images: 参考图像路径列表
        seed: 随机种子
        width: 输出宽度（可选，默认从配置读取）
        height: 输出高度（可选，默认从配置读取）
        filename_prefix: 文件名前缀（可选，默认从配置读取）
    
    Returns:
        完整工作流字典
    """
    default_prefix = filename_prefix or qwen_config.get_default('filename_prefix', 'QwenEdit')
    
    return build_qwen_workflow(
        mode=mode,
        prompt_text=prompt_text,
        reference_images=reference_images,
        seed=seed,
        filename_prefix=default_prefix,
    )


def build_refinement_workflow(
    reference_image: str,
    role_desc: str = "",
    scene_desc: str = "",
    prop_desc: str = "",
    lock_elements: Optional[Union[List[str], List[Dict[str, str]]]] = None,
    seed: Optional[int] = None,
    full_prompt: Optional[str] = None,
    content_type: str = "",
) -> Tuple[Dict[str, Any], str, Dict[str, str]]:
    """
    构建精修阶段工作流（单图编辑模式）
    
    Args:
        reference_image: 参考图像路径
        role_desc: 角色描述
        scene_desc: 场景描述
        prop_desc: 道具描述
        lock_elements: 需要锁定的元素列表（兼容 List[str] 和 List[Dict[str, str]]）
        seed: 随机种子
        full_prompt: 直接使用的完整提示词（跳过 format_qwen_prompt 重建）
    
    Returns:
        Tuple[工作流字典, 提示词, 提示词分段]
    """
    prompt_sections: Dict[str, str] = {}
    
    if full_prompt:
        prompt = full_prompt
        logger.info(f"[Qwen] 使用直接提供的精修完整提示词 ({len(prompt)} chars)")
    else:
        # 处理锁定元素（兼容两种格式）
        lock_elements_text = ""
        if lock_elements:
            for elem in lock_elements:
                if isinstance(elem, dict):
                    elem_text = elem.get('element', elem.get('description', str(elem)))
                else:
                    elem_text = str(elem)
                if lock_elements_text:
                    lock_elements_text += "\n"
                lock_elements_text += f"保持 {elem_text} 完全不变"
        
        # 构建优化部分
        optimizations = []
        if role_desc:
            optimizations.append(f"优化角色: {role_desc}")
        if scene_desc:
            optimizations.append(f"优化场景: {scene_desc}")
        if prop_desc:
            optimizations.append(f"优化道具: {prop_desc}")
        
        # 构建提示词
        prompt = format_qwen_prompt(
            keep=lock_elements_text,
            change="\n".join(optimizations) or "轻微优化细节，提升画质",
            maintain="",
            avoid="",
            fallback=""
        )
        
        prompt_sections = {
            "keep": lock_elements_text,
            "change": "\n".join(optimizations) or "轻微优化细节，提升画质",
            "maintain": "",
            "avoid": "",
            "fallback": "",
        }
        
        logger.info(f"[Qwen] 精修提示词已构建 ({len(prompt)} chars)")
    
    workflow = build_qwen_workflow(
        mode='single_edit',
        prompt_text=prompt,
        reference_images=[reference_image] if reference_image else None,
        seed=seed,
        filename_prefix='refinement',
        content_type=content_type,
    )
    
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
) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
    """
    构建标准化阶段工作流（多视图生成）
    
    Args:
        reference_image: 参考图像路径
        views: 视图数量 (3/6)
        character_name: 资产名称（如"女侠"/"宝剑"）
        seed: 随机种子
        full_prompt: 直接使用的完整提示词（跳过本地模板）
        filename_prefix: 输出文件名前缀
        view_type: 资产类型（character/prop），用于区分提示词语义
        role_desc: 优化后的角色/道具描述（从概念阶段传递过来）
    
    Returns:
        Tuple[工作流字典, 提示词, 额外元数据]
    """
    if full_prompt:
        prompt = full_prompt
        logger.info(f"[Qwen] 使用直接提供的标准化完整提示词 ({len(prompt)} chars)")
    else:
        view_names = {
            3: ["正面视图", "侧面视图", "背面视图"],
            6: ["正面视图", "左前45度", "左侧视图", "背面视图", "右侧视图", "右前45度"],
        }
        
        views_list = view_names.get(views, view_names[3])
        views_desc = "、".join(views_list)
        
        # 构建基础描述（优先使用优化后的描述）
        asset_desc = role_desc if role_desc and len(role_desc) > 5 else character_name
        
        if view_type == 'character':
            prompt = format_qwen_prompt(
                keep=f"保持{asset_desc}的面部特征、发型、身体比例完全一致\n保持服装款式、颜色、材质细节完全一致",
                change=f"生成{asset_desc}的标准{views_desc}\n在一个画布上并排展示所有视图\n每个视图保持相同的光照风格和比例",
                maintain="保持统一的光照方向、色彩风格、视角高度",
                avoid="不要添加背景场景，保持背景纯净，不要改变角色比例",
                fallback="如果有冲突，优先保持角色身份一致性"
            )
        else:
            prompt = format_qwen_prompt(
                keep=f"保持{asset_desc}的形状、材质、颜色完全一致",
                change=f"生成{asset_desc}的标准{views_desc}\n在一个画布上并排展示所有视图\n每个视图保持相同的光照风格和比例",
                maintain="保持统一的光照方向、色彩风格、视角高度",
                avoid="不要添加背景场景，保持背景纯净，不要改变物体比例",
                fallback="如果有冲突，优先保持物体特征一致性"
            )
        
        logger.info(f"[Qwen] 标准化提示词已构建: {views}视图 ({view_type}) | asset_desc='{asset_desc[:60]}'")
    
    workflow = build_qwen_workflow(
        mode='single_edit',
        prompt_text=prompt,
        reference_images=[reference_image] if reference_image else None,
        seed=seed,
        filename_prefix=f'{filename_prefix}_standard',
        denoise=0.95,  # 三视图需要极高 denoise 才能完全改变构图
        output_size=(1024, 1024),
    )
    
    return workflow, prompt, {'stage': 'standardization', 'views': views}


def build_scene_multiangle_workflow(
    reference_image: str,
    scene_dna: str = "",
    per_frame_prompts: Optional[List[str]] = None,
    seed: Optional[int] = None,
    filename_prefix: str = "BarScene_6Angle",
    instruction: Optional[str] = None,
) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
    """
    构建场景多角度标准化工作流（双通道约束）
    
    Args:
        reference_image: 参考图像路径
        scene_dna: 场景DNA文本
        per_frame_prompts: 每帧提示词列表（6 行，每行对应一个镜头）
        seed: 随机种子
        filename_prefix: 输出文件名前缀
        instruction: 直接使用的全局 instruction
    
    Returns:
        Tuple[工作流字典, 提示词, 额外元数据]
    """
    actual_seed = seed or random.randint(0, 2**31 - 1)
    ref_img = _resolve_comfyui_image(reference_image)

    # 尝试加载多场景工作流
    wf_path = qwen_config.get_workflow_file_path("多场景.json")
    
    if not wf_path.exists():
        logger.warning("[SceneMultiAngle] 场景多角度工作流不存在，降级到单图编辑")
        fallback_wf = build_qwen_workflow(
            mode='single_edit',
            prompt_text=scene_dna or "生成场景的多角度视图",
            reference_images=[reference_image] if reference_image else None,
            seed=seed,
            filename_prefix=filename_prefix,
        )
        return fallback_wf, scene_dna or "生成场景的多角度视图", {'stage': 'scene_multiangle', 'fallback': True}

    with open(wf_path, 'r', encoding='utf-8') as f:
        wf = json.load(f)

    # 注入参考图（LoadImage 节点）
    nid_load, ndata_load = find_first_node_by_class_type(wf, 'LoadImage')
    if nid_load:
        wf[nid_load]['inputs']['image'] = ref_img

    # 注入全局 instruction（QwenImageEditPlusAdvance 节点）
    final_instruction = instruction
    if not final_instruction and scene_dna:
        final_instruction = (
            "You are generating alternate camera angles for a short drama scene. "
            "The original scene was generated with these EXACT specifications:\n"
            f"{scene_dna}\n\n"
            "ABSOLUTE PRESERVATION RULES:\n"
            "- All objects listed above must remain in identical quantity, position, material, and color.\n"
            "- Lighting direction and color temperature contrast must not change.\n"
            "- No new objects, people, furniture, or light sources may be added.\n"
            "- If a camera angle obscures an object, its implied position and proportion must still be respected.\n"
            "- Generate photorealistic cinematic frame consistent with the original scene."
        )
    if final_instruction:
        nid_enc, ndata_enc = find_first_node_by_class_type_contains(wf, 'QwenImageEditPlusAdvance')
        if nid_enc:
            wf[nid_enc]['inputs']['instruction'] = final_instruction

    # 注入每帧提示词（PrimitiveStringMultiline 节点）
    nid_psm, ndata_psm = find_first_node_by_class_type(wf, 'PrimitiveStringMultiline')
    if per_frame_prompts and len(per_frame_prompts) > 0:
        prompt_lines = "\n".join(per_frame_prompts)
        if nid_psm:
            wf[nid_psm]['inputs']['value'] = prompt_lines
    elif nid_psm and scene_dna:
        dna_prefix = scene_dna[:80] + "..." if len(scene_dna) > 80 else scene_dna
        default_lines = [
            f"{dna_prefix} Next Scene：广角全景，展示完整场景空间。保持场景核心元素完全不变，严禁添加人物或新物体。",
            f"{dna_prefix} Next Scene：正面中景，标准构图。保持场景核心元素完全不变，严禁添加人物或新物体。",
            f"{dna_prefix} Next Scene：左侧45度斜侧，增加空间纵深。保持场景核心元素完全不变，严禁添加人物或新物体。",
            f"{dna_prefix} Next Scene：右侧45度斜侧，对称展示。保持场景核心元素完全不变，严禁添加人物或新物体。",
            f"{dna_prefix} Next Scene：特写镜头，聚焦核心材质细节。保持纹理、反光、色调完全不变，严禁添加人物或新物体。",
            f"{dna_prefix} Next Scene：正上方90度俯视，展示平面布局。保持各元素位置比例完全不变，允许简化不可见细节，严禁添加人物或新物体。",
        ]
        wf[nid_psm]['inputs']['value'] = "\n".join(default_lines)

    # 设置种子（KSampler 节点）
    nid_ks, ndata_ks = find_first_node_by_class_type_contains(wf, 'KSampler')
    if nid_ks:
        wf[nid_ks]['inputs']['seed'] = actual_seed

    # 设置输出前缀（SaveImage 节点）
    nid_save, ndata_save = find_first_node_by_class_type(wf, 'SaveImage')
    if nid_save:
        wf[nid_save]['inputs']['filename_prefix'] = f"{filename_prefix}_{actual_seed}"

    logger.info(f"[SceneMultiAngle] 已加载场景多角度工作流, seed={actual_seed}, ref={ref_img}")
    return wf, final_instruction or scene_dna or "", {'stage': 'scene_multiangle', 'views': 6}


def structured_prompt_to_comfyui_prompt(prompt_data: Dict[str, Any], custom_text: str = "") -> str:
    """
    将结构化提示词转换为ComfyUI格式
    
    Args:
        prompt_data: 结构化提示词数据
        custom_text: 自定义文本（可选）
    
    Returns:
        格式化后的提示词字符串
    """
    if custom_text:
        return custom_text
    
    return format_qwen_prompt(
        keep=prompt_data.get('keep', ''),
        change=prompt_data.get('change', ''),
        maintain=prompt_data.get('maintain', ''),
        avoid=prompt_data.get('avoid', ''),
        fallback=prompt_data.get('fallback', ''),
    )


def build_comfyui_workflow(
    positive_prompt: str,
    negative_prompt: str = "",
    width: int = 1080,
    height: int = 1920,
    seed: Optional[int] = None,
    steps: int = 8,
    cfg: float = 1.0,
    sampler_name: str = "euler",
    scheduler: str = "normal",
    model_name: str = "zyj_v10.safetensors",
    reference_image: Optional[str] = None,
) -> Dict[str, Any]:
    """
    构建瑶光版文生图工作流（兼容旧版接口）
    
    Args:
        positive_prompt: 提示词
        negative_prompt: 负向提示词
        width: 宽度
        height: 高度
        seed: 随机种子
        steps: 步数
        cfg: CFG比例
        sampler_name: 采样器名称
        scheduler: 调度器
        model_name: 模型名称
        reference_image: 参考图像（img2img模式）
    
    Returns:
        完整工作流字典
    """
    from services.workflow_builder import build_comfyui_workflow as original_build
    return original_build(
        positive_prompt=positive_prompt,
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        seed=seed,
        steps=steps,
        cfg=cfg,
        reference_image=reference_image or "",
        additional_lora=None,
    )


YAOGUANG_DEFAULT_NEGATIVE = ""


def get_available_modes() -> List[Dict[str, Any]]:
    """获取所有可用模式"""
    modes = qwen_config.config.get('modes', {})
    return [
        {'id': mode_id, 'name': mode_data.get('name', mode_id), 'description': mode_data.get('description', '')}
        for mode_id, mode_data in modes.items()
    ]


def reload_config():
    """重新加载配置"""
    qwen_config.reload()


def update_config(updates: Dict[str, Any]):
    """更新配置"""
    qwen_config.update_config(updates)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    logger.info(f"配置文件路径: {qwen_config.config_path}")
    logger.info(f"工作流文件: {qwen_config.get_workflow_file_path()}")
    logger.info(f"可用模式: {get_available_modes()}")
    logger.info(f"Fallback模式: {qwen_config.is_fallback_mode()}")
    
    test_template = "Hello {name}, you are {age} years old. {unknown_var}"
    result = safe_format(test_template, name="Test", age=30)
    logger.info(f"安全格式化测试: {result}")
    
    try:
        wf = load_qwen_workflow()
        logger.info(f"✅ 工作流加载成功，节点数: {len(wf)}")
    except Exception as e:
        logger.error(f"❌ 工作流加载失败: {e}")
    
    try:
        ref_wf, prompt, meta = build_refinement_workflow(
            reference_image="test.png",
            role_desc="科幻战士",
            lock_elements=["面部表情", "服装颜色"]
        )
        logger.info(f"✅ 精修工作流构建成功，提示词长度: {len(prompt)}")
    except Exception as e:
        logger.error(f"❌ 精修工作流构建失败: {e}")