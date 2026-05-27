"""podcast assemble：合成片頭 + 正片(燒字幕) + 片尾卡 → YT 完整版。

從現有 assemble.sh 改造，邏輯不變，ffmpeg 用 Python subprocess 呼叫。
"""
import shutil
import subprocess
import sys
from pathlib import Path
from podcast_toolkit.episode import Episode


def ffprobe_duration(path: Path) -> float:
    """用 ffprobe 量檔案時長（秒）"""
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def build_style_string(style: dict) -> str:
    """組 ffmpeg subtitles filter 的 force_style 字串"""
    parts = [
        f"FontName={style['font_name']}",
        f"FontSize={style['font_size']}",
        f"Bold={style['bold']}",
        f"PrimaryColour={style['primary_colour']}",
        f"OutlineColour={style['outline_colour']}",
        f"BorderStyle={style['border_style']}",
        f"Outline={style['outline']}",
        f"Shadow={style['shadow']}",
        f"MarginV={style['margin_v']}",
    ]
    return ",".join(parts)


def build_filter_complex(cfg: dict, main_dur: float, srt_rel: str) -> str:
    """組裝 ffmpeg filter_complex 字串（含 crop / deletions 支援）。"""
    enc = cfg["encode"]
    res_w, res_h = enc["resolution"].split("x")
    intro_dur = cfg["assets"]["intro_duration"]
    intro_fade_out = cfg["assets"]["intro_fade_out"]
    style_str = build_style_string(cfg["subtitle_style"])

    # main video 前處理 chain：選擇性加 crop
    crop = cfg.get("crop")
    crop_part = ""
    if crop:
        # crop 比例 → px：依最終 resolution 換算（影片 scale 後一致）
        cw = int(int(res_w) * crop["width"])
        ch = int(int(res_h) * crop["height"])
        cx = int(int(res_w) * crop["x"])
        cy = int(int(res_h) * crop["y"])
        crop_part = f"crop={cw}:{ch}:{cx}:{cy},"

    return (
        f"[0:v]scale={res_w}:{res_h},setsar=1,fps={enc['framerate']},"
        f"format={enc['pix_fmt']},fade=t=out:st={intro_dur - intro_fade_out}:d={intro_fade_out}[v0];"
        f"[1:v]subtitles={srt_rel}:force_style='{style_str}',"
        f"scale={res_w}:{res_h},{crop_part}setsar=1,"
        f"fps={enc['framerate']},format={enc['pix_fmt']},"
        f"fade=t=in:st=0:d=0.5,fade=t=out:st={main_dur - 0.5}:d=0.5[v1];"
        f"[2:v]scale={res_w}:{res_h},setsar=1,fps={enc['framerate']},"
        f"format={enc['pix_fmt']},fade=t=in:st=0:d=0.5[v2];"
        f"[0:a]aformat=sample_rates={enc['audio_sample_rate']}:channel_layouts=stereo,"
        f"afade=t=out:st={intro_dur - intro_fade_out}:d={intro_fade_out}[a0];"
        f"[1:a]aformat=sample_rates={enc['audio_sample_rate']}:channel_layouts=stereo,"
        f"afade=t=in:st=0:d=0.5,afade=t=out:st={main_dur - 0.5}:d=0.5[a1];"
        f"[3:a]aformat=sample_rates={enc['audio_sample_rate']}:channel_layouts=stereo,"
        f"afade=t=in:st=0:d=0.5[a2];"
        f"[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[v][a]"
    )


def run(episode_dir: Path, dry_run: bool = False, force: bool = False) -> int:
    if not shutil.which("ffmpeg"):
        print("✗ 找不到 ffmpeg，請 brew install ffmpeg", file=sys.stderr)
        return 4
    if not shutil.which("ffprobe"):
        print("✗ 找不到 ffprobe（隨 ffmpeg 安裝）", file=sys.stderr)
        return 4

    ep = Episode(episode_dir)
    cfg = ep.cfg

    main_video = ep.main_video()
    if not main_video.exists():
        print(f"✗ 找不到正片：{main_video}", file=sys.stderr)
        return 3

    # 字幕：用 v2（resegment 輸出）優先，沒有就回退原 srt
    srt = ep.output_v2_srt()
    if not srt.exists():
        srt = ep.main_srt()
        if not srt.exists():
            print(f"✗ 找不到字幕（_v2 或原 srt）", file=sys.stderr)
            return 3

    intro = ep.asset_path("intro")
    outro_audio = ep.asset_path("outro_audio")
    outro_image = ep.asset_path("outro_image")

    for p, label in [(intro, "intro"), (outro_audio, "outro_audio"), (outro_image, "outro_image")]:
        if not p.exists():
            print(f"✗ 共用資產缺失：{label} = {p}", file=sys.stderr)
            print(f"  跑 podcast relink {episode_dir} 試試", file=sys.stderr)
            return 3

    out = ep.output_yt_video()
    if out.exists() and not force:
        print(f"✗ 輸出已存在：{out}", file=sys.stderr)
        print(f"  加 --force 覆寫", file=sys.stderr)
        return 1

    # 量正片時長
    main_dur = ffprobe_duration(main_video)
    intro_dur = cfg["assets"]["intro_duration"]
    intro_fade_out = cfg["assets"]["intro_fade_out"]
    outro_dur = cfg["assets"]["outro_duration"]

    enc = cfg["encode"]

    # filter_complex：與 assemble.sh 等效
    # 注意：ffmpeg subtitles filter 路徑要相對 cwd，所以 subprocess cwd 設為 03_成品/
    # srt 也要傳檔名（相對路徑），不是絕對路徑
    cwd = ep.subdir("output")
    main_rel = str(main_video.relative_to(cwd)) if main_video.is_relative_to(cwd) else str(main_video)
    srt_rel = str(srt.relative_to(cwd)) if srt.is_relative_to(cwd) else str(srt)

    fc = build_filter_complex(cfg, main_dur=main_dur, srt_rel=srt_rel)

    out_rel = str(out.relative_to(cwd))

    cmd = [
        "ffmpeg", "-y",
        "-i", str(intro),
        "-i", main_rel,
        "-loop", "1", "-t", str(outro_dur), "-i", str(outro_image),
        "-i", str(outro_audio),
        "-filter_complex", fc,
        "-map", "[v]", "-map", "[a]",
        "-c:v", enc["video_codec"], "-crf", str(enc["crf"]),
        "-preset", enc["preset"], "-pix_fmt", enc["pix_fmt"],
        "-c:a", enc["audio_codec"], "-b:a", enc["audio_bitrate"],
        "-ar", str(enc["audio_sample_rate"]),
        "-movflags", "+faststart",
        out_rel,
    ]

    if dry_run:
        print(f"# cwd: {cwd}")
        print(f"# main_duration: {main_dur}")
        print(" ".join(f"'{c}'" if " " in c or "[" in c else c for c in cmd))
        return 0

    print(f"→ 執行 ffmpeg（cwd={cwd}）")
    try:
        subprocess.run(cmd, cwd=cwd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"✗ ffmpeg 失敗：exit {e.returncode}", file=sys.stderr)
        return 4

    print(f"✅ 完成：{out}")
    return 0
