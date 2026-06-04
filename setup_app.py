"""py2app 設定：把 podcast_toolkit 包成 macOS .app。

使用：
    python3 setup_app.py py2app -A      # alias mode（開發用，快）
    python3 setup_app.py py2app          # 正式打包（會把 Python runtime 也包進去）

產物：dist/Podcast.app
"""
from setuptools import setup

APP = ["podcast_toolkit/launcher.py"]
DATA_FILES = [
    ("podcast_toolkit/web/static", [
        "podcast_toolkit/web/static/index.html",
        "podcast_toolkit/web/static/app.css",
        "podcast_toolkit/web/static/app.js",
        "podcast_toolkit/web/static/dashboard.html",
        "podcast_toolkit/web/static/dashboard.css",
        "podcast_toolkit/web/static/dashboard.js",
    ]),
    ("podcast_toolkit/assets", []),  # 留位給未來 intro/outro 包進來
]
OPTIONS = {
    "argv_emulation": False,
    "packages": ["podcast_toolkit", "fastapi", "uvicorn", "pydantic", "starlette"],
    "includes": ["yaml"],
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
