"""雙行字幕（兩位講者同時說話 → 上下兩排）排版與 ASS 產出測試。

前端預覽（app.js renderCaption）早就會把同時間的多張卡疊成上下兩行；
這裡測的是「出片端也照同一套規則排」——dual_line.layout 決定排序，
assemble._write_ass_from_srt 把排序翻成 ASS 每事件的 MarginV。
"""
import json

import yaml

from podcast_toolkit import assemble, dual_line


def _card(start, end, text="字", speaker=None):
    return {"start": start, "end": end, "text": text, "speaker": speaker}


def _events(cards, bottom_anchored=True):
    return dual_line.layout(cards, bottom_anchored=bottom_anchored)


# ── layout：什麼時候分排、誰在上 ──────────────────────────────────────

def test_layout_empty():
    assert dual_line.layout([]) == []


def test_no_overlap_keeps_single_row():
    evs = _events([_card(0, 2, speaker="a"), _card(3, 5, speaker="b")])
    assert len(evs) == 2
    assert all(e["stack_lines"] == 0 for e in evs)


def test_touching_cards_do_not_stack():
    """前卡結束 == 後卡開始：相接不算重疊。"""
    evs = _events([_card(0, 2, speaker="a"), _card(2, 4, speaker="b")])
    assert len(evs) == 2
    assert all(e["stack_lines"] == 0 for e in evs)


def test_full_overlap_stacks_by_speaker_order():
    """同時開始 → 分不出誰先，改用講者 key 字典序：a 在上、b 在下。"""
    cards = [_card(0, 3, "甲", "a"), _card(0, 3, "乙", "b")]
    bottom = {e["speaker"]: e["stack_lines"] for e in _events(cards)}
    assert bottom == {"a": 1, "b": 0}          # 底部對齊：a 往上讓一行
    centered = {e["speaker"]: e["stack_lines"] for e in _events(cards, False)}
    assert centered == {"a": 0, "b": 1}        # 中央對齊：a 留原位、b 往下一行


def test_partial_overlap_splits_only_the_overlapping_part():
    """a 只在跟 b 重疊那段被抬起來；沒重疊的前半仍在原位。"""
    evs = _events([_card(10, 13, "甲", "a"), _card(12, 15, "乙", "b")])
    a_evs = sorted((e for e in evs if e["speaker"] == "a"), key=lambda e: e["start"])
    b_evs = [e for e in evs if e["speaker"] == "b"]
    assert len(a_evs) == 2
    assert (a_evs[0]["start"], a_evs[0]["end"], a_evs[0]["stack_lines"]) == (10, 12, 0)
    assert (a_evs[1]["start"], a_evs[1]["end"], a_evs[1]["stack_lines"]) == (12, 13, 1)
    # b 全程都在最下排 → 相鄰同排片段要併回一個事件，不能切成兩筆
    assert len(b_evs) == 1
    assert (b_evs[0]["start"], b_evs[0]["end"]) == (12, 15)


def test_stack_order_follows_start_time_not_card_order():
    """先開始的排上面，跟卡在 list 裡的位置無關（這裡故意把後開始的放前面）。

    新來的字從下排進場、舊的往上讓 —— 這條規則也是 hold_tail 不會讓字幕
    抬起來又落回原位的原因（stack 只可能隨時間上升）。
    """
    evs = _events([_card(11, 14, "甲", "a"), _card(10, 12, "乙", "b")])
    a_ev = next(e for e in evs if e["speaker"] == "a")
    assert a_ev["stack_lines"] == 0                                       # 後開始 → 留在下排
    assert max(e["stack_lines"] for e in evs if e["speaker"] == "b") == 1  # 先開始 → 被往上推


def test_tiny_overlap_does_not_stack():
    """重疊短於 MIN_STACK_SEC（分軌合併常見的擦邊重疊）→ 不分排，維持現狀。"""
    evs = _events([_card(10, 13, "甲", "a"), _card(12.9, 15, "乙", "b")])
    assert len(evs) == 2
    assert all(e["stack_lines"] == 0 for e in evs)


def test_tiny_solo_slice_absorbed_into_stacked_slice():
    """卡幾乎整段都在重疊：開頭那 0.05 秒不該讓字幕先掉下來再彈上去（閃一下）。"""
    evs = _events([_card(10, 13, "甲", "a"), _card(10.05, 15, "乙", "b")])
    a_evs = [e for e in evs if e["speaker"] == "a"]
    assert len(a_evs) == 1
    assert (a_evs[0]["start"], a_evs[0]["end"], a_evs[0]["stack_lines"]) == (10, 13, 1)


def test_three_speakers_stack_three_rows():
    cards = [_card(0, 3, "甲", "a"), _card(0, 3, "乙", "b"), _card(0, 3, "丙", "c")]
    assert {e["speaker"]: e["stack_lines"] for e in _events(cards)} == {"a": 2, "b": 1, "c": 0}
    assert {e["speaker"]: e["stack_lines"] for e in _events(cards, False)} == {"a": 0, "b": 1, "c": 2}


def test_multiline_card_counts_as_two_lines():
    """下排是兩行字的卡 → 上排要讓開兩行的高度。"""
    evs = _events([_card(0, 3, "甲", "a"), _card(0, 3, "乙一\n乙二", "b")])
    top = next(e for e in evs if e["speaker"] == "a")
    assert top["stack_lines"] == 2


def test_same_speaker_overlap_still_separates():
    """同一講者的卡互相重疊（罕見）也要分排，不能疊在一起。"""
    evs = _events([_card(0, 3, "甲一", "a"), _card(1, 4, "甲二", "a")])
    at_overlap = [e for e in evs if e["stack_lines"] == 1]
    assert len(at_overlap) == 1
    assert (at_overlap[0]["start"], at_overlap[0]["end"]) == (1, 3)


def test_event_order_is_visually_top_to_bottom():
    """輸出順序 = 畫面上從上到下（兩種對齊都是 a 在上）。

    不是為了好看：libass 在垂直置中對齊（Reels alignment=10）時**會忽略 MarginV**，
    改用自己的碰撞堆疊把重疊事件錯開，那時**檔案順序就是顯示順序**（先寫的在上）。
    排錯的話 Reels 的上下兩排會跟 YT 和編輯器預覽相反。
    """
    cards = [_card(0, 3, "甲", "a"), _card(0, 3, "乙", "b")]
    assert [e["speaker"] for e in _events(cards)] == ["a", "b"]
    assert [e["speaker"] for e in _events(cards, False)] == ["a", "b"]


def test_missing_speaker_does_not_crash():
    evs = _events([_card(0, 3, "甲", None), _card(0, 3, "乙", "a")])
    assert sorted(e["stack_lines"] for e in evs) == [0, 1]


# ── hold：換人接話時延長上一句，製造重疊 ──────────────────────────

def test_hold_extends_previous_card_on_speaker_change():
    """字幕來自單軌混音轉錄，卡是一張接一張產生的（實測五集共只有 1 筆真重疊）。
    換人接話時讓上一句多留一段時間，重疊才存在、雙行才有機會出現。"""
    held = dual_line.hold_tail([_card(0, 2, "甲", "a"), _card(2.1, 5, "乙", "b")], 1.0)
    assert held[0]["end"] == 3.0
    assert held[1]["end"] == 5      # 接話那句不動


def test_hold_ignores_same_speaker():
    """同一人連著講的兩張卡不是「接話」，延長只會讓他自己占兩排。"""
    held = dual_line.hold_tail([_card(0, 2, "甲一", "a"), _card(2.1, 5, "甲二", "a")], 1.0)
    assert held[0]["end"] == 2


def test_hold_ignores_long_gap():
    """間隔超過門檻＝換話題不是接話，不 hold。"""
    held = dual_line.hold_tail([_card(0, 2, "甲", "a"), _card(2.5, 5, "乙", "b")], 1.0, 0.3)
    assert held[0]["end"] == 2


def test_hold_never_outlives_the_card_it_overlaps():
    """接話那句很短時，上一句不能比它還晚消失（畫面上會剩一排孤字）。"""
    held = dual_line.hold_tail([_card(0, 2, "甲", "a"), _card(2.1, 2.7, "乙", "b")], 1.0)
    assert held[0]["end"] == 2.7


def test_hold_stops_before_speaker_speaks_again():
    """a 說 → b 插一句 → a 又接話：a 的前一句不能撐到自己下一句，否則同一人占兩排。"""
    held = dual_line.hold_tail(
        [_card(0, 2, "甲一", "a"), _card(2.1, 4, "乙", "b"), _card(2.8, 6, "甲二", "a")], 1.0
    )
    assert held[0]["end"] == 2.8


def test_hold_skips_when_overlap_would_be_too_short():
    """只延得出 0.1 秒重疊 → 抬起來只閃一下（layout 也會併掉），不如不抬。"""
    held = dual_line.hold_tail([_card(0, 2, "甲", "a"), _card(2.2, 2.3, "乙", "b")], 1.0)
    assert held[0]["end"] == 2


def test_hold_leaves_already_overlapping_cards_alone():
    held = dual_line.hold_tail([_card(0, 3, "甲", "a"), _card(2, 5, "乙", "b")], 1.0)
    assert held[0]["end"] == 3


def test_hold_disabled_returns_cards_untouched():
    held = dual_line.hold_tail([_card(0, 2, "甲", "a"), _card(2.1, 5, "乙", "b")], 0)
    assert held[0]["end"] == 2


def test_hold_negative_value_never_shortens_cards():
    """設定填成負數（手滑）只能當作關掉，不能反過來把字幕提早收掉。"""
    held = dual_line.hold_tail([_card(0, 2, "甲", "a"), _card(2.1, 5, "乙", "b")], -1.0)
    assert held[0]["end"] == 2


def test_hold_needs_both_speakers_tagged():
    """有卡沒貼到講者標 → 分不出是不是換人，不 hold。"""
    held = dual_line.hold_tail([_card(0, 2, "甲", "a"), _card(2.1, 5, "乙", None)], 1.0)
    assert held[0]["end"] == 2


def test_hold_extends_in_both_directions_and_lifts_the_one_it_extends():
    """a→b、b→a 都算換人接話，兩個方向都要延長，覆蓋率才不會只剩一半。

    方向不必挑：被延長的永遠是先開始的那張，而 layout 讓先開始的排上面，
    所以延長的效果一律是「上一句往上讓位、讓完就消失」。
    """
    for first, second in (("a", "b"), ("b", "a")):
        held = dual_line.hold_tail(
            [_card(0, 2, "先", first), _card(2.1, 5, "後", second)], 1.0
        )
        assert held[0]["end"] == 3.0, f"{first}→{second} 沒延長"
        lifted = [e for e in _events(held) if e["stack_lines"]]
        assert [e["text"] for e in lifted] == ["先"], f"{first}→{second} 抬錯人"


def test_hold_then_layout_produces_two_rows():
    """hold → layout 串起來：接話的那段 a 在上排、b 在下排。"""
    cards = dual_line.hold_tail([_card(0, 2, "甲", "a"), _card(2.1, 5, "乙", "b")], 1.0)
    stacked = [e for e in _events(cards) if e["stack_lines"] == 1]
    assert len(stacked) == 1
    assert stacked[0]["speaker"] == "a"
    assert (stacked[0]["start"], stacked[0]["end"]) == (2.1, 3.0)


def test_hold_never_makes_a_card_bounce_between_rows():
    """一張卡兩端都有人接話時，不能抬起 → 落下 → 再抬起。

    魁哥集實測到「又要解鎖一個新的職業」這句在 2.5 秒內上下彈了兩次
    （MarginV 178 → 0 → 178）：開頭被前一句的 hold 咬住而抬起，中段沒人重疊落回，
    尾巴自己被 hold 又抬起。字幕上下彈跳比沒有雙行還糟。
    """
    cards = dual_line.hold_tail(
        [_card(0, 2, "甲一", "b"), _card(2.1, 4, "乙", "a"), _card(4.1, 6, "甲二", "b")], 1.0
    )
    rows = [
        e["stack_lines"]
        for e in sorted((e for e in _events(cards) if e["text"] == "乙"),
                        key=lambda e: e["start"])
    ]
    # 允許「尾巴往上讓位給新來的字」，不允許抬起後又落回原位
    assert rows in ([0], [0, 1]), f"排位序列 {rows} 代表這句在畫面上彈了回來"


# ── 排數 → 像素位移 ────────────────────────────────────────────────

def test_stack_offset_is_zero_for_anchor_row():
    assert dual_line.stack_offset_px({"stack_lines": 0}, 48) == 0


def test_stack_offset_scales_with_line_count():
    one = dual_line.stack_offset_px({"stack_lines": 1}, 48)
    assert one == round(48 * dual_line.LINE_HEIGHT_RATIO)
    assert dual_line.stack_offset_px({"stack_lines": 2}, 96) == round(2 * 96 * dual_line.LINE_HEIGHT_RATIO)


# ── ASS 產出（assemble._write_ass_from_srt）────────────────────────

YT_STYLE = {"font_size": 48, "margin_v": 100}
REELS_STYLE = {"font_size": 96, "margin_v": 480, "alignment": 10}


def _write_srt(path, cards):
    from podcast_toolkit import srt_io
    path.write_text(srt_io.serialize(cards), encoding="utf-8")


def _dialogues(ass_text: str) -> list[dict]:
    """拆 ASS 的 Dialogue 行 → {start, end, margin_v, text}（欄位序見 Format 行）。"""
    out = []
    for line in ass_text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        f = line.split(":", 1)[1].split(",", 9)
        out.append({
            "start": f[1].strip(), "end": f[2].strip(),
            "margin_v": int(f[7]), "text": f[9],
        })
    return out


def test_ass_without_speakers_keeps_margin_zero(tmp_path):
    """沒有 speakers sidecar（單軌集）→ 輸出與既有行為完全一樣：每事件 MarginV=0。"""
    src, dst = tmp_path / "a.srt", tmp_path / "a.ass"
    _write_srt(src, [
        {"idx": 1, "start": 0, "end": 2, "text": "甲"},
        {"idx": 2, "start": 1, "end": 3, "text": "乙"},
    ])
    assemble._write_ass_from_srt(src, dst, 1920, 1080)
    ds = _dialogues(dst.read_text(encoding="utf-8"))
    assert len(ds) == 2
    assert all(d["margin_v"] == 0 for d in ds)


def test_ass_dual_line_writes_per_event_margin(tmp_path):
    """重疊兩卡 → 下排沿用 style 的 MarginV（寫 0），上排寫 margin_v + 一行高。

    上排不能靠新增第二個 ASS Style 來做：ffmpeg 的 force_style 沒有 `Style.` 前綴時
    會套用到**所有** style，MarginV=100 會把第二個 style 一起蓋掉。每事件 MarginV
    是 force_style 碰不到的地方。
    """
    src, dst = tmp_path / "a.srt", tmp_path / "a.ass"
    _write_srt(src, [
        {"idx": 1, "start": 10, "end": 13, "text": "甲"},
        {"idx": 2, "start": 10, "end": 13, "text": "乙"},
    ])
    spans = [(10.0, 13.0, "a"), (10.0, 13.0, "b")]
    assemble._write_ass_from_srt(src, dst, 1920, 1080, speaker_spans=spans, style=YT_STYLE)
    ds = _dialogues(dst.read_text(encoding="utf-8"))
    assert len(ds) == 2
    line_h = round(48 * dual_line.LINE_HEIGHT_RATIO)
    assert {d["text"]: d["margin_v"] for d in ds} == {"甲": 100 + line_h, "乙": 0}


def test_ass_dual_line_reels_alignment_pushes_down(tmp_path):
    """Reels 用畫面正中央（SSA v3 alignment=10，MarginV 正值＝往下）→ 第二排往下加。"""
    src, dst = tmp_path / "a.srt", tmp_path / "a.ass"
    _write_srt(src, [
        {"idx": 1, "start": 10, "end": 13, "text": "甲"},
        {"idx": 2, "start": 10, "end": 13, "text": "乙"},
    ])
    spans = [(10.0, 13.0, "a"), (10.0, 13.0, "b")]
    assemble._write_ass_from_srt(src, dst, 1080, 1920, speaker_spans=spans, style=REELS_STYLE)
    ds = _dialogues(dst.read_text(encoding="utf-8"))
    line_h = round(96 * dual_line.LINE_HEIGHT_RATIO)
    assert {d["text"]: d["margin_v"] for d in ds} == {"甲": 0, "乙": 480 + line_h}


def test_ass_dual_line_splits_partial_overlap_into_two_events(tmp_path):
    src, dst = tmp_path / "a.srt", tmp_path / "a.ass"
    _write_srt(src, [
        {"idx": 1, "start": 10, "end": 13, "text": "甲"},
        {"idx": 2, "start": 12, "end": 15, "text": "乙"},
    ])
    spans = [(10.0, 13.0, "a"), (12.0, 15.0, "b")]
    assemble._write_ass_from_srt(src, dst, 1920, 1080, speaker_spans=spans, style=YT_STYLE)
    ds = _dialogues(dst.read_text(encoding="utf-8"))
    assert len(ds) == 3
    jia = sorted((d for d in ds if d["text"] == "甲"), key=lambda d: d["start"])
    assert [(d["start"], d["end"], d["margin_v"]) for d in jia] == [
        ("0:00:10.00", "0:00:12.00", 0),
        ("0:00:12.00", "0:00:13.00", 100 + round(48 * dual_line.LINE_HEIGHT_RATIO)),
    ]


def _sequential_srt(tmp_path):
    """真實素材的樣子：兩張卡一前一後、只差 0.1 秒，不重疊。"""
    src = tmp_path / "a.srt"
    _write_srt(src, [
        {"idx": 1, "start": 10, "end": 12, "text": "甲"},
        {"idx": 2, "start": 12.1, "end": 15, "text": "乙"},
    ])
    return src, [(10.0, 12.0, "a"), (12.1, 15.0, "b")]


def test_ass_sequential_cards_stay_single_row_without_hold(tmp_path):
    """沒開 hold → 這種一前一後的卡永遠不會分排（雙行等於沒作用）。"""
    src, spans = _sequential_srt(tmp_path)
    dst = tmp_path / "a.ass"
    assemble._write_ass_from_srt(src, dst, 1920, 1080, speaker_spans=spans, style=YT_STYLE)
    ds = _dialogues(dst.read_text(encoding="utf-8"))
    assert len(ds) == 2
    assert all(d["margin_v"] == 0 for d in ds)


def test_ass_hold_lifts_previous_card(tmp_path):
    """開了 hold → 甲被切成「原位」＋「抬起來跟乙並排」兩個事件。"""
    src, spans = _sequential_srt(tmp_path)
    dst = tmp_path / "a.ass"
    assemble._write_ass_from_srt(
        src, dst, 1920, 1080, speaker_spans=spans, style=YT_STYLE, hold_sec=1.0
    )
    ds = _dialogues(dst.read_text(encoding="utf-8"))
    assert len(ds) == 3
    lifted = [d for d in ds if d["margin_v"] > 0]
    assert len(lifted) == 1
    assert lifted[0]["text"] == "甲"
    # 甲原本 10~12，hold 1 秒 → 12~13；跟乙（12.1 起）重疊的 12.1~13 這段被抬起
    assert (lifted[0]["start"], lifted[0]["end"]) == ("0:00:12.10", "0:00:13.00")
    assert lifted[0]["margin_v"] == 100 + round(48 * dual_line.LINE_HEIGHT_RATIO)


def test_ass_hold_needs_speakers(tmp_path):
    """單軌集（沒 speakers sidecar）就算 cfg 開著 hold 也不能動時間。"""
    src, _ = _sequential_srt(tmp_path)
    dst = tmp_path / "a.ass"
    assemble._write_ass_from_srt(src, dst, 1920, 1080, hold_sec=1.0)
    ds = _dialogues(dst.read_text(encoding="utf-8"))
    assert [(d["start"], d["end"]) for d in ds] == [
        ("0:00:10.00", "0:00:12.00"), ("0:00:12.10", "0:00:15.00"),
    ]


def test_ass_speaker_matched_by_time_not_index(tmp_path):
    """出片前的 SRT 可能被 filter/shift 重編號（srt_io.serialize 一律從 1 重排），
    所以講者對照只能靠時間重疊，不能靠 idx。"""
    src, dst = tmp_path / "a.srt", tmp_path / "a.ass"
    _write_srt(src, [  # 已重編號成 1、2，但 speakers sidecar 記的是原本的 7、8
        {"idx": 1, "start": 10, "end": 13, "text": "甲"},
        {"idx": 2, "start": 10.2, "end": 13, "text": "乙"},
    ])
    spans = [(9.9, 13.1, "a"), (10.2, 13.0, "b")]
    assemble._write_ass_from_srt(src, dst, 1920, 1080, speaker_spans=spans, style=YT_STYLE)
    ds = _dialogues(dst.read_text(encoding="utf-8"))
    assert {d["text"]: d["margin_v"] for d in ds} == {
        "甲": 100 + round(48 * dual_line.LINE_HEIGHT_RATIO), "乙": 0,
    }


# ── 接線：prepare_assembly → _speaker_spans → 燒進去的那份 ASS ──────
#
# 上面測的是零件；這一段測「出片時這些零件真的有被接上」——sidecar 讀不讀得到、
# cfg 開關管不管用、時間軸有沒有跟著 SRT 一起位移。
# 素材是 conftest 的 SAMPLE_SRT：四張卡首尾相接（0~4.2、4.2~12、12~14、14~22），
# 標成 a/b/a/b 後三處都算「換人接話」，hold 1 秒必定製造出重疊。

SAMPLE_STACKED_MARGIN = 100 + round(60 * dual_line.LINE_HEIGHT_RATIO)  # defaults.yaml 的字級與底邊距


def _write_speakers(ep_dir, mapping):
    """寫 speakers sidecar（idx→講者 key，格式同 cameras sidecar）。"""
    (ep_dir / "03_成品" / "測試集_final_v2.speakers.json").write_text(
        json.dumps(mapping), encoding="utf-8"
    )


def _set_cfg(ep_dir, **kv):
    """改 episode.yaml 的設定鍵（config.merge 會拿 episode 值蓋掉 defaults）。"""
    p = ep_dir / "episode.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    data.update(kv)
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _yt_ass_margins(ep_dir):
    """跑一次 YT 出片規劃，回傳 ffmpeg 真正燒的那份 ASS 的每事件 MarginV。

    不寫死檔名：有刪段時燒的會換成「濾掉刪段卡」的另一份（_v2_assembled_*），
    那是第二個獨立的 _write_ass_from_srt 呼叫點，一樣要接上雙行。
    """
    plan = assemble.prepare_assembly(ep_dir, output_kind="yt", force=True)
    fc = plan["cmd"][plan["cmd"].index("-filter_complex") + 1]
    burned = [p for p in (ep_dir / "04_工作檔").glob("*.ass") if p.name in fc]
    assert len(burned) == 1, f"filter_complex 沒指到唯一一份 ASS：{[p.name for p in burned]}"
    return [d["margin_v"] for d in _dialogues(burned[0].read_text(encoding="utf-8"))]


def test_speaker_spans_reads_sidecar_and_applies_shift(tmp_episode_dir):
    """sidecar 只存 idx→講者，時間得從 canonical 卡取，而且要套上與 SRT 同一個 shift。

    漏掉 shift 會讓講者貼到錯的卡上（assign_speakers 是靠時間重疊對照的）。
    """
    from podcast_toolkit import srt_io
    from podcast_toolkit.episode import Episode

    _write_speakers(tmp_episode_dir, {"1": "a", "2": "b"})
    ep = Episode(tmp_episode_dir)
    cards = srt_io.parse(ep.output_v2_srt().read_text(encoding="utf-8"))
    spans = assemble._speaker_spans(ep, cards, 5.0)
    # 沒標到的卡 3、4 不進 spans
    assert [(round(a, 3), round(b, 3), s) for a, b, s in spans] == [
        (5.0, 9.2, "a"), (9.2, 17.0, "b"),
    ]


def test_speaker_spans_without_sidecar_is_none(tmp_episode_dir):
    """單軌集沒有 speakers sidecar → None，呼叫端就走原本的單行路徑。"""
    from podcast_toolkit import srt_io
    from podcast_toolkit.episode import Episode

    ep = Episode(tmp_episode_dir)
    cards = srt_io.parse(ep.output_v2_srt().read_text(encoding="utf-8"))
    assert assemble._speaker_spans(ep, cards, 0.0) is None


def test_speaker_spans_all_untagged_is_none(tmp_episode_dir):
    """sidecar 在但一張卡都沒標到（例如 idx 全對不上）→ 也要回 None，不能回空 list。"""
    from podcast_toolkit import srt_io
    from podcast_toolkit.episode import Episode

    _write_speakers(tmp_episode_dir, {"99": "a"})
    ep = Episode(tmp_episode_dir)
    cards = srt_io.parse(ep.output_v2_srt().read_text(encoding="utf-8"))
    assert assemble._speaker_spans(ep, cards, 0.0) is None


def test_prepare_assembly_dual_line_stacks_with_sidecar(tmp_episode_full):
    """分軌集（有 sidecar）+ defaults 的 hold → 燒的 ASS 真的出現第二排。"""
    _write_speakers(tmp_episode_full, {"1": "a", "2": "b", "3": "a", "4": "b"})
    margins = _yt_ass_margins(tmp_episode_full)
    # 三處換人接話各被抬起一段 → 三筆事件落在上排，其餘留在原位
    assert sorted(m for m in margins if m) == [SAMPLE_STACKED_MARGIN] * 3


def test_prepare_assembly_dual_line_off_keeps_single_row(tmp_episode_full):
    """subtitle_dual_line: false → 就算有 sidecar 也整集關掉（MarginV 全 0）。"""
    _write_speakers(tmp_episode_full, {"1": "a", "2": "b", "3": "a", "4": "b"})
    _set_cfg(tmp_episode_full, subtitle_dual_line=False)
    assert all(m == 0 for m in _yt_ass_margins(tmp_episode_full))


def test_prepare_assembly_single_track_keeps_single_row(tmp_episode_full):
    """單軌集沒 sidecar → 開關開著也 no-op，輸出與單行時代逐字相同。"""
    assert all(m == 0 for m in _yt_ass_margins(tmp_episode_full))


def test_prepare_assembly_hold_zero_keeps_single_row(tmp_episode_full):
    """subtitle_dual_line_hold: 0 → 不製造重疊，真實素材就幾乎不會有雙行。"""
    _write_speakers(tmp_episode_full, {"1": "a", "2": "b", "3": "a", "4": "b"})
    _set_cfg(tmp_episode_full, subtitle_dual_line_hold=0)
    assert all(m == 0 for m in _yt_ass_margins(tmp_episode_full))


def test_prepare_assembly_dual_line_survives_cuts(tmp_episode_full):
    """有刪段時燒的是另一份 ASS（濾掉刪段卡後重編號），那條路徑也要接上雙行。

    重編號正是講者只能靠時間對照、不能靠 idx 的原因；這裡的刪段落在所有卡之後，
    卡一張都沒少，所以雙行結果應與沒刪段時一模一樣。
    """
    _write_speakers(tmp_episode_full, {"1": "a", "2": "b", "3": "a", "4": "b"})
    _set_cfg(tmp_episode_full, cuts=[[22.5, 25.0]])
    assert sorted(m for m in _yt_ass_margins(tmp_episode_full) if m) == [SAMPLE_STACKED_MARGIN] * 3


def test_prepare_assembly_dual_line_survives_subtitle_offset(tmp_episode_full):
    """字幕位移後講者標要跟著位移：兩邊同軸才對得上，否則講者全貼到同一個人身上。"""
    _write_speakers(tmp_episode_full, {"1": "a", "2": "b", "3": "a", "4": "b"})
    _set_cfg(tmp_episode_full, subtitle_offset_sec=5.0)
    margins = _yt_ass_margins(tmp_episode_full)
    assert sorted(m for m in margins if m) == [SAMPLE_STACKED_MARGIN] * 3
