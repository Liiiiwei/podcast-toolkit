"""podcast resegment：字幕重新斷句 + 錯字修正 + whisper-guard 反幻覺。

從現有 resegment.py 改造，邏輯不變，只是參數從 config 帶入。
"""
import re
import sys
from pathlib import Path
from podcast_toolkit.episode import Episode
from podcast_toolkit.whisper_guard import WhisperGuard, GuardConfig
from podcast_toolkit.whisper_guard.vocab import filter_filler_words

# 「半句結尾」判斷的尾字集合：句子斷在這些連接詞/介詞上像是沒講完。
# web/episode_io.py 的 _flag_review 也 import 這個常數，兩處共用避免漂移。
_HALF_SENTENCE_TAIL = "很會在就把被跟和"


def ts2s(ts: str) -> float:
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def s2ts(t: float) -> str:
    h = int(t // 3600); m = int((t % 3600) // 60); s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        s += 1; ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def run(episode_dir: Path, force: bool = False) -> int:
    ep = Episode(episode_dir)
    cfg = ep.cfg
    rcfg = cfg["resegment"]

    src = ep.main_srt()
    if not src.exists():
        print(f"✗ 找不到字幕：{src}", file=sys.stderr)
        srt_list = list(ep.subdir("output").glob("*.srt"))
        if srt_list:
            print(f"  03_成品/ 內現有 srt：", file=sys.stderr)
            for p in srt_list:
                print(f"    {p.name}", file=sys.stderr)
        return 3

    out = ep.output_v2_srt()
    review = ep.review_file()

    if out.exists() and not force:
        print(f"✗ 輸出已存在：{out}", file=sys.stderr)
        print(f"  加 --force 覆寫", file=sys.stderr)
        return 1

    # whisper-guard
    guard = WhisperGuard(GuardConfig(
        char_loop_min_repeats=rcfg["whisper_guard"]["char_loop_min_repeats"],
    ))
    use_filler = rcfg["use_filler_filter"]

    fixes = cfg["fixes"]
    card_fixes = cfg["card_fixes"]
    force_break = cfg["force_break"]
    force_join = cfg["force_join"]
    maxlen = rcfg["maxlen"]
    hardlen = rcfg["hardlen"]
    gapmax = rcfg["gapmax"]
    qend = rcfg["qend_chars"]
    reaction = set(rcfg["reaction_words"])
    dangle = tuple(rcfg["dangle_endings"])

    def fix_text(s):
        for a, b in fixes:
            s = s.replace(a, b)
        return s

    def card_fix(s):
        for a, b in card_fixes:
            s = s.replace(a, b)
        return s

    def is_dangling(t):
        return t.endswith(dangle)

    guard_stats = {"loops": 0, "fillers": 0}
    guard_log = []

    def guard_clean(s):
        cleaned, removed = guard.remove_char_loops(s)
        if removed:
            guard_stats["loops"] += removed
            guard_log.append((s, cleaned))
        if use_filler:
            before = cleaned
            cleaned = filter_filler_words(cleaned).replace(" ", "")
            if cleaned != before:
                guard_stats["fillers"] += 1
                guard_log.append((before, cleaned))
        return cleaned

    # 解析 whisper 段落
    raw = src.read_text(encoding="utf-8").strip()
    segs = []
    for blk in re.split(r"\n\s*\n", raw):
        lines = [l for l in blk.split("\n") if l.strip()]
        if len(lines) < 3:
            continue
        m = re.search(r"(\d\d:\d\d:\d\d,\d\d\d)\s*-->\s*(\d\d:\d\d:\d\d,\d\d\d)", lines[1])
        txt = guard_clean(fix_text("".join(lines[2:]).replace(" ", "").replace("　", "")))
        segs.append([ts2s(m[1]), ts2s(m[2]), txt])

    # 貪婪合併
    cards = []
    cur = None
    for i, (st, en, txt) in enumerate(segs):
        if cur is None:
            cur = [st, en, txt, i, i]
            continue
        gap = st - cur[1]
        combined = cur[2] + txt
        over = len(combined) > maxlen
        if over and len(combined) <= hardlen and is_dangling(cur[2]):
            over = False
        must_break = (
            i in force_break
            or (i not in force_join and (
                gap > gapmax
                or over
                or cur[2] in reaction
                or txt in reaction
                or (len(cur[2]) >= 5 and cur[2][-1] in qend)
            ))
        )
        if must_break:
            cards.append(cur)
            cur = [st, en, txt, i, i]
        else:
            cur = [cur[0], en, combined, cur[3], i]
    if cur:
        cards.append(cur)

    # 合併後錯字
    for c in cards:
        c[2] = card_fix(c[2])

    # 寫 SRT
    with out.open("w", encoding="utf-8") as f:
        for n, (st, en, txt, _, _) in enumerate(cards, 1):
            f.write(f"{n}\n{s2ts(st)} --> {s2ts(en)}\n{txt}\n\n")

    # 寫複查清單
    risky = []
    with review.open("w", encoding="utf-8") as f:
        f.write(f"# 重新斷句複查清單\n# whisper 原始段落 {len(segs)} → 字幕卡 {len(cards)}\n\n")
        if guard_log:
            f.write(f"# whisper-guard 反幻覺清理：{len(guard_log)} 處（請確認沒有誤收合）\n")
            for before, after in guard_log:
                f.write(f"#   {before!r}  →  {after!r}\n")
            f.write("\n")
        for n, (st, en, txt, fi, li) in enumerate(cards, 1):
            flag = ""
            if len(txt) >= 4 and (txt[-1] in _HALF_SENTENCE_TAIL or txt.endswith(dangle)):
                flag = "  ⚠半句結尾"
                risky.append(n)
            if guard.is_repetitive(txt):
                flag += "  ⚠疑似重複幻覺"
                if n not in risky:
                    risky.append(n)
            f.write(f"[{n:>3}] {s2ts(st)[:-4]}-{s2ts(en)[:-4]} segs{fi}-{li} ({len(txt):>2}) {txt}{flag}\n")

    dist = {}
    for c in cards:
        dist[len(c[2])] = dist.get(len(c[2]), 0) + 1
    print(f"whisper 段落: {len(segs)}  →  字幕卡: {len(cards)}")
    print(f"長度分布(字數:卡數): {dict(sorted(dist.items()))}")
    fw = f"，疊字填充詞清理 {guard_stats['fillers']} 段" if use_filler else ""
    print(f"whisper-guard：移除字元迴圈 {guard_stats['loops']} 處{fw}")
    print(f"待複查的卡: {len(risky)} 張 → {sorted(risky)}")
    print(f"輸出: {out}")
    print(f"複查: {review}")
    return 0
