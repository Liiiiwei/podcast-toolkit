"""py2app 設定：把 podcast_toolkit 包成 macOS .app。

使用：
    python3 setup_app.py py2app -A      # alias mode（開發用，快）
    python3 setup_app.py py2app          # 正式打包（會把 Python runtime 也包進去）

產物：dist/Podcast.app
"""
import sys

from setuptools import setup

# py2app 的 modulegraph 用遞迴遍歷 AST，遇到 pydantic/fastapi 這種大模組會爆預設遞迴上限
# （RecursionError）。建置前拉高，是官方/社群公認的 workaround。
sys.setrecursionlimit(10000)

APP = ["podcast_toolkit/launcher.py"]
DATA_FILES = [
    ("podcast_toolkit/web/static", [
        "podcast_toolkit/web/static/index.html",
        "podcast_toolkit/web/static/app.css",
        "podcast_toolkit/web/static/app.js",
        "podcast_toolkit/web/static/icons.js",       # window.Icons；漏了它正式 build 會破圖
        "podcast_toolkit/web/static/dashboard.html",
        "podcast_toolkit/web/static/dashboard.css",
        "podcast_toolkit/web/static/dashboard.js",
    ]),
    # defaults.yaml + assets → Resources 根（bundle 內 toolkit_root() = Contents/Resources）。
    # 少了它們：開單集會 load_defaults 找不到 defaults.yaml→500、合成找不到 intro/outro/封面。
    ("", ["defaults.yaml"]),
    ("assets", [
        "assets/intro.mp4",
        "assets/outro.mp3",
        "assets/subscribe_card.png",
        "assets/cover.png",
        "assets/intro_music.m4a",
    ]),
    # ffmpeg/ffprobe 內附（完全 turnkey 的 .app 用）：放一份 arm64+videotoolbox 靜態 build 到
    # assets/bin/，再取消下一行註解 → launcher 的 PATH prepend 會優先用它（裝過 brew ffmpeg
    # 的使用者不放也能跑）。
    # ("assets/bin", ["assets/bin/ffmpeg", "assets/bin/ffprobe"]),
]
OPTIONS = {
    "argv_emulation": False,
    "packages": [
        "podcast_toolkit", "fastapi", "uvicorn", "pydantic", "starlette",
        "numpy", "requests", "opencc", "anyio", "h11", "click", "multipart",
        "charset_normalizer",  # requests 的字元偵測；少了它 requests 啟動噴 warning
    ],
    # uvloop 的編譯擴充(uvloop.loop)py2app 打不乾淨 → 啟動 KeyError。排除它，
    # uvicorn 的 loops.auto 會自動退回 asyncio（localhost 單人 server 綽綽有餘）。
    "excludes": ["uvloop"],
    # uvicorn/starlette 大量動態 import，modulegraph 常漏 → 正式（非 alias）build 必補：
    "includes": [
        "yaml", "eval_type_backport",
        "uvicorn.lifespan.on", "uvicorn.lifespan.off",
        "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    ],
    "plist": {
        "CFBundleName": "Podcast",
        "CFBundleDisplayName": "Podcast Toolkit",
        "CFBundleIdentifier": "com.liweisia.podcast-toolkit",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "LSUIElement": False,  # 顯示在 Dock
        "NSHighResolutionCapable": True,
    },
}

setup(
    name="Podcast",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
