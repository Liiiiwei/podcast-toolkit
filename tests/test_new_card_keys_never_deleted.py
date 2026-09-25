"""B3-3：`new:` 開頭的新增卡永遠不會進 `state.deletions`。

背景（附錄記錄更正）：`api.js` 存檔時會過濾掉 `new:` 開頭的鍵。上一梯把它記成
「已知落差：新增卡不進 cuts」，實際重查原始碼後判定**不是落差，是防禦性過濾** ——
那個 filter 永遠過濾不到東西，因為 `new:` key 在三個入口都被更前面的結構擋掉了。
既然它是防禦，正確處置就是「補測試釘住前提」而不是補功能：前提哪天被改破，
這支測試先紅，逼人回來重新判斷 `api.js` 的 filter 還是不是防禦。

為什麼用原始碼層的變更偵測而不是行為測試（誠實話）：
- 這條是**不變式**（某件事永遠不發生），行為測試只能證明「我試過的那幾條路沒發生」。
- 真正的保證來自三個結構性事實（見下面三支測試）。它們一被改動就紅，這才是這支測試的職責。
- 它不證明「`new:` key 若真的進了 deletions 會怎樣」—— 那是 `api.js` filter 的事，
  也正是那個 filter 存在的理由。

`state.deletions` 的每個寫入點都在 `ALLOWED_WRITES` 裡逐條記了「為什麼餵不進 `new:` key」。
新增或改寫任何一個寫入點 → `test_all_deletion_write_sites_are_reviewed` 紅，強制重審。
"""
from __future__ import annotations

import json
import re
from collections import Counter

from .conftest import editor_static_dir

# 寫入點清單：{檔名: {該行 strip 後的內容: (出現次數, 為什麼餵不進 new: key)}}
ALLOWED_WRITES = {
    "app.js": {
        "state.deletions = new Set(snap.deletions);": (
            1,
            "undo 還原快照；快照來自同樣滿足本不變式的狀態（:219 連 newCards 一起存、"
            ":243 一起還原），歸納上封閉",
        ),
        "state.deletions.add(newKey);": (
            2,
            "合併／切卡後重新編號：newKey 由真卡的 c.idx 組出（`${c.idx}:...`），"
            "來源是 state.cards 不是 newCards",
        ),
        "if (mergedDeleted) state.deletions.add(leftKey);": (
            1,
            "同上：leftKey 由真卡 idx 組出",
        ),
        "state.deletions.add(c.idx);": (
            1,
            "同上：c 來自 state.cards，idx 是整數不是 new: 字串",
        ),
        "state.deletions.add(`${c.idx}:0`);": (1, "同上：真卡 idx 組出的 sub-card key"),
        "state.deletions.add(`${c.idx}:1`);": (1, "同上"),
        "state.deletions.add(`${c.idx}:${partIdx + 1}`);": (1, "同上"),
        "state.deletions.add(key);": (
            1,
            "renderCards 的刪除鈕；同一迴圈在 `if (r.newCard)` 就 continue 掉了 → "
            "見 test_render_cards_skips_new_cards_before_delete_toggle",
        ),
        "state.deletions = new Set(data.deletions || []);": (
            1,
            "從磁碟載入；存檔端（api.js）永遠寫 deletions: []，且該處還有 new: filter "
            "把鍵擋在寫入之前 → 磁碟上不可能有 new: 鍵",
        ),
        "state.deletions = new Set(sel.keys);": (
            1,
            "由磁碟 cuts 反推卡選取；sel 來自 expandedCards()，而 state.newCards 在這行"
            "之前已被清空 → 見 test_new_cards_cleared_before_cut_derived_selection",
        ),
        "for (const idx of state.susChecked) state.deletions.add(idx);": (
            1,
            "susChecked 只在可疑紅卡的 checkbox 加入（app.js:1933 `state.susChecked.add(c.idx)`），"
            "c 來自 state.cards；新增卡走 renderNewCardRow，沒有那個 checkbox",
        ),
        "for (const c of reactionCards) state.deletions.add(c.idx);": (
            1,
            "reactionCards 由 state.cards.filter 取得，不含 newCards",
        ),
    },
    "timeline.js": {
        "for (const k of added) state.deletions.add(k);": (
            1,
            "框選；keys 在 filter 裡明文排除 new: → 見 test_marquee_filters_new_card_keys",
        ),
    },
}

WRITE_RE = re.compile(r"state\.deletions\.add\(|state\.deletions = new Set\(")


def _src(name: str) -> str:
    return (editor_static_dir() / name).read_text(encoding="utf-8")


def _line_of(src: str, needle: str) -> int:
    """回傳 needle 唯一出現的行號（1-based）；不唯一或找不到就失敗，不猜。"""
    hits = [i for i, ln in enumerate(src.splitlines(), 1) if needle in ln]
    assert len(hits) == 1, f"期待 {needle!r} 在檔中唯一出現，實得行號 {hits}"
    return hits[0]


def test_all_deletion_write_sites_are_reviewed():
    """完整列舉關卡：多一個沒審過的寫入點就紅。"""
    unexpected = []
    for name, allowed in ALLOWED_WRITES.items():
        src = _src(name)
        found = Counter()
        for i, ln in enumerate(src.splitlines(), 1):
            if WRITE_RE.search(ln):
                found[ln.strip()] += 1
        want = Counter({k: v for k, (v, _) in allowed.items()})
        if found != want:
            for txt, n in (found - want).items():
                unexpected.append({"檔": name, "新增/多出的寫入點": txt, "多幾次": n})
            for txt, n in (want - found).items():
                unexpected.append({"檔": name, "消失/改寫的寫入點": txt, "少幾次": n})
    assert not unexpected, (
        "state.deletions 的寫入點有變動 —— 請逐條確認新路徑餵不進 `new:` 開頭的鍵，"
        "確認後把它連同理由補進 ALLOWED_WRITES：\n"
        + json.dumps(unexpected, ensure_ascii=False, indent=2)
    )


def test_new_card_key_is_minted_only_in_expanded_cards():
    """`new:` 列鍵只有一個產地（expandedCards）。多一個產地就要重審全部下游。

    注意只算「列的 `key` 欄位」—— `new:${tempId}` 另外也被當 DOM 的 data-idx 與
    timeEditKey 用（app.js:1132/1514/1564/1579/7596），那些不會流進 state.deletions。
    """
    src = _src("app.js")
    mints = [
        (i, ln.strip())
        for i, ln in enumerate(src.splitlines(), 1)
        if re.search(r"\bkey: `new:\$\{", ln)
    ]
    assert len(mints) == 1, f"`new:` 鍵的產地不再唯一：{mints}"
    mint_line = mints[0][0]
    fn_line = _line_of(src, "export function expandedCards(")
    # 產地必須落在 expandedCards 之後、且在下一個 top-level function 之前
    nxt = [
        i
        for i, ln in enumerate(src.splitlines(), 1)
        if i > fn_line and re.match(r"(export )?function \w+\(", ln)
    ]
    end = nxt[0] if nxt else len(src.splitlines())
    assert fn_line < mint_line < end, (
        f"`new:` 鍵不再是在 expandedCards() 內產生（產地 :{mint_line}，"
        f"expandedCards :{fn_line}-{end}）→ 下游三道防線的前提失效，全部重審"
    )


def test_render_cards_skips_new_cards_before_delete_toggle():
    """防線一：renderCards 的刪除鈕之前，新增卡已經 continue 掉。"""
    src = _src("app.js")
    loop = _line_of(src, "let prevRenderedRow = null;")  # renderCards 主迴圈前一行
    skip = _line_of(src, "if (r.newCard) {")
    toggle = _line_of(src, "state.deletions.add(key);")
    assert loop < skip < toggle, (
        f"renderCards 的新增卡跳過分支不再擋在刪除鈕之前（迴圈 :{loop}、"
        f"跳過 :{skip}、刪除鈕 :{toggle}）→ 新增卡的 r.key 會被丟進 deletions"
    )
    # continue 必須真的在那個 if 內（:skip+1..skip+3），不是被改成別的處置
    block = "\n".join(src.splitlines()[skip - 1 : skip + 3])
    assert "continue;" in block, f"if (r.newCard) 分支不再 continue：\n{block}"


def test_marquee_filters_new_card_keys():
    """防線二：時間軸框選明文排除 new: 鍵，且排除發生在寫入之前。"""
    src = _src("timeline.js")
    filt = _line_of(src, '!String(r.key).startsWith("new:")')
    write = _line_of(src, "for (const k of added) state.deletions.add(k);")
    assert filt < write, (
        f"框選的 new: 排除（:{filt}）不再在寫入 deletions（:{write}）之前"
    )


def test_new_cards_cleared_before_cut_derived_selection():
    """防線三：由 cuts 反推選取之前，state.newCards 已清空且中途沒被填回。"""
    src = _src("app.js")
    lines = src.splitlines()
    clear = _line_of(src, "state.newCards = [];")
    select = _line_of(src, "state.deletions = new Set(sel.keys);")
    assert clear < select, (
        f"載入流程不再是「先清 newCards（:{clear}）再由 cuts 反推選取（:{select}）」→ "
        "expandedCards() 會吐出 new: 列，sel.keys 就可能含 new: 鍵"
    )
    refill = [
        (i, lines[i - 1].strip())
        for i in range(clear + 1, select)
        if re.search(r"state\.newCards\s*=(?!=)|state\.newCards\.push", lines[i - 1])
    ]
    assert not refill, f"清空與反推選取之間又把 newCards 填回去了：{refill}"


def test_api_new_key_filter_is_still_the_last_line_of_defence():
    """存檔端那道防禦性 filter 還在（它是防禦不是功能，見本檔開頭）。"""
    src = _src("api.js")
    assert 'filter((k) => !String(k).startsWith("new:"))' in src, (
        "api.js 存檔端的 new: 過濾器被拿掉了 —— 它是上面三道防線失效時的最後一關，"
        "要拿掉請先說明為什麼三道防線足夠"
    )
