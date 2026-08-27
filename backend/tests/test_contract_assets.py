"""P-INTEGRATION-1 测试：CreativeOS 画面资产 → director 成片。

核心验收：
1) acts 的 visual_hint（=CreativeOS shot.prompt）成为 video step 的 segment_prompts
2) storyboard(16字段) / characters 角色卡透传到 video & concept step params
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.contract_api import ContentSpec, _build_steps_from_spec  # noqa: E402

SHOT1 = {
    "shot": 1, "act_index": 0, "camera": "中景", "camera_move": "推镜",
    "lighting": "暖光", "composition": "三分法", "style": "电影感",
    "content": "user frowning at phone",
    "start_frame": "hand holding phone", "end_frame": "face close-up",
    "bridge_in": "", "bridge_out": "phone screen", "template": "T-close",
    "duration": 5.0, "ratio": "9:16", "character_ref": "char_01",
    "prompt": "user, 25-35yo, short hair, blue tshirt, frowning at phone, "
              "medium shot, dolly in, warm lighting",
}
CHAR1 = {"character_id": "char_01", "name": "使用者",
         "appearance": "25-35岁短发", "outfit": "蓝T恤", "style_anchor": "写实暖调"}


def _spec(**overrides) -> ContentSpec:
    base = {
        "content_id": "cs_integration_1",
        "script": {
            "type": "video_script_mixin",
            "hook": "水印烦人",
            "acts": [
                {"narration": "水印好烦", "emotion": "共情", "duration": 5,
                 "visual": "人物看手机皱眉", "visual_hint": SHOT1["prompt"]},
            ],
            "cta": "关注",
            "storyboard": [SHOT1],
            "characters": [CHAR1],
        },
        "params": {"provider_id": "minimax_h3", "platform": "douyin",
                   "aspect_ratio": "9:16", "resolution": "720p", "fps": 24},
    }
    base.update(overrides)
    return ContentSpec(**base)


def test_visual_hint_becomes_segment_prompts():
    """CreativeOS shot.prompt（经 visual_hint）→ video step 的 segment_prompts。"""
    steps = _build_steps_from_spec(_spec())
    video = next(s for s in steps if s["stage_id"] == "video")
    segs = video["params"]["segment_prompts"]
    assert segs == [SHOT1["prompt"]]
    # 画面竞争力主源：不是 fallback 到台词
    assert segs[0] != "水印好烦"
    assert "frowning at phone" in segs[0]


def test_storyboard_and_characters_passed_to_video():
    steps = _build_steps_from_spec(_spec())
    video = next(s for s in steps if s["stage_id"] == "video")
    assert video["params"]["storyboard"][0]["start_frame"] == "hand holding phone"
    assert video["params"]["storyboard"][0]["prompt"] == SHOT1["prompt"]
    assert video["params"]["characters"][0]["character_id"] == "char_01"


def test_characters_passed_to_concept():
    steps = _build_steps_from_spec(_spec())
    concept = next(s for s in steps if s["stage_id"] == "concept")
    assert concept["params"]["characters"][0]["name"] == "使用者"
    # concept 图 prompt = 首段画面（含角色外观）
    assert "frowning at phone" in concept["params"]["prompt"]


def test_no_storyboard_fallback_to_visual_hint():
    """无 storyboard 时：visual_hint 仍作为 prompt，否则 fallback 台词。"""
    steps = _build_steps_from_spec(_spec(script={
        "type": "video_script_mixin",
        "acts": [{"narration": "台词", "duration_s": 5, "visual_hint": "森林清晨"}],
    }))
    video = next(s for s in steps if s["stage_id"] == "video")
    assert video["params"]["segment_prompts"] == ["森林清晨"]
    assert video["params"]["storyboard"] == []
