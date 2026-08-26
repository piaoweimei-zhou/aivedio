"""P1c 多平台发布扩展单测：平台话题/文案差异化 + manifest 归因 + 校验。"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from app.publishing import (PLATFORMS, build_publish_package, _hashtags,
                            _PLATFORM_NOTES)
from app.models import Dimension, Monetizer


def _tmp_video():
    """造一个临时"成片"文件。"""
    tmp = tempfile.TemporaryDirectory()
    v = os.path.join(tmp.name, "video.mp4")
    with open(v, "wb") as f:
        f.write(b"\x00\x00\x00\x20ftypmp42")
    return tmp, v


def test_platform_whitelist_and_notes():
    assert set(PLATFORMS) == {"douyin", "kuaishou", "bilibili", "xiaohongshu"}
    for p in PLATFORMS:
        assert _PLATFORM_NOTES[p]


def test_hashtags_include_platform_and_content():
    tags = _hashtags("bilibili", Dimension.KNOWLEDGE, Monetizer.COURSE)
    assert any("#B站" in t for t in tags)          # 平台话题
    assert any("#干货" in t for t in tags)          # 维度话题
    assert any("#副业" in t for t in tags)          # 变现话题
    assert len(tags) <= 8


def test_build_package_platform_douyin(monkeypatch):
    tmp, v = _tmp_video()
    monkeypatch.setenv("TRAFFICOS_DATA_DIR", tmp.name)
    res = build_publish_package(
        title="测试内容", video_path=v, platform="douyin",
        dimension=Dimension.KNOWLEDGE, monetizer=Monetizer.COURSE,
    )
    assert res["platform"] == "douyin"
    with open(res["manifest_path"], encoding="utf-8") as f:
        m = json.load(f)
    assert m["platform"] == "douyin"
    assert "竖版 9:16" in m["platform_notes"]
    assert any("#抖音热门" in t for t in m["hashtags"])
    tmp.cleanup()


def test_build_package_platform_bilibili(monkeypatch):
    tmp, v = _tmp_video()
    monkeypatch.setenv("TRAFFICOS_DATA_DIR", tmp.name)
    res = build_publish_package(title="T", video_path=v, platform="bilibili")
    with open(res["manifest_path"], encoding="utf-8") as f:
        m = json.load(f)
    assert m["platform"] == "bilibili"
    assert "横版 16:9" in m["platform_notes"]
    assert any("#B站" in t for t in m["hashtags"])
    tmp.cleanup()


def test_build_package_xiaohongshu_caption_style(monkeypatch):
    tmp, v = _tmp_video()
    monkeypatch.setenv("TRAFFICOS_DATA_DIR", tmp.name)
    res = build_publish_package(title="资源合集", video_path=v, platform="xiaohongshu")
    assert "｜实测分享" in res["caption"]          # 小红书种草风
    with open(res["manifest_path"], encoding="utf-8") as f:
        m = json.load(f)
    assert any("#种草" in t for t in m["hashtags"])
    tmp.cleanup()


def test_build_package_rejects_bad_platform(monkeypatch):
    tmp, v = _tmp_video()
    monkeypatch.setenv("TRAFFICOS_DATA_DIR", tmp.name)
    with pytest.raises(ValueError):
        build_publish_package(title="T", video_path=v, platform="weibo")
    tmp.cleanup()


def test_build_package_defaults_douyin(monkeypatch):
    tmp, v = _tmp_video()
    monkeypatch.setenv("TRAFFICOS_DATA_DIR", tmp.name)
    res = build_publish_package(title="T", video_path=v)
    assert res["platform"] == "douyin"
    tmp.cleanup()
