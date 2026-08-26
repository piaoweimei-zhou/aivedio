# -*- coding: utf-8 -*-
"""CreativeOS adapter 测试：生成/转换/兜底。"""
import json
import urllib.request

from app.creative_adapter import generate_spec, spec_to_script
from app.orchestrator import build_script_from_topic


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self._data).encode("utf-8")


def test_generate_spec_success(monkeypatch):
    spec = {"script": {"hook": "钩子", "acts": [{"narration": "台词", "emotion": "惊喜"}]}}
    called = {}

    def fake_open(req, timeout=60):
        body = json.loads(req.data.decode("utf-8"))
        called.update(body)
        return _FakeResp(spec)

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    result = generate_spec("去水印", duration_s=5.0, segments=2)
    assert result == spec
    assert called["segments"] == 2
    assert called["topic"] == "去水印"


def test_generate_spec_failure_returns_none(monkeypatch):
    def boom(req, timeout=60):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert generate_spec("去水印") is None


def test_spec_to_script_maps_fields():
    spec = {
        "script": {
            "hook": "三秒去水印",
            "acts": [
                {"narration": "以前要半小时", "emotion": "共情", "duration": 3.0,
                 "visual": "手机屏幕"},
            ],
            "cta": "评论区扣工具",
        },
    }
    script = spec_to_script(spec, 5.0)
    assert script["type"] == "video_script_mixin"
    assert script["hook"] == "三秒去水印"
    assert script["acts"][0]["emotion"] == "共情"
    assert script["acts"][0]["duration"] == 3.0
    assert script["cta"] == "评论区扣工具"


def test_build_script_uses_creative(monkeypatch):
    topic = {"title": "去水印工具"}
    spec = {
        "script": {
            "hook": "钩子",
            "acts": [{"narration": "A", "emotion": "共情", "duration": 5.0, "visual": "v"}],
            "cta": "c",
        },
    }
    monkeypatch.setattr("app.creative_adapter.generate_spec", lambda *a, **kw: spec)
    script = build_script_from_topic(topic, duration_s=5.0, use_creative=True)
    assert script["type"] == "video_script_mixin"
    assert script["acts"][0]["narration"] == "A"


def test_build_script_fallback_when_creative_down(monkeypatch):
    topic = {"title": "去水印工具"}
    monkeypatch.setattr("app.creative_adapter.generate_spec", lambda **kw: None)
    script = build_script_from_topic(topic, duration_s=5.0, use_creative=True)
    # 兜底回 video_act 模板
    assert script["type"] == "video_act"
    assert "快看这里" in script["acts"][0]["narration"]


def test_build_script_fallback_when_creative_raises(monkeypatch):
    topic = {"title": "去水印工具"}

    def boom(**kw):
        raise RuntimeError("adapter broken")

    monkeypatch.setattr("app.creative_adapter.generate_spec", boom)
    script = build_script_from_topic(topic, use_creative=True)
    assert script["type"] == "video_act"


def test_build_script_disabled_creative():
    topic = {"title": "去水印工具"}
    script = build_script_from_topic(topic, use_creative=False)
    assert script["type"] == "video_act"
