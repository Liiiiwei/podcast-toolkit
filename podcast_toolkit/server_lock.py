"""跨進程 lockfile：避免多個 podcast server 同時跑。"""
from __future__ import annotations
import os
from pathlib import Path


def acquire(lock_path: Path, port: int) -> bool:
    """取 lock；成功回 True。失敗（已有活著的 process）回 False。"""
    if lock_path.exists():
        try:
            pid = int(lock_path.read_text(encoding="utf-8").splitlines()[0])
            os.kill(pid, 0)
            return False
        except (ValueError, ProcessLookupError, OSError, IndexError):
            try:
                lock_path.unlink()
            except OSError:
                pass
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(f"{os.getpid()}\n{port}\n", encoding="utf-8")
    return True


def release(lock_path: Path) -> None:
    """釋放 lock（不存在也不報錯）。"""
    try:
        lock_path.unlink()
    except OSError:
        pass


def read(lock_path: Path) -> tuple[int, int] | None:
    """讀 lockfile 回 (pid, port)，無效則回 None。"""
    if not lock_path.exists():
        return None
    try:
        lines = lock_path.read_text(encoding="utf-8").splitlines()
        return (int(lines[0]), int(lines[1]))
    except (ValueError, IndexError, OSError):
        return None
