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
    # 進階使用者手改 config 選雲端且有 key → 原樣回傳（不強制收斂）
    assert body["provider"] == "gemini"


def test_get_config_cloud_provider_without_key_collapses_to_breeze(
    monkeypatch, app_client
):
    # 零雲端金鑰：選了雲端 provider 但沒設 key → 不可用，收斂成本地 breeze
    from podcast_toolkit.web import api as api_mod
    monkeypatch.setattr(api_mod, "_load_config", lambda: {
        "transcribe": {"provider": "gemini"},
    })
    body = app_client.get("/api/config").json()
    assert body["provider"] == "breeze"


def test_get_config_provider_defaults_to_breeze(monkeypatch, app_client):
    # 零雲端金鑰：空 config 預設 provider = 本地 breeze
    from podcast_toolkit.web import api as api_mod
    monkeypatch.setattr(api_mod, "_load_config", lambda: {})
    body = app_client.get("/api/config").json()
    assert body["has_xai_api_key"] is False
    assert body["has_gemini_api_key"] is False
    assert body["provider"] == "breeze"


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


# ---------- 單軌 Gemini 兜底清理 ----------

def test_clean_gemini_words_strips_punct_and_hallucination():
    from podcast_toolkit.web.transcribe import _clean_gemini_words

    words = [
        {"start": 0.0, "end": 2.0, "text": "大家好，歡迎收聽！"},
        {"start": 2.0, "end": 4.0, "text": "。。。"},          # 全標點 → 變空 → 丟掉
        {"start": 100.0, "end": 102.0, "text": "幻覺尾巴"},     # 超過 60s*1.05
    ]
    out = _clean_gemini_words(words, duration_sec=60.0)
    assert len(out) == 1
    assert out[0]["text"] == "大家好 歡迎收聽"  # 標點→空格、收斂、去頭尾


def test_clean_gemini_words_no_duration_keeps_tail():
    from podcast_toolkit.web.transcribe import _clean_gemini_words

    words = [{"start": 9999.0, "end": 10000.0, "text": "尾巴"}]
    assert _clean_gemini_words(words, duration_sec=None) == words


def test_glossary_lines_shared_between_prompts():
    """單軌與分軌 prompt 的詞庫條列必須同一份渲染。"""
    from podcast_toolkit.gemini_subtitle import build_prompt, format_glossary_lines
    from podcast_toolkit.web.transcribe import build_gemini_prompt

    glossary = [{"canonical": "立崴", "sounds_like": ["立偉"], "note": "主持人"}]
    line = format_glossary_lines(glossary)[0]
    assert "必須寫成「立崴」" in line and "立偉" in line
    assert line in build_gemini_prompt(None, glossary=glossary)
    assert line in build_prompt({}, glossary)


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


# ---------- _unwrap_mod60_times 對「真實跨度 > 60s」的 entry 會掉分 ----------
#
# 背景：mod60 解包假設「每筆 entry 的真實跨度 < 60s」，所以 end 的 wrap 只要
# 補到第一個 >= start 的值就停（while e < s: e += 60）。但 Gemini 在長音檔上
# 會違反 prompt 的「每句 15-30 字」要求、回傳數百字的巨型 entry——這種 entry
# 真實跨度動輒 100-200s，end 被 mod60 包了「不只一圈」，解包只補一圈（甚至零圈），
# 整數分鐘就被吃掉。實測 episode「過嗨乳牛3」的 Stereo Mix.wav 長 2272.7s，
# 字幕卻在 2036.8s 就結束（差約 4 分鐘），就是這個累積掉分。


def test_unwrap_mod60_long_span_loses_a_minute():
    """BUG（characterization）：真實跨度 90s 的 entry 被 mod60 包成 end=40，
    解包後只還原成 30s——掉了整整一分鐘。

    真實 [10, 100]（跨度 90s）→ Gemini mod60 回 start=10, end=100%60=40。
    `while e < s` 看到 40 > 10 直接不補，end 停在 40。此測試釘住現行錯誤行為，
    修好後這裡會變號，請連同 xfail 版本一起更新。"""
    from podcast_toolkit.web.transcribe import _unwrap_mod60_times

    out = _unwrap_mod60_times([{"start": 10.0, "end": 40.0, "text": "x"}])
    assert out[0]["start"] == 10.0
    assert out[0]["end"] == 40.0           # 應為 100.0；掉了 60s
    assert out[0]["end"] - out[0]["start"] == 30.0   # 真實跨度 90s


def test_unwrap_mod60_full_minute_span_collapses_to_zero():
    """BUG（characterization）：真實跨度剛好 60s 時 end%60 == start，
    `while e < s` 連一圈都不補（e == s 不成立 e < s），跨度塌成 0。"""
    from podcast_toolkit.web.transcribe import _unwrap_mod60_times

    out = _unwrap_mod60_times([{"start": 10.0, "end": 10.0, "text": "x"}])
    assert out[0]["end"] - out[0]["start"] == 0.0    # 真實跨度 60s → 塌成 0


def test_unwrap_mod60_degenerate_repeats_reproduce_production_ladder():
    """BUG（characterization）：Gemini 在這段音檔上其實是「**重複迴圈**」——把同一筆
    (text, start, end) 一字不差地吐了 33 次（這是 LLM decoding loop，不是真實長語音）。
    33 筆 entry 的 raw time 完全相同，本該一眼可辨（互相重疊），但 start-wrap 啟發法
    （s+30 < last_end → +60）把它們無中生有攤成一條精準 60s 步進的階梯，
    讓重複內容偽裝成 56.84s→2022.5s 的「正常時間軸」，反而把重複藏了起來。

    這正是 03_成品/過嗨乳牛3_final.*.bak.srt 裡 33 個 run 的成因：每個 run 文字
    逐字相同（697 字）、starts = 56.84, 116.84, 176.84 …（步進 60.00）、
    spans 全部 = 45.66s、隱含語速 15+ 字/秒（中文對話約 3-5 字/秒，物理不可能）。
    下游 _dedup_overlapping_times 救不了，因為它跑在 unwrap 之後、時間已被攤開。"""
    from podcast_toolkit.web.transcribe import _unwrap_mod60_times

    # Gemini 重複迴圈：6 筆 entry 全回同一組 (text, 56.84, 42.50)；42.50 = 102.50 % 60
    words = [{"start": 56.84, "end": 42.50, "text": "乳" * 697} for _ in range(6)]
    out = _unwrap_mod60_times(words)

    starts = [round(w["start"], 2) for w in out]
    assert starts == [56.84, 116.84, 176.84, 236.84, 296.84, 356.84]  # 步進 60.00
    # 文字逐字相同（重複迴圈的獨立證據，與時間碼無關）卻被攤成遞增時間軸
    assert len({w["text"] for w in out}) == 1
    for w in out:
        assert round(w["end"] - w["start"], 2) == 45.66               # 窗全部一樣寬
        rate = len(w["text"]) / (w["end"] - w["start"])
        assert rate > 15.0                                            # 物理不可能的語速


@pytest.mark.xfail(
    strict=True,
    reason="_unwrap_mod60_times 尚未用文字長度/音檔長度當錨點校正掉分；"
    "修好後移除本 marker。詳見上方 BUG 區塊。",
)
def test_unwrap_mod60_reconstruction_should_not_imply_impossible_speech_rate():
    """期望（fix-agnostic）：解包後沒有任何 entry 的隱含語速超過物理上限。

    中文 podcast 約 3-5 字/秒；放寬到 8 字/秒當「絕對不可能」上界。任何可接受的
    修法（用文字長度反推跨度 / 用下一條 start 補 end / 整段按字數重攤）都該讓
    隱含語速落在這條線下。現行版本對 720 字 entry 給出 15.8 字/秒 → 失敗。"""
    from podcast_toolkit.web.transcribe import _unwrap_mod60_times

    words = [{"start": 56.84, "end": 42.50, "text": "乳" * 720} for _ in range(6)]
    out = _unwrap_mod60_times(words)

    worst = max(len(w["text"]) / max(w["end"] - w["start"], 1e-6) for w in out)
    assert worst <= 8.0
