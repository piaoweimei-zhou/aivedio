# -*- coding: utf-8 -*-
"""asset_service.py 全量单测（D 目标：40% → 90%+）。async 纯逻辑，tmp_path 注入 storage_dir。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.asset_service import (  # noqa: E402
    ASSET_TYPES,
    AssetRef,
    AssetService,
    STAGE_TYPES,
)


@pytest.fixture
def svc(tmp_path):
    return AssetService(storage_dir=str(tmp_path / "assets"))


@pytest.mark.asyncio
async def test_create_get(svc):
    a = await svc.create("concept", "角色概念", urls=["/output/c/a.png"])
    assert a.asset_id and a.version == 1
    assert svc.get(a.asset_id).name == "角色概念"
    assert svc.get("nope") is None


@pytest.mark.asyncio
async def test_create_project_inherit(svc):
    parent = await svc.create("concept", "父", project_id="proj1")
    child = await svc.create("storyboard", "子", urls=["/output/x.png"], parent_id=parent.asset_id)
    assert child.project_id == "proj1"
    # 显式传入优先
    child2 = await svc.create("storyboard", "子2", parent_id=parent.asset_id, project_id="proj2")
    assert child2.project_id == "proj2"


@pytest.mark.asyncio
async def test_update_versioned(svc):
    a = await svc.create("concept", "n1", urls=["/u/1"])
    updated = await svc.update(a.asset_id, name="n2", metadata={"k": "v"})
    assert updated.version == 2 and updated.name == "n2"
    assert updated.metadata["k"] == "v"
    assert await svc.update("nope") is None


@pytest.mark.asyncio
async def test_list_filters(svc):
    await svc.create("concept", "c1", content_type="character", project_id="p1")
    await svc.create("concept", "c2", content_type="scene", project_id="p1")
    await svc.create("video", "v1", project_id=None)
    assert len(svc.list_assets(asset_type="concept")) == 2
    assert len(svc.list_assets(content_type="character")) == 1
    assert len(svc.list_assets(category="video")) == 1  # 通过类型注册表映射
    assert len(svc.list_assets(project_id="p1")) == 2
    assert len(svc.list_assets(project_id="__none__")) == 1
    assert len(svc.list_assets(parent_id="zzz")) == 0


@pytest.mark.asyncio
async def test_delete_and_purge(svc, tmp_path):
    a = await svc.create("concept", "x", urls=[])
    assert await svc.delete(a.asset_id) is True
    assert svc.get(a.asset_id) is None
    assert await svc.delete("nope") is False
    # purge_files：创建临时文件 + 删除
    from services.providers import provider_utils as pu
    orig_out = pu.OUTPUT_DIR
    pu.OUTPUT_DIR = str(tmp_path / "out")
    os.makedirs(str(tmp_path / "out" / "img"), exist_ok=True)
    f = tmp_path / "out" / "img" / "del.png"
    f.write_bytes(b"x")
    b = await svc.create("concept", "purge", urls=["/output/img/del.png"])
    removed = svc._purge_asset_files(b)
    assert len(removed) == 1 and not f.exists()
    # 重复引用只删一次
    b2 = await svc.create("concept", "purge2",
                          urls=["/output/img/del2.png", "/output/img/del2.png"])
    f2 = tmp_path / "out" / "img" / "del2.png"
    f2.write_bytes(b"x")
    assert len(svc._purge_asset_files(b2)) == 1
    pu.OUTPUT_DIR = orig_out


@pytest.mark.asyncio
async def test_consume_produce_lineage(svc):
    parent = await svc.produce("concept", "源", urls=["/u/s.png"])
    child = await svc.produce("storyboard", "帧", urls=["/u/f.png"], parent_id=parent.asset_id)
    grand = await svc.produce("video", "成片", urls=["/u/v.mp4"], parent_id=child.asset_id)
    assert svc.consume(parent.asset_id).name == "源"
    assert [a.asset_id for a in svc.consume_multi([parent.asset_id, "nope"])] == [parent.asset_id]
    chain = svc.lineage(grand.asset_id)
    assert [a.asset_id for a in chain] == [parent.asset_id, child.asset_id, grand.asset_id]
    assert [a.asset_id for a in svc.children(parent.asset_id)] == [child.asset_id]
    # 断链
    lone = await svc.create("concept", "孤")
    assert [a.asset_id for a in svc.lineage(lone.asset_id)] == [lone.asset_id]


@pytest.mark.asyncio
async def test_broadcast(svc):
    events = []
    svc.on_change(lambda e, a: events.append((e, a.asset_id)))
    a = await svc.create("concept", "b1")
    await svc.update(a.asset_id, name="b2")
    await svc.delete(a.asset_id)
    kinds = [e for e, _ in events]
    assert kinds == ["asset:created", "asset:updated", "asset:deleted"]
    # 回调异常被吞
    svc.on_change(lambda e, a: (_ for _ in ()).throw(RuntimeError("boom")))
    a2 = await svc.create("concept", "b3")
    assert svc.get(a2.asset_id) is not None


@pytest.mark.asyncio
async def test_stats_and_persistence(svc, tmp_path):
    await svc.create("concept", "a")
    await svc.create("concept", "b")
    await svc.create("video", "c")
    stats = svc.stats()
    assert stats["total"] == 3 and stats["by_type"]["concept"] == 2
    # 重新加载
    svc2 = AssetService(storage_dir=str(tmp_path / "assets"))
    assert len(svc2._assets) == 3


def test_asset_types_registry():
    assert STAGE_TYPES["concept"]["category"] == "image"
    assert ASSET_TYPES["video"]["category"] == "video"
    assert ASSET_TYPES["character"]["label"] == "角色"


def test_asset_ref_autogen():
    a = AssetRef(asset_id="", asset_type="concept", name="auto")
    assert a.asset_id and a.created_at and a.updated_at
    b = AssetRef(asset_id="fixed", asset_type="concept", name="f", created_at=1.0)
    assert b.asset_id == "fixed" and b.updated_at == 1.0


@pytest.mark.asyncio
async def test_cleanup_orphaned_dry_run(svc):
    a = await svc.create("concept", "有文件", urls=["/output/noexist/real.png"])
    b = await svc.create("concept", "空url", urls=[])
    report = await svc.cleanup_orphaned(dry_run=True)
    assert report["checked"] == 2
    assert a.asset_id in report["orphaned_ids"]  # 文件不存在 → 孤岛
    assert b.asset_id in report["orphaned_ids"]  # 空 urls → 孤岛
    assert report["removed"] == 0
    # 非 dry_run → 删除
    report2 = await svc.cleanup_orphaned(dry_run=False)
    assert report2["removed"] == 2
    assert len(svc._assets) == 0


@pytest.mark.asyncio
async def test_asset_url_exists_local(svc, tmp_path):
    from services.providers import provider_utils as pu
    orig = pu.OUTPUT_DIR
    pu.OUTPUT_DIR = str(tmp_path / "out")
    os.makedirs(str(tmp_path / "out" / "img"), exist_ok=True)
    f = tmp_path / "out" / "img" / "exists.png"
    f.write_bytes(b"png")
    assert svc._asset_url_exists(["/output/img/exists.png"]) is True
    assert svc._asset_url_exists(["/output/img/missing.png"]) is False
    assert svc._asset_url_exists([""]) is False
    assert svc._asset_url_exists([]) is False
    pu.OUTPUT_DIR = orig
