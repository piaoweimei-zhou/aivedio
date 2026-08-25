"""
ComfyUI 工作流构建器 — 场景/增强类工作流

全景、姿态迁移、超分、提取、分层渲染、模板清理/姿态等构建函数。
"""

from services.workflow_helpers import (
    _COMFYUI_INPUT_DIR,
    _COMFYUI_OUTPUT_DIR,
    _EXTRACTION_TEMPLATES,
    _infer_saveimage_type,
    _load_workflow_template,
    _resolve_comfyui_image,
    _set_filename_prefix,
    _set_ksampler_params,
    find_first_node_by_class_type,
    find_first_node_by_class_type_contains,
    find_node_by_class_type,
)

import copy
import json
import logging
import os
import time
import random
from typing import Dict, Any, Optional, Tuple, List

logger = logging.getLogger(__name__)


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

    logger.info(
        f"[WorkflowBuilder][全景图] 构建完成 | elapsed={time.time()-_t0:.3f}s | nodes={len(wf)}"
    )
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

    load_nodes = find_node_by_class_type(wf, "LoadImage")
    load_nodes.sort(key=lambda x: x[0])
    if len(load_nodes) >= 1 and char_file:
        wf[load_nodes[0][0]]["inputs"]["image"] = char_file
    if len(load_nodes) >= 2 and pose_file:
        wf[load_nodes[1][0]]["inputs"]["image"] = pose_file

    # 注入提示词（TextEncodeQwenImageEditPlus 节点）
    nid_enc, enc_data = find_first_node_by_class_type_contains(wf, "QwenImageEditPlusAdvance")
    if nid_enc and enc_data and "prompt" in enc_data.get("inputs", {}):
        wf[nid_enc]["inputs"]["prompt"] = prompt_text

    # 注入种子和参数
    wf = _set_ksampler_params(
        wf, denoise=1.0, cfg_scale=1.0, seed=actual_seed, steps=4, scheduler="beta57"
    )

    # 尺寸覆写（EmptyLatentImage 节点）
    if width and height:
        nid, ndata = find_first_node_by_class_type(wf, "EmptyLatentImage")
        if nid and ndata:
            wf[nid]["inputs"]["width"] = width
            wf[nid]["inputs"]["height"] = height

    wf = _set_filename_prefix(wf, f"{filename_prefix}_{actual_seed}")

    metadata = {
        "template": "pose_transfer",
        "seed": actual_seed,
        "lora_strength": 0.25,
        "denoise": 1.0,
        "cfg": 1.0,
    }

    logger.info(
        f"[WorkflowBuilder][姿态迁移] 构建完成 | elapsed={time.time()-_t0:.3f}s | nodes={len(wf)}"
    )
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
    nid, ndata = find_first_node_by_class_type(wf, "LoadImage")
    if nid and ndata and ref_file:
        wf[nid]["inputs"]["image"] = ref_file
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
    nid_seedvr, _ = find_first_node_by_class_type(wf, "SeedVR2VideoUpscaler")
    if nid_seedvr:
        wf[nid_seedvr]["inputs"]["seed"] = actual_seed

    wf = _set_filename_prefix(wf, f"{filename_prefix}_{actual_seed}")

    metadata = {
        "template": "upscale",
        "seed": actual_seed,
    }

    logger.info(
        f"[WorkflowBuilder][超分] 构建完成 | elapsed={time.time()-_t0:.3f}s | nodes={len(wf)}"
    )

    # 打印完整工作流 JSON 用于比对（仅 debug 级别）
    logger.debug(
        f"[WorkflowBuilder][超分] 工作流详情: {json.dumps(wf, ensure_ascii=False, indent=2)}"
    )
    return [wf], ["超分放大"], metadata


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
    nid_load, _ = find_first_node_by_class_type(wf, "LoadImage")
    if nid_load and ref_file:
        wf[nid_load]["inputs"]["image"] = ref_file
    elif not ref_file:
        logger.warning(f"[WorkflowBuilder][提取] 参考图路径为空: {reference_image}")

    # 设置种子
    actual_seed = seed or random.randint(0, 2**31 - 1)
    for nid, ndata in find_node_by_class_type(wf, "KSampler"):
        ndata["inputs"]["seed"] = actual_seed

    # 设置 SaveImage prefix — 根据上游节点类型智能识别，而非依赖遍历顺序
    if template == "extract_all":
        _infer_map = {
            "lineart": ["lineart", "line_art", "canny", "hed", "scribble"],
            "depth": ["depth", "midas", "zoe", "leres"],
            "pose": ["pose", "openpose", "dwpose", "dwpreprocessor", "keypoint", "sdpose"],
        }
        for nid, ndata in find_node_by_class_type(wf, "SaveImage"):
            type_tag = _infer_saveimage_type(wf, nid, _infer_map)
            if filename_prefix and filename_prefix != "extraction":
                ndata["inputs"]["filename_prefix"] = f"{filename_prefix}_{type_tag}"
            else:
                ndata["inputs"]["filename_prefix"] = f"{type_tag}_{actual_seed}"
    else:
        for i, (nid, ndata) in enumerate(find_node_by_class_type(wf, "SaveImage")):
            ndata["inputs"]["filename_prefix"] = f"{filename_prefix}_{actual_seed}"

    metadata = {"template": template, "seed": actual_seed, "reference_image": ref_file}
    return [wf], [f"{template}提取"], metadata


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
    load_nodes = find_node_by_class_type(wf, "LoadImage")
    if load_nodes:
        nid, ndata = load_nodes[0]
        ndata["inputs"]["image"] = ref_file
        logger.info(f"[TemplateClean] 参考图 → node {nid}: {ref_file}")

    # 简化工作流只有 1 个 SaveImage（mask_raw）
    for nid, ndata in find_node_by_class_type(wf, "SaveImage"):
        ndata["inputs"]["filename_prefix"] = f"{filename_prefix}_mask_raw"

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
    load_nodes = find_node_by_class_type(wf, "LoadImage")
    if load_nodes:
        nid, ndata = load_nodes[0]
        ndata["inputs"]["image"] = ref_file
        logger.info(f"[TemplatePose] 参考Pose → node {nid}: {ref_file}")

    # 注入 SimplifiedPoseRenderer 参数
    for nid, ndata in wf.items():
        if isinstance(ndata, dict) and ndata.get("class_type") == "SimplifiedPoseRenderer":
            ndata["inputs"]["joint_radius"] = joint_radius
            ndata["inputs"]["line_width"] = line_width
            ndata["inputs"]["head_radius"] = head_radius

    # 设置 SaveImage filename_prefix
    for i, (nid, ndata) in enumerate(find_node_by_class_type(wf, "SaveImage")):
        ndata["inputs"]["filename_prefix"] = f"{filename_prefix}_pose"

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
