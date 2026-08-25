"""comfyui_storyboard 三视图检测裁剪逻辑单测（comfyui_storyboard 3% 覆盖提升）"""
import logging
import os

logging.disable(logging.CRITICAL)

from unittest.mock import patch  # noqa: E402

import services.comfyui_storyboard as sb_mod  # noqa: E402
from services.comfyui_storyboard import ComfyUIStoryboardMixin  # noqa: E402


def _make_inst(comfyui_dir="D:/fake/comfyui"):
    inst = object.__new__(ComfyUIStoryboardMixin)
    inst.config = type("Cfg", (), {"comfyui_dir": comfyui_dir})()
    return inst


def _call_detect(inst, all_ref_items, template, reference_images, trace_id="test", progress_callback=None):  # noqa: E501
    return inst._detect_and_crop_turnaround(
        all_ref_items, template, reference_images, trace_id, progress_callback
    )


class TestDetectAndCropTurnaround:
    def test_3view_template_skips_all(self):
        """3view 模板跳过裁剪，返回 0"""
        inst = _make_inst()
        items = [{"type": "character", "resolved": "a.png", "desc": "三视图"}]
        refs = {"character": "a.png"}
        with patch.object(sb_mod, "_crop_turnaround_to_front_view") as mock_crop:
            count = _call_detect(inst, items, "3view", refs)
        assert count == 0
        mock_crop.assert_not_called()

    def test_no_turnaround_no_crop(self):
        """非三视图（无 pattern、desc 非空非宽图）不裁剪"""
        inst = _make_inst()
        items = [{"type": "character", "resolved": "a.png", "desc": "正面半身像"}]
        refs = {"character": "a.png"}
        with patch.object(sb_mod, "_crop_turnaround_to_front_view") as mock_crop:
            count = _call_detect(inst, items, "default", refs)
        assert count == 0
        mock_crop.assert_not_called()

    def test_desc_pattern_triggers_crop_and_updates_refs(self):
        """desc 含三视图 pattern → 裁剪，更新 reference_images 和 item.resolved"""
        inst = _make_inst()
        items = [{"type": "character", "resolved": "a.png", "desc": "人物三视图展示"}]
        refs = {"character": "a.png"}
        with patch.object(sb_mod, "_crop_turnaround_to_front_view", return_value="a_cropped.png") as mock_crop:  # noqa: E501
            count = _call_detect(inst, items, "default", refs)
        assert count == 1
        mock_crop.assert_called_once_with(os.path.join(inst.config.comfyui_dir, "input"), "a.png", "test")  # noqa: E501
        assert refs["character"] == "a_cropped.png"
        assert items[0]["resolved"] == "a_cropped.png"

    def test_multiple_turnaround_items(self):
        """多个三视图 item 均裁剪并计数"""
        inst = _make_inst()
        items = [
            {"type": "character", "resolved": "a.png", "desc": "三视图"},
            {"type": "scene", "resolved": "b.png", "desc": "普通场景"},
            {"type": "multi_view", "resolved": "c.png", "desc": "多角度视图"},
        ]
        refs = {"character": "a.png", "scene": "b.png", "multi_view": "c.png"}
        with patch.object(sb_mod, "_crop_turnaround_to_front_view", side_effect=["a_c.png", "c_c.png"]) as mock_crop:  # noqa: E501
            count = _call_detect(inst, items, "default", refs)
        assert count == 2
        assert refs["character"] == "a_c.png"
        assert refs["multi_view"] == "c_c.png"
        assert refs["scene"] == "b.png"
        assert mock_crop.call_count == 2
