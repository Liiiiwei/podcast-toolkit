"""jieba 詞界評分斷句引擎（自 Breeze-ASR-25 srt_segment.py 移植）。

核心：balanced_split 在「上限內」挑「邊界分數高 + 接近理想長度」的切點，
評分涵蓋：句末語氣詞加分、連接詞前切加分、詞尾/虛字開頭重罰、副詞/介詞掛尾重罰、
掛尾連接詞重罰、英數字內部不切、以及最重要的——非 jieba 詞界重罰（修「然/後」
「耳/機」這類跨卡切詞）。

jieba 為 optional dependency：沒裝時詞界懲罰項跳過（降級為純規則評分），
模組其餘照常可用。jieba lazy 初始化（第一次用才 load 詞典）。
"""
from __future__ import annotations

import math
from pathlib import Path

try:
    import jieba as _jieba_mod
except ImportError:                     # jieba 未安裝 → 降級純規則
    _jieba_mod = None

PUNCT = "，。、！？；：,!?;:"
# 適合「切在其後」的字（真句末語氣詞/標點）。
# 注意：「的」已移除——定語的「的」（太吵的|時候）切開會拆散修飾語與中心語；
# 真句末的「…是很有興趣的」不靠加分也切得到（其後常伴隨停頓/句界）。
PARTICLE_END = set("了嗎呢吧啊喔噢啦嘛呀耶欸哦囉嘍" + PUNCT)
# 適合「切在其前」的連接詞/轉折（2 字）
CONJ2 = {"然後", "可是", "但是", "所以", "因為", "就是", "而且", "不過",
         "另外", "其實", "譬如", "比如", "當然", "可能", "如果", "那個", "這個", "後來"}
# 不可當「行首」的字（多為詞尾/虛字，放句首很怪）
NO_START = set("麼們嗎呢吧啊喔噢啦嘛呀耶欸哦囉著地得過個子兒的了")
# 不可當「行尾」的字（多為副詞/連接/介詞，後面一定還有字）
# 「於之其」修「來自於|OB車上」類切法；「才再」是掛尾單字（就又也跟或 原本就在）
NO_END = set("很太也都就又更最還不把被跟越比讓沒要想會能可和與在從對給每該並或而且但因所雖於之其才再")
# 掛尾連接詞（卡尾以這些 2 字詞收尾＝語意懸空，重罰；切在其「前」仍由 CONJ2 加分）
DANGLE_TAIL2 = {"然後", "所以", "因為", "但是", "可是", "而且", "或是", "還是",
                "而是", "就是", "不過", "其實", "譬如", "比如", "如果", "甚至", "後來"}
DANGLE_PENALTY = 4.0

# 預設繁體大詞典（Breeze 專案內）；resegment.jieba_dict 可覆寫，不存在則用 jieba 內建
_DEFAULT_DICT = Path("/Users/Mac365/Developer/breeze subtitle/Breeze-ASR-25/dict.txt.big")

# 節目固定專有名詞（避免被斷開）；各集來賓/品牌再由 add_words() 動態加入
_BASE_WORDS = ["印花樂", "過嗨乳牛", "郝慧川", "岳啟儒", "沈奕妤", "惡魔老闆",
               "我愛上班", "育成中心", "孵化器", "客製化", "大稻埕", "台灣八哥"]

# 繁體詞典缺口補丁：dict.txt.big（繁體大詞典）竟缺這些常用詞（FREQ=None），
# 缺了 jieba 會亂拆成假詞（好啟｜發人｜心），連帶讓「非詞界重罰」失準、seg_check 誤報、
# heal_straddle 可能被假詞界誤導。這些都是無歧義常用詞，補進去讓它們恆為單一 token。
# 全數經實測確認為 dict.txt.big 缺漏（見 word_break 測試）。
# 「球評」＝球賽評論員（實例：魁哥集 422/423 被切成 球｜評）、「一開始」（實例 878/879 被切
# 成 一｜開始）、「國中」（dict.txt.big 內 FREQ=3 過低，句中被 國＋中 蓋過→實例 932/933 切成
# 國｜中）；缺詞或詞頻過低時 jieba 在句中會把多字詞拆開、甚至黏出垃圾 token 使 context 詞界看
# 似乾淨、heal 漏修——補進（add_word 會拉高詞頻）後恆整詞、balanced_split 不切其中、heal 也搬得回。
_TRAD_FIX = ["認為", "啟發", "啟蒙", "啟示", "開啟", "啟動", "啟用", "球評", "一開始", "國中"]

# 成語/固定搭配整體保留：登記為單一 token → balanced_split 不會切在成語中間，
# heal_straddle 也會把已被切開的（如「啟發｜人心」跨卡）自動搬回同一卡。
# 只收「有實例、無歧義」的四字成語，不灌整本成語辭典（避免過度合併）。
_IDIOM_KEEP = ["啟發人心"]

_JIEBA = None            # None=未初始化、False=不可用、module=可用
_DICT_OVERRIDE = None    # config 指定的詞典路徑（str）；None=自動偵測
_EXTRA_WORDS: list[str] = []   # add_words 累積的自訂詞（重新初始化時重灌）


def is_cjk(ch: str) -> bool:
    """是否為中日韓表意文字（含相容區）。"""
    return "㐀" <= ch <= "鿿" or "豈" <= ch <= "﫿"


def configure(dict_path: str | None = None) -> None:
    """設定 jieba 詞典路徑（來自 cfg["resegment"]["jieba_dict"]）；None=自動偵測。
    路徑有變 → 重置，下次使用時重新初始化。"""
    global _DICT_OVERRIDE, _JIEBA
    p = str(dict_path) if dict_path else None
    if p != _DICT_OVERRIDE:
        _DICT_OVERRIDE = p
        _JIEBA = None


def _jieba():
    """lazy 初始化 jieba；沒裝/初始化失敗 → False（純規則降級）。"""
    global _JIEBA
    if _JIEBA is None:
        if _jieba_mod is None:
            _JIEBA = False
        else:
            try:
                _jieba_mod.setLogLevel(20)
                dic = Path(_DICT_OVERRIDE) if _DICT_OVERRIDE else _DEFAULT_DICT
                if dic.exists():
                    _jieba_mod.set_dictionary(str(dic))   # 繁體詞典
                for w in _BASE_WORDS + _TRAD_FIX + _IDIOM_KEEP + _EXTRA_WORDS:
                    _jieba_mod.add_word(w)
                _JIEBA = _jieba_mod
            except Exception:
                _JIEBA = False
    return _JIEBA


def available() -> bool:
    """jieba 是否可用（供 check-seg 判斷要不要跳過跨卡切詞檢測）。"""
    return bool(_jieba())


def add_words(words) -> None:
    """加入自訂詞（來賓姓名、品牌、術語），避免被斷開。jieba 缺席時累積備用。"""
    for w in words or []:
        w = (w or "").strip()
        if len(w) >= 2 and w not in _EXTRA_WORDS:
            _EXTRA_WORDS.append(w)
            j = _jieba()
            if j:
                j.add_word(w)


def word_break_ok(text: str):
    """回傳「可斷點」的字元索引集合（＝各詞的邊界）；沒 jieba 則回傳 None。"""
    j = _jieba()
    if not j:
        return None
    pts, n = {0}, 0
    for tok in j.cut(text):
        n += len(tok)
        pts.add(n)
    return pts


def char_width(c: str) -> float:
    """顯示寬度：空白 0、半形 0.5、全形 1。"""
    if c.isspace():
        return 0.0
    return 0.5 if ord(c) < 128 else 1.0


def text_width(s: str) -> float:
    return sum(char_width(c) for c in s)


def balanced_split(chars: list, max_w: float = 16.0, min_w: float = 5.0,
                   width=char_width) -> list[tuple[int, int]]:
    """chars: [(ch, start, end), ...]。回傳片段 [(a, b), ...]（半開區間 index）。

    先算要切幾段與理想長度（平衡），再於「上限內」挑最接近理想又邊界好的切點。
    width：字元寬度函式；上游預設 char_width（ascii 0.5/空白 0），
    toolkit 的 _subsplit 傳「每字元算 1」對齊既有 max_w 原始字數尺。
    """
    n = len(chars)
    text = [c[0] for c in chars]
    cum = [0.0] * (n + 1)
    for i, c in enumerate(text):
        cum[i + 1] = cum[i] + width(c)
    total = cum[n]
    if total <= max_w or n <= 1:
        return [(0, n)]

    wb = word_break_ok("".join(text))   # 可斷點（詞邊界）；None=沒 jieba

    def boundary(idx):
        """切在 idx-1 與 idx 之間的好壞分數（越高越適合斷在這）。"""
        before, at = text[idx - 1], text[idx]
        b = 0.0
        if before in PARTICLE_END:
            b += 3.0
        if "".join(text[idx:idx + 2]) in CONJ2:
            b += 2.2
        if at in NO_START:
            b -= 4.0
        if before in NO_END:
            b -= 3.5
        if "".join(text[max(0, idx - 2):idx]) in DANGLE_TAIL2:
            b -= DANGLE_PENALTY          # 卡尾掛「然後/所以/因為…」→ 重罰
        if before.isascii() and before.strip() and at.isascii() and at.strip():
            b -= 5.0                     # 英/數字串內部不切
        return b

    k = max(2, math.ceil(total / max_w))
    target = total / k
    pieces: list[tuple[int, int]] = []
    start = 0
    while cum[n] - cum[start] > max_w:
        hi = start
        while hi + 1 <= n and cum[hi + 1] - cum[start] <= max_w:
            hi += 1
        lo = start + 1
        while lo < hi and cum[lo] - cum[start] < min_w:
            lo += 1
        best, best_score = hi, -1e18
        for idx in range(lo, hi + 1):
            w = cum[idx] - cum[start]
            score = boundary(idx) - abs(w - target) * 0.5   # 接近理想長度 + 好邊界
            if wb is not None and idx not in wb:            # 切在詞中間 → 重罰
                score -= 8.0
            rem = total - cum[idx]
            if 0 < rem < min_w:                             # 別讓後面剩碎片
                score -= 6.0
            if score > best_score:
                best_score, best = score, idx
        pieces.append((start, best))
        start = best
    pieces.append((start, n))

    # 結尾若太短，與前一段合併後從中間再平均切一次（仍保證 ≤ max_w）
    if len(pieces) >= 2:
        a, b = pieces[-1]
        if cum[b] - cum[a] < min_w:
            s, e = pieces[-2][0], b
            if cum[e] - cum[s] <= max_w:
                pieces[-2:] = [(s, e)]
            else:
                mid = (cum[s] + cum[e]) / 2
                valid = [i for i in range(s + 1, e)
                         if cum[i] - cum[s] <= max_w and cum[e] - cum[i] <= max_w]
                bi = max(valid, key=lambda i: -abs(cum[i] - mid) + boundary(i) * 0.6
                         + (0 if (wb is None or i in wb) else -8.0))
                pieces[-2:] = [(s, bi), (bi, e)]
    return pieces
