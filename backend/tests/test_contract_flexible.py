"""L1：契约层通用剧本展开器测试（acts[N] → 逐段视频，任意段数/时长）"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.contract_api import (  # noqa: E402
    ContentSpec,
    _build_script_video_steps,
    _build_steps_from_spec,
)


def _spec(**overrides) -> ContentSpec:
    base = {
        "content_id": "c_test_001",
        "script": {
            "type": "video_script_mixin",
            "acts": [
                {"narration": "开场旁白", "duration_s": 5, "visual_hint": "森林清晨"},
                {"narration": "中段剧情", "duration_s": 10, "visual_hint": "角色转身"},
                {"narration": "结尾高潮", "duration_s": 15, "visual_hint": "夕阳下"},
            ],
        },
        "params": {"aspect_ratio": "9:16", "resolution": "720p", "fps": 24},
    }
    base.update(overrides)
    return ContentSpec(**base)


# ---------- 通用展开 ----------

def test_acts_expands_to_segmented_video_step():
    """3 幕（5/10/15s）→ concept→video→subtitle→hook→export 5 步。"""
    steps = _build_steps_from_spec(_spec())
    assert [s["stage_id"] for s in steps] == [
        "concept", "video", "subtitle", "hook_overlay", "export",
    ]
    assert steps[0]["stage_id"] == "concept"     # ⭐ 真实图片资产（I2V 输入）
    step = steps[1]
    assert step["stage_id"] == "video"
    assert step["provider_id"] == "minimax_h3"
    assert step["input_from_steps"] == ["s1_concept_scene1"]  # 引用概念图
    p = step["params"]
    assert p["segmented_oneclick"] is True
    assert p["segment_durations"] == [5.0, 10.0, 15.0]      # ⭐ 任意组合时长
    assert len(p["segment_prompts"]) == 3                   # ⭐ 任意段数
    assert p["segment_prompts"][0] == "森林清晨"             # visual_hint 优先
    assert p["tts_texts"] == ["开场旁白", "中段剧情", "结尾高潮"]
    assert p["tts_enabled"] is True
    assert p["aspect_ratio"] == "9:16"
    assert p["resolution"] == "720p"
    assert p["frame_rate"] == 24
    # 字幕/钩子/导出默认开启（对齐一键成片默认流程）
    assert steps[2]["stage_id"] == "subtitle"
    assert steps[2]["params"]["subtitle_texts"] == [
        {"text": "开场旁白"}, {"text": "中段剧情"}, {"text": "结尾高潮"},
    ]
    assert steps[3]["stage_id"] == "hook_overlay"
    assert steps[4]["stage_id"] == "export"


def test_platform_profiles_shape():
    """平台画像：concept 比例 / video 尺寸 / 导出规格按平台对齐。"""
    for platform, prof in (
        ("douyin", ("1080x1920", "9:16", (720, 1280), "1080x1920")),
        ("kuaishou", ("1080x1920", "9:16", (720, 1280), "1080x1920")),
        ("xiaohongshu", ("1080x1440", "3:4", (720, 960), "1080x1440")),
        ("bilibili", ("1920x1080", "16:9", (1280, 720), "1920x1080")),
    ):
        spec = _spec(params={"platform": platform})
        steps = _build_steps_from_spec(spec)
        assert steps[0]["params"]["size"] == prof[0], platform      # concept 比例
        assert steps[1]["params"]["aspect_ratio"] == prof[1], platform
        assert steps[1]["params"]["width"] == prof[2][0], platform
        assert steps[1]["params"]["height"] == prof[2][1], platform
        assert steps[4]["params"]["resolution"] == prof[3], platform  # 导出规格


def test_single_5s_act():
    """单幕 5s → 单段，时长自由（用户要的快速测试场景）。"""
    spec = _spec()
    spec.script["acts"] = [{"narration": "一句话", "duration_s": 5}]
    steps = _build_steps_from_spec(spec)
    p = steps[1]["params"]
    assert p["segment_durations"] == [5.0]
    assert len(p["segment_prompts"]) == 1
    assert p["tts_texts"] == ["一句话"]


def test_mixed_durations():
    """混合时长 4/7/180 → 原样透传。"""
    spec = _spec()
    spec.script["acts"] = [
        {"duration_s": 4}, {"duration_s": 7}, {"duration_s": 180},
    ]
    p = _build_steps_from_spec(spec)[1]["params"]
    assert p["segment_durations"] == [4.0, 7.0, 180.0]


def test_default_duration_when_missing():
    """acts 未给 duration_s → 默认 5s。"""
    spec = _spec()
    spec.script["acts"] = [{"narration": "无时长"}]
    p = _build_steps_from_spec(spec)[1]["params"]
    assert p["segment_durations"] == [5.0]


def test_visual_hint_fallback_to_narration():
    """无 visual_hint → 用 narration 作为画面提示。"""
    spec = _spec()
    spec.script["acts"] = [{"narration": "只有台词", "duration_s": 6}]
    p = _build_steps_from_spec(spec)[1]["params"]
    assert p["segment_prompts"][0] == "只有台词"


def test_no_tts_when_no_narration():
    spec = _spec()
    spec.script["acts"] = [{"duration_s": 5, "visual_hint": "纯画面"}]
    p = _build_steps_from_spec(spec)[1]["params"]
    assert p["tts_enabled"] is False
    assert p["tts_texts"] == []
    # 无台词 → 字幕步骤空
    subs = [s for s in _build_steps_from_spec(spec) if s["stage_id"] == "subtitle"]
    assert subs and subs[0]["params"]["subtitle_texts"] == []


def test_export_default_on():
    """export 默认开启（对齐默认成片流程），平台规格导出。"""
    spec = _spec(params={"platform": "bilibili"})
    steps = _build_steps_from_spec(spec)
    export = steps[4]
    assert export["stage_id"] == "export"
    assert export["params"]["resolution"] == "1920x1080"
    assert export["input_from_steps"] == ["s4_hook"]


def test_reference_assets_passed():
    """spec.assets → reference_image_files（图生视频参考）。"""
    spec = _spec()
    spec.assets = ["https://example.com/ref.png"]
    p = _build_steps_from_spec(spec)[1]["params"]
    assert p["reference_image_files"] == ["https://example.com/ref.png"]


# ---------- 兼容性 ----------

def test_legacy_single_step_unchanged():
    """非 video_script_mixin 或空 acts → 原单 step 逻辑。"""
    spec = _spec()
    spec.script = {"type": "storyboard_batch", "topic": "x"}
    steps = _build_steps_from_spec(spec)
    assert len(steps) == 1
    assert steps[0]["stage_id"] == "storyboard_batch"
    assert steps[0]["params"]["script"] == spec.script


def test_video_script_mixin_no_acts_legacy():
    spec = _spec()
    spec.script = {"type": "video_script_mixin", "topic": "x"}
    steps = _build_steps_from_spec(spec)
    assert len(steps) == 1
    assert steps[0]["params"]["script"] == spec.script


def test_unsupported_type_raises():
    with pytest.raises(Exception) as exc:
        _build_steps_from_spec(_spec(script={"type": "nope"}))
    assert "unsupported" in str(exc.value)


# ---------- 辅助函数 ----------

def test_build_script_video_steps_direct():
    """直接调用辅助函数（纯构造）。"""
    spec = _spec()
    steps = _build_script_video_steps(spec, spec.script["acts"])
    assert steps[0]["stage_id"] == "concept"     # 前置概念图
    assert steps[1]["params"]["segment_durations"] == [5.0, 10.0, 15.0]
