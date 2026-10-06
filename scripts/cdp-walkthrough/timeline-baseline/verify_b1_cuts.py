#!/usr/bin/env python3
"""B1 專梯獨立驗收走查 —— 剪除語意的單一 source of truth（時間版 cuts 為正典）。

驗的是「瀏覽器實測數字」，不是 diff：podcast 編輯器刪卡 → 存檔 → episode.yaml 只留
cuts（無 deletions）→ 重載後紅卡集合一致（round-trip）→ 注入一段換不回卡的 cut（影片
模式在句中剪的），統計列要露出來且存檔時原樣送回（不可靜默吃掉）。

沙盒集刻意帶 subtitle_offset_sec: 1.5，讓「顯示軸 ↔ 磁碟軸」的換算真的被走到；
偏移若少減／多減一次，yaml 裡的 cuts 會整批差 1.5 秒，第 3 項會紅。

每個 ✓ 都綁布林斷言（ok = 實得 == 期待），沒有只印值不斷言的行。

前提：serve_podcast.py 起在 :8795（服務 live working tree）、headless Chrome CDP :9331。
跑法：/usr/bin/python3 -u verify_b1_cuts.py
"""
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cdp_common as C

HERE = Path(__file__).resolve().parent
SERVE = HERE / "serve_podcast.py"
_server = None


def _port_free():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8795/api/episode", timeout=2):
            return False
    except Exception:
        return True


def stop_server():
    """收掉本走查起的 uvicorn；順便清掉別人留在 8795 的（走查要能重跑）。"""
    global _server
    if _server and _server.poll() is None:
        os.killpg(os.getpgid(_server.pid), signal.SIGTERM)
        _server.wait(timeout=10)
    _server = None
    if not _port_free():
        subprocess.run(["bash", "-c", "lsof -ti tcp:8795 | xargs -r kill"], check=False)
        for _ in range(20):
            if _port_free():
                break
            time.sleep(0.25)


def start_server():
    """起 serve_podcast.py 並等到 /api/episode 有回應。

    為什麼每次改 yaml 都要重起：Episode 在 __init__ 就把 episode.yaml 讀進 self.cfg，
    之後 /api/episode 一直讀那份記憶體副本。走查在伺服器背後改檔（注入 foreign cut）
    不重啟的話，量到的是舊快照——那會被誤判成「載入端沒讀 cuts」。
    """
    global _server
    stop_server()
    log = open("/private/tmp/pt-timeline-baseline/server-b1.log", "ab")
    _server = subprocess.Popen(
        [sys.executable, "-u", str(SERVE)],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    for _ in range(80):
        if not _port_free():
            return True
        time.sleep(0.25)
    return False

POD = "http://127.0.0.1:8795/?pthook=1"
EPDIR = Path("/private/tmp/pt-timeline-baseline/episode/20260601 時間軸基準集")
YAML = EPDIR / "episode.yaml"
OUT = Path(__file__).resolve().parent / "verify_b1_cuts_result.json"

# 要刪的兩張卡（idx）與它們在磁碟 _v2.srt 上的時間（期待寫進 yaml 的 cuts）
# 起點 yaml 只放舊格式 deletions:[2]；UI 再刪 #4 → 存檔才是真的有變更（不是原地打轉）
BASE_DEL = [2]
ADD_IDX = 4
WANT_CUTS = [[2.7, 3.9], [7.35, 8.25]]
# 換不回任何一張卡的 cut：落在 #3「4.25–6.65」正中間，兩端都對不齊卡外緣，
# 且不與任何被刪的卡重疊（重疊會讓「原樣保留」的斷言分不清是誰寫的）
FOREIGN = [4.8, 5.2]


def read_yaml():
    import yaml as _y

    return _y.safe_load(YAML.read_text(encoding="utf-8")) or {}


def write_yaml(data):
    import yaml as _y

    YAML.write_text(
        _y.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


async def ws_url():
    with urllib.request.urlopen("http://127.0.0.1:9331/json/version", timeout=5) as r:
        return json.loads(r.read())["webSocketDebuggerUrl"]


async def wait_for(page, expr, timeout=25, interval=0.4):
    for _ in range(int(timeout / interval)):
        v = await C.js(page, expr)
        if v:
            return v
        await asyncio.sleep(interval)
    return None


async def open_pod(browser, ws, sessions):
    page = await C.open_page(browser, ws, sessions, POD, settle=2.0)
    ok = await wait_for(
        page, "document.querySelectorAll('#cards-list .card').length > 0"
    )
    return page, ok


def close(a, b, tol=0.005):
    return abs(float(a) - float(b)) <= tol


def cuts_close(got, want, tol=0.005):
    if not isinstance(got, list) or len(got) != len(want):
        return False
    for g, w in zip(got, want):
        if not isinstance(g, (list, tuple)) or len(g) != 2:
            return False
        if not (close(g[0], w[0], tol) and close(g[1], w[1], tol)):
            return False
    return True


async def fingerprint(page):
    """來源未同步防呆：服務的三個 JS 必須都是含 B1 改動的版本，否則整支中止。
    （測試站台服務的是 live 原始碼樹，但這條防的是「忘了重啟／服務到別棵樹」。）"""
    probe = await C.js(
        page,
        """(async () => {
      const grab = async (p) => (await (await fetch(p, {cache:'no-store'})).text());
      const [core, api, app] = await Promise.all([
        grab('/static/timeline-core.js'), grab('/static/api.js'), grab('/static/app.js'),
      ]);
      return {
        core: core.includes('export function cardKeysToCuts')
              && core.includes('export function cutsToCardSelection'),
        api: api.includes('export function cutToDiskTime')
             && api.includes('export function cutFromDiskTime'),
        app: app.includes('foreignCuts') && app.includes('cutsToCardSelection'),
      };
    })()""",
    )
    return probe


async def red_keys(page):
    """目前畫面上被標成刪除的卡 key（.card.deleted 的 data-idx），排序後回傳。"""
    return await C.js(
        page,
        "[...document.querySelectorAll('#cards-list .card.deleted')]"
        ".map(e => e.dataset.idx).sort()",
    )


async def del_btn_center(page, idx):
    """把第 idx 張卡捲進可視區，回傳它刪除鈕的中心座標（給真事件點擊用）。"""
    return await C.js(
        page,
        """(() => {
      const card = document.querySelector('#cards-list .card[data-idx="%d"]');
      if (!card) return null;
      const b = card.querySelector('.card-del');
      if (!b) return null;
      b.scrollIntoView({block:'center'});
      const r = b.getBoundingClientRect();
      return {cx: r.left + r.width/2, cy: r.top + r.height/2};
    })()"""
        % idx,
    )


async def do_save(page):
    """按儲存並確認真的存成功。

    不用 #unsaved-badge 判完成：unsavedCount() 把 deletions.size 無條件算進去，
    存完重載後刪段還在 → 標記永遠亮著（既有行為，本梯不動它）。改認兩個獨立訊號：
    按鈕回報「已儲存」、且 episode.yaml 的 mtime 真的前進。錯誤 toast 出現即判失敗。
    """
    before = YAML.stat().st_mtime_ns
    await C.js(page, "document.querySelector('#save-btn').click()")
    said = await wait_for(
        page,
        "/已儲存/.test(document.querySelector('#save-btn').textContent)",
        timeout=25,
    )
    moved = False
    for _ in range(50):
        if YAML.stat().st_mtime_ns != before:
            moved = True
            break
        await asyncio.sleep(0.3)
    err = await C.js(
        page,
        "[...document.querySelectorAll('.toast, .toast-error')]"
        ".map(e => e.textContent).filter(t => /失敗/.test(t))",
    )
    await asyncio.sleep(0.6)
    if err:
        print(f"    存檔錯誤 toast：{err}", flush=True)
    return bool(said) and moved and not err


async def main():
    # ── 前置：yaml 清成「只有舊格式 deletions、沒有 cuts」的待遷移起點 ──
    base = read_yaml()
    base.pop("cuts", None)
    base["deletions"] = list(BASE_DEL)
    base["subtitle_offset_sec"] = 1.5
    write_yaml(base)
    if not start_server():
        print("！伺服器起不來，中止", flush=True)
        return ["server"]

    url = await ws_url()
    browser, sessions, ws = await C.connect(url)

    page, ready = await open_pod(browser, ws, sessions)
    C.check("開得起來：卡片已渲染", bool(ready), bool(ready), True)
    if not ready:
        return C.summary()

    fp = await fingerprint(page)
    ok_fp = bool(fp) and fp.get("core") and fp.get("api") and fp.get("app")
    C.check("來源已同步：三個 JS 都是 B1 版", ok_fp, fp, "core/api/app 皆 True")
    if not ok_fp:
        print("！來源未同步，中止（不要把後面的紅當成功能壞掉）", flush=True)
        return C.summary()

    # ── 1. 舊格式 deletions 讀得進來（既有集不能因為改格式就掉刪段） ──
    got_del = await red_keys(page)
    C.check(
        "1 舊集相容：yaml 的 deletions:[2] 仍渲染成紅卡",
        got_del == ["2"],
        got_del,
        ["2"],
    )

    # ── 2. 真點一次刪除鈕（含 elementFromPoint 疊層驗證）→ 紅卡變 {2,4} ──
    rect = await del_btn_center(page, ADD_IDX)
    C.check(f"2a 卡 #{ADD_IDX} 的刪除鈕找得到", bool(rect), bool(rect), True)
    if rect:
        top = await C.top_element_at(page, rect["cx"], rect["cy"])
        cls = top and top.get("cls")
        cls_s = cls if isinstance(cls, str) else ""
        hit = bool(top) and (
            "card-del" in cls_s or str(top.get("tag")) in ("svg", "path", "BUTTON")
        )
        C.check("2b 該座標最上層就是刪除鈕（沒被疊層吃掉）", hit, top, "card-del / 其 svg 子節點")
        await C.mouse_click(page, rect["cx"], rect["cy"])
        await asyncio.sleep(0.5)
    after = await red_keys(page)
    C.check("2c 點刪除後紅卡 = {2,4}", after == ["2", "4"], after, ["2", "4"])

    # ── 3. 存檔：yaml 只剩時間版 cuts，舊 deletions 被遷移掉 ──
    ok_save = await do_save(page)
    C.check("3a 存檔完成（按鈕回報已儲存 + yaml mtime 前進 + 無錯誤 toast）", ok_save, ok_save, True)
    y = read_yaml()
    C.check(
        "3b yaml 寫入時間版 cuts（磁碟軸，已減回 subtitle_offset 1.5s）",
        cuts_close(y.get("cuts"), WANT_CUTS),
        y.get("cuts"),
        WANT_CUTS,
    )
    C.check(
        "3c yaml 不再有 deletions（單一 source of truth）",
        "deletions" not in y,
        sorted(y.keys()),
        "無 deletions 鍵",
    )

    # ── 4. 重載 round-trip：cuts 換回同一組紅卡 ──
    page2, ready2 = await open_pod(browser, ws, sessions)
    C.check("4a 重載後卡片渲染出來", bool(ready2), bool(ready2), True)
    rt = await red_keys(page2)
    C.check("4b 重載後紅卡與存檔前相同", rt == ["2", "4"], rt, ["2", "4"])
    st = await C.js(page2, "document.querySelector('#status').textContent")
    C.check("4c 統計列顯示已刪 2", "已刪 2" in str(st), st, "含「已刪 2」")

    # ── 5. 注入一段換不回卡的 cut（影片模式在句中剪的）→ 要看得見、不可靜默 ──
    y2 = read_yaml()
    y2["cuts"] = sorted(
        [list(FOREIGN)] + [list(c) for c in y2.get("cuts", [])], key=lambda c: c[0]
    )
    write_yaml(y2)
    start_server()  # Episode.cfg 只在建構時讀 yaml，外部改檔必須重啟才看得到
    page3, ready3 = await open_pod(browser, ws, sessions)
    C.check("5a 帶 foreign cut 的集開得起來", bool(ready3), bool(ready3), True)
    st3 = await C.js(page3, "document.querySelector('#status').textContent")
    want_txt = "影片模式剪段 1 段（0.4s，本頁不可編輯）"
    C.check(
        "5b 統計列露出換不回卡的剪段（看不見＝靜默生效）",
        want_txt in str(st3),
        st3,
        f"含「{want_txt}」",
    )
    rt3 = await red_keys(page3)
    C.check(
        "5c foreign cut 沒有被擴寬成整張卡（紅卡仍是 {2,4}）",
        rt3 == ["2", "4"],
        rt3,
        ["2", "4"],
    )

    # ── 6. 再存一次檔：foreign cut 必須原樣回到 yaml（本編輯器不可吃掉別人剪的段） ──
    rect6 = await del_btn_center(page3, 6)
    C.check("6a 卡 #6 的刪除鈕找得到", bool(rect6), bool(rect6), True)
    if rect6:
        await C.mouse_click(page3, rect6["cx"], rect6["cy"])
        await asyncio.sleep(0.5)
    red6 = await red_keys(page3)
    C.check("6b 紅卡變 {2,4,6}", red6 == ["2", "4", "6"], red6, ["2", "4", "6"])
    ok_save3 = await do_save(page3)
    C.check("6c 第二次存檔完成（同上三訊號）", ok_save3, ok_save3, True)
    y3 = read_yaml()
    want6 = [[2.7, 3.9], list(FOREIGN), [7.35, 8.25], [11.05, 13.05]]
    C.check(
        "6d foreign cut 原樣保留，新刪的 #6 一併寫成時間段",
        cuts_close(y3.get("cuts"), want6),
        y3.get("cuts"),
        want6,
    )
    C.check(
        "6e 第二次存檔後仍無 deletions",
        "deletions" not in y3,
        sorted(y3.keys()),
        "無 deletions 鍵",
    )

    # ── 7. 後端合成端看到的剪除區間 = yaml 的 cuts（不是只有 yaml 長得對） ──
    repo = Path(__file__).resolve().parents[3]
    code = (
        "import sys, json, yaml;"
        f"sys.path.insert(0, {json.dumps(str(repo))});"
        "from podcast_toolkit.assemble import cut_intervals_from_cfg;"
        f"cfg = yaml.safe_load(open({json.dumps(str(YAML))}, encoding='utf-8'));"
        "print(json.dumps([list(x) for x in cut_intervals_from_cfg(cfg, [])]))"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    try:
        iv = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        iv = {"__err": (r.stderr or "")[-400:]}
    C.check(
        "7 合成端 cut_intervals_from_cfg 算出的區間 = yaml 的 cuts",
        cuts_close(iv, want6),
        iv,
        want6,
    )

    errs = [e for e in C.console_errors(page3) if "favicon" not in str(e)]
    C.check("8 全程無 console 例外", not errs, errs[:3], [])

    bad = C.summary()
    OUT.write_text(
        json.dumps(
            {"results": C.results_as_dict(), "failed": bad, "yaml_final": read_yaml()},
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"\n結果寫入 {OUT}", flush=True)
    await ws.close()
    return bad


if __name__ == "__main__":
    try:
        bad = asyncio.run(main())
    finally:
        stop_server()
    sys.exit(1 if bad else 0)
