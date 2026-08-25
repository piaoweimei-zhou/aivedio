"""
ComfyUI 工作流构建器 — 角色类工作流

换装、多帧、三视图、多人等角色相关构建函数。
"""

from services.workflow_helpers import (
    _load_workflow_template,
    _resolve_comfyui_image,
    _resolve_template_asset,
    _set_filename_prefix,
    _set_ksampler_params,
    find_first_node_by_class_type,
    find_first_node_by_class_type_contains,
    find_node_by_class_type,
)

import logging
import time
import random
from typing import Dict, Any, Optional, Tuple, List

logger = logging.getLogger(__name__)


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

    load_nodes = find_node_by_class_type(wf, "LoadImage")
    # 按节点 ID 排序，确保顺序稳定（节点 10=角色, 11=场景, 12=道具）
    load_nodes.sort(key=lambda x: x[0])
    if len(load_nodes) >= 1 and char_file:
        wf[load_nodes[0][0]]["inputs"]["image"] = char_file
    if len(load_nodes) >= 2 and scene_file:
        wf[load_nodes[1][0]]["inputs"]["image"] = scene_file
    if len(load_nodes) >= 3 and prop_file:
        wf[load_nodes[2][0]]["inputs"]["image"] = prop_file
    elif len(load_nodes) >= 3 and scene_file:
        wf[load_nodes[2][0]]["inputs"]["image"] = scene_file

    # 注入提示词 — 通过 class_type 查找 QwenImageEditPlusAdvance 节点
    nid, ndata = find_first_node_by_class_type_contains(wf, "QwenImageEditPlusAdvance")
    if nid and ndata and "prompt" in ndata.get("inputs", {}):
        wf[nid]["inputs"]["prompt"] = prompt_text

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
        "template": "costume_change",
        "seed": actual_seed,
        "lora_strength": 0.25,
        "denoise": 1.0,
        "cfg": 1.0,
    }

    logger.info(
        f"[WorkflowBuilder][分镜换装] 构建完成 | elapsed={time.time()-_t0:.3f}s | nodes={len(wf)}"
    )
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
    nid, ndata = find_first_node_by_class_type(wf, "LoadImage")
    if nid and ndata and ref_file:
        wf[nid]["inputs"]["image"] = ref_file

    # 注入提示词（TextEncodeQwenImageEditPlus 正向编码）
    nid_enc, enc_data = find_first_node_by_class_type_contains(wf, "QwenImageEditPlusAdvance")
    if nid_enc and enc_data:
        if per_frame_prompts and len(per_frame_prompts) > 0:
            wf[nid_enc]["inputs"]["prompt"] = "\n".join(per_frame_prompts)
            logger.info(f"[WorkflowBuilder][多帧分镜] 注入 {len(per_frame_prompts)} 帧提示词")
        elif prompt_text:
            wf[nid_enc]["inputs"]["prompt"] = prompt_text

    # 注入种子（KSampler 节点）
    nid_ks, ks_data = find_first_node_by_class_type(wf, "KSampler")
    if nid_ks and ks_data:
        wf[nid_ks]["inputs"]["seed"] = actual_seed

    # 尺寸覆写（EmptyLatentImage 节点）
    if width and height:
        nid, ndata = find_first_node_by_class_type(wf, "EmptyLatentImage")
        if nid and ndata:
            wf[nid]["inputs"]["width"] = width
            wf[nid]["inputs"]["height"] = height

    # 设置输出前缀（SaveImage 节点）
    nid_save, save_data = find_first_node_by_class_type(wf, "SaveImage")
    if nid_save and save_data:
        wf[nid_save]["inputs"]["filename_prefix"] = f"{filename_prefix}_{actual_seed}"

    frame_count = len(per_frame_prompts) if per_frame_prompts else 1
    metadata = {
        "template": "multi_frame",
        "seed": actual_seed,
        "frame_count": frame_count,
        "next_scene_lora": True,
    }

    logger.info(
        f"[WorkflowBuilder][多帧分镜] 构建完成 | elapsed={time.time()-_t0:.3f}s | frames={frame_count} | nodes={len(wf)}"  # noqa: E501
    )  # noqa: E501
    return [wf], ["多帧分镜"], metadata


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
    nid_load, _ = find_first_node_by_class_type(wf, "LoadImage")
    if nid_load and ref_file:
        wf[nid_load]["inputs"]["image"] = ref_file
        logger.info(f"[WorkflowBuilder][3视图] LoadImage节点{nid_load} 注入图片: {ref_file}")
    elif not ref_file:
        logger.warning(f"[WorkflowBuilder][3视图] 参考图路径为空: {reference_image}")

    # 注入 seed 到所有 KSampler 节点（3个视角共用同一 seed）
    ksampler_nodes = find_node_by_class_type(wf, "KSampler")
    for nid, ndata in ksampler_nodes:
        old_seed = ndata["inputs"].get("seed", "?")
        ndata["inputs"]["seed"] = actual_seed
        logger.info(f"[WorkflowBuilder][3视图] KSampler({nid}) seed {old_seed}→{actual_seed}")

    # 设置所有 SaveImage 节点的 filename_prefix
    save_nodes = find_node_by_class_type(wf, "SaveImage")
    for nid, ndata in save_nodes:
        ndata["inputs"]["filename_prefix"] = f"{filename_prefix}_{actual_seed}"
    logger.info(
        f"[WorkflowBuilder][3视图] 设置{len(save_nodes)}个SaveImage节点 prefix={filename_prefix}_{actual_seed}"  # noqa: E501
    )  # noqa: E501

    metadata = {
        "template": "3view",
        "seed": actual_seed,
        "reference_image": ref_file,
        "view_count": 3,
    }

    logger.info(
        f"[WorkflowBuilder][3视图] 构建完成 | elapsed={time.time()-_t0:.3f}s | nodes={len(wf)} | ksamplers={len(ksampler_nodes)}"  # noqa: E501
    )  # noqa: E501
    return [wf], ["三视图生成"], metadata


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
        logger.info(
            "[WorkflowBuilder][多人分镜] ControlNet已禁用（缺少深度/姿态图或use_controlnet=False）"
        )
    elif skip_depth_cn:
        # 仅移除深度ControlNet，姿态ControlNet直连TextEncode
        for cn_id in ["50", "52"]:
            wf.pop(cn_id, None)
        if "53" in wf:
            wf["53"]["inputs"]["conditioning"] = ["60", 0]
        logger.info("[WorkflowBuilder][多人分镜] 深度ControlNet已禁用（缺少深度图）")
    elif skip_pose_cn:
        # 仅移除姿态ControlNet，KSampler positive 连深度ControlNet输出
        for cn_id in ["51", "53"]:
            wf.pop(cn_id, None)
        if "70" in wf:
            wf["70"]["inputs"]["positive"] = ["52", 0]
        logger.info("[WorkflowBuilder][多人分镜] 姿态ControlNet已禁用（缺少姿态图）")

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
