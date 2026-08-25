"""字幕烧录阶段单元测试"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.stages.subtitle_stage import SubtitleStage  # noqa: E402


@pytest.fixture
def stage():
    return SubtitleStage()


def test_split_lines_by_punctuation(stage):
    lines = stage._split_lines("今天天气真好。我们出去玩吧！你吃饭了吗？")
    assert lines == ["今天天气真好。", "我们出去玩吧！", "你吃饭了吗？"]


def test_split_lines_caps_at_18_chars(stage):
    long_line = "这是一个非常非常非常非常非常非常非常非常非常非常非常长的句子没有标点"
    lines = stage._split_lines(long_line)
    assert all(len(line) <= 18 for line in lines)
    assert "".join(lines) == long_line


def test_estimate_duration_cjk(stage):
    # 10 个中文字 → 10*0.28 + 0.7 = 3.5
    assert stage._estimate_duration("一二三四五六七八九十") == pytest.approx(3.5)


def test_build_timeline_uses_explicit_timestamps(stage):
    texts = [{"text": "你好", "start": 1.0, "end": 2.0}]
    timeline = stage._build_timeline(texts, 30.0)
    assert timeline[0]["start"] == 1.0
    assert timeline[0]["end"] == 2.0


def test_build_timeline_auto_estimate_scales_to_video(stage):
    # 文案总时长远超视频时长，应压缩到视频时长内
    texts = [{"text": "一二三四五六七八九十" * 20}]  # 200 字 ≈ 56.7s
    timeline = stage._build_timeline(texts, 10.0)
    assert timeline[-1]["end"] <= 10.0 + 0.01
    assert timeline[0]["start"] == 0.5


def test_ass_time_format(stage):
    assert stage._ass_time(0) == "0:00:00.00"
    assert stage._ass_time(65.5) == "0:01:05.50"
    assert stage._ass_time(3661.25) == "1:01:01.25"


def test_highlight_keywords(stage):
    text = "评论区扣1领工具"
    result = stage._highlight(text, ["扣1"], "FFFFFF", "00FFFF")
    assert "{\\c&H00FFFF&}扣1{\\c&HFFFFFF&}" in result


def test_highlight_escapes_ass_special_chars(stage):
    result = stage._highlight("a{b}c", [], "FFFFFF", "00FFFF")
    assert "\\{" in result and "\\}" in result


def test_build_ass_structure(stage):
    timeline = [{"text": "你好世界", "start": 0.5, "end": 2.0}]
    ass = stage._build_ass(timeline, 1080, 1920, {}, ["世界"])
    assert "[Script Info]" in ass
    assert "PlayResX: 1080" in ass
    assert "PlayResY: 1920" in ass
    assert "Dialogue: 0,0:00:00.50,0:00:02.00,Default" in ass
    assert "{\\c&H00FFFF&}世界{\\c&HFFFFFF&}" in ass
    # 字号应随宽度缩放（1080*0.07=75）
    assert "Style: Default,Microsoft YaHei,75," in ass
