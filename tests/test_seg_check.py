"""podcast check-seg：斷句體檢（純讀取）。"""
from pathlib import Path

from podcast_toolkit import seg_check, word_break
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


# ---- ④ 跨卡切詞 ----

# hardlen 拉高，避免 straddle 測資同時觸發過長味干擾斷言
RCFG4 = {
    "hardlen": 23,
    "dangle_endings": ["所以"],
    "reaction_words": ["對", "嗯"],
    "straddle_gap": 0.35,
}


def test_scan_flags_word_straddling_cards():
    """「然後」被切成 然|後、卡間隔 0.1s → 記一味，回報跨界詞。"""
    cards = [(1, "我們先講到這裡然", 0.0, 2.0),
             (2, "後再來討論價格", 2.1, 4.0)]
    res = seg_check.scan(cards, RCFG4)
    assert res["straddle"] is not None
    assert [(p, c, w) for p, c, w, *_ in res["straddle"]] == [(1, 2, "然後")]
    # 報告要帶兩卡文字，方便人跳到編輯器修
    assert res["straddle"][0][3:] == ("我們先講到這裡然", "後再來討論價格")


def test_scan_straddle_skips_big_gap():
    """卡間隔 > straddle_gap（真氣口）→ 不算跨卡切詞。"""
    cards = [(1, "我們先講到這裡然", 0.0, 2.0),
             (2, "後再來討論價格", 2.6, 4.0)]
    res = seg_check.scan(cards, RCFG4)
    assert res["straddle"] == []


def test_scan_straddle_clean_boundary_not_flagged():
    """卡界剛好落在詞界 → 健康，不記味。"""
    cards = [(1, "我們今天請到一位來賓", 0.0, 2.0),
             (2, "他的公司做設計", 2.1, 4.0)]
    res = seg_check.scan(cards, RCFG4)
    assert res["straddle"] == []


def test_scan_straddle_strips_mic_label():
    """含講者 [MicN] 前綴要先剝掉再判詞界。"""
    cards = [(1, "[Mic1] 我們先講到這裡然", 0.0, 2.0),
             (2, "[Mic1] 後再來討論價格", 2.1, 4.0)]
    res = seg_check.scan(cards, RCFG4)
    assert [w for _, _, w, *_ in res["straddle"]] == ["然後"]


def test_scan_straddle_skipped_without_jieba(monkeypatch, capsys):
    """jieba 缺席 → straddle 為 None，報告註明略過。"""
    monkeypatch.setattr(word_break, "_JIEBA", False)
    cards = [(1, "我們先講到這裡然", 0.0, 2.0),
             (2, "後再來討論價格", 2.1, 4.0)]
    res = seg_check.scan(cards, RCFG4)
    assert res["straddle"] is None
    seg_check._report("x.srt", len(cards), res, limit=12, hardlen=23)
    assert "jieba 未安裝，跨卡切詞檢測略過" in capsys.readouterr().out


def test_scan_untimed_cards_straddle_empty():
    """舊 2 欄格式（無時間）→ ④ 無從判 gap，回空清單、其餘三味照常。"""
    res = seg_check.scan([(1, "我們先講到這裡然"), (2, "後再來討論價格")], RCFG4)
    assert res["straddle"] == []


def test_parse_srt_timed(tmp_path: Path):
    srt = tmp_path / "x.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,500\n大家好\n\n"
        "2\n00:00:01,600 --> 00:00:02,000\n歡迎收聽\n",
        encoding="utf-8",
    )
    assert seg_check.parse_srt_timed(srt) == [
        (1, "大家好", 0.0, 1.5), (2, "歡迎收聽", 1.6, 2.0)]


def test_run_on_episode_returns_0(tmp_episode_dir):
    # conftest 已在 output_v2_srt() 放了 SAMPLE_SRT
    assert Episode(tmp_episode_dir).output_v2_srt().exists()
    assert seg_check.run(tmp_episode_dir) == 0


def test_run_missing_v2_returns_3(tmp_episode_dir):
    Episode(tmp_episode_dir).output_v2_srt().unlink()
    assert seg_check.run(tmp_episode_dir) == 3
