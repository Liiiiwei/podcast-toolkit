#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影片剪輯原型第二～五梯 UX（#9／#6／#12／#14／#13／N1／N2／N5–N9／N11–N14／N17–N20）CDP 走查＋回歸。

紀律（lessons-learned 2026-08-25）：
- 每個 ✓ 都綁布林斷言（ok = 實得 == 期待），只印值不算測。
- 可點元素一律用 document.elementFromPoint(中心) 斷言最上層真的是它，
  互動用 Input.dispatchMouseEvent 打真座標。
- 不釘死由文字寬度決定的像素值，只用「>0」「相等／不等」關係式。
- 每次全新 Chrome profile、伺服器支援 Range、開場驗版本指紋與 seekable。

用法：
  python3 -u drive.py                 # 自己起 serve.py（服務 live static）
  python3 -u drive.py --base http://127.0.0.1:PORT   # 用已經在跑的 serve.py
  python3 -u drive.py --json out.json # 另存 {斷言id: 布林}（突變測試用）
結束碼：全綠 0，有紅 1，環境問題（指紋／seekable）2。
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cdp_driver as D  # noqa: E402

RESULTS = {}
ORDER = []
# 每次導頁後取樣到的 (哪一次導頁, document.visibilityState)；結尾的 N17.vis 斷言用
VIS = []
# 只給突變測試用的旋鈕：設了就不把分頁叫回前景，用來證明 N17.vis 真的會紅
NO_FRONT = os.environ.get("VT_DRIVE_NO_FRONT") == "1"


def check(cid, desc, got, want):
    ok = got == want
    RESULTS[cid] = ok
    ORDER.append(cid)
    print("%s %-10s %s｜實得=%s 期待=%s" % (
        "✓" if ok else "✗", cid, desc,
        json.dumps(got, ensure_ascii=False), json.dumps(want, ensure_ascii=False)))
    sys.stdout.flush()
    return ok


class Page:
    def __init__(self, cdp, base):
        self.cdp = cdp
        self.base = base

    def js(self, expr, timeout=30):
        return self.cdp.evaluate(expr, await_promise=True, timeout=timeout)

    def ctl(self, fail="", delay=""):
        urllib.request.urlopen(
            "%s/__ctl?fail=%s&delay=%s" % (self.base, fail, delay), timeout=5).read()

    def stat(self):
        """假伺服器收到幾次寫入、假集目前的值。"""
        return json.loads(urllib.request.urlopen(
            "%s/__stat" % self.base, timeout=5).read().decode("utf-8"))

    def reset(self, audio=False):
        """假集還原成預設值；audio=True＝還原後改成「有外接音檔」的集。"""
        urllib.request.urlopen(
            "%s/__reset%s" % (self.base, "?audio=1" if audio else ""), timeout=5).read()

    def shot(self, path, sel, pad=6):
        """把某個元素（外擴 pad）截成 PNG。"""
        r = self.js("""(() => { const r = document.querySelector(%s).getBoundingClientRect();
          return {x: r.left, y: r.top, w: r.width, h: r.height}; })()""" % json.dumps(sel))
        res = self.cdp.call("Page.captureScreenshot", {"format": "png", "clip": {
            "x": max(0, r["x"] - pad), "y": max(0, r["y"] - pad),
            "width": r["w"] + pad * 2, "height": r["h"] + pad * 2, "scale": 2}})
        with open(path, "wb") as f:
            f.write(base64.b64decode(res["data"]))

    def front(self):
        """把分頁叫回前景（N17）。headless 分頁被前面的步驟（例如按 Esc）弄成
        visibilityState=hidden 之後，Chrome 會無限期延後影片載入；每次導頁前都叫一次，
        後面的步驟才不會量到「環境不給載影片」。"""
        if not NO_FRONT:
            self.cdp.call("Page.bringToFront")

    def note_vis(self, where):
        """記下這次導頁後分頁是不是 visible；不在這裡中止，統一由結尾的 N17.vis 斷言。"""
        VIS.append((where, self.js("document.visibilityState")))

    def goto(self, query, width=1280, height=900):
        self.front()
        self.cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})
        self.cdp.call("Page.navigate", {"url": "about:blank"})
        time.sleep(0.15)
        self.cdp.call("Page.navigate",
                      {"url": "%s/video-edit-prototype.html%s" % (self.base, query)})
        # init() 跑到底才會掛上走查 hook → 用它當「載入流程結束」的訊號
        if not self.wait("typeof window.__vtStats === 'function'", 20):
            print("✗ 環境：頁面 init 沒跑完（__vtStats 不存在）")
            sys.exit(2)
        # 版本指紋：只有這一梯才有的節點；沒有＝服務到舊副本，中止不要繼續跑
        # （用標記不用 svg：icons.js 載不到是要測的失敗情境，不是舊副本）
        if not self.js("!!document.querySelector('#vt-keys-toggle [data-icon]')"):
            print("✗ 環境：版本指紋未命中（服務到的不是第二梯的檔）")
            sys.exit(2)
        self.note_vis("goto %s" % (query or "（真集）"))

    def wait(self, cond, secs=8):
        end = time.time() + secs
        while time.time() < end:
            try:
                if self.cdp.evaluate("!!(%s)" % cond, await_promise=False, timeout=5):
                    return True
            except Exception:
                pass
            time.sleep(0.1)
        return False

    def mouse(self, kind, x, y, buttons=0):
        self.cdp.call("Input.dispatchMouseEvent", {
            "type": kind, "x": float(x), "y": float(y), "button": "left" if kind != "mouseMoved" or buttons else "none",
            "buttons": buttons, "clickCount": 1 if kind != "mouseMoved" else 0})

    def click(self, x, y):
        self.mouse("mouseMoved", x, y)
        self.mouse("mousePressed", x, y, 1)
        self.mouse("mouseReleased", x, y, 0)
        time.sleep(0.15)

    def drag(self, x1, y1, x2, y2, steps=8):
        self.mouse("mouseMoved", x1, y1)
        self.mouse("mousePressed", x1, y1, 1)
        for i in range(1, steps + 1):
            self.mouse("mouseMoved", x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps, 1)
            time.sleep(0.03)
        self.mouse("mouseReleased", x2, y2, 0)
        time.sleep(0.25)

    def key(self, key, code, vk):
        for t in ("keyDown", "keyUp"):
            self.cdp.call("Input.dispatchKeyEvent", {
                "type": t, "key": key, "code": code,
                "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk})
        time.sleep(0.15)

    def center(self, sel):
        """元素中心座標＋該點最上層是不是它（或它的子孫）。"""
        return self.js("""(() => {
          const el = document.querySelector(%s);
          if (!el) return null;
          const r = el.getBoundingClientRect();
          const x = r.left + r.width / 2, y = r.top + r.height / 2;
          const top = document.elementFromPoint(x, y);
          return {x, y, w: r.width, h: r.height, left: r.left, top: r.top,
                  right: r.right, bottom: r.bottom,
                  hit: !!top && (top === el || el.contains(top)),
                  hitExact: top === el};
        })()""" % json.dumps(sel))


# ───────────────────────── #9 載入失敗訊息 ─────────────────────────
FIXED = ["video", "wave", "subs", "align"]
# 使用者看得到的字裡不該出現的東西：端點、狀態碼、檔名、後端術語
TERMS = r"/\/api\/|HTTP|\.json|\.mp4|\.srt|_final|app server|sample-|\b[45]\d\d\b|undefined|null/i"

READ_NOTE = """(() => {
  const note = document.getElementById('vt-empty-note');
  const lis = Array.from(note.querySelectorAll('li'));
  const tops = lis.map((li) => Math.round(li.getBoundingClientRect().top));
  return {
    hidden: note.hidden,
    height: note.getBoundingClientRect().height,
    keys: lis.map((li) => li.dataset.src),
    texts: lis.map((li) => li.textContent),
    // 分行：每一項的 top 都比上一項大（嚴格遞增）＝一項一行，沒有接成一坨
    separateLines: tops.every((t, i) => i === 0 || t > tops[i - 1]),
    blocks: lis.every((li) => getComputedStyle(li).display !== 'inline'),
    hasTerm: %s.test(note.innerText),
    allTitled: lis.every((li) => li.title.length > 0),
    emptyText: lis.some((li) => li.textContent.replace(/^[^：]*：/, '').trim() === ''),
  };
})()""" % TERMS


def run_9(p):
    combos = [
        ("v", ["video"], ""), ("w", ["wave"], ""), ("s", ["subs"], ""), ("a", ["align"], ""),
        ("vw", ["video", "wave"], ""), ("sa", ["subs", "align"], ""),
        ("all", FIXED, ""),
        # 讓到達順序跟固定順序對不上：波形最慢、影片次慢
        ("alld", FIXED, "wave:500,video:300"),
    ]
    for tag, fails, delay in combos:
        p.ctl(",".join(fails), delay)
        p.goto("")
        p.wait("document.querySelectorAll('#vt-empty-note li').length >= %d" % len(fails), 8)
        time.sleep(0.4)  # 再等一下：多出來的項目（不該有的）也要有機會出現
        n = p.js(READ_NOTE)
        want = [k for k in FIXED if k in fails]
        check("9.%s.keys" % tag, "失敗 %s → 清單項目與固定順序" % "+".join(fails), n["keys"], want)
        check("9.%s.show" % tag, "清單可見（未隱藏且高度>0）",
              (not n["hidden"]) and n["height"] > 0, True)
        check("9.%s.line" % tag, "一項一行（top 嚴格遞增、非 inline）",
              n["separateLines"] and n["blocks"], True)
        check("9.%s.term" % tag, "可見文字不含端點／狀態碼／檔名／術語", n["hasTerm"], False)
        check("9.%s.text" % tag, "每項都有說明文字、技術細節收在 title",
              (not n["emptyText"]) and n["allTitled"], True)
        if "align" in fails:
            # 對齊 popover 的提示與清單同一份文案（單一來源）
            t = p.center("#vt-al-toggle")
            p.click(t["x"], t["y"])
            h = p.js("""(() => { const h = document.getElementById('vt-align-hint');
              const li = document.querySelector('#vt-empty-note li[data-src=align]');
              return {text: h.textContent.trim(), vis: h.getBoundingClientRect().height > 0,
                      same: !!li && h.textContent.trim().length > 0 && li.textContent.endsWith(h.textContent.trim()),
                      term: %s.test(h.textContent)}; })()""" % TERMS)
            check("9.%s.pop" % tag, "對齊 popover 提示可見、與清單同文案、不含術語",
                  [h["vis"], h["same"], h["term"]], [True, True, False])

    # 全部成功 → 清單要藏起來（success 態）
    p.ctl()
    p.goto("")
    time.sleep(0.6)
    n = p.js(READ_NOTE)
    check("9.ok.hide", "四個來源都成功 → 清單隱藏且無項目", [n["hidden"], n["keys"]], [True, []])

    # 存對齊後重載字幕失敗 → 不靜默，進清單；之後再存一次成功 → 那一項要消失
    p.ctl("subs")
    p.js("window.__vtSaveAlign()")
    n = p.js(READ_NOTE)
    check("9.save.fail", "存對齊後重載字幕失敗 → 清單出現字幕一項", n["keys"], ["subs"])
    p.ctl()
    p.js("window.__vtSaveAlign()")
    n = p.js(READ_NOTE)
    check("9.save.ok", "再存一次重載成功 → 清單清空並隱藏", [n["hidden"], n["keys"]], [True, []])

    # demo 模式也走同一張表
    p.ctl("subs,wave")
    p.goto("?demo")
    p.wait("document.querySelectorAll('#vt-empty-note li').length >= 2", 8)
    time.sleep(0.3)
    n = p.js(READ_NOTE)
    check("9.demo.keys", "demo：波形＋字幕失敗 → 固定順序", n["keys"], ["wave", "subs"])
    check("9.demo.term", "demo：可見文字不含檔名／術語", n["hasTerm"], False)
    p.ctl()


# ───────────────────────── #6 播放頭抓柄＋回歸 ─────────────────────────
def run_6(p):
    p.ctl()
    p.goto("?demo")
    ready = p.wait("(() => { const v = document.getElementById('vt-video');"
                   " return v.readyState >= 1 && v.seekable.length > 0; })()", 15)
    if not check("6.pre", "媒體可 seek（readyState>=1 且 seekable.length>0）", ready, True):
        print("✗ 環境：影片不可 seek，後面的 seek 斷言沒有意義，中止")
        sys.exit(2)

    p.js("window.__vtSeek(8)")
    p.wait("!document.getElementById('vt-video').seeking", 5)
    time.sleep(0.2)

    h = p.js("""(() => {
      const g = document.getElementById('vt-playhead-grip');
      const cs = getComputedStyle(g, '::after');
      const m = /rgba?\\(([^)]+)\\)/.exec(cs.backgroundColor);
      const parts = m ? m[1].split(',').map((s) => parseFloat(s)) : [];
      const alpha = parts.length === 4 ? parts[3] : (parts.length === 3 ? 1 : 0);
      const gr = g.getBoundingClientRect();
      const tl = document.getElementById('vt-timeline').getBoundingClientRect();
      const w = parseFloat(cs.width) || 0, hh = parseFloat(cs.height) || 0;
      // 抓柄中心（::after 相對 grip 的位置）
      const hx = gr.left + (parseFloat(cs.left) || 0) + w / 2;
      const hy = gr.top + (parseFloat(cs.top) || 0) + hh / 2;
      const top = document.elementFromPoint(hx, hy);
      return {content: cs.content, w, hh, alpha, hx, hy,
              hitGrip: top === g,
              insideWave: hy > tl.top && hy < tl.bottom && hx > tl.left && hx < tl.right};
    })()""")
    check("6.vis", "抓柄常駐可見（有內容、寬高>0、底色不透明）",
          [h["content"] not in ("none", "normal", ""), h["w"] > 0, h["hh"] > 0, h["alpha"] > 0],
          [True, True, True, True])
    check("6.hit", "抓柄中心最上層是 grip，且落在波形軌內", [h["hitGrip"], h["insideWave"]], [True, True])

    g = p.center("#vt-playhead-grip")
    check("6.hitc", "grip 命中區中心最上層是 grip（elementFromPoint）", g["hitExact"], True)

    # 抓柄不蓋到上面兩軌：同一個 x，在標題卡軌／字幕軌命中的不能是 grip
    over = p.js("""(() => {
      const g = document.getElementById('vt-playhead-grip');
      const x = g.getBoundingClientRect().left + g.getBoundingClientRect().width / 2;
      const f = (id) => { const r = document.getElementById(id).getBoundingClientRect();
        return document.elementFromPoint(x, r.top + r.height / 2) === g; };
      return [f('vt-card-track'), f('vt-sub-track')];
    })()""")
    check("6.lane", "同 x 在標題卡軌／字幕軌命中的不是 grip（沒蓋住上兩軌）", over, [False, False])

    t0 = p.js("document.getElementById('vt-video').currentTime")
    p.drag(h["hx"], h["hy"], h["hx"] + 160, h["hy"])
    p.wait("!document.getElementById('vt-video').seeking", 5)
    a = p.js("""(() => { const g = document.getElementById('vt-playhead-grip');
      const ph = document.getElementById('vt-playhead').getBoundingClientRect();
      const gr = g.getBoundingClientRect();
      return {t: document.getElementById('vt-video').currentTime,
              cuts: window.__vtStats().cutCount,
              scrubbing: g.classList.contains('is-scrubbing'),
              aligned: Math.abs((ph.left + ph.width / 2) - (gr.left + gr.width / 2)) <= 1.5}; })()""")
    check("6.drag", "從抓柄往右拖 → currentTime 變大", [a["t"] != t0, a["t"] > t0], [True, True])
    check("6.side", "拖完沒誤建剪除段、is-scrubbing 已清、播放頭線跟著抓柄",
          [a["cuts"], a["scrubbing"], a["aligned"]], [0, False, True])

    # ── 回歸：點時間軸跳轉 ──
    r = p.js("""(() => { const r = document.getElementById('vt-timeline').getBoundingClientRect();
      return {x: r.left + r.width * 0.2, y: r.top + r.height * 0.75, frac: 0.2,
              dur: window.__vt.duration}; })()""")
    before = p.js("document.getElementById('vt-video').currentTime")
    p.click(r["x"], r["y"])
    p.wait("!document.getElementById('vt-video').seeking", 5)
    after = p.js("document.getElementById('vt-video').currentTime")
    check("R.seek", "點時間軸 20% 處 → 播放頭跳到約 20%（±0.3 秒）且不建剪除段",
          [after != before, abs(after - r["frac"] * r["dur"]) <= 0.3, p.js("window.__vtStats().cutCount")],
          [True, True, 0])

    # ── 回歸：標題卡拖曳／刪除 ──
    p.js("window.__vtDropTpl('big', 10)")
    time.sleep(0.2)
    c = p.center("#vt-card-track .vt-card")
    s0 = p.js("window.__vtCards()[0].start")
    check("R.cardhit", "標題卡塊中心最上層是卡本身", c["hit"], True)
    p.drag(c["x"], c["y"], c["x"] + 90, c["y"])
    s1 = p.js("window.__vtCards()[0].start")
    check("R.carddrag", "往右拖標題卡 → 進點變大", [s1 != s0, s1 > s0], [True, True])
    c = p.center("#vt-card-track .vt-card")
    p.click(c["x"], c["y"])
    sel = p.js("window.__vt.selectedCard === window.__vtCards()[0].id")
    p.key("Backspace", "Backspace", 8)
    check("R.carddel", "點卡選取後按 ⌫ → 卡被刪（0 張）且沒誤建剪除段",
          [sel, p.js("window.__vtCards().length"), p.js("window.__vtStats().cutCount")], [True, 0, 0])

    # ── 回歸：字幕列刪除鈕 ──
    n0 = p.js("window.__vt.subs.length")
    row = p.center("#vt-line-list .vt-line")
    if row:
        p.mouse("mouseMoved", row["x"], row["y"])
        time.sleep(0.2)
    b = p.center("#vt-line-list .vt-line-tool[data-act=delete]")
    check("R.delhit", "字幕列「刪」鈕中心最上層是它", bool(b) and b["hit"], True)
    if b:
        p.click(b["x"], b["y"])
    check("R.del", "點「刪」→ 字幕少一句", p.js("window.__vt.subs.length"), n0 - 1)


# ───────────────────────── #12 操作說明 popover ─────────────────────────
POP_STATE = """(() => { const t = document.getElementById('vt-keys-toggle');
  const pop = document.getElementById('vt-keys-pop'); const r = pop.getBoundingClientRect();
  return {open: !pop.hidden && r.height > 0, aria: t.getAttribute('aria-expanded'),
          inView: r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight,
          focusOnToggle: document.activeElement === t}; })()"""


def run_12(p, height, tag):
    p.ctl()
    p.goto("?demo", height=height)
    t = p.center("#vt-keys-toggle")
    meta = p.js("""(() => { const t = document.getElementById('vt-keys-toggle');
      const pop = document.getElementById('vt-keys-pop');
      const keys = ['空白鍵', '←', '→', '⇧', 'I', 'O', '⌫', 'Esc', '⌘Z'];
      return {tag: t.tagName, missing: keys.filter((k) => !pop.textContent.includes(k)),
              // 清單只有一份：頁面上其他地方的 title 不該再藏一份快捷鍵清單
              dupTitles: Array.from(document.querySelectorAll('[title]'))
                .filter((e) => /進點/.test(e.title) && /出點/.test(e.title) && /Esc/.test(e.title)).length}; })()""")
    check("12.%s.btn" % tag, "ⓘ 是真的 button，中心最上層是它", [meta["tag"], t["hitExact"]], ["BUTTON", True])
    check("12.%s.keys" % tag, "popover 內快捷鍵齊全、title 不再藏第二份", [meta["missing"], meta["dupTitles"]], [[], 0])
    s = p.js(POP_STATE)
    check("12.%s.init" % tag, "初始：關閉、aria-expanded=false", [s["open"], s["aria"]], [False, "false"])
    p.click(t["x"], t["y"])
    s = p.js(POP_STATE)
    check("12.%s.open" % tag, "滑鼠點 ⓘ → 開啟、aria-expanded=true、整塊在視窗內（高 %d）" % height,
          [s["open"], s["aria"], s["inView"]], [True, "true", True])
    p.click(t["x"], t["y"])
    s = p.js(POP_STATE)
    check("12.%s.close" % tag, "再點一次 → 關閉", [s["open"], s["aria"]], [False, "false"])
    # 鍵盤：focus 後按 Enter 開、Esc 關且焦點回到鈕
    p.js("document.getElementById('vt-keys-toggle').focus()")
    p.cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter",
                                          "windowsVirtualKeyCode": 13, "text": "\r"})
    p.cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter",
                                          "windowsVirtualKeyCode": 13})
    time.sleep(0.15)
    s1 = p.js(POP_STATE)
    p.key("Escape", "Escape", 27)
    s2 = p.js(POP_STATE)
    check("12.%s.kbd" % tag, "鍵盤 Enter 開、Esc 關且焦點回到 ⓘ",
          [s1["open"], s2["open"], s2["focusOnToggle"]], [True, False, True])
    # 點外面關
    p.click(t["x"], t["y"])
    o = p.js(POP_STATE)["open"]
    v = p.center("#vt-video")
    p.click(v["x"], v["y"])
    check("12.%s.out" % tag, "開著時點 popover 外面 → 關閉", [o, p.js(POP_STATE)["open"]], [True, False])


# ───────────────────────── #14 標題卡提示不重複 ─────────────────────────
def run_14(p):
    r = p.js("""(() => {
      const body = document.getElementById('vt-sec-card');
      const head = document.querySelector('[aria-controls=vt-sec-card]');
      const own = (e) => Array.from(e.childNodes).filter((n) => n.nodeType === 3)
        .map((n) => n.textContent).join('');
      const all = [head, ...head.querySelectorAll('*'), body, ...body.querySelectorAll('*')];
      const hits = all.filter((e) => own(e).includes('藍軌') && e.getBoundingClientRect().height > 0);
      const note = head.querySelector('.vt-side-note');
      return {count: hits.length, ids: hits.map((e) => e.id),
              note: note.textContent.trim(), noteVisible: note.getBoundingClientRect().height > 0}; })()""")
    check("14.once", "標題卡區提到「藍軌」的可見提示只有一處（版型格下方）",
          [r["count"], r["ids"]], [1, ["vt-tpl-hint"]])
    check("14.note", "標頭註記仍在、非空、不重複「藍軌」",
          [r["noteVisible"], len(r["note"]) > 0, "藍軌" in r["note"]], [True, True, False])


# ───────────────────────── 回歸：對齊 popover、單一捲軸 ─────────────────────────
def run_align(p):
    p.ctl()
    p.goto("")
    t = p.center("#vt-al-toggle")
    check("R.al.hit", "真集：頂列「對齊」鈕可見且中心最上層是它", bool(t) and t["hit"] and t["w"] > 0, True)
    p.click(t["x"], t["y"])
    a = p.js("""(() => { const pop = document.getElementById('vt-align-pop');
      const f = (id) => parseFloat(document.getElementById(id).value);
      return {open: !pop.hidden && pop.getBoundingClientRect().height > 0,
              vals: [f('vt-al-camb'), f('vt-al-head'), f('vt-al-tail'), f('vt-al-sub')]}; })()""")
    check("R.al.open", "點「對齊」→ popover 開、欄位＝該集的值", [a["open"], a["vals"]], [True, [0.25, 1.5, 0.75, 0]])
    # 同時只開一個：開著對齊再點 ⓘ
    k = p.center("#vt-keys-toggle")
    p.click(k["x"], k["y"])
    both = p.js("[document.getElementById('vt-align-pop').hidden, document.getElementById('vt-keys-pop').hidden]")
    check("R.al.excl", "開著對齊再點 ⓘ → 對齊收起、操作說明打開", both, [True, False])
    p.click(t["x"], t["y"])
    p.click(t["x"], t["y"])
    both = p.js("[document.getElementById('vt-align-pop').hidden, document.getElementById('vt-keys-pop').hidden]")
    check("R.al.close", "點「對齊」開（操作說明收起）再點一次關 → 兩個都關", both, [True, True])


def run_scroll(p, query, height, tag):
    p.ctl()
    p.goto(query, height=height)
    time.sleep(0.4)
    r = p.js("""(() => {
      const side = document.querySelector('.vt-side');
      const list = document.querySelector('.vt-line-list');
      // 從字幕清單往上到右欄，實際在捲（內容高 > 可視高、overflow-y 為 auto/scroll）的容器數
      let n = 0;
      for (let el = list; el; el = el.parentElement) {
        const oy = getComputedStyle(el).overflowY;
        if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 1) n++;
        if (el === side) break;
      }
      return {lines: list.querySelectorAll('.vt-line').length, scrollers: n,
              sideScrolls: side.scrollHeight > side.clientHeight + 2,
              pageScrolls: document.documentElement.scrollHeight > innerHeight + 1}; })()""")
    check("R.sc.%s" % tag, "視窗高 %d：字幕面板至多一條捲軸、右欄整欄不捲" % height,
          [r["lines"] > 0, r["scrollers"] <= 1, r["sideScrolls"]], [True, True, False])


# ───────────────────────── N1 對齊未載入不可存 ─────────────────────────
AL_FIELDS = ["vt-al-camb", "vt-al-head", "vt-al-tail", "vt-al-sub"]
AL_DOM = """(() => {
  const ids = %s;
  const g = (id) => document.getElementById(id);
  const hint = g('vt-align-hint'), st = g('vt-al-status'), note = g('vt-align-note');
  return {
    disabled: ids.map((id) => g(id).disabled),
    values: ids.map((id) => g(id).value),
    audioDisabled: g('vt-al-audio').disabled, audioValue: g('vt-al-audio').value,
    saveDisabled: g('vt-al-save').disabled,
    hintText: hint.textContent.trim(), hintVisible: hint.getBoundingClientRect().height > 0,
    note: note ? note.textContent.trim() : '',
    statusText: st.textContent.trim(), statusErr: st.classList.contains('is-err'),
    statusVisible: st.getBoundingClientRect().height > 0,
  };
})()""" % json.dumps(AL_FIELDS)


def run_n1(p):
    # ── 失敗態：只有 /api/episode 失敗 ──
    p.reset()
    p.ctl("align")
    p.goto("")
    t = p.center("#vt-al-toggle")
    p.click(t["x"], t["y"])
    d = p.js(AL_DOM)
    check("N1.f.lock", "對齊載入失敗 → 四欄位與儲存鈕都 disabled",
          [d["disabled"], d["saveDisabled"], d["audioDisabled"]], [[True] * 4, True, True])
    check("N1.f.blank", "欄位留空（不是看似真值的 0）",
          [d["values"], d["audioValue"]], [[""] * 4, ""])
    check("N1.f.hint", "popover 內有可見說明文字、標頭註記＝載入失敗",
          [d["hintVisible"], len(d["hintText"]) > 0, "不能儲存" in d["hintText"], d["note"]],
          [True, True, True, "載入失敗"])
    # 資料層：繞過 disabled 直接呼叫存檔
    s0 = p.stat()["saves"]
    p.js("window.__vtSaveAlign()")
    time.sleep(0.4)
    d = p.js(AL_DOM)
    check("N1.f.data", "程式強制儲存 → 沒有發出寫入請求（增量 0）、有可見錯誤訊息",
          [p.stat()["saves"] - s0, d["statusErr"], d["statusVisible"], len(d["statusText"]) > 0],
          [0, True, True, True])
    # 資料層：把 disabled 拿掉後用真滑鼠點儲存鈕
    p.js("document.getElementById('vt-al-save').disabled = false")
    b = p.center("#vt-al-save")
    p.click(b["x"], b["y"])
    time.sleep(0.4)
    check("N1.f.click", "拿掉 disabled 後真滑鼠點儲存鈕（命中）→ 寫入增量仍是 0",
          [b["hit"], p.stat()["saves"] - s0], [True, 0])
    check("N1.f.keep", "假集的值原封不動（沒被 0 蓋掉）",
          [p.stat()["episode"]["head_trim_sec"], p.stat()["episode"]["camera_sync_offset"]["b"]],
          [1.5, 0.25])
    p.reset()  # 突變時這裡可能真的寫進 0；還原，後面的回歸才不會連帶轉紅

    # ── 載入中：/api/episode 慢 1.5 秒；init 還沒跑完，只能直接讀 DOM ──
    p.ctl("", "align:1500")
    p.front()
    p.cdp.call("Page.navigate", {"url": "about:blank"})
    time.sleep(0.15)
    p.cdp.call("Page.navigate", {"url": "%s/video-edit-prototype.html" % p.base})
    p.wait("document.getElementById('vt-al-save') && document.readyState !== 'loading'", 8)
    time.sleep(0.5)
    p.note_vis("N1 載入中")
    pending = p.js("typeof window.__vtStats !== 'function'")
    d = p.js(AL_DOM)
    s0 = p.stat()["saves"]
    p.js("(() => { const b = document.getElementById('vt-al-save'); b.disabled = false; b.click(); })()")
    time.sleep(0.2)
    check("N1.l.lock", "載入中（init 未完成）→ 欄位與儲存鈕 disabled、欄位留空、強制點存增量 0",
          [pending, d["disabled"], d["saveDisabled"], d["values"], p.stat()["saves"] - s0],
          [True, [True] * 4, True, [""] * 4, 0])
    p.wait("typeof window.__vtStats === 'function'", 15)
    time.sleep(0.2)
    d = p.js(AL_DOM)
    check("N1.l.after", "載入完成 → 自動解鎖並填入真值",
          [d["disabled"], d["saveDisabled"], [float(v) for v in d["values"] if v != ""]],
          [[False] * 4, False, [0.25, 1.5, 0.75, 0.0]])

    # ── 成功態：真值、可存、存後重載讀回 ──
    p.ctl()
    p.goto("")
    t = p.center("#vt-al-toggle")
    p.click(t["x"], t["y"])
    d = p.js(AL_DOM)
    check("N1.ok.open", "載入成功 → 四欄位與儲存鈕可用、欄位＝該集的值、無佔位留空",
          [d["disabled"], d["saveDisabled"], [float(v) for v in d["values"] if v != ""]],
          [[False] * 4, False, [0.25, 1.5, 0.75, 0.0]])
    p.js("""(() => { const el = document.getElementById('vt-al-head'); el.value = '2.25';
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true})); })()""")
    s0 = p.stat()["saves"]
    b = p.center("#vt-al-save")
    p.click(b["x"], b["y"])
    p.wait("document.getElementById('vt-al-status').classList.contains('is-ok')", 8)
    st = p.stat()
    check("N1.ok.save", "真滑鼠點儲存（命中）→ 恰發出 1 次寫入、伺服器端 head=2.25、其餘鍵不變",
          [b["hit"], st["saves"] - s0, st["episode"]["head_trim_sec"],
           st["episode"]["camera_sync_offset"]["b"], st["episode"]["tail_trim_sec"]],
          [True, 1, 2.25, 0.25, 0.75])
    p.goto("")  # 重載整頁 → 讀回
    d = p.js(AL_DOM)
    check("N1.ok.rt", "重載頁面 → 讀回剛存的值（round-trip）",
          [float(v) for v in d["values"] if v != ""], [0.25, 2.25, 0.75, 0.0])
    p.reset()


# ───────────────────────── #13 圖示統一 ─────────────────────────
# 原本散在按鈕文字裡的圖示字符（鍵帽說明裡的 ← → ⇧ ⌫ ⌘ 是「鍵名」不是圖示，不在此列）
GLYPHS = "▶⏸✕✖×▾▸▴▼▲ⓘ"
ICON_BTNS = ["#vt-play", "#vt-zoom-out", "#vt-zoom-in", "#vt-keys-toggle",
             "[aria-controls=vt-sec-card]", "[aria-controls=vt-sec-subs]",
             "#vt-line-list .vt-line:nth-child(2) .vt-line-tool[data-act=split]",
             "#vt-line-list .vt-line:nth-child(2) .vt-line-tool[data-act=merge]",
             "#vt-line-list .vt-line:nth-child(2) .vt-line-tool[data-act=card]",
             "#vt-line-list .vt-line:nth-child(2) .vt-line-tool[data-act=delete]"]
ICON_SCAN = """(() => {
  const sels = %s, glyphs = %s;
  const btns = sels.map((s) => {
    const el = document.querySelector(s);
    if (!el) return {sel: s, missing: true};
    const svgs = Array.from(el.querySelectorAll('svg'));
    const r = el.getBoundingClientRect();
    const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    const sr = svgs[0] ? svgs[0].getBoundingClientRect() : {width: 0, height: 0, left: 0, right: 0, top: 0, bottom: 0};
    return {sel: s, n: svgs.length, w: sr.width, h: sr.height,
            // 圖示整個落在按鈕框內＝沒被裁、沒跑出去
            inside: sr.left >= r.left - 0.5 && sr.right <= r.right + 0.5 &&
                    sr.top >= r.top - 0.5 && sr.bottom <= r.bottom + 0.5,
            // 命中圖示（svg 或其外層）＝圖示吃掉點擊，不算命中按鈕
            hit: !!top && (top === el || (el.contains(top) && !top.closest('svg, [data-icon]'))),
            name: (el.getAttribute('aria-label') || el.title || el.textContent || '').trim(),
            stroke: svgs[0] ? svgs[0].getAttribute('stroke-width') : null,
            color: svgs[0] ? getComputedStyle(svgs[0]).stroke : null};
  });
  // 全頁按鈕／summary 的文字裡不該再有圖示字符，也不該有單獨一個 + 或 -
  const bad = Array.from(document.querySelectorAll('button, summary, [role=button]'))
    .filter((e) => { const t = e.textContent; return Array.from(glyphs).some((g) => t.includes(g)) ||
      /^[-+]$/.test(t.trim()); })
    .map((e) => e.id || e.className || e.tagName);
  const iconEls = Array.from(document.querySelectorAll('[data-icon]'));
  return {btns, bad,
          emptyIcons: iconEls.filter((e) => !e.querySelector('svg')).map((e) => e.dataset.icon),
          rest: ['.vt-adv-caret', '#vt-render-close'].map((s) => {
            const e = document.querySelector(s); return e ? e.querySelectorAll('svg').length : -1; })};
})()""" % (json.dumps(ICON_BTNS), json.dumps(GLYPHS))


def run_13(p, shots=True):
    p.ctl()
    p.goto("?demo")
    row = p.center("#vt-line-list .vt-line:nth-child(2)")
    p.mouse("mouseMoved", row["x"], row["y"])
    time.sleep(0.25)
    r = p.js(ICON_SCAN)
    b = r["btns"]
    check("13.glyph", "按鈕／summary 文字裡已無圖示字符與單獨的 +／-", r["bad"], [])
    check("13.one", "每顆圖示鈕內恰一個圖示元素（svg）",
          [x["sel"] for x in b if x.get("missing") or x["n"] != 1], [])
    check("13.size", "圖示寬高 > 0 且整個落在按鈕框內（沒被裁）",
          [x["sel"] for x in b if x.get("missing") or not (x["w"] > 0 and x["h"] > 0 and x["inside"])], [])
    check("13.hit", "按鈕中心最上層是按鈕本身、不是圖示（圖示不吃點擊）",
          [x["sel"] for x in b if x.get("missing") or not x["hit"]], [])
    check("13.name", "每顆圖示鈕的可及性名稱非空",
          [x["sel"] for x in b if x.get("missing") or not x["name"]], [])
    check("13.same", "線條粗細只有一種（同一套圖示）、沒有空的 [data-icon]、其餘圖示位各有 1 個 svg",
          [len(set(x.get("stroke") for x in b)), None in set(x.get("stroke") for x in b),
           r["emptyIcons"], r["rest"]],
          [1, False, [], [1, 1]])
    if shots:
        p.shot("/private/tmp/vt-ux2-13-toolbar.png", ".vt-controls")
        p.shot("/private/tmp/vt-ux2-13-timeline-head.png", ".vt-tl-head")
        p.shot("/private/tmp/vt-ux2-13-line-tools.png", "#vt-line-list .vt-line:nth-child(2)")

    # 播放鈕：圖示與可及性名稱跟著狀態換，始終恰一個圖示
    PLAY = """(() => { const b = document.getElementById('vt-play');
      return [b.querySelector('[data-icon]').dataset.icon, b.querySelectorAll('svg').length,
              b.textContent.trim()]; })()"""
    s0 = p.js(PLAY)
    pb = p.center("#vt-play")
    p.click(pb["x"], pb["y"])
    p.wait("!document.getElementById('vt-video').paused", 5)
    time.sleep(0.2)
    s1 = p.js(PLAY)
    p.click(pb["x"], pb["y"])
    p.wait("document.getElementById('vt-video').paused", 5)
    time.sleep(0.2)
    s2 = p.js(PLAY)
    check("13.play", "點播放鈕 → 圖示／文字換成暫停，再點換回；全程恰一個圖示",
          [s0, s1, s2], [["play", 1, "播放"], ["pause", 1, "暫停"], ["play", 1, "播放"]])

    # 收合／展開：同一個圖示轉向（不換字符），行為不變
    SEC = """(() => { const h = document.querySelector('[aria-controls=vt-sec-card]');
      const c = h.querySelector('.vt-side-caret');
      return [h.getAttribute('aria-expanded'), getComputedStyle(c).transform !== 'none',
              c.querySelectorAll('svg').length]; })()"""
    a0 = p.js(SEC)
    hb = p.center("[aria-controls=vt-sec-card]")
    p.click(hb["x"], hb["y"])
    time.sleep(0.3)
    a1 = p.js(SEC)
    hb = p.center("[aria-controls=vt-sec-card]")
    p.click(hb["x"], hb["y"])
    time.sleep(0.3)
    a2 = p.js(SEC)
    check("13.caret", "點區塊標頭 → 收合且箭頭轉向；再點展開轉回；圖示始終 1 個",
          [a0, a1, a2], [["true", False, 1], ["false", True, 1], ["true", False, 1]])

    # 失敗路徑：icons.js 載不到 → 不留空白鈕（改顯示文字）且進載入失敗清單
    p.ctl("icons")
    p.goto("?demo")
    time.sleep(0.3)
    f = p.js("""(() => { const note = document.getElementById('vt-empty-note');
      const txt = (s) => document.querySelector(s).textContent.trim();
      const row = document.querySelector('#vt-line-list .vt-line');
      return {svgs: document.querySelectorAll('button svg').length,
              keys: Array.from(note.querySelectorAll('li')).map((li) => li.dataset.src),
              shown: !note.hidden && note.getBoundingClientRect().height > 0,
              texts: [txt('#vt-zoom-out'), txt('#vt-zoom-in'), txt('#vt-keys-toggle'), txt('#vt-play')],
              tools: Array.from(row.querySelectorAll('.vt-line-tool')).every((b) => b.textContent.trim().length > 0)}; })()""")
    check("13.fail", "icons.js 載入失敗 → 按鈕改顯示文字、清單出現「圖示」一項（不靜默）",
          [f["svgs"], f["keys"], f["shown"], f["texts"], f["tools"]],
          [0, ["icons"], True, ["縮小", "放大", "說明", "播放"], True])
    p.ctl()


# ───────────────────────── 第四梯（N2／N5–N9／N11／N12） ─────────────────────────
LOAD_BUSY = """(() => {
  const a = (s) => document.querySelector(s).getAttribute('aria-busy');
  const n = document.getElementById('vt-load-busy');
  return {pending: typeof window.__vtStats !== 'function',
          stage: a('.vt-stage'), tl: a('#vt-timeline'), list: a('#vt-line-list'),
          shown: !n.hidden && n.getBoundingClientRect().height > 0, text: n.textContent.trim()};
})()"""
VIDEO_READY = "document.getElementById('vt-video').readyState >= 1"
CARD_PARTS = """(() => {
  const hit = (el) => { if (!el) return null; const r = el.getBoundingClientRect();
    return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === el; };
  const card = document.querySelector('#vt-card-track .vt-card');
  const del = card && card.querySelector('.vt-card-del');
  return {w: card ? card.getBoundingClientRect().width : 0,
          dels: document.querySelectorAll('#vt-card-track .vt-card-del').length,
          delHit: hit(del), lHit: hit(card && card.querySelector('.vt-card-h.is-l')),
          rHit: hit(card && card.querySelector('.vt-card-h.is-r')),
          name: del ? (del.getAttribute('aria-label') || '') : '',
          icon: del ? [del.querySelectorAll('[data-icon="x"] svg').length, del.textContent.trim()] : null};
})()"""


OUT_DEL = """(() => {
  const track = document.getElementById('vt-card-track');
  const card = track.querySelector('.vt-card');
  const outs = track.querySelectorAll('.vt-card-del.is-out');
  const b = outs[0];
  if (!b || !card) return {outs: outs.length, inCard: card ? card.querySelectorAll('.vt-card-del').length : -1,
                           hit: false, outside: false, inTrack: false, side: null, name: '', icon: null};
  const r = b.getBoundingClientRect(), k = card.getBoundingClientRect(), t = track.getBoundingClientRect();
  return {outs: outs.length, inCard: card.querySelectorAll('.vt-card-del').length,
          hit: document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === b,
          outside: r.left >= k.right || r.right <= k.left,
          inTrack: r.width > 0 && r.left >= t.left && r.right <= t.right && r.top >= t.top && r.bottom <= t.bottom,
          side: r.left >= k.right ? 'right' : r.right <= k.left ? 'left' : 'over',
          name: b.getAttribute('aria-label') || '',
          icon: [b.querySelectorAll('[data-icon="x"] svg').length, b.textContent.trim()]};
})()"""


def space_key(p):
    """帶 text 的空白鍵：按鈕的鍵盤啟動（keyup 觸發 click）要有 text 才會發生。"""
    for t in ("keyDown", "keyUp"):
        p.cdp.call("Input.dispatchKeyEvent", {
            "type": t, "key": " ", "code": "Space", "text": " ",
            "windowsVirtualKeyCode": 32, "nativeVirtualKeyCode": 32})
    time.sleep(0.3)


# 照像素擺卡（由左到右，維持 titleCards 的時間順序＝DOM 順序），選取第 sel 張
ADJ_SETUP = """((cards, sel) => { const s = window.__vt; const dur = window.__vtStats().duration;
  const pps = document.getElementById('vt-card-track').getBoundingClientRect().width / dur;
  if (s.titleCards.length !== cards.length) return null;
  cards.forEach((k, i) => { s.titleCards[i].start = k[0] / pps; s.titleCards[i].end = (k[0] + k[1]) / pps; });
  window.__vtSelectCard(null); window.__vtSelectCard(s.titleCards[sel].id);
  return s.titleCards.map((c) => c.id); })(%s, %d)"""

# 第 idx 張卡（窄卡）的外側鈕與全軌各卡把手的實測
ADJ_PROBE = """((idx) => {
  const track = document.getElementById('vt-card-track');
  const cards = Array.from(track.querySelectorAll('.vt-card'));
  const me = cards[idx];
  if (!me) return {w: 0, gapR: -1, outs: -1, dels: -1, side: null, overlap: -1, inTrack: false, sel: [], cards: []};
  const H = (el) => { if (!el) return null; const r = el.getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    return {x, y, hit: document.elementFromPoint(x, y) === el}; };
  const k = me.getBoundingClientRect(), t = track.getBoundingClientRect();
  const outs = track.querySelectorAll('.vt-card-del.is-out');
  const b = outs[0], r = b ? b.getBoundingClientRect() : null;
  const next = cards[idx + 1];
  return {w: k.width, gapR: next ? next.getBoundingClientRect().left - k.right : -1,
    outs: outs.length, dels: track.querySelectorAll('.vt-card-del').length,
    side: !r ? null : r.left >= k.right ? 'right' : r.right <= k.left ? 'left' : 'over',
    overlap: !r ? -1 : cards.filter((c) => { const q = c.getBoundingClientRect();
      return r.left < q.right && r.right > q.left; }).length,
    inTrack: !!r && r.width > 0 && r.left >= t.left && r.right <= t.right && r.top >= t.top && r.bottom <= t.bottom,
    sel: cards.map((c) => c.classList.contains('is-selected')),
    cards: cards.map((c) => ({l: H(c.querySelector('.vt-card-h.is-l')), r: H(c.querySelector('.vt-card-h.is-r'))}))};
})(%d)"""


def narrow_card(p, px):
    """把第一張標題卡縮成約 px 寬（改出點後重畫），回實際寬度。"""
    p.js("""(() => { const c = window.__vt.titleCards[0];
      const r = document.querySelector('#vt-card-track .vt-card').getBoundingClientRect();
      c.end = c.start + %d / (r.width / (c.end - c.start));
      window.__vtSelectCard(null); })()""" % px)
    time.sleep(0.2)
    return p.js("document.querySelector('#vt-card-track .vt-card').getBoundingClientRect().width")


def run_b4(p):
    # ── N2：初次載入期間有可見的「載入中」；完成或失敗後移除 ──
    # 前面的走查（run_12 按過 Esc）會讓 headless 分頁變成 visibilityState=hidden，Chrome 對
    # 隱藏分頁會無限期延後影片載入（readyState 0／networkState 2）→ 先把分頁叫回前景，
    # 否則量到的是「環境不給載影片」而不是載入中字樣該不該收
    p.front()
    p.reset()
    p.ctl("", "wave:1500")
    p.cdp.call("Page.navigate", {"url": "about:blank"})
    time.sleep(0.15)
    p.cdp.call("Page.navigate", {"url": "%s/video-edit-prototype.html" % p.base})
    p.wait("document.getElementById('vt-load-busy') && document.readyState !== 'loading'", 8)
    time.sleep(0.5)
    p.note_vis("N2 載入中")
    b = p.js(LOAD_BUSY)
    check("N2.busy", "載入中（波形慢 1.5 秒、init 未完成）→ 時間軸區塊 aria-busy=true",
          [b["pending"], b["tl"]], [True, "true"])
    check("N2.text", "載入中 → 標頭有可見的「載入中」字樣，且點名還沒到的波形",
          [b["pending"], b["shown"], "載入中" in b["text"], "波形" in b["text"]], [True, True, True, True])
    p.wait("typeof window.__vtStats === 'function'", 15)
    # 影片 metadata 可能比 init 晚到；等的是「影片本身就緒」這個環境條件，不是等受測的字樣收起
    p.wait(VIDEO_READY, 15)
    time.sleep(0.2)
    b = p.js(LOAD_BUSY)
    check("N2.done", "載入完成 → 三個區塊都沒有 aria-busy、「載入中」字樣收起",
          [b["stage"], b["tl"], b["list"], b["shown"], b["text"]], [None, None, None, False, ""])
    p.ctl("wave")
    p.goto("")
    p.wait(VIDEO_READY, 15)
    time.sleep(0.2)
    b = p.js(LOAD_BUSY)
    note = p.js(READ_NOTE)
    check("N2.fail", "波形載入失敗 → 不留 aria-busy／「載入中」，改由載入失敗清單說明",
          [b["tl"], b["shown"], "wave" in json.dumps(note)], [None, False, True])

    # ── N19：字幕清單的 aria-busy（載入中為 true、載完解除；兩個時點都取樣）──
    p.ctl("", "subs:1500")
    p.front()
    p.cdp.call("Page.navigate", {"url": "about:blank"})
    time.sleep(0.15)
    p.cdp.call("Page.navigate", {"url": "%s/video-edit-prototype.html" % p.base})
    p.wait("document.getElementById('vt-load-busy') && document.readyState !== 'loading'", 8)
    time.sleep(0.5)
    p.note_vis("N19 載入中")
    b = p.js(LOAD_BUSY)
    check("N19.busy", "載入中（字幕慢 1.5 秒、init 未完成）→ 字幕清單 aria-busy=true，字樣點名字幕",
          [b["pending"], b["list"], "字幕" in b["text"]], [True, "true", True])
    p.wait("typeof window.__vtStats === 'function'", 15)
    p.wait(VIDEO_READY, 15)
    time.sleep(0.2)
    b = p.js(LOAD_BUSY)
    check("N19.done", "字幕載完 → 字幕清單沒有 aria-busy，且清單真的有字幕列",
          [b["pending"], b["list"], p.js("document.querySelectorAll('#vt-line-list .vt-line').length > 0")],
          [False, None, True])

    # ── N8：時間軸標頭的說明圖示與「時間軸」文字垂直置中（中心差門檻，不釘像素）──
    p.ctl()
    p.goto("?demo")
    m = p.js("""(() => { const t = document.querySelector('.vt-tl-title');
      const rg = document.createRange(); rg.selectNodeContents(t);
      const a = rg.getBoundingClientRect(), k = document.getElementById('vt-keys-toggle').getBoundingClientRect();
      return {d: Math.abs((a.top + a.height / 2) - (k.top + k.height / 2)), th: a.height, kh: k.height}; })()""")
    check("N8.mid", "「時間軸」文字與說明圖示鈕的垂直中心差 ≤ 1px（兩者都有高度）",
          [m["d"] <= 1, m["th"] > 0, m["kh"] > 0], [True, True, True])

    # ── N6：逐句工具「併」用合併圖示，不再借 chevron-up ──
    row = p.center("#vt-line-list .vt-line:nth-child(2)")
    p.mouse("mouseMoved", row["x"], row["y"])
    time.sleep(0.25)
    g = p.js("""(() => {
      const ds = (root) => Array.from(root.querySelectorAll('path')).map((x) => x.getAttribute('d'));
      // 圖示庫沒載到時回空陣列（斷言照紅），不讓探針拋例外把整支走查中斷
      const of = (name) => { const d = document.createElement('div');
        d.innerHTML = window.Icons ? window.Icons.get(name, {size: 14}) : ''; return ds(d); };
      const b = document.querySelector('#vt-line-list .vt-line:nth-child(2) .vt-line-tool[data-act=merge]');
      const got = ds(b), mg = of('merge'), up = of('chevron-up');
      return {n: got.length, isMerge: mg.length > 0 && JSON.stringify(got) === JSON.stringify(mg),
              isUp: JSON.stringify(got) === JSON.stringify(up),
              vb: (b.querySelector('svg') || {getAttribute: () => null}).getAttribute('viewBox')}; })()""")
    check("N6.icon", "「併」鈕的圖示＝icons.js 的 merge（3 段線、24 格 viewBox），不是 chevron-up",
          [g["n"], g["isMerge"], g["isUp"], g["vb"]], [3, True, False, "0 0 24 24"])

    # ── N7：剪除段時長標籤的 ✕ 改成圖示 ──
    p.js("window.__vtAddCut(2, 5)")
    time.sleep(0.2)
    c = p.js("""(() => { const l = document.querySelector('.vt-cut .vt-cut-label');
      return [l.querySelectorAll('[data-icon="x"] svg').length, l.textContent.trim(),
              /[✕✖×]/.test(l.textContent)]; })()""")
    check("N7.label", "剪除段標籤＝1 個 x 圖示＋「3.0s」，文字裡沒有 ✕ 字符", c, [1, "3.0s", False])
    p.js("window.__vt.cuts.length = 0")

    # ── N12：焦點在按鈕上按空白鍵，只觸發該按鈕 ──
    p.goto("?demo")
    p.wait("document.getElementById('vt-video').readyState >= 1", 15)
    p.js("document.getElementById('vt-keys-toggle').focus()")
    space_key(p)
    s = p.js("""[document.activeElement.id, document.getElementById('vt-keys-pop').hidden,
                 document.getElementById('vt-video').paused]""")
    check("N12.btn", "焦點在說明鈕按空白 → 說明打開，影片沒有跟著播放",
          s, ["vt-keys-toggle", False, True])
    p.js("document.activeElement.blur()")
    space_key(p)
    played = p.wait("!document.getElementById('vt-video').paused", 3)
    check("N12.body", "焦點不在控制項上按空白 → 照舊播放（快捷鍵沒被關掉）", played, True)
    p.js("document.getElementById('vt-video').pause()")

    # ── N18／N20：其他按鈕、summary、文字框、number／select／color 上按空白鍵 ──
    # 真集模式才有對齊面板的 number 欄。每一項都取：焦點還在不在、值、影片是否暫停、
    # 以及空白鍵的 keydown 有沒有被 preventDefault（被擋＝原生行為被吃掉）。
    p.goto("")
    p.wait(VIDEO_READY, 15)
    p.js("""(() => { window.__sp = []; window.addEventListener('keydown', (e) => {
      if (e.key === ' ') window.__sp.push(e.defaultPrevented); }); })()""")

    def space_on(sel):
        p.js("document.getElementById('vt-video').pause(); window.__sp = [];")
        probe = """(() => { const e = document.querySelector(%s);
          if (%%s) e.focus();
          return {focus: document.activeElement === e, value: e.value === undefined ? null : e.value,
                  paused: document.getElementById('vt-video').paused, prevented: window.__sp.slice()}; })()""" % json.dumps(sel)
        before = p.js(probe % "true")
        space_key(p)
        return before, p.js(probe % "false")

    b0, a = space_on("#vt-play")
    check("N18.play", "焦點在播放鈕按空白 → 只觸發這顆鈕一次（開始播放），不會被快捷鍵再切回暫停",
          [b0["focus"], a["focus"], a["paused"], a["prevented"]], [True, True, False, [False]])
    z0 = p.js("document.getElementById('vt-tracks').style.width")
    b0, a = space_on("#vt-zoom-in")
    z1 = p.js("document.getElementById('vt-tracks').style.width")
    check("N18.zoom", "焦點在放大鈕按空白 → 時間軸放大，影片沒有跟著播放",
          [b0["focus"], z1 != z0, a["paused"], a["prevented"]], [True, True, True, [False]])
    o0 = p.js("document.getElementById('vt-adv').open")
    b0, a = space_on("#vt-adv summary")
    check("N18.summary", "焦點在「進階」summary 按空白 → 展開，影片沒有跟著播放",
          [b0["focus"], o0, p.js("document.getElementById('vt-adv').open"), a["paused"], a["prevented"]],
          [True, False, True, True, [False]])
    # 字幕文字框自己會攔住 keydown 不往上冒（window 收不到），所以這一項不看 prevented，
    # 改用「真的多打進一個空白」當原生行為沒被吃掉的證據
    b0, a = space_on("#vt-line-list input[type=text]")
    check("N18.text", "焦點在字幕文字框按空白 → 打進一個空白字元，影片沒有跟著播放",
          [b0["focus"], a["focus"], len(a["value"]) - len(b0["value"]),
           a["value"].replace(" ", "") == b0["value"].replace(" ", ""), a["paused"]],
          [True, True, 1, True, True])
    t = p.center("#vt-al-toggle")
    p.click(t["x"], t["y"])
    b0, a = space_on("#vt-al-head")
    check("N20.num", "焦點在 number 欄按空白 → 值不變、焦點還在、不播放、按鍵沒被擋",
          [b0["focus"], a["focus"], b0["value"] != "", a["value"] == b0["value"], a["paused"], a["prevented"]],
          [True, True, True, True, True, [False]])
    # select／color 的原生行為是彈出選單／選色器（彈出後會吃掉後續按鍵），所以排在最後，
    # 各自測完用滑鼠點一下標題列把它關掉
    for cid, sel, what in (("N20.select", "#st-font", "下拉選單"), ("N20.color", "#st-primary", "選色欄")):
        b0, a = space_on(sel)
        check(cid, "焦點在%s按空白 → 值不變、焦點還在、不播放、按鍵沒被擋" % what,
              [b0["focus"], a["focus"], b0["value"] != "", a["value"] == b0["value"], a["paused"], a["prevented"]],
              [True, True, True, True, True, [False]])
        tt = p.center(".vt-tl-title")
        p.click(tt["x"], tt["y"])
        p.js("document.activeElement && document.activeElement.blur()")
    p.js("document.getElementById('vt-video').pause(); window.__sp = [];")
    space_key(p)
    check("N20.after", "關掉選單／選色器後、焦點不在控制項上按空白 → 快捷鍵照常播放（鍵盤沒被卡住）",
          p.wait("!document.getElementById('vt-video').paused", 3), True)
    p.js("document.getElementById('vt-video').pause()")

    # ── N5：標題卡上的刪除鈕 ──
    p.goto("?demo")
    p.js("window.__vtDropTpl('big', 10)")
    time.sleep(0.2)
    d = p.js(CARD_PARTS)
    check("N5.hit", "卡上有刪除鈕：中心最上層是它、有可及性名稱、內含 1 個 x 圖示且無字",
          [d["dels"], d["delHit"], len(d["name"]) > 0, d["icon"]], [1, True, True, [1, ""]])
    h = p.center("#vt-card-track .vt-card-h.is-r")
    e0 = p.js("window.__vtCards()[0].end")
    p.drag(h["x"], h["y"], h["x"] + 60, h["y"])
    e1 = p.js("window.__vtCards()[0].end")
    check("N5.handle", "有刪除鈕後右把手仍在最上層、往右拖出點變大、卡沒被刪",
          [d["rHit"], d["lHit"], e1 > e0, p.js("window.__vtCards().length")], [True, True, True, 1])
    bt = p.center("#vt-card-track .vt-card-del")
    p.click(bt["x"], bt["y"])
    check("N5.click", "真滑鼠點刪除鈕（命中）→ 卡被刪（0 張）、沒誤建剪除段",
          [bt["hitExact"], p.js("window.__vtCards().length"), p.js("window.__vtStats().cutCount")],
          [True, 0, 0])
    # 窄卡：鈕、兩個把手互不遮擋
    p.js("window.__vtDropTpl('big', 10)")
    time.sleep(0.2)
    w = narrow_card(p, 50)
    d = p.js(CARD_PARTS)
    bt = p.center("#vt-card-track .vt-card-del")
    if bt:
        p.click(bt["x"], bt["y"])
    check("N5.narrow", "窄卡（約 50px）→ 刪除鈕與兩個把手各自在最上層，真滑鼠點鈕可刪",
          [44 <= w <= 56, d["delHit"], d["lHit"], d["rHit"], p.js("window.__vtCards().length")],
          [True, True, True, True, 0])
    # 更窄的卡放不下鈕：不畫（不讓鈕蓋住把手），刪除走選卡＋⌫
    p.js("window.__vtDropTpl('big', 10)")
    time.sleep(0.2)
    w = narrow_card(p, 34)
    d = p.js(CARD_PARTS)
    c = p.center("#vt-card-track .vt-card")
    p.click(c["x"], c["y"])
    p.key("Backspace", "Backspace", 8)
    check("N5.tiny", "放不下鈕的卡（約 34px）→ 不畫刪除鈕、把手仍在最上層；選卡＋⌫ 仍可刪",
          [w < 44, d["dels"], d["rHit"], p.js("window.__vtCards().length")], [True, 0, True, 0])

    # ── N13：放不下鈕的窄卡，選取時在卡的外側畫刪除鈕 ──
    p.js("window.__vtDropTpl('big', 10)")
    time.sleep(0.2)
    w = narrow_card(p, 34)
    c = p.center("#vt-card-track .vt-card")
    p.click(c["x"], c["y"])
    d = p.js(CARD_PARTS)
    o = p.js(OUT_DEL)
    check("N13.show", "窄卡（約 34px）選取後 → 卡外側恰 1 顆刪除鈕（卡內 0 顆）、有可及性名稱與 x 圖示",
          [w < 44, o["outs"], o["inCard"], len(o["name"]) > 0, o["icon"]], [True, 1, 0, True, [1, ""]])
    check("N13.hit", "外側鈕中心最上層就是它、整顆在卡的外面且沒被軌道裁掉；兩支把手仍各自在最上層",
          [o["hit"], o["outside"], o["inTrack"], d["lHit"], d["rHit"]], [True, True, True, True, True])
    # 卡若在這一段被誤刪（例如鈕疊在把手上、拖把手變成點到鈕），後面就取不到把手／卡：
    # 取不到一律當成該條斷言失敗繼續跑，不能讓走查崩潰、連結果檔都寫不出來
    c0 = p.js("window.__vtCards()[0] || null")
    h = p.center("#vt-card-track .vt-card-h.is-r")
    if h:
        p.drag(h["x"], h["y"], h["x"] + 5, h["y"])
    h = p.center("#vt-card-track .vt-card-h.is-l")
    if h:
        p.drag(h["x"], h["y"], h["x"] - 4, h["y"])
    c1 = p.js("window.__vtCards()[0] || null")
    o = p.js(OUT_DEL)
    both = bool(c0) and bool(c1)
    check("N13.handle", "有外側鈕時兩支把手都拖得動（出點變大、進點變小）、卡沒被刪、鈕跟著卡重畫仍命中",
          [both and c1["end"] > c0["end"], both and c1["start"] < c0["start"],
           p.js("window.__vtCards().length"),
           o["outs"], o["hit"], o["outside"]],
          [True, True, 1, 1, True, True])
    p.js("window.__vtSelectCard(null)")
    time.sleep(0.2)
    n0 = p.js("document.querySelectorAll('#vt-card-track .vt-card-del').length")
    c = p.center("#vt-card-track .vt-card")
    if c:
        p.click(c["x"], c["y"])
    bt = p.center("#vt-card-track .vt-card-del.is-out")
    if bt:
        p.click(bt["x"], bt["y"])
    check("N13.click", "取消選取 → 外側鈕收掉（0 顆）；再選取後真滑鼠點外側鈕（命中）→ 卡被刪、沒誤建剪除段",
          [n0, bool(bt) and bt["hitExact"], p.js("window.__vtCards().length"), p.js("window.__vtStats().cutCount")],
          [0, True, 0, 0])
    # 卡貼著軌道尾端：右邊放不下，鈕改放左緣外側（不然會被軌道裁掉點不到）
    p.js("window.__vtDropTpl('big', 10)")
    time.sleep(0.2)
    narrow_card(p, 34)
    p.js("""(() => { const c = window.__vt.titleCards[0]; const d = c.end - c.start;
      c.start = window.__vtStats().duration - d; c.end = c.start + d;
      window.__vtSelectCard(c.id); })()""")
    time.sleep(0.2)
    o = p.js(OUT_DEL)
    bt = p.center("#vt-card-track .vt-card-del.is-out")
    if bt:
        p.click(bt["x"], bt["y"])
    check("N13.flip", "窄卡貼著軌道尾端 → 外側鈕改在卡的左邊、沒被裁掉且命中，真滑鼠點了可刪",
          [o["outs"], o["side"], o["inTrack"], o["hit"], p.js("window.__vtCards().length")],
          [1, "left", True, True, 0])

    # ── N22：外側鈕不得疊在鄰卡上（疊上去會蓋住鄰卡把手 → 想拖鄰卡卻把窄卡刪掉）──
    def adj(cards, sel):
        """重開 demo、照 [[左緣px, 寬px], …] 擺卡（由左到右），選取第 sel 張；回各卡 id。"""
        p.goto("?demo")
        for i in range(len(cards)):
            p.js("window.__vtDropTpl('big', %d)" % (2 + 6 * i))
        time.sleep(0.2)
        ids = p.js(ADJ_SETUP % (json.dumps(cards), sel))
        time.sleep(0.25)
        return ids

    def ids_now():
        return p.js("window.__vtCards().map((c) => c.id)")

    for gap in (0, 6):
        ids = adj([[300, 34], [334 + gap, 120]], 0)
        m = p.js(ADJ_PROBE % 0)
        h = m["cards"][1]["l"] if len(m["cards"]) > 1 else None
        s0 = p.js("window.__vtCards()[1] ? window.__vtCards()[1].start : null")
        if h:
            p.drag(h["x"], h["y"], h["x"] + 6, h["y"])
        s1 = p.js("window.__vtCards()[1] ? window.__vtCards()[1].start : null")
        check("N13.adj%d" % gap,
              "窄卡右邊緊鄰別張卡（間距 %dpx）→ 鄰卡左把手中心最上層是把手本身；按著拖 → 卡數不變、鄰卡進點變大" % gap,
              [m["w"] < 44, round(m["gapR"]), bool(h) and h["hit"], ids_now() == ids,
               s0 is not None and s1 is not None and s1 > s0],
              [True, gap, True, True, True])

    ids = adj([[300, 34], [334, 120]], 0)
    m = p.js(ADJ_PROBE % 0)
    bt = p.center("#vt-card-track .vt-card-del.is-out")
    if bt:
        p.click(bt["x"], bt["y"])
    check("N13.adjflip", "右邊被鄰卡擋住、左邊有空 → 外側鈕翻到左邊、不疊任何卡、沒被裁掉且命中；真滑鼠點了刪的是窄卡",
          [m["outs"], m["side"], m["overlap"], m["inTrack"], bool(bt) and bt["hitExact"], ids_now() == ids[1:]],
          [1, "left", 0, True, True, True])

    got = []
    for cards, sel in (([[174, 120], [300, 34], [340, 120]], 1), ([[0, 34], [34, 120]], 0)):
        ids = adj(cards, sel)
        m = p.js(ADJ_PROBE % sel)
        me = m["cards"][sel] if len(m["cards"]) > sel else None
        c = p.js("""(() => { const el = document.querySelectorAll('#vt-card-track .vt-card')[%d];
          if (!el) return null; const r = el.getBoundingClientRect();
          return {x: r.left + r.width / 2, y: r.top + r.height / 2}; })()""" % sel)
        if c:
            p.click(c["x"], c["y"])
        p.key("Backspace", "Backspace", 8)
        # 把手取不到（卡被誤刪／沒畫出來）一律記成 False，不能讓走查在這裡崩潰
        got.append([m["outs"], bool(me and me["l"]) and me["l"]["hit"], bool(me and me["r"]) and me["r"]["hit"],
                    ids_now() == [x for i, x in enumerate(ids) if i != sel]])
    check("N13.adjnone", "兩邊都放不下（左右各有鄰卡／貼軌道起點且右邊緊鄰）→ 外側鈕 0 顆、把手仍在最上層；選卡＋⌫ 刪的是窄卡",
          got, [[0, True, True, True], [0, True, True, True]])

    # 窄卡貼著軌道尾端（右邊出軌）、左邊又緊鄰別張卡 → 翻左會疊上左鄰卡，所以不畫
    tw = p.js("document.getElementById('vt-card-track').getBoundingClientRect().width")
    ids = adj([[tw - 154, 120], [tw - 34, 34]], 1)
    m = p.js(ADJ_PROBE % 1)
    nb = m["cards"][0] if m["cards"] else None
    c = p.js("""(() => { const el = document.querySelectorAll('#vt-card-track .vt-card')[1];
      if (!el) return null; const r = el.getBoundingClientRect();
      return {x: r.left + r.width / 2, y: r.top + r.height / 2}; })()""")
    if c:
        p.click(c["x"], c["y"])
    p.key("Backspace", "Backspace", 8)
    check("N13.adjend", "窄卡貼軌道尾端且左邊緊鄰別張卡 → 外側鈕 0 顆、左鄰卡右把手仍在最上層；選卡＋⌫ 刪的是窄卡",
          [m["w"] < 44, m["outs"], bool(nb and nb["r"]) and nb["r"]["hit"], ids_now() == (ids or [None])[:1]],
          [True, 0, True, True])

    # 外側鈕另一邊要與鄰卡留 2px：右鄰卡間距 19px（鈕 16＋兩側各 2＝20 放不下）→ 翻左；22px → 放右
    got = []
    for gap in (19, 22):
        adj([[300, 34], [334 + gap, 120]], 0)
        m = p.js(ADJ_PROBE % 0)
        got.append([round(m["gapR"]), m["outs"], m["side"], m["overlap"]])
    check("N13.gap", "右鄰卡間距 19px → 外側鈕翻到左邊；22px → 放右邊；兩種都不疊任何卡",
          got, [[19, 1, "left", 0], [22, 1, "right", 0]])

    # 選取的是別張卡時，窄卡不該冒出外側鈕（左右都有空位也一樣）
    ids = adj([[300, 34], [400, 120]], 1)
    m = p.js(ADJ_PROBE % 0)
    p.js("window.__vtSelectCard(%s)" % json.dumps(ids[0] if ids else None))
    time.sleep(0.2)
    m2 = p.js(ADJ_PROBE % 0)
    check("N13.other", "選取的是寬卡 → 窄卡沒有外側鈕（全軌只有寬卡卡內那 1 顆）；改選窄卡 → 外側鈕才出現",
          [m["w"] < 44, m["sel"], m["outs"], m["dels"], m2["sel"], m2["outs"]],
          [True, [False, True], 0, 1, [True, False], 1])

    # ── N14：存對齊成功、對齊也讀回來了，但字幕重載失敗 → 狀態列不能只說「已儲存」──
    p.reset()
    p.ctl()
    p.goto("")
    t = p.center("#vt-al-toggle")
    p.click(t["x"], t["y"])
    al_wait = """(() => { const c = document.getElementById('vt-al-status').classList;
      return c.contains('is-ok') || c.contains('is-err'); })()"""

    def save_head(val):
        p.js("""(() => { const el = document.getElementById('vt-al-head'); el.value = '%s';
          el.dispatchEvent(new Event('input', {bubbles: true}));
          el.dispatchEvent(new Event('change', {bubbles: true})); })()""" % val)
        sb = p.center("#vt-al-save")
        p.click(sb["x"], sb["y"])
        p.wait(al_wait, 8)
        time.sleep(0.2)
        return sb["hit"]

    p.ctl("subs")
    s0 = p.stat()["saves"]
    hit = save_head("2.5")
    d = p.js(AL_DOM)
    n = p.js(READ_NOTE)
    check("N14.msg", "寫入成功但字幕重載失敗 → 狀態列是錯誤樣式的完整訊息（點名字幕），不是「已儲存」",
          [hit, p.stat()["saves"] - s0, d["statusText"] == "已儲存", "已寫入" in d["statusText"],
           "字幕" in d["statusText"], d["statusErr"], d["statusVisible"]],
          [True, 1, False, True, True, True, True])
    check("N14.list", "同一時間載入失敗清單照舊列出字幕一項、對齊標頭不說載入失敗（對齊有讀回來）",
          [n["keys"], d["note"] == "載入失敗"], [["subs"], False])
    p.ctl()
    save_head("2.75")
    d = p.js(AL_DOM)
    n = p.js(READ_NOTE)
    check("N14.ok", "排除故障後再存一次 → 狀態列回到「已儲存」（非錯誤樣式）、清單清空",
          [d["statusText"], d["statusErr"], n["keys"]], ["已儲存", False, []])
    # 重讀這一集也失敗時，要講的是「重新讀取失敗」（N9），不是只點名字幕 —— 畫面上的值也不可信
    p.ctl("align,subs")
    s0 = p.stat()["saves"]
    save_head("3")
    d = p.js(AL_DOM)
    check("N14.both", "寫入成功、但重讀這一集與字幕都失敗 → 狀態列說的是重新讀取失敗（不是只說字幕），錯誤樣式",
          [p.stat()["saves"] - s0, "已寫入" in d["statusText"], "重新讀取失敗" in d["statusText"],
           "字幕重新載入失敗" in d["statusText"], d["statusErr"], d["note"]],
          [1, True, True, False, True, "載入失敗"])

    # ── N9：存對齊成功、但重讀這一集失敗 → 單一明確訊息 ──
    p.reset()
    p.ctl()
    p.goto("")
    t = p.center("#vt-al-toggle")
    p.click(t["x"], t["y"])
    p.js("""(() => { const el = document.getElementById('vt-al-head'); el.value = '2.25';
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true})); })()""")
    p.ctl("align")
    s0 = p.stat()["saves"]
    sb = p.center("#vt-al-save")
    p.click(sb["x"], sb["y"])
    p.wait("""(() => { const c = document.getElementById('vt-al-status').classList;
      return c.contains('is-ok') || c.contains('is-err'); })()""", 8)
    time.sleep(0.2)
    d = p.js(AL_DOM)
    st = p.stat()
    check("N9.msg", "寫入成功但重讀失敗 → 狀態列不說「已儲存」，改成一則錯誤樣式的明確訊息",
          [st["saves"] - s0, "已儲存" in d["statusText"], "已寫入" in d["statusText"],
           "重新整理" in d["statusText"], d["statusErr"], d["statusVisible"]],
          [1, False, True, True, True, True])
    check("N9.note", "同一時間標頭註記＝載入失敗（兩處說法一致，不互相矛盾）", d["note"], "載入失敗")
    p.ctl()
    p.goto("")
    d = p.js(AL_DOM)
    check("N9.rt", "排除故障後重載頁面 → 讀回剛才寫入的值（訊息沒騙人：確實已寫入）",
          [st["episode"]["head_trim_sec"], [float(v) for v in d["values"] if v != ""]],
          [2.25, [0.25, 2.25, 0.75, 0.0]])

    # ── N11：有外接音檔的集 → 「聲音偏移」欄顯示、可編輯、存檔 round-trip ──
    p.reset(audio=True)
    p.goto("")
    t = p.center("#vt-al-toggle")
    p.click(t["x"], t["y"])
    d = p.js(AL_DOM)
    check("N11.show", "有外接音檔 → 聲音偏移欄可用、值＝該集的 0.4、標頭註記＝有外接音檔",
          [d["audioDisabled"], d["audioValue"] != "" and float(d["audioValue"]), d["note"]],
          [False, 0.4, "有外接音檔"])
    p.js("""(() => { const el = document.getElementById('vt-al-audio'); el.value = '0.65';
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true})); })()""")
    s0 = p.stat()["saves"]
    sb = p.center("#vt-al-save")
    p.click(sb["x"], sb["y"])
    p.wait("document.getElementById('vt-al-status').classList.contains('is-ok')", 8)
    st = p.stat()
    check("N11.save", "改成 0.65 後真滑鼠點儲存 → 恰 1 次寫入、伺服器端 audio＝原路徑＋0.65、其餘鍵不變",
          [sb["hit"], st["saves"] - s0, st["episode"]["audio"], st["episode"]["head_trim_sec"]],
          [True, 1, {"path": "ext-audio.wav", "sync_offset": 0.65}, 1.5])
    p.goto("")
    d = p.js(AL_DOM)
    check("N11.rt", "重載頁面 → 聲音偏移讀回 0.65（round-trip）",
          [d["audioDisabled"], d["audioValue"] != "" and float(d["audioValue"])], [False, 0.65])
    p.reset()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base")
    ap.add_argument("--json")
    a = ap.parse_args()

    srv = None
    base = a.base
    if not base:
        srv = subprocess.Popen([sys.executable, "-u", os.path.join(HERE, "serve.py")],
                               stdout=subprocess.PIPE)
        base = "http://127.0.0.1:%s" % srv.stdout.readline().decode().strip().split("=")[1]
    profile = tempfile.mkdtemp(prefix="vt-ux2-profile-", dir="/private/tmp")
    proc = cdp = None
    try:
        port = D.free_port()
        proc = D.launch_chrome(D.DEFAULT_CHROME, "about:blank", 1280, 900, port, profile)
        D.wait_devtools(port)
        cdp = D.CDP(D.get_page_ws(port), timeout=30)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Network.enable")
        cdp.call("Network.setCacheDisabled", {"cacheDisabled": True})
        p = Page(cdp, base)
        p.reset()  # 假集還原成預設值（上一輪突變可能寫過）
        run_9(p)
        run_6(p)
        run_14(p)
        run_12(p, 900, "h900")
        run_12(p, 760, "h760")
        run_align(p)
        run_scroll(p, "?demo", 760, "d760")
        run_scroll(p, "?demo", 900, "d900")
        run_scroll(p, "", 760, "r760")
        run_scroll(p, "", 900, "r900")
        run_n1(p)
        run_b4(p)
        run_13(p)
    finally:
        if cdp:
            cdp.close()
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        if srv:
            srv.terminate()
        subprocess.run(["trash", profile], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # N17：整支走查每一次導頁後，分頁都要是 visible（不是的那幾次列出來）
    check("N17.vis", "每次導頁後分頁都是 visible（共取樣 %d 次）" % len(VIS),
          [len(VIS) > 0, [w for w, v in VIS if v != "visible"]], [True, []])
    red = [c for c in ORDER if not RESULTS[c]]
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(RESULTS, f, ensure_ascii=False)
    print("\n===== 斷言 %d／%d 通過%s =====" % (
        len(ORDER) - len(red), len(ORDER), ("；紅：" + ", ".join(red)) if red else ""))
    sys.exit(1 if red else 0)


if __name__ == "__main__":
    main()
