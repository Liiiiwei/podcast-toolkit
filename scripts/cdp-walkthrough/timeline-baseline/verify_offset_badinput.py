#!/usr/bin/env python3
"""偏移輸入框「NaN 靜默清零」修正的驗收走查。

修正前的三處讀值都長這樣：`Number(el.value || 0)` / `raw === "" ? 0 : ...`。
問題在 <input type=number>：使用者打出 "1e"、"--" 這種非數字時，
**el.value 回的是空字串**，與「真的把欄位清空」完全無法分辨 ——
於是既有偏移被靜默清成 0（字幕整條跑掉／鏡頭失步），而原本那道
`Number.isFinite` 警示永遠等不到 NaN（死碼，從來沒亮過）。

修正：抽出唯一讀值器 readOffsetInput()，用 validity.badInput 分辨兩者，
字幕偏移／cam B 同步／音檔同步三處共用（一刀兩路，不是三套機制）。

本檔驗的是這條鏈：
  真鍵盤輸入 → input.validity.badInput → readOffsetInput → 呼叫端中止＋toast
  → episode.yaml **一個位元組都不動**（對照：合法值會真的寫進去）

斷言紀律（README／MUTATIONS.md）：
  - 每個 ✓ 綁布林斷言 ok=實得==期待，不印值不斷言。
  - badInput 只能用**真鍵盤事件**打出來（程式化賦值造不出）→ type_into。
  - T1.2 與 T3.1 並列：兩者 el.value 都是 ""，只有 badInput 分得出來 ——
    這是「為什麼非得用 validity」的可量測證據，不是口頭主張。
  - 可點元素驗 elementFromPoint 最上層真的是它。
  - 四態：error（badInput 被擋）／success（合法值寫進 yaml）／
    empty（真清空 → 清除偏移）／loading（存檔期間按鈕 disabled，T2.4 驗）。
  - 兩項真突變：
      MUT-A 拿掉 readOffsetInput 的 badInput 守衛 → 字幕偏移與 cam 偏移
            **同時**被靜默清零（一刀兩路，同一個突變點讓兩條路徑一起紅）；
      MUT-B 拿掉 #cam-save 的早退守衛並把 payload builder 移出 try
            → 例外沒人接，按鈕卡在「儲存中…」且零 toast（靜默失敗）。

前提：headless Chrome CDP（預設 :9522）已啟動；:8795 未被占用。
跑法：CDP_PORT=9522 /usr/bin/env python3 -u verify_offset_badinput.py
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

import yaml

import cdp_common as C

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent.parent
APP_JS = REPO_ROOT / "podcast_toolkit" / "web" / "static" / "app.js"
SANDBOX_ROOT = Path("/private/tmp/pt-timeline-baseline")
EP_YAML = SANDBOX_ROOT / "episode" / "20260601 時間軸基準集" / "episode.yaml"

CDP_PORT = int(os.environ.get("CDP_PORT", "9522"))
POD_PORT = 8795
POD = f"http://127.0.0.1:{POD_PORT}/?pthook=1"

RESULT_JSON = HERE / "verify_offset_badinput_result.json"
LOG_TXT = HERE / "verify_offset_badinput_log.txt"

BASE_SUB = 1.5    # 沙盒 episode.yaml 原有的 subtitle_offset_sec
BASE_CAM = 0.42   # 走查開場 seed 的 camera_sync_offset.b（值取自 test_config_roundtrip SAMPLES）
SEED = f"camera_sync_offset:\n  b: {BASE_CAM}\n"


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
    log = open(SANDBOX_ROOT / "server-offset-badinput.log", "ab")
    p = subprocess.Popen(
        [sys.executable, str(HERE / "serve_podcast.py")], stdout=log, stderr=log
    )
    for _ in range(120):
        if p.poll() is not None:
            raise RuntimeError("serve_podcast 啟動即退出，看 server-offset-badinput.log")
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
    for _ in range(40):
        if not port_busy(POD_PORT):
            return
        time.sleep(0.25)


# ── 磁碟端讀值（不透過產品碼的讀取路徑）──────────────────────────────────
def yaml_text():
    return EP_YAML.read_text(encoding="utf-8")


def yaml_data():
    return yaml.safe_load(yaml_text()) or {}


def sub_off():
    """subtitle_offset_sec；鍵被移除時回 None（save_state 對 0 會 pop）。"""
    return yaml_data().get("subtitle_offset_sec")


def cam_off():
    """camera_sync_offset.b；鍵被移除時回 None（save_state 對 0 會 pop）。"""
    return (yaml_data().get("camera_sync_offset") or {}).get("b")


def seed_yaml(base: str):
    EP_YAML.write_text(base + SEED, encoding="utf-8")


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
    return ok, got


# ── CDP helpers ──────────────────────────────────────────────────────────
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


async def top_hits_button(page, x, y, selector):
    """(x,y) 最上層元素是否落在 selector 之內（含子節點）。
    按鈕把標籤包在 <span> 裡時 elementFromPoint 會回那個 span，點擊照樣冒泡；
    要擋的是「別的疊層蓋在上面」，所以判準是 closest() 命中，不是 id 全等。"""
    return await C.js(
        page,
        f"""(() => {{
          const el = document.elementFromPoint({x}, {y});
          if (!el) return null;
          return {{ tag: el.tagName, id: el.id || null,
                    within: !!el.closest({json.dumps(selector)}) }};
        }})()""",
    )


async def close_page(page):
    if page is None:
        return
    try:
        await page.send("Page.close")
    except Exception:
        pass
    await asyncio.sleep(0.2)


async def open_pod(browser, ws, sessions):
    page = await C.open_page(browser, ws, sessions, POD, settle=2.0)
    ready = await wait_for(
        page,
        "(() => { const b = document.querySelector('#srt-shift-toggle');"
        " return !!(b && !b.hidden); })()",
    )
    return page, ready


async def clear_toasts(page):
    await C.js(
        page,
        "(() => { const b=document.querySelector('#toast-container');"
        " if (b) b.innerHTML=''; return true; })()",
    )


async def toasts(page):
    return await C.js(
        page,
        """(() => {
          const b = document.querySelector('#toast-container');
          if (!b) return { count: 0, items: [] };
          const items = [...b.querySelectorAll('.toast')].map((t) => ({
            kind: t.classList.contains('toast-error') ? 'error'
                : (t.classList.contains('toast-warn') ? 'warn' : 'other'),
            text: t.textContent,
          }));
          return { count: items.length, items };
        })()""",
    )


async def type_into(page, sel, text):
    """真鍵盤輸入（number input 打 '1e' 才會產生 badInput，程式化賦值造不出來）。
    先把 value 清成空字串再打，讓上一輪殘留的 bad input 狀態一併清掉。"""
    await C.js(
        page,
        f"(() => {{ const el=document.querySelector('{sel}'); el.value=''; el.focus();"
        " return document.activeElement === el; })()",
    )
    for ch in text:
        # 只有 char 事件帶 text：keyDown 也帶的話，keyDown 與 char 會各插入一次字元
        await page.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": ch})
        await page.send("Input.dispatchKeyEvent",
                        {"type": "char", "key": ch, "text": ch, "unmodifiedText": ch})
        await page.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch})
        await asyncio.sleep(0.03)
    await asyncio.sleep(0.1)
    state = await C.js(
        page,
        f"(() => {{ const el=document.querySelector('{sel}');"
        " return {value: el.value, badInput: el.validity.badInput}; })()",
    )
    await C.js(page, f"(() => {{ document.querySelector('{sel}').blur(); return true; }})()")
    await asyncio.sleep(0.15)
    return state


async def click_sel(page, sel):
    """真滑鼠點擊 + 疊層驗證，回 (top_el, clicked_ok)。"""
    r = await C.element_rect_center(page, sel)
    if not r:
        return None, False
    top_el = await top_hits_button(page, r["cx"], r["cy"], sel)
    await C.mouse_click(page, r["cx"], r["cy"])
    return top_el, True


async def wait_yaml_change(before: str, timeout: float):
    """等磁碟上的 episode.yaml 真的變了（不看按鈕文案）。逾時回 False。"""
    t = 0.0
    while t < timeout:
        await asyncio.sleep(0.25)
        t += 0.25
        if yaml_text() != before:
            await asyncio.sleep(0.6)  # 等 loadEpisodeState 回來
            return True
    return False


async def open_shift_popover(page):
    top_el, _ = await click_sel(page, "#srt-shift-toggle")
    await asyncio.sleep(0.3)
    st = await C.js(
        page,
        "(() => { const m=document.querySelector('#srt-shift-menu');"
        " const b=document.querySelector('#srt-shift-toggle');"
        " return {open: m ? !m.hidden : null, aria: b ? b.getAttribute('aria-expanded') : null}; })()",
    )
    return top_el, st


async def apply_shift(page, timeout):
    """點「套用偏移」，回 (top_el, yaml 是否被改寫, toast, 按鈕態)。"""
    before = yaml_text()
    await clear_toasts(page)
    top_el, _ = await click_sel(page, "#srt-shift-btn")
    await asyncio.sleep(0.6)
    tl = await toasts(page)  # warn toast 4 秒自動消失，必須在等 yaml 之前先讀
    changed = await wait_yaml_change(before, timeout)
    btn = await C.js(
        page,
        "(() => { const b=document.querySelector('#srt-shift-btn');"
        " return {disabled: b.disabled}; })()",
    )
    return top_el, changed, tl, btn


async def cam_modal_state(page):
    return await C.js(
        page,
        """(() => {
          const d = document.querySelector('#cam-modal');
          const b = document.querySelector('#cam-save');
          return { open: d ? d.open : null,
                   btnDisabled: b ? b.disabled : null,
                   btnText: b ? b.textContent.trim() : null,
                   camVal: document.querySelector('#cam-sync-offset-b').value,
                   audioVal: document.querySelector('#audio-sync-offset').value };
        })()""",
    )


async def open_cam_modal(page):
    # 先清掉殘留 toast：toast 固定在右上（top:16px; right:16px），正好壓在 topbar 右側的
    # 「鏡頭」鈕上（實測 #cam-btn 在 x1029-1103 / y14-46），toast 還在的那 4~8 秒內
    # elementFromPoint 回的是 toast 而不是按鈕，點擊會被吃掉。
    # 這是既有 toast 版面的問題，不在本梯範圍——已記進 MUTATIONS.md 附錄留給下一梯。
    await clear_toasts(page)
    top_el, _ = await click_sel(page, "#cam-btn")
    await asyncio.sleep(0.5)
    return top_el, await cam_modal_state(page)


async def cam_save(page, timeout):
    """點 cam modal 的「儲存」，回 (top_el, yaml 是否被改寫, toast, modal 態)。"""
    before = yaml_text()
    await clear_toasts(page)
    top_el, _ = await click_sel(page, "#cam-save")
    await asyncio.sleep(0.6)
    tl = await toasts(page)  # 同上：先讀 toast 再等 yaml
    changed = await wait_yaml_change(before, timeout)
    st = await cam_modal_state(page)
    return top_el, changed, tl, st


# ── 突變點（產品碼字串，必須逐字命中）──────────────────────────────────────
MUT_A_OLD = (
    "  if (el.validity && el.validity.badInput) {\n"
    '    return { ok: false, reason: "看起來不是數字（既有偏移沒有被改動）" };\n'
    "  }\n"
)
MUT_A_NEW = "  // MUT-A：拿掉 badInput 守衛 → 退回「空字串當成 0」的舊行為\n"

MUT_B1_OLD = (
    '  const camRead = readOffsetInput($("#cam-sync-offset-b"));\n'
    "  if (!camRead.ok) {\n"
    "    showToast(`同步偏移要是數字：${camRead.reason}`, \"warn\");\n"
    "    return;\n"
    "  }\n"
    '  const audioRead = readOffsetInput($("#audio-sync-offset"));\n'
    "  if (!audioRead.ok) {\n"
    "    showToast(`音檔同步偏移要是數字：${audioRead.reason}`, \"warn\");\n"
    "    return;\n"
    "  }\n"
    '  const btn = $("#cam-save");\n'
)
MUT_B1_NEW = '  // MUT-B：拿掉 #cam-save 的兩道早退守衛\n  const btn = $("#cam-save");\n'

MUT_B2_OLD = (
    "  try {\n"
    "    // builder 也會丟例外（非法偏移），所以放進 try——放外面會變成未捕捉例外，\n"
    "    // 按鈕永遠停在「儲存中…」而且沒有任何錯誤表現（靜默失敗）。\n"
    "    const payload = _camModalSavePayload();\n"
    "    await postSave(payload);\n"
)
MUT_B2_NEW = (
    "  const payload = _camModalSavePayload(); // MUT-B：builder 移出 try\n"
    "  try {\n"
    "    await postSave(payload);\n"
)


async def main():
    if port_busy(POD_PORT):
        print(f"[ABORT] :{POD_PORT} 已被占用，本檔要自己管理 serve_podcast，請先停掉", flush=True)
        return 2

    base_yaml = yaml_text()
    if "camera_sync_offset" in base_yaml:
        print("[ABORT] 沙盒 episode.yaml 已含 camera_sync_offset，先還原再跑", flush=True)
        return 2
    if f"subtitle_offset_sec: {BASE_SUB}" not in base_yaml:
        print(f"[ABORT] 沙盒 episode.yaml 的 subtitle_offset_sec 不是 {BASE_SUB}，先還原再跑", flush=True)
        return 2

    log_lines = []

    def logp(s=""):
        print(s, flush=True)
        log_lines.append(str(s))

    ws_url = get_cdp_ws_url(CDP_PORT)
    browser, sessions, ws = await C.connect(ws_url)
    server_holder = [None]  # 突變回合要重啟 server，用單元素 list 讓內層函式改得到
    try:
        logp("=" * 70)
        logp(f"偏移輸入 badInput 走查（NaN 靜默清零修正）  URL={POD}  CDP={CDP_PORT}")
        logp("=" * 70)

        seed_yaml(base_yaml)
        server_holder[0] = start_server()
        page, ready = await open_pod(browser, ws, sessions)

        # ══════════ V0 版本指紋 ══════════
        logp("\n--- V0 版本指紋（避免測到未同步的舊碼）---")
        C.check("V0.0 編輯器已載入且字幕偏移入口已顯示（有字幕卡）", bool(ready), ready, True)
        src = APP_JS.read_text(encoding="utf-8")
        fp = {
            "reader": "function readOffsetInput(el)" in src,
            "uses": src.count("readOffsetInput("),
            "badInput": "el.validity.badInput" in src,
        }
        want_fp = {"reader": True, "uses": 6, "badInput": True}
        C.check("V0.1 原始碼指紋：readOffsetInput 存在且被 5 處呼叫（定義 1 ＋ 呼叫 5）",
                fp == want_fp, fp, want_fp)
        dom_fp = await C.js(
            page,
            """(() => {
              const ids = ['srt-shift-input', 'cam-sync-offset-b', 'audio-sync-offset'];
              const out = {};
              for (const id of ids) {
                const el = document.getElementById(id);
                out[id] = el ? el.type : null;
              }
              return out;
            })()""",
        )
        want_dom = {"srt-shift-input": "number", "cam-sync-offset-b": "number",
                    "audio-sync-offset": "number"}
        C.check("V0.2 三個偏移欄位都在且都是 type=number（badInput 只有 number 會有）",
                dom_fp == want_dom, dom_fp, want_dom)
        C.check(f"V0.3 磁碟基準：subtitle_offset_sec={BASE_SUB}、camera_sync_offset.b={BASE_CAM}",
                (sub_off(), cam_off()) == (BASE_SUB, BASE_CAM),
                {"sub": sub_off(), "cam": cam_off()}, {"sub": BASE_SUB, "cam": BASE_CAM})

        # ══════════ T1 error 態：字幕偏移 badInput ══════════
        logp("\n--- T1 error 態：字幕偏移打非數字 → 擋下，既有偏移不得被清零 ---")
        top_toggle, pop = await open_shift_popover(page)
        C.check("T1.0 「偏移」鈕未被疊層遮擋（elementFromPoint 最上層落在按鈕內）",
                bool(top_toggle and top_toggle.get("within")), top_toggle, {"within": True})
        C.check("T1.1 真點擊 → 浮層展開且 aria-expanded=true",
                pop == {"open": True, "aria": "true"}, pop, {"open": True, "aria": "true"})
        init_val = await C.js(page, "document.querySelector('#srt-shift-input').value")
        C.check(f"T1.2 既有偏移已回填欄位（{BASE_SUB}）",
                init_val == str(BASE_SUB), init_val, str(BASE_SUB))

        bad = await type_into(page, "#srt-shift-input", "1e")
        C.check("T1.3 真鍵盤打 '1e' → el.value 是空字串、badInput=true"
                "（這正是舊碼分不出「非數字」與「清空」的原因）",
                bad == {"value": "", "badInput": True}, bad, {"value": "", "badInput": True})
        if not bad.get("badInput"):
            C.skip("T1.4–T1.6／T4.*／T5.*／MUT-A", "這版 Chrome 沒產生 badInput，不假裝通過")
            C.summary()
            return 1

        top_apply, changed, tl, btn_st = await apply_shift(page, timeout=2.5)
        C.check("T1.4 「套用偏移」鈕未被疊層遮擋", bool(top_apply and top_apply.get("within")),
                top_apply, {"within": True})
        warn_ok = tl["count"] == 1 and tl["items"][0]["kind"] == "warn" \
            and "偏移秒數必須是數字" in tl["items"][0]["text"]
        C.check("T1.5 非數字 → 跳 warn toast（失敗路徑有可見表現，不是靜默）",
                warn_ok, tl, {"count": 1, "kind": "warn", "text": "含「偏移秒數必須是數字」"})
        C.check(f"T1.6 磁碟 episode.yaml 一個位元組都沒動，subtitle_offset_sec 仍 {BASE_SUB}"
                "（舊碼會清成 0 → 鍵被 pop）",
                changed is False and sub_off() == BASE_SUB,
                {"yamlChanged": changed, "sub": sub_off()},
                {"yamlChanged": False, "sub": BASE_SUB})
        C.check("T1.7 被擋下時按鈕沒有卡在 disabled（早退在 disabled 之前）",
                btn_st["disabled"] is False, btn_st, {"disabled": False})

        # ══════════ T2 success 態 ══════════
        logp("\n--- T2 success 態：合法值真的寫進 episode.yaml ---")
        good = await type_into(page, "#srt-shift-input", "2.5")
        C.check("T2.0 重打合法值 → badInput 清掉（error 態可恢復，不是單向門）",
                good == {"value": "2.5", "badInput": False}, good,
                {"value": "2.5", "badInput": False})
        _, changed2, tl2, btn2 = await apply_shift(page, timeout=15)
        C.check("T2.1 點套用 → 磁碟 episode.yaml 真的被改寫", changed2 is True, changed2, True)
        C.check("T2.2 subtitle_offset_sec 讀回 2.5（round-trip：寫入→重載→讀回）",
                sub_off() == 2.5, sub_off(), 2.5)
        C.check("T2.3 成功時不跳 toast（toast 只用在失敗路徑）", tl2["count"] == 0, tl2, {"count": 0})
        C.check("T2.4 存檔結束後按鈕解除 disabled（loading 態有收尾）",
                btn2["disabled"] is False, btn2, {"disabled": False})
        back = await C.js(page, "document.querySelector('#srt-shift-input').value")
        C.check("T2.5 重載後欄位回填新值 2.5（使用者看得到的那一份也對）",
                back == "2.5", back, "2.5")

        # ══════════ T3 empty 態 ══════════
        logp("\n--- T3 empty 態：真清空＝清除偏移（與 T1 的空字串並列對照）---")
        cleared = await type_into(page, "#srt-shift-input", "")
        C.check("T3.0 真清空 → el.value 也是空字串，但 badInput=false"
                "（與 T1.3 完全同值，只有 badInput 分得出來——這是併軌的量測證據）",
                cleared == {"value": "", "badInput": False}, cleared,
                {"value": "", "badInput": False})
        _, changed3, tl3, _ = await apply_shift(page, timeout=15)
        C.check("T3.1 清空後套用 → yaml 被改寫，且 subtitle_offset_sec 鍵被移除（0＝清除偏移）",
                changed3 is True and sub_off() is None,
                {"yamlChanged": changed3, "sub": sub_off()},
                {"yamlChanged": True, "sub": None})
        C.check("T3.2 清除偏移不跳 toast（這是正常操作，不是錯誤）",
                tl3["count"] == 0, tl3, {"count": 0})
        await type_into(page, "#srt-shift-input", str(BASE_SUB))
        _, changed4, _, _ = await apply_shift(page, timeout=15)
        C.check(f"T3.3 打回 {BASE_SUB} 套用 → yaml 讀回 {BASE_SUB}（error→empty→success 三態可互相往返）",
                changed4 is True and sub_off() == BASE_SUB,
                {"yamlChanged": changed4, "sub": sub_off()},
                {"yamlChanged": True, "sub": BASE_SUB})

        # ══════════ T4 cam B 同步偏移 badInput ══════════
        logp("\n--- T4 cam B 同步偏移：同一個讀值器，同樣擋得下來 ---")
        await C.js(page, "(() => { document.body.click(); return true; })()")  # 關掉浮層
        await asyncio.sleep(0.25)
        top_cam, st_open = await open_cam_modal(page)
        C.check("T4.0 「鏡頭」鈕未被疊層遮擋", bool(top_cam and top_cam.get("within")),
                top_cam, {"within": True})
        C.check(f"T4.1 modal 開啟且 cam B 同步偏移回填 {BASE_CAM}",
                st_open["open"] is True and st_open["camVal"] == str(BASE_CAM),
                {"open": st_open["open"], "camVal": st_open["camVal"]},
                {"open": True, "camVal": str(BASE_CAM)})
        badc = await type_into(page, "#cam-sync-offset-b", "1e")
        C.check("T4.2 cam 欄位真鍵盤打 '1e' → value=''、badInput=true",
                badc == {"value": "", "badInput": True}, badc, {"value": "", "badInput": True})
        top_save, changedc, tlc, stc = await cam_save(page, timeout=2.5)
        C.check("T4.3 cam modal 的「儲存」鈕未被疊層遮擋",
                bool(top_save and top_save.get("within")), top_save, {"within": True})
        warn_c = tlc["count"] == 1 and tlc["items"][0]["kind"] == "warn" \
            and "同步偏移要是數字" in tlc["items"][0]["text"]
        C.check("T4.4 非數字 → 跳 warn toast", warn_c, tlc,
                {"count": 1, "kind": "warn", "text": "含「同步偏移要是數字」"})
        C.check(f"T4.5 yaml 一個位元組都沒動，camera_sync_offset.b 仍 {BASE_CAM}"
                "（舊碼會清成 0 → 整個 camera_sync_offset 被 pop → 雙機失步）",
                changedc is False and cam_off() == BASE_CAM,
                {"yamlChanged": changedc, "cam": cam_off()},
                {"yamlChanged": False, "cam": BASE_CAM})
        C.check("T4.6 被擋下時 modal 仍開著、按鈕回「儲存」且未卡在 disabled",
                (stc["open"], stc["btnDisabled"], stc["btnText"]) == (True, False, "儲存"),
                {"open": stc["open"], "btnDisabled": stc["btnDisabled"], "btnText": stc["btnText"]},
                {"open": True, "btnDisabled": False, "btnText": "儲存"})

        # ══════════ T5 音檔同步偏移 badInput ══════════
        logp("\n--- T5 音檔同步偏移：第三個呼叫點，同一個讀值器 ---")
        await type_into(page, "#cam-sync-offset-b", str(BASE_CAM))
        bada = await type_into(page, "#audio-sync-offset", "1e")
        C.check("T5.0 音檔欄位真鍵盤打 '1e' → value=''、badInput=true",
                bada == {"value": "", "badInput": True}, bada, {"value": "", "badInput": True})
        _, changeda, tla, sta = await cam_save(page, timeout=2.5)
        warn_a = tla["count"] == 1 and tla["items"][0]["kind"] == "warn" \
            and "音檔同步偏移要是數字" in tla["items"][0]["text"]
        C.check("T5.1 音檔欄位非數字 → 跳 warn toast 且訊息指名是音檔那一欄",
                warn_a, tla, {"count": 1, "kind": "warn", "text": "含「音檔同步偏移要是數字」"})
        C.check(f"T5.2 yaml 沒動，camera_sync_offset.b 仍 {BASE_CAM}"
                "（cam 欄位是合法的，被擋的是音檔那一欄）",
                changeda is False and cam_off() == BASE_CAM,
                {"yamlChanged": changeda, "cam": cam_off()},
                {"yamlChanged": False, "cam": BASE_CAM})
        C.check("T5.3 modal 仍開著、按鈕未卡在 disabled",
                (sta["open"], sta["btnDisabled"]) == (True, False),
                {"open": sta["open"], "btnDisabled": sta["btnDisabled"]},
                {"open": True, "btnDisabled": False})

        # ══════════ T6 cam success round-trip ══════════
        logp("\n--- T6 cam success：合法值 round-trip ---")
        await type_into(page, "#audio-sync-offset", "")
        await type_into(page, "#cam-sync-offset-b", "0.75")
        _, changed6, tl6, st6 = await cam_save(page, timeout=20)
        C.check("T6.0 合法值 → yaml 真的被改寫且不跳 toast",
                changed6 is True and tl6["count"] == 0,
                {"yamlChanged": changed6, "toast": tl6["count"]},
                {"yamlChanged": True, "toast": 0})
        C.check("T6.1 camera_sync_offset.b 讀回 0.75",
                cam_off() == 0.75, cam_off(), 0.75)
        C.check("T6.2 存檔成功後 modal 關閉、按鈕文案復原「儲存」",
                (st6["open"], st6["btnText"], st6["btnDisabled"]) == (False, "儲存", False),
                {"open": st6["open"], "btnText": st6["btnText"], "btnDisabled": st6["btnDisabled"]},
                {"open": False, "btnText": "儲存", "btnDisabled": False})
        _, st_re = await open_cam_modal(page)
        C.check("T6.3 重開 modal → cam 欄位讀回 0.75（state → yaml → state 走完一圈）",
                st_re["camVal"] == "0.75", st_re["camVal"], "0.75")
        await type_into(page, "#cam-sync-offset-b", "0.425")
        _, changed6b, tl6b, _ = await cam_save(page, timeout=20)
        C.check("T6.4 手打 0.425（step=0.01 不整除，validity.valid 是 false）仍然存得進去"
                "——step 只是箭頭跳動量，拿它當合法性判準會擋掉本來就能用的值",
                changed6b is True and cam_off() == 0.425 and tl6b["count"] == 0,
                {"yamlChanged": changed6b, "cam": cam_off(), "toast": tl6b["count"]},
                {"yamlChanged": True, "cam": 0.425, "toast": 0})
        await open_cam_modal(page)
        await type_into(page, "#cam-sync-offset-b", str(BASE_CAM))
        _, changed7, _, _ = await cam_save(page, timeout=20)
        C.check(f"T6.5 改回 {BASE_CAM} → yaml 讀回 {BASE_CAM}",
                changed7 is True and cam_off() == BASE_CAM,
                {"yamlChanged": changed7, "cam": cam_off()},
                {"yamlChanged": True, "cam": BASE_CAM})

        errs = C.console_errors(page)
        C.check("T1–T6 全段互動無 console 例外", errs == [], errs, [])
        await close_page(page)

        # ══════════ MUT-A：拿掉 badInput 守衛 → 一刀兩路同時紅 ══════════
        logp("\n--- MUT-A 真突變：readOffsetInput 拿掉 badInput 守衛 ---")

        async def run_badinput_round(tag):
            """打 '1e' 到字幕偏移與 cam 偏移各一次，回 (字幕偏移, cam 偏移, toast 總數)。"""
            seed_yaml(base_yaml)
            stop_server(server_holder[0])
            server_holder[0] = start_server()
            p, _ = await open_pod(browser, ws, sessions)
            await open_shift_popover(p)
            await type_into(p, "#srt-shift-input", "1e")
            _, _, t1, _ = await apply_shift(p, timeout=3)
            s = sub_off()
            await C.js(p, "(() => { document.body.click(); return true; })()")
            await asyncio.sleep(0.25)
            await open_cam_modal(p)
            await type_into(p, "#cam-sync-offset-b", "1e")
            _, _, t2, _ = await cam_save(p, timeout=3)
            c = cam_off()
            await close_page(p)
            print(f"    [{tag}] sub={s!r} cam={c!r} "
                  f"toastShift={t1['count']} toastCam={t2['count']}", flush=True)
            return s, c, (t1["count"], t2["count"])

        patch_file(APP_JS, MUT_A_OLD, MUT_A_NEW)
        try:
            sA, cA, tA = await run_badinput_round("MUT-A-RED")
        finally:
            patch_file(APP_JS, MUT_A_NEW, MUT_A_OLD)
        redA1, _ = raw_eq("MUT-A-RED 字幕偏移應維持 1.5（突變後預期被清成 0 → 鍵消失）", sA, BASE_SUB)
        redA2, _ = raw_eq("MUT-A-RED cam 偏移應維持 0.42（突變後預期被清成 0 → 鍵消失）", cA, BASE_CAM)
        sG, cG, tG = await run_badinput_round("MUT-A-GREEN")
        greenA1, _ = raw_eq("MUT-A-GREEN 還原後字幕偏移守住 1.5", sG, BASE_SUB)
        greenA2, _ = raw_eq("MUT-A-GREEN 還原後 cam 偏移守住 0.42", cG, BASE_CAM)
        greenA3, _ = raw_eq("MUT-A-GREEN 還原後兩條路徑各跳一個 toast（字幕偏移, cam 偏移）",
                            tG, (1, 1))
        C.check("MUT-A 真突變成立：拿掉唯一讀值器的 badInput 守衛 → "
                "字幕偏移與 cam 偏移**同時**被靜默清零（RED），還原→兩條都守住（GREEN）。"
                "同一個突變點讓兩條路徑一起紅，正是「一刀兩路、沒有第二套機制」的證據",
                (redA1 is False) and (redA2 is False) and greenA1 and greenA2 and greenA3
                and tA == (0, 0),
                {"red": {"sub": sA, "cam": cA, "toasts": tA},
                 "green": {"sub": sG, "cam": cG, "toasts": tG}},
                {"red": {"sub": "None（被清零）", "cam": "None（被清零）", "toasts": (0, 0)},
                 "green": {"sub": BASE_SUB, "cam": BASE_CAM, "toasts": (1, 1)}})

        # ══════════ MUT-B：早退守衛拿掉＋builder 移出 try → 靜默卡死 ══════════
        logp("\n--- MUT-B 真突變：#cam-save 早退守衛拿掉，且 payload builder 移出 try ---")

        async def run_camsave_round(tag):
            """cam 欄位打 '1e' 後按儲存，回 (按鈕 disabled, 按鈕文案, toast 數)。"""
            seed_yaml(base_yaml)
            stop_server(server_holder[0])
            server_holder[0] = start_server()
            p, _ = await open_pod(browser, ws, sessions)
            await open_cam_modal(p)
            await type_into(p, "#cam-sync-offset-b", "1e")
            _, _, tl, st = await cam_save(p, timeout=3)
            await close_page(p)
            print(f"    [{tag}] disabled={st['btnDisabled']!r} text={st['btnText']!r} "
                  f"toasts={tl['count']}", flush=True)
            return st["btnDisabled"], st["btnText"], tl["count"]

        patch_file(APP_JS, MUT_B1_OLD, MUT_B1_NEW)
        patch_file(APP_JS, MUT_B2_OLD, MUT_B2_NEW)
        try:
            dB, txtB, tB = await run_camsave_round("MUT-B-RED")
        finally:
            patch_file(APP_JS, MUT_B2_NEW, MUT_B2_OLD)
            patch_file(APP_JS, MUT_B1_NEW, MUT_B1_OLD)
        redB1, _ = raw_eq("MUT-B-RED 按鈕應未卡在 disabled（突變後預期 True＝卡死）", dB, False)
        redB2, _ = raw_eq("MUT-B-RED 按鈕文案應是「儲存」（突變後預期停在「儲存中…」）", txtB, "儲存")
        redB3, _ = raw_eq("MUT-B-RED 應有 1 個 toast（突變後預期 0＝靜默失敗）", tB, 1)
        dG2, txtG2, tG2 = await run_camsave_round("MUT-B-GREEN")
        greenB, _ = raw_eq("MUT-B-GREEN 還原後：按鈕未 disabled、文案「儲存」、1 個 toast",
                           (dG2, txtG2, tG2), (False, "儲存", 1))
        C.check("MUT-B 真突變成立：守衛拿掉＋builder 移出 try → 例外沒人接，"
                "按鈕卡在「儲存中…」且零 toast（靜默失敗），還原→可見錯誤＋按鈕可用",
                (redB1 is False) and (redB2 is False) and (redB3 is False) and greenB,
                {"red": {"disabled": dB, "text": txtB, "toasts": tB},
                 "green": {"disabled": dG2, "text": txtG2, "toasts": tG2}},
                {"red": {"disabled": True, "text": "儲存中…", "toasts": 0},
                 "green": {"disabled": False, "text": "儲存", "toasts": 1}})

        stop_server(server_holder[0])
        server_holder[0] = None

        bad_n = C.summary()
        for line in C.results_as_dict():
            log_lines.append(f"{line['status'].upper()}\t{line['name']}")
        RESULT_JSON.write_text(
            json.dumps({"mode": "offset-badinput", "results": C.results_as_dict()},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        LOG_TXT.write_text("\n".join(log_lines), encoding="utf-8")
        return 1 if bad_n else 0
    finally:
        stop_server(server_holder[0])
        EP_YAML.write_text(base_yaml, encoding="utf-8")  # 沙盒 yaml 還原成跑之前的樣子
        try:
            await ws.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
