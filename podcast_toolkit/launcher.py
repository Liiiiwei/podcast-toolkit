"""macOS .app 入口：起 podcast server + 開瀏覽器。

py2app entry script 直接呼叫 main()。
與 podcast ui CLI 等價（共用 edit.run_dashboard）。
"""
from __future__ import annotations
import sys
import traceback
from pathlib import Path

LOG_PATH = Path.home() / ".podcast-toolkit" / "launcher.log"


def _alert(title: str, message: str) -> None:
    """跳原生 macOS 對話框。"""
    import subprocess
    script = f'display alert "{title}" message "{message}"'
    try:
        subprocess.run(["osascript", "-e", script], timeout=10)
    except Exception:
        pass


def _log_exception(exc: BaseException) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write("=" * 40 + "\n")
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)


def main() -> int:
    try:
        from podcast_toolkit import edit
        return edit.run_dashboard()
    except SystemExit:
        raise
    except BaseException as exc:
        _log_exception(exc)
        _alert(
            "Podcast Toolkit 啟動失敗",
            f"錯誤：{exc}\\n\\n詳細 log：{LOG_PATH}",
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
