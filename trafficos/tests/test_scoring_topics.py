"""B3 选题打分器 + 选题库/信号/爆款 API 测试"""
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

_tmp = tempfile.mkdtemp(prefix="trafficos_test_b3_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402
from app.scoring import compute_score, normalize_weights, suggest_dimension_monetizer  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated():
    # 测试执行时设置 env（storage 每次动态解析 data_dir，实现文件级隔离）
    os.environ["TRAFFICOS_DATA_DIR"] = _tmp
    yield
    # 清空本文件数据目录下的全部集合（内存 + 文件），避免测试间残留
    from app.storage import _store
    for key, col in list(_store.items()):
        if key[1] == _tmp:
            col.clear()


# ---------- 打分器（纯函数）----------

def test_score_high_signal_high_convert():
    r = compute_score({"hot": 0.9, "competition": 0.2, "fit": 0.8,
                       "timeliness": 0.9, "convert_value": 0.9, "signal": 0.8})
    assert 0.0 <= r["score"] <= 1.0
    assert r["score"] > 0.7  # 全面高特征应高分
    assert set(r["breakdown"].keys()) == {"hot", "competition", "fit",
                                          "timeliness", "convert", "signal"}


def test_score_low_all():
    r = compute_score({"hot": 0.1, "competition": 0.9, "fit": 0.1,
                       "timeliness": 0.1, "convert_value": 0.1, "signal": 0.1})
    assert r["score"] < 0.35  # 全面低特征应低分


def test_score_missing_features_neutral():
    r = compute_score({})
    # 全缺失 → 全部 0.5，score = 0.5
    assert r["score"] == pytest.approx(0.5, abs=0.01)


def test_normalize_weights():
    w = normalize_weights({"hot": 100, "fit": 100})  # 非规范权重
    assert abs(sum(w.values()) - 1.0) < 1e-9       # 归一化为和 1
    assert w["hot"] == pytest.approx(w["fit"])     # 同权重 → 同占比


def test_suggest_dimension_monetizer():
    d, m = suggest_dimension_monetizer("XX去水印工具神器，批量下载").values()
    assert d == "soft_ad"
    assert m == "tool"
    d2, m2 = suggest_dimension_monetizer("零基础学会XX，进阶教程").values()
    assert d2 == "knowledge"
    assert m2 == "course"


# ---------- 选题库 API ----------

def test_topic_create_auto_score_and_tag():
    r = client.post("/api/traffic/topics", json={
        "title": "XX去水印工具神器，批量下载",
        "weights": {"hot": 0.9, "competition": 0.3, "fit": 0.8,
                    "timeliness": 0.8, "convert_value": 0.9, "signal": 0.7},
    })
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["id"].startswith("topic_")
    assert t["dimension"] == "soft_ad"       # 自动打标
    assert t["monetizer"] == "tool"           # 自动打标
    assert t["score"] > 0.6                    # 自动打分


def test_topic_list_sorted_by_score():
    client.post("/api/traffic/topics", json={
        "title": "低分选题", "status": "pending",
        "weights": {"hot": 0.1, "competition": 0.9, "fit": 0.1,
                    "timeliness": 0.1, "convert_value": 0.1, "signal": 0.1},
    })
    client.post("/api/traffic/topics", json={
        "title": "高分选题", "status": "pending",
        "weights": {"hot": 0.9, "competition": 0.1, "fit": 0.9,
                    "timeliness": 0.9, "convert_value": 0.9, "signal": 0.9},
    })
    r = client.get("/api/traffic/topics")
    topics = r.json()
    assert len(topics) == 2
    assert topics[0]["title"] == "高分选题"   # 默认按 score 降序


def test_topic_rescore_and_filter():
    r = client.post("/api/traffic/topics", json={
        "title": "测试选题", "weights": {"hot": 0.5, "fit": 0.5},
    })
    tid = r.json()["id"]
    # rescore
    r = client.post(f"/api/traffic/topics/{tid}/score", json={"hot": 0.95})
    assert r.json()["score"] > 0.6
    # filter by status
    r = client.get("/api/traffic/topics", params={"status": "used"})
    assert r.json() == []


def test_topic_meta_weights():
    r = client.get("/api/traffic/topics/meta/score-weights")
    assert r.status_code == 200
    assert "hot" in r.json()["weights"]


# ---------- 信号 API ----------

def test_signal_report_and_top_keywords():
    client.post("/api/traffic/signals", json={
        "field": "去水印", "keyword": "去水印工具", "heat": 5.0, "source": "tool",
    })
    client.post("/api/traffic/signals", json={
        "field": "去水印", "keyword": "去水印工具", "heat": 3.0, "source": "tool",
    })
    client.post("/api/traffic/signals", json={
        "field": "剪辑", "keyword": "批量剪辑", "heat": 2.0, "source": "tool",
    })
    r = client.get("/api/traffic/signals/top-keywords")
    body = r.json()
    top = body["top"]
    assert top[0]["keyword"] == "去水印工具"   # 聚合去重求和
    assert top[0]["heat"] == pytest.approx(8.0)
    assert body["total_keywords"] == 2


def test_signal_batch():
    r = client.post("/api/traffic/signals/batch", json=[
        {"field": "a", "keyword": "k1", "heat": 1.0},
        {"field": "a", "keyword": "k2", "heat": 2.0},
    ])
    assert r.json()["inserted"] == 2
    assert len(client.get("/api/traffic/signals").json()) == 2


# ---------- 爆款拆解库 API ----------

def test_hit_crud():
    r = client.post("/api/traffic/hits", json={
        "url": "https://example.com/video1", "title": "爆款A",
        "source": "manual", "raw_meta": {"views": 100000},
    })
    assert r.status_code == 200
    hid = r.json()["id"]
    r = client.get("/api/traffic/hits")
    assert len(r.json()) == 1
    assert client.delete(f"/api/traffic/hits/{hid}").status_code == 200
    assert client.get(f"/api/traffic/hits/{hid}").status_code == 404
