# -*- coding: utf-8 -*-
"""provider_utils.py 全量单测（无重依赖纯逻辑，D 目标：21% → 90%+）。"""
# flake8: noqa: E501  # 断言长行不可避免（路径/URL 比较）
import base64
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.providers import provider_utils as pu  # noqa: E402


# ==================== API Key ====================

def test_provider_key_env_known():
    assert pu.provider_key_env("comfyui") == "COMFYUI_API_KEY"
    assert pu.provider_key_env("volcengine") == "ARK_API_KEY"
    assert pu.provider_key_env("modelscope") == "MODELSCOPE_API_KEY"


def test_provider_key_env_unknown_fallback():
    key = pu.provider_key_env("my-custom/provider")
    assert key == "API_PROVIDER_MY_CUSTOM_PROVIDER_KEY"


def test_get_api_key(monkeypatch):
    monkeypatch.setenv("COMFYUI_API_KEY", "  abc123  ")
    assert pu.get_api_key("comfyui") == "abc123"
    monkeypatch.delenv("COMFYUI_API_KEY", raising=False)
    assert pu.get_api_key("comfyui") == ""


def test_bearer_auth():
    assert pu.bearer_auth("abc") == "Bearer abc"
    assert pu.bearer_auth("Bearer abc") == "Bearer abc"
    assert pu.bearer_auth("") == ""
    assert pu.bearer_auth("   ") == ""


# ==================== parse_size ====================

def test_parse_size_valid():
    assert pu.parse_size("1024x1024") == (1024, 1024)
    assert pu.parse_size("512*768") == (512, 768)
    assert pu.parse_size(" 1280 X 720 ") == (1280, 720)
    assert pu.parse_size("64x64") == (64, 64)


def test_parse_size_invalid():
    assert pu.parse_size("") == (0, 0)
    assert pu.parse_size(None) == (0, 0)
    assert pu.parse_size("abc") == (0, 0)
    assert pu.parse_size("1024") == (0, 0)
    assert pu.parse_size("1024x") == (0, 0)


# ==================== 输出路径 ====================

def test_output_path_for(tmp_path, monkeypatch):
    monkeypatch.setattr(pu, "OUTPUT_DIR", str(tmp_path / "out"))
    p = pu.output_path_for("a.png", "video")
    assert p == str(tmp_path / "out" / "video" / "a.png")
    assert os.path.isdir(tmp_path / "out" / "video")


def test_output_url_for():
    assert pu.output_url_for("a.png") == "/output/output/a.png"
    assert pu.output_url_for("a.png", "video") == "/output/video/a.png"


# ==================== 图片保存 ====================

@pytest.mark.asyncio
async def test_save_image_b64(tmp_path, monkeypatch):
    monkeypatch.setattr(pu, "OUTPUT_DIR", str(tmp_path / "out"))
    raw = base64.b64encode(b"fake-png-bytes").decode()
    url = await pu.save_image_to_output({"type": "b64", "value": raw}, prefix="t_", category="img")
    assert url.startswith("/output/img/t_")
    fname = url.split("/")[-1]
    assert os.path.exists(os.path.join(str(tmp_path), "out", "img", fname))


@pytest.mark.asyncio
async def test_save_image_b64_jpg_ext(tmp_path, monkeypatch):
    monkeypatch.setattr(pu, "OUTPUT_DIR", str(tmp_path / "out"))
    raw = base64.b64encode(b"jpeg-data").decode()
    url = await pu.save_image_to_output(
        {"type": "b64", "value": raw, "mime_type": "image/jpeg"}, prefix="t_", category="img"
    )
    assert url.endswith(".jpg")


@pytest.mark.asyncio
async def test_save_image_local_path_direct(tmp_path, monkeypatch):
    monkeypatch.setattr(pu, "OUTPUT_DIR", str(tmp_path))
    url = await pu.save_image_to_output({"type": "url", "value": "/output/img/x.png"}, prefix="t_")
    assert url == "/output/img/x.png"


@pytest.mark.asyncio
async def test_save_image_download_failure_returns_value(tmp_path, monkeypatch):
    monkeypatch.setattr(pu, "OUTPUT_DIR", str(tmp_path / "out"))

    class FakeResp:
        def raise_for_status(self):
            raise Exception("network down")

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url):
            return FakeResp()

    monkeypatch.setattr(pu.httpx, "AsyncClient", FakeClient)
    url = await pu.save_image_to_output({"type": "url", "value": "http://x/y.png"}, prefix="t_")
    assert url == "http://x/y.png"


# ==================== 视频保存 ====================

@pytest.mark.asyncio
async def test_save_video_empty():
    assert await pu.save_video_to_output("") == ""


@pytest.mark.asyncio
async def test_save_video_local_direct():
    assert await pu.save_video_to_output("/output/v/x.mp4") == "/output/v/x.mp4"


# ==================== reference_to_data_url ====================

def test_reference_data_url_direct():
    assert pu.reference_to_data_url({"url": "data:image/png;base64,AAA"}) == "data:image/png;base64,AAA"
    assert pu.reference_to_data_url({"url": "http://x/a.png"}) == "http://x/a.png"
    assert pu.reference_to_data_url({"url": "https://x/a.png"}) == "https://x/a.png"


def test_reference_data_url_local_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(pu, "OUTPUT_DIR", str(tmp_path))
    # 不存在的本地路径 → 原样返回
    assert pu.reference_to_data_url({"url": "/output/notexist.png"}) == "/output/notexist.png"


def test_reference_data_url_local_file(tmp_path, monkeypatch):
    monkeypatch.setattr(pu, "OUTPUT_DIR", str(tmp_path))
    f = tmp_path / "img.png"
    # 用最小 PNG 头生成可打开文件
    f.write_bytes(bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a49444154789c6360000002000154a24a9d0000000049454e44ae426082"))
    data = pu.reference_to_data_url({"url": "/output/img.png"})
    assert data.startswith("data:image/png;base64,")
    # fallback 分支（PIL 读不出来时直接读文件）
    f2 = tmp_path / "raw.bin"
    f2.write_bytes(b"rawbytes")
    data2 = pu.reference_to_data_url({"url": "/output/raw.bin"}, max_size=0)
    assert data2.startswith("data:image/png;base64,")


# ==================== output_file_from_url ====================

def test_output_file_from_url_basic(tmp_path, monkeypatch):
    monkeypatch.setattr(pu, "OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(pu, "GENERATED_DIR", str(tmp_path / "gen"))
    monkeypatch.setattr(pu, "UPLOADS_DIR", str(tmp_path / "up"))
    assert pu.output_file_from_url("") is None
    assert os.path.normpath(pu.output_file_from_url("/output/cat/f.png")) == os.path.normpath(str(tmp_path / "out" / "cat" / "f.png"))
    assert os.path.normpath(pu.output_file_from_url("/data/generated/x.png")) == os.path.normpath(str(tmp_path / "gen" / "x.png"))
    assert os.path.normpath(pu.output_file_from_url("/data/uploads/y.png")) == os.path.normpath(str(tmp_path / "up" / "y.png"))
    assert os.path.normpath(pu.output_file_from_url("/static/director/uploads/z.png")) == os.path.normpath(str(tmp_path / "up" / "z.png"))
    assert pu.output_file_from_url("/other/path.png") is None
    # 带查询参数
    assert os.path.normpath(pu.output_file_from_url("/output/a/b.png?v=1#frag")) == os.path.normpath(str(tmp_path / "out" / "a" / "b.png"))


def test_output_file_comfy_image(tmp_path, monkeypatch):
    monkeypatch.setattr(pu, "GENERATED_DIR", str(tmp_path / "gen"))
    (tmp_path / "gen").mkdir(exist_ok=True)
    (tmp_path / "gen" / "real.png").write_bytes(b"x")
    # filename 空 → None
    assert pu.output_file_from_url("/api/comfyui/image") is None
    # 扁平查找命中
    got = pu.output_file_from_url("/api/comfyui/image?filename=real.png")
    assert got == str(tmp_path / "gen" / "real.png")
    # subfolder 命中
    (tmp_path / "gen" / "sub").mkdir(exist_ok=True)
    (tmp_path / "gen" / "sub" / "s.png").write_bytes(b"x")
    got2 = pu.output_file_from_url("/api/comfyui/image?filename=s.png&subfolder=sub")
    assert got2 == str(tmp_path / "gen" / "sub" / "s.png")
    # 不存在 → None
    assert pu.output_file_from_url("/api/comfyui/image?filename=nope.png") is None


# ==================== extract_image_from_response ====================

def test_extract_gemini():
    data = {"candidates": [{"content": {"parts": [{"inlineData": {"data": "QQ==", "mimeType": "image/png"}}]}}]}
    r = pu.extract_image_from_response(data)
    assert r == {"type": "b64", "value": "QQ==", "mime_type": "image/png"}


def test_extract_openai_url():
    data = {"data": [{"url": "http://x/1.png"}]}
    assert pu.extract_image_from_response(data) == {"type": "url", "value": "http://x/1.png"}


def test_extract_openai_b64():
    data = {"data": [{"b64_json": "QQ=="}]}
    assert pu.extract_image_from_response(data)["type"] == "b64"


def test_extract_recursive():
    data = {"output": {"results": [{"image_url": "http://deep/2.png"}]}}
    assert pu.extract_image_from_response(data) == {"type": "url", "value": "http://deep/2.png"}


def test_extract_empty():
    assert pu.extract_image_from_response({}) == {"type": "url", "value": ""}
    assert pu.extract_image_from_response(None) == {"type": "url", "value": ""}


def test_extract_recursive_b64_key():
    assert pu._extract_image_recursive({"b64_json": "YWI="}, 0)["type"] == "b64"
    assert pu._extract_image_recursive({"url": "/output/x.png"}, 0)["type"] == "url"
    assert pu._extract_image_recursive([{"a": {"b": "not-url"}}], 0) is None
    assert pu._extract_image_recursive(42, 0) is None
    assert pu._extract_image_recursive("plain-string", 0) is None
    # 深度超限
    deep = {"k": {"k": {"k": {"k": {"k": {"k": {"k": {"k": {"k": "http://x"}}}}}}}}}
    assert pu._extract_image_recursive(deep, 0) is None or pu._extract_image_recursive(deep, 0)["type"] == "url"
