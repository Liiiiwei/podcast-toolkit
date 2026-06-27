"""podcast check-seg：斷句體檢（純讀取，不改字幕）。

對一集的 _final_v2.srt 掃三類「斷句異味卡」，輸出卡號清單讓人到編輯器跳對：
  ① 過長        顯示字數 > resegment.hardlen（resegment 硬切上限，照定義不該超）
  ② 掛尾連接詞   卡尾停在 dangle_endings（然後/所以/因為…），讀起來像沒講完
  ③ 過短非反應詞 顯示字數 < SHORT_CHARS 且不在 reaction_words（多為自然停頓，可忽略大半）

門檻全讀 cfg["resegment"]，字數用 sentence_resegment._is_punct 算（與斷句引擎同一把尺）。
"""
import re
import sys
from pathlib import Path

from podcast_toolkit.episode import Episode
from podcast_toolkit.sentence_resegment import _is_punct

# 過短門檻：顯示字數 < N 且非反應詞才算異味（對齊 sentence_resegment.run 的 min_chars 預設）
SHORT_CHARS = 3


def _disp_len(s: str) -> int:
    """顯示字數（不含標點 / 空白），與 sentence_resegment._disp_len 同尺。"""
    return sum(1 for c in s if not _is_punct(c))


def parse_srt(path: Path) -> list[tuple[int, str]]:
    """回傳 [(idx, text), ...]；解析方式對齊 resegment.run（re.split 空行分塊）。"""
    raw = path.read_text(encoding="utf-8").strip()
    out: list[tuple[int, str]] = []
    for blk in re.split(r"\n\s*\n", raw):
        lines = [l for l in blk.split("\n") if l.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        try:
            idx = int(lines[0].strip())
        except ValueError:
            continue
        out.append((idx, " ".join(lines[2:]).strip()))
    return out


def scan(cards: list[tuple[int, str]], rcfg: dict) -> dict:
    """回傳三類異味卡。純函式，方便測試。"""
    hardlen = rcfg["hardlen"]
    dangle = tuple(rcfg["dangle_endings"])
    reaction = set(rcfg["reaction_words"])

    longc, dangling, shortc = [], [], []
    for idx, txt in cards:
        if _disp_len(txt) > hardlen:
            longc.append((idx, txt))
        # 掛尾：去尾端標點/空白後，結尾是連接詞
        tail = re.sub(r"[。！？，、；：\s]+$", "", txt)
        if tail.endswith(dangle):
            which = next((d for d in dangle if tail.endswith(d)), "")
            dangling.append((idx, txt, which))
        compact = "".join(c for c in txt if not c.isspace())
        if _disp_len(txt) < SHORT_CHARS and compact not in reaction:
            shortc.append((idx, txt))
    return {"long": longc, "dangle": dangling, "short": shortc}


def _report(name: str, total: int, res: dict, *, limit: int, hardlen: int) -> None:
    longc, dangling, shortc = res["long"], res["dangle"], res["short"]
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

    tot = len(longc) + len(dangling) + len(shortc)
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
    cards = parse_srt(src)
    res = scan(cards, rcfg)
    _report(src.name, len(cards), res, limit=limit, hardlen=rcfg["hardlen"])
    return 0
