"""
解析结果数据结构
所有平台解析器返回统一的ParseResult对象
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List


class VideoQuality(Enum):
    """视频质量等级"""

    LOW = "low"  # 低清（480p及以下）
    MEDIUM = "medium"  # 标清（720p）
    HIGH = "high"  # 高清（1080p）
    ULTRA = "ultra"  # 超清（2K及以上）
    UNKNOWN = "unknown"  # 未知


@dataclass
class ParseResult:
    """
    解析结果数据结构
    所有平台解析器返回统一的ParseResult对象
    """

    # 基本信息
    success: bool = False  # 是否解析成功
    url: str = ""  # 原始输入URL
    platform: str = ""  # 平台名称（douyin/kuaishou/xiaohongshu等）
    video_id: str = ""  # 视频ID

    # 视频信息
    title: str = ""  # 视频标题
    description: str = ""  # 视频描述/文案
    author: str = ""  # 作者昵称
    author_id: str = ""  # 作者ID
    author_avatar: str = ""  # 作者头像URL

    # 视频地址
    video_url: str = ""  # 无水印视频直链（核心）
    video_url_list: List[str] = field(default_factory=list)  # 多清晰度视频地址列表
    cover_url: str = ""  # 封面图URL
    music_url: str = ""  # 背景音乐URL
    music_name: str = ""  # 背景音乐名称

    # 视频属性
    duration: int = 0  # 视频时长（秒）
    width: int = 0  # 视频宽度
    height: int = 0  # 视频高度
    quality: VideoQuality = VideoQuality.UNKNOWN  # 视频质量
    file_size: int = 0  # 文件大小（字节）

    # 互动数据
    likes: int = 0  # 点赞数
    comments: int = 0  # 评论数
    shares: int = 0  # 分享数
    collects: int = 0  # 收藏数
    plays: int = 0  # 播放数

    # 标签/话题
    tags: List[str] = field(default_factory=list)  # 标签列表
    topics: List[str] = field(default_factory=list)  # 话题列表

    # 元数据
    publish_time: str = ""  # 发布时间（ISO格式或时间戳）
    create_time: int = 0  # 创建时间戳
    raw_data: dict = field(default_factory=dict)  # 原始API返回数据（调试用）

    # 错误信息
    error: str = ""  # 错误信息（解析失败时）
    error_code: int = 0  # 错误码

    def __post_init__(self):
        """初始化后处理"""
        # 如果有video_url但video_url_list为空，自动添加
        if self.video_url and not self.video_url_list:
            self.video_url_list = [self.video_url]

    def to_dict(self) -> dict:
        """转换为字典（用于JSON序列化）"""
        result = {}
        for key, value in self.__dict__.items():
            if isinstance(value, Enum):
                result[key] = value.value
            elif isinstance(value, list) and value and isinstance(value[0], Enum):
                result[key] = [v.value for v in value]
            else:
                result[key] = value
        return result

    def to_json(self, indent: int = 2) -> str:
        """转换为JSON字符串"""
        import json

        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def error_result(
        cls, url: str, platform: str, error: str, error_code: int = -1
    ) -> "ParseResult":
        """创建错误结果"""
        return cls(success=False, url=url, platform=platform, error=error, error_code=error_code)

    @classmethod
    def success_result(cls, url: str, platform: str, video_url: str, **kwargs) -> "ParseResult":
        """创建成功结果"""
        return cls(success=True, url=url, platform=platform, video_url=video_url, **kwargs)
