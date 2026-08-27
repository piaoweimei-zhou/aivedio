# -*- coding: utf-8 -*-
"""解析器基类模块（迁自 bupvideo common/parser_base，仅保留 B 站链路所需）

定义统一的解析器接口、解析结果数据结构、解析异常。
"""
from .base import BaseParser
from .exceptions import (
    APIRateLimitError,
    APIResponseError,
    InvalidVideoIdError,
    NetworkError,
    ParseError,
    ParserNotImplementedError,
    PrivateVideoError,
    SignatureError,
    UnsupportedURLError,
    VideoNotFoundError,
)
from .result import ParseResult, VideoQuality

__all__ = [
    "BaseParser",
    "ParseResult",
    "VideoQuality",
    "ParseError",
    "NetworkError",
    "UnsupportedURLError",
    "InvalidVideoIdError",
    "APIRateLimitError",
    "APIResponseError",
    "VideoNotFoundError",
    "PrivateVideoError",
    "SignatureError",
    "ParserNotImplementedError",
]
