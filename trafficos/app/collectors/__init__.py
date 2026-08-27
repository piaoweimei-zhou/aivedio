# -*- coding: utf-8 -*-
"""采集器模块：把平台真实视频解析为结构化数据（真实热点 + ROI 数据源）。"""
from .collector import parse_one, _heat, report_tool_events  # noqa: F401

__all__ = ["parse_one", "_heat", "report_tool_events"]
