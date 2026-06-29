"""podcast check-seg：斷句體檢（純讀取）。"""
from pathlib import Path

from podcast_toolkit import seg_check
from podcast_toolkit.episode import Episode


# 最小門檻：過長 >5 字、掛尾連接詞、反應詞放行
RCFG = {
    "hardlen": 5,
    "dangle_endings": ["然後", "所以"],
    "reaction_words": ["對", "嗯"],
}


def test_scan_flags_long_card():
    cards = [(1, "這是一張很長會超過五字的卡")]
    res = seg_check.scan(cards, RCFG)
    assert [idx for idx, _ in res["long"]] == [1]
    assert res["dangle"] == [] and res["short"] == []


def test_scan_flags_dangling_ending():
    cards = [(7, "我先去買東西然後")]
    res = seg_check.scan(cards, RCFG)
    assert res["dangle"] == [(7, "我先去買東西然後", "然後")]


def test_scan_dangling_ignores_trailing_punct():
    # 尾端有標點也要先剝掉再判連接詞
    cards = [(8, "那要在哪裡所以，")]
    res = seg_check.scan(cards, RCFG)
    assert [idx for idx, *_ in res["dangle"]] == [8]


def test_scan_short_card_but_reaction_word_passes():
    cards = [(2, "對"), (3, "嗯"), (4, "你")]
    res = seg_check.scan(cards, RCFG)
    # 對/嗯 是反應詞不算異味，「你」過短才算
    assert [idx for idx, _ in res["short"]] == [4]


def test_scan_clean_card_no_flags():
    cards = [(5, "好的沒問題")]  # 5 字不過長、無掛尾、不過短
    res = seg_check.scan(cards, RCFG)
    assert res["long"] == [] and res["dangle"] == [] and res["short"] == []


def test_parse_srt_roundtrip(tmp_path: Path):
    srt = tmp_path / "x.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n大家好\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\n歡迎收聽\n",
        encoding="utf-8",
    )
    assert seg_check.parse_srt(srt) == [(1, "大家好"), (2, "歡迎收聽")]


def test_run_on_episode_returns_0(tmp_episode_dir):
    # conftest 已在 output_v2_srt() 放了 SAMPLE_SRT
    assert Episode(tmp_episode_dir).output_v2_srt().exists()
    assert seg_check.run(tmp_episode_dir) == 0


def test_run_missing_v2_returns_3(tmp_episode_dir):
    Episode(tmp_episode_dir).output_v2_srt().unlink()
    assert seg_check.run(tmp_episode_dir) == 3
