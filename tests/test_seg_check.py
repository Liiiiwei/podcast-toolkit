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


# ---- ⑤ 時間重疊 ----

def test_scan_flags_time_overlap_single_track():
    """單軌（不傳 speakers）：前卡未結束下一卡已開始 → 記重疊、附秒數。"""
    cards = [(1, "前面這句", 0.0, 2.0), (2, "後面這句", 1.5, 3.0)]
    res = seg_check.scan(cards, RCFG4)
    assert res["overlap"] == [(1, 2, 0.5)]


def test_scan_overlap_same_speaker_flagged():
    """分軌但同一講者重疊 → 仍記（同一人不可能同時講兩句）。"""
    cards = [(1, "前面這句", 0.0, 2.0), (2, "後面這句", 1.5, 3.0)]
    res = seg_check.scan(cards, RCFG4, {1: "a", 2: "a"})
    assert res["overlap"] == [(1, 2, 0.5)]


def test_scan_overlap_cross_speaker_preserved():
    """分軌不同講者重疊 → 雙人同時說話的既定設計，不記味。"""
    cards = [(1, "前面這句", 0.0, 2.0), (2, "後面這句", 1.5, 3.0)]
    res = seg_check.scan(cards, RCFG4, {1: "a", 2: "b"})
    assert res["overlap"] == []


def test_scan_overlap_touching_boundary_not_flagged():
    """前卡 end 剛好等於後卡 start（無縫相接）→ 不算重疊。"""
    cards = [(1, "前面這句", 0.0, 2.0), (2, "後面這句", 2.0, 3.0)]
    res = seg_check.scan(cards, RCFG4)
    assert res["overlap"] == []


def test_scan_overlap_untimed_cards_empty():
    """舊 2 欄格式（無時間）→ 無從判重疊，回空清單。"""
    res = seg_check.scan([(1, "前面這句"), (2, "後面這句")], RCFG4)
    assert res["overlap"] == []


def test_report_prints_overlap_line(capsys):
    """報告要印出 ⑤ 時間重疊那一行與對數。"""
    cards = [(1, "前面這句", 0.0, 2.0), (2, "後面這句", 1.5, 3.0)]
    res = seg_check.scan(cards, RCFG4)
    seg_check._report("x.srt", len(cards), res, limit=12, hardlen=23)
    out = capsys.readouterr().out
    assert "⑤ 時間重疊：1 對" in out
    assert "#1→#2" in out


# ---- ⑥ 相鄰重複 ----

def test_scan_flags_adjacent_dup_same_speaker():
    """同講者、緊接、同文字 → 記一味、附文字。"""
    cards = [(1, "外文系啊", 0.0, 1.0), (2, "外文系啊", 1.0, 2.0)]
    res = seg_check.scan(cards, RCFG4, {1: "c", 2: "c"})
    assert res["dup"] == [(1, 2, "外文系啊")]


def test_scan_dup_cross_speaker_preserved():
    """跨講者同文字（Mic2/Mic3 各講一次）＝兩人真話 → 不記味。"""
    cards = [(1, "外文系啊", 0.0, 1.0), (2, "外文系啊", 1.0, 2.0)]
    res = seg_check.scan(cards, RCFG4, {1: "b", 2: "c"})
    assert res["dup"] == []


def test_scan_dup_skips_across_pause():
    """同講者同文字但有真停頓（刻意重覆講）→ 不記味。"""
    cards = [(1, "真的假的", 0.0, 1.0), (2, "真的假的", 1.5, 2.5)]
    res = seg_check.scan(cards, RCFG4, {1: "c", 2: "c"})
    assert res["dup"] == []


def test_scan_dup_single_track_empty():
    """單軌（不傳 speakers）無從判同講者 → 不檢，回空清單。"""
    cards = [(1, "外文系啊", 0.0, 1.0), (2, "外文系啊", 1.0, 2.0)]
    res = seg_check.scan(cards, RCFG4)
    assert res["dup"] == []


def test_scan_dup_strips_mic_label():
    """含 [MicN] 前綴要先剝掉再比對文字。"""
    cards = [(1, "[Mic2] 外文系啊", 0.0, 1.0), (2, "[Mic2] 外文系啊", 1.0, 2.0)]
    res = seg_check.scan(cards, RCFG4, {1: "c", 2: "c"})
    assert [(p, c, t) for p, c, t in res["dup"]] == [(1, 2, "外文系啊")]


def test_scan_dup_different_text_not_flagged():
    """同講者緊接但文字不同 → 不算重複。"""
    cards = [(1, "這個", 0.0, 1.0), (2, "那個", 1.0, 2.0)]
    res = seg_check.scan(cards, RCFG4, {1: "c", 2: "c"})
    assert res["dup"] == []


def test_report_prints_dup_line(capsys):
    """報告要印出 ⑥ 相鄰重複那一行與對數。"""
    cards = [(1, "外文系啊", 0.0, 1.0), (2, "外文系啊", 1.0, 2.0)]
    res = seg_check.scan(cards, RCFG4, {1: "c", 2: "c"})
    seg_check._report("x.srt", len(cards), res, limit=12, hardlen=23)
    out = capsys.readouterr().out
    assert "⑥ 相鄰重複：1 對" in out
    assert "「外文系啊」" in out
