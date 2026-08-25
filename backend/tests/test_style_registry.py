"""网感风格注册表 + 阶段接入 单元测试"""

from services.style_registry import (
    _DEFAULT_STYLE_ID,
    get_script_guidance,
    get_style,
    get_style_or_default,
    get_style_params,
    get_visual_prompt,
    list_styles,
)
from services.stages.script_stage import ScriptStage
from services.stages.concept_stage import _apply_style_prompt as concept_apply
from services.stages.storyboard_stage import _apply_style_prompt as storyboard_apply


def test_list_styles_has_six_and_default():
    styles = list_styles()
    assert len(styles) == 6
    ids = {s["style_id"] for s in styles}
    assert ids == {
        "wanggan_vivid",
        "cinematic",
        "healing",
        "cyberpunk",
        "retro_film",
        "fresh_japanese",
    }
    defaults = [s for s in styles if s.get("is_default")]
    assert len(defaults) == 1
    assert defaults[0]["style_id"] == _DEFAULT_STYLE_ID


def test_style_fields_complete():
    for s in list_styles():
        assert s["name"]
        assert s["description"]
        assert s["script_guidance"]
        assert s["visual_prompt"]
        assert s["params"]["steps"] > 0
        assert s["tags"]


def test_get_style_and_fallback():
    assert get_style("cinematic")["name"] == "电影感"
    assert get_style("not_exist") is None
    assert get_style(None) is None
    assert get_style_or_default("not_exist")["style_id"] == _DEFAULT_STYLE_ID
    assert get_style_or_default(None)["style_id"] == _DEFAULT_STYLE_ID
    assert get_style_or_default("cyberpunk")["style_id"] == "cyberpunk"


def test_getters():
    assert "高饱和" in get_script_guidance("wanggan_vivid")
    assert "high saturation" in get_visual_prompt("wanggan_vivid")
    assert get_style_params("wanggan_vivid")["steps"] == 25
    # 未知风格回退默认
    assert get_script_guidance("nope") == get_script_guidance(None)


def test_script_stage_system_prompt_injects_style():
    stage = ScriptStage()
    prompt = stage._build_system_prompt(
        video_type="full_ai_short",
        template={"label": "全AI情景短剧", "structure": "三幕", "tone": "剧情吸引"},
        acts=3,
        duration_seconds=30,
        characters=[],
        tone_extra="",
        target_audience="",
        hook_style="comment_1",
        style_guidance="整体风格：赛博朋克。未来感、科技感强",
    )
    assert "网感风格" in prompt
    assert "赛博朋克" in prompt
    # 无风格时不注入
    prompt2 = stage._build_system_prompt(
        video_type="full_ai_short",
        template={"label": "全AI情景短剧", "structure": "三幕", "tone": "剧情吸引"},
        acts=3,
        duration_seconds=30,
        characters=[],
        tone_extra="",
        target_audience="",
        hook_style="comment_1",
        style_guidance="",
    )
    assert "网感风格" not in prompt2


def test_concept_style_prompt_appended():
    prompt = concept_apply("一个穿汉服的女孩", "cyberpunk")
    assert "cyberpunk" in prompt
    assert "一个穿汉服的女孩" in prompt
    # 重复调用不重复追加
    prompt2 = concept_apply(prompt, "cyberpunk")
    assert prompt2 == prompt
    # 空 style_id 不追加（保持向后兼容）
    assert concept_apply("测试", "") == "测试"
    # 未知 style_id 不追加
    assert concept_apply("测试", "nope") == "测试"


def test_storyboard_style_prompt_appended():
    prompt = storyboard_apply("石板小路", "cinematic")
    assert "cinematic" in prompt
    assert "石板小路" in prompt
    assert storyboard_apply("测试", "") == "测试"
