"""srt_io：解析 srt 為 cards、序列化 cards 為 srt。"""
from podcast_toolkit import srt_io


SAMPLE = """\
1
00:00:00,000 --> 00:00:04,200
大家好

2
00:00:04,200 --> 00:00:12,000
今天聊乳牛
"""


def test_parse_returns_cards_in_order():
    cards = srt_io.parse(SAMPLE)
    assert len(cards) == 2
    assert cards[0] == {"idx": 1, "start": 0.0, "end": 4.2, "text": "大家好"}
    assert cards[1]["idx"] == 2
    assert cards[1]["text"] == "今天聊乳牛"


def test_parse_handles_multiline_text():
    text = "1\n00:00:00,000 --> 00:00:01,000\n第一行\n第二行\n"
    cards = srt_io.parse(text)
    assert cards[0]["text"] == "第一行\n第二行"


def test_serialize_roundtrips():
    cards = srt_io.parse(SAMPLE)
    assert srt_io.serialize(cards).strip() == SAMPLE.strip()


def test_serialize_applies_text_overrides():
    cards = srt_io.parse(SAMPLE)
    out = srt_io.serialize(cards, overrides={1: "大家午安"})
    assert "大家午安" in out
    assert "大家好" not in out
    # 第 2 段未動
    assert "今天聊乳牛" in out


def test_parse_skips_blank_blocks():
    text = "\n\n1\n00:00:00,000 --> 00:00:01,000\nA\n\n\n"
    assert len(srt_io.parse(text)) == 1


# ---------- splits（前端 Enter 切分後送來）----------

def test_serialize_splits_card_into_two_with_renumber():
    cards = srt_io.parse(SAMPLE)
    out = srt_io.serialize(cards, splits={2: ["今天聊", "乳牛"]})
    # 1 維持不變、原 2 被切成新 2 / 3
    assert "1\n00:00:00,000 --> 00:00:04,200\n大家好" in out
    assert "\n2\n" in out
    assert "今天聊" in out
    assert "\n3\n" in out
    assert "乳牛" in out
    # 應該總共 3 段
    lines = [l for l in out.strip().split("\n") if l.strip().isdigit()]
    assert lines == ["1", "2", "3"]


def test_serialize_splits_packs_tight_when_card_has_trailing_silence():
    """原卡 4.2 → 12.0（dur=7.8），但「今天聊乳牛」5 字 × 0.3s/字 = 1.5s budget；
    dur 遠大於 budget → sub-cards 從 t0 緊湊排、尾段不分配字幕。
    「今天聊」(3 字) = 0.9s → 4.2 → 5.1；「乳牛」(2 字) = 0.6s → 5.1 → 5.7。
    剩 5.7→12.0 (6.3s) 不指派字幕，避免 sub-card 1 被推進靜音裡。
    """
    cards = srt_io.parse(SAMPLE)
    out = srt_io.serialize(cards, splits={2: ["今天聊", "乳牛"]})
    assert "00:00:04,200 --> 00:00:05,100" in out
    assert "00:00:05,100 --> 00:00:05,700" in out


def test_serialize_splits_falls_back_to_proportional_when_tight():
    """原卡很短（5 字裝在 1s 內），budget 1.5s > dur 1.0s → 退回比例分配貼滿整段。"""
    text = "1\n00:00:00,000 --> 00:00:01,000\n今天聊乳牛\n"
    cards = srt_io.parse(text)
    out = srt_io.serialize(cards, splits={1: ["今天聊", "乳牛"]})
    # 3/5 與 2/5
    assert "00:00:00,000 --> 00:00:00,600" in out
    assert "00:00:00,600 --> 00:00:01,000" in out


def test_serialize_splits_ignores_single_segment():
    """splits 只給 1 段視為沒切：走 else 分支、文字維持原句、idx 不重編。"""
    cards = srt_io.parse(SAMPLE)
    text, idx_map = srt_io.serialize_with_map(cards, splits={2: ["不會被當 override"]})
    assert idx_map == [(1, 0), (2, 0)]
    assert "今天聊乳牛" in text
    assert "不會被當 override" not in text


def test_serialize_with_map_returns_composite_lookup():
    cards = srt_io.parse(SAMPLE)
    _, idx_map = srt_io.serialize_with_map(cards, splits={2: ["前", "後"]})
    # 新 idx 1 = 原 (1,0)；新 idx 2 = 原 (2,0)；新 idx 3 = 原 (2,1)
    assert idx_map == [(1, 0), (2, 0), (2, 1)]


def test_serialize_splits_with_overrides_on_other_card():
    """同時送 overrides[1] 改文字 + splits[2] 切第 2 卡 → 兩個都生效，序號連續。"""
    cards = srt_io.parse(SAMPLE)
    out = srt_io.serialize(
        cards, overrides={1: "改過的第一句"}, splits={2: ["a", "b"]}
    )
    assert "改過的第一句" in out
    assert "\n2\n" in out and "\n3\n" in out


def test_split_does_not_shift_later_cards_times():
    """使用者的核心擔憂：切卡 2 之後，卡 3/4/5 的時間必須一毫秒都不動，
    子卡時間必須關在原卡 [start, end] 內、單調且不重疊，整份 SRT 時間全域不回頭。"""
    cards = [
        {"idx": 1, "start": 0.0, "end": 5.0, "text": "第一卡"},
        {"idx": 2, "start": 5.0, "end": 12.0, "text": "這張要被切成三段的長卡內容"},
        {"idx": 3, "start": 12.0, "end": 18.0, "text": "第三卡"},
        {"idx": 4, "start": 18.0, "end": 26.0, "text": "第四卡"},
        {"idx": 5, "start": 26.0, "end": 32.0, "text": "第五卡"},
    ]
    text, idx_map = srt_io.serialize_with_map(
        cards, splits={2: ["這張要被切", "成三段的", "長卡內容"]}
    )
    out = srt_io.parse(text)
    assert len(out) == 7  # 5 - 1 + 3

    # (1) 切卡後面的卡：時間逐位元不變（只有序號重編）
    by_new = {c["idx"]: c for c in out}
    assert (by_new[5]["start"], by_new[5]["end"]) == (12.0, 18.0)
    assert (by_new[6]["start"], by_new[6]["end"]) == (18.0, 26.0)
    assert (by_new[7]["start"], by_new[7]["end"]) == (26.0, 32.0)
    # 前面的卡也不動
    assert (by_new[1]["start"], by_new[1]["end"]) == (0.0, 5.0)

    # (2) 子卡時間關在原卡 [5.0, 12.0] 內、單調、不重疊
    subs = [by_new[2], by_new[3], by_new[4]]
    for s in subs:
        assert 5.0 <= s["start"] <= s["end"] <= 12.0
    for a, b in zip(subs, subs[1:]):
        assert a["end"] <= b["start"] + 1e-9

    # (3) 整份 SRT 時間全域單調不回頭
    for a, b in zip(out, out[1:]):
        assert a["start"] <= b["start"] + 1e-9

    # (4) idx_map 翻譯正確：新卡 5 對應原卡 3（deletions/鏡頭標記靠這個搬家）
    assert idx_map[4] == (3, 0)
    assert idx_map[1] == (2, 0) and idx_map[3] == (2, 2)
# --- 功能2A：time_overrides（手動拖拉改時間）---


def test_time_override_on_unsplit_card():
    """未切卡的時間被覆寫成新的 start/end。"""
    cards = srt_io.parse(SAMPLE)
    out = srt_io.serialize(cards, time_overrides={(2, 0): (5.0, 9.0)})
    assert "00:00:05,000 --> 00:00:09,000" in out
    # 第 1 卡沒被覆寫，維持原時間
    assert "00:00:00,000 --> 00:00:04,200" in out


def test_time_override_does_not_change_idx_map():
    """時間覆寫不影響 idx_map（編號不變）。"""
    cards = srt_io.parse(SAMPLE)
    _, idx_map = srt_io.serialize_with_map(
        cards, time_overrides={(1, 0): (1.0, 2.0)}
    )
    assert idx_map == [(1, 0), (2, 0)]


def test_time_override_partial_on_split_keeps_char_alloc():
    """切句 + 只覆寫其中一段時間 → 被覆寫段用手動值，未覆寫段仍走字數分配。"""
    cards = srt_io.parse(SAMPLE)
    # 第 2 卡（4.2–12.0）切成兩段，只手動改第 1 段（part 0）
    text, idx_map = srt_io.serialize_with_map(
        cards,
        splits={2: ["前半段", "後半段"]},
        time_overrides={(2, 0): (4.2, 6.0)},
    )
    assert idx_map == [(1, 0), (2, 0), (2, 1)]
    # part 0 用手動值
    assert "00:00:04,200 --> 00:00:06,000" in text
    # part 1 沒被覆寫 → 仍是 allocate_split_times 算出的值（不等於手動段）
    alloc = srt_io.allocate_split_times(4.2, 12.0, ["前半段", "後半段"])
    p1_start = srt_io.seconds_to_srt_ts(alloc[1][0])
    assert p1_start in text


# --- 功能2B：merges（把字卡併進上一張，時間 = 上一張.start → 被併卡.end）---


def test_merge_extends_previous_end_and_drops_card():
    """併卡 2 進卡 1：只剩 2 張卡；卡 1 結束時間延到卡 2 的結束，卡 2 不單獨輸出。
    合併後文字由 caller 用 override 落在卡 1（這裡驗證時間 + 掉卡）。"""
    cards = [
        {"idx": 1, "start": 0.0, "end": 2.0, "text": "你好"},
        {"idx": 2, "start": 2.0, "end": 4.0, "text": "世界"},
        {"idx": 3, "start": 4.0, "end": 6.0, "text": "再見"},
    ]
    text, idx_map = srt_io.serialize_with_map(
        cards, overrides={1: "你好世界"}, merges={2}
    )
    # 只剩卡 1、卡 3 兩張輸出（卡 2 被併掉）
    assert idx_map == [(1, 0), (3, 0)]
    assert text.count("-->") == 2
    # 卡 1：合併文字 + 時間延到 4.0
    assert "你好世界" in text
    assert "00:00:00,000 --> 00:00:04,000" in text


def test_merge_first_card_has_no_previous_ignored():
    """第一張卡沒有上一張可併 → 忽略 merge，照常輸出。"""
    cards = [
        {"idx": 1, "start": 0.0, "end": 2.0, "text": "你好"},
        {"idx": 2, "start": 2.0, "end": 4.0, "text": "世界"},
    ]
    text, idx_map = srt_io.serialize_with_map(cards, merges={1})
    assert idx_map == [(1, 0), (2, 0)]
    assert text.count("-->") == 2


def test_merge_extends_to_merged_card_time_override_end():
    """被併卡若有 time_override，延伸的結束時間用 override 的 end。"""
    cards = [
        {"idx": 1, "start": 0.0, "end": 2.0, "text": "A"},
        {"idx": 2, "start": 2.0, "end": 4.0, "text": "B"},
    ]
    text, _ = srt_io.serialize_with_map(
        cards, merges={2}, time_overrides={(2, 0): (2.0, 5.5)}
    )
    assert "00:00:00,000 --> 00:00:05,500" in text
    assert text.count("-->") == 1


def test_merge_folds_into_previous_split_last_part():
    """上一張是切句卡 → 併進它的最後一段（延伸最後一段的結束時間）。"""
    cards = [
        {"idx": 1, "start": 0.0, "end": 4.0, "text": "前後"},
        {"idx": 2, "start": 4.0, "end": 6.0, "text": "尾"},
    ]
    text, idx_map = srt_io.serialize_with_map(
        cards, splits={1: ["前", "後"]}, merges={2}
    )
    # 切句 2 段 + 併掉卡 2 → 仍 2 張，最後一張是 (1,1)
    assert idx_map == [(1, 0), (1, 1)]
    assert text.count("-->") == 2
    # (1,1) 的結束延到卡 2 的結束 6.0
    assert "00:00:06,000" in text


def test_merge_multiple_chain_into_first():
    """卡 2、3 同時併進卡 1 → 只剩 1 張，時間 0→6。"""
    cards = [
        {"idx": 1, "start": 0.0, "end": 2.0, "text": "一"},
        {"idx": 2, "start": 2.0, "end": 4.0, "text": "二"},
        {"idx": 3, "start": 4.0, "end": 6.0, "text": "三"},
    ]
    text, idx_map = srt_io.serialize_with_map(cards, merges={2, 3})
    assert idx_map == [(1, 0)]
    assert text.count("-->") == 1
    assert "00:00:00,000 --> 00:00:06,000" in text


def test_split_sec_per_char_matches_frontend_constant():
    """防漂移：app.js 的 SPLIT_SEC_PER_CHAR 必須跟後端 srt_io 同值。

    切卡 sub-card 時間在前端（app.js expandedCards）與後端
    （srt_io.allocate_split_times）各算一次，共用 0.3s/字 這個常數；任一邊改了
    沒同步另一邊，存檔前後 UI 會跳動。這裡直接讀 app.js 比對，改任一邊忘了改
    另一邊就會紅。
    """
    import re
    from pathlib import Path

    app_js = (
        Path(__file__).resolve().parent.parent
        / "podcast_toolkit" / "web" / "static" / "app.js"
    )
    src = app_js.read_text(encoding="utf-8")
    m = re.search(r"const SPLIT_SEC_PER_CHAR\s*=\s*([0-9.]+)\s*;", src)
    assert m, "app.js 找不到 const SPLIT_SEC_PER_CHAR 宣告（檔案結構變了？）"
    assert float(m.group(1)) == srt_io.SPLIT_SEC_PER_CHAR, (
        f"前端 app.js SPLIT_SEC_PER_CHAR={m.group(1)} 與後端 "
        f"srt_io.SPLIT_SEC_PER_CHAR={srt_io.SPLIT_SEC_PER_CHAR} 不一致；兩邊要同步改。"
    )
