"""FastAPI 五條路由的整合測試。"""
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from podcast_toolkit.episode import Episode
from podcast_toolkit.web.api import build_app


@pytest.fixture
def client(tmp_episode_dir: Path):
    # 放一個假 main_video，讓 /api/video 有東西讀
    main_video = tmp_episode_dir / "01_母帶" / "測試集.mp4"
    main_video.write_bytes(b"FAKE" * 1000)

    ep = Episode(tmp_episode_dir)
    app = build_app(ep, shutdown=lambda: None)
    return TestClient(app)


def test_get_root_serves_index_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "<html" in r.text.lower()


def test_get_episode_returns_state(client):
    r = client.get("/api/episode")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "測試集"
    assert isinstance(body["cards"], list)
    assert body["crop_yt"] is None
    assert body["crop_reels"] is None


def test_get_video_with_range_returns_206(client):
    r = client.get("/api/video", headers={"Range": "bytes=0-10"})
    assert r.status_code == 206
    assert "bytes 0-10/" in r.headers["content-range"]


def test_post_save_writes_files_and_keeps_server_alive(client, tmp_episode_dir):
    """/api/save 只儲存,不關 server (使用者按完還要接著按合成)。"""
    called = {"n": 0}
    from podcast_toolkit.web.api import build_app
    ep = Episode(tmp_episode_dir)
    app = build_app(ep, shutdown=lambda: called.__setitem__("n", called["n"] + 1))
    c = TestClient(app)
    r = c.post(
        "/api/save",
        json={"crop": None, "deletions": [3], "cards": []},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    import yaml
    data = yaml.safe_load((tmp_episode_dir / "episode.yaml").read_text(encoding="utf-8"))
    assert data["deletions"] == [3]
    import time
    time.sleep(0.5)
    assert called["n"] == 0, "save 不應該觸發 shutdown(只有 /api/shutdown 才關 server)"


def test_post_shutdown_calls_callback(client, tmp_episode_dir):
    called = {"n": 0}
    from podcast_toolkit.web.api import build_app
    ep = Episode(tmp_episode_dir)
    app = build_app(ep, shutdown=lambda: called.__setitem__("n", called["n"] + 1))
    c = TestClient(app)
    r = c.post("/api/shutdown")
    assert r.status_code == 204
    import time
    time.sleep(0.5)
    assert called["n"] == 1


def test_pump_progress_renames_tmp_to_out_on_success(monkeypatch, tmp_path):
    """ffmpeg 結束碼 0 且 tmp_out 存在 → rename 到 out。"""
    from podcast_toolkit.web import assemble_job

    tmp_out = tmp_path / ".final.mp4.tmp"
    tmp_out.write_bytes(b"fake video bytes")
    out = tmp_path / "final.mp4"

    class FakeProc:
        stdout = iter(["progress=end\n"])
        stderr = type("S", (), {"read": lambda self: ""})()
        def wait(self): return 0

    assemble_job._pump_progress(FakeProc(), total_dur=10.0,
                                 out_path=out, tmp_out=tmp_out)
    assert out.exists()
    assert not tmp_out.exists()


def test_pump_progress_does_not_overwrite_on_failure(tmp_path):
    """ffmpeg 失敗 → tmp_out 砍掉、out 保持原狀。"""
    from podcast_toolkit.web import assemble_job

    out = tmp_path / "final.mp4"
    out.write_bytes(b"existing good output")
    tmp_out = tmp_path / ".final.mp4.tmp"
    tmp_out.write_bytes(b"half-baked output")

    class FakeProc:
        stdout = iter([])
        stderr = type("S", (), {"read": lambda self: "ffmpeg crashed"})()
        def wait(self): return 1

    assemble_job._pump_progress(FakeProc(), total_dur=10.0,
                                 out_path=out, tmp_out=tmp_out)
    assert out.read_bytes() == b"existing good output"
    assert not tmp_out.exists()  # tmp 要被清掉


def test_start_job_with_two_targets_queues_both(monkeypatch, tmp_episode_full):
    """start_job 接 ['yt', 'reels'] → 兩個都進 queue。"""
    from podcast_toolkit.web import assemble_job
    from podcast_toolkit.episode import Episode

    class _FakeProc:
        stdout = iter([])
        stderr = None
        def wait(self): return 0

    spawned = []
    monkeypatch.setattr(assemble_job, "Popen",
                        lambda *a, **k: spawned.append(a[0]) or _FakeProc())
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)

    ep = Episode(tmp_episode_full)
    # 確保 state 是 idle
    assemble_job._STATE["state"] = "idle"

    info = assemble_job.start_job(ep, targets=["yt", "reels"], force=True)

    state = assemble_job.get_status()
    assert state["queue"] == ["yt", "reels"]
    assert state["current"] == "yt"
    assert state["index"] == 0
    assert state["total"] == 2


def test_assemble_endpoint_requires_targets(client):
    r = client.post("/api/assemble", json={"force": True})
    assert r.status_code == 400
    assert "targets" in r.json()["detail"]


def test_assemble_endpoint_with_yt_reels(client, monkeypatch):
    from podcast_toolkit.web import assemble_job
    monkeypatch.setattr(assemble_job, "start_job",
                        lambda ep, targets, force: {
                            "targets": targets,
                            "out_paths": [f"/fake/{t}.mp4" for t in targets],
                        })
    r = client.post("/api/assemble",
                    json={"targets": ["yt", "reels"], "force": True})
    assert r.status_code == 200
    assert r.json()["targets"] == ["yt", "reels"]
    assert len(r.json()["out_paths"]) == 2


def test_list_episode_files_classifies_by_kind(tmp_episode_full):
    """_list_episode_files 對每個檔案標 kind + 字幕角色。"""
    from podcast_toolkit.web.api import _list_episode_files

    # 補出測試需要的檔案（fixture 沒有的）
    NAME = "測試集"
    (tmp_episode_full / "01_母帶" / f"{NAME}.srt").write_text("", encoding="utf-8")
    (tmp_episode_full / "02_片頭片尾" / "intro.mp4").write_bytes(b"")
    (tmp_episode_full / "03_成品" / f"{NAME}_YT完整版.mp4").write_bytes(b"")
    (tmp_episode_full / "03_成品" / f"{NAME}_Reels.mp4").write_bytes(b"")
    (tmp_episode_full / "04_工作檔" / "switch_list.json").write_text("[]", encoding="utf-8")

    files = _list_episode_files(tmp_episode_full)
    by_path = {f["path"]: f for f in files}

    # 主影片在 01_母帶/{name}.mp4
    assert by_path[f"01_母帶/{NAME}.mp4"]["kind"] == "main_video"
    # 主字幕（原始 _v1）也在 01_母帶
    raw = by_path[f"01_母帶/{NAME}.srt"]
    assert raw["kind"] == "subtitle"
    assert raw["is_main_srt_backup"] is True
    # _v2 在 03_成品
    v2 = by_path[f"03_成品/{NAME}_final_v2.srt"]
    assert v2["kind"] == "subtitle"
    assert v2["is_active_srt"] is True
    # 合成輸出
    assert by_path[f"03_成品/{NAME}_YT完整版.mp4"]["kind"] == "composite"
    assert by_path[f"03_成品/{NAME}_Reels.mp4"]["kind"] == "composite"
    # 片頭片尾
    assert by_path["02_片頭片尾/intro.mp4"]["kind"] == "intro_outro"
    # 工作檔
    assert by_path["04_工作檔/switch_list.json"]["kind"] == "work"


def test_flag_suspicious_pause_marks_three_rules():
    """三條規則各自要能命中對應的卡。"""
    from podcast_toolkit.web.episode_io import _flag_suspicious_pause

    cards = [
        # 0: 正常句子，長度 8、時長 4.2、沒前一張 → 不可疑
        {"idx": 1, "start": 0.0,  "end": 4.2,  "text": "大家好歡迎來到我愛上班"},
        # 1: reaction_only：text 是 "對"
        {"idx": 2, "start": 4.2,  "end": 5.0,  "text": "對"},
        # 2: short_long：1 個字，持續 3 秒（>2.0）
        {"idx": 3, "start": 5.0,  "end": 8.0,  "text": "啊"},
        # 3: big_gap_before：距上一張 2 秒（>1.5）
        {"idx": 4, "start": 10.0, "end": 12.0, "text": "我們繼續講剛剛的話題"},
        # 4: 完全正常
        {"idx": 5, "start": 12.0, "end": 16.0, "text": "這集會講到產品設計"},
    ]
    sus_cfg = {
        "short_long_max_chars": 3,
        "short_long_min_dur_sec": 2.0,
        "big_gap_min_sec": 1.5,
    }
    reactions = ["對", "嗯", "哈哈哈"]

    _flag_suspicious_pause(cards, sus_cfg, reactions)

    assert cards[0]["suspicious_pause"] is False
    assert cards[1]["suspicious_pause"] is True
    assert "reaction_only" in cards[1]["suspicious_reasons"]
    assert cards[2]["suspicious_pause"] is True
    assert "short_long" in cards[2]["suspicious_reasons"]
    assert cards[3]["suspicious_pause"] is True
    assert "big_gap_before" in cards[3]["suspicious_reasons"]
    assert cards[4]["suspicious_pause"] is False


def test_get_episode_returns_suspicious_pause_per_card(tmp_episode_dir):
    """整合測試：/api/episode 回的每張卡都要帶 suspicious_pause 欄位。"""
    from podcast_toolkit.web.api import build_app
    # 覆寫 _v2.srt 塞進一張 reaction_only 卡
    srt = (
        "1\n00:00:00,000 --> 00:00:04,000\n大家好歡迎收聽\n\n"
        "2\n00:00:04,000 --> 00:00:05,000\n對\n\n"
        "3\n00:00:05,000 --> 00:00:10,000\n繼續講下一段\n"
    )
    (tmp_episode_dir / "03_成品" / "測試集_final_v2.srt").write_text(
        srt, encoding="utf-8"
    )
    ep = Episode(tmp_episode_dir)
    app = build_app(ep, shutdown=lambda: None)
    c = TestClient(app)

    r = c.get("/api/episode")
    assert r.status_code == 200
    cards = r.json()["cards"]
    assert len(cards) == 3
    # 每張卡都要有欄位
    for card in cards:
        assert "suspicious_pause" in card
        assert "suspicious_reasons" in card
    # 卡 #2 文字是 "對"（reaction_only）→ 應該被標紅
    assert cards[1]["suspicious_pause"] is True
    assert "reaction_only" in cards[1]["suspicious_reasons"]


def test_start_job_rejects_when_running(tmp_episode_full):
    from podcast_toolkit.web import assemble_job
    from podcast_toolkit.episode import Episode

    assemble_job._STATE["state"] = "running"
    try:
        with pytest.raises(RuntimeError, match="已有"):
            assemble_job.start_job(Episode(tmp_episode_full),
                                   targets=["yt"], force=True)
    finally:
        assemble_job._STATE["state"] = "idle"


def test_post_transcribe_runs_resegment_to_merge_word_level_into_sentences(
    monkeypatch, tmp_episode_dir
):
    """Grok STT 回字層 words[]，pipeline 寫到 main_srt 後要跑 resegment
    把字層合成句子層 _v2.srt。沒做 resegment 的話 _v2.srt 會變一字一段。"""
    from podcast_toolkit.web import api as api_mod
    from podcast_toolkit.web import transcribe as transcribe_mod
    from podcast_toolkit.episode import Episode

    # 主影片 stub（POST 用 path 指向它）
    main_video = tmp_episode_dir / "01_母帶" / "測試集.mp4"
    main_video.write_bytes(b"FAKE" * 1000)

    # 不要動到真實 ~/.podcast-toolkit/config.json
    monkeypatch.setattr(api_mod, "_load_config", lambda: {"xai_api_key": "fake"})

    # 模擬 Grok：一個字一張 card 寫到 out_srt（看起來像現在線上 bug）
    def fake_pipeline(*, api_key, src_audio, out_srt, work_dir):
        out_srt.parent.mkdir(parents=True, exist_ok=True)
        chars = "大家好歡迎來到我愛上班今天要聊的是過嗨乳牛"
        lines = []
        for i, ch in enumerate(chars, 1):
            start_ms = (i - 1) * 200
            end_ms = i * 200
            sh, sm, ss, sms = (start_ms // 3600000, (start_ms // 60000) % 60,
                               (start_ms // 1000) % 60, start_ms % 1000)
            eh, em, es, ems = (end_ms // 3600000, (end_ms // 60000) % 60,
                               (end_ms // 1000) % 60, end_ms % 1000)
            lines.append(
                f"{i}\n{sh:02d}:{sm:02d}:{ss:02d},{sms:03d} --> "
                f"{eh:02d}:{em:02d}:{es:02d},{ems:03d}\n{ch}\n"
            )
        out_srt.write_text("\n".join(lines), encoding="utf-8")
        return out_srt
    monkeypatch.setattr(transcribe_mod, "run_grok_pipeline", fake_pipeline)

    ep = Episode(tmp_episode_dir)
    app = api_mod.build_app(ep, shutdown=lambda: None)
    c = TestClient(app)

    r = c.post("/api/transcribe", json={"path": "01_母帶/測試集.mp4"})
    assert r.status_code == 200, r.text

    # 字層原稿要落在 main_srt
    main_srt = tmp_episode_dir / "01_母帶" / "測試集.srt"
    assert main_srt.exists(), "Grok 原稿沒寫到 main_srt"

    # _v2.srt 要是句子層：每張 card 應該明顯多於 1 字
    from podcast_toolkit import srt_io
    v2 = tmp_episode_dir / "03_成品" / "測試集_final_v2.srt"
    cards = srt_io.parse(v2.read_text(encoding="utf-8"))
    assert len(cards) >= 1
    avg_len = sum(len(c["text"]) for c in cards) / len(cards)
    assert avg_len >= 3, (
        f"_v2.srt 看起來沒跑 resegment：平均每張卡只有 {avg_len:.1f} 字"
    )
