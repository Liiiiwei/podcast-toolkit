"""silencedetect 解析器 + 執行器測試。"""
import subprocess

import pytest

from podcast_toolkit import silencedetect


# ─── 解析 ffmpeg stderr ─────────────────────────────────────────────


def test_parse_head_silence_returns_seconds_for_leading_silence():
    stderr = (
        "[silencedetect @ 0x111] silence_start: 0\n"
        "[silencedetect @ 0x111] silence_end: 2.5 | silence_duration: 2.5\n"
        "[silencedetect @ 0x111] silence_start: 30\n"
        "[silencedetect @ 0x111] silence_end: 31.2 | silence_duration: 1.2\n"
    )
    assert silencedetect.parse_head_silence(stderr) == pytest.approx(2.5)


def test_parse_head_silence_accepts_small_offset():
    """ffmpeg 偶爾回 silence_start: 0.001234 也算開頭靜音。"""
    stderr = (
        "[silencedetect @ 0x111] silence_start: 0.001234\n"
        "[silencedetect @ 0x111] silence_end: 1.8 | silence_duration: 1.799\n"
    )
    assert silencedetect.parse_head_silence(stderr) == pytest.approx(1.8)


def test_parse_head_silence_returns_zero_when_no_leading_silence():
    """第一段 silence_start 不在 0 附近 → 開頭非靜音，回 0.0。"""
    stderr = (
        "[silencedetect @ 0x111] silence_start: 30\n"
        "[silencedetect @ 0x111] silence_end: 31.2 | silence_duration: 1.2\n"
    )
    assert silencedetect.parse_head_silence(stderr) == 0.0


def test_parse_head_silence_returns_zero_for_empty_stderr():
    assert silencedetect.parse_head_silence("") == 0.0


# ─── detect_head_silence 與 ffmpeg subprocess 整合 ─────────────


def test_detect_head_silence_invokes_ffmpeg(monkeypatch, tmp_path):
    """確認跑了 ffmpeg + silencedetect filter，並回傳解析結果。"""
    fake_stderr = (
        "[silencedetect @ 0x222] silence_start: 0\n"
        "[silencedetect @ 0x222] silence_end: 3.4 | silence_duration: 3.4\n"
    )

    called = {}

    def fake_run(cmd, capture_output, text, timeout):
        called["cmd"] = cmd
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="", stderr=fake_stderr
        )

    monkeypatch.setattr(silencedetect.subprocess, "run", fake_run)

    media = tmp_path / "fake.mp4"
    media.write_bytes(b"x")
    result = silencedetect.detect_head_silence(media)

    assert result == pytest.approx(3.4)
    assert called["cmd"][0] == "ffmpeg"
    assert "silencedetect=" in " ".join(called["cmd"])
    assert str(media) in called["cmd"]


def test_detect_head_silence_raises_when_ffmpeg_missing(monkeypatch, tmp_path):
    def fake_run(*a, **kw):
        raise FileNotFoundError("no ffmpeg")

    monkeypatch.setattr(silencedetect.subprocess, "run", fake_run)
    media = tmp_path / "fake.mp4"
    media.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="ffmpeg"):
        silencedetect.detect_head_silence(media)
