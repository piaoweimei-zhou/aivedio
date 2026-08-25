"""qc_service 聚合逻辑单元测试：aggregate 计分/合规/版权/通过判定、JSON 解析"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.qc.qc_service import aggregate, _parse_json_object  # noqa: E402


def make_tech(score=80, **kw):
    t = {"score": score, "voice": 70, "composition_cv": 75, "has_audio": True, "audio_metrics": {"sample_rate": 44100}}  # noqa: E501
    t.update(kw)
    return t


def make_sem(overrides=None):
    s = {"composition": 80, "consistency": 78, "lip_sync": 70, "rhythm": 75, "compliance": 90}
    if overrides:
        s.update(overrides)
    return s


class TestAggregate:
    def test_basic_scoring(self):
        r = aggregate(make_tech(), make_sem(), threshold=60.0)
        assert r.total_score > 60
        assert r.passed is True
        assert not r.blocked

    def test_too_low_score_fails(self):
        r = aggregate(make_tech(score=30, voice=30, composition_cv=20), make_sem({k: 30 for k in ("composition", "consistency", "lip_sync", "rhythm", "compliance")}), threshold=60.0)  # noqa: E501
        assert r.passed is False

    def test_lipsync_downgrade_no_audio(self):
        # 无音轨 + 模型给高 lip_sync → 降为保守 60
        r = aggregate(make_tech(has_audio=False), make_sem({"lip_sync": 85}), threshold=60.0)
        assert r.dimensions["lip_sync"] == 60
        assert any("lip_sync" in n for n in r.notes)

    def test_lipsync_downgrade_low_sample_rate(self):
        r = aggregate(make_tech(audio_metrics={"sample_rate": 24000}), make_sem({"lip_sync": 90}), threshold=60.0)  # noqa: E501
        assert r.dimensions["lip_sync"] == 60

    def test_lipsync_kept_when_has_audio(self):
        r = aggregate(make_tech(), make_sem({"lip_sync": 85}), threshold=60.0)
        assert r.dimensions["lip_sync"] == 85

    def test_compliance_hard_blocks(self):
        r = aggregate(make_tech(), make_sem({"compliance_hits": ["诱导加私信，政治敏感"]}), threshold=60.0)
        assert r.blocked is True
        assert r.compliance_detail and r.compliance_detail[0]["severity"] == "hard"

    def test_compliance_medium_penalty(self):
        t = make_tech()
        s = make_sem({"compliance": 90, "compliance_hits": ["诱导加微信"]})
        r = aggregate(t, s, threshold=60.0)
        # medium 不拦截，但 compliance 扣分
        assert r.blocked is False
        assert r.dimensions["compliance"] < 90

    def test_compliance_soft_no_penalty(self):
        r = aggregate(make_tech(), make_sem({"compliance_hits": ["轻微标题党"]}), threshold=60.0)
        assert r.blocked is False
        assert r.dimensions["compliance"] == 90

    def test_copyright_blocks(self):
        r = aggregate(make_tech(), make_sem({"copyright_hits": ["画面出现迪士尼卡通形象"]}), threshold=60.0)
        assert r.blocked is True
        assert any("版权高风险" in b for b in r.blocked_reasons)

    def test_no_semantic_dims(self):
        r = aggregate(make_tech(), {}, threshold=60.0)
        assert any("语义质检未返回" in n for n in r.notes)


class TestParseJsonObject:
    def test_bare_json(self):
        assert _parse_json_object('{"a": 1}') == {"a": 1}

    def test_code_block(self):
        out = _parse_json_object('```json\n{"a": 2}\n```')
        assert out == {"a": 2}

    def test_with_surrounding_text(self):
        out = _parse_json_object('思考过程... {"b": 3} 结尾')
        assert out == {"b": 3}

    def test_invalid(self):
        assert _parse_json_object("") is None
        assert _parse_json_object("not json at all") is None
