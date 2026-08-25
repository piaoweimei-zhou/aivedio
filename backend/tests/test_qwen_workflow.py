"""qwen_workflow 纯逻辑单元测试：安全格式化、5段式提示词、结构化转换"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.qwen_workflow import (  # noqa: E402
    safe_format,
    format_qwen_prompt,
    structured_prompt_to_comfyui_prompt,
)


class TestSafeFormat:
    def test_format_map(self):
        assert safe_format("Hello {name}", name="Test") == "Hello Test"

    def test_missing_var_fallback(self):
        # 未知变量触发 format_map KeyError → string.Template 回退，$unknown 保留
        out = safe_format("Hello {name}, age {unknown}", name="T")
        assert "Hello T" in out

    def test_empty_template(self):
        assert safe_format("") == ""

    def test_none_value_to_empty(self):
        assert safe_format("a={x}", x=None) == "a="

    def test_no_braces_passthrough(self):
        # 无 {var} 时 format_map 直接返回原样
        assert safe_format("Hello $name", name="T") == "Hello $name"


class TestFormatQwenPrompt:
    def test_full(self):
        out = format_qwen_prompt(keep="k", change="c", maintain="m", avoid="a", fallback="f")
        assert "[KEEP]\nk" in out
        assert "[CHANGE]\nc" in out
        assert "[MAINTAIN]\nm" in out
        assert "[AVOID]\na" in out
        assert "[FALLBACK]\nf" in out

    def test_partial(self):
        out = format_qwen_prompt(keep="k", change="c")
        assert "[KEEP]" in out and "[CHANGE]" in out
        assert "[MAINTAIN]" not in out

    def test_empty(self):
        assert format_qwen_prompt() == ""

    def test_image_prefix(self):
        out = format_qwen_prompt(keep="k", image_prefix="Image A")
        assert out.startswith("[Image A]\n\n[KEEP]")


class TestStructuredPromptToComfyui:
    def test_custom_text_wins(self):
        out = structured_prompt_to_comfyui_prompt({"keep": "k"}, custom_text="自定义")
        assert out == "自定义"

    def test_from_data(self):
        out = structured_prompt_to_comfyui_prompt({"keep": "k", "change": "c"})
        assert "[KEEP]\nk" in out and "[CHANGE]\nc" in out

    def test_empty_data(self):
        assert structured_prompt_to_comfyui_prompt({}) == ""
