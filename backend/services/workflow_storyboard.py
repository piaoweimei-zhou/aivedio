"""
ComfyUI 工作流构建器 — 分镜工作流

分镜批量构建，组合各角色/场景/增强构建函数。
"""

from services.workflow_helpers import (
    _EXTRACTION_TEMPLATES,
)
from services.workflow_character import (
    build_3view_workflow,
    build_costume_change_workflow,
    build_multi_frame_workflow,
    build_multi_person_workflow,
)
from services.workflow_scene import (
    build_extraction_workflow,
    build_layered_render_workflow,
    build_panorama_workflow,
    build_pose_transfer_workflow,
    build_template_clean_workflow,
    build_template_pose_workflow,
    build_upscale_workflow,
)
from services.workflow_storyboard_custom import (
    build_single_person_workflow,
    build_dual_person_workflow,
    build_local_multi_workflow,
    build_gpt_storyboard_workflow,
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
