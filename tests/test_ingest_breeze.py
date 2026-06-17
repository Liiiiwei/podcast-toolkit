"""匯入 Breeze ASR 字幕(ingest_breeze)測試。"""
from __future__ import annotations

from podcast_toolkit import cameras_io, ingest_breeze, srt_io
from podcast_toolkit.episode import Episode

_BREEZE_SRT = """\
1
00:00:00,600 --> 00:00:02,440
[Mic1] 好 謝謝 謝謝

2
00:00:15,540 --> 00:00:18,860
[Mic2] 然後我們就是有那個錄影會放到 YT

3
00:00:19,440 --> 00:00:22,360
[Mic3] 待會我們就自然聊天就可以了這樣
"""


def test_parse_label():
    assert ingest_breeze.parse_label("[Mic1] 好 謝謝") == ("Mic1", "好 謝謝")
    assert ingest_breeze.parse_label("[Mic 2]  你好 ") == ("Mic 2", "你好")
    assert ingest_breeze.parse_label("沒有標籤的純文字") == (None, "沒有標籤的純文字")
    assert ingest_breeze.parse_label("[郝慧川] 大家好") == ("郝慧川", "大家好")


def test_ingest_strips_labels_and_maps_mics(tmp_path):
    p = tmp_path / "x_含講者.srt"
    p.write_text(_BREEZE_SRT, encoding="utf-8")
    cards, speakers = ingest_breeze.ingest(p)
    assert [c["text"] for c in cards] == [
        "好 謝謝 謝謝", "然後我們就是有那個錄影會放到 YT", "待會我們就自然聊天就可以了這樣",
    ]                                                # [MicN] 去乾淨
    assert [c["idx"] for c in cards] == [1, 2, 3]    # 重編號
    assert speakers == {1: "a", 2: "b", 3: "c"}      # Mic1→a Mic2→b Mic3→c
    assert cards[0]["start"] == 0.6 and cards[1]["end"] == 18.86   # 時間保留


def test_ingest_unlabeled_returns_no_speakers(tmp_path):
    p = tmp_path / "x_字幕.srt"
    p.write_text("1\n00:00:00,000 --> 00:00:01,000\n純文字沒有講者\n", encoding="utf-8")
    cards, speakers = ingest_breeze.ingest(p)
    assert cards[0]["text"] == "純文字沒有講者"
    assert speakers == {}


def test_ingest_real_names_assigned_by_appearance(tmp_path):
    srt = ("1\n00:00:00,000 --> 00:00:01,000\n[來賓] 甲\n\n"
           "2\n00:00:01,000 --> 00:00:02,000\n[主持] 乙\n\n"
           "3\n00:00:02,000 --> 00:00:03,000\n[來賓] 丙\n")
    p = tmp_path / "n_含講者.srt"
    p.write_text(srt, encoding="utf-8")
    _, speakers = ingest_breeze.ingest(p)
    assert speakers == {1: "a", 2: "b", 3: "a"}      # 非 MicN → 按首次出現序配字母,同名同 key


def test_find_breeze_srt_prefers_speaker_version(tmp_path):
    (tmp_path / "ep_字幕.srt").write_text("x", encoding="utf-8")
    (tmp_path / "ep_含講者.srt").write_text("x", encoding="utf-8")
    assert ingest_breeze.find_breeze_srt(tmp_path).name == "ep_含講者.srt"


def test_run_writes_v2_and_speakers_with_backup(tmp_episode_dir):
    src = tmp_episode_dir / "測試集_含講者.srt"
    src.write_text(_BREEZE_SRT, encoding="utf-8")
    ep = Episode(tmp_episode_dir)
    v2 = ep.output_v2_srt()
    before = v2.read_text(encoding="utf-8")           # fixture 已放一份 _v2.srt

    rc = ingest_breeze.run(tmp_episode_dir)           # 自動找到 *含講者*.srt
    assert rc == 0

    cards = srt_io.parse(v2.read_text(encoding="utf-8"))
    assert all("[Mic" not in c["text"] for c in cards)        # 無殘留標籤
    assert len(cards) == 3

    spk = cameras_io.load(ep.output_v2_speakers_json())
    assert spk == {1: "a", 2: "b", 3: "c"}                    # speakers.json 正確(cameras_io 格式)

    backup = v2.with_name(f"{v2.stem}.pre-breeze.bak{v2.suffix}")
    assert backup.exists() and backup.read_text(encoding="utf-8") == before  # 原檔有備份


def test_run_missing_srt_returns_3(tmp_episode_dir):
    # 集資料夾沒有任何 Breeze 字幕 → exit 3
    assert ingest_breeze.run(tmp_episode_dir, srt=str(tmp_episode_dir / "nope.srt")) == 3
