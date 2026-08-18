"""
线稿提取阶段

从图像提取线稿/边缘图。
使用 ComfyUI lineart/canny 工作流。
"""

from services.stages._extraction_base import SingleExtractionStage


class LineartExtractionStage(SingleExtractionStage):
    """线稿提取阶段 — 支持 method 参数（lineart/canny 等）"""

    stage_id = "lineart_extraction"
    stage_name = "线稿提取"
    output_type = "lineart"
    template = "lineart_extraction"
    asset_type = "lineart"
    name_suffix = "线稿"
    prompt_text = "extract lineart"
    description = "从图像提取线稿/边缘图 (lineart/canny)"
    default_method = "lineart"
