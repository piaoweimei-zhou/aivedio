"""结尾钩子引导框阶段单元测试"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.stages.hook_overlay_stage import HookOverlayStage  # noqa: E402


@pytest.fixture
def stage():
    return HookOverlayStage()


def test_position_y_bottom(stage):
    assert stage._position_y("bottom", 1920, 100) == "H-h-100"


def test_position_y_top(stage):
    assert stage._position_y("top", 1920, 50) == "50"


def test_position_y_center(stage):
    assert stage._position_y("center", 1920, 0) == "(H-h)/2"


def test_resolve_margin_default_10pct(stage):
    # 未显式传 margin 时，默认按高度 10% 留白，避免贴底
    assert stage._resolve_margin({}, 1920) == 192
    assert stage._resolve_margin({}, 864) == 86


def test_resolve_margin_explicit_wins(stage):
    # 用户显式传 margin 时优先使用用户值
    assert stage._resolve_margin({"margin": 0}, 1920) == 0
    assert stage._resolve_margin({"margin": 50}, 1080) == 50


def test_resolve_margin_minimum_one(stage):
    # 极端小高度也至少保留 1px，避免归零贴底
    assert stage._resolve_margin({}, 5) == 1


def test_generate_hook_image_creates_png(stage):
    from PIL import Image

    path = stage._generate_hook_image(1080, "评论区扣1领工具", "私信领取完整工具包")
    assert os.path.exists(path)
    img = Image.open(path)
    assert img.mode == "RGBA"
    # 宽度 = 1080 * 0.9
    assert img.size[0] == 972
    img.close()
    os.remove(path)


def test_generate_hook_image_no_sub_text(stage):
    from PIL import Image

    path = stage._generate_hook_image(720, "扣1", "")
    assert os.path.exists(path)
    img = Image.open(path)
    assert img.size[0] == 648
    img.close()
    os.remove(path)


def test_build_overlay_xy_static(stage):
    # 动画关闭：x 居中 + y 固定到最终位，无时钟项
    expr = stage._build_overlay_xy("H-h-86", 26.0, False)
    assert expr == "x=(W-w)/2:y=H-h-86"
    assert "sin(" not in expr
    assert "exp(" not in expr


def test_build_overlay_xy_animate(stage):
    # 动画开启：含 (t-{start}) 阻尼振荡时钟，指数衰减 + 正弦摆动 + enable 时间窗
    expr = stage._build_overlay_xy("H-h-86", 26.0, True)
    assert expr.startswith("x=(W-w)/2:y=")
    assert "H-h-86" in expr
    assert "exp(-1.6*" in expr
    assert "sin((t-26.0)*13)" in expr
    # 含 enable 时间窗（enable 前 overlay 不 eval 坐标，故无需 if() 兜底）
    assert "enable=gte(t\\,26.00)" in expr
    # 不叠加 if()（叠加会导致 filter 解析器含逗号表达式 + enable 时误判）
    assert "if(" not in expr


def test_build_overlay_xy_t_start_zero(stage):
    # start=0 时弹跳时钟从 0 开始，衰减振荡仍正确
    expr = stage._build_overlay_xy("H-h-86", 0.0, True)
    assert "sin((t-0.0)*13)" in expr
