#!/usr/bin/env python3
"""建 podcast 模式沙盒集：/private/tmp/pt-timeline-baseline/episode/20260601 時間軸基準集/

複用 podcast_toolkit/web/static/sample-video.mp4 + sample-subtitles.json（video-edit-prototype
demo 用的同一份素材），跨兩模式共用同一份時間資料方便比對。用 ffmpeg 從影片抽音軌
供 /api/waveform 的 main_audio() 落地找得到真實可解碼音檔。

冪等：目錄已存在就整個重建（trash 舊的），可重跑。
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
STATIC = REPO / "podcast_toolkit" / "web" / "static"
SANDBOX_ROOT = Path("/private/tmp/pt-timeline-baseline")
NAME = "時間軸基準集"
EP_DIR = SANDBOX_ROOT / "episode" / f"20260601 {NAME}"

FFMPEG = "/Users/Mac365/.local/bin/ffmpeg"


def sec_to_srt_ts(t: float) -> str:
    ms = round(t * 1000)
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build():
    if EP_DIR.exists():
        # 禁用 rm -rf／shutil.rmtree，一律用 trash CLI 清舊沙盒（即使是自建臨時目錄也不例外）。
        subprocess.run(["trash", str(EP_DIR)], check=True)
    for sub in ("01_母帶", "03_成品", "04_工作檔"):
        (EP_DIR / sub).mkdir(parents=True, exist_ok=True)

    src_video = STATIC / "sample-video.mp4"
    if not src_video.exists():
        sys.exit(f"找不到 {src_video}")
    dst_video = EP_DIR / "01_母帶" / f"{NAME}.mp4"
    shutil.copyfile(src_video, dst_video)

    dst_audio = EP_DIR / "01_母帶" / f"{NAME}.wav"
    r = subprocess.run(
        [FFMPEG, "-y", "-i", str(src_video), "-vn", "-ac", "1", "-ar", "48000", str(dst_audio)],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not dst_audio.exists():
        sys.exit(f"ffmpeg 抽音軌失敗：{r.stderr[-800:]}")

    subs = json.loads((STATIC / "sample-subtitles.json").read_text(encoding="utf-8"))["subs"]
    lines = []
    for i, s in enumerate(subs, 1):
        lines.append(str(i))
        lines.append(f"{sec_to_srt_ts(s['start'])} --> {sec_to_srt_ts(s['end'])}")
        lines.append(s["text"])
        lines.append("")
    srt_text = "\n".join(lines) + "\n"
    (EP_DIR / "03_成品" / f"{NAME}_final_v2.srt").write_text(srt_text, encoding="utf-8")
    # main_srt 指向的檔（episode.yaml 規定要有，即使編輯器讀的是 _v2）
    (EP_DIR / "03_成品" / f"{NAME}_final.srt").write_text(srt_text, encoding="utf-8")

    episode_yaml = f"""date: 20260601
name: {NAME}
main_video: "01_母帶/{{name}}.mp4"
main_srt: "03_成品/{{name}}_final.srt"
fixes: []
card_fixes: []
force_break: []
force_join: []
"""
    (EP_DIR / "episode.yaml").write_text(episode_yaml, encoding="utf-8")

    print(f"沙盒集建好：{EP_DIR}")
    print(f"  video={dst_video} ({dst_video.stat().st_size} bytes)")
    print(f"  audio={dst_audio} ({dst_audio.stat().st_size} bytes)")
    print(f"  subs={len(subs)} 句")
    return EP_DIR


if __name__ == "__main__":
    build()
