"""
生产阶段插件包

每个阶段实现 StagePlugin 接口，独立文件。
"""

from services.stages.concept_stage import ConceptStage
from services.stages.refine_stage import RefineStage
from services.stages.angle_stage import AngleStage
from services.stages.pano_stage import PanoStage
from services.stages.storyboard_stage import StoryboardStage
from services.stages.video_stage import VideoStage
from services.stages.edit_stage import EditStage
from services.stages.export_stage import ExportStage
from services.stages.depth_map_stage import DepthMapStage
from services.stages.lineart_extraction_stage import LineartExtractionStage
from services.stages.pose_extraction_stage import PoseExtractionStage
from services.stages.template_batch_extract_stage import TemplateBatchExtractStage
from services.stages.template_clean_stage import TemplateCleanStage
from services.stages.template_pose_stage import TemplatePoseStage

__all__ = [
    "ConceptStage",
    "RefineStage",
    "AngleStage",
    "PanoStage",
    "StoryboardStage",
    "VideoStage",
    "EditStage",
    "ExportStage",
    "DepthMapStage",
    "LineartExtractionStage",
    "PoseExtractionStage",
    "TemplateBatchExtractStage",
    "TemplateCleanStage",
    "TemplatePoseStage",
]
