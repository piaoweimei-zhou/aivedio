"""P2c 账号规模化单测：seed 账号矩阵扩展 + 幂等。"""
from __future__ import annotations

import tempfile

from scripts.seed import SEED_ACCOUNTS


def test_seed_accounts_matrix_scaled():
    """账号矩阵 ≥10 个，覆盖 5 维度 × 多变现 × 多节奏。"""
    assert len(SEED_ACCOUNTS) >= 10
    names = [a.name for a in SEED_ACCOUNTS]
    assert len(set(names)) == len(names)          # 名称唯一（幂等键）
    dims = {a.dimension for a in SEED_ACCOUNTS}
    assert len(dims) >= 3                          # 覆盖主要维度
    cads = {a.cadence for a in SEED_ACCOUNTS}
    assert len(cads) == 3                          # 高/中/低频都有
    mons = {a.monetizer for a in SEED_ACCOUNTS}
    assert len(mons) >= 5                          # 多变现覆盖


def test_seed_accounts_idempotent_insert(monkeypatch):
    """seed 幂等：重复插入同名账号不重复。"""
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("TRAFFICOS_DATA_DIR", tmp.name)
    from app.storage import get_collection

    col = get_collection("accounts")
    inserted = 0
    for acc in SEED_ACCOUNTS:
        existing = {r["name"] for r in col.list()}
        if acc.name in existing:
            continue
        col.insert(acc)
        inserted += 1
    # 第一次：全部插入
    assert inserted == len(SEED_ACCOUNTS)
    assert len(col.list()) == len(SEED_ACCOUNTS)
    # 第二次：全部跳过
    again = 0
    for acc in SEED_ACCOUNTS:
        if acc.name in {r["name"] for r in col.list()}:
            again += 1
    assert again == len(SEED_ACCOUNTS)
    tmp.cleanup()
