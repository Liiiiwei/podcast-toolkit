"""標題卡 → ASS 事件：座標／字級映射、剪除過濾、以及對 force_style 的免疫。"""
import re

import pytest

from podcast_toolkit import title_cards

W, H = 1920, 1080


def _card(**over):
    c = {"start": 1.0, "end": 3.0, "tpl": "big", "text": "魔王關卡",
         "scale": 100, "x": 0.5, "y": 0.5}
    c.update(over)
    return c


def _tags(line: str) -> str:
    """取事件的第一組 override tag。"""
    return re.search(r"\{(.*?)\}", line).group(1)


def _fields(line: str) -> list[str]:
    return line.split(",", 9)


def _path_size(line: str) -> tuple[int, int]:
    """從 drawing 事件的路徑取出矩形寬高（`m 0 0 l w 0 l w h l 0 h`）。"""
    nums = [int(n) for n in re.findall(r"-?\d+", line.split("\\p1}")[1])]
    return nums[4], nums[7]


# ── 色碼與透明度 ────────────────────────────────────────────────
def test_colour_is_bgr_and_alpha_is_inverted_opacity():
    """ASS 是 &HBBGGRR、alpha 是「透明度」——兩個都跟直覺相反。"""
    assert title_cards._ass_color("#ff7849") == "&H4978FF&"
    assert title_cards._ass_alpha(1.0) == "&H00&"   # 全不透明 → alpha 0
    assert title_cards._ass_alpha(0.0) == "&HFF&"
    assert title_cards._ass_alpha(0.62) == "&H61&"


# ── 版型：三種都畫得出來 ─────────────────────────────────────────
@pytest.mark.parametrize("tpl", sorted(title_cards.TEMPLATES))
def test_every_template_renders(tpl):
    rows = title_cards.ass_events([_card(tpl=tpl)], W, H)
    assert rows, f"{tpl} 沒產出事件"
    assert all(r.startswith("Dialogue: ") and r.endswith("\n") for r in rows)
    # 最後一列是文字，前面都是 drawing（\p1）
    assert "\\p1" not in rows[-1] and _card()["text"] not in "".join(rows[:-1])


def test_lower_third_has_accent_bar_and_quote_has_hollow_ring():
    lower = title_cards.ass_events([_card(tpl="lower", text="Liwei Sia｜顧問")], W, H)
    assert any("&H4978FF&" in r for r in lower), "下標條少了橘色左條"

    quote = title_cards.ass_events([_card(tpl="quote", text="剪片是判斷問題")], W, H)
    ring = [r for r in quote if r.count(" m ") >= 1 and "\\p1" in r]
    # 中空框＝一條路徑裡有兩個 m（外框順時針、內框逆時針）
    assert any(r.count("m ") == 2 for r in ring), "引言框不是中空的，會蓋住畫面"
    assert "“" in quote[-1] and "”" in quote[-1], "引言框少了引號"


def test_every_template_draws_a_backdrop_behind_the_text():
    """底框是三個版型共同的視覺主體，字疊在畫面上沒它會糊掉。

    突變測試抓到的缺口：把底框那行拿掉，原本 15 條測試全綠。
    """
    for tpl, spec in title_cards.TEMPLATES.items():
        rows = title_cards.ass_events([_card(tpl=tpl)], W, H)
        want = f"\\1c{title_cards._ass_color(spec['bg'])}" \
               f"\\1a{title_cards._ass_alpha(spec['bg_alpha'])}"
        # 底框＝實心矩形（單一 m）＋版型指定的底色與透明度
        back = [r for r in rows if want in r and "\\p1" in r and r.count("m ") == 1]
        assert len(back) == 1, f"{tpl} 沒有底框（或畫了不只一個）"
        w, h = _path_size(back[0])
        assert w > 0 and h > 0, f"{tpl} 底框是零尺寸，等於沒畫"


def test_backdrop_grows_with_the_text():
    """底框跟著文字長度縮放——這是選 ASS drawing 而不是 drawtext 的理由。"""
    short = title_cards.ass_events([_card(text="短")], W, H)[0]
    long_ = title_cards.ass_events([_card(text="這是一句明顯長很多的標題文字")], W, H)[0]
    assert _path_size(long_)[0] > _path_size(short)[0], "底框沒跟著文字變寬"


# ── 對 force_style 免疫 ─────────────────────────────────────────
def test_text_event_neutralises_borderstyle_3():
    """ASS 沒有 inline 的 BorderStyle override。字幕樣式若選 3（不透明色塊），
    libass 會拿描邊色在字後面畫實心塊 —— 只能把描邊／陰影色調成全透明繞開。"""
    t = _tags(title_cards.ass_events([_card()], W, H)[-1])
    assert "\\3a&HFF&" in t and "\\4a&HFF&" in t and "\\bord0" in t and "\\shad0" in t


def test_layer_is_above_subtitles():
    rows = title_cards.ass_events([_card()], W, H, layer=5)
    layers = [int(_fields(r)[0].split(": ")[1]) for r in rows]
    assert layers == sorted(layers) and min(layers) >= 5


# ── 座標與字級映射 ──────────────────────────────────────────────
def test_centre_anchor_maps_ratio_to_pixels():
    """CSS 的 translate(-50%,-50%) ↔ ASS 的 \\an5 + \\pos(x*W, y*H)。"""
    t = _tags(title_cards.ass_events([_card(x=0.25, y=0.75)], W, H)[-1])
    assert "\\an5" in t and "\\pos(480.0,810.0)" in t


def test_font_size_follows_width_and_scale():
    base = title_cards.TEMPLATES["big"]["base"]
    for scale in (50, 100, 150):
        t = _tags(title_cards.ass_events([_card(scale=scale)], W, H)[-1])
        assert f"\\fs{W * base * scale / 100:.1f}" in t


def test_ratio_coords_survive_portrait_output():
    """同一張卡在 Reels（直式）要落在相對位置一樣的地方。"""
    t = _tags(title_cards.ass_events([_card(x=0.5, y=0.5)], 1080, 1920)[-1])
    assert "\\pos(540.0,960.0)" in t


# ── 換行與跳脫 ──────────────────────────────────────────────────
def test_long_text_wraps_and_disables_auto_wrap():
    long = "後製這一關才是真正的魔王關卡不是拍攝也不是腳本"
    t = title_cards.ass_events([_card(text=long)], W, H)[-1]
    assert "\\N" in t, "長標題沒換行，會超出畫面"
    assert "\\q2" in _tags(t), "沒關掉自動換行，自己算的 \\N 會被覆寫"


def test_braces_in_text_do_not_become_override_tags():
    t = title_cards.ass_events([_card(text="{\\fs99}壞掉")], W, H)[-1]
    assert "{\\fs99}" not in t


# ── normalize 與剪除過濾 ────────────────────────────────────────
def test_normalize_is_the_lenient_layer():
    """normalize 餵的是已經落地的 episode.yaml —— 壞掉的卡回退或丟掉，
    不該讓整集合成失敗。嚴格把關在 plan_io.parse_plan（那裡會拋錯給使用者看）。"""
    out = title_cards.normalize([
        {"start": 1, "end": 2, "text": "有字"},                 # 沒給 tpl → 預設
        {"start": 3, "end": 4, "tpl": "big", "text": "  "},     # 空白卡 → 丟
        {"start": 5, "end": 5, "tpl": "big", "text": "零長"},   # 迄點不大於起點 → 丟
        {"start": 6, "end": 7, "tpl": "不存在", "text": "壞版型"},  # → 回退預設版型
        "根本不是卡",
    ])
    assert [(c["start"], c["tpl"]) for c in out] == [
        (1.0, title_cards.DEFAULT_TPL), (6.0, title_cards.DEFAULT_TPL)
    ]
    d = title_cards.TEMPLATES[title_cards.DEFAULT_TPL]
    assert (out[0]["x"], out[0]["y"], out[0]["scale"]) == (d["x"], d["y"], 100.0)


def test_normalize_clamps_out_of_range_position_and_scale():
    out = title_cards.normalize([
        {"start": 1, "end": 2, "text": "拖出畫面", "x": 9, "y": -3, "scale": 9999},
    ])
    assert (out[0]["x"], out[0]["y"], out[0]["scale"]) == (1.0, 0.0, 400.0)


def test_drop_by_intervals_uses_same_rule_as_subtitles():
    """判準跟 filter_srt_by_intervals 一致：起點落在剪除區就丟，時間不位移。"""
    cards = [_card(start=1.0, end=3.0), _card(start=5.0, end=7.0),
             _card(start=9.0, end=11.0)]
    kept = title_cards.drop_by_intervals(cards, [(4.0, 6.0)])
    assert [c["start"] for c in kept] == [1.0, 9.0]
    # 邊界：等於起點要丟、等於迄點要留
    assert title_cards.drop_by_intervals([_card(start=4.0, end=4.5)], [(4.0, 6.0)]) == []
    assert len(title_cards.drop_by_intervals([_card(start=6.0, end=6.5)], [(4.0, 6.0)])) == 1
