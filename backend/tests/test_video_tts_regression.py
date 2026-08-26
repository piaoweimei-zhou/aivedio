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


def test_single_seg_tts_updates_images_to_mixed_url(monkeypatch):
    """P0 单段无声音回归：单段混音后 r.images 必须同步指向混音产物。

    修复前：_generate_h3_segmented 只改 r.video_url/r.image_url，
    而 r.images 仍是 H3 原片，_register_asset 用 result.images 注册，
    导致 subtitle/hook/export 全部用无声源，成片听不到人声。
    """
    import asyncio as _asyncio
    from types import SimpleNamespace

    from services.provider_service import ProviderResult
    from services.stages.video_stage import VideoStage

    stage = VideoStage()
    called = {}

    async def fake_gen_video(**kwargs):
        return ProviderResult(
            provider_id="minimax_h3",
            video_url="http://x/h3_orig.mp4",
            image_url="http://x/h3_orig.mp4",
            images=["http://x/h3_orig.mp4"],
            filenames=["h3_orig.mp4"],
            seed=0,
            elapsed_ms=1000,
            prompt="画面",
        )

    async def fake_tts(svc, text, i):
        return "http://x/tts.flac"

    async def fake_mix(video_url, tts_url):
        called["video_url"] = video_url
        return "http://x/mixed.mp4"

    monkeypatch.setattr(stage, "_gen_tts_segment", fake_tts)
    monkeypatch.setattr(stage, "_mix_tts_into_segment", fake_mix)

    async def run():
        return await stage._generate_h3_segmented(
            provider_svc=SimpleNamespace(generate_video=fake_gen_video),
            prompt="测试画面",
            segment_prompts=["画面提示"],
            tts_texts=["三秒搞定去水印"],
            images=["http://x/c.png"],
            width=720,
            height=1280,
            duration=5.0,
            segment_seconds=5.0,
            seed=1,
            model="",
            aspect_ratio="9:16",
            resolution="720p",
            segment_durations=[],
        )

    result = _asyncio.run(run())
    # 关键断言：_register_asset 用 result.images，必须指向混音产物（含人声）
    assert result.images == ["http://x/mixed.mp4"], (
        f"result.images 应指向混音产物，实际={result.images}"
    )
    assert result.image_url == "http://x/mixed.mp4"
    assert called["video_url"] == "http://x/h3_orig.mp4"
