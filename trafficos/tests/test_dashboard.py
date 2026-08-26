"""B7 数据分析核心 + metrics 回传 + dashboard 看板测试"""
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

_tmp = tempfile.mkdtemp(prefix="trafficos_test_b7_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("TRAFFICOS_COST_PER_CONTENT", "1.0")

from app.main import app  # noqa: E402
from app.analytics import aggregate, group_by, sum_conversions  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated():
    os.environ["TRAFFICOS_DATA_DIR"] = _tmp
    yield
    from app.storage import _store
    for key, col in list(_store.items()):
        if key[1] == _tmp:
            col.clear()


# 测试数据：3 条（知识/工具 × 2 账号）
def _seed():
    rows = [
        {"content_id": "c1", "account_id": "acc_a", "dimension": "knowledge",
         "monetizer": "course", "views": 1000, "likes": 80, "comments": 10,
         "shares": 5, "follows": 20, "revenue": 200.0,
         "conversions": {"course_signup": 2, "course_pay": 1}},
        {"content_id": "c2", "account_id": "acc_a", "dimension": "soft_ad",
         "monetizer": "tool", "views": 500, "likes": 40, "comments": 3,
         "shares": 2, "follows": 5, "revenue": 80.0,
         "conversions": {"download": 8, "activate": 2}},
        {"content_id": "c3", "account_id": "acc_b", "dimension": "knowledge",
         "monetizer": "resource", "views": 2000, "likes": 150, "comments": 30,
         "shares": 20, "follows": 60, "revenue": 50.0,
         "conversions": {"resource_claim": 12}},
    ]
    for r in rows:
        client.post("/api/traffic/metrics", json=r)
    return rows


# ---------- analytics 纯逻辑 ----------

def test_sum_conversions():
    assert sum_conversions({"a": 2, "b": 3}) == 5
    assert sum_conversions({"a": "x"}) == 0
    assert sum_conversions(None) == 0


def test_aggregate_basic():
    a = aggregate([
        {"views": 100, "revenue": 50.0, "conversions": {"d": 2}},
        {"views": 300, "revenue": 0.0, "conversions": {}},
    ])
    assert a["contents"] == 2
    assert a["views"] == 400
    assert a["revenue"] == 50.0
    assert a["conversion_total"] == 2
    assert a["cost"] == 2.0          # cost_per_content=1.0 × 2
    assert a["roi"] == 25.0          # 50 / 2
    assert a["engagement_rate"] == 0.0  # 无互动


def test_aggregate_engagement_rate():
    a = aggregate([{"views": 100, "likes": 10, "comments": 5, "shares": 5}])
    assert a["engagement_rate"] == 0.2


def test_group_by_sorted():
    _seed()
    recs = client.get("/api/traffic/metrics").json()
    dims = group_by(recs, "dimension")
    assert [d["group"] for d in dims] == ["knowledge", "soft_ad"]  # revenue 降序
    knowledge = dims[0]
    assert knowledge["views"] == 3000
    assert knowledge["revenue"] == 250.0
    assert knowledge["roi"] == 125.0  # 250 / 2 条


# ---------- metrics API ----------

def test_report_and_list():
    _seed()
    recs = client.get("/api/traffic/metrics").json()
    assert len(recs) == 3
    # 过滤
    assert len(client.get("/api/traffic/metrics", params={"dimension": "knowledge"}).json()) == 2
    assert len(client.get("/api/traffic/metrics", params={"account_id": "acc_a"}).json()) == 2
    assert len(client.get("/api/traffic/metrics", params={"content_id": "c1"}).json()) == 1


def test_batch_and_delete():
    _seed()
    r = client.post("/api/traffic/metrics/batch", json=[
        {"content_id": "b1", "views": 10},
        {"content_id": "b2", "views": 20},
    ])
    assert r.json() == {"inserted": 2}
    recs = client.get("/api/traffic/metrics").json()
    assert len(recs) == 5
    # delete
    tid = recs[0]["id"]
    assert client.delete(f"/api/traffic/metrics/{tid}").json()["success"] is True
    assert len(client.get("/api/traffic/metrics").json()) == 4


def test_get_metric_404():
    assert client.get("/api/traffic/metrics/nonexistent").status_code == 404


# ---------- dashboard ----------

def test_overview():
    _seed()
    ov = client.get("/api/traffic/dashboard/overview").json()
    assert ov["overall"]["views"] == 3500
    assert ov["overall"]["revenue"] == 330.0
    dims = {d["dimension"]: d for d in ov["dimensions"]}
    assert dims["knowledge"]["revenue_share"] == pytest.approx(250 / 330, abs=1e-3)


def test_by_dimension_and_account():
    _seed()
    dims = client.get("/api/traffic/dashboard/by-dimension").json()
    assert dims[0]["group"] == "knowledge"
    accs = client.get("/api/traffic/dashboard/by-account").json()
    assert accs[0]["group"] == "acc_a"  # 280 > 50


def test_by_content():
    _seed()
    items = client.get("/api/traffic/dashboard/by-content").json()
    assert items[0]["content_id"] == "c1"  # 200 > 80 > 50
    assert items[0]["conversions"]["course_signup"] == 2


def test_roi_report():
    _seed()
    rp = client.get("/api/traffic/dashboard/roi-report").json()
    assert rp["summary"]["revenue"] == 330.0
    assert rp["summary"]["cost"] == 3.0
    assert rp["summary"]["roi"] == pytest.approx(110.0, abs=1e-3)
    assert len(rp["by_dimension"]) == 2
    assert len(rp["by_account"]) == 2
