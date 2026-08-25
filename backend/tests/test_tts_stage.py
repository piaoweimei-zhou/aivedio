"""TTS 音频生成阶段单元测试"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.provider_service import ProviderResult  # noqa: E402
from services.stages.tts_stage import TtsStage  # noqa: E402


@pytest.fixture
def stage():
    return TtsStage()


def make_fake_comfyui(result):
    class Fake:
        async def generate_tts_audio(self, **kw):
            return result

    return Fake()


class FakeAssetSvc:
    def __init__(self):
        self.created = []

    async def create(self, **kw):
        self.created.append(kw)
        return {"asset_id": "asset-1", **kw}


def patch_env(monkeypatch, fake_comfyui, asset_svc=None):
    svc = asset_svc or FakeAssetSvc()
    monkeypatch.setattr("services.comfyui_service.get_comfyui_service", lambda: fake_comfyui)
    monkeypatch.setattr("services.stage_service.get_asset_service", lambda: svc)
    monkeypatch.setattr("services.stage_service.get_provider_service", lambda: None)
    return svc


def fake_result():
    url = "http://127.0.0.1:8188/view?filename=tts1.wav&type=output"
    return ProviderResult(
        image_url=url,
        images=[url],
        elapsed_ms=1200,
        provider_id="comfyui",
        prompt_id="prompt-abc",
    )


async def test_tts_execute_success(stage, monkeypatch):
    svc = patch_env(monkeypatch, make_fake_comfyui(fake_result()))
    res = await stage.execute([], "comfyui", {"text": "你好世界", "mode": "voice_design"})
    assert res.success
    assert res.elapsed_ms == 1200
    assert svc.created, "应注册新音频资产"
    created = svc.created[0]
    assert created["asset_type"] == "audio"
    assert created["metadata"]["mode"] == "voice_design"
    assert created["metadata"]["text"] == "你好世界"
    assert created["metadata"]["language"] == "Auto"


async def test_tts_requires_text(stage, monkeypatch):
    svc = patch_env(monkeypatch, make_fake_comfyui(fake_result()))
    res = await stage.execute([], "comfyui", {"mode": "voice_design"})
    assert not res.success
    assert "文本不能为空" in res.error
    assert not svc.created


async def test_tts_forwards_ref_audio(stage, monkeypatch):
    captured = {}

    class Fake:
        async def generate_tts_audio(self, **kw):
            captured.update(kw)
            return fake_result()

    patch_env(monkeypatch, Fake())
    res = await stage.execute(
        [],
        "comfyui",
        {
            "text": "克隆测试",
            "mode": "voice_clone",
            "ref_audio_url": "http://x/ref.wav",
            "ref_text": "参考文本",
        },
    )
    assert captured["mode"] == "voice_clone"
    assert captured["ref_audio_url"] == "http://x/ref.wav"
    assert captured["ref_text"] == "参考文本"
    assert captured["asset_tag"] == "tts_standalone"
    assert res.success


async def test_tts_exception_returns_error(stage, monkeypatch):
    class Fake:
        async def generate_tts_audio(self, **kw):
            raise RuntimeError("boom-tts")

    patch_env(monkeypatch, Fake())
    res = await stage.execute([], "comfyui", {"text": "x"})
    assert not res.success
    assert "boom-tts" in res.error


def test_tts_stage_meta(stage):
    assert stage.stage_def.stage_id == "tts"
    assert stage.stage_def.output_type == "audio"
    assert stage.stage_def.default_provider == "comfyui"
