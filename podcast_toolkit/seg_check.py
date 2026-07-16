"""podcast check-seg：斷句體檢（純讀取，不改字幕）。

對一集的 _final_v2.srt 掃五類「斷句異味卡」，輸出卡號清單讓人到編輯器跳對：
  ① 過長        顯示字數 > resegment.hardlen（resegment 硬切上限，照定義不該超）
  ② 掛尾連接詞   卡尾停在 dangle_endings（然後/所以/因為…），讀起來像沒講完
  ③ 過短非反應詞 顯示字數 < SHORT_CHARS 且不在 reaction_words（多為自然停頓，可忽略大半）
  ④ 跨卡切詞     相鄰兩卡間隔 ≤ straddle_gap、且 jieba 詞（≥2 字）跨越卡界
                （然/後、耳/機…）；jieba 未安裝時此味跳過並註明
  ⑤ 時間重疊     相鄰卡時間相疊（前卡未結束、後卡已開始）→ UI 疊字/兩短句重疊；
                有 speakers（分軌）時只記同講者重疊，跨講者同時說話是設計不算

門檻全讀 cfg["resegment"]，字數用 sentence_resegment._is_punct 算（與斷句引擎同一把尺）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from podcast_toolkit import word_break
from podcast_toolkit.episode import Episode
from podcast_toolkit.sentence_resegment import _is_punct

# 過短門檻：顯示字數 < N 且非反應詞才算異味（對齊 sentence_resegment.run 的 min_chars 預設）
SHORT_CHARS = 3
# ④ 跨卡切詞：相鄰卡間隔門檻預設（秒）；rcfg["straddle_gap"] 可覆寫
STRADDLE_GAP = 0.35
# ④ 取前卡尾/後卡頭各 N 字接起來跑 jieba（詞界判斷不需要整卡）
_STRADDLE_WIN = 6
# ⑤ 時間重疊：相鄰卡 前卡 end > 後卡 start + 此秒數 才判為重疊（濾浮點雜訊）
_OVERLAP_EPS = 0.001
# 行首 [Mic1] / [郝慧川] 之類講者標籤（對齊 ingest_breeze._LABEL_RE）
_LABEL_RE = re.compile(r"^\s*\[\s*([^\]]+?)\s*\]\s*")


def _disp_len(s: str) -> int:
    """顯示字數（不含標點 / 空白），與 sentence_resegment._disp_len 同尺。"""
    return sum(1 for c in s if not _is_punct(c))


def _strip_label(s: str) -> str:
    """剝掉行首 [MicN]/[講者名] 前綴（_v2.srt 通常已無，防含講者版直接體檢）。"""
    return _LABEL_RE.sub("", s).strip()


def parse_srt(path: Path) -> list[tuple[int, str]]:
    """回傳 [(idx, text), ...]；解析方式對齊 resegment.run（re.split 空行分塊）。"""
    return [(idx, txt) for idx, txt, _, _ in parse_srt_timed(path)]


def _ts2s(ts: str) -> float:
    """'HH:MM:SS,mmm' → 秒。"""
    h, m, rest = ts.strip().split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt_timed(path: Path) -> list[tuple[int, str, float, float]]:
    """回傳 [(idx, text, start, end), ...]；④ 跨卡切詞需要卡間隔才多帶時間。"""
    raw = path.read_text(encoding="utf-8").strip()
    out: list[tuple[int, str, float, float]] = []
    for blk in re.split(r"\n\s*\n", raw):
        lines = [l for l in blk.split("\n") if l.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        try:
            idx = int(lines[0].strip())
        except ValueError:
            continue
        try:
            a, b = lines[1].split("-->")
            start, end = _ts2s(a), _ts2s(b)
        except (ValueError, IndexError):
            start = end = 0.0
        out.append((idx, " ".join(lines[2:]).strip(), start, end))
    return out


def _scan_straddle(cards: list, gap_thr: float) -> list | None:
    """④ 跨卡切詞：回傳 [(前卡idx, 後卡idx, 跨界詞, 前卡文字, 後卡文字), ...]。

    只檢「兩卡都帶時間（4 欄）、間隔 ≤ gap_thr、前卡尾與後卡頭皆 CJK」的相鄰對；
    前卡尾 6 字＋後卡頭 6 字接起來跑 jieba，卡界若不在詞界上＝有詞跨卡。
    jieba 缺席 → 回傳 None（呼叫端註明略過）。
    """
    if not word_break.available():
        return None
    out = []
    for prev, cur in zip(cards, cards[1:]):
        if len(prev) < 4 or len(cur) < 4:
            continue                                  # 無時間資訊（舊 2 欄格式）→ 跳過
        pidx, ptxt, _, pend = prev[0], prev[1], prev[2], prev[3]
        cidx, ctxt, cstart = cur[0], cur[1], cur[2]
        if cstart - pend > gap_thr:
            continue                                  # 卡間真氣口 → 不算切詞
        pt, ct = _strip_label(ptxt), _strip_label(ctxt)
        if not pt or not ct:
            continue
        if not (word_break.is_cjk(pt[-1]) and word_break.is_cjk(ct[0])):
            continue
        tail, head = pt[-_STRADDLE_WIN:], ct[:_STRADDLE_WIN]
        pts = word_break.word_break_ok(tail + head)
        pos = len(tail)
        if pts is None or pos in pts:
            continue                                  # 卡界剛好在詞界 → 健康
        a = max(p for p in pts if p < pos)            # 跨界詞起點
        b = min(p for p in pts if p > pos)            # 跨界詞終點
        out.append((pidx, cidx, (tail + head)[a:b], ptxt, ctxt))
    return out


def _scan_overlap(cards: list, speakers: dict | None = None) -> list:
    """⑤ 時間重疊：回傳 [(前卡idx, 後卡idx, 重疊秒數), ...]。

    只檢帶時間（4 欄）的相鄰對，依 start 排序；前卡 end > 後卡 start + _OVERLAP_EPS
    即重疊。speakers 提供時只記「同一講者」的重疊——不同（或未知）講者的時間重疊是分軌
    雙人同時說話的既定設計（見 srt_merge），不算異味；speakers 空／None（單軌）→ 全記。
    """
    timed = [c for c in cards if len(c) >= 4]
    ordered = sorted(timed, key=lambda c: float(c[2]))
    speakers = speakers or {}
    out = []
    for prev, cur in zip(ordered, ordered[1:]):
        if speakers and speakers.get(int(prev[0])) != speakers.get(int(cur[0])):
            continue
        ov = float(prev[3]) - float(cur[2])
        if ov > _OVERLAP_EPS:
            out.append((prev[0], cur[0], round(ov, 3)))
    return out


def scan(cards: list, rcfg: dict, speakers: dict | None = None) -> dict:
    """回傳五類異味卡。純函式，方便測試。

    cards 每項 (idx, text) 或 (idx, text, start, end)；④⑤ 只在帶時間時檢。
    speakers（{idx: 講者}）供 ⑤ 區分分軌同時說話；不傳＝單軌，⑤ 記所有重疊。
    """
    hardlen = rcfg["hardlen"]
    dangle = tuple(rcfg["dangle_endings"])
    reaction = set(rcfg["reaction_words"])

    longc, dangling, shortc = [], [], []
    for c in cards:
        idx, txt = c[0], c[1]
        if _disp_len(txt) > hardlen:
            longc.append((idx, txt))
        # 掛尾：去尾端標點/空白後，結尾是連接詞
        tail = re.sub(r"[。！？，、；：\s]+$", "", txt)
        if tail.endswith(dangle):
            which = next((d for d in dangle if tail.endswith(d)), "")
            dangling.append((idx, txt, which))
        compact = "".join(c2 for c2 in txt if not c2.isspace())
        if _disp_len(txt) < SHORT_CHARS and compact not in reaction:
            shortc.append((idx, txt))
    straddle = _scan_straddle(cards, float(rcfg.get("straddle_gap", STRADDLE_GAP)))
    overlap = _scan_overlap(cards, speakers)
    return {"long": longc, "dangle": dangling, "short": shortc,
            "straddle": straddle, "overlap": overlap}


def _report(name: str, total: int, res: dict, *, limit: int, hardlen: int) -> None:
    longc, dangling, shortc = res["long"], res["dangle"], res["short"]
    straddle = res.get("straddle")
    overlap = res.get("overlap") or []
    print(f"\n{name}　共 {total} 卡")
    print(f"  ① 過長(>{hardlen}字)：{len(longc)} 卡"
          + ("　✅ 健康（resegment 硬切上限就是此值）" if not longc else "　⚠️ 該為 0，超出代表沒切乾淨"))
    for idx, t in longc[:limit]:
        print(f"      #{idx}  {t}  ({_disp_len(t)}字)")
    if len(longc) > limit:
        print(f"      …還有 {len(longc) - limit} 卡")

    print(f"  ② 掛尾連接詞：{len(dangling)} 卡　⚠️ 主要訊號——卡尾若無真實停頓就把連接詞挪到下一卡")
    for idx, t, d in dangling[:limit]:
        print(f"      #{idx}  …{t[-12:]}  (掛「{d}」)")
    if len(dangling) > limit:
        print(f"      …還有 {len(dangling) - limit} 卡")

    print(f"  ③ 過短(<{SHORT_CHARS}字非反應詞)：{len(shortc)} 卡　🔸 多為自然單詞停頓，可忽略大半")
    for idx, t in shortc[:limit]:
        print(f"      #{idx}  「{t}」")
    if len(shortc) > limit:
        print(f"      …還有 {len(shortc) - limit} 卡")

    if straddle is None:
        print("  ④ 跨卡切詞：jieba 未安裝，跨卡切詞檢測略過")
    else:
        print(f"  ④ 跨卡切詞：{len(straddle)} 對　⚠️ 詞被切在兩卡（然/後、耳/機…），讀感最傷")
        for pidx, cidx, word, pt, ct in straddle[:limit]:
            print(f"      #{pidx}→#{cidx}  切「{word}」：…{pt[-10:]}｜{ct[:10]}…")
        if len(straddle) > limit:
            print(f"      …還有 {len(straddle) - limit} 對")

    print(f"  ⑤ 時間重疊：{len(overlap)} 對　⚠️ 前卡未結束下一卡已開始 → 疊字/兩短句重疊")
    for pidx, cidx, ov in overlap[:limit]:
        print(f"      #{pidx}→#{cidx}  疊 {ov:.3f}s")
    if len(overlap) > limit:
        print(f"      …還有 {len(overlap) - limit} 對")

    tot = len(longc) + len(dangling) + len(shortc) + len(straddle or []) + len(overlap)
    pct = tot / total * 100 if total else 0.0
    print(f"  → 異味卡合計 {tot} / {total}（{pct:.1f}%）")


def run(episode_dir: Path, *, limit: int = 12) -> int:
    ep = Episode(episode_dir)
    src = ep.output_v2_srt()
    if not src.exists():
        print(f"✗ 找不到字幕：{src}", file=sys.stderr)
        srt_list = list(ep.subdir("output").glob("*.srt"))
        if srt_list:
            print("  03_成品/ 內現有 srt：", file=sys.stderr)
            for p in srt_list:
                print(f"    {p.name}", file=sys.stderr)
        return 3

    rcfg = ep.cfg["resegment"]
    # ④ 用 jieba 判詞界：詞典可由 resegment.jieba_dict 覆寫；glossary（來賓名等）進詞典
    word_break.configure(rcfg.get("jieba_dict"))
    word_break.add_words([g.get("canonical") for g in (ep.cfg.get("glossary") or [])])
    cards = parse_srt_timed(src)
    # ⑤ 時間重疊：載入 speakers（分軌集才有）→ 只記同講者重疊，跨講者同時說話不算
    from podcast_toolkit import cameras_io
    speakers = cameras_io.load(ep.output_v2_speakers_json())
    res = scan(cards, rcfg, speakers)
    _report(src.name, len(cards), res, limit=limit, hardlen=rcfg["hardlen"])
    return 0
