"""講者平滑 + 去甩尾（subtitle_cleanup）測試。"""
from __future__ import annotations

from podcast_toolkit.subtitle_cleanup import (
    destrand_cards, reflow_by_phrases, smooth_speakers,
)


def _cards(*spans):
    """spans: (start, end, text) → cards list（idx 從 1）。"""
    return [{"idx": i, "start": s, "end": e, "text": t}
            for i, (s, e, t) in enumerate(spans, 1)]


# ---- smooth_speakers ----

def test_smooth_merges_short_blip_between_same_speaker():
    """夾在 c 中間的短 blip（b, 0.5s）→ 併回 c。"""
    cards = _cards((0, 3, "x"), (3, 6, "x"), (6, 6.5, "x"), (6.5, 9, "x"), (9, 12, "x"))
    spk = {1: "c", 2: "c", 3: "b", 4: "c", 5: "c"}
    out = smooth_speakers(cards, spk, blip_sec=2.0)
    assert out == {1: "c", 2: "c", 3: "c", 4: "c", 5: "c"}


def test_smooth_merges_consecutive_blips():
    """連續兩個不同 blip（b 再 a）夾在 c 中間 → 兩個都併回 c。"""
    cards = _cards((0, 4, "x"), (4, 5, "x"), (5, 5.8, "x"), (5.8, 10, "x"))
    spk = {1: "c", 2: "b", 3: "a", 4: "c"}
    out = smooth_speakers(cards, spk, blip_sec=2.0)
    assert out == {1: "c", 2: "c", 3: "c", 4: "c"}


def test_smooth_keeps_long_segment():
    """夠長的段（b, 4s ≥ blip_sec）不算 blip → 不動。"""
    cards = _cards((0, 3, "x"), (3, 7, "x"), (7, 10, "x"))
    spk = {1: "c", 2: "b", 3: "c"}
    out = smooth_speakers(cards, spk, blip_sec=2.0)
    assert out == {1: "c", 2: "b", 3: "c"}


def test_smooth_keeps_short_edge_segment():
    """第一段雖短（只有右鄰）不算夾中間的 blip → 保留（避免誤併合法開場短句）。"""
    cards = _cards((0, 1.5, "x"), (1.5, 6, "x"), (6, 10, "x"))
    spk = {1: "a", 2: "b", 3: "c"}
    out = smooth_speakers(cards, spk, blip_sec=2.0)
    assert out == {1: "a", 2: "b", 3: "c"}


def test_smooth_empty_speakers_is_noop():
    assert smooth_speakers(_cards((0, 1, "x")), {}) == {}


# ---- destrand_cards ----

def test_destrand_moves_short_lead_to_prev_same_speaker():
    """後卡「量能 …」開頭 2 字甩尾 → 接回前卡，切點一致。"""
    cards = _cards((0.0, 2.0, "我們會處理"), (2.0, 5.0, "量能 然後呢做這個"))
    spk = {1: "c", 2: "c"}
    destrand_cards(cards, spk)
    assert cards[0]["text"] == "我們會處理量能"
    assert cards[1]["text"] == "然後呢做這個"
    assert cards[0]["end"] == cards[1]["start"]   # 切點一致、時間連續


def test_destrand_skips_different_speaker():
    """前後卡不同講者（可能是真的兩人）→ 不挪。"""
    cards = _cards((0.0, 2.0, "我們會處理"), (2.0, 5.0, "量能 然後呢做這個"))
    spk = {1: "c", 2: "b"}
    destrand_cards(cards, spk)
    assert cards[1]["text"] == "量能 然後呢做這個"   # 原樣


def test_destrand_skips_long_lead():
    """開頭詞 >2 字（不是甩尾，是正常開頭）→ 不挪。"""
    cards = _cards((0.0, 2.0, "我們會處理"), (2.0, 5.0, "量能很多 然後呢"))
    spk = {1: "c", 2: "c"}
    destrand_cards(cards, spk)
    assert cards[1]["text"] == "量能很多 然後呢"


def test_destrand_skips_when_no_rest():
    """整卡就是一個短詞（無後文）= 獨立短回應，不是甩尾 → 不挪。"""
    cards = _cards((0.0, 2.0, "我們會處理"), (2.0, 3.0, "對啊"))
    spk = {1: "c", 2: "c"}
    destrand_cards(cards, spk)
    assert cards[1]["text"] == "對啊"


def test_destrand_cascades_left_to_right():
    """連續甩尾：一次 pass 由左到右各自接回。"""
    cards = _cards((0.0, 2.0, "一切的"), (2.0, 4.0, "起點 那再到"), (4.0, 6.0, "量能 然後"))
    spk = {1: "c", 2: "c", 3: "c"}
    destrand_cards(cards, spk)
    assert cards[0]["text"] == "一切的起點"
    assert cards[1]["text"] == "那再到量能"
    assert cards[2]["text"] == "然後"


# ---- reflow_by_phrases ----

def test_reflow_splits_at_cjk_spaces_joins_across_cards():
    """連續同講者：空格在流行/機器人後切；送餐|機器人(卡邊界無空格)接起來。"""
    cards = _cards((0.0, 3.0, "其實區塊鏈很流行 然後什麼送餐"),
                   (3.0, 6.0, "機器人 那個時候也剛冒出來"))
    new, _ = reflow_by_phrases(cards, {1: "c", 2: "c"}, gap=0.3)
    assert [c["text"] for c in new] == [
        "其實區塊鏈很流行", "然後什麼送餐機器人", "那個時候也剛冒出來"]
    assert [c["idx"] for c in new] == [1, 2, 3]


def test_reflow_protects_ascii_adjacent_spaces():
    """英/數旁的空格不算語句邊界（line pay 不拆）；只有兩中文字間才斷。"""
    cards = _cards((0.0, 4.0, "用 line pay 付款 然後走"))
    new, _ = reflow_by_phrases(cards, {1: "c"})
    assert [c["text"] for c in new] == ["用 line pay 付款", "然後走"]


def test_reflow_different_speaker_not_joined():
    cards = _cards((0.0, 3.0, "我問你"), (3.0, 6.0, "題目"))
    new, ns = reflow_by_phrases(cards, {1: "a", 2: "c"}, gap=0.3)
    assert [c["text"] for c in new] == ["我問你", "題目"]
    assert ns == {1: "a", 2: "c"}


def test_reflow_pause_breaks_run():
    """同講者但間隔 > gap（真停頓）→ 不併。"""
    cards = _cards((0.0, 3.0, "第一句"), (5.0, 8.0, "第二句"))
    new, _ = reflow_by_phrases(cards, {1: "c", 2: "c"}, gap=0.3)
    assert [c["text"] for c in new] == ["第一句", "第二句"]


def test_reflow_subsplit_long_phrase():
    """超過 max_w 的無空格中文串 → 硬切成 ≤max_w，內容不丟。"""
    cards = _cards((0.0, 4.0, "一二三四五六七八九十甲乙丙丁戊己庚辛"))
    new, _ = reflow_by_phrases(cards, {1: "c"}, max_w=16)
    assert all(len(c["text"]) <= 16 for c in new)
    assert "".join(c["text"] for c in new) == "一二三四五六七八九十甲乙丙丁戊己庚辛"


def test_reflow_subsplit_keeps_cjk_ascii_terms_intact():
    """超過 max_w 觸發硬切時，混排詞（胚 pae / 很多 idea）不可被空格切碎。

    硬切點只准落在「兩中文字之間」，所以「中文–空格–英/數」邊界（_subsplit 早期
    只擋 ascii↔ascii 與 的得地，會在此切開 → 留白計畫 王奕翔集 把品牌名切成碎卡）
    現在會往左退到合法中文邊界，品牌名/英文詞整段保留。"""
    # 兩個 case 都做成「一張卡、單一語句（無兩中文字間空格）、長度 > max_w」→ 必走 _subsplit
    cards = _cards((0.0, 4.0, "中文字一二三四五六七八九十甲乙胚 pae丙丁戊己"))
    new, _ = reflow_by_phrases(cards, {1: "c"}, max_w=16)
    assert all(len(c["text"]) <= 16 for c in new)
    texts = [c["text"] for c in new]
    assert any("胚 pae" in t for t in texts), texts          # 胚 與 pae 不被拆開
    assert not any(t in ("胚", "pae") for t in texts), texts  # 沒有單獨的碎卡

    cards2 = _cards((0.0, 4.0, "前面這是一段比較長的中文字句很多 idea 出現之後接更多字"))
    new2, _ = reflow_by_phrases(cards2, {1: "c"}, max_w=16)
    assert all(len(c["text"]) <= 16 for c in new2)
    texts2 = [c["text"] for c in new2]
    # idea 整段保留、且左右空格沒被當邊界拆掉（永遠是「 idea 」貼在某張卡裡）
    assert any(" idea " in t for t in texts2), texts2
    assert not any(t in ("idea", "很多", "出現") for t in texts2), texts2


def test_reflow_subsplit_no_word_straddle():
    """硬切改走 word_break 評分：不再把「然後」「耳機」切成 然|後、耳|機。"""
    for src, word in [
        ("他們家的隔音跟通風設備真的做得很不錯然後我們就決定租下來了", "然後"),
        ("因為現場真的太吵的時候你根本聽不見耳機裡的聲音", "耳機"),
    ]:
        new, _ = reflow_by_phrases(_cards((0.0, 8.0, src)), {1: "c"}, max_w=16)
        texts = [c["text"] for c in new]
        assert len(texts) >= 2, texts                       # 有真的切
        assert "".join(texts) == src                        # 內容不丟
        assert any(word in t for t in texts), texts         # 詞完整留在某卡
        for a, b in zip(texts, texts[1:]):
            assert a[-1] + b[0] != word, texts              # 不切在詞中間


def test_reflow_conservative_run_kept_verbatim():
    """保守化：run 無 proofread 空格邊界、也無過長卡 → 原卡原時間直接保留。"""
    cards = _cards((0.0, 1.0, "我們今天請到"), (1.1, 2.0, "一位很棒的來賓"))
    new, ns = reflow_by_phrases(cards, {1: "c", 2: "c"}, gap=0.3, max_w=16)
    assert [c["text"] for c in new] == ["我們今天請到", "一位很棒的來賓"]
    # 時間逐卡不變（不重併重切 → 保住 Breeze 逐字時間精度）
    assert [(c["start"], c["end"]) for c in new] == [(0.0, 1.0), (1.1, 2.0)]
    assert ns == {1: "c", 2: "c"}


def test_reflow_conservative_not_applied_when_space_boundary_exists():
    """run 內只要有可用空格邊界（兩側皆 CJK）→ 仍照舊重切。"""
    cards = _cards((0.0, 1.5, "我們今天請到 一位"), (1.6, 2.4, "很棒的來賓"))
    new, _ = reflow_by_phrases(cards, {1: "c", 2: "c"}, gap=0.3, max_w=16)
    assert [c["text"] for c in new] == ["我們今天請到", "一位很棒的來賓"]


def test_reflow_conservative_not_applied_when_card_too_long():
    """run 內有卡超過 max_w → 仍要切（≤max_w、內容不丟）。"""
    src = "一二三四五六七八九十甲乙丙丁戊己庚辛"
    new, _ = reflow_by_phrases(_cards((0.0, 4.0, src)), {1: "c"}, max_w=16)
    assert len(new) >= 2
    assert all(len(c["text"]) <= 16 for c in new)
    assert "".join(c["text"] for c in new) == src


# ---- 單字碎卡合併（Breeze 逐字卡殘留）----

def test_reflow_merges_single_char_runts():
    """保守化 run 內的單字碎卡（避 / 開）→ 併成完整詞「避開」，時間跨接。"""
    cards = _cards((0.0, 0.4, "避"), (0.4, 0.8, "開"))
    new, _ = reflow_by_phrases(cards, {1: "c", 2: "c"}, gap=0.3, max_w=16)
    assert [c["text"] for c in new] == ["避開"]
    assert (new[0]["start"], new[0]["end"]) == (0.0, 0.8)


def test_reflow_merges_chain_of_single_chars():
    """連續單字碎卡（只/要/一次）→ 併掉單字卡，內容不丟。"""
    cards = _cards((0.0, 0.3, "只"), (0.3, 0.6, "要"), (0.6, 1.2, "一次"))
    new, _ = reflow_by_phrases(cards, {1: "c", 2: "c", 3: "c"}, gap=0.3, max_w=16)
    assert "".join(c["text"] for c in new) == "只要一次"
    assert all(len(c["text"]) >= 2 for c in new)   # 不再有單字卡


def test_reflow_single_char_reaction_not_merged():
    """反應詞單字卡（對）保留、不併進鄰卡。"""
    cards = _cards((0.0, 0.5, "對"), (0.5, 1.5, "我也這樣覺得"))
    new, _ = reflow_by_phrases(
        cards, {1: "c", 2: "c"}, gap=0.3, max_w=16, reaction=frozenset({"對"}))
    assert [c["text"] for c in new] == ["對", "我也這樣覺得"]


def test_reflow_runt_not_merged_across_pause():
    """真停頓分開的單字卡（避 … 開 gap>0.3）→ 各自成卡，不跨停頓併。"""
    cards = _cards((0.0, 0.4, "避"), (2.0, 2.4, "開"))
    new, _ = reflow_by_phrases(cards, {1: "c", 2: "c"}, gap=0.3, max_w=16)
    assert [c["text"] for c in new] == ["避", "開"]


def test_reflow_runt_not_merged_across_clause_space():
    """單字碎卡不可跨 proofread 空格（真語句邊界）併到別的語句去。"""
    # 「好」在空格後自成語句；不可被前併成「請說好」
    cards = _cards((0.0, 2.0, "那你先請說 好"), (2.0, 4.0, "我們開始"))
    new, _ = reflow_by_phrases(cards, {1: "c", 2: "c"}, gap=0.3, max_w=16)
    assert [c["text"] for c in new] == ["那你先請說", "好我們開始"]
