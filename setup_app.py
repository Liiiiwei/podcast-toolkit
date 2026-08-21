"""py2app 設定：把 podcast_toolkit 包成 macOS .app。

使用：
    python3 setup_app.py py2app -A      # alias mode（開發用，快）
    python3 setup_app.py py2app          # 正式打包（會把 Python runtime 也包進去）

產物：dist/JOIN Podcast Toolkit.app
"""
import os
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
        # build 識別碼（磁碟端）：由 build_app.sh 每次打包產生，前端從磁碟即時讀來比對
        # 記憶體版本（/api/version），偵測「App 已更新但行程還是舊的」。漏了它前端探針失效。
        "podcast_toolkit/web/static/build-info.json",
        "podcast_toolkit/web/static/dashboard.html",
        "podcast_toolkit/web/static/dashboard.css",
        "podcast_toolkit/web/static/dashboard.js",
    ]),
    # defaults.yaml + assets → Resources 根（bundle 內 toolkit_root() = Contents/Resources）。
    # 少了它們：開單集會 load_defaults 找不到 defaults.yaml→500、合成找不到 intro/outro/封面。
    ("", ["defaults.yaml"]),
    # 開新集的範本（init.py:45/60 用 toolkit_root()/templates 讀）。少了它們：
    # 打包版開新集會 read_text 找不到→FileNotFoundError→前端「init 失敗」紅字。
    ("templates", ["templates/episode.yaml", "templates/TODO.md"]),
    ("assets", [
        "assets/intro.mp4",
        "assets/outro.mp3",
        "assets/subscribe_card.png",
        "assets/cover.png",
        "assets/intro_music.m4a",
    ]),
    # ffmpeg/ffprobe 內附（完全 turnkey 的 .app 用）：放一份 arm64 靜態 build（含 libass 燒字幕、
    # 0 個外部 dylib 依賴）到 assets/bin/ → launcher 的 PATH prepend 會優先用它（裝過 brew ffmpeg
    # 的使用者不放也能跑）。來源 osxexperts.net ffmpeg 7.1 arm64 靜態版。
    ("assets/bin", ["assets/bin/ffmpeg", "assets/bin/ffprobe"]),
]
OPTIONS = {
    "argv_emulation": False,
    # app 圖示：銀麥 3D 合成在深藍 macOS squircle 底（assets/AppIcon.icns）。
    # 換圖示只需重打包一次即生效（py2app 會把它設成 CFBundleIconFile）。
    "iconfile": "assets/AppIcon.icns",
    "packages": [
        "podcast_toolkit", "fastapi", "uvicorn", "pydantic", "starlette",
        "numpy", "requests", "opencc", "anyio", "h11", "click", "multipart",
        "charset_normalizer",  # requests 的字元偵測；少了它 requests 啟動噴 warning
        "jieba",  # word_break 中文斷詞（reflow 品質）；純 Python + dict.txt，不牽 torch
    ],
    "excludes": [
        # uvloop 的編譯擴充(uvloop.loop)py2app 打不乾淨 → 啟動 KeyError。排除它，
        # uvicorn 的 loops.auto 會自動退回 asyncio（localhost 單人 server 綽綽有餘）。
        "uvloop",
        # 轉錄在打包版一律走 Breeze subprocess（自帶 py-runtime + site-packages），
        # 主 app 不需要也不該內嵌 torch/mlx 這整包 ML 依賴。web/transcribe.py 的
        # mlx_whisper、word_break 的 jieba 都是 lazy import，但 py2app 的 modulegraph
        # 靜態走 AST，照樣把 mlx_whisper→torch→numba→llvmlite→scipy→sympy→huggingface_hub
        # →PyInstaller 整包拉進來（並炸在 PyInstaller 的 hook-gi GstGL）。全部排除；
        # bundle 內這些路徑是死碼（真要本地轉錄請用 Breeze sidecar）。
        "torch", "torchgen", "torchaudio", "torchvision",
        "mlx", "mlx_whisper", "whisper",
        "numba", "llvmlite", "scipy", "sympy",
        "huggingface_hub", "transformers", "safetensors",
        "PyInstaller", "pytest",
    ],
    # uvicorn/starlette 大量動態 import，modulegraph 常漏 → 正式（非 alias）build 必補：
    "includes": [
        "yaml", "eval_type_backport",
        "uvicorn.lifespan.on", "uvicorn.lifespan.off",
        "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    ],
    "plist": {
        # CFBundleName 同時決定 py2app 輸出的 .app 檔名（dist/<CFBundleName>.app）
        "CFBundleName": "JOIN Podcast Toolkit",
        "CFBundleDisplayName": "JOIN Podcast Toolkit",
        "CFBundleIdentifier": "com.liweisia.podcast-toolkit",
        # CFBundleVersion 帶成每次都變的 build 識別碼（給 Finder / crash log 認版），
        # 由 build_app.sh export BUILD_ID 帶進來；直接跑 py2app（無 build_app.sh）退回版號。
        # CFBundleShortVersionString 維持 marketing 版號不動（0.2.0）。
        "CFBundleVersion": os.environ.get("BUILD_ID", "0.2.0"),
        "CFBundleShortVersionString": "0.2.0",
        "LSUIElement": False,  # 顯示在 Dock
        "NSHighResolutionCapable": True,
        # Finder/launchd 啟動時環境沒有 LANG/LC_ALL → Python 3.9 locale 退回
        # US-ASCII，讀 ffmpeg stderr（含中文集名/路徑）時會 UnicodeDecodeError。
        # LaunchServices 會在啟動前注入這些變數，強制 Python UTF-8 模式（治本，
        # 一次免疫全 app 的 open()/subprocess 文字解碼）。
        "LSEnvironment": {
            "PYTHONUTF8": "1",
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
        },
    },
}

setup(
    name="JOIN Podcast Toolkit",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
