"""podcast relink：重建集資料夾內 02_片頭片尾/ 的 symlink。"""
import sys
from pathlib import Path
from podcast_toolkit import config
from podcast_toolkit.init import ASSET_SYMLINKS


def run(episode_dir: Path) -> int:
    episode_dir = Path(episode_dir).resolve()
    if not episode_dir.exists():
        print(f"✗ 資料夾不存在：{episode_dir}", file=sys.stderr)
        return 3

    intro_outro = episode_dir / "02_片頭片尾"
    if not intro_outro.exists():
        intro_outro.mkdir()

    toolkit = config.toolkit_root()
    relinked = 0
    for link_name, rel_target in ASSET_SYMLINKS.items():
        link_path = intro_outro / link_name
        target = toolkit / rel_target
        if not target.exists():
            print(f"⚠ toolkit 內找不到資產：{target}", file=sys.stderr)
            continue
        if link_path.is_symlink() or link_path.exists():
            link_path.unlink()
        link_path.symlink_to(target)
        relinked += 1

    print(f"✓ 重建 {relinked} 個 symlink → {toolkit / 'assets'}")
    return 0
