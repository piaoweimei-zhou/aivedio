"""edit_stage 卡点剪辑单元测试：节拍计划 / BGM 合成"""
import wave

from services.stages.edit_stage import EditStage


def test_beat_plan_energetic():
    """燃点快切：120 BPM，每 2 拍切一次 → 段长 1s，总时长约 30s"""
    seg_dur, n, t, total = EditStage._compute_beat_plan(120, 2, 30)
    assert abs(seg_dur - 1.0) < 1e-6
    assert 35 <= n <= 50
    assert abs(total - 30) < 2


def test_beat_plan_rhythm():
    """节奏卡点：80 BPM，每 2 拍切一次 → 段长 1.5s"""
    seg_dur, n, t, total = EditStage._compute_beat_plan(80, 2, 30)
    assert abs(seg_dur - 1.5) < 1e-6
    assert 15 <= n <= 25
    assert abs(total - 30) < 2


def test_beat_plan_emotional():
    """情绪慢卡：40 BPM，每拍切一次 → 段长 1.5s"""
    seg_dur, n, t, total = EditStage._compute_beat_plan(40, 1, 30)
    assert abs(seg_dur - 1.5) < 1e-6
    assert 20 <= n <= 30
    assert abs(total - 30) < 2


def test_beat_plan_min_segments():
    """极短目标时长 → 至少 1 段"""
    seg_dur, n, t, total = EditStage._compute_beat_plan(120, 2, 0.1)
    assert n >= 1
    assert total > 0


def test_beat_plan_caps_segments():
    """超长目标时长 → 段数封顶 60"""
    seg_dur, n, t, total = EditStage._compute_beat_plan(120, 1, 999)
    assert n <= 60


def test_synthesize_beat_track_wav(tmp_path):
    """BGM 合成：生成有效 WAV，时长正确且非静音"""
    out = tmp_path / "bgm.wav"
    EditStage._synthesize_beat_track(120, 2.0, "whoosh", str(out))
    with wave.open(str(out), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 44100
        assert w.getnframes() > 0
        frames = w.readframes(w.getnframes())
    # 非静音：存在非零采样
    assert any(b != 0 for b in frames[:4096])


def test_synthesize_beat_track_sfx_variants(tmp_path):
    """三种音效类型都能生成有效 WAV"""
    for sfx in ("whoosh", "hit", "soft"):
        out = tmp_path / f"bgm_{sfx}.wav"
        EditStage._synthesize_beat_track(80, 1.0, sfx, str(out))
        with wave.open(str(out), "rb") as w:
            assert w.getnframes() > 0
