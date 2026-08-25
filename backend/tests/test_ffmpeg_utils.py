"""ffmpeg_utils 纯逻辑单元测试：ffmpeg/ffprobe 路径解析"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.stages.ffmpeg_utils import _ffmpeg_bin, _ffprobe_bin  # noqa: E402


class TestFfmpegBin:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("FFMPEG_PATH", raising=False)
        monkeypatch.delenv("FFPROBE_PATH", raising=False)
        assert _ffmpeg_bin() == "ffmpeg"
        assert _ffprobe_bin() == "ffprobe"

    def test_custom_path(self, monkeypatch):
        monkeypatch.setenv("FFMPEG_PATH", r"D:\tools\ffmpeg.exe")
        monkeypatch.setenv("FFPROBE_PATH", r"D:\tools\ffprobe.exe")
        assert _ffmpeg_bin() == r"D:\tools\ffmpeg.exe"
        assert _ffprobe_bin() == r"D:\tools\ffprobe.exe"

    def test_dir_path(self, monkeypatch, tmp_path):
        # 指向目录时自动拼 exe
        monkeypatch.setenv("FFMPEG_PATH", str(tmp_path))
        assert _ffmpeg_bin() == os.path.join(str(tmp_path), "ffmpeg.exe")
