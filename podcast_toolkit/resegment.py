"""podcast resegment：字幕重新斷句 + 錯字修正 + whisper-guard 反幻覺。

從現有 resegment.py 改造，邏輯不變，只是參數從 config 帶入。
"""
import re
import shutil
import sys
from pathlib import Path
from typing import Optional
from podcast_toolkit import cameras_io, srt_io
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


def remap_speakers_by_time(
    old_cards: list[dict], old_spk: dict[int, str], new_cards: list
) -> dict[int, str]:
    """舊卡的講者標依「時間重疊最多」重新貼到新卡上，回傳新的 idx→講者。

    resegment 把字幕整個重切、idx 從 1 重編，舊 sidecar 的 idx 直接留著就會錯位——
    講者標貼到不相干的句子，燒進成品字幕就是講錯人。兩邊唯一的共同基準是時間軸。

    old_cards: srt_io.parse 的結果；new_cards: run() 的 [start, end, txt, ...]（1-based 依序編號）。
    """
    spans = sorted(
        (float(c["start"]), float(c["end"]), old_spk[int(c["idx"])])
        for c in old_cards
        if int(c["idx"]) in old_spk
    )
    new_spk: dict[int, str] = {}
    j = 0  # 兩邊都照時間排序 → 用滑動指標掃過去，不必兩層迴圈（六千卡的集會慢到有感）
    for n, card in enumerate(new_cards, 1):
        st, en = float(card[0]), float(card[1])
        while j < len(spans) and spans[j][1] <= st:
            j += 1
        best, best_ov, k = None, 0.0, j
        while k < len(spans) and spans[k][0] < en:
            ov = min(en, spans[k][1]) - max(st, spans[k][0])
            if ov > best_ov:
                best, best_ov = spans[k][2], ov
            k += 1
        if best is not None:
            new_spk[n] = best
    return new_spk


def run(episode_dir: Path, force: bool = False, src: Optional[Path] = None) -> int:
    """重新斷句。

    src 給定 → 拿那份字幕當來源；省略 → 沿用 episode.yaml 的主字幕檔（main_srt）。
    不論來源是哪份，輸出一律寫回 _v2.srt：編輯器只認這份，寫到別的位置等於產出一個
    介面上看不到的孤兒檔。
    """
    ep = Episode(episode_dir)
    cfg = ep.cfg
    rcfg = cfg["resegment"]

    if src is not None:
        # 指定的來源不存在就直接停。靜默退回主字幕的話，使用者會以為重切的是自己挑的
        # 那份，實際切的是另一份 —— 而且 _v2 當場被覆寫，錯得無聲無息又救不回來。
        src = Path(src)
        if not src.is_file():
            print(f"✗ 找不到指定的來源字幕：{src}", file=sys.stderr)
            return 3
    else:
        src = ep.main_srt()
        if not src.exists():
            print(f"✗ 找不到字幕：{src}", file=sys.stderr)
            srt_list = list(ep.subdir("output").glob("*.srt"))
            if srt_list:
                print("  03_成品/ 內現有 srt：", file=sys.stderr)
                for p in srt_list:
                    print(f"    {p.name}", file=sys.stderr)
            return 3

    out = ep.output_v2_srt()
    review = ep.review_file()

    if out.exists() and not force:
        print(f"✗ 輸出已存在：{out}", file=sys.stderr)
        print("  加 --force 覆寫", file=sys.stderr)
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

    # 講者 sidecar 要在覆寫 _v2 之前先讀舊的（新舊卡靠時間軸對應，見 remap_speakers_by_time）。
    # 舊卡片固定取自 _v2（即使有 src override 也一樣）：講者標的 idx 是對著 _v2 編的，
    # 拿 src 當基準會對錯人。
    spk_path = ep.output_v2_speakers_json()
    old_spk = cameras_io.load(spk_path)
    old_cards = srt_io.parse(out.read_text(encoding="utf-8")) if out.exists() else []

    # 寫 SRT
    with out.open("w", encoding="utf-8") as f:
        for n, (st, en, txt, _, _) in enumerate(cards, 1):
            f.write(f"{n}\n{srt_io.seconds_to_srt_ts(st)} --> {srt_io.seconds_to_srt_ts(en)}\n{txt}\n\n")

    # 重建講者 sidecar；兩份缺一就無從對應，寧可原封不動也不要亂貼或刪掉使用者的標記
    if old_spk and old_cards:
        new_spk = remap_speakers_by_time(old_cards, old_spk, cards)
        shutil.copy(spk_path, spk_path.with_name(spk_path.name + ".pre-resegment.bak"))
        cameras_io.save(spk_path, new_spk)
        print(f"講者標重建: {len(old_spk)} → {len(new_spk)} 張卡（舊檔備份 .pre-resegment.bak）")

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
            f.write(f"[{n:>3}] {srt_io.seconds_to_srt_ts(st)[:-4]}-{srt_io.seconds_to_srt_ts(en)[:-4]} segs{fi}-{li} ({len(txt):>2}) {txt}{flag}\n")

    dist = {}
    for c in cards:
        dist[len(c[2])] = dist.get(len(c[2]), 0) + 1
    print(f"whisper 段落: {len(segs)}  →  字幕卡: {len(cards)}")
    print(f"長度分布(字數:卡數): {dict(sorted(dist.items()))}")
    fw = f"，疊字填充詞清理 {guard_stats['fillers']} 段" if use_filler else ""
    print(f"whisper-guard：移除字元迴圈 {guard_stats['loops']} 處{fw}")
    print(f"待複查的卡: {len(risky)} 張 → {sorted(risky)}")
    print(f"來源: {src}")  # 有 --src 時要能一眼確認切的是哪份，別事後靠猜
    print(f"輸出: {out}")
    print(f"複查: {review}")
    return 0
