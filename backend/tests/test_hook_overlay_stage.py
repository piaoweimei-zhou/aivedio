"""结尾钩子引导框阶段单元测试"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.stages.hook_overlay_stage import HookOverlayStage


@pytest.fixture
def stage():
    return HookOverlayStage()


def test_position_y_bottom(stage):
    assert stage._position_y("bottom", 1920, 100) == "H-h-100"


def test_position_y_top(stage):
    assert stage._position_y("top", 1920, 50) == "50"


def test_position_y_center(stage):
    assert stage._position_y("center", 1920, 0) == "(H-h)/2"


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
