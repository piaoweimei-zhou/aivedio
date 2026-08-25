"""workflow_helpers 纯逻辑单元测试：DAG 节点查找、参数注入、类型推断"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.workflow_helpers import (  # noqa: E402
    find_node_by_class_type,
    find_node_by_title,
    find_first_node_by_class_type,
    find_first_node_by_class_type_contains,
    _infer_saveimage_type,
    _detect_age_in_prompt,
    _get_denoise_sequence,
    _set_ksampler_params,
    _set_reference_image,
    _set_clip_text,
    _set_filename_prefix,
    _detect_fusion_type,
)


def make_wf():
    return {
        "1": {"class_type": "KSampler", "inputs": {"denoise": 0.5, "cfg": 7, "seed": 42, "steps": 20, "scheduler": "normal"}},  # noqa: E501
        "2": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
        "3": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "old"}},
        "5": {"_meta": {"title": "我的节点"}, "class_type": "Anything", "inputs": {}},
    }


class TestFindNode:
    def test_find_by_class_type(self):
        wf = make_wf()
        r = find_node_by_class_type(wf, "KSampler")
        assert r == [("1", wf["1"])]
        assert find_node_by_class_type(wf, "NotExist") == []

    def test_find_by_title(self):
        wf = make_wf()
        r = find_node_by_title(wf, "我的节点")
        assert len(r) == 1 and r[0][0] == "5"
        assert find_node_by_title(wf, "nope") == []

    def test_find_first(self):
        wf = make_wf()
        nid, nd = find_first_node_by_class_type(wf, "LoadImage")
        assert nid == "2"
        nid2, nd2 = find_first_node_by_class_type(wf, "NotExist")
        assert nid2 is None and nd2 is None

    def test_find_first_contains(self):
        wf = make_wf()
        nid, nd = find_first_node_by_class_type_contains(wf, "KSampler")
        assert nid == "1"
        nid2, _ = find_first_node_by_class_type_contains(wf, "zzz")
        assert nid2 is None


class TestInferSaveimageType:
    def test_direct_class_type(self):
        # SaveImage 上游是 Lineart 节点
        wf = {
            "10": {"class_type": "SaveImage", "inputs": {"images": ["20", 0]}},
            "20": {"class_type": "LineartPreprocessor", "inputs": {}},
        }
        t = _infer_saveimage_type(wf, "10", {"lineart": ["lineart"]})
        assert t == "lineart"

    def test_preprocessor_param(self):
        wf = {
            "10": {"class_type": "SaveImage", "inputs": {"images": ["20", 0]}},
            "20": {"class_type": "AIO_Preprocessor", "inputs": {"preprocessor": "Lineart"}},
        }
        t = _infer_saveimage_type(wf, "10", {"lineart": ["lineart"]})
        assert t == "lineart"

    def test_no_match(self):
        wf = {"10": {"class_type": "SaveImage", "inputs": {"images": ["20", 0]}}, "20": {"class_type": "KSampler", "inputs": {}}}  # noqa: E501
        assert _infer_saveimage_type(wf, "10", {"lineart": ["lineart"]}) == "unknown"

    def test_cycle_safe(self):
        wf = {
            "1": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
            "2": {"class_type": "NodeX", "inputs": {"a": ["1", 0]}},
        }
        assert _infer_saveimage_type(wf, "1", {}) == "unknown"


class TestDetectAge:
    def test_exact_age(self):
        assert _detect_age_in_prompt("一个3岁小孩")[0] == "child"
        assert _detect_age_in_prompt("一个10岁少年")[0] == "child"
        assert _detect_age_in_prompt("一个14岁少年")[0] == "teen"
        assert _detect_age_in_prompt("一个18岁青年")[0] == "teen"
        assert _detect_age_in_prompt("一个25岁青年")[0] == "young"
        assert _detect_age_in_prompt("一个40岁中年")[0] == "adult"
        assert _detect_age_in_prompt("一个60岁老人")[0] == "elder"

    def test_keywords(self):
        assert _detect_age_in_prompt("可爱的小女孩")[0] == "child"
        assert _detect_age_in_prompt("青少年模特")[0] == "teen"
        assert _detect_age_in_prompt("漂亮美女")[0] == "young"

    def test_unknown(self):
        assert _detect_age_in_prompt("一个机器人")[0] == "unknown"


class TestDenoiseSequence:
    def test_fish_single_step(self):
        assert _get_denoise_sequence() == [(1.0, "primary_char", 1.0)]


class TestSetKsamplerParams:
    def test_overwrite(self):
        wf = make_wf()
        _set_ksampler_params(wf, denoise=0.8, cfg_scale=5, seed=99, steps=30, scheduler="beta57")
        k = wf["1"]["inputs"]
        assert k["denoise"] == 0.8 and k["cfg"] == 5 and k["seed"] == 99
        assert k["steps"] == 30 and k["scheduler"] == "beta57"

    def test_no_ksampler(self):
        wf = {"9": {"class_type": "LoadImage", "inputs": {}}}
        assert _set_ksampler_params(wf, 1.0, 1.0, 1) == wf


class TestSetReferenceImage:
    def test_by_node_id(self, monkeypatch):
        wf = make_wf()
        _set_reference_image(wf, node_id="2", image_path="new.png")
        assert wf["2"]["inputs"]["image"] == "new.png"

    def test_by_class_type(self, monkeypatch):
        wf = make_wf()
        wf["2"]["inputs"].pop("image")  # 无 image 字段时不注入
        wf["2"]["inputs"]["image"] = "will_be_replaced.png"
        _set_reference_image(wf, node_id="", image_path="img.png", class_type="LoadImage")
        assert wf["2"]["inputs"]["image"] == "img.png"


class TestSetClipText:
    def test_by_node_id_text(self):
        wf = make_wf()
        _set_clip_text(wf, node_id="4", text="新文本")
        assert wf["4"]["inputs"]["text"] == "新文本"

    def test_by_class_type_contains(self):
        wf = make_wf()
        _set_clip_text(wf, node_id="", text="hello", class_type="CLIPTextEncode")
        assert wf["4"]["inputs"]["text"] == "hello"


class TestSetFilenamePrefix:
    def test_all_saveimage(self):
        wf = make_wf()
        _set_filename_prefix(wf, "T01_new")
        assert wf["3"]["inputs"]["filename_prefix"] == "T01_new"


class TestDetectFusionType:
    def test_char_scene(self):
        r = _detect_fusion_type({"character": "c.png", "scene": "s.png"})
        assert r["type"] == "char_scene"
        assert r["image_1"] == "c.png" and r["image_2"] == "s.png"

    def test_char_char(self):
        r = _detect_fusion_type({"character": "a.png", "character2": "b.png", "scene": "s.png"})
        assert r["type"] == "char_char"
        assert r["image_1"] == "a.png" and r["image_3"] == "b.png"

    def test_char_prop(self):
        r = _detect_fusion_type({"character": "a.png", "prop": "p.png"})
        assert r["type"] == "char_prop"
        assert r["image_1"] == "a.png" and r["image_3"] == "p.png"

    def test_scene_prop(self):
        r = _detect_fusion_type({"prop": "p.png", "scene": "s.png"})
        assert r["type"] == "scene_prop"

    def test_fallback(self):
        r = _detect_fusion_type({"scene": "s.png"})
        assert r["type"] == "char_scene"
        assert r["image_2"] == "s.png"
