# -*- coding: utf-8 -*-
"""P0 配音丢失回归基线：minimax_h3 单段（1 镜）有台词必须走逐镜 TTS 混音路径。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.stages.video_stage import _should_use_h3_segmented  # noqa: E402


def test_h3_single_segment_with_tts_goes_segmented():
    """⭐ 修复前 bug：单段+台词 → False（台词丢给 H3，成片无声旁白）。
    修复后：必须 True（走逐镜 → 独立 TTS 混音）。"""
    assert _should_use_h3_segmented(
        provider_id="minimax_h3",
        segment_prompts=["画面提示"],
        force_segmented=True,
        tts_texts=["三秒搞定去水印"],
    ) is True


def test_h3_single_segment_no_tts():
    """单段 + 无台词 → 不走逐镜（纯画面，无需 TTS）。"""
    assert _should_use_h3_segmented(
        provider_id="minimax_h3",
        segment_prompts=["纯画面"],
        force_segmented=False,
        tts_texts=[],
    ) is False


def test_h3_single_segment_blank_tts():
    """单段 + 空白台词（空串/空白）→ 视为无台词，不走逐镜。"""
    assert _should_use_h3_segmented(
        provider_id="minimax_h3",
        segment_prompts=["画面"],
        force_segmented=False,
        tts_texts=["   ", ""],
    ) is False


def test_h3_multi_segment_always_segmented():
    """多段（>1 镜）无论有无台词都走逐镜。"""
    assert _should_use_h3_segmented(
        provider_id="minimax_h3",
        segment_prompts=["A", "B"],
        force_segmented=False,
        tts_texts=[],
    ) is True
    assert _should_use_h3_segmented(
        provider_id="minimax_h3",
        segment_prompts=["A", "B"],
        force_segmented=False,
        tts_texts=["x", "y"],
    ) is True


def test_non_h3_provider_never_segmented():
    """非 minimax_h3 provider 不走逐镜路径。"""
    assert _should_use_h3_segmented(
        provider_id="comfyui",
        segment_prompts=["A", "B"],
        force_segmented=True,
        tts_texts=["x"],
    ) is False


def test_no_segments_not_segmented():
    """无分段画面且未强制分段 → 不走逐镜。"""
    assert _should_use_h3_segmented(
        provider_id="minimax_h3",
        segment_prompts=[],
        force_segmented=False,
        tts_texts=["台词"],
    ) is False
