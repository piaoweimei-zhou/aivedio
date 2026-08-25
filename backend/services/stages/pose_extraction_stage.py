"""
姿态提取阶段

从人物图像提取 OpenPose 姿态骨架图。
使用 ComfyUI openpose 工作流。
"""

from services.stages._extraction_base import SingleExtractionStage


class PoseExtractionStage(SingleExtractionStage):
    """姿态提取阶段 — 固定 openpose，不接受 method 参数"""

    stage_id = "pose_extraction"
    stage_name = "姿态提取"
    output_type = "pose"
    template = "pose_extraction"
    asset_type = "pose"
    name_suffix = "姿态"
    prompt_text = "extract pose"
    description = "从人物图像提取 OpenPose 姿态骨架图"
    default_method = None  # 不传 method
    fixed_extraction_type = "openpose"  # extraction_type 固定
    input_content_types = ["character"]
