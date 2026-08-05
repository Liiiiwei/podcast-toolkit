"""resegment.run + POST /api/resegment（自帶字幕只跑斷句後處理，不跑 STT）。"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from podcast_toolkit import resegment
from podcast_toolkit.episode import Episode
from podcast_toolkit.web import transcribe_job
from podcast_toolkit.web.api import build_app


# 字層 / 短段的原始字幕，丟給 resegment 貪婪合併成句子層
RAW_SRT = """\
1
00:00:00,000 --> 00:00:01,000
大家好

2
00:00:01,000 --> 00:00:02,000
歡迎來到

3
00:00:02,000 --> 00:00:03,000
我愛上班

4
00:00:03,000 --> 00:00:09,000
今天要聊的是過嗨乳牛這個議題
"""


def _write_main_srt(ep_dir: Path) -> Path:
    """把 RAW_SRT 寫到 main_srt（01_母帶/測試集.srt）。conftest 沒建這份。"""
    main = ep_dir / "01_母帶" / "測試集.srt"
    main.write_text(RAW_SRT, encoding="utf-8")
    return main


@pytest.fixture
def client(tmp_episode_dir: Path):
    # 重置背景 job 狀態，避免別的測試殘留 "running" 擋掉 resegment
    transcribe_job._reset()
    ep = Episode(tmp_episode_dir)
    app = build_app(ep, shutdown=lambda: None)
    return TestClient(app)


# --- 直接測 resegment.run ---


def test_resegment_run_rewrites_v2_and_review(tmp_episode_dir):
    _write_main_srt(tmp_episode_dir)
    ep = Episode(tmp_episode_dir)
    v2 = ep.output_v2_srt()
    before = v2.read_text(encoding="utf-8")  # conftest 的 SAMPLE_SRT

    rc = resegment.run(tmp_episode_dir, force=True)

    assert rc == 0
    after = v2.read_text(encoding="utf-8")
    assert after != before  # 從 RAW_SRT 重新斷句，內容應改變
    assert "大家好歡迎來到我愛上班" in after.replace("\n", "")  # 前三短段被合併
    assert ep.review_file().is_file()  # 複查清單有產出


def test_resegment_run_missing_main_srt_returns_3(tmp_episode_dir):
    # 不寫 main_srt → resegment 找不到來源，回 rc=3
    rc = resegment.run(tmp_episode_dir, force=True)
    assert rc == 3


# --- 測 POST /api/resegment ---


def test_api_resegment_rewrites_v2_and_backs_up(client, tmp_episode_dir):
    _write_main_srt(tmp_episode_dir)
    v2 = tmp_episode_dir / "03_成品" / "測試集_final_v2.srt"
    before = v2.read_text(encoding="utf-8")

    r = client.post("/api/resegment", json={})

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["out_srt"] == "03_成品/測試集_final_v2.srt"
    # _v2.srt 真的被重寫
    assert v2.read_text(encoding="utf-8") != before
    # 重跑前先備份了舊 SRT（_v2 / main_srt）→ 至少一份 .bak.srt
    baks = list(tmp_episode_dir.rglob("*.bak.srt"))
    assert baks, "應留下 .bak.srt 備份"
    assert any(b in body["backups"][0] for b in ["03_成品", "01_母帶"])


def test_api_resegment_no_source_srt_returns_400(client, tmp_episode_dir):
    # 沒有 main_srt（01_母帶/測試集.srt 不存在），也沒給 src_srt → 400
    r = client.post("/api/resegment", json={})
    assert r.status_code == 400
    assert "來源字幕" in r.json()["detail"]


def test_api_resegment_src_srt_copies_to_main(client, tmp_episode_dir):
    # 使用者把自轉字幕放在 04_工作檔/，用 src_srt 指定 → 端點複製到 main_srt 再跑
    chosen = tmp_episode_dir / "04_工作檔" / "我的字幕.srt"
    chosen.write_text(RAW_SRT, encoding="utf-8")

    r = client.post("/api/resegment", json={"src_srt": "04_工作檔/我的字幕.srt"})

    assert r.status_code == 200
    main = tmp_episode_dir / "01_母帶" / "測試集.srt"
    assert main.is_file()  # 已複製成 main_srt
    assert main.read_text(encoding="utf-8") == RAW_SRT


def test_api_resegment_missing_src_srt_returns_404(client, tmp_episode_dir):
    r = client.post("/api/resegment", json={"src_srt": "04_工作檔/不存在.srt"})
    assert r.status_code == 404


def test_api_resegment_blocked_while_transcribing(client, tmp_episode_dir):
    _write_main_srt(tmp_episode_dir)
    transcribe_job._set(state="running")
    try:
        r = client.post("/api/resegment", json={})
        assert r.status_code == 409
    finally:
        transcribe_job._reset()


# --- 講者 sidecar 重建（重切後 idx 全部重編，舊 idx 留著就錯位）---


def _write_speakers(ep_dir: Path, mapping: dict) -> Path:
    from podcast_toolkit import cameras_io

    path = ep_dir / "03_成品" / "測試集_final_v2.speakers.json"
    cameras_io.save(path, mapping)
    return path


def test_resegment_rebuilds_speakers_onto_new_cards(tmp_episode_dir):
    """舊 bug：resegment 重切完不動 speakers.json，舊 idx 直接套到新卡 → 講者標
    貼到不相干的句子上（燒進成品就是講錯人），而且多出來的 idx 指向不存在的卡。"""
    from podcast_toolkit import cameras_io, srt_io

    _write_main_srt(tmp_episode_dir)
    # conftest 的 SAMPLE_SRT 是 4 張卡，講者 a/b/c/a
    spk_path = _write_speakers(tmp_episode_dir, {1: "a", 2: "b", 3: "c", 4: "a"})

    rc = resegment.run(tmp_episode_dir, force=True)

    assert rc == 0
    ep = Episode(tmp_episode_dir)
    cards = srt_io.parse(ep.output_v2_srt().read_text(encoding="utf-8"))
    new_spk = cameras_io.load(spk_path)
    # 新字幕只有 2 張卡：0–3.0 全落在舊卡1(0–4.2) → a；3.0–9.0 與舊卡2(4.2–12) 重疊最多 → b
    assert [c["idx"] for c in cards] == [1, 2]
    assert new_spk == {1: "a", 2: "b"}
    # 舊檔要留一份 —— 對應關係萬一貼錯，使用者還救得回來
    bak = spk_path.with_name(spk_path.name + ".pre-resegment.bak")
    assert bak.exists()
    assert cameras_io.load(bak) == {1: "a", 2: "b", 3: "c", 4: "a"}


def test_resegment_without_speakers_creates_none(tmp_episode_dir):
    """單軌集本來就沒有講者標，重切不該無中生有一份（也不該留備份檔）。"""
    _write_main_srt(tmp_episode_dir)
    ep = Episode(tmp_episode_dir)
    spk_path = ep.output_v2_speakers_json()

    assert resegment.run(tmp_episode_dir, force=True) == 0

    assert not spk_path.exists()
    assert not spk_path.with_name(spk_path.name + ".pre-resegment.bak").exists()


def test_remap_speakers_picks_larger_overlap():
    """跨兩張舊卡的新卡，講者跟重疊多的那邊走（各佔一半以上的那個才算數）。"""
    old_cards = [
        {"idx": 1, "start": 0.0, "end": 10.0, "text": ""},
        {"idx": 2, "start": 10.0, "end": 20.0, "text": ""},
    ]
    old_spk = {1: "a", 2: "b"}
    new_cards = [
        [0.0, 12.0, "偏前"],    # a 佔 10 秒、b 佔 2 秒
        [8.0, 20.0, "偏後"],    # a 佔 2 秒、b 佔 10 秒
        [25.0, 30.0, "沒交集"],  # 完全沒重疊 → 不給講者，別亂猜
    ]

    assert resegment.remap_speakers_by_time(old_cards, old_spk, new_cards) == {
        1: "a",
        2: "b",
    }
