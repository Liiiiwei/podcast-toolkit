#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vt-ux2 走查用伺服器：支援 Range 的靜態伺服器＋假的 /api/*（真集模式）。

- --static 指定要服務的 static 目錄（預設＝本 repo 的 live static；突變測試會指到沙盒副本）
- 啟動後 stdout 第一行印「PORT=<數字>」
- 控制端點 /__ctl?fail=video,wave&delay=wave:600
    fail ：哪些來源要回 503（video／wave／subs／align）；demo 的 sample-* 檔與真集的
           /api/* 用同一組鍵，所以同一支走查能驗兩種模式
    delay：某來源延遲幾毫秒才回（讓「到達順序」跟畫面上的固定順序不一樣）
  每次呼叫都是整組覆寫；/__ctl 不帶參數＝全部清空。
- POST /api/save 會把對齊鍵套進記憶體裡的假集（不寫檔）並計數 → 走查驗得出
  「有沒有發出寫入」與「存了讀不讀得回」
    /__stat ：回 {saves: 寫入次數, episode: 目前的假集}
    /__reset：假集還原成預設值、寫入次數歸零
"""
import argparse
import copy
import json
import os
import re
import sys
import time
import http.server
import socketserver
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STATIC = os.path.normpath(
    os.path.join(HERE, "..", "..", "..", "podcast_toolkit", "web", "static"))

# 路徑 → 來源鍵
KEY_OF = {
    "/api/video": "video", "/sample-video.mp4": "video",
    "/api/waveform": "wave", "/sample-waveform.json": "wave",
    "/api/subtitles": "subs", "/sample-subtitles.json": "subs",
    "/api/episode": "align",
    "/icons.js": "icons",
}
# 真集端點 → 借用哪個示範檔當內容
API_FILE = {
    "/api/video": "sample-video.mp4",
    "/api/waveform": "sample-waveform.json",
    "/api/subtitles": "sample-subtitles.json",
}
# 假的 episode.yaml 對齊值：刻意都不是 0，回歸斷言才分得出「有讀到」與「預設值」
EPISODE = {
    "episode_dir": "/tmp/vt-ux2-fake-episode",
    "audio": {"path": "", "sync_offset": 0},
    "camera_sync_offset": {"b": 0.25},
    "head_trim_sec": 1.5,
    "tail_trim_sec": 0.75,
    "subtitle_offset_sec": 0,
}
EPISODE_DEFAULT = copy.deepcopy(EPISODE)
STAT = {"saves": 0}
CTL = {"fail": set(), "delay": {}}
ROOT = DEFAULT_STATIC


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        if path == "/__ctl":
            q = urllib.parse.parse_qs(u.query)
            CTL["fail"] = set(x for x in q.get("fail", [""])[0].split(",") if x)
            CTL["delay"] = {}
            for item in q.get("delay", [""])[0].split(","):
                if ":" in item:
                    k, ms = item.split(":")
                    CTL["delay"][k] = int(ms)
            return self._json(200, {"fail": sorted(CTL["fail"]), "delay": CTL["delay"]})

        if path == "/__stat":
            return self._json(200, {"saves": STAT["saves"], "episode": EPISODE})
        if path == "/__reset":
            EPISODE.clear()
            EPISODE.update(copy.deepcopy(EPISODE_DEFAULT))
            STAT["saves"] = 0
            return self._json(200, {"ok": True})

        key = KEY_OF.get(path)
        if key:
            ms = CTL["delay"].get(key)
            if ms:
                time.sleep(ms / 1000.0)
            if key in CTL["fail"]:
                return self._json(503, {"error": "vt-ux2 模擬失敗"})
        if path == "/api/episode":
            return self._json(200, EPISODE)
        if path in API_FILE:
            return self._file(os.path.join(ROOT, API_FILE[path]))
        if path.startswith("/api/"):
            return self._json(404, {"error": "not found"})
        fp = self.translate_path(path)
        if os.path.isfile(fp) and self.headers.get("Range"):
            return self._file(fp)
        return super().do_GET()

    def do_POST(self):
        # 「儲存對齊」會 POST /api/save；不寫任何檔，只套進記憶體裡的假集並計數。
        # 與後端 save_state 同樣是 key-presence 語意：沒帶的鍵不動。
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if urllib.parse.urlparse(self.path).path == "/api/save":
            try:
                d = json.loads(raw.decode("utf-8") or "{}")
            except ValueError:
                return self._json(400, {"error": "bad json"})
            STAT["saves"] += 1
            for k in ("head_trim_sec", "tail_trim_sec", "subtitle_offset_sec"):
                if k in d:
                    EPISODE[k] = d[k]
            if "camera_sync_offset_b" in d:
                EPISODE["camera_sync_offset"] = {"b": d["camera_sync_offset_b"]}
            if "audio" in d:
                EPISODE["audio"] = d["audio"]
            return self._json(200, {"ok": True})
        return self._json(404, {"error": "not found"})

    def _file(self, fp):
        """回整檔或 206 區間（python -m http.server 不回 206，影片會不可 seek）。"""
        if not os.path.isfile(fp):
            return self.send_error(404)
        size = os.path.getsize(fp)
        ctype = self.guess_type(fp)
        start, end, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)$", (self.headers.get("Range") or "").strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1) == "":
                start = max(0, size - int(m.group(2)))
            else:
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            if start >= size:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.end_headers()
                return
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(fp, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(65536, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    global ROOT
    ap = argparse.ArgumentParser()
    ap.add_argument("--static", default=DEFAULT_STATIC)
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args()
    ROOT = os.path.abspath(a.static)
    srv = Server(("127.0.0.1", a.port), Handler)
    print("PORT=%d" % srv.server_address[1])
    sys.stdout.flush()
    srv.serve_forever()


if __name__ == "__main__":
    main()
