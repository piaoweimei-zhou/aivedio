"""B4 包装生成器 + 包装 API 测试"""
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

_tmp = tempfile.mkdtemp(prefix="trafficos_test_b4_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402
from app.packaging import _shorten_title, generate_packaging  # noqa: E402
from app.models import Dimension, Monetizer  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated():
    os.environ["TRAFFICOS_DATA_DIR"] = _tmp
    yield
    from app.storage import _store
    for key, col in list(_store.items()):
        if key[1] == _tmp:
            col.clear()


# ---------- 生成器纯逻辑 ----------

def test_generate_soft_ad_tool():
    """软广×工具：标题含'工具/亲测/神器'语义，钩子含'3 秒'等。"""
    r = generate_packaging("短视频批量去水印工具", Dimension.SOFT_AD, Monetizer.TOOL)
    assert r["applied_templates"] == "default"
    assert len(r["titles"]) >= 3
    assert len(r["hooks"]) >= 1
    assert r["cover_style"]
    # 标题填入了主题
    assert any("去水印" in t for t in r["titles"])
    # 钩子非空
    assert all(h for h in r["hooks"])


def test_generate_knowledge_course():
    r = generate_packaging("零基础学剪辑", Dimension.KNOWLEDGE, Monetizer.COURSE)
    assert r["applied_templates"] == "default"
    assert any("避坑" in t or "零基础" in t for t in r["titles"])


def test_generate_unknown_combo_generic():
    """未配置组合 → generic 兜底，但仍可用。"""
    r = generate_packaging("任意选题", Dimension.PURE_CONTENT, Monetizer.COURSE)
    assert r["applied_templates"] == "generic"
    assert r["titles"]


def test_shorten_title():
    assert _shorten_title("短标题") == "短标题"
    long_t = "这是一个非常非常非常长的标题内容测试"
    out = _shorten_title(long_t, max_len=8)
    assert len(out) <= 8
    assert out.endswith("…")


def test_custom_template_overrides_default():
    """用户配置模板优先于内置默认。"""
    client.post("/api/traffic/packaging/templates", json={
        "dimension": "soft_ad", "monetizer": "tool",
        "title_templates": ["自定义标题 {topic} AAA"],
        "hook_templates": ["自定义钩子 {topic}"],
        "cover_style": "自定义封面",
    })
    r = client.post("/api/traffic/packaging/generate", params={
        "title": "去水印工具", "dimension": "soft_ad", "monetizer": "tool",
    })
    body = r.json()
    assert body["applied_templates"] == "custom"
    assert "自定义标题" in body["titles"][0]
    assert body["cover_style"] == "自定义封面"


# ---------- API ----------

def test_generate_api():
    r = client.post("/api/traffic/packaging/generate", params={
        "title": "短视频去水印神器",
        "dimension": "soft_ad",
        "monetizer": "tool",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["titles"]
    assert body["hooks"]
    assert body["cover_style"]


def test_template_crud():
    r = client.post("/api/traffic/packaging/templates", json={
        "dimension": "knowledge", "monetizer": "resource",
        "title_templates": ["{topic} 全套资料"], "hook_templates": ["转存就送"],
        "cover_style": "资源风格",
    })
    assert r.status_code == 200
    tid = r.json()["id"]
    assert len(client.get("/api/traffic/packaging/templates").json()) == 1
    r = client.put(f"/api/traffic/packaging/templates/{tid}", json={
        "dimension": "knowledge", "monetizer": "resource",
        "title_templates": ["{topic} 更新版资料"], "hook_templates": ["转存就送"],
        "cover_style": "资源风格",
    })
    assert "更新版" in r.json()["title_templates"][0]
    assert client.delete(f"/api/traffic/packaging/templates/{tid}").status_code == 200
    assert client.get("/api/traffic/packaging/templates").json() == []


def test_generate_invalid_enum():
    r = client.post("/api/traffic/packaging/generate", params={
        "title": "x", "dimension": "bad", "monetizer": "tool",
    })
    assert r.status_code == 422
