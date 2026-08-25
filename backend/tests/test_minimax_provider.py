"""MiniMax H3 供应商单元测试（不依赖 ComfyUI/GPU）"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.provider_service import get_provider_service, reset_provider_service  # noqa: E402
from services.providers.minimax_provider import MinimaxProvider  # noqa: E402


def test_provider_registered_with_video_capability():
    reset_provider_service()
    try:
        svc = get_provider_service()
        p = svc.get_provider("minimax_h3")
        assert p is not None, "minimax_h3 应已注册"
        assert isinstance(p, MinimaxProvider)
        assert "video" in p.capabilities
        assert p.is_available()
    finally:
        reset_provider_service()


def test_available_providers_contains_minimax():
    reset_provider_service()
    try:
        svc = get_provider_service()
        ids = [p["id"] for p in svc.available_providers("video")]
        assert "minimax_h3" in ids
    finally:
        reset_provider_service()


import asyncio  # noqa: E402
import unittest.mock as mock  # noqa: E402

from services.provider_service import ProviderResult  # noqa: E402


def test_generate_video_routes_to_service():
    provider = MinimaxProvider()
    captured = {}

    class _FakeResult:
        image_url = "http://127.0.0.1:8188/view?filename=mmh3_x.mp4"
        prompt_id = "pid-123"
        seed = 7
        images = [image_url]
        filenames = ["mmh3_x.mp4"]

    class _FakeSvc:
        async def generate_minimax_h3(self, **kw):
            captured.update(kw)
            return _FakeResult()

    with mock.patch("services.comfyui_service.get_comfyui_service", return_value=_FakeSvc()):
        res: ProviderResult = asyncio.run(
            provider.generate_video(
                prompt="夕阳下的湖面",
                duration=5.0,
                aspect_ratio="9:16",
                seed=7,
            )
        )

    assert captured["prompt"] == "夕阳下的湖面"
    assert captured["width"] == 480
    assert captured["height"] == 864
    assert captured["duration_seconds"] == 5.0
    assert captured["audio_mode"] == "native"
    assert captured["seed"] == 7
    assert res.provider_id == "minimax_h3"
    assert res.video_url.endswith("mmh3_x.mp4")
    assert res.prompt_id == "pid-123"


def test_comfyui_mixin_present():
    from services.comfyui_service import ComfyUIService

    # 组合类应具备 MiniMax 方法（避免运行时 missing attribute）
    assert hasattr(ComfyUIService, "generate_minimax_h3")
