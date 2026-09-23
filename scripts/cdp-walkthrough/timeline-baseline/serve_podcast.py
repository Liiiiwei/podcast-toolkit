#!/usr/bin/env python3
"""把 build_app 綁到 pt-timeline-baseline 沙盒集，起在 8795（不寫 lockfile、不干擾使用者
正在跑的 instance）。CONFIG_PATH/TYPO_DICT_PATH 導去沙盒目錄，避免污染使用者真實
~/.podcast-toolkit/config.json 的 recent_episodes（比照 tests/conftest.py 的
_isolate_user_config）。

沿用 scripts/cdp-walkthrough/vt-realmode/serve_sandbox.py 的慣例（README:22-27）。
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

import uvicorn

from podcast_toolkit.episode import Episode
from podcast_toolkit.web import api

SANDBOX_ROOT = Path("/private/tmp/pt-timeline-baseline")
EP = SANDBOX_ROOT / "episode" / "20260601 時間軸基準集"

# 隔離設定檔，別寫進使用者真實 config（tests/conftest.py:_isolate_user_config 同款做法）
api.CONFIG_PATH = SANDBOX_ROOT / "_pcfg_config.json"
api.TYPO_DICT_PATH = SANDBOX_ROOT / "_pcfg_typo.json"

# 2026-09-23 T0：曾因並行 session 動刀中，強制改服務 git HEAD 的 pristine 快照
# （head-snapshot/）以取得「動刀前」基準線。T1/T2/T3 起這個併發已結束、且本梯要
# 驗的正是 live working tree 的改動，所以恢復服務 api.STATIC_DIR 預設值
# （podcast_toolkit/web/static 原始碼樹，見 shared.py:resolve_static_dir，未凍結
# 打包時直接指到原始碼，不需複製/同步）。history-snapshot 分支保留不刪，供回頭
# 重驗 T0 pristine 基準之用。

app = api.build_app(Episode(EP), shutdown=lambda: None, _idle_threshold_sec=1e9)
uvicorn.run(app, host="127.0.0.1", port=8795, log_level="warning")
