"""Episode 物件：把集資料夾路徑 + 設定包起來。"""
from pathlib import Path
from podcast_toolkit import config


class Episode:
    SUBDIRS = {
        "master": "01_母帶",
        "intro_outro": "02_片頭片尾",
        "output": "03_成品",
        "work": "04_工作檔",
    }

    def __init__(self, episode_dir: Path):
        self.dir = Path(episode_dir).resolve()
        self.raw_episode = config.load_episode(self.dir)
        defaults = config.load_defaults()
        self.cfg = config.merge(defaults, self.raw_episode)

    @property
    def name(self) -> str:
        return self.cfg["name"]

    @property
    def date(self):
        return self.cfg["date"]

    def subdir(self, key: str) -> Path:
        return self.dir / self.SUBDIRS[key]

    def resolve_episode_path(self, rel: str) -> Path:
        """展開 {name} 後解析為絕對路徑（相對於集資料夾）"""
        expanded = config.expand_placeholders(rel, self.name)
        return self.dir / expanded

    def main_video(self) -> Path:
        return self.resolve_episode_path(self.cfg["main_video"])

    def main_srt(self) -> Path:
        return self.resolve_episode_path(self.cfg["main_srt"])

    def main_audio(self) -> Path:
        """Gemini 轉字幕的輸入音檔。預設找 01_母帶/ 內最新一個 .m4a / .mp3 / .wav。"""
        master = self.subdir("master")
        candidates = sorted(
            [p for p in master.glob("*") if p.suffix.lower() in (".m4a", ".mp3", ".wav")],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise FileNotFoundError(f"{master} 內找不到 .m4a / .mp3 / .wav 音檔")
        return candidates[0]

    def output_srt(self) -> Path:
        """Gemini 產出的 SRT，即 main_srt 預設指向的位置。"""
        return self.subdir("output") / f"{self.name}_final.srt"

    def output_v2_srt(self) -> Path:
        return self.subdir("output") / f"{self.name}_final_v2.srt"

    def output_yt_video(self) -> Path:
        return self.subdir("output") / f"{self.name}_YT完整版.mp4"

    def review_file(self) -> Path:
        return self.subdir("work") / "_resegment_review.txt"

    def asset_path(self, key: str) -> Path:
        """toolkit 共用資產（intro / outro / subscribe_card）絕對路徑"""
        rel = self.cfg["assets"][key]
        return config.toolkit_root() / rel
