#!/usr/bin/env python3
"""獨立走查：補 verify_render_fingerprint.py 沒覆蓋到的 loading 態（骨架卡）。

為什麼要單獨一支：指紋護欄的 10 個狀態都是「卡片已經回來」之後的畫面，
renderCardSkeletons 只在「開集但卡片還沒回來」那一瞬間被呼叫——D6 抽檔時
它漏了 export，靜態核對抓到、指紋護欄一條都沒紅。這支把那個時點補上。

做法：直接 dynamic import /static/render.js 呼叫 renderCardSkeletons(3)，
同時證明 (1) 模組圖解得開（循環 import 沒把 render.js 弄壞）、
(2) 骨架真的畫進 #cards-list。

突變證據（2026-09-25 實跑）：把 render.js 的 `export function renderCardSkeletons`
改成 `function renderCardSkeletons`，本支即紅
（SyntaxError: does not provide an export named 'renderCardSkeletons'），
還原後回綠——正是 D6 當天漏掉的那個缺陷。

跑法：/usr/bin/python3 -u verify_render_skeleton_loading.py
前提：headless Chrome CDP 在 :9522（可用 CDP_PORT 覆寫）；本支自己起 serve_podcast.py。
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cdp_common as C

HERE = Path(__file__).resolve().parent
CDP_PORT = int(os.environ.get("CDP_PORT", "9522"))
POD_PORT = 8795
POD = f"http://127.0.0.1:{POD_PORT}/?pthook=1"


def ws_url():
    with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=5) as r:
        return json.loads(r.read())["webSocketDebuggerUrl"]


def port_busy(p):
    s = socket.socket()
    s.settimeout(0.3)
    try:
        s.connect(("127.0.0.1", p))
        return True
    except OSError:
        return False
    finally:
        s.close()


async def main():
    log = open(HERE / "verify_render_skeleton_loading_server.log", "ab")
    srv = subprocess.Popen([sys.executable, str(HERE / "serve_podcast.py")], stdout=log, stderr=log)
    for _ in range(120):
        if port_busy(POD_PORT):
            time.sleep(0.6)
            break
        time.sleep(0.25)

    ws = None
    try:
        browser, sessions, ws = await C.connect(ws_url())
        page = await C.open_page(browser, ws, sessions, POD, settle=2.5, width=1400, height=1600)
        r = await C.js(page, """(async () => {
          const m = await import('/static/render.js');
          if (typeof m.renderCardSkeletons !== 'function') return {err: '沒有匯出 renderCardSkeletons'};
          m.renderCardSkeletons(3);
          const list = document.querySelector('#cards-list');
          return {
            skel: list.querySelectorAll('.card-skeleton').length,
            spans: list.querySelectorAll('.card-skeleton > span').length,
          };
        })()""")
        got = r if isinstance(r, dict) else {"raw": r}
        C.check("LOAD-1 render.js 匯出 renderCardSkeletons 且模組圖解得開",
                got.get("skel"), got.get("skel"), 3)
        C.check("LOAD-2 骨架卡內部三段佔位都畫出來（3 張 × 3 段）",
                got.get("spans"), got.get("spans"), 9)
    finally:
        if ws:
            await ws.close()
        srv.terminate()
        srv.wait(timeout=10)
        log.close()

    bad = C.summary()
    (HERE / "verify_render_skeleton_loading_result.json").write_text(
        json.dumps(C.results_as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    asyncio.run(main())
