"""provider_utils 纯逻辑单元测试：API Key、Bearer、尺寸解析、输出路径/URL"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.providers.provider_utils import (  # noqa: E402
    provider_key_env,
    get_api_key,
    bearer_auth,
    parse_size,
    output_path_for,
    output_url_for,
)


class TestProviderKeyEnv:
    def test_known_provider(self):
        assert provider_key_env("comfyui") == "COMFYUI_API_KEY"
        assert provider_key_env("openai_compat") == "OPENAI_API_KEY"
        assert provider_key_env("volcengine") == "ARK_API_KEY"

    def test_unknown_provider(self):
        assert provider_key_env("my_provider") == "API_PROVIDER_MY_PROVIDER_KEY"


class TestGetApiKey:
    def test_read_env(self, monkeypatch):
        monkeypatch.setenv("COMFYUI_API_KEY", "  secret-key  ")
        assert get_api_key("comfyui") == "secret-key"

    def test_missing_env(self, monkeypatch):
        monkeypatch.delenv("COMFYUI_API_KEY", raising=False)
        assert get_api_key("comfyui") == ""


class TestBearerAuth:
    def test_plain_key(self):
        assert bearer_auth("abc123") == "Bearer abc123"

    def test_already_bearer(self):
        assert bearer_auth("Bearer abc123") == "Bearer abc123"

    def test_whitespace(self):
        assert bearer_auth("  abc123  ") == "Bearer abc123"

    def test_empty(self):
        assert bearer_auth("") == ""
        assert bearer_auth("   ") == ""


class TestParseSize:
    def test_normal(self):
        assert parse_size("1024x1024") == (1024, 1024)
        assert parse_size("720X480") == (720, 480)
        assert parse_size("640*360") == (640, 360)
        assert parse_size(" 800 x 600 ") == (800, 600)

    def test_invalid(self):
        assert parse_size("abc") == (0, 0)
        assert parse_size("") == (0, 0)
        assert parse_size(None) == (0, 0)


class TestOutputPathUrl:
    def test_output_path_for(self, tmp_path, monkeypatch):
        import services.providers.provider_utils as pu
        monkeypatch.setattr(pu, "OUTPUT_DIR", str(tmp_path))
        p = output_path_for("a.png")
        assert p == os.path.join(str(tmp_path), "output", "a.png")
        assert os.path.isdir(os.path.join(str(tmp_path), "output"))

    def test_output_path_for_category(self, tmp_path, monkeypatch):
        import services.providers.provider_utils as pu
        monkeypatch.setattr(pu, "OUTPUT_DIR", str(tmp_path))
        p = output_path_for("b.mp4", "temp")
        assert p == os.path.join(str(tmp_path), "temp", "b.mp4")

    def test_output_url_for(self):
        assert output_url_for("a.png") == "/output/output/a.png"
        assert output_url_for("a.png", "temp") == "/output/temp/a.png"
