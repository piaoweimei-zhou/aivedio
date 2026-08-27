# -*- coding: utf-8 -*-
"""平台解析器（迁自 bupvideo watermark_remover/parsers，当前只启用 B 站链路）

B 站 view API 匿名可解析、含真实播放量，作为主线第一阶段 A/B 两线的数据源；
抖音/快手/小红书解析依赖 Playwright 等浏览器环境，后续有需要再迁入。
"""
from .bilibili import BilibiliParser

PARSER_REGISTRY = {
    "bilibili": BilibiliParser,
}

PLATFORM_DISPLAY_NAMES = {
    "bilibili": "B站",
}

__all__ = ["BilibiliParser", "PARSER_REGISTRY", "PLATFORM_DISPLAY_NAMES"]
