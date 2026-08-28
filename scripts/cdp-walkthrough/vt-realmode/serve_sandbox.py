"""把 build_app 綁到沙盒集起在 8792，不寫 lockfile（不干擾使用者正在跑的 instance）。"""
import sys

sys.path.insert(0, "/Users/Mac365/conductor/workspaces/podcast-toolkit/colombo")

import uvicorn

from podcast_toolkit.episode import Episode
from podcast_toolkit.web.api import build_app

EP = "/private/tmp/pt-e2e/20260825 端對端測試"
app = build_app(Episode(EP), shutdown=lambda: None, _idle_threshold_sec=1e9)
uvicorn.run(app, host="127.0.0.1", port=8792, log_level="warning")
