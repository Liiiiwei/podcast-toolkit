"""標題卡 → ASS 事件。

原型（video-edit-prototype）在影片上疊三種版型的標題卡：大字報 / 下標條 /
引言框。這裡把同一份資料翻成 libass 畫得出來的 Dialogue 事件，讓合成出來的
成品跟原型看到的一致。

為什麼走 ASS 而不是 ffmpeg 的 drawtext/drawbox：
- 卡片跟字幕燒在同一個 subtitles filter，合成的濾鏡鏈完全不用動
- drawtext 沒有自動換行，也沒有「底框跟著文字長度縮放」

為什麼版型的底框用 ASS drawing（`\\p1`）而不是 Style 的 BorderStyle=3：
ffmpeg 的 force_style token 沒有 `Style.` 前綴時會套用到**所有** style，
BorderStyle / BackColour 會被字幕樣式一起蓋掉。inline override tag 的優先權
高於 style，所以卡片整組用 inline tag 畫，對 force_style 免疫。

已知近似：libass 真正的字寬要有字型檔才量得到，這裡用「全形 1em、半形 0.5em」
估算來決定換行點與底框大小。中文為主的短標題誤差很小，長英文句子會偏窄。
"""

from __future__ import annotations

import unicodedata
from typing import Any

# 每個版型對齊原型 video-edit-prototype.js 的 CARD_TEMPLATES 與 .vt-card-view CSS：
#   base   字級佔畫面寬的比例（× scale/100）
#   x / y  預設位置（畫面比例，且是「中心點」—— CSS 用 translate(-50%,-50%)）
#   max_w  整塊卡最寬佔畫面寬多少（對應 CSS max-width）
#   bar    左側色條寬（em），對應 lower 的 border-left
#   pad_*  內距（em）
#   line_h 行高（em），只用來估底框高度
TEMPLATES: dict[str, dict[str, Any]] = {
    "big": {
        "name": "重點大字",
        "base": 0.055, "x": 0.5, "y": 0.5, "max_w": 0.72,
        "bar": 0.0, "bar_color": None,
        "pad_l": 0.7, "pad_r": 0.7, "pad_y": 0.35, "line_h": 1.25,
        "bg": "#000000", "bg_alpha": 0.62,
        "border": None, "border_w": 0.0, "border_alpha": 1.0,
        "bold": True, "italic": False, "align": "center", "quotes": False,
    },
    "lower": {
        "name": "下標條",
        "base": 0.034, "x": 0.2, "y": 0.84, "max_w": 0.92,
        "bar": 0.18, "bar_color": "#ff7849",
        "pad_l": 0.55, "pad_r": 0.8, "pad_y": 0.3, "line_h": 1.25,
        "bg": "#000000", "bg_alpha": 0.72,
        "border": None, "border_w": 0.0, "border_alpha": 1.0,
        "bold": True, "italic": False, "align": "left", "quotes": False,
    },
    "quote": {
        "name": "引言框",
        "base": 0.038, "x": 0.5, "y": 0.5, "max_w": 0.62,
        "bar": 0.0, "bar_color": None,
        "pad_l": 0.8, "pad_r": 0.8, "pad_y": 0.5, "line_h": 1.25,
        "bg": "#000000", "bg_alpha": 0.5,
        "border": "#ffffff", "border_w": 0.06, "border_alpha": 0.55,
        "bold": False, "italic": True, "align": "left", "quotes": True,
    },
}

DEFAULT_TPL = "big"

# 引號裝飾（原型的 ::before / ::after），連同 0.15em 的外距
_QUOTE_OPEN = "\u201c"
_QUOTE_CLOSE = "\u201d"
_QUOTE_GAP = 0.15
_QUOTE_ALPHA = 0.7


def _ass_color(hex_rgb: str) -> str:
    """#rrggbb → &HBBGGRR&（ASS 的色序跟 HTML 相反）。"""
    h = hex_rgb.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{b}{g}{r}&".upper()


def _ass_alpha(opacity: float) -> str:
    """CSS 的不透明度 → ASS 的透明度（0 = 完全不透明）。"""
    a = int(round((1.0 - max(0.0, min(1.0, float(opacity)))) * 255))
    return f"&H{a:02X}&"


def _char_em(ch: str) -> float:
    """單一字元佔幾個 em —— 全形/寬字 1.0，其餘 0.5。"""
    return 1.0 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 0.5


def _line_em(s: str) -> float:
    return sum(_char_em(c) for c in s)


def _wrap(text: str, budget_em: float) -> list[str]:
    """依 em 估算逐字換行；使用者自己打的換行一律保留。"""
    out: list[str] = []
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        raw = raw.strip()
        if not raw:
            out.append("")
            continue
        cur, w = "", 0.0
        for ch in raw:
            cw = _char_em(ch)
            if cur and w + cw > budget_em:
                out.append(cur)
                cur, w = ch, cw
            else:
                cur += ch
                w += cw
        out.append(cur)
    return out or [""]


def _escape(s: str) -> str:
    """ASS 的 `{}` 是 override block 的界線，會把使用者打的大括號吃掉。

    改成全形括號比讓字消失好 —— 中文標題本來就少用半形大括號。
    """
    return s.replace("{", "\uff5b").replace("}", "\uff5d")


def normalize(raw_cards: Any) -> list[dict[str, Any]]:
    """把 plan.cards（或 episode.yaml 的 title_cards）正規化成內部形狀。

    壞掉的卡直接丟掉而不是拋例外：這是「畫面上多一張字卡」的裝飾層，
    不該讓整集合成失敗。缺欄位就用版型預設值補。
    """
    cards: list[dict[str, Any]] = []
    for item in raw_cards or []:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            continue
        if not (end > start >= 0):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        tpl = str(item.get("tpl") or DEFAULT_TPL)
        if tpl not in TEMPLATES:
            tpl = DEFAULT_TPL
        t = TEMPLATES[tpl]
        try:
            scale = float(item.get("scale", 100))
        except (TypeError, ValueError):
            scale = 100.0
        try:
            x = float(item.get("x", t["x"]))
            y = float(item.get("y", t["y"]))
        except (TypeError, ValueError):
            x, y = t["x"], t["y"]
        cards.append({
            "start": start,
            "end": end,
            "tpl": tpl,
            "text": text,
            "scale": max(20.0, min(400.0, scale)),
            "x": max(0.0, min(1.0, x)),
            "y": max(0.0, min(1.0, y)),
        })
    cards.sort(key=lambda c: (c["start"], c["end"]))
    return cards


def drop_by_intervals(
    cards: list[dict[str, Any]], intervals: list[tuple[float, float]]
) -> list[dict[str, Any]]:
    """把起點落在剪除區間內的卡拿掉。

    判準跟字幕的 filter_srt_by_intervals 一模一樣（`a <= start < b`），
    因為兩者燒在同一個時間軸上 —— 字幕與卡片都是在 select/setpts 之前燒，
    每塊的 t 仍是原始時間軸，所以不需要位移，只需要丟。
    """
    if not intervals:
        return list(cards)
    return [
        c for c in cards
        if not any(a <= c["start"] < b for a, b in intervals)
    ]


def _fmt(t: float) -> str:
    t = max(0.0, float(t))
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - h * 3600 - m * 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _rect(w: float, h: float) -> str:
    """ASS drawing：從 (0,0) 起算的實心矩形（座標取整數，libass 用 1/1 scale）。"""
    w, h = int(round(w)), int(round(h))
    return f"m 0 0 l {w} 0 l {w} {h} l 0 {h}"


def _ring(w: float, h: float, bw: float) -> str:
    """ASS drawing：中空矩形框（外框順時針、內框逆時針 → 非零環繞規則挖洞）。

    不用 `\\bord` 畫框，是因為字幕樣式的 force_style 會設 BorderStyle：
    BorderStyle=3 時 libass 把「描邊」畫成不透明色塊，框就變成實心。
    自己畫兩個環的話，長什麼樣完全由 drawing 決定，樣式改不到。
    """
    w, h = int(round(w)), int(round(h))
    b = max(1, int(round(bw)))
    return (
        f"m 0 0 l {w} 0 l {w} {h} l 0 {h} "
        f"m {b} {b} l {b} {h - b} l {w - b} {h - b} l {w - b} {b}"
    )


def ass_events(
    cards: list[dict[str, Any]],
    play_res_x: int,
    play_res_y: int,
    *,
    font_name: str = "Arial",
    layer: int = 5,
) -> list[str]:
    """把標題卡畫成 ASS Dialogue 行（底框一行、文字一行）。

    回傳的每個字串已含結尾換行，可以直接接在 [Events] 後面。
    """
    rows: list[str] = []
    fn = _escape(str(font_name or "Arial"))
    for c in cards:
        t = TEMPLATES[c["tpl"]]
        fs = play_res_x * t["base"] * (c["scale"] / 100.0)
        if fs <= 0:
            continue
        side = t["bar"] + t["pad_l"] + t["pad_r"]
        budget = max(1.0, t["max_w"] * play_res_x / fs - side)
        lines = _wrap(c["text"], budget)
        widths = [_line_em(s) for s in lines]
        if t["quotes"]:
            widths[0] += _line_em(_QUOTE_OPEN) + _QUOTE_GAP
            widths[-1] += _line_em(_QUOTE_CLOSE) + _QUOTE_GAP
            qa = _ass_alpha(_QUOTE_ALPHA)
            lines = list(lines)
            lines[0] = f"{{\\alpha{qa}}}{_QUOTE_OPEN}{{\\alpha&H00&}}" + _escape(lines[0])
            lines[-1] = (
                (lines[-1] if len(lines) == 1 else _escape(lines[-1]))
                + f"{{\\alpha{qa}}}{_QUOTE_CLOSE}{{\\alpha&H00&}}"
            )
        else:
            lines = [_escape(s) for s in lines]

        text_em = max(widths) if widths else 0.0
        box_w = (side + text_em) * fs
        box_h = (len(lines) * t["line_h"] + t["pad_y"] * 2) * fs
        cx = c["x"] * play_res_x
        cy = c["y"] * play_res_y
        left = cx - box_w / 2.0
        top = cy - box_h / 2.0

        # 底框／色條／外框都是獨立的 drawing 事件：\an7 讓 drawing 的 (0,0)
        # 對到 \pos 給的左上角，形狀完全由 drawing 決定，force_style 碰不到。
        def _shape(color: str, opacity: float, path: str) -> str:
            tags = (
                f"\\an7\\pos({left:.1f},{top:.1f})\\bord0\\shad0"
                f"\\1c{_ass_color(color)}\\1a{_ass_alpha(opacity)}\\p1"
            )
            return (
                f"Dialogue: {layer},{_fmt(c['start'])},{_fmt(c['end'])},Default,,0,0,0,,"
                f"{{{tags}}}{path}{{\\p0}}\n"
            )

        rows.append(_shape(t["bg"], t["bg_alpha"], _rect(box_w, box_h)))
        if t["bar"] > 0 and t["bar_color"]:
            rows.append(
                _shape(t["bar_color"], 1.0, _rect(t["bar"] * fs, box_h))
            )
        if t["border"] and t["border_w"] > 0:
            rows.append(
                _shape(
                    t["border"], t["border_alpha"],
                    _ring(box_w, box_h, t["border_w"] * fs),
                )
            )

        # 文字：置中版用 \an5 對畫面中心，靠左版用 \an4 對「底框左緣＋左內距」
        if t["align"] == "center":
            anchor = f"\\an5\\pos({cx:.1f},{cy:.1f})"
        else:
            anchor = f"\\an4\\pos({left + (t['bar'] + t['pad_l']) * fs:.1f},{cy:.1f})"
        tags = (
            f"{anchor}\\fn{fn}\\fs{fs:.1f}"
            f"\\b{1 if t['bold'] else 0}\\i{1 if t['italic'] else 0}"
            # \3a/\4a 全透明：字幕樣式若選了 BorderStyle=3（不透明色塊），
            # libass 會拿描邊色在文字後面畫實心塊；ASS 沒有 inline 的
            # BorderStyle 可以關，只能把那兩個顏色調成看不見。
            "\\1c&HFFFFFF&\\1a&H00&\\bord0\\shad0\\3a&HFF&\\4a&HFF&\\q2"
        )
        # 換行符先在 f-string 外面接好：Python 3.12 以前，f-string 的表達式段
        # 不能含反斜線（打包用的是 3.9，寫在裡面會整個模組 SyntaxError）。
        body = "\\N".join(lines)
        rows.append(
            f"Dialogue: {layer + 1},{_fmt(c['start'])},{_fmt(c['end'])},Default,,0,0,0,,"
            f"{{{tags}}}{body}\n"
        )
    return rows
