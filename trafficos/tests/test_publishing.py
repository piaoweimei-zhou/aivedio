"""B6 发布包生成 + PublishJob 测试"""
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

_tmp = tempfile.mkdtemp(prefix="trafficos_test_b6_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402
from app.publishing import _hashtags, build_publish_package  # noqa: E402
from app.models import Dimension, Monetizer  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated():
    os.environ["TRAFFICOS_DATA_DIR"] = _tmp
    yield
    import shutil
    for d in ("covers", "publish_packages"):
        p = os.path.join(_tmp, d)
        if os.path.exists(p):
            shutil.rmtree(p)
    from app.storage import _store
    for key, col in list(_store.items()):
        if key[1] == _tmp:
            col.clear()


def _fake_video() -> str:
    """伪造一个视频文件（仅测试拷贝/清单）。"""
    p = os.path.join(_tmp, "fake_video.mp4")
    with open(p, "wb") as f:
        f.write(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 1024)
    return p


# ---------- 生成器纯逻辑 ----------

def test_hashtags():
    tags = _hashtags("douyin", Dimension.KNOWLEDGE, Monetizer.COURSE)
    assert "#知识分享" in tags
    assert "#副业" in tags
    assert len(tags) <= 8  # 平台+维度+变现合并后上限 8


def test_build_package_structure():
    r = build_publish_package(
        "零基础学剪辑",
        _fake_video(),
        dimension=Dimension.KNOWLEDGE,
        monetizer=Monetizer.COURSE,
    )
    assert r["package_id"].startswith("pkg_")
    # 文件齐全
    assert r["files"] == sorted(r["files"])
    for f in ("video.mp4", "cover.jpg", "title.txt", "caption.txt", "manifest.json"):
        assert f in r["files"], f"缺少 {f}"
    # 视频已拷贝
    assert os.path.exists(r["video_path"])
    assert os.path.getsize(r["video_path"]) > 1000
    # 标题/文案
    assert r["title"] == "零基础学会剪辑，别再走弯路" or "零基础" in r["title"]
    assert "零基础" in r["caption"]
    assert "#知识分享" in r["caption"]  # 话题进文案
    assert r["size_bytes"] > 0


def test_manifest_content():
    r = build_publish_package(
        "3秒解决去水印",
        _fake_video(),
        dimension=Dimension.SOFT_AD,
        monetizer=Monetizer.TOOL,
        account_id="acc_x",
        content_id="c_123",
    )
    import json
    with open(r["manifest_path"], encoding="utf-8") as f:
        m = json.load(f)
    assert m["dimension"] == "soft_ad"
    assert m["monetizer"] == "tool"
    assert m["account_id"] == "acc_x"
    assert m["content_id"] == "c_123"
    assert m["mode"] == "semi_auto"
    assert m["video"] == "video.mp4"
    assert os.path.exists(os.path.join(os.path.dirname(r["manifest_path"]), m["video"]))


def test_build_package_missing_video():
    with pytest.raises(FileNotFoundError):
        build_publish_package("标题", os.path.join(_tmp, "nonexistent.mp4"))


# ---------- API ----------

def test_generate_package_api():
    r = client.post("/api/traffic/publish/package", params={
        "title": "3秒解决去水印",
        "video_path": _fake_video(),
        "dimension": "soft_ad",
        "monetizer": "tool",
        "account_id": "acc_a",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["package_id"]
    assert os.path.exists(body["video_path"])
    assert "#去水印工具" in body["caption"]


def test_generate_package_api_missing_video():
    r = client.post("/api/traffic/publish/package", params={
        "title": "x", "video_path": os.path.join(_tmp, "nope.mp4"),
    })
    assert r.status_code == 400


def test_publish_job_lifecycle():
    # 创建 job
    r = client.post("/api/traffic/publish/jobs", json={
        "account_id": "acc_a", "topic_id": "t1", "content_id": "c1",
    })
    assert r.status_code == 200
    jid = r.json()["id"]
    assert r.json()["status"] == "pending"
    # 列表
    assert len(client.get("/api/traffic/publish/jobs").json()) == 1
    # 手动发布后标记
    r = client.put(f"/api/traffic/publish/jobs/{jid}/mark-published", params={"note": "已手动发布"})
    assert r.json()["status"] == "published"
    assert r.json()["result"]["mode"] == "semi_auto"
    # 状态过滤
    assert len(client.get("/api/traffic/publish/jobs", params={"status": "published"}).json()) == 1
    assert len(client.get("/api/traffic/publish/jobs", params={"status": "pending"}).json()) == 0
    # 通用状态流转
    r = client.put(f"/api/traffic/publish/jobs/{jid}/status", params={"status": "failed"})
    assert r.json()["status"] == "failed"
    # 404
    assert client.put("/api/traffic/publish/jobs/nope/mark-published").status_code == 404
