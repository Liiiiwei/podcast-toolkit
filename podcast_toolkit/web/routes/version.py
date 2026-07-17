"""版本探針：回報「這個正在跑的行程」是哪一版 build。

用途：搭配前端 20 秒心跳偵測「App 已更新、但行程還是更新前的舊行程」。
py2app 打包版沒有 auto-reload —— Python 模組在 import 當下就編進記憶體，
磁碟換了記憶體不會換。前端拿這個記憶體版本，跟磁碟上的 build-info.json
（NoCacheStaticFiles 即時送、永遠最新）比對，不一致就代表行程過期。

關鍵（本方案最容易寫錯的一點）：build_id 必須取自 **import 進記憶體的**
podcast_toolkit._build_info，**絕對不可以在這裡當場讀磁碟上的 build-info.json**。
那份磁碟檔在 App 更新後會立刻變新，後端若也讀磁碟就永遠跟前端一致、探針失效。
這裡回的是「凍進這個行程記憶體」的值，才能反映行程本身的新舊。
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from podcast_toolkit.web.shared import RouteContext

try:
    # 由 build_app.sh 於每次打包時產生；import 當下即「凍」進記憶體。
    from podcast_toolkit._build_info import BUILD_ID as _BUILD_ID
except ImportError:
    # 開發樹（沒跑過 build）沒有這個檔；用 "dev" 佔位，前端比對照樣運作。
    _BUILD_ID = "dev"


def register(app: FastAPI, ctx: RouteContext) -> None:
    @app.get("/api/version")
    def get_version():
        # 注意：回傳記憶體常數 _BUILD_ID，不要在這裡讀檔（讀檔會拿到磁碟新版，探針失效）。
        return JSONResponse({"build_id": _BUILD_ID})
