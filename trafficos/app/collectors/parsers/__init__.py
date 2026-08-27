# -*- coding: utf-8 -*-
"""平台解析器（迁自 bupvideo watermark_remover/parsers）

- bilibili：requests 匿名 view API，含真实播放量（A/B 两线主数据源）
- kuaishou / xiaohongshu：requests 主通道 + Playwright 兜底（可选，无 playwright 时走 requests）
- douyin：Playwright 拦截 detail XHR（必需 playwright；无则返回明确错误）

合规：仅聚合分析/内部选题，不搬运内容。
"""
from .bilibili import BilibiliParser
from .douyin import DouyinParser
from .kuaishou import KuaishouParser
from .xiaohongshu import XiaohongshuParser

PARSER_REGISTRY = {
    "douyin": DouyinParser,
    "kuaishou": KuaishouParser,
    "xiaohongshu": XiaohongshuParser,
    "bilibili": BilibiliParser,
}

PLATFORM_DISPLAY_NAMES = {
    "douyin": "抖音",
    "kuaishou": "快手",
    "xiaohongshu": "小红书",
    "bilibili": "B站",
}

__all__ = [
    "BilibiliParser", "DouyinParser", "KuaishouParser", "XiaohongshuParser",
    "PARSER_REGISTRY", "PLATFORM_DISPLAY_NAMES",
]
