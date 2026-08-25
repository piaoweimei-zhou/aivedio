# -*- coding: utf-8 -*-
"""P2 大文件拆分后的纯逻辑单元补测

覆盖拆分新文件中可脱离 ComfyUI 服务的纯函数/静态方法，
确保 mixin 拆分的逻辑正确性有测试保护。
"""

from services.comfyui_generation import ComfyUIGenerationMixin
from services.comfyui_generation_vision import ComfyUIGenerationVisionMixin, _project_prefix
from services.comfyui_storyboard_batch import ComfyUIStoryboardBatchMixin


# ─────────────────────────── _project_prefix ───────────────────────────
class TestProjectPrefix:
    def test_normal_id(self):
        assert _project_prefix("abc123") == "proj_abc123"

    def test_empty_id_fallback(self):
        assert _project_prefix(None).startswith("proj_")
        assert _project_prefix("").startswith("proj_")

    def test_special_chars_sanitized(self):
        p = _project_prefix("a/b\\c:d e")
        assert "/" not in p and "\\" not in p and ":" not in p and " " not in p
        assert p.startswith("proj_")

    def test_long_id_truncated(self):
        p = _project_prefix("x" * 100)
        assert len(p) <= 30  # "proj_" + 24 + 边界
        assert p.endswith("x" * 24)

    def test_deterministic(self):
        assert _project_prefix("proj-1") == _project_prefix("proj-1")


# ─────────────────────── _strip_workflow_meta ─────────────────────────
class TestStripWorkflowMeta:
    def test_keeps_normal_nodes(self):
        wf = {
            "3": {"class_type": "KSampler", "inputs": {"seed": 1}},
            "5": {"class_type": "VAEDecode", "inputs": {}},
        }
        out = ComfyUIGenerationMixin._strip_workflow_meta(wf)
        assert set(out.keys()) == {"3", "5"}

    def test_strips_top_level_meta_keys(self):
        wf = {
            "_meta": {"title": "x"},
            "_comment": "note",
            "3": {"class_type": "KSampler", "inputs": {}},
        }
        out = ComfyUIGenerationMixin._strip_workflow_meta(wf)
        assert "_meta" not in out and "_comment" not in out
        assert "3" in out

    def test_keeps_underscore_node_with_class_type(self):
        wf = {"_custom": {"class_type": "SomeNode", "inputs": {}}}
        out = ComfyUIGenerationMixin._strip_workflow_meta(wf)
        assert "_custom" in out

    def test_strips_nested_meta_in_node(self):
        wf = {"3": {"class_type": "KSampler", "inputs": {"seed": 1}, "_meta": {"x": 1}}}
        out = ComfyUIGenerationMixin._strip_workflow_meta(wf)
        assert "_meta" not in out["3"]
        assert out["3"]["class_type"] == "KSampler"

    def test_keeps_non_dict_values(self):
        wf = {"3": "raw"}
        out = ComfyUIGenerationMixin._strip_workflow_meta(wf)
        assert out["3"] == "raw"


# ──────────────────── _get_intermediates_dir ──────────────────────────
class TestGetIntermediatesDir:
    def test_path_structure(self):
        obj = object.__new__(ComfyUIStoryboardBatchMixin)
        d = obj._get_intermediates_dir("project-12345678", "trace-abc")
        assert d.name == "trace-abc"
        assert d.parent.name == "project-12345678"[-8:]
        assert d.is_dir()

    def test_project_id_suffix_used(self):
        obj = object.__new__(ComfyUIStoryboardBatchMixin)
        d = obj._get_intermediates_dir("long-project-ABCDEFG", "t1")
        assert d.parent.name == "long-project-ABCDEFG"[-8:]


# ─────────────── _build_full_body_expansion_prompt ────────────────────
class TestBuildFullBodyExpansionPrompt:
    def test_no_source_desc(self):
        obj = object.__new__(ComfyUIGenerationVisionMixin)
        p = obj._build_full_body_expansion_prompt("")
        assert "全身" in p and "鞋子" in p

    def test_with_source_desc(self):
        obj = object.__new__(ComfyUIGenerationVisionMixin)
        p = obj._build_full_body_expansion_prompt("基于参考图的女侠，红衣")
        assert "风格" in p and "女侠" in p

    def test_desc_too_long_truncated(self):
        obj = object.__new__(ComfyUIGenerationVisionMixin)
        p = obj._build_full_body_expansion_prompt("x" * 200)
        # feature_hint 限制 60 字符
        assert len(p) < 120
