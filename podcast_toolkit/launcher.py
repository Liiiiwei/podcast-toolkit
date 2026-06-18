"""macOS .app 入口：起 podcast server + 開瀏覽器。

py2app entry script 直接呼叫 main()。
與 podcast ui CLI 等價（共用 edit.run_dashboard）。
"""
from __future__ import annotations
import os
import sys
import traceback
from pathlib import Path

LOG_PATH = Path.home() / ".podcast-toolkit" / "launcher.log"


def _ensure_media_tools_on_path() -> None:
    """從 Finder / .app 啟動的進程走 launchd 的最小 PATH（沒有 shell 的 ~/.zshrc），
    `shutil.which("ffmpeg")` 與 bare `ffmpeg`/`ffprobe` 會找不到 → 上傳/合成/轉字幕全壞。
    把 (1) .app 內附的 ffmpeg 目錄（若有）(2) 常見 Homebrew/本地安裝路徑 前插 PATH，
    一次打通所有呼叫點（assemble / audio_align / vad_gate / web.transcribe / silencedetect）。"""
    candidates: list[str] = []
    here = Path(__file__).resolve().parent
    # 內附 ffmpeg：py2app 把 DATA_FILES 放進 .app/Contents/Resources/，launcher 也在那一帶
    for rel in ("bin", "../Resources/bin", "assets/bin", "../assets/bin"):
        d = (here / rel).resolve()
        if (d / "ffmpeg").exists():
            candidates.append(str(d))
    # launchd PATH 不含這些（裝過 install.sh 的 brew ffmpeg 在此）
    candidates += ["/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local" / "bin")]
    parts = os.environ.get("PATH", "").split(os.pathsep)
    new = [c for c in candidates if c and c not in parts]
    if new:
        os.environ["PATH"] = os.pathsep.join(new + parts)


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
        _ensure_media_tools_on_path()
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
