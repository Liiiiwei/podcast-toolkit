"""A4: STT 供應商切換（xAI + Gemini）

設計：
- transcribe.run_pipeline(*, provider, api_key, src_audio, out_srt, work_dir, progress)
  根據 provider 分流到 run_grok_pipeline / run_gemini_pipeline
- transcribe.PROVIDERS = {"xai": ..., "gemini": ...}
- transcribe_job.start_job(ep, *, src_rel, provider, api_key) 把 provider 傳下去
- /api/config GET 回 has_xai_api_key / has_gemini_api_key / provider
- /api/config POST 接 xai_api_key / gemini_api_key / provider
- /api/transcribe 讀 cfg.provider 決定走哪個 provider
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from podcast_toolkit.episode import Episode
from podcast_toolkit.web import transcribe as transcribe_mod
from podcast_toolkit.web import transcribe_job
from podcast_toolkit.web.api import build_app


# ---------- transcribe.run_pipeline 分流 ----------

def test_run_pipeline_dispatches_to_xai(tmp_path, monkeypatch):
    """provider="xai" → 呼叫 run_grok_pipeline。"""
    called = {}

    def fake_grok(*, api_key, src_audio, out_srt, work_dir, progress=None, **_):
        called["provider"] = "xai"
        called["api_key"] = api_key
        return out_srt

    monkeypatch.setitem(transcribe_mod.PROVIDERS, "xai", fake_grok)

    src = tmp_path / "in.mp3"
    src.write_bytes(b"x")
    out = tmp_path / "out.srt"

    transcribe_mod.run_pipeline(
        provider="xai",
        api_key="K1",
        src_audio=src,
        out_srt=out,
        work_dir=tmp_path,
    )
    assert called == {"provider": "xai", "api_key": "K1"}


def test_run_pipeline_dispatches_to_gemini(tmp_path, monkeypatch):
    """provider="gemini" → 呼叫 run_gemini_pipeline。"""
    called = {}

    def fake_gemini(*, api_key, src_audio, out_srt, work_dir, progress=None, **_):
        called["provider"] = "gemini"
        called["api_key"] = api_key
        return out_srt

    monkeypatch.setitem(transcribe_mod.PROVIDERS, "gemini", fake_gemini)

    src = tmp_path / "in.mp3"
    src.write_bytes(b"x")
    out = tmp_path / "out.srt"

    transcribe_mod.run_pipeline(
        provider="gemini",
        api_key="K2",
        src_audio=src,
        out_srt=out,
        work_dir=tmp_path,
    )
    assert called == {"provider": "gemini", "api_key": "K2"}


def test_run_pipeline_rejects_unknown_provider(tmp_path):
    src = tmp_path / "in.mp3"
    src.write_bytes(b"x")
    out = tmp_path / "out.srt"

    with pytest.raises(transcribe_mod.TranscribeError):
        transcribe_mod.run_pipeline(
            provider="claude-stt",  # 不存在
            api_key="K",
            src_audio=src,
            out_srt=out,
            work_dir=tmp_path,
        )


# ---------- transcribe_job 帶 provider ----------

def _wait_until_state(target: str, timeout_s: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        s = transcribe_job.get_status()
        if s["state"] == target:
            return s
        time.sleep(0.02)
    raise AssertionError(f"等不到 state={target}：{transcribe_job.get_status()}")


def test_start_job_passes_provider_to_pipeline(monkeypatch, tmp_episode_dir):
    """transcribe_job.start_job(provider=...) 要把 provider 傳給 run_pipeline。"""
    from podcast_toolkit import resegment

    transcribe_job._reset()
    captured = {}

    def fake_dispatch(*, provider, api_key, src_audio, out_srt, work_dir, progress=None, **_):
        captured["provider"] = provider
        captured["api_key"] = api_key
        out_srt.parent.mkdir(parents=True, exist_ok=True)
        out_srt.write_text("", encoding="utf-8")
        return out_srt

    monkeypatch.setattr(transcribe_mod, "run_pipeline", fake_dispatch)
    monkeypatch.setattr(resegment, "run", lambda *_a, **_k: 0)

    src = tmp_episode_dir / "01_母帶" / "測試集.mp4"
    src.write_bytes(b"FAKE")

    ep = Episode(tmp_episode_dir)
    transcribe_job.start_job(
        ep,
        src_rel="01_母帶/測試集.mp4",
        provider="gemini",
        api_key="G_KEY",
    )
    _wait_until_state("done")
    assert captured == {"provider": "gemini", "api_key": "G_KEY"}


# ---------- /api/config ----------

@pytest.fixture
def app_client(tmp_episode_dir):
    main_video = tmp_episode_dir / "01_母帶" / "測試集.mp4"
    main_video.write_bytes(b"FAKE")
    ep = Episode(tmp_episode_dir)
    app = build_app(ep, shutdown=lambda: None)
    return TestClient(app)


def test_get_config_returns_both_keys_and_provider(monkeypatch, app_client):
    """GET /api/config 回 has_xai_api_key / has_gemini_api_key / provider。"""
    from podcast_toolkit.web import api as api_mod
    monkeypatch.setattr(api_mod, "_load_config", lambda: {
        "xai_api_key": "x",
        "gemini_api_key": "g",
        "transcribe": {"provider": "gemini"},
    })
    r = app_client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert body["has_xai_api_key"] is True
    assert body["has_gemini_api_key"] is True
    assert body["provider"] == "gemini"


def test_get_config_provider_defaults_to_xai(monkeypatch, app_client):
    from podcast_toolkit.web import api as api_mod
    monkeypatch.setattr(api_mod, "_load_config", lambda: {})
    body = app_client.get("/api/config").json()
    assert body["has_xai_api_key"] is False
    assert body["has_gemini_api_key"] is False
    assert body["provider"] == "xai"


def test_post_config_saves_gemini_key_and_provider(monkeypatch, app_client):
    saved = {}
    from podcast_toolkit.web import api as api_mod
    monkeypatch.setattr(api_mod, "_load_config", lambda: {})
    monkeypatch.setattr(api_mod, "_save_config", lambda d: saved.update(d))

    r = app_client.post("/api/config", json={
        "gemini_api_key": "g-NEW",
        "provider": "gemini",
    })
    assert r.status_code == 200
    assert saved.get("gemini_api_key") == "g-NEW"
    assert saved.get("transcribe", {}).get("provider") == "gemini"
    body = r.json()
    assert body["has_gemini_api_key"] is True
    assert body["provider"] == "gemini"


def test_post_config_rejects_invalid_provider(monkeypatch, app_client):
    from podcast_toolkit.web import api as api_mod
    monkeypatch.setattr(api_mod, "_load_config", lambda: {})
    monkeypatch.setattr(api_mod, "_save_config", lambda d: None)
    r = app_client.post("/api/config", json={"provider": "claude-stt"})
    assert r.status_code == 400


# ---------- /api/transcribe with provider ----------

def test_post_transcribe_uses_configured_gemini_provider(
    monkeypatch, tmp_episode_dir
):
    """cfg.transcribe.provider == "gemini" → 用 gemini_api_key 起 job。"""
    from podcast_toolkit.web import api as api_mod
    from podcast_toolkit.web import transcribe_job as job_mod

    job_mod._reset()

    main_video = tmp_episode_dir / "01_母帶" / "測試集.mp4"
    main_video.write_bytes(b"FAKE")

    monkeypatch.setattr(api_mod, "_load_config", lambda: {
        "gemini_api_key": "G_KEY",
        "transcribe": {"provider": "gemini"},
    })

    captured = {}

    def fake_start(ep, *, src_rel, provider, api_key, **_):
        captured["provider"] = provider
        captured["api_key"] = api_key
        return {"src_path": src_rel}

    monkeypatch.setattr(job_mod, "start_job", fake_start)

    ep = Episode(tmp_episode_dir)
    app = api_mod.build_app(ep, shutdown=lambda: None)
    c = TestClient(app)
    r = c.post("/api/transcribe", json={"path": "01_母帶/測試集.mp4"})
    assert r.status_code == 202, r.text
    assert captured == {"provider": "gemini", "api_key": "G_KEY"}


def test_post_transcribe_rejects_when_selected_provider_key_missing(
    monkeypatch, tmp_episode_dir
):
    """選 gemini 卻沒設 gemini_api_key → 400。"""
    from podcast_toolkit.web import api as api_mod
    from podcast_toolkit.web import transcribe_job as job_mod
    job_mod._reset()

    main_video = tmp_episode_dir / "01_母帶" / "測試集.mp4"
    main_video.write_bytes(b"FAKE")

    monkeypatch.setattr(api_mod, "_load_config", lambda: {
        "xai_api_key": "x-only",
        "transcribe": {"provider": "gemini"},
    })

    ep = Episode(tmp_episode_dir)
    app = api_mod.build_app(ep, shutdown=lambda: None)
    c = TestClient(app)
    r = c.post("/api/transcribe", json={"path": "01_母帶/測試集.mp4"})
    assert r.status_code == 400
    assert "Gemini" in r.text or "gemini" in r.text
