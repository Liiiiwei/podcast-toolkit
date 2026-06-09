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
    """組 ffmpeg subtitles filter 的 force_style 字串。
    alignment 為選用：ASS numpad 對應（2=底部置中預設、5=畫面正中、8=頂部置中）。"""
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
    if "alignment" in style and style["alignment"] is not None:
        parts.append(f"Alignment={style['alignment']}")
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


def shift_srt(src: Path, dst: Path, offset_sec: float) -> None:
    """把 SRT 時間軸整體位移 offset_sec 秒，寫到 dst。

    用途：外接音檔對齊。字幕原本是外接音檔時間軸，audio sync_offset 把
    外接音檔對齊到 cam A 後，字幕也要 shift -sync_offset 才能對到 cam A 時間軸。
    位移後 end<=0 的卡片整段被丟掉；start<0 則 clamp 到 0。
    """
    from podcast_toolkit import srt_io
    cards = srt_io.parse(src.read_text(encoding="utf-8"))
    shifted: list[dict] = []
    for c in cards:
        new_end = c["end"] + offset_sec
        if new_end <= 0:
            continue
        new_start = max(0.0, c["start"] + offset_sec)
        shifted.append({**c, "start": new_start, "end": new_end})
    dst.write_text(srt_io.serialize(shifted), encoding="utf-8")


def _write_ass_from_srt(src: Path, dst: Path, play_res_x: int, play_res_y: int) -> None:
    """轉 SRT → ASS 並寫入明確的 PlayResX/PlayResY。

    libass 對 SRT 預設 PlayResY=288，會把 MarginV/FontSize 用 frame_h/288 放大
    （MarginV=100 在 1080 frame 變成 374px from bottom），跟前端預覽的
    「字幕距裁切框底 8%」對不上。把 PlayResY 設為 output frame 高度後，
    MarginV=N 就等同於最終輸出的 N 像素，預覽 / 輸出一致。
    """
    from podcast_toolkit import srt_io

    def _fmt(t: float) -> str:
        t = max(0.0, float(t))
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t - h * 3600 - m * 60
        return f"{h}:{m:02d}:{s:05.2f}"

    cards = srt_io.parse(src.read_text(encoding="utf-8"))
    head = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        f"PlayResX: {play_res_x}\n"
        f"PlayResY: {play_res_y}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    rows = [head]
    for c in cards:
        text = (c["text"] or "").replace("\r\n", "\n").replace("\n", "\\N")
        rows.append(
            f"Dialogue: 0,{_fmt(c['start'])},{_fmt(c['end'])},Default,,0,0,0,,{text}\n"
        )
    dst.write_text("".join(rows), encoding="utf-8")


def _wm_overlay_params(wm_cfg: dict, res_w, res_h) -> tuple[int, str]:
    """從 watermark cfg 推導 (scale_width_px, overlay_xy_expr)。

    座標都是相對最終 frame（crop+scale 後的 final resolution），
    所以 res_w/res_h 用 final encode resolution 即可。
    """
    rw, rh = int(res_w), int(res_h)
    wm_w = int(rw * float(wm_cfg.get("width_pct", 0.11)))
    mx = int(rw * float(wm_cfg.get("margin_right_pct", 0.03)))
    my = int(rh * float(wm_cfg.get("margin_top_pct", 0.03)))
    pos = wm_cfg.get("position", "top-right")
    table = {
        "top-right": f"main_w-overlay_w-{mx}:{my}",
        "top-left": f"{mx}:{my}",
        "bottom-right": f"main_w-overlay_w-{mx}:main_h-overlay_h-{my}",
        "bottom-left": f"{mx}:main_h-overlay_h-{my}",
    }
    return wm_w, table.get(pos, table["top-right"])


def _wm_enabled(cfg: dict, wm_input_idx: int | None) -> bool:
    """watermark 是否真的會被燒進去。

    需要同時：cfg.watermark.enabled=true、有人傳了 wm_input_idx（代表 prepare_assembly 已驗證 logo 檔存在並把它加進 ffmpeg input list）。
    """
    if wm_input_idx is None:
        return False
    wm = cfg.get("watermark") or {}
    return bool(wm.get("enabled"))


def _build_audio_align_filter(sync_offset: float) -> str:
    """組外接音檔的對齊 filter prefix（接在 aformat 後、aselect 前）。

    與 audio_align 的 sign convention 一致：
    - 正值 offset = 外接音檔比 cam A 晚 X 秒 → 跳掉前 X 秒（atrim + reset PTS）
    - 負值 offset = 外接音檔比 cam A 早 |X| 秒 → 前面補 |X| 秒靜音（adelay）
    - 接近 0 → 不加任何 filter

    回傳的字串以逗號結尾，方便直接接在後續 filter 之前；不對齊則回 ""。
    """
    if abs(sync_offset) < 0.001:
        return ""
    if sync_offset > 0:
        return f"atrim=start={sync_offset:.3f},asetpts=PTS-STARTPTS,"
    delay_ms = int(round(-sync_offset * 1000))
    return f"adelay=delays={delay_ms}:all=1,"


def build_filter_complex_yt(
    cfg: dict,
    main_dur: float,
    srt_rel: str,
    deletion_intervals: list[tuple[float, float]] | None = None,
    wm_input_idx: int | None = None,
    audio_input_idx: int | None = None,
    audio_sync_offset: float = 0.0,
) -> str:
    """YT 16:9：原本的三段 concat（intro + main + outro card），讀 crop_yt。

    audio_input_idx：若提供，正片音訊改從該 input idx 取（外接音檔），
    並套用 audio_sync_offset 對齊；None = 用 cam A 原音。
    """
    enc = cfg["encode"]
    res_w, res_h = enc["resolution"].split("x")
    intro_dur = cfg["assets"]["intro_duration"]
    intro_fade_out = cfg["assets"]["intro_fade_out"]
    style_str = build_style_string(cfg["subtitle_style"])

    # main video 前處理 chain：crop_yt（源像素裁切）→ scale 到 1920×1080
    prep_part = _crop_part_str(cfg.get("crop_yt"), res_w, res_h)

    # 刪除區間：select / aselect filter（跳過 deletion 時間段）
    select_v, select_a = "", ""
    if deletion_intervals:
        ranges = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in deletion_intervals)
        select_v = f"select='not({ranges})',setpts=N/FRAME_RATE/TB,"
        select_a = f"aselect='not({ranges})',asetpts=N/SR/TB,"

    # 主音訊來源：外接音檔（含對齊）或 cam A 原音
    if audio_input_idx is not None:
        a_idx = audio_input_idx
        align_a = _build_audio_align_filter(audio_sync_offset)
    else:
        a_idx = 1
        align_a = ""

    # 字幕必須燒在最終 res_w×res_h frame 上（在 crop+scale 之後），否則
    # 字幕會以原片底部為基準算 MarginV，crop 後跟前端預覽（鎖在裁切框內）對不上。
    v1_pre = (
        f"[1:v]{prep_part}"
        f"subtitles={srt_rel}:force_style='{style_str}',"
        f"{select_v}setsar=1,fps={enc['framerate']},format={enc['pix_fmt']}"
    )
    v1_fade = (
        f"fade=t=in:st=0:d=0.5,fade=t=out:st={main_dur - 0.5}:d=0.5"
    )
    if _wm_enabled(cfg, wm_input_idx):
        wm_w, xy = _wm_overlay_params(cfg["watermark"], res_w, res_h)
        v1_chain = (
            f"{v1_pre}[v1pre];"
            f"[{wm_input_idx}:v]scale={wm_w}:-1[wm];"
            f"[v1pre][wm]overlay={xy},{v1_fade}[v1]"
        )
    else:
        v1_chain = f"{v1_pre},{v1_fade}[v1]"
    return (
        f"[0:v]scale={res_w}:{res_h},setsar=1,fps={enc['framerate']},"
        f"format={enc['pix_fmt']},fade=t=out:st={intro_dur - intro_fade_out}:d={intro_fade_out}[v0];"
        f"{v1_chain};"
        f"[2:v]scale={res_w}:{res_h},setsar=1,fps={enc['framerate']},"
        f"format={enc['pix_fmt']},fade=t=in:st=0:d=0.5[v2];"
        f"[0:a]aformat=sample_rates={enc['audio_sample_rate']}:channel_layouts=stereo,"
        f"afade=t=out:st={intro_dur - intro_fade_out}:d={intro_fade_out}[a0];"
        f"[{a_idx}:a]aformat=sample_rates={enc['audio_sample_rate']}:channel_layouts=stereo,"
        f"{align_a}{select_a}afade=t=in:st=0:d=0.5,afade=t=out:st={main_dur - 0.5}:d=0.5[a1];"
        f"[3:a]aformat=sample_rates={enc['audio_sample_rate']}:channel_layouts=stereo,"
        f"afade=t=in:st=0:d=0.5[a2];"
        f"[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[v][a]"
    )


def build_filter_complex_reels(
    cfg: dict,
    main_dur: float,
    srt_rel: str,
    deletion_intervals: list[tuple[float, float]] | None = None,
    audio_input_idx: int | None = None,
    audio_sync_offset: float = 0.0,
    wm_input_idx: int | None = None,
) -> str:
    """Reels 9:16：只有主影片，1080x1920，用 crop_reels。

    audio_input_idx：若提供，音訊改從該 input idx 取（外接音檔）並對齊；
    None = 用主影片原音。"""
    enc = cfg["encode"]
    res_w, res_h = 1080, 1920
    style_str = build_style_string(cfg.get("subtitle_style_reels") or cfg["subtitle_style"])

    # crop_reels 源像素裁切 → scale 到 1080×1920；無 crop 時純 scale
    prep_part = _crop_part_str(cfg.get("crop_reels"), res_w, res_h)

    select_v, select_a = "", ""
    if deletion_intervals:
        ranges = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in deletion_intervals)
        select_v = f"select='not({ranges})',setpts=N/FRAME_RATE/TB,"
        select_a = f"aselect='not({ranges})',asetpts=N/SR/TB,"

    # 主音訊來源：外接音檔（含對齊）或主影片原音
    if audio_input_idx is not None:
        a_idx = audio_input_idx
        align_a = _build_audio_align_filter(audio_sync_offset)
    else:
        a_idx = 0
        align_a = ""

    # 字幕燒在最終 1080×1920 frame 上（crop+scale 之後），對齊前端「字幕鎖在裁切框內」預覽。
    v_pre = (
        f"[0:v]{prep_part}"
        f"subtitles={srt_rel}:force_style='{style_str}',"
        f"{select_v}setsar=1,fps={enc['framerate']},format={enc['pix_fmt']}"
    )
    v_fade = (
        f"fade=t=in:st=0:d=0.5,fade=t=out:st={main_dur - 0.5}:d=0.5"
    )
    if _wm_enabled(cfg, wm_input_idx):
        wm_w, xy = _wm_overlay_params(cfg["watermark"], res_w, res_h)
        v_chain = (
            f"{v_pre}[vpre];"
            f"[{wm_input_idx}:v]scale={wm_w}:-1[wm];"
            f"[vpre][wm]overlay={xy},{v_fade}[v]"
        )
    else:
        v_chain = f"{v_pre},{v_fade}[v]"
    return (
        f"{v_chain};"
        f"[{a_idx}:a]aformat=sample_rates={enc['audio_sample_rate']}:channel_layouts=stereo,"
        f"{align_a}{select_a}afade=t=in:st=0:d=0.5,afade=t=out:st={main_dur - 0.5}:d=0.5[a]"
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
    prep_part: str,
    fps,
    fmt: str,
    setpts_prefix: str = "",
) -> str:
    """組單一鏡頭的前處理 chain（PTS 對齊 → crop/scale → 字幕 → 規格化）。

    cam_label='a' 或 'b'；輸出 label 為 [m_{cam_label}_v]。
    setpts_prefix 給 cam B 用來把 PTS 移到主時間軸（cam A 留空）。
    prep_part 已包含 crop（源像素）+ scale 到目標解析度的完整 chain。
    """
    # 字幕燒在最終裁切後 frame（crop+scale 之後），對齊前端「字幕鎖在裁切框內」預覽。
    return (
        f"[{src_idx}:v]{setpts_prefix}{prep_part}"
        f"subtitles={srt_rel}:force_style='{style_str}',"
        f"setsar=1,fps={fps},format={fmt}[m_{cam_label}_v]"
    )


def _multicam_segments(segments: list[dict]) -> tuple[list[str], str, str]:
    """組 per-segment trim + 必要時的 segment concat。

    回傳 (parts, main_v_in, main_a_in)。單段時直接用 seg_v_0/seg_a_0，
    多段才額外加 concat=n=N 進 main_v_raw/main_a_raw。

    [m_a_v]/[m_b_v]/[m_a_a] 多次引用時必須明確 split/asplit；ffmpeg 的
    auto-split 在這個 trim+concat 圖形下會吃掉幀導致主段截斷（只剩前 1-2 段）。
    """
    parts: list[str] = []
    n = len(segments)

    n_a_v = sum(1 for s in segments if s["cam"] == "a")
    n_b_v = sum(1 for s in segments if s["cam"] == "b")
    n_a_a = n  # 音訊全部走 cam A

    if n_a_v > 1:
        labels = "".join(f"[m_a_v_{i}]" for i in range(n_a_v))
        parts.append(f"[m_a_v]split={n_a_v}{labels}")
    if n_b_v > 1:
        labels = "".join(f"[m_b_v_{i}]" for i in range(n_b_v))
        parts.append(f"[m_b_v]split={n_b_v}{labels}")
    if n_a_a > 1:
        labels = "".join(f"[m_a_a_{i}]" for i in range(n_a_a))
        parts.append(f"[m_a_a]asplit={n_a_a}{labels}")

    cam_idx = {"a": 0, "b": 0}
    audio_idx = 0
    for i, seg in enumerate(segments):
        cam = seg["cam"]
        s, e = float(seg["start"]), float(seg["end"])
        n_cam = n_a_v if cam == "a" else n_b_v
        v_src = f"m_{cam}_v_{cam_idx[cam]}" if n_cam > 1 else f"m_{cam}_v"
        cam_idx[cam] += 1
        a_src = f"m_a_a_{audio_idx}" if n_a_a > 1 else "m_a_a"
        audio_idx += 1
        parts.append(
            f"[{v_src}]trim={s:.3f}:{e:.3f},setpts=PTS-STARTPTS[seg_v_{i}]"
        )
        # 音訊永遠取 cam A（多鏡頭只切畫面，聲音來源固定）
        parts.append(
            f"[{a_src}]atrim={s:.3f}:{e:.3f},asetpts=PTS-STARTPTS[seg_a_{i}]"
        )
    if n > 1:
        seg_labels = "".join(f"[seg_v_{i}][seg_a_{i}]" for i in range(n))
        parts.append(
            f"{seg_labels}concat=n={n}:v=1:a=1[main_v_raw][main_a_raw]"
        )
        return parts, "main_v_raw", "main_a_raw"
    return parts, "seg_v_0", "seg_a_0"


def _crop_part_str(crop: dict | None, res_w, res_h) -> str:
    """把源視訊轉到目標解析度的 prep chain（含 trailing comma）。

    crop 用 iw*W:ih*H:iw*X:ih*Y 表達式直接吃源像素，再 scale 到目標解析度。
    比舊的 scale→crop→scale 三段穩 — 源 aspect 跟目標不同時（例如 1920×1080
    源 + 1080×1920 Reels 目標）不會先被壓扁再裁。crop 不存在時只 scale。

    res_w / res_h 可傳 str 或 int（YT 用 cfg 字串 split、Reels 是 int）。
    """
    rw, rh = int(res_w), int(res_h)
    if not crop:
        return f"scale={rw}:{rh},"
    return (
        f"crop=iw*{crop['width']}:ih*{crop['height']}:"
        f"iw*{crop['x']}:ih*{crop['y']},scale={rw}:{rh},"
    )


def _cam_crop_parts(base_crop: dict | None, res_w, res_h) -> tuple[str, str]:
    """把 crop_yt / crop_reels 拆成 (prep_a, prep_b)：含 crop+scale 完整 prep chain。

    base_crop.b（optional dict）= cam B 獨立 crop；沒設就 fallback 用 base 給兩鏡頭。
    無 base_crop 時兩鏡頭都只 scale 到目標解析度（不裁切）。
    """
    if not base_crop:
        scale_only = _crop_part_str(None, res_w, res_h)
        return scale_only, scale_only
    crop_b = base_crop.get("b") or base_crop
    return _crop_part_str(base_crop, res_w, res_h), _crop_part_str(crop_b, res_w, res_h)


def build_filter_complex_yt_multicam(
    cfg: dict,
    main_dur: float,
    srt_rel: str,
    segments: list[dict],
    sync_offset_b: float = 0.0,
    audio_input_idx: int | None = None,
    audio_sync_offset: float = 0.0,
    wm_input_idx: int | None = None,
) -> str:
    """YT 雙鏡頭：[0]=intro, [1]=cam A, [2]=cam B, [3]=outro image, [4]=outro audio。

    Cam B 先 setpts 對齊主時間軸再燒字幕；音訊一律走 cam A（除非提供 audio_input_idx）。
    每段依 segments[i].cam 從 [m_a_v]/[m_b_v] trim 出來，最後與 intro/outro concat=n=3。

    audio_input_idx：若提供，主音訊改從該 input idx 取（外接音檔），
    並套用 audio_sync_offset 對齊；None = 走 cam A 原音。
    """
    enc = cfg["encode"]
    res_w, res_h = enc["resolution"].split("x")
    intro_dur = cfg["assets"]["intro_duration"]
    intro_fade_out = cfg["assets"]["intro_fade_out"]
    style_str = build_style_string(cfg["subtitle_style"])
    sr = enc["audio_sample_rate"]
    fmt = enc["pix_fmt"]
    fps = enc["framerate"]

    crop_part_a, crop_part_b = _cam_crop_parts(cfg.get("crop_yt"), res_w, res_h)

    # 主音訊來源：外接音檔（含對齊）或 cam A 原音
    if audio_input_idx is not None:
        a_idx_main = audio_input_idx
        align_a = _build_audio_align_filter(audio_sync_offset)
    else:
        a_idx_main = 1
        align_a = ""

    parts: list[str] = []
    # Cam A：不需 PTS 位移（主時間軸就是它的時間軸）
    parts.append(
        _multicam_cam_prep(1, "a", srt_rel, style_str, res_w, res_h, crop_part_a, fps, fmt)
    )
    parts.append(
        f"[{a_idx_main}:a]aformat=sample_rates={sr}:channel_layouts=stereo,{align_a}anull[m_a_a]"
    )
    # Cam B：先把 PTS 移到主時間軸，subtitles 之後讀到的時間才會對
    parts.append(
        _multicam_cam_prep(
            2, "b", srt_rel, style_str, res_w, res_h, crop_part_b, fps, fmt,
            setpts_prefix=f"setpts=PTS-{sync_offset_b}/TB,",
        )
    )

    seg_parts, main_v_in, main_a_in = _multicam_segments(segments)
    parts.extend(seg_parts)

    # main 段 fade in/out（若有 watermark，先 overlay 再 fade，讓淡入淡出帶到 logo）
    if _wm_enabled(cfg, wm_input_idx):
        wm_w, xy = _wm_overlay_params(cfg["watermark"], res_w, res_h)
        parts.append(f"[{wm_input_idx}:v]scale={wm_w}:-1[wm_main]")
        parts.append(
            f"[{main_v_in}][wm_main]overlay={xy},"
            f"fade=t=in:st=0:d=0.5,fade=t=out:st={main_dur - 0.5}:d=0.5[main_v]"
        )
    else:
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
    audio_input_idx: int | None = None,
    audio_sync_offset: float = 0.0,
    wm_input_idx: int | None = None,
) -> str:
    """Reels 雙鏡頭：[0]=cam A, [1]=cam B。1080×1920，無 intro/outro。

    audio_input_idx：若提供，音訊改從該 input idx 取（外接音檔）並對齊；
    None = 用 cam A 原音。
    """
    enc = cfg["encode"]
    res_w, res_h = 1080, 1920
    style_str = build_style_string(cfg.get("subtitle_style_reels") or cfg["subtitle_style"])
    sr = enc["audio_sample_rate"]
    fmt = enc["pix_fmt"]
    fps = enc["framerate"]

    crop_part_a, crop_part_b = _cam_crop_parts(cfg.get("crop_reels"), res_w, res_h)

    # 主音訊來源：外接音檔（含對齊）或 cam A 原音
    if audio_input_idx is not None:
        a_idx_main = audio_input_idx
        align_a = _build_audio_align_filter(audio_sync_offset)
    else:
        a_idx_main = 0
        align_a = ""

    parts: list[str] = []
    parts.append(
        _multicam_cam_prep(0, "a", srt_rel, style_str, res_w, res_h, crop_part_a, fps, fmt)
    )
    parts.append(
        f"[{a_idx_main}:a]aformat=sample_rates={sr}:channel_layouts=stereo,{align_a}anull[m_a_a]"
    )
    parts.append(
        _multicam_cam_prep(
            1, "b", srt_rel, style_str, res_w, res_h, crop_part_b, fps, fmt,
            setpts_prefix=f"setpts=PTS-{sync_offset_b}/TB,",
        )
    )

    seg_parts, main_v_in, main_a_in = _multicam_segments(segments)
    parts.extend(seg_parts)

    if _wm_enabled(cfg, wm_input_idx):
        wm_w, xy = _wm_overlay_params(cfg["watermark"], res_w, res_h)
        parts.append(f"[{wm_input_idx}:v]scale={wm_w}:-1[wm_main]")
        parts.append(
            f"[{main_v_in}][wm_main]overlay={xy},"
            f"fade=t=in:st=0:d=0.5,fade=t=out:st={main_dur - 0.5}:d=0.5[v]"
        )
    else:
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
    preview_sec: int | None = None,
) -> dict:
    """檢查資產 → 算出 ffmpeg 命令、cwd、輸出路徑、總時長。

    output_kind = 'yt' 或 'reels'：
      - yt：1920x1080，含 intro + outro card，用 crop_yt
      - reels：1080x1920,只含主影片，用 crop_reels

    preview_sec：若為正整數，ffmpeg 加 -t 截斷輸出長度（含 intro/正片/outro 全鏈路前 N 秒）；
    輸出檔名插入 .preview 避免覆蓋正式成品。

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

    # 雙鏡頭判斷：cameras.b 有設且 cam A/B 檔案都存在 → 走 multicam
    cam_b_rel = (cfg.get("cameras") or {}).get("b")
    multicam = bool(cam_b_rel)

    if multicam:
        cam_a_rel = cfg["cameras"]["a"]
        main_video = ep.resolve_episode_path(cam_a_rel)
        cam_b_video = ep.resolve_episode_path(cam_b_rel)
        if not main_video.exists():
            raise AssembleError(f"找不到 cam A：{main_video}", exit_code=3)
        if not cam_b_video.exists():
            raise AssembleError(f"找不到 cam B：{cam_b_video}", exit_code=3)
    else:
        main_video = ep.main_video()
        if not main_video.exists():
            raise AssembleError(f"找不到正片：{main_video}", exit_code=3)

    # 字幕：尊重 yaml srt_path override（cam-modal 手選），否則 fallback _v2 → 原 srt
    srt = ep.active_srt()
    if not srt.exists():
        srt = ep.output_v2_srt()
        if not srt.exists():
            srt = ep.main_srt()
            if not srt.exists():
                raise AssembleError("找不到字幕（srt_path / _v2 / 原 srt 都不存在）", exit_code=3)

    # 外接音檔（T64）：cfg.audio.path 有設且檔案存在 → 主音訊改走外接，並套 sync_offset 對齊
    # 字幕原本錄在外接音檔時間軸；先 shift -sync_offset 對到 cam A 時間軸後，
    # 後面的 deletion_intervals / segment_plan / ffmpeg subtitles= 才會用到同一份時間軸。
    audio_cfg = cfg.get("audio") or {}
    audio_file: Path | None = None
    audio_sync_offset = 0.0
    if audio_cfg.get("path"):
        audio_file = ep.resolve_episode_path(audio_cfg["path"])
        if not audio_file.exists():
            raise AssembleError(f"找不到外接音檔：{audio_file}", exit_code=3)
        audio_sync_offset = float(audio_cfg.get("sync_offset") or 0.0)
        if abs(audio_sync_offset) >= 0.001:
            shifted_srt = ep.subdir("work") / "_v2_aligned.srt"
            shift_srt(srt, shifted_srt, -audio_sync_offset)
            srt = shifted_srt

    # 輸出路徑分支
    if output_kind == "yt":
        out = ep.output_yt_video()
    else:
        out = ep.output_reels_video()

    # preview 模式：檔名插 .preview 避免覆蓋正式成品（驗證 bitrate 用，反覆跑不影響交付檔）
    if preview_sec and preview_sec > 0:
        out = out.with_name(f"{out.stem}.preview{out.suffix}")

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
                    f"共用資產缺失：{label} = {p}（請確認 toolkit/assets/ 內有對應檔案）",
                    exit_code=3,
                )

    # 量正片時長
    main_dur = ffprobe_duration(main_video)
    enc = cfg["encode"]

    # filter_complex：subtitles filter 路徑要相對 cwd，subprocess cwd 設為 03_成品/
    cwd = ep.subdir("output")
    main_rel = str(main_video.relative_to(cwd)) if main_video.is_relative_to(cwd) else str(main_video)

    # 把 SRT 轉成有明確 PlayResX/Y 的 ASS，PlayResY 設為輸出 frame 高，避免
    # libass 對 SRT 的預設 PlayResY=288 把 MarginV/FontSize 等比放大。
    if output_kind == "yt":
        ass_res_w, ass_res_h = (int(x) for x in enc["resolution"].split("x"))
    else:
        ass_res_w, ass_res_h = 1080, 1920
    ass_path = ep.subdir("work") / f"_v2_aligned_{ass_res_w}x{ass_res_h}.ass"
    _write_ass_from_srt(srt, ass_path, ass_res_w, ass_res_h)
    srt_rel = str(ass_path.relative_to(cwd)) if ass_path.is_relative_to(cwd) else str(ass_path)

    deletions = list(cfg.get("deletions") or [])
    head_trim = float(cfg.get("head_trim_sec") or 0)
    tail_trim = float(cfg.get("tail_trim_sec") or 0)

    # multicam 分流：segment plan 取代 deletion_intervals，已內建 deletions + 頭尾 trim
    segments: list[dict] = []
    sync_offset_b = 0.0
    if multicam:
        from podcast_toolkit import cameras_io, srt_io
        from podcast_toolkit.segment_plan import build_segment_plan

        cards = srt_io.parse(srt.read_text(encoding="utf-8"))
        cameras_mapping = cameras_io.load(ep.output_v2_cameras_json())
        segments = build_segment_plan(
            cards=cards,
            deletions=deletions,
            cameras_mapping=cameras_mapping,
            main_dur=main_dur,
            head_trim_sec=head_trim,
            tail_trim_sec=tail_trim,
        )
        # main_dur = 所有 keep 段加總（segment_plan 已扣 deletion + trim）
        main_dur = sum(s["end"] - s["start"] for s in segments)
        sync_offset_b = float((cfg.get("camera_sync_offset") or {}).get("b") or 0.0)
        # multicam 直接燒原 _v2.srt（trim 自動把被刪段的字幕一起切掉，不需 clean_srt）
        deletion_intervals = []
    else:
        # 處理 deletions + 頭尾 trim：算時間區間 + 寫一份過濾後的 srt 給 ffmpeg 燒字幕
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

    cam_b_rel_str = ""
    if multicam:
        cam_b_rel_str = (
            str(cam_b_video.relative_to(cwd)) if cam_b_video.is_relative_to(cwd) else str(cam_b_video)
        )

    # 外接音檔（T64）：audio_file / audio_sync_offset 已在 SRT shift 區塊驗證過，
    # 這裡只把 ffmpeg input 用的相對路徑算出來。
    audio_rel_str: str | None = None
    if audio_file is not None:
        audio_rel_str = (
            str(audio_file.relative_to(cwd)) if audio_file.is_relative_to(cwd) else str(audio_file)
        )

    # Watermark logo：cfg.watermark.enabled=true 且 assets.logo 指向的檔案實際存在才會 wire。
    # 兩條件任一不符 → wm_rel_str=None，後面 wm_input_idx 也是 None，filter 自動 no-op。
    wm_cfg = cfg.get("watermark") or {}
    wm_rel_str: str | None = None
    if wm_cfg.get("enabled"):
        try:
            logo_path = ep.asset_path("logo")
            if logo_path.exists():
                wm_rel_str = (
                    str(logo_path.relative_to(cwd)) if logo_path.is_relative_to(cwd) else str(logo_path)
                )
            else:
                print(f"⚠ watermark.enabled=true 但找不到 {logo_path}，自動跳過 overlay", file=sys.stderr)
        except KeyError:
            print("⚠ watermark.enabled=true 但 assets.logo 未設定，自動跳過 overlay", file=sys.stderr)

    if output_kind == "yt":
        intro_dur = cfg["assets"]["intro_duration"]
        outro_dur = cfg["assets"]["outro_duration"]
        if multicam:
            # yt multi inputs：intro(0) + camA(1) + camB(2) + outro_image(3) + outro_audio(4) → 外接音檔 = 5 → watermark = 5 or 6
            audio_input_idx = 5 if audio_rel_str else None
            wm_next = 6 if audio_rel_str else 5
            wm_input_idx = wm_next if wm_rel_str else None
            fc = build_filter_complex_yt_multicam(
                cfg, main_dur=main_dur, srt_rel=srt_rel,
                segments=segments, sync_offset_b=sync_offset_b,
                audio_input_idx=audio_input_idx,
                audio_sync_offset=audio_sync_offset,
                wm_input_idx=wm_input_idx,
            )
            cmd = [
                "ffmpeg", "-y",
                "-i", str(intro),
                "-i", main_rel,
                "-i", cam_b_rel_str,
                "-loop", "1", "-t", str(outro_dur), "-i", str(outro_image),
                "-i", str(outro_audio),
            ]
            if audio_rel_str:
                cmd += ["-i", audio_rel_str]
            if wm_rel_str:
                cmd += ["-i", wm_rel_str]
            cmd += [
                "-filter_complex", fc,
                "-map", "[v]", "-map", "[a]",
                "-c:v", enc["video_codec"],
                "-b:v", enc["video_bitrate"],
                "-maxrate", enc["video_maxrate"], "-bufsize", enc["video_bufsize"],
                "-preset", enc["preset"], "-pix_fmt", enc["pix_fmt"],
                "-c:a", enc["audio_codec"], "-b:a", enc["audio_bitrate"],
                "-ar", str(enc["audio_sample_rate"]),
                "-movflags", "+faststart",
                tmp_out_rel,
            ]
        else:
            # yt non-multi inputs：intro(0) + main(1) + outro_image(2) + outro_audio(3) → 外接音檔 = 4 → watermark = 4 or 5
            audio_input_idx = 4 if audio_rel_str else None
            wm_next = 5 if audio_rel_str else 4
            wm_input_idx = wm_next if wm_rel_str else None
            fc = build_filter_complex_yt(
                cfg, main_dur=main_dur, srt_rel=srt_rel,
                deletion_intervals=deletion_intervals,
                audio_input_idx=audio_input_idx,
                audio_sync_offset=audio_sync_offset,
                wm_input_idx=wm_input_idx,
            )
            cmd = [
                "ffmpeg", "-y",
                "-i", str(intro),
                "-i", main_rel,
                "-loop", "1", "-t", str(outro_dur), "-i", str(outro_image),
                "-i", str(outro_audio),
            ]
            if audio_rel_str:
                cmd += ["-i", audio_rel_str]
            if wm_rel_str:
                cmd += ["-i", wm_rel_str]
            cmd += [
                "-filter_complex", fc,
                "-map", "[v]", "-map", "[a]",
                "-c:v", enc["video_codec"],
                "-b:v", enc["video_bitrate"],
                "-maxrate", enc["video_maxrate"], "-bufsize", enc["video_bufsize"],
                "-preset", enc["preset"], "-pix_fmt", enc["pix_fmt"],
                "-c:a", enc["audio_codec"], "-b:a", enc["audio_bitrate"],
                "-ar", str(enc["audio_sample_rate"]),
                "-movflags", "+faststart",
                tmp_out_rel,
            ]
        total_dur = intro_dur + main_dur + outro_dur
    else:
        if multicam:
            # reels multi inputs：camA(0) + camB(1) → 外接音檔 = 2 → watermark = 2 or 3
            audio_input_idx = 2 if audio_rel_str else None
            wm_next = 3 if audio_rel_str else 2
            wm_input_idx = wm_next if wm_rel_str else None
            fc = build_filter_complex_reels_multicam(
                cfg, main_dur=main_dur, srt_rel=srt_rel,
                segments=segments, sync_offset_b=sync_offset_b,
                audio_input_idx=audio_input_idx,
                audio_sync_offset=audio_sync_offset,
                wm_input_idx=wm_input_idx,
            )
            cmd = [
                "ffmpeg", "-y",
                "-i", main_rel,
                "-i", cam_b_rel_str,
            ]
            if audio_rel_str:
                cmd += ["-i", audio_rel_str]
            if wm_rel_str:
                cmd += ["-i", wm_rel_str]
            cmd += [
                "-filter_complex", fc,
                "-map", "[v]", "-map", "[a]",
                "-c:v", enc["video_codec"],
                "-b:v", enc["video_bitrate"],
                "-maxrate", enc["video_maxrate"], "-bufsize", enc["video_bufsize"],
                "-preset", enc["preset"], "-pix_fmt", enc["pix_fmt"],
                "-c:a", enc["audio_codec"], "-b:a", enc["audio_bitrate"],
                "-ar", str(enc["audio_sample_rate"]),
                "-movflags", "+faststart",
                tmp_out_rel,
            ]
        else:
            # reels non-multi inputs：main(0) → 外接音檔 = 1 → watermark = 1 or 2
            audio_input_idx = 1 if audio_rel_str else None
            wm_next = 2 if audio_rel_str else 1
            wm_input_idx = wm_next if wm_rel_str else None
            fc = build_filter_complex_reels(
                cfg, main_dur=main_dur, srt_rel=srt_rel,
                deletion_intervals=deletion_intervals,
                audio_input_idx=audio_input_idx,
                audio_sync_offset=audio_sync_offset,
                wm_input_idx=wm_input_idx,
            )
            cmd = [
                "ffmpeg", "-y",
                "-i", main_rel,
            ]
            if audio_rel_str:
                cmd += ["-i", audio_rel_str]
            if wm_rel_str:
                cmd += ["-i", wm_rel_str]
            cmd += [
                "-filter_complex", fc,
                "-map", "[v]", "-map", "[a]",
                "-c:v", enc["video_codec"],
                "-b:v", enc["video_bitrate"],
                "-maxrate", enc["video_maxrate"], "-bufsize", enc["video_bufsize"],
                "-preset", enc["preset"], "-pix_fmt", enc["pix_fmt"],
                "-c:a", enc["audio_codec"], "-b:a", enc["audio_bitrate"],
                "-ar", str(enc["audio_sample_rate"]),
                "-movflags", "+faststart",
                tmp_out_rel,
            ]
        total_dur = main_dur

    # preview 模式：在 -movflags 前插 -t，截斷整段輸出（含 intro+正片+outro 全鏈路）為前 N 秒
    if preview_sec and preview_sec > 0:
        insert_at = cmd.index("-movflags")
        cmd[insert_at:insert_at] = ["-t", str(preview_sec)]
        total_dur = min(total_dur, float(preview_sec))

    return {
        "cmd": cmd,
        "cwd": cwd,
        "out": out,
        "tmp_out": tmp_out,
        "main_dur": main_dur,
        "total_dur": total_dur,
        "output_kind": output_kind,
    }


def _original_to_mp4_time(t_src: float, deletion_intervals: list[tuple[float, float]]) -> float:
    """source 時間軸 → rendered Reels mp4 時間軸（扣掉前面被刪掉的總長度）。

    rendered mp4 是把 deletion_intervals（含 head_trim/tail_trim）從 source 拿掉後 concat，
    所以 mp4 t = source_t - sum(被 source_t 之前刪掉的長度)。
    若 t_src 落在某段 deletion 內部 → 視為剛好在該段結尾，回傳 deletion 起點對應的 mp4 t。
    """
    deleted_before = 0.0
    for a, b in deletion_intervals:
        if b <= t_src:
            deleted_before += b - a
        elif a < t_src < b:
            deleted_before += t_src - a
            break
        else:
            break
    return max(0.0, t_src - deleted_before)


def extract_reels_clips(
    episode_dir: Path,
    clip_names: list[str] | None = None,
    force: bool = False,
) -> list[dict]:
    """從已合成的 Reels mp4 用 ffmpeg -c copy 切出 reels_clips 定義的片段。

    episode.yaml `reels_clips` 是 list of {name, start_card, end_card}；
    start/end_card 是 1-indexed 字幕卡編號（含頭含尾）。

    時間軸：rendered Reels mp4 已扣掉 deletions + head_trim + tail_trim，
    所以 clip 起訖時間要把原 SRT 時間換算到 mp4 時間軸（_original_to_mp4_time）。

    clip_names=None → 跑全部；給 list → 只跑指定 name。
    輸出寫到 03_成品/clips/{episode_name}_{clip_name}.mp4。

    multicam（cameras.b 有設）暫不支援：segment_plan 時間軸更複雜，
    需另寫換算邏輯，先保險 raise。
    """
    from podcast_toolkit import srt_io

    if not shutil.which("ffmpeg"):
        raise AssembleError("找不到 ffmpeg，請 brew install ffmpeg")

    ep = Episode(episode_dir)
    cfg = ep.cfg

    reels_clips = list(cfg.get("reels_clips") or [])
    if not reels_clips:
        raise AssembleError("episode.yaml 沒設 reels_clips；無片段可截取", exit_code=1)

    if (cfg.get("cameras") or {}).get("b"):
        raise AssembleError(
            "multicam 模式暫不支援 reels_clips（segment_plan 時間軸換算尚未實作）",
            exit_code=1,
        )

    reels_mp4 = ep.output_reels_video()
    if not reels_mp4.exists():
        raise AssembleError(
            f"找不到 Reels 母片：{reels_mp4}（請先跑 podcast assemble --kind reels）",
            exit_code=3,
        )

    srt_path = ep.active_srt()
    if not srt_path.exists():
        srt_path = ep.output_v2_srt()
        if not srt_path.exists():
            raise AssembleError("找不到字幕用以對應 card 時間", exit_code=3)

    cards = srt_io.parse(srt_path.read_text(encoding="utf-8"))
    by_idx = {c["idx"]: c for c in cards}

    deletions = list(cfg.get("deletions") or [])
    head_trim = float(cfg.get("head_trim_sec") or 0)
    tail_trim = float(cfg.get("tail_trim_sec") or 0)

    main_video = ep.main_video()
    if not main_video.exists():
        raise AssembleError(f"找不到正片以量總時長：{main_video}", exit_code=3)
    main_dur = ffprobe_duration(main_video)

    deletion_intervals = build_deletion_intervals(srt_path, deletions) if deletions else []
    if head_trim > 0:
        deletion_intervals.append((0.0, head_trim))
    if tail_trim > 0:
        deletion_intervals.append((main_dur - tail_trim, main_dur))
    deletion_intervals = sorted(deletion_intervals)

    if clip_names is not None:
        wanted = set(clip_names)
        existing = {c.get("name") for c in reels_clips}
        missing = wanted - existing
        if missing:
            raise AssembleError(f"找不到指定 clip：{sorted(missing)}", exit_code=1)
        to_run = [c for c in reels_clips if c.get("name") in wanted]
    else:
        to_run = reels_clips

    clips_dir = ep.subdir("output") / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    for clip in to_run:
        name = clip.get("name")
        if not name:
            raise AssembleError(f"clip 缺 name：{clip}", exit_code=1)
        start_card = int(clip.get("start_card", 0))
        end_card = int(clip.get("end_card", 0))
        start_c = by_idx.get(start_card)
        end_c = by_idx.get(end_card)
        if start_c is None:
            raise AssembleError(f"clip '{name}' start_card={start_card} 在 SRT 找不到", exit_code=1)
        if end_c is None:
            raise AssembleError(f"clip '{name}' end_card={end_card} 在 SRT 找不到", exit_code=1)
        if end_c["end"] <= start_c["start"]:
            raise AssembleError(
                f"clip '{name}' end_card 時間早於 start_card（{end_c['end']:.2f} <= {start_c['start']:.2f}）",
                exit_code=1,
            )

        t_mp4_start = _original_to_mp4_time(start_c["start"], deletion_intervals)
        t_mp4_end = _original_to_mp4_time(end_c["end"], deletion_intervals)

        out_path = clips_dir / f"{ep.name}_{name}.mp4"
        if out_path.exists() and not force:
            raise AssembleError(f"片段已存在：{out_path}（加 --force 覆寫）", exit_code=1)

        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{t_mp4_start:.3f}",
            "-to", f"{t_mp4_end:.3f}",
            "-i", str(reels_mp4),
            "-c", "copy",
            "-movflags", "+faststart",
            str(out_path),
        ]
        print(f"→ 截 clip '{name}'：mp4 {t_mp4_start:.2f}s–{t_mp4_end:.2f}s → {out_path}")
        subprocess.run(cmd, check=True)

        results.append({
            "name": name,
            "start_sec": t_mp4_start,
            "end_sec": t_mp4_end,
            "duration": t_mp4_end - t_mp4_start,
            "path": str(out_path),
        })

    return results


def run_clips(episode_dir: Path, clip_names: list[str] | None = None, force: bool = False) -> int:
    """CLI entry：podcast clip。clip_names=None → 跑全部。"""
    try:
        results = extract_reels_clips(episode_dir, clip_names=clip_names, force=force)
    except AssembleError as e:
        print(f"✗ {e}", file=sys.stderr)
        return e.exit_code
    except subprocess.CalledProcessError as e:
        print(f"✗ ffmpeg 失敗：exit {e.returncode}", file=sys.stderr)
        return 4

    for r in results:
        print(f"✅ {r['name']}：{r['duration']:.2f}s → {r['path']}")
    return 0


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
