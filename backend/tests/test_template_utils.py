"""template_utils 纯逻辑单元测试：template_id 校验、文件名前缀、资产类型匹配"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.template_utils import (  # noqa: E402
    validate_template_id,
    safe_filename_prefix,
    match_asset_type_by_filename,
    DEFAULT_TYPE_MAP,
)


# ── validate_template_id ──────────────────────────────────────
class TestValidateTemplateId:
    def test_valid(self):
        assert validate_template_id("T01_双人正面对话") is True
        assert validate_template_id("T99_abc_123") is True

    def test_empty(self):
        assert validate_template_id("") is False
        assert validate_template_id(None) is False

    def test_path_traversal(self):
        for bad in ["..", "../evil", "a/b", "a\\b", "a:b", "a;b", "a|b", "a<b", "a>b", "a?b", "a*b", 'a"b']:  # noqa: E501
            assert validate_template_id(bad) is False, bad

    def test_too_long(self):
        assert validate_template_id("T" + "a" * 100) is False


# ── safe_filename_prefix ──────────────────────────────────────
class TestSafeFilenamePrefix:
    def test_chinese_name(self):
        # 中文替换为下划线后压缩，只剩编号
        assert safe_filename_prefix("T01_双人正面对话") == "T01"

    def test_ascii_name(self):
        # 保留编号 + 英文段
        assert safe_filename_prefix("T02_depth") == "T02_depth"
        assert safe_filename_prefix("T03_pose") == "T03_pose"

    def test_no_number(self):
        assert safe_filename_prefix("双人正面对话") == "TPL"


# ── match_asset_type_by_filename ──────────────────────────────
class TestMatchAssetTypeByFilename:
    def test_match_by_prefix(self):
        assert match_asset_type_by_filename("T01_pose.png") == ("pose", "姿态")
        assert match_asset_type_by_filename("T01_depth.png") == ("depth", "深度图")
        assert match_asset_type_by_filename("T01_lineart.png") == ("lineart", "线稿")

    def test_match_by_separator(self):
        assert match_asset_type_by_filename("something_T01_pose.png") == ("pose", "姿态")

    def test_skip_keywords(self):
        assert match_asset_type_by_filename("T01_depth_clean.png", skip_keywords=["depth_clean"]) is None  # noqa: E501

    def test_no_match(self):
        assert match_asset_type_by_filename("random_file.png") is None
        assert match_asset_type_by_filename("") is None

    def test_custom_map(self):
        cmap = {"custom": ("custom_type", "自定义")}
        assert match_asset_type_by_filename("x_custom_y.png", type_map=cmap) == ("custom_type", "自定义")  # noqa: E501
        assert match_asset_type_by_filename("x_pose_y.png", type_map=cmap) is None

    def test_default_map_has_expected_keys(self):
        assert set(DEFAULT_TYPE_MAP.keys()) == {"lineart", "depth_raw", "depth", "pose"}
