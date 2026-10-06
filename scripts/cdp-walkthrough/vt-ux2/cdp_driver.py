#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可重複使用的 CDP（Chrome DevTools Protocol）headless 驅動器。

用途：給驗收 agent 實測影片剪輯原型頁面的版面與互動。只用標準庫
（socket / urllib / 自寫 WebSocket frame），不依賴任何 pip 套件。

已知坑（照 lessons-learned 做對）：
1. 舊 user-data-dir 的媒體快取會毀損，導致影片永遠載不完（readyState 卡住）。
   → 每次啟動都用「全新且唯一」的 --user-data-dir，結束時 trash 掉（不用 rm -rf）。
2. 測到的若是舊副本，症狀長得像「功能沒生效」。
   → 載入後先斷言「版本指紋」（頁面上只有 live 版才有的字串），不符立即中止。
3. 影片 seek 需要伺服器支援 Range；本驅動器搭配 range_server.py 使用。
4. python 一律 -u 執行（避免全緩衝看不到卡在哪）；限時用 curl -m（本機無 GNU timeout）。

CLI：
  python3 -u cdp_driver.py --url <URL> [--height 900] [--width 1280]
         [--probe-file <path> | --probe-js '<expr>'] [--expect '<指紋子字串>']
         [--chrome <path>] [--nav-timeout 30] [--keep-profile]

輸出：stdout 印一個 JSON 物件：
  {"fingerprint_ok": bool, "expect": "...", "probe": <probe 回傳值或 null>,
   "error": null 或錯誤字串}
fingerprint 不符或發生錯誤時 exit code != 0。
"""

import argparse
import base64
import json
import os
import re
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

DEFAULT_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# 預設版本指紋：live 版 video-edit-prototype.html 的標題關鍵字
DEFAULT_EXPECT = "時間軸線性剪輯"


def log(msg):
    """診斷訊息走 stderr，stdout 只留最終 JSON。"""
    sys.stderr.write("[cdp] %s\n" % msg)
    sys.stderr.flush()


# ───────────────────────── 最小 WebSocket client（純標準庫）─────────────────────────
class WS:
    """只實作 CDP 需要的部分：client 送 masked text frame、收 server 的
    unmasked frame，處理分片與 ping/pong 控制訊框。"""

    def __init__(self, url, timeout=30):
        assert url.startswith("ws://"), "只支援 ws://"
        rest = url[len("ws://"):]
        hostport, _, path = rest.partition("/")
        host, _, port_s = hostport.partition(":")
        port = int(port_s or 80)
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._inbuf = b""
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            "GET /%s HTTP/1.1\r\n"
            "Host: %s:%d\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ) % (path, host, port, key)
        self.sock.sendall(req.encode())
        # 讀完握手回應（直到空行）；剩餘 bytes 留進 _inbuf 備用
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket 握手時連線中斷")
            data += chunk
        head, _, leftover = data.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError("WebSocket 握手失敗：%r" % head[:120])
        self._inbuf = leftover

    def _recv_exact(self, n):
        while len(self._inbuf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("socket 已關閉")
            self._inbuf += chunk
        out, self._inbuf = self._inbuf[:n], self._inbuf[n:]
        return out

    def send(self, msg):
        data = msg.encode("utf-8")
        header = bytearray([0x81])  # FIN + opcode text
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def recv(self):
        """回傳一則完整文字訊息（自動拼接分片、吃掉 ping/pong）。"""
        frags = bytearray()
        while True:
            b1 = self._recv_exact(1)[0]
            fin = b1 & 0x80
            opcode = b1 & 0x0F
            b2 = self._recv_exact(1)[0]
            masked = b2 & 0x80
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else None
            payload = self._recv_exact(length) if length else b""
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:  # close
                raise ConnectionError("WebSocket 被對端關閉")
            if opcode == 0x9:  # ping → 回 pong
                self._send_control(0xA, payload)
                continue
            if opcode == 0xA:  # pong
                continue
            frags += payload
            if fin:
                return frags.decode("utf-8", "replace")

    def _send_control(self, opcode, payload=b""):
        header = bytearray([0x80 | opcode])
        mask = os.urandom(4)
        header.append(0x80 | len(payload))
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def close(self):
        try:
            self._send_control(0x8)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


# ───────────────────────── CDP 連線包裝 ─────────────────────────
class CDP:
    def __init__(self, ws_url, timeout=30):
        self.ws = WS(ws_url, timeout=timeout)
        self._id = 0

    def call(self, method, params=None, timeout=30):
        """送一個 CDP 命令，讀到對應 id 的回應才回傳（其間的 event 直接丟棄）。"""
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while True:
            if time.time() > deadline:
                raise TimeoutError("CDP 命令逾時：%s" % method)
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("CDP 錯誤 %s：%s" % (method, msg["error"]))
                return msg.get("result", {})
            # 其餘是 event，本驅動器用 readyState 輪詢判載入，不靠 event，直接丟棄

    def evaluate(self, expression, await_promise=True, timeout=30):
        """在頁面 context 執行 JS，回傳 by-value 結果。拋 JS 例外會帶出描述。"""
        res = self.call("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": await_promise,
            "returnByValue": True,
            "userGesture": True,
        }, timeout=timeout)
        if res.get("exceptionDetails"):
            ex = res["exceptionDetails"]
            desc = ex.get("exception", {}).get("description") or ex.get("text")
            raise RuntimeError("JS 例外：%s" % desc)
        return res.get("result", {}).get("value")

    def close(self):
        self.ws.close()


# ───────────────────────── Chrome 啟動 / 連線 ─────────────────────────
def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def launch_chrome(chrome, url, width, height, debug_port, profile_dir):
    args = [
        chrome,
        "--headless=new",
        "--remote-debugging-port=%d" % debug_port,
        "--user-data-dir=%s" % profile_dir,  # 每次全新且唯一，避免媒體快取毀損
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--disable-extensions",
        "--disable-background-networking",
        "--window-size=%d,%d" % (width, height),
        "--autoplay-policy=no-user-gesture-required",
        url,
    ]
    log("啟動 Chrome：port=%d profile=%s" % (debug_port, profile_dir))
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc


def wait_devtools(debug_port, timeout=20):
    """輪詢 /json/version 直到 devtools 起來，回傳 version JSON。"""
    deadline = time.time() + timeout
    url = "http://127.0.0.1:%d/json/version" % debug_port
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last_err = e
            time.sleep(0.2)
    raise TimeoutError("devtools 未就緒：%s" % last_err)


def get_page_ws(debug_port, timeout=20):
    """從 /json/list 取出 type=page 的 webSocketDebuggerUrl。"""
    deadline = time.time() + timeout
    url = "http://127.0.0.1:%d/json/list" % debug_port
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                targets = json.loads(r.read().decode())
            for t in targets:
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.2)
    raise TimeoutError("找不到 page 類型的 devtools target")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--probe-file")
    ap.add_argument("--probe-js")
    ap.add_argument("--expect", default=DEFAULT_EXPECT,
                    help="版本指紋子字串，會在 document.title + outerHTML 裡找")
    ap.add_argument("--chrome", default=DEFAULT_CHROME)
    ap.add_argument("--nav-timeout", type=int, default=30)
    ap.add_argument("--keep-profile", action="store_true",
                    help="保留 user-data-dir（預設結束時 trash 掉）")
    args = ap.parse_args()

    out = {"fingerprint_ok": False, "expect": args.expect, "probe": None, "error": None}

    # 每次全新且唯一的 profile 目錄
    profile_dir = tempfile.mkdtemp(prefix="pt-cdp-profile-", dir="/private/tmp")
    proc = None
    cdp = None
    try:
        debug_port = free_port()
        proc = launch_chrome(args.chrome, args.url, args.width, args.height,
                             debug_port, profile_dir)
        wait_devtools(debug_port)
        ws_url = get_page_ws(debug_port)
        cdp = CDP(ws_url, timeout=args.nav_timeout)

        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        # 以 Emulation 鎖死版面視窗尺寸（比 --window-size 在 headless 更可靠）
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": args.width, "height": args.height,
            "deviceScaleFactor": 1, "mobile": False,
        })
        # 明確導航（即使啟動帶了 URL，再導一次確保 state 乾淨、可預期）
        cdp.call("Page.navigate", {"url": args.url}, timeout=args.nav_timeout)

        # 輪詢 readyState 直到 complete；不靠 event，避免分片/順序複雜度
        deadline = time.time() + args.nav_timeout
        state = None
        while time.time() < deadline:
            try:
                state = cdp.evaluate("document.readyState", await_promise=False, timeout=5)
            except Exception:
                state = None
            if state == "complete":
                break
            time.sleep(0.2)
        log("readyState=%s" % state)

        # 版本指紋斷言：title 或整頁 outerHTML 裡要找得到 expect
        fp = cdp.evaluate(
            "(function(){var s=(document.title||'')+'\\n'+"
            "(document.documentElement?document.documentElement.outerHTML:'');"
            "return s.indexOf(%s)>=0;})()" % json.dumps(args.expect),
            await_promise=False, timeout=10)
        out["fingerprint_ok"] = bool(fp)
        if not fp:
            out["error"] = "版本指紋未命中（服務到的可能是舊副本或錯誤頁面）"
            print(json.dumps(out, ensure_ascii=False))
            sys.exit(3)

        # 執行 probe（若有）
        probe_js = None
        if args.probe_file:
            with open(args.probe_file, "r", encoding="utf-8") as f:
                probe_js = f.read()
        elif args.probe_js:
            probe_js = args.probe_js
        if probe_js:
            out["probe"] = cdp.evaluate(probe_js, await_promise=True, timeout=args.nav_timeout)

        print(json.dumps(out, ensure_ascii=False))
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
        print(json.dumps(out, ensure_ascii=False))
        sys.exit(1)
    finally:
        if cdp:
            cdp.close()
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        # 清掉一次性 profile：用 trash CLI（不用 rm -rf）
        if not args.keep_profile and os.path.isdir(profile_dir):
            try:
                subprocess.run(["trash", profile_dir], check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass


if __name__ == "__main__":
    main()
