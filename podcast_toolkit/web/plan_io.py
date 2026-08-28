"""把影片剪輯原型匯出的「剪輯指令」套進一集：cuts → episode.yaml、字幕 → _v2.srt。

原型（static/video-edit-prototype.html）讓人在瀏覽器裡剪片、改字幕、擺標題卡，按
「輸出剪輯指令」吐出一份 plan JSON。這個模組是那份 JSON 在後端的收件人——把它的
真來源寫回這一集既有的欄位，讓 assemble 那條管線原封不動地照舊跑。

標題卡（plan.cards）寫進 episode.yaml 的 title_cards；assemble 會把它們跟字幕燒在
同一個 ASS（見 title_cards 模組）。座標存的是**畫面比例**不是像素，所以同一份卡在
YT（橫）與 Reels（直）都放得到相對位置一樣的地方。

刻意不做的事（不是漏掉）：
- 被剪掉的句子（dropped）與剪後時間（outStart/outEnd）一律忽略：字幕以**原始時間軸**
  整份寫回，哪幾句該掉、時間該往前挪多少，交給 assemble.cut_intervals_from_cfg +
  filter_srt_by_intervals 依 cuts 自己算。同一件事只留一個真相。
"""
from __future__ import annotations

import json
from typing import Any

import yaml

from podcast_toolkit import srt_io, title_cards
from podcast_toolkit.episode import Episode
from podcast_toolkit.fsutil import atomic_write_text

# 與原型 video-edit-prototype.js 的 PLAN_VERSION 對齊；不合就整份拒收
PLAN_VERSION = 1

# 原型送 hex 色（#rrggbb 好給 <input type=color> 用），episode.yaml 存 ASS 色碼
_STYLE_HEX_KEYS = {
    "primary_colour_hex": "primary_colour",
    "outline_colour_hex": "outline_colour",
}
# 直接照抄的數值欄位（皆為 build_style_string 讀得到的鍵）
_STYLE_NUM_KEYS = (
    "font_size", "bold", "border_style", "outline", "shadow", "margin_v", "alignment",
)


class PlanError(ValueError):
    """剪輯指令格式不合。呼叫端轉成 400，訊息直接給使用者看。"""


def _num(v: Any, label: str) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise PlanError(f"{label} 不是數字：{v!r}")
    if n != n or n in (float("inf"), float("-inf")):
        raise PlanError(f"{label} 不是有效數字：{v!r}")
    return n


def _ass_colour(hex_str: Any, label: str) -> str:
    """#rrggbb → &H00BBGGRR（ASS 是 AABBGGRR，跟 HTML 的 RGB 順序相反）。"""
    s = str(hex_str or "").strip().lstrip("#")
    if len(s) != 6 or any(c not in "0123456789abcdefABCDEF" for c in s):
        raise PlanError(f"{label} 應為 #rrggbb 色碼：{hex_str!r}")
    rr, gg, bb = s[0:2], s[2:4], s[4:6]
    return f"&H00{bb.upper()}{gg.upper()}{rr.upper()}"


def normalize_style(style: Any) -> dict[str, Any]:
    """原型的 style 物件 → episode.yaml 的 subtitle_style 片段（只含有給的欄位）。

    走 config.merge 的自動深合併：這裡只寫使用者實際調過的鍵，沒給的鍵仍吃 defaults.yaml。
    """
    if style is None:
        return {}
    if not isinstance(style, dict):
        raise PlanError("style 應為物件")
    out: dict[str, Any] = {}
    name = style.get("font_name")
    if name is not None:
        if not str(name).strip():
            raise PlanError("style.font_name 不能是空字串")
        out["font_name"] = str(name).strip()
    for k in _STYLE_NUM_KEYS:
        if style.get(k) is None:
            continue
        v = _num(style[k], f"style.{k}")
        out[k] = int(v) if float(v).is_integer() else v
    for src, dst in _STYLE_HEX_KEYS.items():
        if style.get(src) is None:
            continue
        out[dst] = _ass_colour(style[src], f"style.{src}")
    return out


def parse_plan(raw: Any) -> dict[str, Any]:
    """驗證＋正規化一份剪輯指令。任何一處不合就丟 PlanError——不做「盡量吃一點」的
    容錯：半套進去的剪輯指令比明講失敗更難發現。"""
    obj = raw
    if isinstance(obj, (str, bytes)):
        try:
            obj = json.loads(obj)
        except (ValueError, UnicodeDecodeError) as e:
            raise PlanError(f"不是合法 JSON：{e}")
    if not isinstance(obj, dict):
        raise PlanError("這不是一份剪輯指令（應為 JSON 物件）")
    if obj.get("version") != PLAN_VERSION:
        raise PlanError(f"版本不合：需要 {PLAN_VERSION}，收到 {obj.get('version')!r}")
    for k in ("cuts", "subtitles", "cards"):
        if not isinstance(obj.get(k), list):
            raise PlanError(f"缺少或格式錯誤：{k}")

    cuts: list[list[float]] = []
    for i, c in enumerate(obj["cuts"]):
        if isinstance(c, dict):
            a = _num(c.get("start"), f"cuts[{i}].start")
            b = _num(c.get("end"), f"cuts[{i}].end")
        elif isinstance(c, (list, tuple)) and len(c) == 2:
            a = _num(c[0], f"cuts[{i}][0]")
            b = _num(c[1], f"cuts[{i}][1]")
        else:
            raise PlanError(f"cuts[{i}] 應為 [起, 迄]")
        if b <= a:
            raise PlanError(f"cuts[{i}] 迄點不大於起點")
        cuts.append([round(max(0.0, a), 3), round(b, 3)])
    cuts.sort()

    subs: list[dict[str, Any]] = []
    empty = 0
    for i, x in enumerate(obj["subtitles"]):
        if not isinstance(x, dict):
            raise PlanError(f"subtitles[{i}] 應為物件")
        a = _num(x.get("start"), f"subtitles[{i}].start")
        b = _num(x.get("end"), f"subtitles[{i}].end")
        if b <= a:
            raise PlanError(f"subtitles[{i}] 迄點不大於起點")
        text = ("" if x.get("text") is None else str(x["text"])).strip()
        if not text:
            # 空白句寫進 SRT 只會變成一張空卡（下游會照燒一個空事件），略過並回報
            empty += 1
            continue
        subs.append({"start": round(max(0.0, a), 3), "end": round(b, 3), "text": text})
    subs.sort(key=lambda s: (s["start"], s["end"]))

    cards: list[dict[str, Any]] = []
    cards_empty = 0
    for i, x in enumerate(obj["cards"]):
        if not isinstance(x, dict):
            raise PlanError(f"cards[{i}] 應為物件")
        a = _num(x.get("start"), f"cards[{i}].start")
        b = _num(x.get("end"), f"cards[{i}].end")
        if b <= a:
            raise PlanError(f"cards[{i}] 迄點不大於起點")
        tpl = str(x.get("tpl") or "")
        if tpl not in title_cards.TEMPLATES:
            raise PlanError(
                f"cards[{i}] 版型不認得：{tpl!r}"
                f"（可用：{'、'.join(sorted(title_cards.TEMPLATES))}）"
            )
        text = ("" if x.get("text") is None else str(x["text"])).strip()
        if not text:
            # 空白卡燒出來就是一個空底框，比沒有更糟；略過並回報
            cards_empty += 1
            continue
        d = title_cards.TEMPLATES[tpl]
        cards.append({
            "start": round(max(0.0, a), 3),
            "end": round(b, 3),
            "tpl": tpl,
            "text": text,
            "scale": round(_num(x.get("scale", 100), f"cards[{i}].scale"), 2),
            "x": round(_num(x.get("x", d["x"]), f"cards[{i}].x"), 4),
            "y": round(_num(x.get("y", d["y"]), f"cards[{i}].y"), 4),
        })
    cards.sort(key=lambda c: (c["start"], c["end"]))

    return {
        "cuts": cuts,
        "subtitles": subs,
        "cards": cards,
        "style": normalize_style(obj.get("style")),
        "empty_subtitles": empty,
        "empty_cards_skipped": cards_empty,
    }


def apply_plan(ep: Episode, plan: dict[str, Any]) -> dict[str, Any]:
    """把 parse_plan 的產物寫進這一集。回摘要供 API 回傳。

    寫兩個檔：_v2.srt（先備份 .bak，比照 /api/shift-srt）與 episode.yaml。
    全部驗證與轉換都在寫檔前做完，所以中途不會留下半套的 SRT。
    """
    v2 = ep.output_v2_srt()
    cards = [
        {"idx": i + 1, "start": s["start"], "end": s["end"], "text": s["text"]}
        for i, s in enumerate(plan["subtitles"])
    ]
    new_srt = srt_io.serialize(cards)

    yaml_path = ep.dir / "episode.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    if plan["cuts"]:
        data["cuts"] = [list(c) for c in plan["cuts"]]
    else:
        data.pop("cuts", None)
    if plan["cards"]:
        data["title_cards"] = [dict(c) for c in plan["cards"]]
    else:
        data.pop("title_cards", None)
    if plan["style"]:
        # 逐鍵疊加，不整段換掉：使用者可能在 yaml 手寫過這裡沒送的欄位
        base = dict(data.get("subtitle_style") or {})
        base.update(plan["style"])
        data["subtitle_style"] = base
    new_yaml = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)

    backup = None
    if v2.exists():
        backup = v2.with_suffix(v2.suffix + ".bak")
        atomic_write_text(backup, v2.read_text(encoding="utf-8"))
    v2.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(v2, new_srt)
    atomic_write_text(yaml_path, new_yaml)

    return {
        "cuts": len(plan["cuts"]),
        "cut_total_sec": round(sum(b - a for a, b in plan["cuts"]), 3),
        "subtitles": len(plan["subtitles"]),
        "empty_subtitles_skipped": plan["empty_subtitles"],
        "cards": len(plan["cards"]),
        "empty_cards_skipped": plan["empty_cards_skipped"],
        "style_keys": sorted(plan["style"].keys()),
        "srt_path": str(v2.relative_to(ep.dir)),
        "srt_backup": str(backup.relative_to(ep.dir)) if backup else None,
    }
