"""
深度图提取阶段

从图像提取深度图 (depth map)。
使用 ComfyUI depth/midas 工作流。
"""

from services.stages._extraction_base import SingleExtractionStage


class DepthMapStage(SingleExtractionStage):
    """深度图提取阶段 — 支持 method 参数（depth/midas 等）"""

    stage_id = "depth_map"
    stage_name = "深度图提取"
    output_type = "depth"
    template = "depth_map"
    asset_type = "depth"
    name_suffix = "深度图"
    prompt_text = "extract depth map"
    description = "从图像提取深度图 (depth/midas)"
    default_method = "depth"
