"""资产整理器（asset_organizer）单元测试"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.asset_organizer import (  # noqa: E402
    FALLBACK_PROJECT,
    build_asset_filename,
    build_asset_rel_path,
    next_seq,
    organize_asset_files,
    sanitize_keyword,
    short_hash,
)


class TestSanitizeKeyword:
    def test_normal(self):
        # 连字符在文件名中合法，保留
        assert sanitize_keyword("my-project") == "my-project"
        # 空白替换为下划线
        assert sanitize_keyword("my project") == "my_project"
        assert sanitize_keyword("角色") == "角色"

    def test_empty(self):
        assert sanitize_keyword("") == "asset"
        assert sanitize_keyword(None) == "asset"

    def test_invalid_chars(self):
        # 非法字符替换 + 12 字符截断
        assert sanitize_keyword("a/b\\c:d*e?f\"g<h>i|j") == "a_b_c_d_e_f_"

    def test_truncation(self):
        assert len(sanitize_keyword("x" * 100)) <= 12

    def test_unknown_fallback(self):
        # 修复 nknown 前缀 bug：unknown 兜底为 global
        assert sanitize_keyword("unknown", FALLBACK_PROJECT) == "global"


class TestBuildFilename:
    def test_basic(self):
        name = build_asset_filename("proj", "concept", "", 1, ".png", "abc123")
        assert name == "proj_concept_001_abc123.png"

    def test_with_content(self):
        name = build_asset_filename("proj", "concept", "character", 2, ".png", "abc123")
        assert name == "proj_concept_character_002_abc123.png"

    def test_unknown_project(self):
        name = build_asset_filename("unknown", "storyboard", "", 1, ".png", "abc123")
        assert name.startswith("global_storyboard_001_")

    def test_rel_path(self):
        rel = build_asset_rel_path("proj", "video", "", 3, ".mp4", "abc123")
        assert rel == "proj/video/proj_video_003_abc123.mp4"

    def test_short_hash_deterministic(self):
        assert short_hash("file.png") == short_hash("file.png")
        assert len(short_hash("file.png")) == 6


class TestNextSeq:
    def test_empty_dir(self, tmp_path):
        assert next_seq(str(tmp_path), "proj", "concept") == 1

    def test_existing_files(self, tmp_path):
        target = tmp_path / "proj" / "concept"
        target.mkdir(parents=True)
        (target / "proj_concept_005_aaaaaa.png").write_bytes(b"x")
        (target / "proj_concept_003_bbbbbb.png").write_bytes(b"x")
        assert next_seq(str(tmp_path), "proj", "concept") == 6

    def test_content_scoped(self, tmp_path):
        target = tmp_path / "proj" / "concept"
        target.mkdir(parents=True)
        (target / "proj_concept_character_002_aaaaaa.png").write_bytes(b"x")
        # 不同 content 关键词互不影响
        assert next_seq(str(tmp_path), "proj", "concept", "scene") == 1
        assert next_seq(str(tmp_path), "proj", "concept", "character") == 3


class TestOrganizeAssetFiles:
    def _make_flat(self, tmp_path, fname, content=b"data"):
        src = tmp_path / fname
        src.write_bytes(content)
        return src

    def test_copy_to_semantic_dir(self, tmp_path):
        src = self._make_flat(tmp_path, "ComfyUI_00001_.png")
        organized, skipped = organize_asset_files(
            ["ComfyUI_00001_.png"],
            project_id="proj",
            stage_id="concept",
            generated_dir=str(tmp_path),
            comfyui_output_dir="",
        )
        assert skipped == []
        assert len(organized) == 1
        assert "subfolder=proj/concept" in organized[0]
        # 源文件保留（复制模式）
        assert src.exists()
        # 目标文件存在
        target_dir = tmp_path / "proj" / "concept"
        assert target_dir.is_dir()
        assert len(list(target_dir.iterdir())) == 1

    def test_move_mode(self, tmp_path):
        src = self._make_flat(tmp_path, "ComfyUI_00001_.png")
        organized, skipped = organize_asset_files(
            ["ComfyUI_00001_.png"],
            project_id="proj",
            stage_id="concept",
            generated_dir=str(tmp_path),
            comfyui_output_dir="",
            move=True,
        )
        assert skipped == []
        assert len(organized) == 1
        assert not src.exists()  # 移动后源文件消失
        target_dir = tmp_path / "proj" / "concept"
        assert len(list(target_dir.iterdir())) == 1

    def test_remote_url_skipped(self, tmp_path):
        organized, skipped = organize_asset_files(
            ["https://example.com/remote.png"],
            project_id="proj",
            stage_id="concept",
            generated_dir=str(tmp_path),
            comfyui_output_dir="",
        )
        assert organized == []
        assert skipped == ["https://example.com/remote.png"]

    def test_missing_file_skipped(self, tmp_path):
        organized, skipped = organize_asset_files(
            ["not_exist.png"],
            project_id="proj",
            stage_id="concept",
            generated_dir=str(tmp_path),
            comfyui_output_dir="",
        )
        assert organized == []
        assert skipped == ["not_exist.png"]

    def test_sequential_naming(self, tmp_path):
        for i in range(3):
            self._make_flat(tmp_path, f"ComfyUI_{i:05d}_.png")
        organized, _ = organize_asset_files(
            [f"ComfyUI_{i:05d}_.png" for i in range(3)],
            project_id="proj",
            stage_id="concept",
            generated_dir=str(tmp_path),
            comfyui_output_dir="",
        )
        assert len(organized) == 3
        names = [o.split("filename=")[1].split("&")[0] for o in organized]
        assert names == sorted(names)
        # 序号递增
        seqs = [int(n.split("_")[2]) for n in names]
        assert seqs == [1, 2, 3]
