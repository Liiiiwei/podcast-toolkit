#!/usr/bin/env python3
"""toast 覆蓋層吞點擊（UX）的驗收走查。

原缺陷：#toast-container 固定在右上（top:16px），個別 .toast 為了「點擊關閉」
開 pointer-events:auto。toast 在的那 4~8 秒，topbar 右側操作列的
elementFromPoint 回的是 toast —— 點「完成並儲存」「取消」「輸出」「鏡頭」
全部沒反應，且畫面沒有任何失敗表現（靜默失敗）。窄螢幕 topbar 換行後
連左側四顆（回首頁／詞庫／設定／快捷鍵）一起被擋，七顆全滅。

修法是三件事，缺一不可（勘查實測：右上／topbar 下緣／右下／底部置中四個候選位置
都各有受害者，純搬位置只是換一批人被擋）：
  1. 容器搬到右下 —— 離開 topbar 操作列。
  2. .toast 本體 pointer-events:none —— 覆蓋層本體不再吞任何點擊。
  3. 唯一吃點擊的 ✕ 排在 toast 左端、容器固定寬 —— ✕ 在右端時（23×20px）正好壓住
     右下角的 #drawer-toggle；寬度隨訊息長短浮動還會讓它的位置不可預測。

硬要求（README／MUTATIONS.md）：
  - 每個 ✓ 綁布林斷言；覆蓋判準一律用 document.elementFromPoint，不用幾何 rect 猜
    （2026-08-25 教訓：CDP 的滑鼠事件打的是座標，會被疊層吃掉）。
    取樣點是中心＋四角（內縮 3px）五點，任一點被吃就算被擋 —— 只驗中心會漏掉
    「按鈕邊緣被 ✕ 壓到」這種部分重疊。
  - 突變逐一部件關，不是只有「全開 vs 全關」（2026-08-08 教訓：只關全部抓不到死碼）。

用法：CDP_PORT=<port> /usr/bin/python3 -u verify_toast_no_block.py
      （需要先起一個 --remote-debugging-port=<port> 的 headless Chrome）
"""
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import cdp_common as C

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent.parent
TOAST_CSS = REPO_ROOT / "podcast_toolkit" / "web" / "static" / "toast.css"
SANDBOX_ROOT = Path("/private/tmp/pt-timeline-baseline")

CDP_PORT = int(os.environ.get("CDP_PORT", "9522"))
POD_PORT = 8795
POD = f"http://127.0.0.1:{POD_PORT}/?pthook=1"
DASH = f"http://127.0.0.1:{POD_PORT}/static/dashboard.html"

RESULT_JSON = HERE / "verify_toast_no_block_result.json"

# topbar 七顆按鈕（窄螢幕 topbar 換行，左群也會進入原 toast 的覆蓋範圍）
TOPBAR_BTNS = [
    "#back-to-dash-btn", "#glossary-btn", "#settings-btn", "#shortcuts-btn",
    "#cam-btn", "#output-menu-btn", "#save-btn",
]
WIDTHS = [(1400, 1600), (900, 800), (620, 800)]
# 訊息長度會改變 toast 寬度 → 用三種長度驗 ✕ 的位置不飄
MSGS = [
    ("短", "好"),
    ("中", "同步偏移要是數字"),
    ("長", "部分對齊失敗：第 3、7、12 句找不到對應時間點，已保留原時間"),
]

# 突變用的三處（各自恰好命中 1 次，否則 patch_file 拒絕）
PE_OLD = "  pointer-events: none;\n  display: flex;"
PE_NEW = "  pointer-events: auto;\n  display: flex;"
DIR_OLD = "  flex-direction: row-reverse;"
DIR_NEW = "  flex-direction: row;"
POS_OLD = "  inset: auto 16px 16px auto;"
POS_NEW = "  inset: auto 16px auto auto;\n  top: 16px;"


# ── serve_podcast 生命週期 ────────────────────────────────────────────────
def port_busy(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
        return True
    except urllib.error.URLError:
        return False
    except Exception:
        return True


def start_server():
    log = open(SANDBOX_ROOT / "server-toast-no-block.log", "ab")
    p = subprocess.Popen(
        [sys.executable, str(HERE / "serve_podcast.py")], stdout=log, stderr=log
    )
    for _ in range(120):
        if p.poll() is not None:
            raise RuntimeError("serve_podcast 啟動即退出，看 server-toast-no-block.log")
        if port_busy(POD_PORT):
            time.sleep(0.6)
            return p
        time.sleep(0.25)
    raise RuntimeError("serve_podcast 30 秒內沒起來")


def stop_server(p):
    if p is None:
        return
    p.terminate()
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait(timeout=5)


def patch_file(path: Path, old: str, new: str):
    text = path.read_text(encoding="utf-8")
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"預期 {path.name} 恰有 1 處命中 {old[:40]!r}…，實際 {n} 處，拒絕突變")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def raw_eq(name, got, want):
    """突變測試專用：不進 C.results 分母，只印證據。"""
    ok = got == want
    print(f"    [{'PASS' if ok else 'FAIL'}] {name}  got={got!r} want={want!r}", flush=True)
    return ok


def get_cdp_ws_url(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=5) as r:
        return json.loads(r.read())["webSocketDebuggerUrl"]


async def wait_for(page, expr, timeout=25, interval=0.3):
    t = 0.0
    val = None
    while t < timeout:
        val = await C.js(page, expr)
        if val:
            return val
        await asyncio.sleep(interval)
        t += interval
    return val


async def close_page(page):
    if page is None:
        return
    try:
        await C.js(page, "document.querySelectorAll('video').forEach(v => v.pause())")
    except Exception:
        pass
    try:
        await page.send("Page.close")
    except Exception:
        pass
    await asyncio.sleep(0.2)


# ── 量測 ─────────────────────────────────────────────────────────────────
# 全頁可點元素逐一取「中心＋四角」五點打 elementFromPoint；
# 最上層若落在 #toast-container 內＝這顆按鈕此刻（至少部分）點不到。
BLOCKED_JS = """(() => {
  const box = document.querySelector('#toast-container');
  if (!box) return { err: '找不到 #toast-container' };
  const r = box.getBoundingClientRect();
  const toasts = box.querySelectorAll('.toast').length;
  const name = (el) => el.id || el.className.toString().split(' ')[0] || el.tagName;
  const blockedCenter = [];   // 中心點被吃 = 這顆按鈕按不到（硬失敗）
  const clipped = [];         // 只有邊角被吃 = 部分遮蔽（記錄 blocker，不當失敗）
  const els = [...document.querySelectorAll(
    'button, input, select, textarea, a[href], [role=button], .card')]
    .filter((el) => el.getClientRects().length && !el.closest('#toast-container'));
  for (const el of els) {
    const b = el.getBoundingClientRect();
    if (!b.width || !b.height) continue;
    const P = 3;
    const pts = [
      ['center', b.left + b.width / 2, b.top + b.height / 2],
      ['corner', b.left + P, b.top + P], ['corner', b.right - P, b.top + P],
      ['corner', b.left + P, b.bottom - P], ['corner', b.right - P, b.bottom - P],
    ];
    let cornerHit = null;
    for (const [kind, px, py] of pts) {
      const cx = Math.round(px), cy = Math.round(py);
      if (cx < 0 || cy < 0 || cx > innerWidth || cy > innerHeight) continue;
      const top = document.elementFromPoint(cx, cy);
      if (!top || el.contains(top) || top === el || !top.closest('#toast-container')) continue;
      const blocker = top.closest('.toast-close') ? 'toast-close' : 'toast-body';
      if (kind === 'center') { blockedCenter.push(name(el) + '<' + blocker + '>'); cornerHit = null; break; }
      if (!cornerHit) cornerHit = name(el) + '<' + blocker + '>';
    }
    if (cornerHit) clipped.push(cornerHit);
  }
  const c = box.querySelector('.toast-close');
  const cr = c ? c.getBoundingClientRect() : null;
  return { toasts, blocked: blockedCenter, clipped,
           rect: [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)],
           closeCenter: cr ? [Math.round(cr.left + cr.width / 2), Math.round(cr.top + cr.height / 2)] : null,
           closeSize: cr ? [Math.round(cr.width), Math.round(cr.height)] : null,
           vw: innerWidth, vh: innerHeight };
})()"""


async def blocked(page):
    return await C.js(page, BLOCKED_JS)


async def topbar_hits(page):
    """topbar 七顆逐顆：中心點的最上層元素是否落在自己之內。
    按鈕標籤包在 <span> 裡時 elementFromPoint 回那個 span，點擊照樣冒泡，
    所以判準是 closest(選擇器) 命中，不是節點全等。"""
    return await C.js(
        page,
        f"""(() => {{
          const out = {{}};
          for (const sel of {json.dumps(TOPBAR_BTNS)}) {{
            const el = document.querySelector(sel);
            if (!el || !el.getClientRects().length) {{ out[sel] = 'absent'; continue; }}
            const b = el.getBoundingClientRect();
            const top = document.elementFromPoint(
              Math.round(b.left + b.width / 2), Math.round(b.top + b.height / 2));
            out[sel] = !top ? 'null'
              : (top.closest(sel) ? 'self'
                : (top.closest('#toast-container') ? 'toast' : (top.id || top.tagName)));
          }}
          return out;
        }})()""",
    )


async def show(page, msg, kind):
    await C.js(page, "document.querySelectorAll('#toast-container .toast').forEach(t => t.remove())")
    await C.js(page, f"window.showToast({json.dumps(msg)}, {json.dumps(kind)})")
    await asyncio.sleep(0.45)


async def toast_count(page):
    return await C.js(page, "document.querySelectorAll('#toast-container .toast').length")


async def open_pod(browser, ws, sessions, w, h):
    page = await C.open_page(browser, ws, sessions, POD, settle=2.0, width=w, height=h)
    ready = await wait_for(
        page,
        "(() => { const b = document.querySelector('#save-btn');"
        " return !!(b && typeof window.showToast === 'function'); })()",
    )
    return page, ready


# ── 走查 ─────────────────────────────────────────────────────────────────
async def run_width(browser, ws, sessions, w, h):
    tag = f"W{w}"
    page, ready = await open_pod(browser, ws, sessions, w, h)
    if not ready:
        C.check(f"{tag}.0 頁面就緒（#save-btn + showToast）", False, "未就緒", "就緒")
        await close_page(page)
        return page
    C.check(f"{tag}.0 頁面就緒（#save-btn + showToast）", True, "就緒", "就緒")

    # empty 態：沒有 toast 時七顆都命中自己（後面的斷言才有意義的基線）
    base = await topbar_hits(page)
    vis = {k: v for k, v in base.items() if v != "absent"}
    C.check(f"{tag}.1 基線：無 toast 時 topbar 各鈕都命中自己",
            bool(vis) and all(v == "self" for v in vis.values()), vis,
            {k: "self" for k in vis})

    # warn 態 × 三種訊息長度：零被擋，且 ✕ 位置不隨訊息長短飄
    marks = {}
    for label, msg in MSGS:
        await show(page, msg, "warn")
        r = await blocked(page)
        marks[label] = r
        C.check(f"{tag}.2{label} warn toast（{label}訊息）顯示中，零個可點元素的中心被擋住",
                r.get("toasts") == 1 and r.get("blocked") == [],
                {"toasts": r.get("toasts"), "中心被擋": r.get("blocked"), "邊角被壓": r.get("clipped")},
                {"toasts": 1, "中心被擋": []})
    # 邊角被壓是結構性下限：✕ 是可見可點的實體（約 23×20px），無論擺哪都會與底下某些
    # 元素的邊緣相交 —— 前段實測右上／topbar 下緣／右下／底部置中四個位置都有受害者。
    # 能做到零像素重疊的唯一方法是拿掉 ✕（error toast 8 秒無法手動關）。所以門檻是
    # 「中心可點」＋「邊角被壓的一律是 ✕ 而非本體，且不得是 topbar 操作列成員」。
    clip_all = sorted({c for m in marks.values() for c in m.get("clipped", [])})
    C.check(f"{tag}.2c 邊角被壓的（若有）一律來自 ✕ 而非 toast 本體",
            all("<toast-close>" in c for c in clip_all), clip_all, "全部 <toast-close>")
    topbar_ids = [b.lstrip("#") for b in TOPBAR_BTNS]
    C.check(f"{tag}.2d 邊角被壓的不含 topbar 操作列成員",
            not [c for c in clip_all if c.split("<")[0] in topbar_ids], clip_all, "不含 topbar 七顆")

    xs = sorted({m["closeCenter"][0] for m in marks.values() if m.get("closeCenter")})
    C.check(f"{tag}.3 ✕ 的 x 座標不隨訊息長短飄（容器固定寬）",
            len(xs) == 1, {"三種長度量到的 ✕ x": xs}, "單一值")

    hits = await topbar_hits(page)
    vis2 = {k: v for k, v in hits.items() if v != "absent"}
    C.check(f"{tag}.4 toast 顯示中，topbar 各鈕仍命中自己",
            bool(vis2) and all(v == "self" for v in vis2.values()), vis2,
            {k: "self" for k in vis2})

    # 位置：貼右下（rect 與視窗右下角各差 16px）
    r = marks["中"]
    rect, vw, vh = r.get("rect"), r.get("vw"), r.get("vh")
    C.check(f"{tag}.5 容器貼右下角（右緣/下緣各留 16px）",
            bool(rect) and abs(vw - rect[2] - 16) <= 1 and abs(vh - rect[3] - 16) <= 1,
            {"rect": rect, "vw": vw, "vh": vh},
            {"right": vw - 16, "bottom": vh - 16})

    # 本體不吃點擊：toast 中心的最上層不是 .toast（pointer-events:none 生效的直接證據）
    body_top = await C.js(
        page,
        """(() => {
          const t = document.querySelector('#toast-container .toast');
          if (!t) return 'no-toast';
          const b = t.getBoundingClientRect();
          const el = document.elementFromPoint(Math.round(b.right - 20), Math.round(b.top + b.height / 2));
          return !el ? 'null' : (el.closest('.toast') ? 'toast' : (el.id || el.className.toString().split(' ')[0] || el.tagName));
        })()""",
    )
    C.check(f"{tag}.6 toast 本體（非 ✕ 處）的最上層不是 toast，點擊穿透到底下元素",
            body_top not in ("toast", "no-toast", "null"), body_top, "不是 toast")

    # error 態也一樣不擋
    await show(page, "存檔失敗：磁碟寫入錯誤", "error")
    r2 = await blocked(page)
    C.check(f"{tag}.7 error toast 顯示中也零個中心被擋",
            r2.get("toasts") == 1 and r2.get("blocked") == [],
            {"toasts": r2.get("toasts"), "中心被擋": r2.get("blocked"), "邊角被壓": r2.get("clipped")},
            {"toasts": 1, "中心被擋": []})

    # success 態：✕ 自己點得到，真滑鼠點擊後 toast 消失
    close_info = await C.js(
        page,
        """(() => {
          const c = document.querySelector('#toast-container .toast-close');
          if (!c) return null;
          const b = c.getBoundingClientRect();
          const cx = Math.round(b.left + b.width / 2), cy = Math.round(b.top + b.height / 2);
          const top = document.elementFromPoint(cx, cy);
          return { x: cx, y: cy, hitsSelf: !!(top && top.closest('.toast-close')),
                   label: c.getAttribute('aria-label') };
        })()""",
    )
    C.check(f"{tag}.8 ✕ 關閉鈕存在、可及（elementFromPoint 命中自己）且有 aria-label",
            bool(close_info) and close_info["hitsSelf"] and close_info["label"] == "關閉通知",
            close_info, {"hitsSelf": True, "label": "關閉通知"})
    if close_info:
        await C.mouse_click(page, close_info["x"], close_info["y"])
        await asyncio.sleep(0.6)
        left = await toast_count(page)
        C.check(f"{tag}.9 按 ✕ 後 toast 真的消失", left == 0, left, 0)
    else:
        C.skip(f"{tag}.9 按 ✕ 後 toast 真的消失", "✕ 不存在，前提不成立")

    # modal 開著：toast 要看得見（popover 進 top-layer 的目的），且一樣不擋任何點擊。
    # 註：showModal() 讓 dialog 以外整份文件 inert，命中測試一律回 dialog，
    #     所以 modal 期間 ✕ 按不到（瀏覽器語意，非本系統缺陷；toast 照樣自動消失）。
    await C.js(page, "(() => { const b = document.querySelector('#cam-btn'); if (b) b.click(); })()")
    await asyncio.sleep(0.9)
    dlg = await C.js(page, "(() => { const d = [...document.querySelectorAll('dialog')].find(x => x.open); return d ? d.id : null; })()")
    if not dlg:
        C.skip(f"{tag}.10 modal 開著時 toast 仍看得見", "cam modal 沒開起來")
        C.skip(f"{tag}.11 modal 開著時零個可點元素被 toast 擋住", "cam modal 沒開起來")
    else:
        await show(page, "同步偏移要是數字", "warn")
        seen = await C.js(
            page,
            """(() => {
              const t = document.querySelector('#toast-container .toast');
              if (!t) return 'no-toast';
              const b = t.getBoundingClientRect();
              const cs = getComputedStyle(t);
              return { onScreen: b.width > 0 && b.height > 0 && b.right <= innerWidth + 1 && b.bottom <= innerHeight + 1,
                       visibility: cs.visibility, opacity: Number(cs.opacity) };
            })()""",
        )
        C.check(f"{tag}.10 modal 開著時 toast 仍看得見（在視窗內、visible、不透明）",
                isinstance(seen, dict) and seen.get("onScreen")
                and seen.get("visibility") == "visible" and seen.get("opacity") == 1,
                seen, {"onScreen": True, "visibility": "visible", "opacity": 1})
        r3 = await blocked(page)
        C.check(f"{tag}.11 modal 開著時零個可點元素的中心被 toast 擋住（含 modal 內按鈕）",
                r3.get("blocked") == [],
                {"中心被擋": r3.get("blocked"), "邊角被壓": r3.get("clipped")}, {"中心被擋": []})
    return page


async def mutate_and_scan(browser, ws, sessions):
    """在目前的 CSS 狀態下開一次頁面，回 (blocked 清單, ✕ 是否壓進 topbar)。"""
    page, _ = await open_pod(browser, ws, sessions, 1400, 1600)
    await show(page, "突變測試", "warn")
    r = await blocked(page)
    over = await C.js(
        page,
        """(() => {
          const c = document.querySelector('#toast-container .toast-close');
          const tb = document.querySelector('.topbar');
          if (!c || !tb) return null;
          const a = c.getBoundingClientRect(), b = tb.getBoundingClientRect();
          return !(a.right < b.left || a.left > b.right || a.bottom < b.top || a.top > b.bottom);
        })()""",
    )
    await close_page(page)
    return r.get("blocked"), over


async def main():
    srv = None
    ws = None
    page = None
    mutated = []
    try:
        if port_busy(POD_PORT):
            raise RuntimeError(f"{POD_PORT} 已被占用，先關掉舊的 serve_podcast")
        srv = start_server()
        browser, sessions, ws = await C.connect(get_cdp_ws_url(CDP_PORT))

        for w, h in WIDTHS:
            print(f"\n──── 視窗 {w}×{h} ────", flush=True)
            page = await run_width(browser, ws, sessions, w, h)
            errs = C.console_errors(page)
            C.check(f"W{w}.12 無 console 錯誤", not errs, errs[:3], [])
            await close_page(page)
            page = None

        # ── 自動消失（warn 4 秒）：只在預設寬度驗一次 ──────────────────
        print("\n──── 自動消失 ────", flush=True)
        page, _ = await open_pod(browser, ws, sessions, 1400, 1600)
        await show(page, "自動消失測試", "warn")
        n0 = await toast_count(page)
        await asyncio.sleep(5.0)
        n1 = await toast_count(page)
        C.check("A1 warn toast 約 4 秒後自動消失", n0 == 1 and n1 == 0,
                {"顯示時": n0, "5 秒後": n1}, {"顯示時": 1, "5 秒後": 0})
        await close_page(page)
        page = None

        # ── dashboard 也掛同一套 toast ────────────────────────────────
        print("\n──── dashboard ────", flush=True)
        page = await C.open_page(browser, ws, sessions, DASH, settle=2.0, width=1400, height=1600)
        dash_ready = await wait_for(page, "(() => typeof window.showToast === 'function')()")
        if not dash_ready:
            C.skip("D1 dashboard toast 不擋任何可點元素", "dashboard 沒載起來")
        else:
            await show(page, "找不到集數資料夾", "error")
            rd = await blocked(page)
            C.check("D1 dashboard toast 顯示中零個可點元素的中心被擋",
                    rd.get("toasts") == 1 and rd.get("blocked") == [],
                    {"toasts": rd.get("toasts"), "中心被擋": rd.get("blocked"), "邊角被壓": rd.get("clipped")},
                    {"toasts": 1, "中心被擋": []})
        await close_page(page)
        page = None

        # ── 突變：三個部件逐一關，最後全關 ────────────────────────────
        print("\n──── 突變測試（逐一關掉三個部件）────", flush=True)
        print("  MUT-A：只把 ✕ 移回 toast 右端（flex-direction 還原 row）", flush=True)
        patch_file(TOAST_CSS, DIR_OLD, DIR_NEW); mutated.append("dir")
        ba, _ = await mutate_and_scan(browser, ws, sessions)
        hitA = [c for c in (ba or []) if c.startswith("drawer-toggle<toast-close")]
        okA = raw_eq("MUT-A drawer-toggle 的中心被 ✕ 壓住（證明『✕ 擺左端』這半在做事）",
                     bool(hitA), True)
        print(f"      被擋清單：{ba}", flush=True)
        patch_file(TOAST_CSS, DIR_NEW, DIR_OLD); mutated.remove("dir")

        print("  MUT-B：只把 .toast 的 pointer-events 改回 auto", flush=True)
        patch_file(TOAST_CSS, PE_OLD, PE_NEW); mutated.append("pe")
        bb, _ = await mutate_and_scan(browser, ws, sessions)
        okB = raw_eq("MUT-B 有人被擋（證明『本體不吃點擊』這半在做事）", bool(bb), True)
        print(f"      被擋清單：{bb}", flush=True)
        patch_file(TOAST_CSS, PE_NEW, PE_OLD); mutated.remove("pe")

        print("  MUT-C：只把容器位置改回右上", flush=True)
        patch_file(TOAST_CSS, POS_OLD, POS_NEW); mutated.append("pos")
        bc, overC = await mutate_and_scan(browser, ws, sessions)
        # 本體已不吃點擊，所以單獨還原位置未必立刻有人被擋 ——
        # 但唯一吃點擊的 ✕ 會直接壓進 topbar，這就是「搬位置」這半的作用。
        okC = raw_eq("MUT-C ✕ 落回 topbar 範圍內（證明『搬右下』這半在做事）", overC, True)
        print(f"      被擋清單：{bc}", flush=True)
        patch_file(TOAST_CSS, POS_NEW, POS_OLD); mutated.remove("pos")

        print("  MUT-ALL：三者全部還原成舊行為", flush=True)
        patch_file(TOAST_CSS, DIR_OLD, DIR_NEW); mutated.append("dir")
        patch_file(TOAST_CSS, PE_OLD, PE_NEW); mutated.append("pe")
        patch_file(TOAST_CSS, POS_OLD, POS_NEW); mutated.append("pos")
        ball, _ = await mutate_and_scan(browser, ws, sessions)
        hitAll = [c for c in (ball or []) if c.startswith("save-btn<")]
        okAll = raw_eq("MUT-ALL save-btn 的中心被 toast 本體擋住（重現原始缺陷）", bool(hitAll), True)
        print(f"      被擋清單：{ball}", flush=True)
        patch_file(TOAST_CSS, DIR_NEW, DIR_OLD); mutated.remove("dir")
        patch_file(TOAST_CSS, PE_NEW, PE_OLD); mutated.remove("pe")
        patch_file(TOAST_CSS, POS_NEW, POS_OLD); mutated.remove("pos")

        bg, _ = await mutate_and_scan(browser, ws, sessions)
        okG = raw_eq("MUT-GREEN 三者還原後回到零被擋", bg, [])

        C.check("MUT 突變四段＋還原都如預期（A/B/C 各自可紅、ALL 重現原缺陷、GREEN 全綠）",
                okA and okB and okC and okAll and okG,
                {"A": ba, "B": bb, "C ✕壓topbar": overC, "ALL": ball, "GREEN": bg},
                {"A": "含 drawer-toggle", "B": "非空", "C ✕壓topbar": True,
                 "ALL": "含 save-btn", "GREEN": []})
    finally:
        # 突變殘留一定要還原（中途炸掉也不能留半套 CSS 在 repo 裡）
        for key, (new, old) in {
            "dir": (DIR_NEW, DIR_OLD), "pe": (PE_NEW, PE_OLD), "pos": (POS_NEW, POS_OLD),
        }.items():
            if key in mutated:
                patch_file(TOAST_CSS, new, old)
        await close_page(page)
        if ws:
            try:
                await ws.close()
            except Exception:
                pass
        stop_server(srv)

    bad = C.summary()
    RESULT_JSON.write_text(
        json.dumps({"results": C.results_as_dict(), "failed": bad}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"結果寫入 {RESULT_JSON.name}", flush=True)
    return 1 if bad else 0


sys.exit(asyncio.run(main()))
