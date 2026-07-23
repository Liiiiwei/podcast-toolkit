"""T19: 錯字字典套用 + typo_entries 一路傳到 provider / job。

設計：
- transcribe.apply_typo_dict(text, entries) — 把使用者字典套到任意字串
- run_pipeline / run_grok_pipeline 多收 typo_entries 參數
- transcribe_job.start_job 多收 typo_entries 參數
- /api/transcribe 自動讀 _load_typo_dict() 傳給 start_job
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from podcast_toolkit.episode import Episode
from podcast_toolkit.web import transcribe as transcribe_mod
from podcast_toolkit.web import transcribe_job


# ---------- apply_typo_dict ----------

def test_apply_typo_dict_replaces_wrong_with_right():
    entries = [
        {"wrong": "歐巴馬", "right": "歐巴馬總統", "note": ""},
        {"wrong": "咖啡因", "right": "咖啡因", "note": ""},
    ]
    out = transcribe_mod.apply_typo_dict("今天歐巴馬喝了咖啡因。", entries)
    assert "歐巴馬總統" in out
    assert "咖啡因" in out


def test_apply_typo_dict_empty_entries_returns_unchanged():
    text = "原文不變"
    assert transcribe_mod.apply_typo_dict(text, []) == text
    assert transcribe_mod.apply_typo_dict(text, None) == text


def test_apply_typo_dict_no_match_returns_unchanged():
    entries = [{"wrong": "不存在", "right": "替換", "note": ""}]
    assert transcribe_mod.apply_typo_dict("完全沒命中", entries) == "完全沒命中"


def test_apply_typo_dict_skips_malformed_entries():
    """缺 wrong / right 的 entry 要被略過，不會炸。"""
    entries = [
        {"wrong": "", "right": "X"},
        {"wrong": "Y", "right": ""},
        {"note": "缺 wrong / right"},
        {"wrong": "舊", "right": "新"},
    ]
    out = transcribe_mod.apply_typo_dict("這是舊的", entries)
    assert out == "這是新的"


# ---------- run_pipeline / run_grok_pipeline 多帶 typo_entries ----------

def test_run_pipeline_passes_typo_entries_to_provider(tmp_path, monkeypatch):
    captured = {}

    def fake_provider(*, api_key, src_audio, out_srt, work_dir, progress=None, typo_entries=None, glossary=None):
        captured["typo_entries"] = typo_entries
        return out_srt

    monkeypatch.setitem(transcribe_mod.PROVIDERS, "xai", fake_provider)

    src = tmp_path / "in.mp3"
    src.write_bytes(b"x")
    out = tmp_path / "out.srt"
    entries = [{"wrong": "A", "right": "B", "note": ""}]

    transcribe_mod.run_pipeline(
        provider="xai",
        api_key="K",
        src_audio=src,
        out_srt=out,
        work_dir=tmp_path,
        typo_entries=entries,
    )
    assert captured["typo_entries"] == entries


def test_run_pipeline_typo_entries_defaults_to_none(tmp_path, monkeypatch):
    """沒給 typo_entries 也能正常分流（向後相容）。"""
    captured = {}

    def fake_provider(*, api_key, src_audio, out_srt, work_dir, progress=None, typo_entries=None, glossary=None):
        captured["typo_entries"] = typo_entries
        return out_srt

    monkeypatch.setitem(transcribe_mod.PROVIDERS, "xai", fake_provider)

    src = tmp_path / "in.mp3"
    src.write_bytes(b"x")
    out = tmp_path / "out.srt"

    transcribe_mod.run_pipeline(
        provider="xai",
        api_key="K",
        src_audio=src,
        out_srt=out,
        work_dir=tmp_path,
    )
    # glossary 落地後 run_pipeline 會把 typo_entries normalize 成 list（合併 glossary→typo），
    # None 不會直透到 provider；沒給就是空 list，不是 None。
    assert not captured["typo_entries"]


# ---------- transcribe_job 多帶 typo_entries ----------

def _wait_until_state(target: str, timeout_s: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        s = transcribe_job.get_status()
        if s["state"] == target:
            return s
        time.sleep(0.02)
    raise AssertionError(f"等不到 state={target}：{transcribe_job.get_status()}")


def test_start_job_passes_typo_entries_to_pipeline(monkeypatch, tmp_episode_dir):
    from podcast_toolkit import resegment

    transcribe_job._reset()
    captured = {}

    def fake_dispatch(*, provider, api_key, src_audio, out_srt, work_dir, progress=None, typo_entries=None, glossary=None):
        captured["typo_entries"] = typo_entries
        out_srt.parent.mkdir(parents=True, exist_ok=True)
        out_srt.write_text("", encoding="utf-8")
        return out_srt

    monkeypatch.setattr(transcribe_mod, "run_pipeline", fake_dispatch)
    monkeypatch.setattr(resegment, "run", lambda *_a, **_k: 0)

    src = tmp_episode_dir / "01_母帶" / "測試集.mp4"
    src.write_bytes(b"FAKE")

    ep = Episode(tmp_episode_dir)
    entries = [{"wrong": "X", "right": "Y", "note": ""}]
    transcribe_job.start_job(
        ep,
        src_rel="01_母帶/測試集.mp4",
        provider="xai",
        api_key="K",
        typo_entries=entries,
    )
    _wait_until_state("done")
    assert captured["typo_entries"] == entries


# ---------- /api/transcribe 自動載入 typo-dict ----------

def test_post_transcribe_loads_typo_dict_and_passes_to_start_job(
    monkeypatch, tmp_episode_dir
):
    from podcast_toolkit.web import api as api_mod
    from podcast_toolkit.web import transcribe_job as job_mod

    job_mod._reset()

    main_video = tmp_episode_dir / "01_母帶" / "測試集.mp4"
    main_video.write_bytes(b"FAKE")

    monkeypatch.setattr(api_mod, "_load_config", lambda: {
        "xai_api_key": "K",
        "transcribe": {"provider": "xai"},
    })
    entries = [{"wrong": "舊", "right": "新", "note": ""}]
    monkeypatch.setattr(api_mod, "_load_typo_dict", lambda: entries)

    captured = {}

    def fake_start(ep, *, src_rel, provider, api_key, typo_entries=None, glossary=None):
        captured["typo_entries"] = typo_entries
        return {"src_path": src_rel}

    monkeypatch.setattr(job_mod, "start_job", fake_start)

    ep = Episode(tmp_episode_dir)
    app = api_mod.build_app(ep, shutdown=lambda: None)
    c = TestClient(app)
    r = c.post("/api/transcribe", json={"path": "01_母帶/測試集.mp4"})
    assert r.status_code == 202, r.text
    assert captured["typo_entries"] == entries
