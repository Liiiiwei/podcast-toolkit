"""subtitle_polish：字幕最後拋光測試。"""
from __future__ import annotations

import json
from pathlib import Path

from podcast_toolkit import srt_io, subtitle_polish


def test_polish_merges_flash_and_splits_long_hold(tmp_episode_dir: Path):
    """閃字幕會併回前卡；超過 6 秒的長卡會切開並重編號。"""
    srt_path = tmp_episode_dir / "03_成品" / "測試集_final_v2.srt"
    srt_path.write_text(
        """\
1
00:00:00,000 --> 00:00:02,000
沒有任何這個安全措施

2
00:00:02,000 --> 00:00:02,020
的概念

3
00:00:03,000 --> 00:00:10,000
電力公司的或是哪裡的水利署的這種
""",
        encoding="utf-8",
    )
    spk_path = tmp_episode_dir / "03_成品" / "測試集_final_v2.speakers.json"
    spk_path.write_text(json.dumps({"1": "a", "2": "a", "3": "b"}), encoding="utf-8")

    report = subtitle_polish.run(tmp_episode_dir)
    cards = srt_io.parse(srt_path.read_text(encoding="utf-8"))

    assert report["changed"] is True
    assert report["merged_flash_cards"] == 1
    assert report["split_problem_cards"] == 1
    assert report["after"]["errors"] == 0
    assert report["after"]["short_flash"] == 0
    assert report["after"]["long_hold"] == 0
    assert [c["idx"] for c in cards] == [1, 2, 3]
    assert cards[0]["text"] == "沒有任何這個安全措施的概念"
    assert cards[1]["text"] == "電力公司的或是"
    assert cards[2]["text"] == "哪裡的水利署的這種"
    assert (tmp_episode_dir / "04_工作檔" / "_subtitle_polish_report.json").exists()
    assert list(srt_path.parent.glob("測試集_final_v2.*.pre-polish.bak.srt"))


def test_polish_keeps_single_reaction_flash(tmp_episode_dir: Path):
    """單字或短反應詞即使小於 0.3 秒也保留，避免吃掉自然接話。"""
    srt_path = tmp_episode_dir / "03_成品" / "測試集_final_v2.srt"
    srt_path.write_text(
        """\
1
00:00:00,000 --> 00:00:01,000
我覺得可以

2
00:00:01,000 --> 00:00:01,150
對

3
00:00:01,200 --> 00:00:02,200
然後繼續講
""",
        encoding="utf-8",
    )

    report = subtitle_polish.run(tmp_episode_dir)
    cards = srt_io.parse(srt_path.read_text(encoding="utf-8"))

    assert report["changed"] is False
    assert report["after"]["short_flash"] == 0
    assert [c["text"] for c in cards] == ["我覺得可以", "對", "然後繼續講"]


def test_flash_fragment_does_not_merge_across_known_speakers():
    cards = [
        {"idx": 1, "start": 0.0, "end": 1.0, "text": "上一位說"},
        {"idx": 2, "start": 1.0, "end": 1.1, "text": "的概念"},
    ]

    polished, speakers, merged = subtitle_polish._merge_flash_cards(
        cards, {1: "a", 2: "b"}
    )

    assert merged == 0
    assert [card["text"] for card in polished] == ["上一位說", "的概念"]
    assert speakers == {1: "a", 2: "b"}


def test_first_flash_fragment_can_merge_into_following_same_speaker_card():
    cards = [
        {"idx": 1, "start": 0.0, "end": 0.1, "text": "所以"},
        {"idx": 2, "start": 0.1, "end": 1.0, "text": "我們繼續"},
    ]

    polished, speakers, merged = subtitle_polish._merge_flash_cards(
        cards, {1: "a", 2: "a"}
    )

    assert merged == 1
    assert [card["text"] for card in polished] == ["所以我們繼續"]
    assert speakers == {1: "a"}


def test_long_card_is_split_until_quality_limits_are_met():
    cards = [{"idx": 1, "start": 0.0, "end": 20.0, "text": "這是一段非常長而且需要多次切割才能符合字幕限制的內容"}]

    polished, _, splits = subtitle_polish._split_problem_cards(cards, {}, [])
    report = subtitle_polish.analyze(polished)

    assert splits >= 1
    assert report["long_hold"] == []
    assert report["long_chars"] == []
    assert report["fast"] == []


def test_split_uses_word_timestamps_when_words_match_card_text():
    card = {"idx": 1, "start": 0.0, "end": 10.0, "text": "前半句或是後半句"}
    words = [
        {"word": "前半句", "start": 0.0, "end": 3.0},
        {"word": "或是", "start": 3.0, "end": 4.0},
        {"word": "後半句", "start": 4.0, "end": 10.0},
    ]

    pieces = subtitle_polish._split_long_card(card, words)

    assert pieces[0]["text"] == "前半句"
    assert pieces[0]["end"] == 3.0
    assert pieces[1]["start"] == 3.0


def test_invalid_polish_candidate_keeps_original_srt(tmp_episode_dir: Path, monkeypatch):
    srt_path = tmp_episode_dir / "03_成品" / "測試集_final_v2.srt"
    original = "1\n00:00:00,000 --> 00:00:01,000\n原始字幕\n"
    srt_path.write_text(original, encoding="utf-8")

    monkeypatch.setattr(
        subtitle_polish,
        "_merge_flash_cards",
        lambda cards, speakers: (cards, speakers, 1),
    )
    monkeypatch.setattr(
        subtitle_polish,
        "_split_problem_cards",
        lambda cards, speakers, words: (
            [{"idx": 1, "start": 2.0, "end": 1.0, "text": "無效字幕"}],
            {},
            1,
        ),
    )

    report = subtitle_polish.run(tmp_episode_dir)

    assert report["ok"] is False
    assert report["written"] is False
    assert srt_path.read_text(encoding="utf-8") == original
