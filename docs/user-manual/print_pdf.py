#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 docs/user-manual/index.html 印成 PDF。

改一次手冊就要重印一次 PDF，參數不固定下來每次都得重想。這支刻意住在手冊旁邊：
紙張 Letter（612x792pt，與 7/31 那版一致）、printBackground=true（不開的話 callout
三色和程式碼底色會整片消失，等於印出另一份文件）。

用 CDP 而不是 `--print-to-pdf` CLI，是因為要在送印前確認 24 張圖都載完 ——
headless 不等圖，圖沒載完就印，PDF 裡是一排空白框。
"""
import asyncio
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

import websockets

# 這支就住在 docs/user-manual/ 裡，一律同目錄取檔，不去推算 repo root ——
# 之前放在 _scratch/ 時是往上兩層找，換位置就會算錯。
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "index.html")
OUT = os.path.join(HERE, "podcast-toolkit-user-manual.pdf")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9333


def log(msg):
    print(msg, flush=True)


def launch_chrome():
    proc = subprocess.Popen(
        [
            CHROME,
            "--remote-debugging-port=%d" % PORT,
            "--headless=new",
            "--no-first-run",
            "--no-default-browser-check",
            "--allow-file-access-from-files",
            "--user-data-dir=/private/tmp/cdp-profile-pdf",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 25
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:%d/json/list" % PORT, timeout=2
            ) as r:
                pages = [t for t in json.load(r) if t.get("type") == "page"]
            if pages:
                return proc, pages[0]["webSocketDebuggerUrl"]
        except Exception:
            time.sleep(0.4)
    proc.terminate()
    raise RuntimeError("Chrome 起不來")


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self._id = 0

    async def send(self, method, **params):
        self._id += 1
        mid = self._id
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("%s -> %s" % (method, msg["error"]))
                return msg.get("result", {})

    async def evaluate(self, expr):
        r = await self.send(
            "Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True
        )
        if r.get("exceptionDetails"):
            raise RuntimeError("JS 例外：%s" % json.dumps(r["exceptionDetails"])[:300])
        return r.get("result", {}).get("value")


async def main():
    if not os.path.isfile(SRC):
        raise SystemExit("找不到手冊：%s" % SRC)
    url = "file://" + urllib.request.pathname2url(SRC)
    proc, ws_url = launch_chrome()
    try:
        async with websockets.connect(ws_url, max_size=None) as ws:
            cdp = CDP(ws)
            await cdp.send("Page.enable")
            await cdp.send("Runtime.enable")
            await cdp.send("Emulation.setEmulatedMedia", media="print")
            await cdp.send("Page.navigate", url=url)

            # 等 DOM + 全部圖片解碼完成；沒載完就印，PDF 裡會是一排空白框
            deadline = time.time() + 60
            stat = None
            while time.time() < deadline:
                try:
                    stat = await cdp.evaluate(
                        "(()=>{const i=[...document.images];"
                        "return {ready:document.readyState,"
                        "total:i.length,"
                        "done:i.filter(x=>x.complete&&x.naturalWidth>0).length};})()"
                    )
                except RuntimeError:
                    stat = None
                if stat and stat["ready"] == "complete" and stat["total"] == stat["done"]:
                    break
                await asyncio.sleep(0.5)
            log("載入狀態：%s" % stat)
            if not stat or stat["total"] != stat["done"] or stat["total"] == 0:
                raise SystemExit("圖片沒載完（%s），停手不印" % stat)

            await asyncio.sleep(1.0)  # 讓字型 reflow 落定
            r = await cdp.send(
                "Page.printToPDF",
                printBackground=True,
                paperWidth=8.5,
                paperHeight=11,
                marginTop=0.4,
                marginBottom=0.4,
                marginLeft=0.4,
                marginRight=0.4,
                preferCSSPageSize=False,
                # 依 h1/h2/h3 自動產生多層 PDF 書籤大綱（Chrome 原生開關，不影響版面）
                generateDocumentOutline=True,
            )
            data = base64.b64decode(r["data"])
            with open(OUT, "wb") as f:
                f.write(data)
            log("✅ %s  %d bytes" % (OUT, len(data)))
    finally:
        proc.terminate()


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
