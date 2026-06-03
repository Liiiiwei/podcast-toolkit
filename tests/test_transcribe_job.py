"""轉字幕 background job 的狀態機測試。

設計：
- start_job(ep, src_rel, api_key) 立即回（不阻塞），背景跑 ffmpeg + Grok STT + resegment
- get_status() 回 {state, phase, percent, out_srt, error, started_at}
- phase: "compress" | "upload" | "resegment"
- state: "idle" | "running" | "done" | "error"
- 同一時間只允許一個 job
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from podcast_toolkit.episode import Episode
from podcast_toolkit.web import transcribe_job


@pytest.fixture(autouse=True)
def _reset_state():
    """每個 test 前後都把模組級 state 清回 idle，避免互相污染。"""
    transcribe_job._reset()
    yield
    transcribe_job._reset()


def _wait_until_state(target: str, timeout_s: float = 3.0) -> dict:
    """poll get_status() 直到 state == target，逾時就 fail。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = transcribe_job.get_status()
        if status["state"] == target:
            return status
        time.sleep(0.02)
    raise AssertionError(
        f"等不到 state={target}，目前 status={transcribe_job.get_status()}"
    )


def test_initial_state_is_idle():
    status = transcribe_job.get_status()
    assert status["state"] == "idle"
    assert status["phase"] is None
    assert status["percent"] == 0.0
    assert status["error"] is None
    assert status["out_srt"] is None


def test_start_job_sets_running_then_done(monkeypatch, tmp_episode_dir):
    """fake pipeline + resegment 都成功 → 狀態走到 done，out_srt 是 _v2.srt 相對路徑。"""
    # 主檔當輸入
    src = tmp_episode_dir / "01_母帶" / "測試集.mp4"
    src.write_bytes(b"FAKE" * 100)

    # fake pipeline：直接寫 main_srt（不真的呼叫 Grok）
    from podcast_toolkit.web import transcribe as transcribe_mod

    def fake_pipeline(*, api_key, src_audio, out_srt, work_dir, progress=None):
        out_srt.parent.mkdir(parents=True, exist_ok=True)
        out_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n哈囉\n", encoding="utf-8")
        if progress:
            progress("compress", 100.0)
            progress("upload", 100.0)
        return out_srt

    monkeypatch.setattr(
        transcribe_mod, "run_pipeline",
        lambda *, provider, **kw: fake_pipeline(**kw),
    )

    # fake resegment：直接寫 _v2.srt
    from podcast_toolkit import resegment

    def fake_resegment(_ep_dir, force=False):
        v2 = tmp_episode_dir / "03_成品" / "測試集_final_v2.srt"
        v2.write_text("1\n00:00:00,000 --> 00:00:01,000\n哈囉\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(resegment, "run", fake_resegment)

    ep = Episode(tmp_episode_dir)
    transcribe_job.start_job(
        ep, src_rel="01_母帶/測試集.mp4", provider="xai", api_key="fake"
    )

    done = _wait_until_state("done")
    assert done["phase"] == "resegment"
    assert done["percent"] == 100.0
    assert done["out_srt"] == "03_成品/測試集_final_v2.srt"
    assert done["error"] is None


def test_start_job_rejects_when_already_running(monkeypatch, tmp_episode_dir):
    """同時間只允許一個 job：第二次 start_job 要 raise。"""
    from podcast_toolkit.web import transcribe as transcribe_mod
    from podcast_toolkit import resegment

    # 讓 pipeline 卡住到我們允許它結束
    import threading

    gate = threading.Event()

    def slow_pipeline(*, api_key, src_audio, out_srt, work_dir, progress=None):
        gate.wait(timeout=2.0)
        out_srt.parent.mkdir(parents=True, exist_ok=True)
        out_srt.write_text("", encoding="utf-8")
        return out_srt

    monkeypatch.setattr(
        transcribe_mod, "run_pipeline",
        lambda *, provider, **kw: slow_pipeline(**kw),
    )
    monkeypatch.setattr(resegment, "run", lambda *_a, **_k: 0)

    src = tmp_episode_dir / "01_母帶" / "測試集.mp4"
    src.write_bytes(b"FAKE")

    ep = Episode(tmp_episode_dir)
    transcribe_job.start_job(
        ep, src_rel="01_母帶/測試集.mp4", provider="xai", api_key="fake"
    )

    # 等狀態變成 running 再嘗試第二次
    _wait_until_state("running")

    with pytest.raises(RuntimeError):
        transcribe_job.start_job(
            ep, src_rel="01_母帶/測試集.mp4", provider="xai", api_key="fake"
        )

    # 放閘讓 worker 收工，避免 test 留下 thread
    gate.set()
    _wait_until_state("done")


def test_pipeline_error_sets_error_state(monkeypatch, tmp_episode_dir):
    """pipeline raise TranscribeError → state=error，error 訊息會被記下。"""
    from podcast_toolkit.web import transcribe as transcribe_mod

    def boom(*, api_key, src_audio, out_srt, work_dir, progress=None):
        raise transcribe_mod.TranscribeError("Grok 回 401")

    monkeypatch.setattr(
        transcribe_mod, "run_pipeline",
        lambda *, provider, **kw: boom(**kw),
    )

    src = tmp_episode_dir / "01_母帶" / "測試集.mp4"
    src.write_bytes(b"FAKE")

    ep = Episode(tmp_episode_dir)
    transcribe_job.start_job(
        ep, src_rel="01_母帶/測試集.mp4", provider="xai", api_key="fake"
    )

    status = _wait_until_state("error")
    assert "Grok 回 401" in (status["error"] or "")


def test_progress_callback_advances_phase(monkeypatch, tmp_episode_dir):
    """pipeline 回報 phase=compress → upload，job state 同步更新；
    用 Event 讓 worker 在每階段卡住等 test 觀察，避免 timing race。"""
    from podcast_toolkit.web import transcribe as transcribe_mod
    from podcast_toolkit import resegment

    import threading

    in_compress = threading.Event()
    leave_compress = threading.Event()
    in_upload = threading.Event()
    leave_upload = threading.Event()

    def staged_pipeline(*, api_key, src_audio, out_srt, work_dir, progress=None):
        progress("compress", 0.0)
        in_compress.set()
        leave_compress.wait(timeout=2.0)
        progress("compress", 100.0)

        progress("upload", 0.0)
        in_upload.set()
        leave_upload.wait(timeout=2.0)
        progress("upload", 100.0)

        out_srt.parent.mkdir(parents=True, exist_ok=True)
        out_srt.write_text("", encoding="utf-8")
        return out_srt

    monkeypatch.setattr(
        transcribe_mod, "run_pipeline",
        lambda *, provider, **kw: staged_pipeline(**kw),
    )
    monkeypatch.setattr(resegment, "run", lambda *_a, **_k: 0)

    src = tmp_episode_dir / "01_母帶" / "測試集.mp4"
    src.write_bytes(b"FAKE")

    ep = Episode(tmp_episode_dir)
    transcribe_job.start_job(
        ep, src_rel="01_母帶/測試集.mp4", provider="xai", api_key="fake"
    )

    assert in_compress.wait(timeout=2.0)
    assert transcribe_job.get_status()["phase"] == "compress"
    leave_compress.set()

    assert in_upload.wait(timeout=2.0)
    assert transcribe_job.get_status()["phase"] == "upload"
    leave_upload.set()

    done = _wait_until_state("done")
    assert done["phase"] == "resegment"
