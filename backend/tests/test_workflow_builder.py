"""workflow_builder 单元测试：节点查找 / 模板加载 / 工作流构建"""
import pytest

from services import workflow_builder as wb


def _wf():
    return {
        "1": {"class_type": "KSampler", "inputs": {"seed": 1}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello"}},
        "3": {"class_type": "SaveImage", "inputs": {}},
    }


def test_find_node_by_class_type():
    nodes = wb.find_node_by_class_type(_wf(), "KSampler")
    assert len(nodes) == 1
    assert nodes[0][0] == "1"
    assert wb.find_node_by_class_type(_wf(), "NoSuchNode") == []


def test_find_first_node_by_class_type():
    nid, node = wb.find_first_node_by_class_type(_wf(), "CLIPTextEncode")
    assert nid == "2"
    nid2, node2 = wb.find_first_node_by_class_type(_wf(), "NoSuchNode")
    assert (nid2, node2) == (None, None)


def test_find_first_node_by_class_type_contains():
    nid, node = wb.find_first_node_by_class_type_contains(_wf(), "KSampler")
    assert nid == "1"


def test_find_node_by_title():
    wf = {"1": {"class_type": "KSampler", "_meta": {"title": "采样器"}}}
    nodes = wb.find_node_by_title(wf, "采样器")
    assert len(nodes) == 1
    assert wb.find_node_by_title(wf, "不存在") == []


def test_base_workflow_loaded():
    assert wb.BASE_WORKFLOW, "标准版模板(文生图.json)应已加载"
    assert wb.CINEMATIC_WORKFLOW, "影视级模板(最终文生图.json)应已加载"


def test_workflow_node_summary():
    summary = wb.get_workflow_node_summary(_wf())
    assert len(summary) == 3
    assert summary["1"]["class"] == "KSampler"


def test_infer_saveimage_type():
    wf = {
        "1": {"class_type": "KSampler", "inputs": {}},
        "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
    }
    tag = wb._infer_saveimage_type(wf, "2", {"image": ["saveimage"]})
    assert tag == "image"


def test_infer_saveimage_type_unknown():
    wf = {"1": {"class_type": "SaveImage", "inputs": {}}}
    tag = wb._infer_saveimage_type(wf, "1", {"image": ["saveimage"]})
    assert tag == "image"


def test_infer_saveimage_type_no_match():
    wf = {"1": {"class_type": "SomethingElse", "inputs": {}}}
    tag = wb._infer_saveimage_type(wf, "1", {"image": ["saveimage"]})
    assert tag == "unknown"
