"""A4: STT 供應商切換（xAI + OpenAI + 本地 whisper_mlx）

設計：
- transcribe.run_pipeline(*, provider, api_key, src_audio, out_srt, work_dir, progress)
  根據 provider 分流到 run_grok_pipeline / run_openai_pipeline / run_whisper_mlx_pipeline
- transcribe.PROVIDERS = {"xai": ..., "openai": ..., "whisper_mlx": ...}
- transcribe_job.start_job(ep, *, src_rel, provider, api_key) 把 provider 傳下去
- /api/config GET 回 has_xai_api_key / has_openai_api_key / provider
- /api/config POST 接 xai_api_key / openai_api_key / provider
- /api/transcribe 讀 cfg.provider 決定走哪個 provider
"""
from __future__ import annotations

import time

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
        provider="xai",
        api_key="X_KEY",
    )
    _wait_until_state("done")
    assert captured == {"provider": "xai", "api_key": "X_KEY"}


# ---------- /api/config ----------

@pytest.fixture
def app_client(tmp_episode_dir):
    main_video = tmp_episode_dir / "01_母帶" / "測試集.mp4"
    main_video.write_bytes(b"FAKE")
    ep = Episode(tmp_episode_dir)
    app = build_app(ep, shutdown=lambda: None)
    return TestClient(app)


def test_get_config_returns_keys_and_provider(monkeypatch, app_client):
    """GET /api/config 回 has_xai_api_key / provider。"""
    from podcast_toolkit.web import api as api_mod
    monkeypatch.setattr(api_mod, "_load_config", lambda: {
        "xai_api_key": "x",
        "transcribe": {"provider": "xai"},
    })
    r = app_client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert body["has_xai_api_key"] is True
    # 零雲端金鑰：雲端 provider（xai）對外收斂成本地 whisper_mlx
    assert body["provider"] == "whisper_mlx"


def test_get_config_provider_defaults_to_whisper_mlx(monkeypatch, app_client):
    # 零雲端金鑰：空 config 預設 provider = 本地 whisper_mlx
    from podcast_toolkit.web import api as api_mod
    monkeypatch.setattr(api_mod, "_load_config", lambda: {})
    body = app_client.get("/api/config").json()
    assert body["has_xai_api_key"] is False
    assert body["provider"] == "whisper_mlx"


def test_post_config_saves_provider_whisper_mlx(monkeypatch, app_client):
    # 零雲端金鑰：產品路線存本地 whisper_mlx provider
    saved = {}
    from podcast_toolkit.web import api as api_mod
    monkeypatch.setattr(api_mod, "_load_config", lambda: {})
    monkeypatch.setattr(api_mod, "_save_config", lambda d: saved.update(d))

    r = app_client.post("/api/config", json={"provider": "whisper_mlx"})
    assert r.status_code == 200
    assert saved.get("transcribe", {}).get("provider") == "whisper_mlx"
    body = r.json()
    assert body["provider"] == "whisper_mlx"


def test_post_config_rejects_invalid_provider(monkeypatch, app_client):
    from podcast_toolkit.web import api as api_mod
    monkeypatch.setattr(api_mod, "_load_config", lambda: {})
    monkeypatch.setattr(api_mod, "_save_config", lambda d: None)
    r = app_client.post("/api/config", json={"provider": "claude-stt"})
    assert r.status_code == 400


# ---------- /api/transcribe with provider ----------

def test_post_transcribe_uses_configured_xai_provider(
    monkeypatch, tmp_episode_dir
):
    """cfg.transcribe.provider == "xai" → 用 xai_api_key 起 job。"""
    from podcast_toolkit.web import api as api_mod
    from podcast_toolkit.web import transcribe_job as job_mod

    job_mod._reset()

    main_video = tmp_episode_dir / "01_母帶" / "測試集.mp4"
    main_video.write_bytes(b"FAKE")

    monkeypatch.setattr(api_mod, "_load_config", lambda: {
        "xai_api_key": "X_KEY",
        "transcribe": {"provider": "xai"},
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
    assert captured == {"provider": "xai", "api_key": "X_KEY"}


def test_post_transcribe_rejects_when_selected_provider_key_missing(
    monkeypatch, tmp_episode_dir
):
    """選 openai 卻沒設 openai_api_key → 400。"""
    from podcast_toolkit.web import api as api_mod
    from podcast_toolkit.web import transcribe_job as job_mod
    job_mod._reset()

    main_video = tmp_episode_dir / "01_母帶" / "測試集.mp4"
    main_video.write_bytes(b"FAKE")

    monkeypatch.setattr(api_mod, "_load_config", lambda: {
        "xai_api_key": "x-only",
        "transcribe": {"provider": "openai"},
    })

    ep = Episode(tmp_episode_dir)
    app = api_mod.build_app(ep, shutdown=lambda: None)
    c = TestClient(app)
    r = c.post("/api/transcribe", json={"path": "01_母帶/測試集.mp4"})
    assert r.status_code == 400
    assert "OpenAI" in r.text or "openai" in r.text


# ---------- _run_cloud_stt_pipeline 共用骨架 ----------

def test_cloud_pipeline_skeleton_empty_words_raises(tmp_path, monkeypatch):
    from podcast_toolkit.web import transcribe as t

    monkeypatch.setattr(t, "_ffmpeg_compress", lambda src, dst: dst.write_bytes(b"x"))
    src = tmp_path / "in.mp3"
    src.write_bytes(b"x")
    with pytest.raises(t.TranscribeError, match="沒東西"):
        t._run_cloud_stt_pipeline(
            transcribe_fn=lambda compressed: [],
            compressed_name="_t.mp3",
            empty_msg="沒東西",
            src_audio=src,
            out_srt=tmp_path / "out.srt",
            work_dir=tmp_path,
        )


def test_cloud_pipeline_skeleton_applies_post_words_and_writes_srt(tmp_path, monkeypatch):
    from podcast_toolkit.web import transcribe as t

    monkeypatch.setattr(t, "_ffmpeg_compress", lambda src, dst: dst.write_bytes(b"x"))
    src = tmp_path / "in.mp3"
    src.write_bytes(b"x")
    out = tmp_path / "out.srt"
    phases = []

    result = t._run_cloud_stt_pipeline(
        transcribe_fn=lambda compressed: [
            {"text": "哈囉", "start": 0.0, "end": 1.0},
            {"text": "丟掉我", "start": 1.0, "end": 2.0},
        ],
        compressed_name="_t.mp3",
        empty_msg="沒東西",
        src_audio=src,
        out_srt=out,
        work_dir=tmp_path,
        progress=lambda phase, pct: phases.append((phase, pct)),
        post_words=lambda ws: [w for w in ws if w["text"] != "丟掉我"],
    )
    assert result == out
    body = out.read_text(encoding="utf-8")
    assert "哈囉" in body
    assert "丟掉我" not in body
    assert ("compress", 0.0) in phases and ("upload", 100.0) in phases


# ---------- _words_to_cards 切長 entry 的時間分配 ----------

def test_words_to_cards_long_entry_gets_allocated_times():
    """超長 entry 切成多張卡時，每張卡要分到自己的時間窗，
    不能全部共用母段 [start, end]（會在卡片層重新製造同時段重疊，
    編輯器預覽 activeCardAt / 拆卡時間排列都會錯亂）。"""
    from podcast_toolkit.web.transcribe import _words_to_cards

    text = "這是一段沒有標點的超長轉錄內容會被每三十個字硬切成多張字幕卡" * 20  # 600 字
    cards = _words_to_cards([{"text": text, "start": 56.84, "end": 102.5}])

    assert len(cards) >= 2
    # 600 字 × 0.3s > 45.66s → 比例分配貼滿整段：頭尾對齊母段、卡卡相接
    assert cards[0]["start"] == 56.84
    assert cards[-1]["end"] == 102.5
    for prev, cur in zip(cards, cards[1:]):
        assert cur["start"] == prev["end"]
        assert prev["start"] < prev["end"]
    # 不允許任兩張卡共用同一個 (start, end)
    spans = {(c["start"], c["end"]) for c in cards}
    assert len(spans) == len(cards)


def test_words_to_cards_short_entry_times_untouched():
    """沒切的卡時間要原封不動，不能被語速規則截短。"""
    from podcast_toolkit.web.transcribe import _words_to_cards

    cards = _words_to_cards([{"text": "哈囉大家好", "start": 3.2, "end": 17.7}])
    assert len(cards) == 1
    assert cards[0]["start"] == 3.2
    assert cards[0]["end"] == 17.7


def test_words_to_cards_sparse_entry_packs_from_start():
    """母段比語速 budget 長（trailing silence）→ 從 start 緊湊排，
    與 srt_io.allocate_split_times（編輯器拆卡）同一套規則。"""
    from podcast_toolkit.web.transcribe import _words_to_cards

    text = "甲" * 30 + "乙" * 10  # 40 字、兩個 chunk，budget = 12s
    cards = _words_to_cards([{"text": text, "start": 0.0, "end": 60.0}])
    assert [c["text"] for c in cards] == ["甲" * 30, "乙" * 10]
    assert cards[0]["start"] == 0.0
    assert cards[0]["end"] == pytest.approx(9.0)
    assert cards[1]["start"] == pytest.approx(9.0)
    assert cards[1]["end"] == pytest.approx(12.0)
