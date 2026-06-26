"""podcast assemble：合成片頭 + 正片(燒字幕) + 片尾卡 → YT 完整版。

從現有 assemble.sh 改造，邏輯不變，ffmpeg 用 Python subprocess 呼叫。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from podcast_toolkit.episode import Episode


def _has_subtitles_filter(ffmpeg_path: str) -> bool:
    """該 ffmpeg build 有沒有 subtitles 濾鏡（= 有沒有編進 libass）。"""
    try:
        out = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-h", "filter=subtitles"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return False
    return "Filter subtitles" in out


@lru_cache(maxsize=1)
def ffmpeg_bin() -> str:
    """挑「真的有 subtitles 濾鏡（libass）」的 ffmpeg，不靠 PATH 順序。

    踩雷：Homebrew 的 ffmpeg 常沒 --enable-libass（無 subtitles 濾鏡），而啟動器/launcher
    把 /opt/homebrew/bin 前插 PATH → bare "ffmpeg" 會選到它 → 燒字幕合成整條 filtergraph
    爆「No such filter: subtitles」。逐一挑含 subtitles 的 build；都沒有才退回 PATH 上的 ffmpeg
    （非燒字幕的操作仍可跑）。"""
    candidates: list[str] = []
    which = shutil.which("ffmpeg")
    if which:
        candidates.append(which)
    candidates += [
        str(Path.home() / ".local" / "bin" / "ffmpeg"),
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
    ]
    seen: set[str] = set()
    for c in candidates:
        if not c or c in seen or not Path(c).exists():
            continue
        seen.add(c)
        if _has_subtitles_filter(c):
            return c
    return which or "ffmpeg"


def ffprobe_duration(path: Path) -> float:
    """用 ffprobe 量檔案時長（秒）"""
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def escape_filter_path(path: str) -> str:
    """脫逸要嵌進 filter_complex 的檔案路徑（如 subtitles= 的檔名）。

    ffmpeg 解析分兩層：先 filtergraph 層（' \\ [ ] , ; 是特殊字元），
    再 filter 參數層（: ' \\ 是特殊字元），所以特殊字元要脫逸兩次。
    集名含空格、引號、逗號、中括號時，沒脫逸會直接破壞整條 filter。
    """
    s = str(path)
    # 參數層：\ ' : 先各補一個反斜線
    s = s.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
    # filtergraph 層：對上一層結果再脫逸一次
    return "".join("\\" + ch if ch in "\\'[],;" else ch for ch in s)


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


def _pad_and_merge_cuts(
    intervals: list[tuple[float, float]], v2_cards: list[dict], pad: float
) -> list[tuple[float, float]]:
    """把每個刪除區間往前後各「最多」吃掉 pad 秒的邊緣雜音（換氣/吸氣/底噪）；
    但**夾在保留卡的語音邊界內**——絕不咬進要保留的語音（含部分被切的卡）。pad<=0 → 原樣返回。

    保留卡 = 未被任一刪段「整段涵蓋」的卡（部分被切的卡仍算保留）。連刪數張、中間沒有保留卡
    語音時間隙也一起砍；夾著保留卡（含 flush-adjacent：保留卡 start==前段尾）則不併。
    pad = 每側「最多」吃的雜音秒數（左右各自獨立 cap、pad 不跨側挪用）；每側殘留 = 該側間隙 −
    min(該側間隙, pad)：某側間隙 > pad 處留下自然停頓（不壓平，刻意的大停頓會被尊重），≤ pad 處
    該側整段吃掉。預設小值（0.15s）留呼吸、不趕；給很大 → 吃滿到鄰卡邊界（殘留 0、貼死，會趕）。
    某一側沒有鄰卡（片頭/片尾側）→ 該側不外吃（頭尾留給 head/tail trim 處理）。
    """
    intervals = sorted(intervals)
    if pad <= 0 or not intervals:
        return intervals  # 關閉 → 維持原本逐卡區間（向後相容、逐位元等同 baseline）

    # 正規化：修反置 (start>end)、丟零長度（手動 cuts 打錯時的防呆）
    norm = sorted((min(a, b), max(a, b)) for a, b in intervals if a != b)
    if not norm:
        return []

    cards = [(float(c["start"]), float(c["end"])) for c in v2_cards]
    # 保留卡 = 沒被任一刪段「整段涵蓋」的卡；部分被切的卡仍算保留（存活語音不可被 pad 吃掉）
    kept = [
        (cs, ce) for cs, ce in cards
        if not any(s - 1e-6 <= cs and ce <= e + 1e-6 for s, e in norm)
    ]

    # 先併「相鄰刪段之間沒有保留卡語音」的區間（連刪 → 中間間隙一起砍）。判定「間隙有保留卡」
    # = 有保留卡與間隙 (pe, s) 重疊（cs < s 且 ce > pe）→ flush-adjacent（cs==pe、連續字幕常態）也擋得下。
    pre = [list(norm[0])]
    for s, e in norm[1:]:
        pe = pre[-1][1]
        gap_has_kept = any(cs < s - 1e-6 and ce > pe + 1e-6 for cs, ce in kept)
        if s <= pe + 1e-6 or not gap_has_kept:
            pre[-1][1] = max(pe, e)
        else:
            pre.append([s, e])

    # 各段往前後延伸 pad，夾在保留卡語音邊界內（部分被切的卡用 min(ce,s)/max(cs,e) 夾住存活語音）。
    # 某側沒有鄰卡 → 該側界 = s/e 本身（不往片頭/片尾外吃）。
    out: list[tuple[float, float]] = []
    for s, e in pre:
        lefts = [min(ce, s) for cs, ce in kept if cs < s - 1e-6]
        rights = [max(cs, e) for cs, ce in kept if ce > e + 1e-6]
        left_limit = max(lefts) if lefts else s
        right_limit = min(rights) if rights else e
        ns = max(s - pad, left_limit, 0.0)
        ne = min(e + pad, right_limit)
        out.append((ns, max(ne, ns)))

    # 延伸後若重疊/相鄰 → 合併
    out.sort()
    merged = [out[0]]
    for s, e in out[1:]:
        ps, pe = merged[-1]
        if s <= pe + 1e-6:
            merged[-1] = (ps, max(pe, e))
        else:
            merged.append((s, e))
    return merged


def cut_intervals_from_cfg(
    cfg: dict, v2_cards: list[dict], deletions_override: list | None = None,
    extra_intervals: list | None = None,
) -> list[tuple[float, float]]:
    """取得**時間版刪除區間**（_v2/源時間軸的秒數），與字幕脫鉤。

    優先序：deletions_override（overlay「保留全部內容」用；idx list，[] = 無刪段）
      → cfg['cuts']（時間版新格式 [[start,end]...] 或 [{start,end}...]）
      → cfg['deletions']（舊 idx，用 v2_cards 換算 → 自動遷移讀取）。

    最後依 cfg['cut_pad'] 把每段往前後延伸吃掉間隙雜音（夾在鄰卡語音邊界內）；0 = 不延伸。
    """
    by_idx = {c["idx"]: c for c in v2_cards}

    def _from_idx(idxs) -> list[tuple[float, float]]:
        out = []
        for i in idxs or []:
            c = by_idx.get(int(i))
            if c is not None:
                out.append((float(c["start"]), float(c["end"])))
        return sorted(out)

    if deletions_override is not None:
        intervals = _from_idx(deletions_override)
    else:
        cuts = cfg.get("cuts")
        if cuts:
            intervals = []
            for c in cuts:
                if isinstance(c, dict):
                    intervals.append((float(c["start"]), float(c["end"])))
                else:
                    intervals.append((float(c[0]), float(c[1])))
        else:
            intervals = _from_idx(cfg.get("deletions") or [])

    cuts = _pad_and_merge_cuts(sorted(intervals), v2_cards, float(cfg.get("cut_pad") or 0))
    if extra_intervals:
        # 去空拍的靜音區間：已自帶緩衝、不再走 cut_pad 延伸（避免吃進鄰段語音），
        # 只與刪段聯集後合併重疊。
        allc = sorted(
            list(cuts)
            + [(float(min(a, b)), float(max(a, b))) for a, b in extra_intervals if a != b]
        )
        merged: list[list[float]] = [list(allc[0])]
        for s, e in allc[1:]:
            if s <= merged[-1][1] + 1e-6:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        cuts = [(s, e) for s, e in merged]
    return cuts


def filter_srt_by_intervals(
    src: Path, dst: Path, intervals: list[tuple[float, float]]
) -> None:
    """把落在任一刪除區間內的字幕卡拿掉，寫到 dst（單機燒字幕用，時間版）。"""
    from podcast_toolkit import srt_io
    cards = srt_io.parse(src.read_text(encoding="utf-8"))
    kept = [
        c for c in cards
        if not any(a <= float(c["start"]) < b for a, b in intervals)
    ]
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


def _video_encode_args(enc: dict) -> list[str]:
    """組視訊編碼參數。-preset 是 libx264 專屬；videotoolbox 硬體編碼器帶了會直接報錯。"""
    args = [
        "-c:v", enc["video_codec"],
        "-b:v", enc["video_bitrate"],
        "-maxrate", enc["video_maxrate"], "-bufsize", enc["video_bufsize"],
    ]
    if "videotoolbox" not in enc["video_codec"]:
        args += ["-preset", enc["preset"]]
    args += ["-pix_fmt", enc["pix_fmt"]]
    return args


def _hwaccel_args(enc: dict) -> list[str]:
    """影片輸入前的硬體解碼參數（-hwaccel 只作用於緊接的下一個 -i）。
    不帶 -hwaccel_output_format → 解碼後自動下載回系統記憶體，與 CPU 濾鏡（subtitles/crop）相容。"""
    hw = enc.get("hwaccel")
    return ["-hwaccel", str(hw)] if hw else []


def _rotate_for(cfg: dict, cam: str) -> float:
    """讀 cfg.rotate.{cam} 的度數（cam='a'|'b'）；缺值 / 格式錯 → 0。

    旋轉是「源畫面拉正」屬性，綁定實體攝影機（per cam），YT / Reels 共用同一角度；
    crop 才是 per-version（輸出比例不同）。所以 rotate 由 builder 直接讀 cfg、不走 builder 參數。
    """
    rot = cfg.get("rotate") or {}
    try:
        return float(rot.get(cam) or 0.0)
    except (TypeError, ValueError):
        return 0.0


# === 旋轉拉正預烤（P2c）===
# rotate filter 又慢（實測 0.7× 即時）又難平行化（全核榨頂 ~1.9×），是燒字幕多機集輸出
# 的主瓶頸。對策：有角度的鏡頭先「一次性」轉正成中間 proxy 檔快取，之後每次輸出改吃 proxy、
# 該鏡頭 rotate 設 0 → 畫面完全一致（proxy 就是 rotate 後的幀、黑角保留交給後續 crop 切），
# 但 assemble 跑在無 rotate 的 ~3× 速度。快取鍵 = 角度 + 來源檔簽章，沒變就重用。

def _leveled_proxy_paths(work_dir: Path, cam_label: str) -> tuple[Path, Path, Path]:
    """回 (proxy, tmp, meta)。proxy=轉正後中間檔；tmp=寫入中暫存；meta=快取鍵 json。"""
    up = cam_label.upper()
    return (
        work_dir / f"_leveled_cam{up}.mp4",
        work_dir / f".leveled_cam{up}.tmp.mp4",
        work_dir / f"_leveled_cam{up}.json",
    )


def _src_signature(src: Path) -> tuple[int, int]:
    """來源檔簽章 (mtime 取整秒, size)——換片/重新匯出就會變 → proxy 失效重烤。"""
    st = src.stat()
    return int(st.st_mtime), st.st_size


def _leveled_proxy_valid(proxy: Path, meta: Path, src: Path, angle: float) -> bool:
    """proxy 可否重用：檔在 + meta 的角度與來源簽章吻合現況。"""
    if not proxy.exists() or not meta.exists():
        return False
    try:
        m = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    mtime, size = _src_signature(src)
    return (
        abs(float(m.get("angle", 0.0)) - float(angle)) < 1e-6
        and m.get("src_mtime") == mtime
        and m.get("src_size") == size
    )


def write_leveled_meta(meta: Path, src: Path, angle: float) -> None:
    """轉正成功後寫快取鍵（assemble_job 跑完 proxy 呼叫）。"""
    mtime, size = _src_signature(src)
    meta.write_text(
        json.dumps({"angle": float(angle), "src_mtime": mtime, "src_size": size}),
        encoding="utf-8",
    )


def build_leveled_cmd(src: str, out: str, angle: float, enc: dict) -> list[str]:
    """預烤轉正指令：整支來源套 rotate=angle:ow=iw:oh=ih（與 _rotate_part 同義，黑角保留），
    硬解 + 高碼率硬編成中間檔。無音訊（multicam 音訊一律走 cam A，proxy 用不到）。
    高碼率（預設 60M，可由 enc.leveled_video_bitrate 覆寫）求視覺無損——assemble 之後還會
    再編一次到最終碼率，避免兩代壓縮看得出來。"""
    hw = _hwaccel_args(enc)
    venc = ["-c:v", enc["video_codec"]]
    if "videotoolbox" not in enc["video_codec"]:
        venc += ["-preset", enc.get("preset", "medium")]
    bitrate = enc.get("leveled_video_bitrate", "60M")
    venc += ["-b:v", bitrate, "-maxrate", bitrate, "-bufsize", bitrate, "-pix_fmt", enc["pix_fmt"]]
    return [
        ffmpeg_bin(), "-y",
        *hw, "-i", src,
        "-vf", f"rotate={float(angle)}*PI/180:ow=iw:oh=ih",
        *venc, "-an",
        out,
    ]


def _maybe_leveled(
    work_dir: Path, src: Path, cam_label: str, angle: float, enc: dict
) -> tuple[Path, dict | None, bool]:
    """旋轉拉正預烤判斷。回 (assemble 要吃的來源, prebake_spec_or_None, baked)。

    angle≈0 → (src, None, False)：不轉正，照舊。
    proxy 有效 → (proxy, None, True)：重用快取，assemble 跳過 rotate。
    proxy 無效 → (proxy, spec, True)：spec 描述要先跑的轉正指令，assemble 一樣跳過 rotate
                （proxy 由 assemble_job 在主合成前建好）。
    """
    if abs(float(angle)) < 1e-6:
        return src, None, False
    proxy, tmp, meta = _leveled_proxy_paths(work_dir, cam_label)
    if _leveled_proxy_valid(proxy, meta, src, angle):
        return proxy, None, True
    spec = {
        "cmd": build_leveled_cmd(str(src), str(tmp), angle, enc),
        "cwd": str(work_dir),
        "proxy": proxy,
        "tmp": tmp,
        "meta": meta,
        "angle": float(angle),
        "src": src,
        "total_dur": ffprobe_duration(src),
        "label": f"cam{cam_label.upper()} 旋轉拉正",
    }
    return proxy, spec, True


def _subs_part(srt_rel: str | None, style_str: str) -> str:
    """字幕燒製 filter 片段（含結尾逗號）。

    srt_rel=None → sidecar 模式（影片不燒字幕，字幕另存 .srt）→ 回空字串，整段 no-op。
    """
    if not srt_rel:
        return ""
    return f"subtitles={escape_filter_path(srt_rel)}:force_style='{style_str}',"


def _speed_parts(factor: float) -> tuple[str, str]:
    """回 (video_setpts, audio_atempo) 兩個 filter 片段（各含結尾逗號）。

    只加速正片：影片 setpts=PTS/factor、音訊 atempo=factor。factor≈1 → 兩者皆空字串。
    字幕在這之前就燒進畫面 → 像素隨畫面一起加速 → 自動同步（燒字幕模式）。
    """
    if not factor or abs(float(factor) - 1.0) < 1e-6:
        return "", ""
    f = float(factor)
    return f"setpts=PTS/{f},", f"atempo={f},"


def build_filter_complex_yt(
    cfg: dict,
    main_dur: float,
    srt_rel: str | None,
    deletion_intervals: list[tuple[float, float]] | None = None,
    wm_input_idx: int | None = None,
    audio_input_idx: int | None = None,
    audio_sync_offset: float = 0.0,
    speed_factor: float = 1.0,
) -> str:
    """YT 16:9：原本的三段 concat（intro + main + outro card），讀 crop_yt。

    audio_input_idx：若提供，正片音訊改從該 input idx 取（外接音檔），
    並套用 audio_sync_offset 對齊；None = 用 cam A 原音。
    srt_rel=None → sidecar 模式（影片不燒字幕）。speed_factor>1 → 只加速正片。
    main_dur 已是「加速後」的正片長度（呼叫端先除以 speed_factor），fade 計時直接用。
    """
    enc = cfg["encode"]
    res_w, res_h = enc["resolution"].split("x")
    intro_dur = cfg["assets"]["intro_duration"]
    intro_fade_out = cfg["assets"]["intro_fade_out"]
    style_str = build_style_string(cfg["subtitle_style"])
    speed_v, speed_a = _speed_parts(speed_factor)

    # main video 前處理 chain：rotate（cam A 拉正）→ crop_yt（源像素裁切）→ scale 到 1920×1080
    prep_part = _crop_part_str(cfg.get("crop_yt"), res_w, res_h, _rotate_for(cfg, "a"))

    # 刪除區間：select / aselect filter（跳過 deletion 時間段）
    # 切塊串接，避免單一 not(...) 運算式過長讓 ffmpeg 解析失敗（見 _chunked_select）
    select_v = _chunked_select(deletion_intervals, audio=False)
    select_a = _chunked_select(deletion_intervals, audio=True)

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
        f"{_subs_part(srt_rel, style_str)}"
        f"{select_v}setsar=1,fps={enc['framerate']},format={enc['pix_fmt']}"
    )
    # 倍速：字幕燒製後再 setpts，字幕像素隨畫面一起加速 → 自動同步
    if speed_v:
        v1_pre = f"{v1_pre},{speed_v.rstrip(',')}"
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
        f"{align_a}{select_a}{speed_a}afade=t=in:st=0:d=0.5,afade=t=out:st={main_dur - 0.5}:d=0.5[a1];"
        f"[3:a]aformat=sample_rates={enc['audio_sample_rate']}:channel_layouts=stereo,"
        f"afade=t=in:st=0:d=0.5[a2];"
        f"[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[v][a]"
    )


def build_filter_complex_reels(
    cfg: dict,
    main_dur: float,
    srt_rel: str | None,
    deletion_intervals: list[tuple[float, float]] | None = None,
    audio_input_idx: int | None = None,
    audio_sync_offset: float = 0.0,
    wm_input_idx: int | None = None,
    speed_factor: float = 1.0,
) -> str:
    """Reels 9:16：只有主影片，1080x1920，用 crop_reels。

    audio_input_idx：若提供，音訊改從該 input idx 取（外接音檔）並對齊；
    None = 用主影片原音。
    srt_rel=None → sidecar 模式（不燒字幕）。speed_factor>1 → 加速；main_dur 已是加速後長度。"""
    enc = cfg["encode"]
    res_w, res_h = 1080, 1920
    style_str = build_style_string(cfg.get("subtitle_style_reels") or cfg["subtitle_style"])
    speed_v, speed_a = _speed_parts(speed_factor)

    # rotate（cam A 拉正）→ crop_reels 源像素裁切 → scale 到 1080×1920；無 crop 時純 scale
    prep_part = _crop_part_str(cfg.get("crop_reels"), res_w, res_h, _rotate_for(cfg, "a"))

    # 切塊串接，避免單一 not(...) 運算式過長讓 ffmpeg 解析失敗（見 _chunked_select）
    select_v = _chunked_select(deletion_intervals, audio=False)
    select_a = _chunked_select(deletion_intervals, audio=True)

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
        f"{_subs_part(srt_rel, style_str)}"
        f"{select_v}setsar=1,fps={enc['framerate']},format={enc['pix_fmt']}"
    )
    # 倍速：字幕燒製後再 setpts，字幕像素隨畫面一起加速 → 自動同步
    if speed_v:
        v_pre = f"{v_pre},{speed_v.rstrip(',')}"
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
        f"{align_a}{select_a}{speed_a}afade=t=in:st=0:d=0.5,afade=t=out:st={main_dur - 0.5}:d=0.5[a]"
    )


# 保留舊名做相容呼叫（既有呼叫端預設走 YT 分支）
def build_filter_complex(cfg, main_dur, srt_rel, deletion_intervals=None):
    return build_filter_complex_yt(cfg, main_dur, srt_rel, deletion_intervals)


def _chunked_select(intervals, *, audio):
    """把剪除區間切成數塊、串接多個 (a)select，避免單一 not(between+…) 運算式過長
    讓 ffmpeg 的 expression parser 失敗（Cannot allocate memory / Error initializing filters）。
    silence_trim 開啟時剪除區間可達上百段，一條 not(...) 就會爆；切塊後每條運算式都短。
    (a)select 不重設 PTS → 每塊的 t 都還是原始時間軸，移除各塊聯集後，最後一次 (a)setpts 重切幀。
    回傳空字串表示沒有要剪的區間。"""
    if not intervals:
        return ""
    sel = "aselect" if audio else "select"
    reset = "asetpts=N/SR/TB" if audio else "setpts=N/FRAME_RATE/TB"
    chunk = 25
    parts = []
    for i in range(0, len(intervals), chunk):
        ranges = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in intervals[i:i + chunk])
        parts.append(f"{sel}='not({ranges})'")
    return ",".join(parts) + f",{reset},"


def build_audio_only(
    cfg: dict,
    main_dur: float,
    removed_intervals: list[tuple[float, float]] | None = None,
    audio_sync_offset: float = 0.0,
) -> str:
    """純音訊（原速 MP3）：intro 音 + 正片音（去除 removed_intervals、不加速）+ outro 音 concat。

    音訊一律單軌（cam A 原音或外接音檔），與鏡頭 A/B 切換無關 → 單機 / 雙機共用這條。
    input 約定：[0]=intro、[1]=主音訊來源、[2]=outro 音檔。removed_intervals 是源（cam A）
    時間軸的剪除區間（刪段 + 頭尾 trim + 去空拍）；外接音檔先 align 對到 cam A 軸再剪。
    main_dur = 剪完的正片長度（原速），給 afade 收尾計時。
    """
    enc = cfg["encode"]
    sr = enc["audio_sample_rate"]
    intro_dur = cfg["assets"]["intro_duration"]
    intro_fade_out = cfg["assets"]["intro_fade_out"]
    align_a = _build_audio_align_filter(audio_sync_offset)
    # 切塊串接，避免單一 not(...) 運算式過長讓 ffmpeg 解析失敗（見 _chunked_select）
    select_a = _chunked_select(removed_intervals, audio=True)
    return (
        f"[0:a]aformat=sample_rates={sr}:channel_layouts=stereo,"
        f"afade=t=out:st={intro_dur - intro_fade_out}:d={intro_fade_out}[a0];"
        f"[1:a]aformat=sample_rates={sr}:channel_layouts=stereo,"
        f"{align_a}{select_a}afade=t=in:st=0:d=0.5,afade=t=out:st={main_dur - 0.5}:d=0.5[a1];"
        f"[2:a]aformat=sample_rates={sr}:channel_layouts=stereo,"
        f"afade=t=in:st=0:d=0.5[a2];"
        # concat 後再 aresample 重新切幀：aselect/concat 產生的不規則幀會讓 libmp3lame 噴
        # 「inadequate AVFrame plane padding」（PCM 外接音檔特別容易中），aresample 重配幀緩衝修掉。
        f"[a0][a1][a2]concat=n=3:v=0:a=1,aresample={sr}[a]"
    )


def _multicam_segments(
    segments: list[dict],
    *,
    srt_rel: str | None,
    style_str: str,
    crop_part_a: str,
    crop_part_b: str,
    fps,
    fmt: str,
    sync_offset_b: float,
    a_vidx: int,
    b_vidx: int,
) -> tuple[list[str], str, str]:
    """組 per-segment「trim 先切 → crop/scale → 燒字幕 → 正規化」+ 必要時 concat。

    回傳 (parts, main_v_in, main_a_in)。單段時直接用 seg_v_0/seg_a_0，
    多段才額外加 concat=n=N 進 main_v_raw/main_a_raw。

    效能（P2）：crop/scale/字幕從舊版「每台全長各跑一次再 trim」改成「每段只跑自己那台」。
    GPU 解碼搬回 RAM 後的 CPU 濾鏡鏈（crop/scale/libass 燒字幕）由 2×全長 降到 1×成品長
    ── 兩台不再各自把整片燒一遍再 trim 丟掉。解碼仍走全長（要再省得逐段 -ss seek，另案）。

    字幕燒在 setpts=PTS-STARTPTS「之前」：trim 後幀仍帶主時間軸 PTS（cam B 已先 setpts
    對齊主軸），libass 直接讀到正確時間 → 不需逐段位移 SRT；且仍接在 crop/scale 之後，
    維持「字幕鎖在裁切框內」。

    a_vidx / b_vidx = cam A / cam B 的視訊 input index（YT=1/2，Reels=0/1）。
    同一 cam 出現 N 段時用明確 split=N：ffmpeg auto-split 在這個 trim+concat 圖形下
    會吃掉幀導致主段截斷（只剩前 1-2 段，2026-06 regression）。
    """
    parts: list[str] = []
    n = len(segments)

    n_a_v = sum(1 for s in segments if s["cam"] == "a")
    n_b_v = sum(1 for s in segments if s["cam"] == "b")
    n_a_a = n  # 音訊全部走 cam A

    subs = _subs_part(srt_rel, style_str)
    # cam B 先把 PTS 移到主時間軸，後面 trim / 字幕讀到的時間才對（cam A 原生就是主軸）
    b_setpts = f"setpts=PTS-{sync_offset_b}/TB,"

    # 視訊：每台先 split 成自己的段數（>1 才明確 split）。cam B 的 setpts 在 split 行套一次。
    if n_a_v > 1:
        labels = "".join(f"[a_v_{i}]" for i in range(n_a_v))
        parts.append(f"[{a_vidx}:v]split={n_a_v}{labels}")
    if n_b_v > 1:
        labels = "".join(f"[b_v_{i}]" for i in range(n_b_v))
        parts.append(f"[{b_vidx}:v]{b_setpts}split={n_b_v}{labels}")
    if n_a_a > 1:
        labels = "".join(f"[m_a_a_{i}]" for i in range(n_a_a))
        parts.append(f"[m_a_a]asplit={n_a_a}{labels}")

    cam_idx = {"a": 0, "b": 0}
    audio_idx = 0
    for i, seg in enumerate(segments):
        cam = seg["cam"]
        s, e = float(seg["start"]), float(seg["end"])
        if cam == "a":
            v_src = f"a_v_{cam_idx['a']}" if n_a_v > 1 else f"{a_vidx}:v"
            pre = ""
            crop = crop_part_a
        else:
            v_src = f"b_v_{cam_idx['b']}" if n_b_v > 1 else f"{b_vidx}:v"
            # 多段時 setpts 已在 split 行；單段沒 split 行 → 在這條 branch 補
            pre = "" if n_b_v > 1 else b_setpts
            crop = crop_part_b
        cam_idx[cam] += 1
        # trim 先切（主時間軸）→ crop/scale → 燒字幕（PTS 仍主軸）→ 收 PTS 歸零 → 規格化供 concat
        parts.append(
            f"[{v_src}]{pre}trim={s:.3f}:{e:.3f},{crop}{subs}"
            f"setpts=PTS-STARTPTS,setsar=1,fps={fps},format={fmt}[seg_v_{i}]"
        )
        # 音訊永遠取 cam A（多鏡頭只切畫面，聲音來源固定）
        a_src = f"m_a_a_{audio_idx}" if n_a_a > 1 else "m_a_a"
        audio_idx += 1
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


def _rotate_part(rotate_deg: float) -> str:
    """把傾斜畫面拉正的 rotate filter 片段（含結尾逗號）。角度≈0 → 空字串。

    ow=iw:oh=ih 保持原 frame 尺寸（旋轉露出的角落填黑）；交給後續 crop 把黑角裁掉。
    所以 rotate 之後的 crop 仍以「源尺寸」為基準算 iw*X/ih*Y，座標語意不變。
    bilinear=1（預設）讓邊緣平滑，避免小角度旋轉鋸齒。
    """
    if not rotate_deg or abs(float(rotate_deg)) < 1e-6:
        return ""
    return f"rotate={float(rotate_deg)}*PI/180:ow=iw:oh=ih,"


def _crop_part_str(crop: dict | None, res_w, res_h, rotate_deg: float = 0.0) -> str:
    """把源視訊轉到目標解析度的 prep chain（含 trailing comma）。

    順序：rotate（拉正）→ crop（源像素裁切）→ scale（目標解析度）。
    crop 用 iw*W:ih*H:iw*X:ih*Y 表達式直接吃源像素，再 scale 到目標解析度。
    比舊的 scale→crop→scale 三段穩 — 源 aspect 跟目標不同時（例如 1920×1080
    源 + 1080×1920 Reels 目標）不會先被壓扁再裁。crop 不存在時只 rotate+scale。

    rotate_deg：>0 順時針拉正；旋轉露出的黑角靠 crop 內縮裁掉（旋轉建議搭配 crop）。
    res_w / res_h 可傳 str 或 int（YT 用 cfg 字串 split、Reels 是 int）。
    """
    rw, rh = int(res_w), int(res_h)
    rot = _rotate_part(rotate_deg)
    if not crop:
        return f"{rot}scale={rw}:{rh},"
    return (
        f"{rot}crop=iw*{crop['width']}:ih*{crop['height']}:"
        f"iw*{crop['x']}:ih*{crop['y']},scale={rw}:{rh},"
    )


def _cam_crop_parts(
    base_crop: dict | None, res_w, res_h,
    rotate_a: float = 0.0, rotate_b: float = 0.0,
) -> tuple[str, str]:
    """把 crop_yt / crop_reels 拆成 (prep_a, prep_b)：含 rotate+crop+scale 完整 prep chain。

    base_crop.b（optional dict）= cam B 獨立 crop；沒設就 fallback 用 base 給兩鏡頭。
    無 base_crop 時兩鏡頭都只 rotate+scale 到目標解析度（不裁切）。
    rotate_a / rotate_b：cam A / cam B 各自的拉正角度（度數，per cam 獨立）。
    """
    if not base_crop:
        return (
            _crop_part_str(None, res_w, res_h, rotate_a),
            _crop_part_str(None, res_w, res_h, rotate_b),
        )
    crop_b = base_crop.get("b") or base_crop
    return (
        _crop_part_str(base_crop, res_w, res_h, rotate_a),
        _crop_part_str(crop_b, res_w, res_h, rotate_b),
    )


def build_filter_complex_yt_multicam(
    cfg: dict,
    main_dur: float,
    srt_rel: str | None,
    segments: list[dict],
    sync_offset_b: float = 0.0,
    audio_input_idx: int | None = None,
    audio_sync_offset: float = 0.0,
    wm_input_idx: int | None = None,
    speed_factor: float = 1.0,
    overlay_ass_rel: str | None = None,
) -> str:
    """YT 雙鏡頭：[0]=intro, [1]=cam A, [2]=cam B, [3]=outro image, [4]=outro audio。

    Cam B 先 setpts 對齊主時間軸再燒字幕；音訊一律走 cam A（除非提供 audio_input_idx）。
    每段依 segments[i].cam 從 [m_a_v]/[m_b_v] trim 出來，最後與 intro/outro concat=n=3。

    audio_input_idx：若提供，主音訊改從該 input idx 取（外接音檔），
    並套用 audio_sync_offset 對齊；None = 走 cam A 原音。
    srt_rel=None → sidecar 模式（不燒字幕）。speed_factor>1 → 只加速 concat 後的正片段（intro/outro 不動）。
    """
    enc = cfg["encode"]
    res_w, res_h = enc["resolution"].split("x")
    intro_dur = cfg["assets"]["intro_duration"]
    intro_fade_out = cfg["assets"]["intro_fade_out"]
    style_str = build_style_string(cfg["subtitle_style"])
    sr = enc["audio_sample_rate"]
    fmt = enc["pix_fmt"]
    fps = enc["framerate"]
    speed_v, speed_a = _speed_parts(speed_factor)

    crop_part_a, crop_part_b = _cam_crop_parts(
        cfg.get("crop_yt"), res_w, res_h, _rotate_for(cfg, "a"), _rotate_for(cfg, "b")
    )

    # 主音訊來源：外接音檔（含對齊）或 cam A 原音
    if audio_input_idx is not None:
        a_idx_main = audio_input_idx
        align_a = _build_audio_align_filter(audio_sync_offset)
    else:
        a_idx_main = 1
        align_a = ""

    parts: list[str] = []
    # 音訊永遠走 cam A（或外接音檔）；視訊每段只跑自己那台的 crop/scale/字幕（見 _multicam_segments）。
    # 某鏡頭完全沒 segment 時 _multicam_segments 不產生它的 branch（不會懸空 → 不會 EINVAL）。
    parts.append(
        f"[{a_idx_main}:a]aformat=sample_rates={sr}:channel_layouts=stereo,{align_a}anull[m_a_a]"
    )
    seg_parts, main_v_in, main_a_in = _multicam_segments(
        segments, srt_rel=srt_rel, style_str=style_str,
        crop_part_a=crop_part_a, crop_part_b=crop_part_b,
        fps=fps, fmt=fmt, sync_offset_b=sync_offset_b,
        a_vidx=1, b_vidx=2,
    )
    parts.extend(seg_parts)

    # main 段：先疊封面（只在正片段）→ 倍速 setpts → fade in/out（淡入淡出帶到封面與加速後時間軸）
    if _wm_enabled(cfg, wm_input_idx):
        wm_w, xy = _wm_overlay_params(cfg["watermark"], res_w, res_h)
        parts.append(f"[{wm_input_idx}:v]scale={wm_w}:-1[wm_main]")
        parts.append(
            f"[{main_v_in}][wm_main]overlay={xy},"
            f"{speed_v}fade=t=in:st=0:d=0.5,fade=t=out:st={main_dur - 0.5}:d=0.5[main_v]"
        )
    else:
        parts.append(
            f"[{main_v_in}]{speed_v}fade=t=in:st=0:d=0.5,fade=t=out:st={main_dur - 0.5}:d=0.5[main_v]"
        )
    parts.append(
        f"[{main_a_in}]{speed_a}afade=t=in:st=0:d=0.5,afade=t=out:st={main_dur - 0.5}:d=0.5[main_a]"
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

    if overlay_ass_rel:
        # 抽換字幕：concat 後（intro+正片+outro 全幀、成品時間軸）直接疊字幕，
        # 不經來源時間軸轉換 → 任何「對齊成品時間軸的 .srt/.ass」都能原樣換上。
        # 單次編碼、無中間檔；鏡頭/刪段/倍速已烙進 [vbody]，字幕純圖層。
        parts.append(
            "[v_intro][a_intro][main_v][main_a][v_outro][a_outro]"
            "concat=n=3:v=1:a=1[vbody][a]"
        )
        parts.append(
            f"[vbody]subtitles={overlay_ass_rel}:force_style='{style_str}'[v]"
        )
    else:
        parts.append(
            "[v_intro][a_intro][main_v][main_a][v_outro][a_outro]"
            "concat=n=3:v=1:a=1[v][a]"
        )

    return ";".join(parts)


def build_filter_complex_reels_multicam(
    cfg: dict,
    main_dur: float,
    srt_rel: str | None,
    segments: list[dict],
    sync_offset_b: float = 0.0,
    audio_input_idx: int | None = None,
    audio_sync_offset: float = 0.0,
    wm_input_idx: int | None = None,
    speed_factor: float = 1.0,
) -> str:
    """Reels 雙鏡頭：[0]=cam A, [1]=cam B。1080×1920，無 intro/outro。

    audio_input_idx：若提供，音訊改從該 input idx 取（外接音檔）並對齊；
    None = 用 cam A 原音。
    srt_rel=None → sidecar 模式（不燒字幕）。speed_factor>1 → 加速正片；main_dur 已是加速後長度。
    """
    enc = cfg["encode"]
    res_w, res_h = 1080, 1920
    style_str = build_style_string(cfg.get("subtitle_style_reels") or cfg["subtitle_style"])
    sr = enc["audio_sample_rate"]
    fmt = enc["pix_fmt"]
    fps = enc["framerate"]
    speed_v, speed_a = _speed_parts(speed_factor)

    crop_part_a, crop_part_b = _cam_crop_parts(
        cfg.get("crop_reels"), res_w, res_h, _rotate_for(cfg, "a"), _rotate_for(cfg, "b")
    )

    # 主音訊來源：外接音檔（含對齊）或 cam A 原音
    if audio_input_idx is not None:
        a_idx_main = audio_input_idx
        align_a = _build_audio_align_filter(audio_sync_offset)
    else:
        a_idx_main = 0
        align_a = ""

    parts: list[str] = []
    # 音訊永遠走 cam A（或外接音檔）；視訊每段只跑自己那台的 crop/scale/字幕（見 _multicam_segments）
    parts.append(
        f"[{a_idx_main}:a]aformat=sample_rates={sr}:channel_layouts=stereo,{align_a}anull[m_a_a]"
    )
    seg_parts, main_v_in, main_a_in = _multicam_segments(
        segments, srt_rel=srt_rel, style_str=style_str,
        crop_part_a=crop_part_a, crop_part_b=crop_part_b,
        fps=fps, fmt=fmt, sync_offset_b=sync_offset_b,
        a_vidx=0, b_vidx=1,
    )
    parts.extend(seg_parts)

    if _wm_enabled(cfg, wm_input_idx):
        wm_w, xy = _wm_overlay_params(cfg["watermark"], res_w, res_h)
        parts.append(f"[{wm_input_idx}:v]scale={wm_w}:-1[wm_main]")
        parts.append(
            f"[{main_v_in}][wm_main]overlay={xy},"
            f"{speed_v}fade=t=in:st=0:d=0.5,fade=t=out:st={main_dur - 0.5}:d=0.5[v]"
        )
    else:
        parts.append(
            f"[{main_v_in}]{speed_v}fade=t=in:st=0:d=0.5,fade=t=out:st={main_dur - 0.5}:d=0.5[v]"
        )
    parts.append(
        f"[{main_a_in}]{speed_a}afade=t=in:st=0:d=0.5,afade=t=out:st={main_dur - 0.5}:d=0.5[a]"
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
    subtitle_mode: str = "burn",
    overlay_srt: Path | None = None,
    out_override: Path | None = None,
    deletions_override: list | None = None,
    audio_only: bool = False,
) -> dict:
    """檢查資產 → 算出 ffmpeg 命令、cwd、輸出路徑、總時長。

    output_kind = 'yt' 或 'reels'：
      - yt：1920x1080，含 intro + outro card，用 crop_yt
      - reels：1080x1920,只含主影片，用 crop_reels

    subtitle_mode：
      - 'burn'（直接合成）：字幕燒進畫面（現行行為）。來源時間軸 _v2 隨管線轉換後逐段燒。
      - 'sidecar'（輸出字幕與影片）：影片不燒字幕，另把字幕映射到「成品時間軸」
        （收刪段 → ÷倍速 → +片頭偏移）寫成 .srt，放在成品旁。倍速下也對得齊。
      - 'overlay'（抽換字幕）：影片不燒來源字幕，改在合成最後一段（concat 後、成品時間軸全幀）
        直接疊 overlay_srt。overlay_srt 必須已對齊「成品時間軸」（例如自 YT 下載修正後的 .srt）。
        鏡頭/刪段/倍速完全照舊烙進畫面，字幕變成可任意抽換的圖層；單次編碼、無中間檔。

    overlay_srt：subtitle_mode='overlay' 時必填，成品時間軸的字幕檔。
    out_override：指定輸出檔路徑（None = 用 {name}_YT完整版.mp4）；抽換字幕時用來另存、不蓋原成品。

    preview_sec：若為正整數，ffmpeg 加 -t 截斷輸出長度（含 intro/正片/outro 全鏈路前 N 秒）；
    輸出檔名插入 .preview 避免覆蓋正式成品。

    tmp_out 寫在 04_工作檔/.{out.name}.tmp，呼叫端跑完 ffmpeg 後負責 rename 到 03_成品/。
    回傳 dict：cmd / cwd / out / tmp_out / main_dur / total_dur / output_kind / sidecar_srt。
    sidecar_srt：burn 模式為 None；sidecar 模式為 {"path": Path, "content": str}，呼叫端成功後落檔。
    """
    if output_kind not in ("yt", "reels"):
        raise AssembleError(f"未知 output_kind={output_kind}")
    if subtitle_mode not in ("burn", "sidecar", "overlay"):
        raise AssembleError(f"未知 subtitle_mode={subtitle_mode}")
    if subtitle_mode == "overlay":
        if overlay_srt is None:
            raise AssembleError("subtitle_mode='overlay' 需提供 overlay_srt")
        if not Path(overlay_srt).exists():
            raise AssembleError(f"找不到 overlay_srt：{overlay_srt}", exit_code=3)
        if output_kind != "yt":
            raise AssembleError("overlay 模式目前只支援 output_kind='yt'")
    # overlay / sidecar 都不燒「來源時間軸」字幕；overlay 改在最後一段疊成品時間軸字幕
    burn_subs = subtitle_mode == "burn"
    # 原速 MP3：純音訊輸出 → 不燒字幕、不需 libass，永遠原速（force speed=1.0，見下）
    if audio_only:
        burn_subs = False

    if not shutil.which("ffmpeg"):
        raise AssembleError("找不到 ffmpeg，請 brew install ffmpeg")
    if not shutil.which("ffprobe"):
        raise AssembleError("找不到 ffprobe（隨 ffmpeg 安裝）")
    # 燒字幕需要 libass；若挑不到含 subtitles 濾鏡的 ffmpeg，提早給清楚錯誤
    # （否則 ffmpeg 會在 filtergraph 噴「No such filter: subtitles」很難懂）。
    if burn_subs and not _has_subtitles_filter(ffmpeg_bin()):
        raise AssembleError(
            "ffmpeg 缺 subtitles 濾鏡（沒編 libass），無法燒字幕。"
            "請裝含 libass 的 ffmpeg（例如 evermeet 靜態版放 ~/.local/bin，"
            "或用 brew 重裝含 libass 的 ffmpeg）。"
        )

    ep = Episode(episode_dir)
    cfg = ep.cfg

    # 去空拍（全片跳剪停頓）：偵測中段靜音 → 當額外刪除區間，交給 cut_intervals_from_cfg
    # 與既有刪段聯集，下游剪裁/字幕對齊照舊。詳見 _silence_cuts_from_cfg。
    silence_cuts = _silence_cuts_from_cfg(ep)

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

    # 字幕時間軸總位移（非破壞性，shift 到 work 暫存檔，原 srt 不動）：
    #   -audio_sync_offset：把外接音檔時間軸的字幕對回 cam A（見上）
    #   +subtitle_offset_sec：使用者在 UI 設的非破壞性字幕偏移（正值=字幕往後延）
    subtitle_offset = float(cfg.get("subtitle_offset_sec") or 0.0)
    srt_total_shift = -audio_sync_offset + subtitle_offset
    if abs(srt_total_shift) >= 0.001:
        shifted_srt = ep.subdir("work") / "_v2_aligned.srt"
        shift_srt(srt, shifted_srt, srt_total_shift)
        srt = shifted_srt

    # 輸出路徑分支
    if output_kind == "yt":
        out = ep.output_yt_video()
    else:
        out = ep.output_reels_video()

    # 抽換字幕等情境：另存到指定檔名，不蓋原成品
    if out_override is not None:
        out = Path(out_override)

    # 原速 MP3：純音訊，輸出 03_成品/{name}_原速.mp3（套編輯 + 含片頭尾、不加速）
    if audio_only:
        out = ep.subdir("output") / f"{ep.name}_原速.mp3"

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

    # 量正片時長（main_dur_src = 未扣刪段/未加速的源長度，sidecar 時間軸換算要用原長）
    main_dur = ffprobe_duration(main_video)
    main_dur_src = main_dur
    enc = cfg["encode"]

    # 倍速：speed.enabled 時取 factor（夾在 atempo 合法區間 0.5–2.0；預設 1.25）；否則 1.0 = 不加速
    speed_cfg = cfg.get("speed") or {}
    speed_factor = 1.0
    if speed_cfg.get("enabled"):
        try:
            speed_factor = float(speed_cfg.get("factor") or 1.0)
        except (TypeError, ValueError):
            speed_factor = 1.0
        speed_factor = min(2.0, max(0.5, speed_factor))
    # 原速 MP3：永遠原速（main_dur 不除倍速、afade 計時直接用未加速長度）
    if audio_only:
        speed_factor = 1.0

    # 字幕卡（源時間軸）：sidecar 模式拿來映射成成品時間軸；multicam 段規劃也共用這份 parse
    from podcast_toolkit import srt_io
    all_cards = srt_io.parse(srt.read_text(encoding="utf-8"))

    # filter_complex：subtitles filter 路徑要相對 cwd，subprocess cwd 設為 03_成品/
    cwd = ep.subdir("output")

    # 旋轉拉正預烤（P2c）：有角度的鏡頭先一次性轉正成 proxy 快取，assemble 改吃 proxy、
    # 該鏡頭 rotate 設 0（畫面一致，省掉每次輸出最重的 rotate）。proxy 由 assemble_job 在
    # 主合成前建好；角度/來源沒變就重用，YT/Reels/重輸出共享。只在 multicam 啟用（單機後續再補）。
    render_cfg = cfg
    prebakes: list[dict] = []
    # 原速 MP3 只取音訊，不需旋轉拉正 proxy（旋轉不影響音訊）→ 跳過 prebake 省掉建 proxy 的時間
    if multicam and not audio_only:
        work_dir = ep.subdir("work")
        new_rot = dict(cfg.get("rotate") or {})
        main_video, pb_a, baked_a = _maybe_leveled(work_dir, main_video, "a", _rotate_for(cfg, "a"), enc)
        cam_b_video, pb_b, baked_b = _maybe_leveled(work_dir, cam_b_video, "b", _rotate_for(cfg, "b"), enc)
        if baked_a:
            new_rot["a"] = 0.0
        if baked_b:
            new_rot["b"] = 0.0
        if baked_a or baked_b:
            render_cfg = {**cfg, "rotate": new_rot}
        prebakes = [pb for pb in (pb_a, pb_b) if pb]

    main_rel = str(main_video.relative_to(cwd)) if main_video.is_relative_to(cwd) else str(main_video)

    # burn 模式才需要把 SRT 轉成有明確 PlayResX/Y 的 ASS（PlayResY=輸出 frame 高，避免
    # libass 對 SRT 預設 PlayResY=288 把 MarginV/FontSize 等比放大）。sidecar 不燒字幕 → srt_rel=None。
    srt_rel: str | None = None
    if burn_subs:
        if output_kind == "yt":
            ass_res_w, ass_res_h = (int(x) for x in enc["resolution"].split("x"))
        else:
            ass_res_w, ass_res_h = 1080, 1920
        ass_path = ep.subdir("work") / f"_v2_aligned_{ass_res_w}x{ass_res_h}.ass"
        _write_ass_from_srt(srt, ass_path, ass_res_w, ass_res_h)
        srt_rel = str(ass_path.relative_to(cwd)) if ass_path.is_relative_to(cwd) else str(ass_path)

    # 抽換字幕：把成品時間軸的 overlay_srt 轉成成品解析度的 ASS，最後一段直接疊在全幀上
    overlay_ass_rel: str | None = None
    if subtitle_mode == "overlay":
        ov_w, ov_h = (int(x) for x in enc["resolution"].split("x"))
        ov_ass = ep.subdir("work") / f"_overlay_{ov_w}x{ov_h}.ass"
        _write_ass_from_srt(Path(overlay_srt), ov_ass, ov_w, ov_h)
        overlay_ass_rel = str(ov_ass.relative_to(cwd)) if ov_ass.is_relative_to(cwd) else str(ov_ass)

    # deletions_override 非 None → 取代 yaml deletions（抽換字幕「保留全部內容」用：對齊外部字幕的
    # 完整時間軸，避免事後加的刪段讓外部字幕在刪點之後整段慢出現）
    head_trim = float(cfg.get("head_trim_sec") or 0)
    tail_trim = float(cfg.get("tail_trim_sec") or 0)

    # 刪段已改**時間版**（cuts，存 _v2 時間軸，與字幕脫鉤）。canonical _v2 卡供 legacy idx 換算，
    # 也供鏡頭 legacy 換算共用（不依賴 active_srt，避免 srt_path 指到別份字幕時錯位）。
    v2_canon_path = ep.output_v2_srt()
    v2_canon_cards = (
        srt_io.parse(v2_canon_path.read_text(encoding="utf-8"))
        if v2_canon_path.exists() else all_cards
    )
    cut_intervals = cut_intervals_from_cfg(
        cfg, v2_canon_cards, deletions_override, extra_intervals=silence_cuts
    )
    # _v2 → cam-A 時間軸：與 srt 一樣 -sync 位移（segment_plan / 單機 deletion_intervals 同軸）
    if audio_file is not None and abs(audio_sync_offset) >= 0.001:
        cut_intervals = [
            (a - audio_sync_offset, b - audio_sync_offset) for a, b in cut_intervals
        ]

    # removed_intervals = 源時間軸上「被剪掉的區間」，sidecar .srt 映射用（單機 / 雙機共用同一套換算）
    segments: list[dict] = []
    sync_offset_b = 0.0
    if multicam:
        from podcast_toolkit import cameras_io
        from podcast_toolkit.segment_plan import build_segment_plan

        # 鏡頭時間版切換點（同 _v2 時間軸 → -sync）
        cam_transitions = cameras_io.load_transitions(ep.output_v2_cameras_json(), v2_canon_cards)
        if audio_file is not None and abs(audio_sync_offset) >= 0.001:
            cam_transitions = [
                {"t": tr["t"] - audio_sync_offset, "cam": tr["cam"]}
                for tr in cam_transitions
            ]
        segments = build_segment_plan(
            cut_intervals=cut_intervals,
            cam_transitions=cam_transitions,
            main_dur=main_dur,
            head_trim_sec=head_trim,
            tail_trim_sec=tail_trim,
        )
        # main_dur = 所有 keep 段加總（segment_plan 已扣刪段 + 頭尾 trim）
        main_dur = sum(s["end"] - s["start"] for s in segments)
        sync_offset_b = float((cfg.get("camera_sync_offset") or {}).get("b") or 0.0)
        # multicam 直接燒原字幕（trim 自動把被刪段的字幕一起切掉，不需 clean_srt）
        deletion_intervals = []
        removed_intervals = _removed_intervals_from_segments(segments, main_dur_src)
    else:
        # 單機：cut_intervals 直接當刪除區間 + 頭尾 trim
        deletion_intervals = list(cut_intervals)
        if head_trim > 0:
            deletion_intervals.append((0.0, head_trim))
        if tail_trim > 0:
            deletion_intervals.append((main_dur - tail_trim, main_dur))
        deletion_intervals = sorted(deletion_intervals)
        removed_intervals = deletion_intervals

        if burn_subs and cut_intervals:
            # 燒字幕：去掉落在刪除區間的字幕卡，避免 select 後字幕時間錯位閃爍
            clean_srt = ep.subdir("work") / f"_v2_assembled_{output_kind}.srt"
            filter_srt_by_intervals(srt, clean_srt, cut_intervals)
            srt = clean_srt
            srt_rel = str(srt.relative_to(cwd)) if srt.is_relative_to(cwd) else str(srt)

        if deletion_intervals:
            # main_dur 用於 fade-out 計時，扣掉刪除區間總長（含頭尾 trim）
            deleted_total = sum(b - a for a, b in deletion_intervals)
            main_dur = main_dur - deleted_total

    # 倍速：正片時間軸壓縮為 main_dur/factor，供四個 builder 的 fade 計時與 total_dur 用
    main_dur = main_dur / speed_factor

    # 防呆：刪段/頭尾 trim 把正片砍到 0（或全刪）→ main_dur<=0 會讓 fade=t=out:st=main_dur-0.5
    # 變負、multicam segments 變空，ffmpeg 不是報錯就是默默產壞檔。明確報錯勝過輸出壞片。
    if main_dur <= 0:
        raise AssembleError("刪段／頭尾 trim 後正片長度為 0，請至少保留一段內容", exit_code=1)

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

    # 節目封面 overlay：cfg.watermark.enabled=true 且封面 / logo 檔案實際存在才會 wire。
    # 取圖優先 assets.cover（節目封面），退回 assets.logo；皆無 → wm_rel_str=None，filter 自動 no-op。
    # 既有 overlay 邏輯只疊在「正片段」（intro/outro 不碰），天生符合「錄影開始後才放封面」。
    wm_cfg = cfg.get("watermark") or {}
    wm_rel_str: str | None = None
    if wm_cfg.get("enabled"):
        overlay_path: Path | None = None
        for asset_key in ("cover", "logo"):
            try:
                p = ep.asset_path(asset_key)
            except KeyError:
                continue
            if p.exists():
                overlay_path = p
                break
        if overlay_path is not None:
            wm_rel_str = (
                str(overlay_path.relative_to(cwd)) if overlay_path.is_relative_to(cwd) else str(overlay_path)
            )
        else:
            print(
                "⚠ watermark.enabled=true 但找不到 assets.cover / assets.logo，自動跳過封面 overlay",
                file=sys.stderr,
            )

    if audio_only:
        # 原速 MP3：純音訊，音訊一律用外接 mix 音檔（不用影片內建聲音）。
        # input 0=intro、1=mix、2=outro 音檔。
        if not audio_rel_str:
            raise AssembleError(
                "原速 MP3 需要外接 mix 音檔（不用影片內建聲音）；"
                "請先在「鏡頭與音檔對齊」指定外接音檔。",
                exit_code=3,
            )
        intro_dur = cfg["assets"]["intro_duration"]
        outro_dur = cfg["assets"]["outro_duration"]
        intro = ep.asset_path("intro")
        outro_audio = ep.asset_path("outro_audio")
        fc = build_audio_only(
            cfg, main_dur=main_dur, removed_intervals=removed_intervals,
            audio_sync_offset=audio_sync_offset,
        )
        cmd = [
            ffmpeg_bin(), "-y",
            "-i", str(intro),
            "-i", audio_rel_str,
            "-i", str(outro_audio),
            "-filter_complex", fc,
            "-map", "[a]", "-vn",
            "-c:a", "libmp3lame", "-q:a", "2",
            "-ar", str(enc["audio_sample_rate"]),
            tmp_out_rel,
        ]
        total_dur = intro_dur + main_dur + outro_dur
    elif output_kind == "yt":
        intro_dur = cfg["assets"]["intro_duration"]
        outro_dur = cfg["assets"]["outro_duration"]
        if multicam:
            # yt multi inputs：intro(0) + camA(1) + camB(2) + outro_image(3) + outro_audio(4) → 外接音檔 = 5 → watermark = 5 or 6
            audio_input_idx = 5 if audio_rel_str else None
            wm_next = 6 if audio_rel_str else 5
            wm_input_idx = wm_next if wm_rel_str else None
            fc = build_filter_complex_yt_multicam(
                render_cfg, main_dur=main_dur, srt_rel=srt_rel,
                segments=segments, sync_offset_b=sync_offset_b,
                audio_input_idx=audio_input_idx,
                audio_sync_offset=audio_sync_offset,
                wm_input_idx=wm_input_idx,
                speed_factor=speed_factor,
                overlay_ass_rel=overlay_ass_rel,
            )
            hw = _hwaccel_args(enc)
            cmd = [
                ffmpeg_bin(), "-y",
                *hw, "-i", str(intro),
                *hw, "-i", main_rel,
                *hw, "-i", cam_b_rel_str,
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
                *_video_encode_args(enc),
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
                speed_factor=speed_factor,
            )
            hw = _hwaccel_args(enc)
            cmd = [
                ffmpeg_bin(), "-y",
                *hw, "-i", str(intro),
                *hw, "-i", main_rel,
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
                *_video_encode_args(enc),
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
                render_cfg, main_dur=main_dur, srt_rel=srt_rel,
                segments=segments, sync_offset_b=sync_offset_b,
                audio_input_idx=audio_input_idx,
                audio_sync_offset=audio_sync_offset,
                wm_input_idx=wm_input_idx,
                speed_factor=speed_factor,
            )
            hw = _hwaccel_args(enc)
            cmd = [
                ffmpeg_bin(), "-y",
                *hw, "-i", main_rel,
                *hw, "-i", cam_b_rel_str,
            ]
            if audio_rel_str:
                cmd += ["-i", audio_rel_str]
            if wm_rel_str:
                cmd += ["-i", wm_rel_str]
            cmd += [
                "-filter_complex", fc,
                "-map", "[v]", "-map", "[a]",
                *_video_encode_args(enc),
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
                speed_factor=speed_factor,
            )
            hw = _hwaccel_args(enc)
            cmd = [
                ffmpeg_bin(), "-y",
                *hw, "-i", main_rel,
            ]
            if audio_rel_str:
                cmd += ["-i", audio_rel_str]
            if wm_rel_str:
                cmd += ["-i", wm_rel_str]
            cmd += [
                "-filter_complex", fc,
                "-map", "[v]", "-map", "[a]",
                *_video_encode_args(enc),
                "-c:a", enc["audio_codec"], "-b:a", enc["audio_bitrate"],
                "-ar", str(enc["audio_sample_rate"]),
                "-movflags", "+faststart",
                tmp_out_rel,
            ]
        total_dur = main_dur

    # preview 模式：在 -movflags 前插 -t，截斷整段輸出（含 intro+正片+outro 全鏈路）為前 N 秒
    # （audio_only 的 MP3 cmd 沒有 -movflags，也不走 preview）
    if preview_sec and preview_sec > 0 and not audio_only:
        insert_at = cmd.index("-movflags")
        cmd[insert_at:insert_at] = ["-t", str(preview_sec)]
        total_dur = min(total_dur, float(preview_sec))

    # sidecar 字幕（輸出字幕與影片）：把源字幕映射到成品時間軸（收刪段 → ÷倍速 → +片頭偏移）。
    # YT 正片接在片頭後 → intro_offset=intro_duration；Reels 無片頭 = 0。呼叫端跑完 ffmpeg 成功才落檔。
    sidecar_srt: dict | None = None
    if subtitle_mode == "sidecar":
        intro_offset = float(cfg["assets"]["intro_duration"]) if output_kind == "yt" else 0.0
        content = build_sidecar_srt(all_cards, removed_intervals, speed_factor, intro_offset)
        sidecar_srt = {"path": out.with_suffix(".srt"), "content": content}

    return {
        "cmd": cmd,
        "cwd": cwd,
        "out": out,
        "tmp_out": tmp_out,
        "main_dur": main_dur,
        "total_dur": total_dur,
        "output_kind": "mp3" if audio_only else output_kind,
        "sidecar_srt": sidecar_srt,
        "prebake": prebakes,
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


def _removed_intervals_from_segments(
    segments: list[dict], main_dur_src: float
) -> list[tuple[float, float]]:
    """從 multicam segment plan（保留段）算出「被移除區間」= [0, main_dur_src] 內的補集。

    segment_plan 已內建 deletions + head/tail trim，保留段是源時間軸上「會出現在成品」的區間；
    其補集（段與段之間的縫、第一段前、最後段後）就是被剪掉的部分，
    餵給 _original_to_mp4_time 即可把源時間映射到 multicam 正片時間軸（與單機共用同一套邏輯）。
    """
    removed: list[tuple[float, float]] = []
    prev = 0.0
    for seg in sorted(segments, key=lambda s: float(s["start"])):
        s, e = float(seg["start"]), float(seg["end"])
        if s > prev:
            removed.append((prev, s))
        prev = max(prev, e)
    if prev < main_dur_src:
        removed.append((prev, main_dur_src))
    return removed


def map_src_to_output_time(
    t_src: float,
    removed_intervals: list[tuple[float, float]],
    speed: float,
    intro_offset: float,
) -> float:
    """源字幕時間軸 → 最終成品時間軸（sidecar .srt 用）。

    三步轉換，與影片走的完全是同一套：
      1. 收掉被刪段 / 頭尾 trim（_original_to_mp4_time）→ 正片時間軸
      2. ÷ speed（倍速；影片 setpts=PTS/speed 把正片壓短同樣倍率）
      3. + intro_offset（YT 正片接在片頭之後；Reels 無片頭 = 0）

    這就是「倍速後單獨輸出字幕也不跑掉」的關鍵：影片加速多少，.srt 同步壓縮多少。
    """
    t_body = _original_to_mp4_time(t_src, removed_intervals)
    return intro_offset + t_body / (speed or 1.0)


def build_sidecar_srt(
    cards: list[dict],
    removed_intervals: list[tuple[float, float]],
    speed: float,
    intro_offset: float,
) -> str:
    """把源時間軸字幕卡映射到成品時間軸，回一份重新編號的 SRT 字串（sidecar 模式用）。

    落在被刪除區間內的卡 → start/end 都映射到同一點 → 長度≈0 → 丟掉（與影片把該段剪掉一致）。
    """
    from podcast_toolkit import srt_io

    out_cards: list[dict] = []
    for c in cards:
        s = map_src_to_output_time(float(c["start"]), removed_intervals, speed, intro_offset)
        e = map_src_to_output_time(float(c["end"]), removed_intervals, speed, intro_offset)
        if e - s <= 0.01:
            continue
        out_cards.append({**c, "start": s, "end": e})
    for i, c in enumerate(out_cards, 1):
        c["idx"] = i
    return srt_io.serialize(out_cards)


def _silence_cuts_from_cfg(ep) -> list[tuple[float, float]]:
    """偵測中段靜音 → 當額外刪除區間（_v2 軸）；silence_trim.enabled 關閉則回空。

    偵測跑在外接音檔（＝字幕時間軸），無外接音檔則用正片自身音軌；每段內縮 pad
    留緩衝不貼死語音。主合成與 reels clip 換算共用，確保兩邊刪段聯集一致。
    """
    cfg = ep.cfg
    out: list[tuple[float, float]] = []
    _st = cfg.get("silence_trim") or {}
    if _st.get("enabled"):
        from podcast_toolkit.web.silencedetect import detect_silence_intervals
        _min_sil = float(_st.get("min_silence") or 0.8)
        _pad = float(_st["pad"]) if _st.get("pad") is not None else 0.15
        _noise = float(_st.get("noise_db") or -30.0)
        _apath = (cfg.get("audio") or {}).get("path")
        _sil_media = ep.resolve_episode_path(_apath) if _apath else ep.main_video()
        for _s, _e in detect_silence_intervals(
            _sil_media, threshold_db=_noise, min_dur=_min_sil
        ):
            _ns, _ne = _s + _pad, _e - _pad
            if _ne - _ns > 0.05:
                out.append((_ns, _ne))
    return out


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

    head_trim = float(cfg.get("head_trim_sec") or 0)
    tail_trim = float(cfg.get("tail_trim_sec") or 0)

    main_video = ep.main_video()
    if not main_video.exists():
        raise AssembleError(f"找不到正片以量總時長：{main_video}", exit_code=3)
    main_dur = ffprobe_duration(main_video)

    # 刪段時間版（cuts + 靜音剪段，與主合成同源；reels 母片已扣掉故 clip 換算需一致）
    silence_cuts = _silence_cuts_from_cfg(ep)
    deletion_intervals = list(
        cut_intervals_from_cfg(cfg, cards, extra_intervals=silence_cuts)
    )
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
            ffmpeg_bin(), "-y",
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
        output_kind: str = "yt", subtitle_mode: str = "burn",
        overlay_srt: Path | None = None, out_override: Path | None = None) -> int:
    try:
        plan = prepare_assembly(
            episode_dir, output_kind=output_kind, force=force, subtitle_mode=subtitle_mode,
            overlay_srt=overlay_srt, out_override=out_override,
        )
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

    # sidecar 模式：影片成功後才把對齊好的字幕 .srt 落在成品旁
    sidecar = plan.get("sidecar_srt")
    if sidecar:
        sidecar["path"].write_text(sidecar["content"], encoding="utf-8")
        print(f"✅ 字幕：{sidecar['path']}")

    print(f"✅ 完成：{out}")
    return 0
