"""火山引擎 provider 单元测试：锁定 Seedance/Seedream 端点与响应解析"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.providers.volcengine_provider import VolcEngineProvider


@pytest.fixture
def provider():
    os.environ["ARK_API_KEY"] = "test-ark-key"
    yield VolcEngineProvider()
    os.environ.pop("ARK_API_KEY", None)


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs.get("json")))
        return FakeResponse(self._responses.pop(0))

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, None))
        return FakeResponse(self._responses.pop(0))


def test_capabilities_include_text(provider):
    assert "text" in provider.capabilities


def test_default_models(provider):
    assert provider._get_image_model() == "doubao-seedream-4-5"
    assert provider._get_video_model() == "doubao-seedance-1-5-pro"
    assert provider._get_text_model() == "doubao-seed-2-0-pro"


@pytest.mark.parametrize("input_size,expected", [
    ("1024x1024", "2048x2048"),   # 1:1
    ("768x1024", "1728x2304"),    # 3:4
    ("1280x720", "2848x1600"),    # 16:9
    ("720x1280", "1600x2848"),    # 9:16
    ("1024x768", "2304x1728"),    # 4:3
    ("", "2048x2048"),            # 非法回退
])
def test_normalize_size_meets_seedream_minimum(provider, input_size, expected):
    """Seedream 4.x 要求总像素 ≥ 3686400，所有映射尺寸必须满足"""
    result = provider._normalize_size(input_size)
    assert result == expected
    w, h = map(int, result.split("x"))
    assert w * h >= 3686400


def test_generate_text_uses_chat_completions(provider):
    client = FakeClient([
        {"choices": [{"message": {"content": "剧本内容"}}], "id": "chat-1"},
    ])
    with patch("services.providers.volcengine_provider.httpx.AsyncClient", return_value=client):
        result = asyncio.run(provider.generate_text("写个剧本", model="ep-test"))
    assert result.metadata["text"] == "剧本内容"
    method, url, body = client.calls[0]
    assert method == "POST"
    assert url.endswith("/api/v3/chat/completions")
    assert body["model"] == "ep-test"


def test_generate_video_uses_tasks_endpoint_and_content_video_url(provider):
    """Seedance 端点必须带 /tasks/，URL 从 content.video_url 提取"""
    client = FakeClient([
        {"id": "cgt-123", "status": "queued"},          # 创建
        {"id": "cgt-123", "status": "succeeded",
         "content": {"video_url": "https://ark.example.com/v.mp4"}},  # 轮询
    ])
    with patch("services.providers.volcengine_provider.httpx.AsyncClient", return_value=client), \
         patch("services.providers.volcengine_provider.save_video_to_output",
               new=AsyncMock(return_value="/output/video/volc_vid.mp4")):
        result = asyncio.run(provider.generate_video("猫咪奔跑", duration=5, aspect_ratio="16:9"))

    assert result.status == "succeeded"
    assert result.video_url == "/output/video/volc_vid.mp4"
    create_method, create_url, create_body = client.calls[0]
    assert create_url.endswith("/api/v3/contents/generations/tasks")
    assert create_body["duration"] == 5
    assert create_body["ratio"] == "16:9"
    query_method, query_url, _ = client.calls[1]
    assert query_url.endswith("/api/v3/contents/generations/tasks/cgt-123")


def test_generate_video_failed_status_raises(provider):
    client = FakeClient([
        {"id": "cgt-456", "status": "queued"},
        {"id": "cgt-456", "status": "failed", "error": {"message": "余额不足"}},
    ])
    with patch("services.providers.volcengine_provider.httpx.AsyncClient", return_value=client):
        with pytest.raises(ValueError, match="余额不足"):
            asyncio.run(provider.generate_video("测试"))
