"""server_lock 模組測試。"""
import os
from pathlib import Path

import pytest

from podcast_toolkit import server_lock


def test_acquire_creates_lockfile(tmp_path: Path):
    lock = tmp_path / ".server.lock"
    assert server_lock.acquire(lock, port=12345) is True
    assert lock.exists()
    content = lock.read_text(encoding="utf-8").splitlines()
    assert content[0] == str(os.getpid())
    assert content[1] == "12345"


def test_acquire_fails_if_pid_alive(tmp_path: Path):
    lock = tmp_path / ".server.lock"
    # 先以自己的 pid 寫入（自己一定活著）
    lock.write_text(f"{os.getpid()}\n9999\n", encoding="utf-8")
    assert server_lock.acquire(lock, port=12345) is False


def test_acquire_clears_stale_lock(tmp_path: Path):
    lock = tmp_path / ".server.lock"
    # 寫一個極不可能存在的 pid
    lock.write_text("9999999\n9999\n", encoding="utf-8")
    assert server_lock.acquire(lock, port=12345) is True
    assert str(os.getpid()) in lock.read_text(encoding="utf-8")


def test_release_idempotent(tmp_path: Path):
    lock = tmp_path / ".server.lock"
    server_lock.release(lock)  # 不存在也不報錯
    lock.write_text("123\n456\n", encoding="utf-8")
    server_lock.release(lock)
    assert not lock.exists()
    server_lock.release(lock)  # 再 release 一次


def test_read_returns_pid_port(tmp_path: Path):
    lock = tmp_path / ".server.lock"
    assert server_lock.read(lock) is None
    lock.write_text("123\n456\n", encoding="utf-8")
    assert server_lock.read(lock) == (123, 456)
    lock.write_text("garbage\n", encoding="utf-8")
    assert server_lock.read(lock) is None
