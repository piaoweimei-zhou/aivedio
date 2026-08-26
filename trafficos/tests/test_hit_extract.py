"""P1b 爆款拆解链单测：工具下载事件自动写 hits 拆解库。"""
from __future__ import annotations

import asyncio
import tempfile

from app.api.signals import report_tool_event
from app.models import ToolEvent
from app.storage import get_collection


def _tmp(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("TRAFFICOS_DATA_DIR", tmp.name)
    return tmp


def _run(coro):
    return asyncio.run(coro)


def test_download_event_writes_hit(monkeypatch):
    tmp = _tmp(monkeypatch)
    evt = ToolEvent(
        tool_name="watermark-remover",
        action="download",
        title="某明星采访视频",
        url="https://example.com/v/123",
        field="entertainment",
    )
    sig = _run(report_tool_event(evt))
    # 信号入库
    assert sig.source == "tool:watermark-remover"
    # 爆款拆解自动入库（source=auto）
    hits = get_collection("hits").list()
    assert len(hits) == 1
    h = hits[0]
    assert h["source"] == "auto"
    assert h["title"] == "某明星采访视频"
    assert h["url"] == "https://example.com/v/123"
    assert h["raw_meta"]["tool_name"] == "watermark-remover"
    assert h["raw_meta"]["keyword"]  # 自动提取关键词非空
    tmp.cleanup()


def test_non_download_event_no_hit(monkeypatch):
    tmp = _tmp(monkeypatch)
    evt = ToolEvent(tool_name="watermark-remover", action="search", title="去水印")
    _run(report_tool_event(evt))
    assert len(get_collection("hits").list()) == 0
    tmp.cleanup()


def test_download_without_url_or_title_no_hit(monkeypatch):
    tmp = _tmp(monkeypatch)
    evt = ToolEvent(tool_name="watermark-remover", action="download")  # 无 url/title
    _run(report_tool_event(evt))
    assert len(get_collection("hits").list()) == 0
    tmp.cleanup()


def test_tool_tracker_sdk_has_track_download():
    from sdk.tool_tracker import ToolTracker

    t = ToolTracker(base_url="http://127.0.0.1:8001", tool_name="x")
    assert hasattr(t, "track_download")
