"""podcast init：在現有集資料夾建立子目錄結構 + 範本。"""
import re
import sys
from pathlib import Path
from podcast_toolkit import config

SUBDIRS = ["01_母帶", "03_成品", "04_工作檔"]


def parse_folder_name(folder: Path):
    """從 'YYYYMMDD 集名' 解析 date / name。"""
    m = re.match(r"^(\d{8})\s+(.+)$", folder.name)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def run(episode_dir: Path) -> int:
    episode_dir = episode_dir.resolve()
    if not episode_dir.exists():
        print(f"✗ 資料夾不存在：{episode_dir}", file=sys.stderr)
        return 3

    date, name = parse_folder_name(episode_dir)
    if not date:
        print(f"⚠ 資料夾名不符合 'YYYYMMDD 集名' 慣例：{episode_dir.name}")
        print("  episode.yaml 的 date / name 會留空，請手動填入")
        date, name = "", ""
    else:
        print(f"✓ 解析資料夾名 → date={date}, name={name}")

    # 建子資料夾（已存在就跳過）
    for sub in SUBDIRS:
        (episode_dir / sub).mkdir(exist_ok=True)
    print(f"✓ 建立 / 確認子資料夾：{', '.join(SUBDIRS)}")

    toolkit = config.toolkit_root()

    # 複製 episode.yaml 範本（不覆蓋）
    ep_yaml = episode_dir / "episode.yaml"
    if ep_yaml.exists():
        print("⚠ episode.yaml 已存在，不覆蓋")
    else:
        template = (toolkit / "templates" / "episode.yaml").read_text(encoding="utf-8")
        # 用展開後的 name 改 'YYYYMMDD' / '集名' 預留位
        rendered = template
        if date:
            rendered = rendered.replace("YYYYMMDD", date)
        if name:
            rendered = rendered.replace("集名", name)
        ep_yaml.write_text(rendered, encoding="utf-8")
        print("✓ 產生 episode.yaml")

    # 複製 TODO.md（不覆蓋）
    todo = episode_dir / "TODO.md"
    if todo.exists():
        print("⚠ TODO.md 已存在，不覆蓋")
    else:
        template = (toolkit / "templates" / "TODO.md").read_text(encoding="utf-8")
        rendered = template
        if date:
            rendered = rendered.replace("{date}", date)
        if name:
            rendered = rendered.replace("{name}", name)
        todo.write_text(rendered, encoding="utf-8")
        print("✓ 產生 TODO.md")

    print()
    print("完成。下一步：")
    print(f"  1. 把錄音放進 {episode_dir.name}/01_母帶/")
    print(f"  2. 轉好的字幕放進 {episode_dir.name}/03_成品/{name}_final.srt")
    print(f"  3. 跑 podcast resegment \"{episode_dir}\"")
    return 0
