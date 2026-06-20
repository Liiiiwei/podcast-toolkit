"""轉錄稿『模糊/不確定字』偵測 → 產『待確認詞』清單給人工 curate 進該集 glossary。

設計立場（依實測）：ASR 約一半的聽錯是『模型很有信心地錯』（逐字機率高），純信心法救不了，
也無法自動判斷正解。所以這支**只建議、不自動改稿**：把候選攤開、排序、附證據，由人決定。

訊號（依價值排序，皆不強制依賴 jieba；jieba/詞典找得到就用來提升精度）：
- R7 fed_but_missing：episode glossary 餵過的 canonical，全稿(連 sounds_like)一次都沒出現
  → 多半被聽成別的字或漏抓（茄芷袋/Wazaiii/酷學營 屬此型）。最該人聽，信心=高。
- new_variant：既有 glossary 條目附近冒出『編輯距離 1』的新誤聽變體（妮可基滿←妮可基嫚）→ 建議補 sounds_like。
- R5 recurring_oov：高頻、不在任何詞庫(或不在中文詞典)的 2–4 字 CJK 串（炫音）→ 疑似沒收錄專名/反覆聽錯。
- R1 low_prob：逐字機率低（需 conf sidecar；沒有就跳過）。

過濾：填充詞停用集、已收錄詞 crosscheck、同名多寫法聚類。輸出三檔信心 + 明確建議動作。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

CJK = "一-鿿"
_CJK_RUN = re.compile(f"[{CJK}]+")

# 中文填充/口頭禪（單獨成串就丟，不報、不累加次數）
FILLERS = set("嗯啊呃喔哦欸唉哈呵嘛啦吼蛤呀耶哎噢咧捏內ㄟ")
FILLER_WORDS = {
    "嗯", "啊", "呃", "喔", "哦", "欸", "唉", "哈哈", "呵呵", "那個", "這個",
    "然後", "就是", "對對", "對對對", "對啊", "嘛", "啦", "吼", "蛤", "之類",
    "那種", "這樣", "這樣子", "什麼", "怎麼", "我們", "你們", "他們", "可是",
    "因為", "所以", "但是", "覺得", "真的", "其實", "後來", "現在", "已經",
    "一個", "一直", "一下", "知道", "可能", "應該", "比較", "還有", "或者",
    "如果", "時候", "的時候", "我覺得", "我們的", "你知道", "就會", "就是說",
}
# n-gram 邊界若是這些『功能字』，多半是把正常句子切出來的雜訊，降權/濾掉
EDGE_STOP = set("的了是我你他她它們這那就都也很還不沒要會能可和與在從對給每並或而且但因所雖之有個把被讓跟越比")


def fmt_time(sec: float) -> str:
    sec = max(0.0, float(sec or 0))
    m, s = int(sec // 60), int(sec % 60)
    return f"{m:02d}:{s:02d}"


def _norm(s: str) -> str:
    """正規化比對用：去空白、ASCII 轉小寫。"""
    return re.sub(r"\s+", "", (s or "")).lower()


def _full_text(cards: list) -> str:
    return "".join((c.get("text") or "") for c in cards)


def _glossary_forms(glossary: list) -> set:
    """glossary 所有已知寫法（canonical + sounds_like），正規化。"""
    forms = set()
    for g in glossary or []:
        forms.add(_norm(g.get("canonical", "")))
        for s in g.get("sounds_like") or []:
            forms.add(_norm(s))
    forms.discard("")
    return forms


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _find_dict_words() -> set:
    """盡量找 Breeze 的 dict.txt.big（繁中詞典），把『詞』載成 set 當 OOV 判斷依據。
    找不到 → 回空 set（R5 退回 stoplist 啟發，精度略降）。可用環境變數 BREEZE_DICT 指定。"""
    cands = []
    env = os.environ.get("BREEZE_DICT")
    if env:
        cands.append(Path(env))
    home = Path.home()
    cands += [
        home / "Developer" / "breeze subtitle" / "Breeze-ASR-25" / "dict.txt.big",
        home / "Developer" / "Breeze-ASR-25" / "dict.txt.big",
    ]
    for p in cands:
        try:
            if p and p.exists():
                words = set()
                with open(p, encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        w = line.split(" ", 1)[0].strip()
                        if w:
                            words.add(w)
                return words
        except (OSError, UnicodeDecodeError):   # 壞檔/二進位/權限 → 安全 fallback
            continue
    return set()


def _occurrences(cards: list, needle_norm: str) -> list:
    """回傳含該（正規化）字串的卡 [(idx, start, text)]。"""
    hits = []
    for c in cards:
        if needle_norm and needle_norm in _norm(c.get("text", "")):
            hits.append((c.get("idx"), c.get("start", 0.0), c.get("text", "")))
    return hits


# ---------------- 規則 ----------------

def detect_fed_but_missing(cards: list, glossary: list) -> list:
    """R7：glossary 餵過、全稿(連 sounds_like)0 次出現的 canonical。"""
    text = _norm(_full_text(cards))
    out = []
    for g in glossary or []:
        canon = g.get("canonical", "")
        if len(_norm(canon)) < 2:
            continue
        forms = [canon] + list(g.get("sounds_like") or [])
        if any(_norm(f) and _norm(f) in text for f in forms):
            continue  # 有出現任一形態 → 不算漏
        out.append({
            "word": canon, "kind": "fed_but_missing", "count": 0,
            "confidence": "high", "samples": [], "canonical_hint": canon,
            "variants": [], "note": g.get("note", ""),
            "suggestion": "人工聽：確認本集是否提到；找出被聽成什麼 → 補進 sounds_like",
        })
    return out


def detect_new_variant(cards: list, glossary: list, *, dict_words: set = None,
                       min_count: int = 2) -> list:
    """既有 glossary 條目的『編輯距離 1』新誤聽變體（同長度），建議補 sounds_like。
    高精度條件（擋 印花樂→印花的/印花布 這種『專名+常用字』假陽）：
      - 差異字必須是『內容字』（不是助詞/邊界功能字）→ 擋掉 印花的/印花這/印花它
      - 變體本身不是詞典詞 → 擋掉 印花布(=印花布料，真詞)
      - 至少出現 min_count 次 → 擋掉只出現一次的隨機『印花X』
    這樣只留下『反覆、像誤聽』的真變體（櫻花樂、妮可基滿）。"""
    dict_words = dict_words or set()
    runs = _CJK_RUN.findall(_full_text(cards))
    grams = set()
    for r in runs:
        for n in (3, 4, 5):
            for i in range(len(r) - n + 1):
                grams.add(r[i:i + n])
    forms = _glossary_forms(glossary)
    out = []
    seen = set()
    for g in glossary or []:
        canon = g.get("canonical", "")
        nc = _norm(canon)
        if len(nc) < 3 or any(ord(ch) < 128 for ch in nc):  # 只對中文 ≥3 字專名做
            continue
        for gram in grams:
            ng = _norm(gram)
            if ng in forms or ng in seen or len(ng) != len(nc) or ng in dict_words:
                continue
            if _levenshtein(ng, nc) != 1:
                continue
            diff = [i for i in range(len(nc)) if nc[i] != ng[i]]
            if len(diff) != 1 or ng[diff[0]] in EDGE_STOP or ng[diff[0]] in FILLERS:
                continue   # 差在助詞/功能字 → 是『專名+常用字』，不是誤聽
            hits = _occurrences(cards, ng)
            if len(hits) < min_count:
                continue
            seen.add(ng)
            out.append({
                "word": gram, "kind": "new_variant", "count": len(hits),
                "confidence": "mid", "canonical_hint": canon,
                "samples": [{"idx": h[0], "time": fmt_time(h[1]), "text": h[2]} for h in hits[:3]],
                "variants": [], "note": "",
                "suggestion": f"補進「{canon}」的 sounds_like（多字唯一可安全硬替換）",
            })
    return out


def detect_recurring_oov(cards: list, glossary: list, *, dict_words: set,
                         min_count: int = 2, top: int = 15) -> list:
    """R5：高頻、不在詞庫/詞典的 2–4 字 CJK 串。dict_words 有 → 用 OOV；無 → 退回 stoplist 啟發。"""
    known = _glossary_forms(glossary)
    # 累計 n-gram 出現（以卡為單位記證據）
    counts: dict[str, int] = {}
    samples: dict[str, list] = {}
    for c in cards:
        for run in _CJK_RUN.findall(c.get("text", "")):
            seen_in_card = set()
            for n in (2, 3, 4):
                for i in range(len(run) - n + 1):
                    g = run[i:i + n]
                    if g in seen_in_card:
                        continue
                    seen_in_card.add(g)
                    counts[g] = counts.get(g, 0) + 1
                    sl = samples.setdefault(g, [])
                    if len(sl) < 3:   # 只留前 3 筆證據，長稿不放大記憶體
                        sl.append({"idx": c.get("idx"), "time": fmt_time(c.get("start", 0)),
                                   "text": c.get("text", "")})

    def is_candidate(g: str) -> bool:
        ng = _norm(g)
        if ng in known or g in FILLER_WORDS:
            return False
        if all(ch in FILLERS for ch in g):
            return False
        if g[0] in EDGE_STOP or g[-1] in EDGE_STOP:   # 邊界是功能字 → 多半是切出來的雜訊
            return False
        if dict_words:
            return g not in dict_words                # 有詞典：不是詞 = 可疑
        return True                                   # 無詞典：靠 stoplist + 邊界濾 + 頻次

    cands = [(g, n) for g, n in counts.items() if n >= min_count and is_candidate(g)]
    # 去子串：若 g 被某個更高/等頻的更長候選包住，留長的
    cands.sort(key=lambda x: (-len(x[0]), -x[1]))
    kept = []
    for g, n in cands:
        if any((g in lg and n <= ln) for lg, ln in kept):
            continue
        kept.append((g, n))
    kept.sort(key=lambda x: -x[1])
    out = []
    for g, n in kept[:top]:
        out.append({
            "word": g, "kind": "recurring_oov", "count": n,
            "confidence": "mid" if n >= 4 else "low",
            "canonical_hint": "", "variants": [],
            "samples": samples[g][:3], "note": "",
            "suggestion": "若是專名 → 加詞庫；常用音誤聽 → 交給 proofread（勿硬替換）",
        })
    return out


def detect_low_prob(cards: list, conf: dict, *, thr: float = 0.5, top: int = 15) -> list:
    """R1：逐字機率低的字/串（需 conf={idx: [{w,p}]} sidecar；沒有就回空）。"""
    if not conf:
        return []
    out = []

    def flush(run, c):
        frag = "".join(run).strip()
        if len(_norm(frag)) >= 2:
            out.append({"word": frag, "kind": "low_prob", "count": 1,
                        "confidence": "low", "canonical_hint": "", "variants": [],
                        "samples": [{"idx": c.get("idx"), "time": fmt_time(c.get("start", 0)),
                                     "text": c.get("text", "")}], "note": "",
                        "suggestion": "聲學模糊：人工聽一下這個時間點"})

    for c in cards:
        words = conf.get(str(c.get("idx"))) or conf.get(c.get("idx"))
        if not words:
            continue
        run = []
        for w in words:
            p = w.get("p")
            tok = (w.get("w") or "").strip()
            if p is not None and p < thr and re.search(f"[{CJK}A-Za-z]", tok):
                run.append(tok)
            elif run:
                flush(run, c)
                run = []
        if run:                      # 句尾/段尾的低機率串（最該人聽）也要收
            flush(run, c)
    return out[:top]


# ---------------- 聚類 + 編排 ----------------

def _cluster(cands: list) -> list:
    """同名多寫法（編輯距離≤1 或共享多數字）聚成一條，variants 收齊。"""
    out = []
    for c in cands:
        placed = False
        for o in out:
            if o["kind"] != c["kind"]:
                continue
            a, b = _norm(o["word"]), _norm(c["word"])
            if a and b and abs(len(a) - len(b)) <= 1 and _levenshtein(a, b) <= 1:
                if c["word"] not in o["variants"] and c["word"] != o["word"]:
                    o["variants"].append(c["word"])
                o["count"] += c["count"]
                placed = True
                break
        if not placed:
            out.append(dict(c))
    return out


KIND_RANK = {"fed_but_missing": 0, "new_variant": 1, "recurring_oov": 2, "low_prob": 3}


def suggest_candidates(cards: list, glossary: list, *, conf: dict = None,
                       dict_words: set = None, min_count: int = 2,
                       include_recurring: bool = False) -> list:
    """主入口：回傳排序後的候選清單。

    預設只跑高精度信號（R7 餵過卻消失 + 既有詞新變體 + R1 低機率）。
    include_recurring=True 才跑 R5 重複 OOV——它無 jieba 斷詞時會把正常語句的
    n-gram 碎片（件事/為其實/商業模）當 OOV 報出來洗版，故預設關閉，留待接上 jieba 再開。"""
    if dict_words is None:
        dict_words = _find_dict_words()
    cands = []
    cands += detect_fed_but_missing(cards, glossary)
    cands += detect_new_variant(cards, glossary, dict_words=dict_words, min_count=min_count)
    if include_recurring:
        cands += detect_recurring_oov(cards, glossary, dict_words=dict_words, min_count=min_count)
    cands += detect_low_prob(cards, conf or {})
    cands = _cluster(cands)
    conf_rank = {"high": 0, "mid": 1, "low": 2}
    cands.sort(key=lambda c: (KIND_RANK.get(c["kind"], 9), conf_rank.get(c["confidence"], 9), -c["count"]))
    return cands


# ---------------- 輸出 ----------------

_CONF_ICON = {"high": "🔴 高", "mid": "🟡 中", "low": "🟢 低"}
_KIND_LABEL = {"fed_but_missing": "餵過卻消失", "new_variant": "既有詞新變體",
               "recurring_oov": "重複怪詞", "low_prob": "聲學模糊"}


def to_markdown(cands: list, name: str, *, has_conf: bool) -> str:
    lines = [f"## 本集模糊字候選 — {name}（共 {len(cands)} 條；已濾填充詞 / 已收錄詞）", ""]
    if not has_conf:
        lines.append("> ⚠ 本集無逐字機率欄（低信心規則未啟用）；以『餵過卻消失』+『既有詞新變體』為主。")
        lines.append("")
    if not cands:
        lines.append("（沒有偵測到需要人工確認的候選。）")
        return "\n".join(lines) + "\n"
    for i, c in enumerate(cands, 1):
        head = f"{i}. 「{c['word']}」"
        if c["count"]:
            head += f" ×{c['count']}"
        head += f"  〔{_KIND_LABEL.get(c['kind'], c['kind'])}〕  信心:{_CONF_ICON.get(c['confidence'], c['confidence'])}"
        lines.append(head)
        if c.get("canonical_hint") and c["canonical_hint"] != c["word"]:
            lines.append(f"   ↳ 對應詞庫：{c['canonical_hint']}")
        if c.get("variants"):
            lines.append(f"   ↳ 其他寫法：{ '、'.join(c['variants']) }")
        for s in c.get("samples", [])[:3]:
            lines.append(f"   ⏱ {s['time']}  💬 {s['text']}")
        if c.get("note"):
            lines.append(f"   📝 {c['note']}")
        lines.append(f"   👉 {c['suggestion']}")
        lines.append("")
    return "\n".join(lines) + "\n"


def to_yaml_block(cands: list) -> str:
    """可貼回 episode.yaml 的片段（只含高/中信心）。schema 對齊 config.normalize_glossary。"""
    pick = [c for c in cands if c["confidence"] in ("high", "mid")]
    lines = ["# === 自動偵測候選，請人工 review 後併入上方 glossary ===",
             "# 確認後刪掉 _ 開頭欄位，整段貼進 episode.yaml 的 glossary: 區即生效",
             "glossary_candidates:"]
    if not pick:
        lines.append("  []  # 無高/中信心候選")
        return "\n".join(lines) + "\n"
    # 用 json.dumps 產 scalar（YAML 接受 JSON-style 雙引號字串與 flow list），
    # 這樣 canonical/note/變體詞含雙引號或反斜線也產出合法 YAML，貼回 episode.yaml 不會壞檔。
    for c in pick:
        canon = c.get("canonical_hint") or c["word"]
        sl = [c["word"]] if c["kind"] == "new_variant" else []
        note = c.get("note") or f'{_KIND_LABEL.get(c["kind"], "")}；本集×{c["count"]}'
        lines.append(f'  - canonical: {json.dumps(canon, ensure_ascii=False)}')
        lines.append(f'    sounds_like: {json.dumps(sl, ensure_ascii=False)}')
        lines.append(f'    note: {json.dumps(note, ensure_ascii=False)}')
        lines.append(f'    _evidence: {{kind: {c["kind"]}, count: {c["count"]}, confidence: {c["confidence"]}}}')
    return "\n".join(lines) + "\n"


def to_json(cands: list) -> str:
    return json.dumps({"candidates": cands}, ensure_ascii=False, indent=2)


# ---------------- CLI 編排（產出 sidecar + 人工 curate 寫回 .glossary.json） ----------------

MD_NAME = "_glossary_candidates.md"
YAML_NAME = "_glossary_candidates.yaml"
JSON_NAME = "_glossary_candidates.json"
IGNORE_NAME = "_glossary_candidates.ignore.json"


def _load_ignore(ep_dir: Path) -> set:
    p = ep_dir / IGNORE_NAME
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_ignore(ep_dir: Path, words: set) -> None:
    (ep_dir / IGNORE_NAME).write_text(
        json.dumps(sorted(words), ensure_ascii=False, indent=2), encoding="utf-8")


def add_to_episode_glossary(ep_dir: Path, canonical: str, *, sounds_like=None, note: str = "") -> None:
    """把一條詞併進 <ep>/.glossary.json（web 與 CLI 共用的集詞庫；P0-2 後 proofread 也讀它）。
    canonical 為主鍵，sounds_like 取聯集。不碰 episode.yaml（保留註解）。"""
    from podcast_toolkit import config
    p = ep_dir / config.EPISODE_GLOSSARY_FILENAME
    raw = []
    if p.exists():
        broken = False
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            broken = True
        if broken or not isinstance(raw, list):
            # 壞檔/形狀不對：先備份再覆寫，避免裡面可能有的人工 curate 詞無聲消失
            bak = p.with_name(p.name + ".corrupt.bak")
            try:
                p.replace(bak)
                print(f"⚠ {p.name} 解析失敗，已備份成 {bak.name} 再重建")
            except OSError:
                pass
            raw = []
    raw.append({"canonical": canonical, "sounds_like": list(sounds_like or []), "note": note})
    merged = config.dedup_glossary(config.normalize_glossary(raw))
    p.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")


def generate(episode_dir, *, srt=None, quiet=False) -> list:
    """偵測 → 寫 _glossary_candidates.{md,yaml,json} 到集資料夾。回候選 list。"""
    from podcast_toolkit import srt_io
    from podcast_toolkit.episode import Episode
    ep = Episode(Path(episode_dir))
    src = Path(srt) if srt else ep.output_v2_srt()
    if not src.exists():
        if not quiet:
            print(f"（找不到字幕 {src.name}，略過模糊字偵測）")
        return []
    cards = srt_io.parse(src.read_text(encoding="utf-8"))
    dict_words = _find_dict_words()
    cands = suggest_candidates(cards, ep.cfg.get("glossary") or [], dict_words=dict_words)
    ignore = _load_ignore(ep.dir)
    cands = [c for c in cands if c["word"] not in ignore]

    ep.dir.joinpath(MD_NAME).write_text(to_markdown(cands, ep.name, has_conf=False), encoding="utf-8")
    ep.dir.joinpath(YAML_NAME).write_text(to_yaml_block(cands), encoding="utf-8")
    ep.dir.joinpath(JSON_NAME).write_text(to_json(cands), encoding="utf-8")
    if not quiet:
        if cands:
            highs = sum(1 for c in cands if c["confidence"] == "high")
            print(f"🔎 模糊字候選：{len(cands)} 條（高信心 {highs}）→ {MD_NAME}")
            print(f"   逐條勾選加入詞庫：podcast glossary-review \"{ep.dir}\"")
        else:
            print("🔎 模糊字偵測：沒有需要人工確認的候選。")
    return cands


def review(episode_dir) -> int:
    """互動式逐條 curate：寫回 .glossary.json（不碰 episode.yaml）。"""
    import sys
    ep_dir = Path(episode_dir).resolve()
    jpath = ep_dir / JSON_NAME
    if not jpath.exists():
        print(f"✗ 找不到 {JSON_NAME}，請先跑 podcast ingest-breeze 或 glossary-suggest。", file=sys.stderr)
        return 3
    cands = json.loads(jpath.read_text(encoding="utf-8")).get("candidates", [])
    if not cands:
        print("沒有候選詞。")
        return 0
    if not sys.stdin.isatty():
        print(f"（非互動環境，{len(cands)} 條候選請見 {MD_NAME} / {YAML_NAME}）")
        return 0

    ignore = _load_ignore(ep_dir)
    added = 0
    print(f"逐條 curate（{len(cands)} 條）。指令：a=採用建議  s 文字=設sounds_like  "
          "c 文字=改canonical  i=以後別再問  Enter=跳過  q=結束\n")
    for n, c in enumerate(cands, 1):
        canon = c.get("canonical_hint") or c["word"]
        print(f"[{n}/{len(cands)}] {_CONF_ICON.get(c['confidence'],'')}  「{c['word']}」"
              f"〔{_KIND_LABEL.get(c['kind'], c['kind'])}〕")
        for s in c.get("samples", [])[:2]:
            print(f"      ⏱ {s['time']}  💬 {s['text']}")
        print(f"      👉 {c['suggestion']}")
        try:
            ans = input("   > ").strip()
        except EOFError:
            break
        if ans == "q":
            break
        if ans in ("", "skip"):
            continue
        if ans == "i":
            ignore.add(c["word"])
            continue
        if ans == "a":
            if c["kind"] == "new_variant":
                add_to_episode_glossary(ep_dir, canon, sounds_like=[c["word"]], note=c.get("note", ""))
                print(f"      ✓ 「{c['word']}」加進「{canon}」的 sounds_like")
                added += 1
            else:
                print("      （此類 canonical 已在詞庫；用 s 文字 加 sounds_like 或 Enter 跳過）")
            continue
        if ans.startswith("s "):
            sl = ans[2:].strip()
            if sl and all(ord(ch) >= 128 for ch in sl) and len(sl) < 3:
                print("      ⚠ 短同音詞硬替換易誤傷；仍加入（canonical 為主鍵）")
            add_to_episode_glossary(ep_dir, canon, sounds_like=[sl], note=c.get("note", ""))
            print(f"      ✓ 「{sl}」加進「{canon}」的 sounds_like")
            added += 1
            continue
        if ans.startswith("c "):
            new_canon = ans[2:].strip()
            add_to_episode_glossary(ep_dir, new_canon, note=c.get("note", ""))
            print(f"      ✓ 「{new_canon}」加進本集詞庫")
            added += 1
            continue
        print("      （無法辨識，跳過）")
    _save_ignore(ep_dir, ignore)
    print(f"\n完成：加入 {added} 條到 .glossary.json（proofread/轉錄下次生效）；忽略清單 {len(ignore)} 條。")
    return 0
