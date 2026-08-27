# -*- coding: utf-8 -*-
"""prompt_service.py 全量单测（D 目标：25% → 90%+）。纯逻辑，tmp_path 注入 prompt_dir。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.prompt_service import (  # noqa: E402
    PromptEntry,
    PromptService,
    PromptVariable,
)


@pytest.fixture
def svc(tmp_path):
    return PromptService(prompt_dir=str(tmp_path / "prompts"))


# ==================== 模型 ====================

def test_prompt_variable_roundtrip():
    v = PromptVariable(name="topic", default="科技", description="主题", required=True)
    d = v.to_dict()
    assert d["name"] == "topic" and d["required"] is True
    v2 = PromptVariable.from_dict({**d, "extra": 1})
    assert v2.name == "topic" and not hasattr(v2, "extra")


def test_prompt_entry_extract_variables():
    e = PromptEntry(prompt_id="p1", name="n", content="你好 {name} 和 {topic}，{name} 再见")
    assert sorted(e.extract_variables()) == ["name", "topic"]


def test_prompt_entry_from_dict_filters_unknown():
    e = PromptEntry.from_dict({"prompt_id": "p1", "name": "n", "content": "c", "not_a_field": 1})
    assert e.prompt_id == "p1" and not hasattr(e, "not_a_field")


# ==================== CRUD ====================

def test_create_auto_extract_variables(svc):
    e = svc.create("开场", "早上好 {audience}，欢迎来到{channel}")
    assert e.prompt_id.startswith("prompt_")
    assert {v["name"] for v in e.variables} == {"audience", "channel"}
    assert svc.get(e.prompt_id) is e


def test_create_with_explicit_variables(svc):
    e = svc.create("带变量", "内容 {v}", variables=[{"name": "v", "default": "x", "required": True}])
    assert e.variables == [{"name": "v", "default": "x", "required": True}]


def test_get_missing(svc):
    assert svc.get("nope") is None


def test_update_basic(svc):
    e = svc.create("a", "old")
    updated = svc.update(e.prompt_id, {"name": "b", "category": "action", "tags": ["x"]})
    assert updated.name == "b" and updated.category == "action"
    assert updated.tags == ["x"]
    assert updated.version == 2


def test_update_missing(svc):
    assert svc.update("nope", {"name": "x"}) is None


def test_update_content_re_extract_vars(svc):
    e = svc.create("a", "原 {a}")
    e2 = svc.update(e.prompt_id, {"content": "新 {a} {b}"})
    names = {v["name"] for v in e2.variables}
    assert "b" in names and "a" in names


def test_delete(svc):
    e = svc.create("a", "c")
    assert svc.delete(e.prompt_id) is True
    assert svc.get(e.prompt_id) is None
    assert svc.delete("nope") is False


# ==================== list 过滤 ====================

def test_list_filters(svc):
    a = svc.create("甲", "甲内容 abc", category="action", tags=["热"], project_id="p1",
                   quality_score=5.0)
    b = svc.create("乙", "乙内容 def", category="style", tags=["冷"], project_id="",
                   quality_score=3.0)
    # 项目过滤（含全局）
    assert {p.prompt_id for p in svc.list_prompts(project_id="p1")} == {a.prompt_id, b.prompt_id}
    # 分类过滤
    assert [p.prompt_id for p in svc.list_prompts(category="style")] == [b.prompt_id]
    # 标签过滤
    assert [p.prompt_id for p in svc.list_prompts(tag="热")] == [a.prompt_id]
    # 关键词过滤
    assert [p.prompt_id for p in svc.list_prompts(keyword="甲内容")] == [a.prompt_id]
    # 质量降序
    assert svc.list_prompts()[0].prompt_id == a.prompt_id


# ==================== 历史 / 回滚 ====================

def test_history_and_rollback(svc):
    e = svc.create("a", "v1 content")
    svc.update(e.prompt_id, {"content": "v2 content"})
    svc.update(e.prompt_id, {"content": "v3 content"})
    hist = svc.get_history(e.prompt_id)
    assert len(hist) >= 2
    assert hist[0]["version"] > hist[1]["version"]  # 降序
    rolled = svc.rollback(e.prompt_id, 1)
    assert rolled.content == "v1 content"
    assert rolled.version == 4  # 递增
    # 回滚不存在的版本
    assert svc.rollback(e.prompt_id, 999) is None
    assert svc.rollback("nope", 1) is None


# ==================== 默认提示词 ====================

def test_set_and_get_default(svc):
    e1 = svc.create("d1", "c1", project_id="p1")
    e2 = svc.create("d2", "c2", project_id="p1")
    assert svc.set_default(e1.prompt_id, "p1") is True
    # 同项目再设第二个 → 第一个取消
    svc.set_default(e2.prompt_id, "p1")
    assert svc.get_default("p1").prompt_id == e2.prompt_id
    assert svc.get(e1.prompt_id).is_default is False
    # 不存在
    assert svc.set_default("nope", "p1") is False
    # 优先级：项目+阶段 > 项目+通用
    e3 = svc.create("d3", "c3", project_id="p1", stage_id="storyboard")
    svc.set_default(e3.prompt_id, "p1", "storyboard")
    assert svc.get_default("p1", "storyboard").prompt_id == e3.prompt_id
    assert svc.get_default("p1").prompt_id == e2.prompt_id
    # 取消
    assert svc.unset_default(e2.prompt_id) is True
    assert svc.unset_default("nope") is False


# ==================== 变量解析 ====================

def test_resolve(svc):
    e = svc.create("r", "你好 {name}，{missing}", variables=[{"name": "name", "default": "小明"}])
    resolved, entry = svc.resolve(e.prompt_id, {"missing": "世界"})
    assert resolved == "你好 小明，世界"
    assert entry.usage_count == 1
    # 不存在
    assert svc.resolve("nope") is None
    # 自动提取的变量默认空 → 替换为空（设计行为）；resolve_content 无变量映射时保留原占位符
    e2 = svc.create("r2", "保留 {x}")
    r2, _ = svc.resolve(e2.prompt_id)
    assert r2 == "保留 "
    assert svc.resolve_content("保留 {x}") == "保留 {x}"


def test_resolve_content(svc):
    assert svc.resolve_content("a {x} b {y}", {"x": "1"}) == "a 1 b {y}"
    assert svc.resolve_content("无变量") == "无变量"


# ==================== 统计 ====================

def test_categories_tags_stats(svc):
    svc.create("a", "c", category="action", tags=["x", "y"], quality_score=4.0)
    svc.create("b", "c", category="action", tags=["x"], quality_score=2.0)
    cats = {c["category"]: c["count"] for c in svc.get_categories()}
    assert cats == {"action": 2, "custom": 0} or cats["action"] == 2
    tags = {t["tag"]: t["count"] for t in svc.get_tags()}
    assert tags["x"] == 2 and tags["y"] == 1
    stats = svc.get_stats()
    assert stats["total"] == 2
    assert stats["avg_quality"] == 3.0
    assert stats["total_usage"] == 0


# ==================== 持久化加载 ====================

def test_load_persisted(svc, tmp_path):
    e = svc.create("持久", "内容", tags=["t1"])
    svc2 = PromptService(prompt_dir=str(tmp_path / "prompts"))
    loaded = svc2.get(e.prompt_id)
    assert loaded is not None and loaded.name == "持久" and loaded.tags == ["t1"]
