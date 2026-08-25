"""MiniMax H3 视频工作流构建器单元测试（不依赖 ComfyUI/GPU）"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.workflow_minimax import (  # noqa: E402
    build_minimax_h3_video_workflow,
    frames_for_duration,
    resolve_minimax_size,
)


def _all_refs_int(wf):
    refs = []
    for nid, node in wf.items():
        for k, v in node["inputs"].items():
            if isinstance(v, list) and len(v) == 2:
                refs.append((nid, k, v))
    return refs


def test_frames_for_duration_align17():
    # 5s→120 帧，对齐到 17 整数倍 →124
    assert frames_for_duration(5) == 124
    # 10s→240，240%17=2，(5-2)=3 →243
    assert frames_for_duration(10) == 243
    # 很短时长也有下限
    assert frames_for_duration(0.1) >= 5


def test_resolve_minimax_size_align32():
    assert resolve_minimax_size(480, 864) == (480, 864)
    assert resolve_minimax_size(0, 0) == (480, 864)  # 默认(width or 480 / height or 864)
    # 非 32 倍数向下取整
    assert resolve_minimax_size(500, 900) == (480, 896)


def test_build_workflow_prompt_injected():
    wf = build_minimax_h3_video_workflow(
        prompt="一只橘猫趴在窗台晒太阳",
        width=480,
        height=864,
        duration_seconds=5,
        seed=42,
        audio_mode="native",
        filename_prefix="mmh3_test",
    )
    assert wf["cond"]["class_type"] == "MiniMaxH3AudioConditioningT8"
    assert "橘猫趴在窗台" in wf["cond"]["inputs"]["prompt"]
    assert wf["noise"]["inputs"]["noise_seed"] == 42
    assert wf["cond"]["inputs"]["length"] == 124
    assert wf["cond"]["inputs"]["width"] == 480
    assert wf["cond"]["inputs"]["height"] == 864
    assert wf["cond"]["inputs"]["audio_mode"] == "native"
    assert wf["combine"]["class_type"] == "VHS_VideoCombine"
    assert wf["combine"]["inputs"]["format"] == "video/h264-mp4"


def test_build_workflow_all_refs_are_int():
    wf = build_minimax_h3_video_workflow("测试提示词")
    refs = _all_refs_int(wf)
    assert refs, "应存在节点引用"
    for nid, key, (src, idx) in refs:
        assert isinstance(idx, int), f"{nid}.{key} 索引应为 int, 实为 {idx!r}"
        assert idx >= 0
        # 引用目标必须存在
        assert src in wf, f"{nid}.{key} 引用了不存在的节点 {src}"
    # 覆盖核心链路必须出现的引用
    assert wf["sampler"]["inputs"]["latent_image"] == ["cond", 1]
    assert wf["sampler"]["inputs"]["sampler"] == ["rate", 1]
    assert wf["sampler"]["inputs"]["sigmas"] == ["rate", 2]
    assert wf["combine"]["inputs"]["images"] == ["avdecode", 0]
    assert wf["avdecode"]["inputs"]["av_latent"] == ["sampler", 0]


def test_build_workflow_minimal_required_cond():
    wf = build_minimax_h3_video_workflow("hello")
    req = wf["cond"]["inputs"]
    # 新版本节点必填字段齐全且类型正确
    assert isinstance(req["add_source_as_reference"], bool)
    assert isinstance(req["strict_prompt_tags"], bool)
    assert isinstance(req["audio_denoise_strength"], float)
    assert isinstance(req["prompt_primary_audio_ordinal"], int)
    assert req["ref_image_size"] == "match"
    assert req["reference_video_policy"] == "official_2_to_15s"
    assert req["task_type"] == "auto"
