#!/usr/bin/env python3
# 支援 HTTP Range 的靜態伺服器：python -m http.server 不回 206，影片會變成不可 seek。
# 邏輯照抄 scripts/cdp-walkthrough/vt-proto/range_server.py（README:79-88 的斷言紀律同源），
# 差別只有 ROOT 可由環境變數指定 —— 原版 ROOT 寫死是「腳本自己所在目錄」，
# 沒辦法拿來服務 podcast_toolkit/web/static/（video-edit-prototype.js 的相對路徑
# sample-video.mp4 等資源必須跟 html 同目錄才解析得到），故另開一支，不改動原檔。
import http.server, os, re, socketserver

ROOT = os.environ.get("ROOT") or os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8796"))

class RangeHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def do_GET(self):
        rng = self.headers.get("Range")
        path = self.translate_path(self.path)
        if not rng or not os.path.isfile(path):
            return super().do_GET()
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        if not m:
            return super().do_GET()
        size = os.path.getsize(path)
        start = int(m.group(1)) if m.group(1) else 0
        end = int(m.group(2)) if m.group(2) else size - 1
        end = min(end, size - 1)
        if start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        length = end - start + 1
        ctype = self.guess_type(path)
        self.send_response(206)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break
                remaining -= len(chunk)

class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

if __name__ == "__main__":
    with Server(("127.0.0.1", PORT), RangeHandler) as httpd:
        print(f"serving {ROOT} on http://127.0.0.1:{PORT}", flush=True)
        httpd.serve_forever()
