"""C 组：内容初始化种子测试（幂等 + 内容完整性）"""
import os
import sys
import tempfile

import pytest

_tmp = tempfile.mkdtemp(prefix="trafficos_test_seed_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["TRAFFICOS_DATA_DIR"] = _tmp

import scripts.seed as seed_mod  # noqa: E402
from app.storage import get_collection  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated():
    for name in ("accounts", "packaging", "topics"):
        get_collection(name).clear()
    yield
    from app.storage import _store
    for key, col in list(_store.items()):
        if key[1] == _tmp:
            col.clear()


def test_seed_content_counts():
    r = seed_mod.seed()
    assert r == {"accounts": 5, "templates": 6, "topics": 30}


def test_seed_idempotent():
    seed_mod.seed()
    r2 = seed_mod.seed()
    assert r2 == {"accounts": 0, "templates": 0, "topics": 0}
    # 数量不变
    assert len(get_collection("accounts").list()) == 5
    assert len(get_collection("topics").list()) == 30


def test_seed_covers_all_monetizers():
    seed_mod.seed()
    mons = {t["monetizer"] for t in get_collection("topics").list()}
    assert mons == {"adshare", "netdisk", "xianyu", "saas", "resource", "course", "tool"}


def test_seed_topics_all_scored():
    seed_mod.seed()
    topics = get_collection("topics").list()
    assert all(t["score"] > 0 for t in topics)
    assert all(t["source"] == "seed" for t in topics)


def test_seed_accounts_shape():
    seed_mod.seed()
    accs = get_collection("accounts").list()
    assert len(accs) == 5
    dims = {a["dimension"] for a in accs}
    assert dims == {"pure_content", "knowledge", "soft_ad"}
    # 每账号有维度+变现+人设
    for a in accs:
        assert a["dimension"] and a["monetizer"] and a["persona"]


def test_seed_force_rebuild():
    seed_mod.seed()
    r = seed_mod.seed(force=True)
    assert r == {"accounts": 5, "templates": 6, "topics": 30}
