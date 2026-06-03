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


def build_deletion_intervals(v2_srt_path: Path, deletions: list[int]) -> list[tuple[float, float]]:
    """讀 _v2.srt → 對應 deletion idx 的時間區間（秒）。"""
    from podcast_toolkit import srt_io
    if not deletions:
        return []
    cards = srt_io.parse(v2_srt_path.read_text(encoding="utf-8"))
    by_idx = {c["idx"]: c for c in cards}
    intervals = []
    for idx in deletions:
        c = by_idx.get(int(idx))
        if c is None:
            continue
        intervals.append((c["start"], c["end"]))
    intervals.sort()
    return intervals


def filter_deletion_srt(src: Path, dst: Path, deletions: list[int]) -> None:
    """把要刪除的字幕段拿掉，寫到 dst（idx 仍維持原樣，ffmpeg 不在意）。"""
    from podcast_toolkit import srt_io
    cards = srt_io.parse(src.read_text(encoding="utf-8"))
    deletion_set = {int(i) for i in deletions or []}
    kept = [c for c in cards if c["idx"] not in deletion_set]
    dst.write_text(srt_io.serialize(kept), encoding="utf-8")


def build_filter_complex_yt(
    cfg: dict,
    main_dur: float,
    srt_rel: str,
    deletion_intervals: list[tuple[float, float]] | None = None,
) -> str:
    """YT 16:9：原本的三段 concat（intro + main + outro card），讀 crop_yt。"""
    enc = cfg["encode"]
    res_w, res_h = enc["resolution"].split("x")
    intro_dur = cfg["assets"]["intro_duration"]
    intro_fade_out = cfg["assets"]["intro_fade_out"]
    style_str = build_style_string(cfg["subtitle_style"])

    # main video 前處理 chain：選擇性加 crop_yt
    crop = cfg.get("crop_yt")
    crop_part = ""
    if crop:
        # crop 比例 → px：依最終 resolution 換算（影片 scale 後一致）
        cw = int(int(res_w) * crop["width"])
        ch = int(int(res_h) * crop["height"])
        cx = int(int(res_w) * crop["x"])
        cy = int(int(res_h) * crop["y"])
        # crop 後 scale 回原解析度，否則和 intro/outro concat 時尺寸不符
        crop_part = f"crop={cw}:{ch}:{cx}:{cy},scale={res_w}:{res_h},"

    # 刪除區間：select / aselect filter（跳過 deletion 時間段）
    select_v, select_a = "", ""
    if deletion_intervals:
        ranges = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in deletion_intervals)
        select_v = f"select='not({ranges})',setpts=N/FRAME_RATE/TB,"
        select_a = f"aselect='not({ranges})',asetpts=N/SR/TB,"

    return (
        f"[0:v]scale={res_w}:{res_h},setsar=1,fps={enc['framerate']},"
        f"format={enc['pix_fmt']},fade=t=out:st={intro_dur - intro_fade_out}:d={intro_fade_out}[v0];"
        f"[1:v]subtitles={srt_rel}:force_style='{style_str}',"
        f"scale={res_w}:{res_h},{crop_part}{select_v}setsar=1,"
        f"fps={enc['framerate']},format={enc['pix_fmt']},"
        f"fade=t=in:st=0:d=0.5,fade=t=out:st={main_dur - 0.5}:d=0.5[v1];"
        f"[2:v]scale={res_w}:{res_h},setsar=1,fps={enc['framerate']},"
        f"format={enc['pix_fmt']},fade=t=in:st=0:d=0.5[v2];"
        f"[0:a]aformat=sample_rates={enc['audio_sample_rate']}:channel_layouts=stereo,"
        f"afade=t=out:st={intro_dur - intro_fade_out}:d={intro_fade_out}[a0];"
        f"[1:a]aformat=sample_rates={enc['audio_sample_rate']}:channel_layouts=stereo,"
        f"{select_a}afade=t=in:st=0:d=0.5,afade=t=out:st={main_dur - 0.5}:d=0.5[a1];"
        f"[3:a]aformat=sample_rates={enc['audio_sample_rate']}:channel_layouts=stereo,"
        f"afade=t=in:st=0:d=0.5[a2];"
        f"[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[v][a]"
    )


def build_filter_complex_reels(
    cfg: dict,
    main_dur: float,
    srt_rel: str,
    deletion_intervals: list[tuple[float, float]] | None = None,
) -> str:
    """Reels 9:16：只有主影片，1080x1920，用 crop_reels。"""
    enc = cfg["encode"]
    res_w, res_h = 1080, 1920
    style_str = build_style_string(cfg["subtitle_style"])

    crop = cfg.get("crop_reels")
    crop_part = ""
    if crop:
        cw = int(res_w * crop["width"])
        ch = int(res_h * crop["height"])
        cx = int(res_w * crop["x"])
        cy = int(res_h * crop["y"])
        # crop 後 scale 回 1080×1920，否則輸出尺寸會被 crop 縮成 432×1920 之類的怪比例
        crop_part = f"crop={cw}:{ch}:{cx}:{cy},scale={res_w}:{res_h},"

    select_v, select_a = "", ""
    if deletion_intervals:
        ranges = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in deletion_intervals)
        select_v = f"select='not({ranges})',setpts=N/FRAME_RATE/TB,"
        select_a = f"aselect='not({ranges})',asetpts=N/SR/TB,"

    return (
        f"[0:v]subtitles={srt_rel}:force_style='{style_str}',"
        f"scale={res_w}:{res_h},{crop_part}{select_v}setsar=1,"
        f"fps={enc['framerate']},format={enc['pix_fmt']},"
        f"fade=t=in:st=0:d=0.5,fade=t=out:st={main_dur - 0.5}:d=0.5[v];"
        f"[0:a]aformat=sample_rates={enc['audio_sample_rate']}:channel_layouts=stereo,"
        f"{select_a}afade=t=in:st=0:d=0.5,afade=t=out:st={main_dur - 0.5}:d=0.5[a]"
    )


# 保留舊名做相容呼叫（既有呼叫端預設走 YT 分支）
def build_filter_complex(cfg, main_dur, srt_rel, deletion_intervals=None):
    return build_filter_complex_yt(cfg, main_dur, srt_rel, deletion_intervals)


def _multicam_cam_prep(
    src_idx: int,
    cam_label: str,
    srt_rel: str,
    style_str: str,
    res_w: str | int,
    res_h: str | int,
    crop_part: str,
    fps,
    fmt: str,
    setpts_prefix: str = "",
) -> str:
    """組單一鏡頭的前處理 chain（PTS 對齊 → 字幕 → scale/crop → 規格化）。

    cam_label='a' 或 'b'；輸出 label 為 [m_{cam_label}_v]。
    setpts_prefix 給 cam B 用來把 PTS 移到主時間軸（cam A 留空）。
    """
    return (
        f"[{src_idx}:v]{setpts_prefix}subtitles={srt_rel}:force_style='{style_str}',"
        f"scale={res_w}:{res_h},{crop_part}setsar=1,fps={fps},format={fmt}[m_{cam_label}_v]"
    )


def _multicam_segments(segments: list[dict]) -> tuple[list[str], str, str]:
    """組 per-segment trim + 必要時的 segment concat。

    回傳 (parts, main_v_in, main_a_in)。單段時直接用 seg_v_0/seg_a_0，
    多段才額外加 concat=n=N 進 main_v_raw/main_a_raw。
    """
    parts: list[str] = []
    n = len(segments)
    for i, seg in enumerate(segments):
        cam = seg["cam"]
        s, e = float(seg["start"]), float(seg["end"])
        parts.append(
            f"[m_{cam}_v]trim={s:.3f}:{e:.3f},setpts=PTS-STARTPTS[seg_v_{i}]"
        )
        # 音訊永遠取 cam A（多鏡頭只切畫面，聲音來源固定）
        parts.append(
            f"[m_a_a]atrim={s:.3f}:{e:.3f},asetpts=PTS-STARTPTS[seg_a_{i}]"
        )
    if n > 1:
        seg_labels = "".join(f"[seg_v_{i}][seg_a_{i}]" for i in range(n))
        parts.append(
            f"{seg_labels}concat=n={n}:v=1:a=1[main_v_raw][main_a_raw]"
        )
        return parts, "main_v_raw", "main_a_raw"
    return parts, "seg_v_0", "seg_a_0"


def build_filter_complex_yt_multicam(
    cfg: dict,
    main_dur: float,
    srt_rel: str,
    segments: list[dict],
    sync_offset_b: float = 0.0,
) -> str:
    """YT 雙鏡頭：[0]=intro, [1]=cam A, [2]=cam B, [3]=outro image, [4]=outro audio。

    Cam B 先 setpts 對齊主時間軸再燒字幕；音訊一律走 cam A。
    每段依 segments[i].cam 從 [m_a_v]/[m_b_v] trim 出來，最後與 intro/outro concat=n=3。
    """
    enc = cfg["encode"]
    res_w, res_h = enc["resolution"].split("x")
    intro_dur = cfg["assets"]["intro_duration"]
    intro_fade_out = cfg["assets"]["intro_fade_out"]
    style_str = build_style_string(cfg["subtitle_style"])
    sr = enc["audio_sample_rate"]
    fmt = enc["pix_fmt"]
    fps = enc["framerate"]

    crop = cfg.get("crop_yt")
    crop_part = ""
    if crop:
        cw = int(int(res_w) * crop["width"])
        ch = int(int(res_h) * crop["height"])
        cx = int(int(res_w) * crop["x"])
        cy = int(int(res_h) * crop["y"])
        crop_part = f"crop={cw}:{ch}:{cx}:{cy},scale={res_w}:{res_h},"

    parts: list[str] = []
    # Cam A：不需 PTS 位移（主時間軸就是它的時間軸）
    parts.append(
        _multicam_cam_prep(1, "a", srt_rel, style_str, res_w, res_h, crop_part, fps, fmt)
    )
    parts.append(
        f"[1:a]aformat=sample_rates={sr}:channel_layouts=stereo[m_a_a]"
    )
    # Cam B：先把 PTS 移到主時間軸，subtitles 之後讀到的時間才會對
    parts.append(
        _multicam_cam_prep(
            2, "b", srt_rel, style_str, res_w, res_h, crop_part, fps, fmt,
            setpts_prefix=f"setpts=PTS-{sync_offset_b}/TB,",
        )
    )

    seg_parts, main_v_in, main_a_in = _multicam_segments(segments)
    parts.extend(seg_parts)

    # main 段 fade in/out
    parts.append(
        f"[{main_v_in}]fade=t=in:st=0:d=0.5,fade=t=out:st={main_dur - 0.5}:d=0.5[main_v]"
    )
    parts.append(
        f"[{main_a_in}]afade=t=in:st=0:d=0.5,afade=t=out:st={main_dur - 0.5}:d=0.5[main_a]"
    )

    # Intro / outro
    parts.append(
        f"[0:v]scale={res_w}:{res_h},setsar=1,fps={fps},format={fmt},"
        f"fade=t=out:st={intro_dur - intro_fade_out}:d={intro_fade_out}[v_intro]"
    )
    parts.append(
        f"[0:a]aformat=sample_rates={sr}:channel_layouts=stereo,"
        f"afade=t=out:st={intro_dur - intro_fade_out}:d={intro_fade_out}[a_intro]"
    )
    parts.append(
        f"[3:v]scale={res_w}:{res_h},setsar=1,fps={fps},format={fmt},"
        f"fade=t=in:st=0:d=0.5[v_outro]"
    )
    parts.append(
        f"[4:a]aformat=sample_rates={sr}:channel_layouts=stereo,"
        f"afade=t=in:st=0:d=0.5[a_outro]"
    )

    parts.append(
        "[v_intro][a_intro][main_v][main_a][v_outro][a_outro]"
        "concat=n=3:v=1:a=1[v][a]"
    )

    return ";".join(parts)


def build_filter_complex_reels_multicam(
    cfg: dict,
    main_dur: float,
    srt_rel: str,
    segments: list[dict],
    sync_offset_b: float = 0.0,
) -> str:
    """Reels 雙鏡頭：[0]=cam A, [1]=cam B。1080×1920，無 intro/outro。"""
    enc = cfg["encode"]
    res_w, res_h = 1080, 1920
    style_str = build_style_string(cfg["subtitle_style"])
    sr = enc["audio_sample_rate"]
    fmt = enc["pix_fmt"]
    fps = enc["framerate"]

    crop = cfg.get("crop_reels")
    crop_part = ""
    if crop:
        cw = int(res_w * crop["width"])
        ch = int(res_h * crop["height"])
        cx = int(res_w * crop["x"])
        cy = int(res_h * crop["y"])
        crop_part = f"crop={cw}:{ch}:{cx}:{cy},scale={res_w}:{res_h},"

    parts: list[str] = []
    parts.append(
        _multicam_cam_prep(0, "a", srt_rel, style_str, res_w, res_h, crop_part, fps, fmt)
    )
    parts.append(
        f"[0:a]aformat=sample_rates={sr}:channel_layouts=stereo[m_a_a]"
    )
    parts.append(
        _multicam_cam_prep(
            1, "b", srt_rel, style_str, res_w, res_h, crop_part, fps, fmt,
            setpts_prefix=f"setpts=PTS-{sync_offset_b}/TB,",
        )
    )

    seg_parts, main_v_in, main_a_in = _multicam_segments(segments)
    parts.extend(seg_parts)

    parts.append(
        f"[{main_v_in}]fade=t=in:st=0:d=0.5,fade=t=out:st={main_dur - 0.5}:d=0.5[v]"
    )
    parts.append(
        f"[{main_a_in}]afade=t=in:st=0:d=0.5,afade=t=out:st={main_dur - 0.5}:d=0.5[a]"
    )

    return ";".join(parts)


class AssembleError(RuntimeError):
    """assemble 任一階段失敗都丟這個；exit_code 給 CLI 對應退出碼。"""

    def __init__(self, message: str, exit_code: int = 4):
        super().__init__(message)
        self.exit_code = exit_code


def prepare_assembly(
    episode_dir: Path,
    output_kind: str = "yt",
    force: bool = False,
) -> dict:
    """檢查資產 → 算出 ffmpeg 命令、cwd、輸出路徑、總時長。

    output_kind = 'yt' 或 'reels'：
      - yt：1920x1080，含 intro + outro card，用 crop_yt
      - reels：1080x1920，只含主影片，用 crop_reels

    tmp_out 寫在 04_工作檔/.{out.name}.tmp，呼叫端跑完 ffmpeg 後負責 rename 到 03_成品/。
    回傳 dict：cmd / cwd / out / tmp_out / main_dur / total_dur / output_kind。
    """
    if output_kind not in ("yt", "reels"):
        raise AssembleError(f"未知 output_kind={output_kind}")

    if not shutil.which("ffmpeg"):
        raise AssembleError("找不到 ffmpeg，請 brew install ffmpeg")
    if not shutil.which("ffprobe"):
        raise AssembleError("找不到 ffprobe（隨 ffmpeg 安裝）")

    ep = Episode(episode_dir)
    cfg = ep.cfg

    main_video = ep.main_video()
    if not main_video.exists():
        raise AssembleError(f"找不到正片：{main_video}", exit_code=3)

    # 字幕：用 v2（resegment 輸出）優先，沒有就回退原 srt
    srt = ep.output_v2_srt()
    if not srt.exists():
        srt = ep.main_srt()
        if not srt.exists():
            raise AssembleError("找不到字幕（_v2 或原 srt）", exit_code=3)

    # 輸出路徑分支
    if output_kind == "yt":
        out = ep.output_yt_video()
    else:
        out = ep.output_reels_video()

    if out.exists() and not force:
        raise AssembleError(f"輸出已存在：{out}（加 --force 覆寫）", exit_code=1)

    # YT 才需要 intro / outro 共用資產
    if output_kind == "yt":
        intro = ep.asset_path("intro")
        outro_audio = ep.asset_path("outro_audio")
        outro_image = ep.asset_path("outro_image")
        for p, label in [(intro, "intro"), (outro_audio, "outro_audio"), (outro_image, "outro_image")]:
            if not p.exists():
                raise AssembleError(
                    f"共用資產缺失：{label} = {p}（跑 podcast relink {episode_dir} 試試）",
                    exit_code=3,
                )

    # 量正片時長
    main_dur = ffprobe_duration(main_video)
    enc = cfg["encode"]

    # filter_complex：subtitles filter 路徑要相對 cwd，subprocess cwd 設為 03_成品/
    cwd = ep.subdir("output")
    main_rel = str(main_video.relative_to(cwd)) if main_video.is_relative_to(cwd) else str(main_video)
    srt_rel = str(srt.relative_to(cwd)) if srt.is_relative_to(cwd) else str(srt)

    # 處理 deletions + 頭尾 trim：算時間區間 + 寫一份過濾後的 srt 給 ffmpeg 燒字幕
    deletions = list(cfg.get("deletions") or [])
    head_trim = float(cfg.get("head_trim_sec") or 0)
    tail_trim = float(cfg.get("tail_trim_sec") or 0)

    deletion_intervals = build_deletion_intervals(srt, deletions) if deletions else []
    if head_trim > 0:
        deletion_intervals.append((0.0, head_trim))
    if tail_trim > 0:
        deletion_intervals.append((main_dur - tail_trim, main_dur))
    deletion_intervals = sorted(deletion_intervals)

    if deletions:
        # 燒字幕：去掉刪除段，避免 select 後字幕時間錯位閃爍
        clean_srt = ep.subdir("work") / f"_v2_assembled_{output_kind}.srt"
        filter_deletion_srt(srt, clean_srt, deletions)
        srt = clean_srt
        srt_rel = str(srt.relative_to(cwd)) if srt.is_relative_to(cwd) else str(srt)

    if deletion_intervals:
        # main_dur 用於 fade-out 計時，扣掉刪除區間總長（含頭尾 trim）
        deleted_total = sum(b - a for a, b in deletion_intervals)
        main_dur = main_dur - deleted_total

    # tmp_out 寫在 work/，成功後由呼叫端 rename 到 out
    # 保留 .mp4 結尾，否則 ffmpeg 從 .tmp 副檔名無法判斷輸出格式
    tmp_out = ep.subdir("work") / f".{out.stem}.tmp{out.suffix}"
    tmp_out_rel = str(tmp_out.relative_to(cwd)) if tmp_out.is_relative_to(cwd) else str(tmp_out)

    if output_kind == "yt":
        intro_dur = cfg["assets"]["intro_duration"]
        outro_dur = cfg["assets"]["outro_duration"]
        fc = build_filter_complex_yt(cfg, main_dur=main_dur, srt_rel=srt_rel,
                                     deletion_intervals=deletion_intervals)
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
            tmp_out_rel,
        ]
        total_dur = intro_dur + main_dur + outro_dur
    else:
        fc = build_filter_complex_reels(cfg, main_dur=main_dur, srt_rel=srt_rel,
                                        deletion_intervals=deletion_intervals)
        cmd = [
            "ffmpeg", "-y",
            "-i", main_rel,
            "-filter_complex", fc,
            "-map", "[v]", "-map", "[a]",
            "-c:v", enc["video_codec"], "-crf", str(enc["crf"]),
            "-preset", enc["preset"], "-pix_fmt", enc["pix_fmt"],
            "-c:a", enc["audio_codec"], "-b:a", enc["audio_bitrate"],
            "-ar", str(enc["audio_sample_rate"]),
            "-movflags", "+faststart",
            tmp_out_rel,
        ]
        total_dur = main_dur

    return {
        "cmd": cmd,
        "cwd": cwd,
        "out": out,
        "tmp_out": tmp_out,
        "main_dur": main_dur,
        "total_dur": total_dur,
        "output_kind": output_kind,
    }


def run(episode_dir: Path, dry_run: bool = False, force: bool = False,
        output_kind: str = "yt") -> int:
    try:
        plan = prepare_assembly(episode_dir, output_kind=output_kind, force=force)
    except AssembleError as e:
        print(f"✗ {e}", file=sys.stderr)
        return e.exit_code

    cmd = plan["cmd"]
    cwd = plan["cwd"]
    out = plan["out"]
    tmp_out = plan["tmp_out"]

    if dry_run:
        print(f"# cwd: {cwd}")
        print(f"# main_duration: {plan['main_dur']}")
        print(" ".join(f"'{c}'" if " " in c or "[" in c else c for c in cmd))
        return 0

    print(f"→ 執行 ffmpeg（cwd={cwd}）")
    try:
        subprocess.run(cmd, cwd=cwd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"✗ ffmpeg 失敗：exit {e.returncode}", file=sys.stderr)
        return 4

    # 成功才把 tmp_out rename 到 out
    if tmp_out.exists():
        tmp_out.replace(out)

    print(f"✅ 完成：{out}")
    return 0
