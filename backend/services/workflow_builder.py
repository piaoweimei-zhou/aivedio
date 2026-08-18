"""
ComfyUI 工作流构建器 — 门面模块

对外统一导出所有工作流构建函数。
实现已拆分到以下子模块：
- workflow_helpers: 共享工具与常量
- workflow_core: 核心构建（文生图/图生图/精修/标准化）
- workflow_character: 角色类工作流
- workflow_scene: 场景/增强类工作流
- workflow_storyboard: 分镜工作流
"""

# 共享工具与常量
from services.workflow_helpers import (
    ADDITIONAL_LORAS,
    BASE_WORKFLOW,
    CINEMATIC_WORKFLOW,
    PROP_WORKFLOW,
    QWEN_WORKFLOW_FILE,
    STORYBOARD_TEMPLATES,
    YAOGUANG_DEFAULT_NEGATIVE,
    _COMFYUI_INPUT_DIR,
    _COMFYUI_OUTPUT_DIR,
    _EXTRACTION_TEMPLATES,
    _REFINE_LORA_STRENGTH,
    _REFINE_SCALE_LENGTH,
    _build_character_fusion_step,
    _detect_age_in_prompt,
    _detect_fusion_type,
    _get_denoise_sequence,
    _infer_saveimage_type,
    _load_workflow,
    _load_workflow_template,
    _resolve_comfyui_image,
    _resolve_template_asset,
    _set_clip_text,
    _set_filename_prefix,
    _set_ksampler_params,
    _set_reference_image,
    find_first_node_by_class_type,
    find_first_node_by_class_type_contains,
    find_node_by_class_type,
    find_node_by_title,
    format_qwen_prompt,
    get_workflow_node_summary,
)

# 核心构建
from services.workflow_core import (
    _build_fallback_workflow,
    build_comfyui_workflow,
    build_qwen_workflow,
    build_refinement_workflow,
    build_scene_multiangle_workflow,
    build_standardization_workflow,
    structured_prompt_to_comfyui_prompt,
)

# 角色类工作流
from services.workflow_character import (
    build_3view_workflow,
    build_costume_change_workflow,
    build_multi_frame_workflow,
    build_multi_person_workflow,
)

# 场景/增强类工作流
from services.workflow_scene import (
    build_extraction_workflow,
    build_layered_render_workflow,
    build_panorama_workflow,
    build_pose_transfer_workflow,
    build_template_clean_workflow,
    build_template_pose_workflow,
    build_upscale_workflow,
)

# 分镜工作流
from services.workflow_storyboard import build_storyboard_workflow_v2

__all__ = [
    # 工具
    "find_node_by_class_type", "find_node_by_title",
    "find_first_node_by_class_type", "find_first_node_by_class_type_contains",
    "get_workflow_node_summary", "format_qwen_prompt",
    # 常量
    "BASE_WORKFLOW", "CINEMATIC_WORKFLOW", "PROP_WORKFLOW",
    "YAOGUANG_DEFAULT_NEGATIVE", "ADDITIONAL_LORAS", "STORYBOARD_TEMPLATES",
    "_COMFYUI_OUTPUT_DIR", "_COMFYUI_INPUT_DIR",
    # 核心构建
    "build_comfyui_workflow", "build_qwen_workflow",
    "build_refinement_workflow", "build_standardization_workflow",
    "build_scene_multiangle_workflow", "structured_prompt_to_comfyui_prompt",
    # 角色
    "build_costume_change_workflow", "build_multi_frame_workflow",
    "build_3view_workflow", "build_multi_person_workflow",
    # 场景/增强
    "build_panorama_workflow", "build_pose_transfer_workflow",
    "build_upscale_workflow", "build_extraction_workflow",
    "build_layered_render_workflow", "build_template_clean_workflow",
    "build_template_pose_workflow",
    # 分镜
    "build_storyboard_workflow_v2",
]
