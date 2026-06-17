"""podcast CLI 入口。"""
import argparse
import sys
from pathlib import Path


def cmd_init(args):
    from podcast_toolkit import init as init_mod
    return init_mod.run(Path(args.path))


def cmd_subtitle(args):
    from podcast_toolkit import gemini_subtitle
    return gemini_subtitle.run(
        Path(args.path), force=args.force, dry_run=args.dry_run, per_mic=args.per_mic,
    )


def cmd_resegment(args):
    from podcast_toolkit import resegment
    return resegment.run(Path(args.path), force=args.force)


def cmd_proofread(args):
    from podcast_toolkit import proofread
    return proofread.run(
        Path(args.path), provider=args.provider, model=args.model, force=args.force,
    )


def cmd_auto(args):
    from podcast_toolkit import auto
    return auto.run(
        Path(args.path),
        do_proofread=not args.no_proofread,
        do_camera=not args.no_camera,
        do_trim=not args.no_trim,
        provider=args.provider,
        model=args.model,
        force=args.force,
    )


def cmd_ingest_breeze(args):
    from podcast_toolkit import ingest_breeze
    return ingest_breeze.run(Path(args.path), srt=args.srt, force=args.force)


def cmd_merge_per_mic(args):
    from podcast_toolkit import srt_merge
    from podcast_toolkit.episode import Episode
    ep = Episode(Path(args.path))
    return srt_merge.run(ep, force=args.force)


def cmd_suggest_cameras(args):
    from podcast_toolkit import cameras_suggest
    from podcast_toolkit.episode import Episode
    ep = Episode(Path(args.path))
    return cameras_suggest.run(ep, force=args.force)


def cmd_assemble(args):
    from podcast_toolkit import assemble
    return assemble.run(Path(args.path), dry_run=args.dry_run, force=args.force)


def cmd_clip(args):
    from podcast_toolkit import assemble
    names = list(args.names) if args.names else None
    return assemble.run_clips(Path(args.path), clip_names=names, force=args.force)


def cmd_edit(args):
    from podcast_toolkit import edit
    return edit.run(Path(args.path))


def cmd_ui(args):
    from podcast_toolkit import edit
    return edit.run_dashboard()


def build_parser():
    p = argparse.ArgumentParser(prog="podcast", description="Podcast 剪輯 toolkit")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="腳手架：建立子資料夾 + episode.yaml")
    pi.add_argument("path", nargs="?", default=".", help="集資料夾路徑（預設：當前目錄）")
    pi.set_defaults(func=cmd_init)

    ps = sub.add_parser("subtitle", help="Gemini API 把 01_母帶/ 音檔轉成 SRT 字幕")
    ps.add_argument("path", nargs="?", default=".", help="集資料夾路徑（預設：當前目錄）")
    ps.add_argument("--force", action="store_true", help="覆寫已存在的字幕")
    ps.add_argument("--dry-run", action="store_true", help="只印 prompt 不呼叫 API")
    ps.add_argument(
        "--per-mic", action="store_true",
        help="分軌轉錄：用 episode.yaml.mics 的逐路 mic，先 VAD 閘掉串音再上傳 Gemini",
    )
    ps.set_defaults(func=cmd_subtitle)

    pr = sub.add_parser("resegment", help="字幕重新斷句 + 錯字修正")
    pr.add_argument("path", nargs="?", default=".", help="集資料夾路徑（預設：當前目錄）")
    pr.add_argument("--force", action="store_true", help="覆寫已存在的輸出")
    pr.set_defaults(func=cmd_resegment)

    pp = sub.add_parser("proofread", help="字幕語意校對（本地 Claude Code / Gemini，就地改 _v2.srt）")
    pp.add_argument("path", nargs="?", default=".", help="集資料夾路徑（預設：當前目錄）")
    pp.add_argument(
        "--provider", choices=["claude_code", "gemini", "off"], default=None,
        help="覆寫設定的校對 provider（預設讀 episode.yaml / defaults.yaml 的 proofread.provider=auto）",
    )
    pp.add_argument(
        "--model", default=None,
        help="覆寫校對模型（claude_code 用 claude --model，如 sonnet / opus / haiku；gemini 用 model id）",
    )
    pp.add_argument("--force", action="store_true", help="保留參數一致性（校對一律就地覆寫並備份）")
    pp.set_defaults(func=cmd_proofread)

    pao = sub.add_parser(
        "auto",
        help="一鍵自動後製：校對字幕 + 對應鏡頭 + 去頭去尾（盡量自動，只剩人工確認）",
    )
    pao.add_argument("path", nargs="?", default=".", help="集資料夾路徑（預設：當前目錄）")
    pao.add_argument("--no-proofread", action="store_true", help="跳過字幕校對")
    pao.add_argument("--no-camera", action="store_true", help="跳過鏡頭對應")
    pao.add_argument("--no-trim", action="store_true", help="跳過去頭去尾")
    pao.add_argument(
        "--provider", choices=["claude_code", "gemini", "off"], default=None,
        help="覆寫校對 provider（預設 auto：本地 Claude Code → Gemini key → 跳過）",
    )
    pao.add_argument(
        "--model", default=None,
        help="覆寫校對模型（如 sonnet / opus / haiku）；校對是 bulk 任務，sonnet 比預設快很多",
    )
    pao.add_argument("--force", action="store_true", help="重跑（含重測頭尾靜音、覆寫既有 trim）")
    pao.set_defaults(func=cmd_auto)

    pib = sub.add_parser(
        "ingest-breeze",
        help="匯入 Breeze ASR 字幕(含講者 [MicN])→ 去標籤寫 _final_v2.srt + speakers.json",
    )
    pib.add_argument("path", nargs="?", default=".", help="集資料夾路徑（預設：當前目錄）")
    pib.add_argument(
        "--srt", default=None,
        help="指定 Breeze SRT 路徑（預設自動找集內 *含講者*.srt / *最終字幕*.srt）",
    )
    pib.add_argument("--force", action="store_true", help="保留參數一致性（一律就地覆寫並備份）")
    pib.set_defaults(func=cmd_ingest_breeze)

    pm = sub.add_parser(
        "merge-per-mic",
        help="把 04_工作檔/_mic_*.srt 合併成 03_成品/_final_v2.srt + _final_v2.speakers.json",
    )
    pm.add_argument("path", nargs="?", default=".", help="集資料夾路徑（預設：當前目錄）")
    pm.add_argument("--force", action="store_true", help="覆寫已存在的輸出")
    pm.set_defaults(func=cmd_merge_per_mic)

    psc = sub.add_parser(
        "suggest-cameras",
        help="從分軌 speakers.json 自動建議時間版鏡頭切換點 → cameras.json（人再覆蓋例外）",
    )
    psc.add_argument("path", nargs="?", default=".", help="集資料夾路徑（預設：當前目錄）")
    psc.add_argument("--force", action="store_true", help="覆寫已存在的 cameras.json（先備份 .bak）")
    psc.set_defaults(func=cmd_suggest_cameras)

    pa = sub.add_parser("assemble", help="合成片頭+正片+片尾 → YT 完整版")
    pa.add_argument("path", nargs="?", default=".", help="集資料夾路徑（預設：當前目錄）")
    pa.add_argument("--dry-run", action="store_true", help="只印 ffmpeg 指令不執行")
    pa.add_argument("--force", action="store_true", help="覆寫已存在的輸出")
    pa.set_defaults(func=cmd_assemble)

    pc = sub.add_parser("clip", help="從合成的 Reels mp4 切出 reels_clips 定義的片段（-c copy 快速無損）")
    pc.add_argument("path", nargs="?", default=".", help="集資料夾路徑（預設：當前目錄）")
    pc.add_argument("--name", dest="names", action="append", help="只跑指定 clip name（可多次）；省略 = 跑全部")
    pc.add_argument("--force", action="store_true", help="覆寫已存在的片段")
    pc.set_defaults(func=cmd_clip)

    pe = sub.add_parser("edit", help="在瀏覽器編輯：裁切 / 刪段 / 改字")
    pe.add_argument("path", nargs="?", default=".", help="集資料夾路徑（預設：當前目錄）")
    pe.set_defaults(func=cmd_edit)

    pu = sub.add_parser("ui", help="開啟瀏覽器 dashboard（無預選集）")
    pu.set_defaults(func=cmd_ui)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        rc = args.func(args)
        sys.exit(rc or 0)
    except FileNotFoundError as e:
        print(f"✗ 檔案缺失：{e}", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
