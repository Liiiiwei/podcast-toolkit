"""一鍵自動編排(auto.py)測試。

不實跑校對 / ffmpeg——monkeypatch proofread.run 與 autotrim.run,
專注驗證:步驟開關、鏡頭在無 speakers.json 時略過、缺字幕(rc=3)會中止。
"""
from __future__ import annotations

from podcast_toolkit import auto
from podcast_toolkit.episode import Episode


def _patch_steps(monkeypatch, calls):
    monkeypatch.setattr(auto.proofread, "run",
                        lambda *a, **k: calls.append("proofread") or 0)
    monkeypatch.setattr(auto.autotrim, "run",
                        lambda *a, **k: calls.append("trim") or {})


def test_camera_skipped_without_speakers_json(tmp_episode_dir):
    ep = Episode(tmp_episode_dir)
    msg = auto._run_camera(ep)
    assert "speakers.json" in msg and "略過" in msg     # 單軌集正常略過


def test_run_all_steps_in_order(tmp_episode_dir, monkeypatch):
    calls: list[str] = []
    _patch_steps(monkeypatch, calls)
    rc = auto.run(tmp_episode_dir)
    assert rc == 0
    assert calls == ["proofread", "trim"]               # 鏡頭略過,只跑校對+去頭尾


def test_flags_disable_steps(tmp_episode_dir, monkeypatch):
    calls: list[str] = []
    _patch_steps(monkeypatch, calls)
    auto.run(tmp_episode_dir, do_proofread=False, do_camera=False)
    assert calls == ["trim"]                            # 只剩去頭尾

    calls.clear()
    auto.run(tmp_episode_dir, do_trim=False)
    assert calls == ["proofread"]                       # 只剩校對


def test_missing_srt_aborts_with_3(tmp_episode_dir, monkeypatch):
    calls: list[str] = []
    _patch_steps(monkeypatch, calls)
    # proofread 回 3(沒 _v2.srt)→ 整條中止,不再跑去頭尾
    monkeypatch.setattr(auto.proofread, "run", lambda *a, **k: 3)
    rc = auto.run(tmp_episode_dir)
    assert rc == 3
    assert "trim" not in calls
