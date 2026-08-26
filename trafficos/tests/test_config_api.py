"""TrafficOS 配置 API 冒烟测试"""
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

# 用临时数据目录隔离测试
_tmp = tempfile.mkdtemp(prefix="trafficos_test_")
os.environ["TRAFFICOS_DATA_DIR"] = _tmp
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_collections():
    yield
    # 每个用例后清空集合文件，避免相互污染
    import glob
    for f in glob.glob(os.path.join(_tmp, "*.json")):
        os.remove(f)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ---------- dimensions ----------

def test_dimension_crud():
    # create
    r = client.post("/api/traffic/dimensions", json={
        "code": "knowledge", "name": "知识讲解", "target": "建信任",
        "action": "挂课程", "ratio": 0.35,
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["id"].startswith("dim_")
    assert d["code"] == "knowledge"
    did = d["id"]

    # list
    r = client.get("/api/traffic/dimensions")
    assert len(r.json()) == 1

    # get
    r = client.get(f"/api/traffic/dimensions/{did}")
    assert r.json()["name"] == "知识讲解"

    # update
    r = client.put(f"/api/traffic/dimensions/{did}", json={
        "code": "knowledge", "name": "知识讲解v2", "target": "建信任",
        "action": "挂课程", "ratio": 0.40,
    })
    assert r.json()["name"] == "知识讲解v2"
    assert r.json()["ratio"] == 0.40

    # delete
    r = client.delete(f"/api/traffic/dimensions/{did}")
    assert r.status_code == 200
    assert client.get(f"/api/traffic/dimensions/{did}").status_code == 404


def test_dimension_invalid_code():
    r = client.post("/api/traffic/dimensions", json={"code": "bad", "name": "x"})
    assert r.status_code == 422  # 枚举校验


# ---------- monetizers ----------

def test_monetizer_crud():
    r = client.post("/api/traffic/monetizers", json={
        "code": "adshare", "name": "广告分成", "priority": 1,
    })
    assert r.status_code == 200
    mid = r.json()["id"]
    assert client.get("/api/traffic/monetizers").json()[0]["code"] == "adshare"
    assert client.delete(f"/api/traffic/monetizers/{mid}").status_code == 200
    assert client.get(f"/api/traffic/monetizers/{mid}").status_code == 404


# ---------- accounts ----------

def test_account_crud():
    r = client.post("/api/traffic/accounts", json={
        "name": "知识号-课程",
        "dimension": "knowledge",
        "monetizer": "course",
        "persona": "行业专家",
        "cadence": "medium",
        "bio": "专注XX领域，私信领资料",
    })
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["dimension"] == "knowledge"
    assert a["monetizer"] == "course"
    assert a["cadence"] == "medium"
    acc_id = a["id"]

    r = client.get("/api/traffic/accounts")
    assert len(r.json()) == 1

    r = client.put(f"/api/traffic/accounts/{acc_id}", json={
        "name": "知识号-课程v2", "dimension": "knowledge", "monetizer": "course",
        "persona": "行业专家", "cadence": "low", "bio": "新简介",
    })
    assert r.json()["cadence"] == "low"

    assert client.delete(f"/api/traffic/accounts/{acc_id}").status_code == 200
    assert client.get(f"/api/traffic/accounts/{acc_id}").status_code == 404


def test_account_persistence_across_instances():
    """存储层可审计：重启（重建集合）后数据仍在。"""
    from app.storage import _store, get_collection
    r = client.post("/api/traffic/accounts", json={
        "name": "内容号-广告", "dimension": "pure_content",
        "monetizer": "adshare", "persona": "泛娱乐", "cadence": "high",
    })
    assert r.status_code == 200
    # 模拟重启：清空内存单例，重新加载（读同一 JSON 文件）
    _store.clear()
    col = get_collection("accounts")
    assert len(col.list()) == 1
    assert col.list()[0]["name"] == "内容号-广告"
