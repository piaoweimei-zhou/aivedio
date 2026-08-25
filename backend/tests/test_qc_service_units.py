"""QcService CV2 客观质检补充测试：probe/composition 各分支、技术评分逻辑

mock cv2.VideoCapture，用不同结构的合成帧覆盖构图探测的过曝/过暗/模糊等分支。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.qc import qc_service  # noqa: E402


def _mk_frame(mode="normal"):
    if mode == "bright":
        import numpy as np

        return np.full((480, 640, 3), 255, dtype=np.uint8)
    if mode == "dark":
        import numpy as np

        return np.zeros((480, 640, 3), dtype=np.uint8)
    import numpy as np

    frame = (np.random.rand(480, 640, 3) * 255).astype(np.uint8)
    # 中心显著主体块（产生梯度质心）
    frame[140:340, 220:420] = 200
    return frame


class _Cap:
    """Fake cv2.VideoCapture：按 mode 产出固定结构帧"""

    opened = True

    def __init__(self, path, mode="normal", n_frames=8):
        self.mode = mode
        self.n_frames = n_frames
        self.idx = 0

    def isOpened(self):
        return self.opened

    def get(self, prop):
        import cv2

        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return self.n_frames
        return 30.0

    def set(self, prop, idx):
        self.idx = int(idx)

    def read(self):
        if self.idx >= self.n_frames:
            return False, None
        return True, _mk_frame(self.mode)

    def release(self):
        self.opened = False


def test_composition_normal(monkeypatch):
    monkeypatch.setattr(
        qc_service.cv2, "VideoCapture", lambda p: _Cap(p, mode="normal")
    )
    out = qc_service._composition_cv_probe("v.mp4")
    m = out.get("compo_metrics") or {}
    assert m.get("sampled_frames", 0) >= 1
    assert "过曝" not in m.get("rules", []) and "过暗" not in m.get("rules", [])


def test_composition_overexposed(monkeypatch):
    monkeypatch.setattr(
        qc_service.cv2, "VideoCapture", lambda p: _Cap(p, mode="bright")
    )
    out = qc_service._composition_cv_probe("v.mp4")
    m = out.get("compo_metrics") or {}
    assert "过曝" in m.get("rules", [])


def test_composition_too_dark(monkeypatch):
    monkeypatch.setattr(
        qc_service.cv2, "VideoCapture", lambda p: _Cap(p, mode="dark")
    )
    out = qc_service._composition_cv_probe("v.mp4")
    m = out.get("compo_metrics") or {}
    assert "过暗" in m.get("rules", [])


def test_composition_no_frames(monkeypatch):
    monkeypatch.setattr(
        qc_service.cv2, "VideoCapture", lambda p: _Cap(p, n_frames=0)
    )
    out = qc_service._composition_cv_probe("v.mp4")
    assert out["composition_cv"] == 0


# ── run_technical_qc：评分扣分逻辑（mock 三个底层 probe）──────


def test_run_technical_qc_bad_video(monkeypatch):
    monkeypatch.setattr(qc_service, "_probe_video", lambda p: {"readable": False})
    monkeypatch.setattr(qc_service, "_audio_probe", lambda p: {})
    monkeypatch.setattr(qc_service, "_composition_cv_probe", lambda p: {})
    tech = qc_service.run_technical_qc("v.mp4")
    assert tech["score"] == 0
    assert any("无法解码" in i for i in tech["issues"])


def test_run_technical_qc_lowres_blur_black(monkeypatch):
    monkeypatch.setattr(
        qc_service,
        "_probe_video",
        lambda p: {
            "readable": True,
            "width": 480,
            "height": 360,
            "duration_s": 2.0,
            "avg_laplacian_blur": 10,
            "black_frame_ratio": 0.5,
            "bpp": 0.05,
        },
    )
    monkeypatch.setattr(qc_service, "_audio_probe", lambda p: {"has_audio": False})
    monkeypatch.setattr(qc_service, "_composition_cv_probe", lambda p: {})
    tech = qc_service.run_technical_qc("v.mp4")
    issues = " ".join(tech["issues"])
    assert "分辨率偏低" in issues
    assert "模糊" in issues
    assert "黑屏" in issues
    assert "码率密度过低" in issues
    assert "无音轨" in issues


def test_run_technical_qc_good_video(monkeypatch):
    monkeypatch.setattr(
        qc_service,
        "_probe_video",
        lambda p: {
            "readable": True,
            "width": 1920,
            "height": 1080,
            "duration_s": 30.0,
            "avg_laplacian_blur": 150,
            "black_frame_ratio": 0.01,
            "bpp": 0.25,
        },
    )
    monkeypatch.setattr(qc_service, "_audio_probe", lambda p: {"has_audio": True})
    monkeypatch.setattr(
        qc_service, "_composition_cv_probe", lambda p: {"composition_cv": 80}
    )
    tech = qc_service.run_technical_qc("v.mp4")
    assert tech["score"] == 100
    assert tech["issues"] == []
    assert tech.get("composition_cv") == 80
