"""單卡內手動折兩行（兩個人同時說話 → 一人一行）的端到端行為。

編輯器把折行存成卡片文字裡的 "\\n"，之後要一路活著走到燒字：
    textOverrides("甲\\n乙") → srt_io.serialize 多行 cue
                             → srt_io.parse 讀回同一個 "\\n"
                             → assemble._write_ass_from_srt 轉成 ASS 的 "\\N"
這條鏈以前完全沒有測試覆蓋（前端 textContent 會把換行吃掉，所以從來沒有帶
"\\n" 的卡片走到過後端）。
"""

from podcast_toolkit import assemble, srt_io

WRAPPED = "甲：你先講\n乙：不，你先"


def _dialogue_texts(ass_text: str) -> list[str]:
    """ASS Dialogue 行的 Text 欄（Format 的第 10 欄，之後不再有逗號分欄）。"""
    return [
        line.split(":", 1)[1].split(",", 9)[9]
        for line in ass_text.splitlines()
        if line.startswith("Dialogue:")
    ]


def test_serialize_writes_wrapped_card_as_two_srt_lines():
    """折行的卡在 SRT 裡就是兩行文字，序號與時間軸不受影響。"""
    cards = [
        {"idx": 1, "start": 0.0, "end": 2.0, "text": "原句"},
        {"idx": 2, "start": 2.0, "end": 4.0, "text": "下一句"},
    ]
    out = srt_io.serialize(cards, overrides={1: WRAPPED})
    assert "甲：你先講\n乙：不，你先" in out
    # 折行不能被誤判成 cue 邊界 → 第二張卡還在，序號仍是 2
    assert "\n2\n00:00:02,000 --> 00:00:04,000\n下一句" in out


def test_parse_round_trips_wrapped_card():
    """寫出去再讀回來，換行位置與後面的卡都要一模一樣。"""
    cards = [
        {"idx": 1, "start": 0.0, "end": 2.0, "text": WRAPPED},
        {"idx": 2, "start": 2.0, "end": 4.0, "text": "下一句"},
    ]
    back = srt_io.parse(srt_io.serialize(cards))
    assert [c["text"] for c in back] == [WRAPPED, "下一句"]
    assert [c["idx"] for c in back] == [1, 2]


def test_ass_converts_wrapped_card_to_backslash_n(tmp_path):
    """燒字用的 ASS 只吃 "\\N"；留著原始 "\\n" 會把 Dialogue 行截斷。"""
    src, dst = tmp_path / "a.srt", tmp_path / "a.ass"
    src.write_text(
        srt_io.serialize([{"idx": 1, "start": 0.0, "end": 2.0, "text": WRAPPED}]),
        encoding="utf-8",
    )
    assemble._write_ass_from_srt(src, dst, 1920, 1080)
    ass = dst.read_text(encoding="utf-8")
    assert _dialogue_texts(ass) == ["甲：你先講\\N乙：不，你先"]
    # WrapStyle: 0 = 尊重手動換行、不再自動折；沒有它 libass 會照寬度重排
    assert "WrapStyle: 0" in ass


def test_ass_does_not_wrap_single_line_card(tmp_path):
    """沒折過的卡不能被動到（不設字數門檻、不自動折行）。"""
    src, dst = tmp_path / "a.srt", tmp_path / "a.ass"
    long_text = "這是一句刻意寫得很長的字幕用來確認系統不會自作主張幫我折行"
    src.write_text(
        srt_io.serialize([{"idx": 1, "start": 0.0, "end": 2.0, "text": long_text}]),
        encoding="utf-8",
    )
    assemble._write_ass_from_srt(src, dst, 1920, 1080)
    assert _dialogue_texts(dst.read_text(encoding="utf-8")) == [long_text]
