"""stage_service 单元测试：阶段定义 / 输入校验 / 注册解析 / 辅助函数"""

from services.asset_service import AssetProduceResult, AssetRef
from services.stage_service import (
    StageDef,
    StagePlugin,
    StageService,
    build_reference_images,
    collect_content_type,
    reset_stage_service,
)


class DummyStage(StagePlugin):
    stage_def = StageDef(
        stage_id="dummy",
        name="测试阶段",
        input_types=["concept"],
        output_type="edit",
        default_provider="comfyui",
        supported_providers=["comfyui"],
    )

    async def execute(self, input_assets, provider_id="", params=None):
        return AssetProduceResult(
            asset=AssetRef(asset_id="a1", asset_type="edit", name="out"),
            success=True,
        )


def _asset(asset_type, content_type="", urls=None):
    return AssetRef(
        asset_id=f"id_{asset_type}",
        asset_type=asset_type,
        name=asset_type,
        content_type=content_type,
        urls=urls or [],
    )


def test_stage_def_defaults():
    d = StageDef(
        stage_id="x",
        name="X",
        input_types=["a"],
        output_type="b",
        default_provider="p",
        supported_providers=["p"],
    )
    assert d.description == ""
    assert d.input_content_types == []


def test_validate_inputs_asset_type():
    stage = DummyStage()
    assert stage.validate_inputs([_asset("concept")]) is None
    err = stage.validate_inputs([_asset("video")])
    assert err and "不接受的输入类型" in err


def test_validate_inputs_content_type():
    stage = DummyStage()
    stage.stage_def = StageDef(
        stage_id="dummy",
        name="测试阶段",
        input_types=["concept"],
        output_type="edit",
        default_provider="comfyui",
        supported_providers=["comfyui"],
        input_content_types=["character"],
    )
    err = stage.validate_inputs([_asset("concept", content_type="scene")])
    assert err and "缺少内容类型" in err
    assert stage.validate_inputs([_asset("concept", content_type="character")]) is None


def test_validate_inputs_empty():
    stage = DummyStage()
    assert stage.validate_inputs([]) is None


def test_resolve_provider():
    stage = DummyStage()
    assert stage._resolve_provider("") == "comfyui"
    assert stage._resolve_provider("jimeng") == "jimeng"


def test_register_and_resolve():
    reset_stage_service()
    svc = StageService()
    svc.register(DummyStage())
    defs = svc.resolve(["concept"])
    assert any(d.stage_id == "dummy" for d in defs)
    defs2 = svc.resolve(["video"])
    assert not any(d.stage_id == "dummy" for d in defs2)
    assert svc.get_stage_def("dummy").name == "测试阶段"


def test_list_stages_contains_builtin():
    reset_stage_service()
    svc = StageService()
    ids = {d["stage_id"] for d in svc.list_stages()}
    assert "concept" in ids
    assert "video" in ids


def test_build_reference_images():
    assets = [
        _asset("concept", urls=["http://x/c.png"]),
        _asset("pose", urls=["http://x/p.png"]),
        _asset("depth", urls=["http://x/d.png"]),
        _asset("lineart", urls=["http://x/l.png"]),
    ]
    refs = build_reference_images(assets)
    assert [r["role"] for r in refs] == ["character", "pose", "depth", "mask"]


def test_build_reference_images_multi_group():
    assets = [
        _asset("concept", urls=["http://x/1.png"]),
        _asset("concept", urls=["http://x/2.png"]),
        _asset("concept", urls=["http://x/3.png"]),
    ]
    refs = build_reference_images(assets, multi_group=True)
    assert [r["role"] for r in refs] == ["character", "character2", "character3"]


def test_build_reference_images_skips_no_url():
    assets = [_asset("concept", urls=[]), _asset("pose", urls=["http://x/p.png"])]
    refs = build_reference_images(assets)
    assert [r["role"] for r in refs] == ["pose"]


def test_collect_content_type():
    assert collect_content_type([_asset("concept", content_type="")]) == ""
    assert collect_content_type([_asset("concept", content_type="scene")]) == "scene"
    assert collect_content_type([_asset("a"), _asset("b", content_type="prop")]) == "prop"
