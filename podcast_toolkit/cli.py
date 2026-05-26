"""podcast CLI 入口。"""
import argparse
import sys
from pathlib import Path


def cmd_init(args):
    from podcast_toolkit import init as init_mod
    return init_mod.run(Path(args.path))


def cmd_resegment(args):
    from podcast_toolkit import resegment
    return resegment.run(Path(args.path), force=args.force)


def cmd_assemble(args):
    from podcast_toolkit import assemble
    return assemble.run(Path(args.path), dry_run=args.dry_run, force=args.force)


def cmd_relink(args):
    from podcast_toolkit import relink
    return relink.run(Path(args.path))


def build_parser():
    p = argparse.ArgumentParser(prog="podcast", description="Podcast 剪輯 toolkit")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="腳手架：建立子資料夾 + episode.yaml + symlink")
    pi.add_argument("path", nargs="?", default=".", help="集資料夾路徑（預設：當前目錄）")
    pi.set_defaults(func=cmd_init)

    pr = sub.add_parser("resegment", help="字幕重新斷句 + 錯字修正")
    pr.add_argument("path", nargs="?", default=".", help="集資料夾路徑（預設：當前目錄）")
    pr.add_argument("--force", action="store_true", help="覆寫已存在的輸出")
    pr.set_defaults(func=cmd_resegment)

    pa = sub.add_parser("assemble", help="合成片頭+正片+片尾 → YT 完整版")
    pa.add_argument("path", nargs="?", default=".", help="集資料夾路徑（預設：當前目錄）")
    pa.add_argument("--dry-run", action="store_true", help="只印 ffmpeg 指令不執行")
    pa.add_argument("--force", action="store_true", help="覆寫已存在的輸出")
    pa.set_defaults(func=cmd_assemble)

    prl = sub.add_parser("relink", help="修復斷掉的 symlink")
    prl.add_argument("path", nargs="?", default=".", help="集資料夾路徑（預設：當前目錄）")
    prl.set_defaults(func=cmd_relink)

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
