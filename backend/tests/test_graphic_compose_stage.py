"""GraphicStage / ComposeStage 单元测试：模板渲染、LLM 路径、降级与错误分支

mock 掉 provider(LLM) + asset service，验证图形/合成阶段的纯逻辑。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.asset_service import AssetRef  # noqa: E402


def _ref(aid="asset_1", atype="image"):
    return AssetRef(asset_id=aid, asset_type=atype, name=aid)


class _FakeAssetSvc:
    async def create(self, **kw):
        return type("A", (), {"asset_id": "asset_graphic_1"})()


def _patch_deps(monkeypatch, llm_text=None, raise_exc=False):
    import services.provider_service as ps  # noqa: F401

    class FakeProvider:
        async def generate_text(self, **kw):
            from services.provider_service import ProviderResult

            if raise_exc:
                raise RuntimeError("LLM down")
            text = (
                llm_text
                or json.dumps(
                    {
                        "title": "T",
                        "subtitle": "S",
                        "items": [
                            {"label": "A", "value": "1"},
                            {"label": "B", "value": "2"},
                            {"label": "C", "value": "3"},
                        ],
                    },
                    ensure_ascii=False,
                )
            )
            return ProviderResult(
                metadata={"text": text}, provider_id="openai_compat"
            )

    monkeypatch.setattr(ps, "get_provider_service", lambda *a, **k: FakeProvider())
    # patch 模块内已绑定的名字（from ... import），确保 stage 取到 fake
    import services.stages.graphic_stage as gs
    monkeypatch.setattr(gs, "get_provider_service", lambda *a, **k: FakeProvider())


def _mk_stage(stage_cls):
    """构造 stage 实例（bypass __init__），保留类级 stage_def"""
    return stage_cls.__new__(stage_cls)


async def test_graphic_stage_execute(tmp_path, monkeypatch):
    from services.stages.graphic_stage import GraphicStage

    _patch_deps(monkeypatch)
    stage = _mk_stage(GraphicStage)
    result = await stage.execute(
        [_ref()], params={"topic": "测试图形", "graphic_type": "infographic"}
    )
    # 未配置真实 API key → LLM 调用在 provider 层失败，但阶段优雅降级（不抛异常）
    assert result is not None and result.success
    assert result.asset is not None


async def test_graphic_stage_unknown_type(tmp_path, monkeypatch):
    from services.stages.graphic_stage import GraphicStage

    _patch_deps(monkeypatch)
    stage = _mk_stage(GraphicStage)
    result = await stage.execute(
        [_ref()], params={"graphic_type": "no_such_type"}
    )
    assert not result.success and "不支持" in (result.error or "")


async def test_graphic_stage_llm_failure(tmp_path, monkeypatch):
    from services.stages.graphic_stage import GraphicStage

    _patch_deps(monkeypatch, raise_exc=True)
    stage = _mk_stage(GraphicStage)
    result = await stage.execute(
        [_ref()], params={"graphic_type": "infographic"}
    )
    assert not result.success and "LLM 调用失败" in (result.error or "")


async def test_graphic_stage_bad_json(tmp_path, monkeypatch):
    from services.stages.graphic_stage import GraphicStage

    _patch_deps(monkeypatch, llm_text="这不是 JSON")
    stage = _mk_stage(GraphicStage)
    result = await stage.execute(
        [_ref()], params={"graphic_type": "infographic"}
    )
    # 无法解析 → 降级为原始文本渲染，仍成功（优雅降级，不抛异常）
    assert result is not None and result.success


async def test_graphic_stage_empty_content(tmp_path, monkeypatch):
    from services.stages.graphic_stage import GraphicStage

    class EmptyProvider:
        async def generate_text(self, **kw):
            from services.provider_service import ProviderResult

            return ProviderResult(metadata={"text": ""}, provider_id="openai_compat")

    import services.stages.graphic_stage as gs
    monkeypatch.setattr(gs, "get_provider_service", lambda *a, **k: EmptyProvider())
    stage = _mk_stage(GraphicStage)
    result = await stage.execute(
        [_ref()], params={"graphic_type": "infographic"}
    )
    assert not result.success and "空内容" in (result.error or "")


async def test_graphic_render_variants(tmp_path):
    """直接调用 _render_graphic 覆盖各类型渲染分支"""
    from services.stages.graphic_stage import GraphicStage

    stage = GraphicStage()
    base = {
        "title": "T",
        "subtitle": "S",
        "items": [
            {"label": "A", "value": "1"},
            {"label": "B", "value": "2"},
            {"label": "C", "value": "3"},
        ],
    }
    for gtype in ["infographic", "comparison", "tutorial", "checklist", "quote",
                  "data_chart", "video_cover", "emotional_scene"]:
        url = await stage._render_graphic(gtype, base, "minimal", 640, 480)
        assert isinstance(url, str), f"{gtype} returned {type(url)}"


async def test_compose_stage_execute(tmp_path):
    from services.stages.compose_stage import ComposeStage

    stage = ComposeStage()
    # 少于 min_count=2 inputs → graceful error, no exception
    result = await stage.execute([_ref()], params={"prompt": "合成测试"})
    assert result is not None and not result.success


async def test_stage_registry_and_base():
    from services.stage_service import (
        AssetProduceResult,
        StagePlugin,
        get_stage_service,
    )

    svc = get_stage_service()
    stages = svc.list_stages()
    assert isinstance(stages, list)

    class P(StagePlugin):
        stage_id = "test_x"
        display_name = "Test"

        async def execute(self, input_assets, provider_id="", params=None):
            return AssetProduceResult(
                asset=type("A", (), {"asset_id": "a1"})(), success=True
            )

    p = P()
    r = await p.execute([])
    assert r.success and r.asset.asset_id == "a1"
