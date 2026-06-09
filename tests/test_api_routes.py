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
    把字層合成句子層 _v2.srt。沒做 resegment 的話 _v2.srt 會變一字一段。

    /api/transcribe 是 background job：202 立即回，要 poll /api/transcribe/status
    直到 state == done 才檢查檔案。"""
    import time as _time

    from podcast_toolkit.web import api as api_mod
    from podcast_toolkit.web import transcribe as transcribe_mod
    from podcast_toolkit.web import transcribe_job as transcribe_job_mod
    from podcast_toolkit.episode import Episode

    transcribe_job_mod._reset()

    # 主影片 stub（POST 用 path 指向它）
    main_video = tmp_episode_dir / "01_母帶" / "測試集.mp4"
    main_video.write_bytes(b"FAKE" * 1000)

    # 不要動到真實 ~/.podcast-toolkit/config.json（預設 provider 已改 gemini）
    monkeypatch.setattr(api_mod, "_load_config", lambda: {"gemini_api_key": "fake"})

    # 模擬 Grok：一個字一張 card 寫到 out_srt（看起來像現在線上 bug）
    def fake_pipeline(*, api_key, src_audio, out_srt, work_dir, progress=None, **_):
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
    monkeypatch.setattr(
        transcribe_mod, "run_pipeline",
        lambda *, provider, **kw: fake_pipeline(**kw),
    )

    ep = Episode(tmp_episode_dir)
    app = api_mod.build_app(ep, shutdown=lambda: None)
    c = TestClient(app)

    r = c.post("/api/transcribe", json={"path": "01_母帶/測試集.mp4"})
    assert r.status_code == 202, r.text

    # poll status
    deadline = _time.monotonic() + 5.0
    while _time.monotonic() < deadline:
        s = c.get("/api/transcribe/status").json()
        if s["state"] in ("done", "error"):
            break
        _time.sleep(0.05)
    assert s["state"] == "done", f"job 沒 done：{s}"

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


def test_get_transcribe_status_returns_idle_initially(client):
    """還沒跑過任何 job 時，/api/transcribe/status 回 idle。"""
    from podcast_toolkit.web import transcribe_job as transcribe_job_mod
    transcribe_job_mod._reset()

    r = client.get("/api/transcribe/status")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "idle"
    assert body["phase"] is None


def test_post_auto_align_400_when_cam_b_missing(client):
    """T23b: cameras.b 還沒在 yaml 時按自動對齊 → 400，提示使用者先存 cam B。"""
    r = client.post("/api/auto-align")
    assert r.status_code == 400
    assert "cam B" in r.json()["detail"]


def test_get_episode_reflects_cameras_b_after_save(tmp_episode_dir):
    """T23a-followup bug 防回歸：/api/save 寫入 cam_b_path 後，
    下一次 GET /api/episode 應拿到新 cameras.b（必須 reload Episode cfg）。
    沒 reload 的話 A/B toggle 不會出現。
    """
    (tmp_episode_dir / "01_母帶" / "測試集.mp4").write_bytes(b"")
    (tmp_episode_dir / "01_母帶" / "B-roll.mp4").write_bytes(b"")

    ep = Episode(tmp_episode_dir)
    app = build_app(ep, shutdown=lambda: None)
    c = TestClient(app)

    # 初始 cameras 只有 a
    initial = c.get("/api/episode").json()
    assert "b" not in (initial.get("cameras") or {})

    # POST cam B + offset
    r = c.post(
        "/api/save",
        json={
            "cam_b_path": "01_母帶/B-roll.mp4",
            "camera_sync_offset_b": 1.5,
            "cards": [],
        },
    )
    assert r.status_code == 200

    # GET 應反映新值
    state = c.get("/api/episode").json()
    assert state["cameras"].get("b") == "01_母帶/B-roll.mp4"
    assert state["camera_sync_offset"].get("b") == 1.5


# ─── A1：拖放上傳到 01_母帶/ ───────────────────────────────────────────


def test_post_upload_writes_audio_to_madai(client, tmp_episode_dir):
    """POST /api/upload 把音檔寫到 01_母帶/。"""
    files = {"file": ("note.mp3", b"FAKE_MP3_BYTES", "audio/mpeg")}
    r = client.post("/api/upload", files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["path"] == "01_母帶/note.mp3"
    assert (tmp_episode_dir / "01_母帶" / "note.mp3").read_bytes() == b"FAKE_MP3_BYTES"


def test_post_upload_writes_video_to_madai(client, tmp_episode_dir):
    """POST /api/upload 把影片寫到 01_母帶/。"""
    files = {"file": ("clip.mov", b"FAKE_MOV_BYTES", "video/quicktime")}
    r = client.post("/api/upload", files=files)
    assert r.status_code == 200, r.text
    assert (tmp_episode_dir / "01_母帶" / "clip.mov").read_bytes() == b"FAKE_MOV_BYTES"


def test_post_upload_rejects_unknown_extension(client):
    """不在 TRANSCRIBABLE_EXTS 內的副檔名 → 400。"""
    files = {"file": ("readme.txt", b"hello", "text/plain")}
    r = client.post("/api/upload", files=files)
    assert r.status_code == 400
    assert "副檔名" in r.json()["detail"]


def test_post_upload_rejects_path_traversal(client, tmp_episode_dir):
    """檔名含路徑分隔字元 → 400，且絕對不會寫到 ep.dir 之外。"""
    files = {"file": ("../evil.mp3", b"x", "audio/mpeg")}
    r = client.post("/api/upload", files=files)
    assert r.status_code == 400
    # 確認沒寫到 tmp_episode_dir 的上層
    assert not (tmp_episode_dir.parent / "evil.mp3").exists()


def test_post_upload_rejects_overwrite_existing(client, tmp_episode_dir):
    """同名檔案已存在 → 409，不覆蓋。"""
    existing = tmp_episode_dir / "01_母帶" / "dup.mp3"
    existing.write_bytes(b"OLD_CONTENT")
    files = {"file": ("dup.mp3", b"NEW_CONTENT", "audio/mpeg")}
    r = client.post("/api/upload", files=files)
    assert r.status_code == 409
    assert existing.read_bytes() == b"OLD_CONTENT"


def test_post_upload_rejects_empty_filename(client):
    """空檔名 → FastAPI 多媒體層 422 或我們的 400，都算 reject。"""
    files = {"file": ("", b"x", "audio/mpeg")}
    r = client.post("/api/upload", files=files)
    assert r.status_code in (400, 422)


# ─── C5：智慧 trim 開頭（silencedetect 建議） ─────────────────────────


def test_post_detect_silence_returns_head_seconds(client, monkeypatch):
    """/api/detect-silence 呼叫 silencedetect.detect_head_silence 並回傳秒數。"""
    from podcast_toolkit.web import silencedetect as _sd

    called = {}

    def fake(path):
        called["path"] = path
        return 2.7

    monkeypatch.setattr(_sd, "detect_head_silence", fake)
    r = client.post("/api/detect-silence")
    assert r.status_code == 200, r.text
    assert r.json() == {"head_silence_sec": 2.7}
    # 確認傳入的是 main_video
    assert "01_母帶/測試集.mp4" in str(called["path"])


def test_post_detect_silence_returns_400_when_no_main_video(client, tmp_episode_dir):
    """main_video 不存在 → 400。"""
    (tmp_episode_dir / "01_母帶" / "測試集.mp4").unlink()
    r = client.post("/api/detect-silence")
    assert r.status_code == 400
    assert "main_video" in r.json()["detail"] or "找不到" in r.json()["detail"]


# --- A3：/api/episode/new 新集 wizard --------------------------------------


def test_post_episode_new_creates_folder_and_switches(client, tmp_episode_dir):
    """傳 date + name → 在當前集的父資料夾下建 'YYYYMMDD 集名' 並 switch holder。"""
    parent = tmp_episode_dir.parent
    r = client.post(
        "/api/episode/new",
        json={"date": "20260610", "name": "新一集"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    new_path = Path(body["path"])
    assert new_path == parent / "20260610 新一集"
    assert new_path.is_dir()
    # init 後該有 episode.yaml 跟三個子資料夾
    assert (new_path / "episode.yaml").is_file()
    for sub in ("01_母帶", "03_成品", "04_工作檔"):
        assert (new_path / sub).is_dir()
    # 後續 GET /api/episode 應反映已切到新集
    r2 = client.get("/api/episode")
    assert r2.status_code == 200
    assert r2.json()["name"] == "新一集"


def test_post_episode_new_rejects_existing_target(client, tmp_episode_dir):
    """目標資料夾已存在 → 409。"""
    parent = tmp_episode_dir.parent
    (parent / "20260610 重複集").mkdir()
    r = client.post(
        "/api/episode/new",
        json={"date": "20260610", "name": "重複集"},
    )
    assert r.status_code == 409
    assert "已存在" in r.json()["detail"]


def test_post_episode_new_rejects_missing_date(client):
    r = client.post("/api/episode/new", json={"name": "缺日期"})
    assert r.status_code == 400


def test_post_episode_new_rejects_missing_name(client):
    r = client.post("/api/episode/new", json={"date": "20260610"})
    assert r.status_code == 400


def test_post_episode_new_rejects_bad_date_format(client):
    r = client.post(
        "/api/episode/new",
        json={"date": "2026-06-10", "name": "格式錯"},
    )
    assert r.status_code == 400
    assert "YYYYMMDD" in r.json()["detail"] or "日期" in r.json()["detail"]


def test_post_episode_new_rejects_name_with_path_sep(client):
    r = client.post(
        "/api/episode/new",
        json={"date": "20260610", "name": "壞/名字"},
    )
    assert r.status_code == 400


def test_build_app_with_none_ep_does_not_crash():
    from podcast_toolkit.web.api import build_app
    app = build_app(ep=None, shutdown=lambda: None)
    client = TestClient(app)
    # 任一既有 endpoint 在 ep=None 時應該回 409，不是 500
    r = client.get("/api/episode")
    assert r.status_code == 409
    assert "尚未選集" in r.json()["detail"]
