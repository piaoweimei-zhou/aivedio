"""资产类型枚举

统一资产类型字符串，避免拼写错误（如 story_board vs storyboard）。

使用方式：
    from services.asset_type import AssetType

    # 使用常量
    asset_svc.create(asset_type=AssetType.VIDEO, ...)
    asset_svc.create(asset_type=AssetType.STORYBOARD, ...)
"""
from typing import List


class AssetType:
    """资产类型常量（字符串常量，便于与现有代码兼容）"""

    # 基础类型
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"

    # 分镜相关
    STORYBOARD = "storyboard"
    STORYBOARD_BATCH = "storyboard_batch"
    STORYBOARD_LAYERED = "storyboard_layered"
    STORYBOARD_MULTI = "storyboard_multi"

    # 概念/参考
    CONCEPT = "concept"
    MULTI_VIEW = "multi_view"
    PANO = "pano"

    # 后处理
    EDIT = "edit"
    REFINE = "refine"

    # 文本
    SCRIPT = "script"

    # ControlNet 中间产物
    MASK = "mask"
    DEPTH = "depth"
    DEPTH_CLEAN = "depth_clean"
    POSE = "pose"
    LINEART = "lineart"

    @classmethod
    def all_types(cls) -> List[str]:
        """返回所有合法的资产类型字符串"""
        return [
            cls.IMAGE, cls.VIDEO, cls.AUDIO,
            cls.STORYBOARD, cls.STORYBOARD_BATCH, cls.STORYBOARD_LAYERED, cls.STORYBOARD_MULTI,
            cls.CONCEPT, cls.MULTI_VIEW, cls.PANO,
            cls.EDIT, cls.REFINE,
            cls.SCRIPT,
            cls.MASK, cls.DEPTH, cls.DEPTH_CLEAN, cls.POSE, cls.LINEART,
        ]

    @classmethod
    def is_valid(cls, asset_type: str) -> bool:
        """检查资产类型是否合法"""
        return asset_type in cls.all_types()

    @classmethod
    def is_image_like(cls, asset_type: str) -> bool:
        """是否为图片类资产（可用于图片生成工作流的输入）"""
        return asset_type in (
            cls.IMAGE, cls.STORYBOARD, cls.STORYBOARD_LAYERED, cls.STORYBOARD_MULTI,
            cls.CONCEPT, cls.MULTI_VIEW, cls.PANO,
            cls.MASK, cls.DEPTH, cls.DEPTH_CLEAN, cls.POSE, cls.LINEART, cls.REFINE,
        )

    @classmethod
    def is_video_like(cls, asset_type: str) -> bool:
        """是否为视频类资产"""
        return asset_type == cls.VIDEO
