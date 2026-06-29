# 全 UI 化 podcast-toolkit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓使用者雙擊 macOS `.app` 進入瀏覽器 Dashboard 選集 / 編輯 / 合成，CLI 保留並存。

**Architecture:** 在 `podcast_toolkit.web.api.build_app` 增加「Dashboard 模式」（`ep=None`），用同一 FastAPI server 依 holder 狀態切換頁面；新增 `launcher.py` 作 .app 入口；用 py2app 打包；全程序共用一個 global lockfile。

**Tech Stack:** FastAPI、uvicorn、vanilla JS、py2app、pytest、macOS osascript。

**Spec:** [docs/superpowers/specs/2026-06-04-fully-web-ui-design.md](../../superpowers/specs/2026-06-04-fully-web-ui-design.md)

---

## 檔案結構

**新增：**
- `podcast_toolkit/server_lock.py` — 共用 lockfile 模組（acquire / release / read）
- `podcast_toolkit/web/dashboard.py` — Dashboard 純函式（episode_stage / recent / list_episodes）
- `podcast_toolkit/launcher.py` — `.app` 入口
- `podcast_toolkit/web/static/dashboard.html` / `dashboard.css` / `dashboard.js`
- `setup_app.py` — py2app 設定
- `tests/test_server_lock.py`
- `tests/test_dashboard.py`
- `tests/test_api_dashboard.py`

**改動：**
- `podcast_toolkit/web/api.py` — `build_app(ep=None)`、新增 Dashboard endpoints、加 `_require_ep()` guard、config 加 `episode_roots`
- `podcast_toolkit/cli.py` — 加 `podcast ui` subcommand
- `podcast_toolkit/edit.py` — `run()` 拆 `run_with_ep` / `run_dashboard`，改用 global lockfile
- `podcast_toolkit/web/static/index.html` — 加「← 回 Dashboard」按鈕
- `podcast_toolkit/web/static/app.js` — 綁定 close endpoint

---

## Task 1: 共用 lockfile 模組 `server_lock.py`

**Files:**
- Create: `podcast_toolkit/server_lock.py`
- Create: `tests/test_server_lock.py`

- [ ] **Step 1.1: 寫 failing test — acquire 在無 lock 時成功並寫 pid+port**

`tests/test_server_lock.py`:
```python
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
```

- [ ] **Step 1.2: Run test，確認 fail**

Run: `pytest tests/test_server_lock.py::test_acquire_creates_lockfile -v`
Expected: `ModuleNotFoundError: No module named 'podcast_toolkit.server_lock'`

- [ ] **Step 1.3: 寫最小實作**

`podcast_toolkit/server_lock.py`:
```python
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
```

- [ ] **Step 1.4: Run test，確認 pass**

Run: `pytest tests/test_server_lock.py -v`
Expected: PASS

- [ ] **Step 1.5: 加 4 個額外 test**

加在 `tests/test_server_lock.py` 末尾：
```python
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
```

- [ ] **Step 1.6: Run 全部 test**

Run: `pytest tests/test_server_lock.py -v`
Expected: 5 passed

- [ ] **Step 1.7: Commit**

```bash
git add podcast_toolkit/server_lock.py tests/test_server_lock.py
git commit -m "feat: 共用 server_lock 模組（取代 edit.py 內嵌的 lockfile）"
```

---

## Task 2: dashboard.py — episode_stage()

**Files:**
- Create: `podcast_toolkit/web/dashboard.py`
- Create: `tests/test_dashboard.py`

- [ ] **Step 2.1: 寫 failing tests（5 個 stage 分支）**

`tests/test_dashboard.py`:
```python
"""dashboard.py 純函式測試。"""
from pathlib import Path

import pytest

from podcast_toolkit.web import dashboard


def test_stage_broken_when_no_episode_yaml(tmp_path: Path):
    folder = tmp_path / "no_yaml"
    folder.mkdir()
    assert dashboard.episode_stage(folder) == "broken"


def test_stage_empty_when_no_main_video(tmp_episode_dir: Path):
    # tmp_episode_dir 沒放 main_video
    assert dashboard.episode_stage(tmp_episode_dir) == "empty"


def test_stage_needs_transcribe(tmp_episode_dir: Path):
    (tmp_episode_dir / "01_母帶" / "測試集.mp4").write_bytes(b"X")
    assert dashboard.episode_stage(tmp_episode_dir) == "needs_transcribe"


def test_stage_needs_assemble(tmp_episode_dir: Path):
    (tmp_episode_dir / "01_母帶" / "測試集.mp4").write_bytes(b"X")
    (tmp_episode_dir / "03_成品" / "測試集_v2.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n")
    assert dashboard.episode_stage(tmp_episode_dir) == "needs_assemble"


def test_stage_done_when_output_exists(tmp_episode_dir: Path):
    (tmp_episode_dir / "01_母帶" / "測試集.mp4").write_bytes(b"X")
    (tmp_episode_dir / "03_成品" / "測試集_v2.srt").write_text("x")
    (tmp_episode_dir / "03_成品" / "測試集_yt.mp4").write_bytes(b"OUT")
    assert dashboard.episode_stage(tmp_episode_dir) == "done"
```

- [ ] **Step 2.2: Run，確認 fail**

Run: `pytest tests/test_dashboard.py -v`
Expected: `ImportError` 或 `AttributeError`

- [ ] **Step 2.3: 寫 dashboard.py 最小實作**

`podcast_toolkit/web/dashboard.py`:
```python
"""Dashboard 純函式：episode stage / recent / list_episodes。

不依賴 FastAPI，方便單元測試。
"""
from __future__ import annotations
from pathlib import Path

from podcast_toolkit.episode import Episode


def episode_stage(ep_dir: Path) -> str:
    """回傳集數階段：broken / empty / needs_transcribe / needs_assemble / done。"""
    try:
        ep = Episode(ep_dir)
    except Exception:
        return "broken"
    if not ep.main_video().exists():
        return "empty"
    if not ep.output_v2_srt().exists():
        return "needs_transcribe"
    if not (ep.output_yt_video().exists() or ep.output_reels_video().exists()):
        return "needs_assemble"
    return "done"
```

- [ ] **Step 2.4: Run，確認 pass**

Run: `pytest tests/test_dashboard.py -v`
Expected: 5 passed

- [ ] **Step 2.5: Commit**

```bash
git add podcast_toolkit/web/dashboard.py tests/test_dashboard.py
git commit -m "feat(dashboard): episode_stage() 五階段判定"
```

---

## Task 3: dashboard.py — recent 讀寫

**Files:**
- Modify: `podcast_toolkit/web/dashboard.py`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 3.1: 加 failing tests**

加到 `tests/test_dashboard.py` 末尾：
```python
def test_load_recent_returns_empty_on_missing(tmp_path: Path):
    cfg = tmp_path / "config.json"
    assert dashboard.load_recent(cfg) == []


def test_load_recent_returns_empty_on_bad_json(tmp_path: Path):
    cfg = tmp_path / "config.json"
    cfg.write_text("not json", encoding="utf-8")
    assert dashboard.load_recent(cfg) == []


def test_save_then_load_roundtrip(tmp_path: Path):
    cfg = tmp_path / "config.json"
    dashboard.save_recent(cfg, ["/a", "/b"])
    assert dashboard.load_recent(cfg) == ["/a", "/b"]


def test_save_preserves_other_config_keys(tmp_path: Path):
    """save_recent 不能炸掉 config.json 內既有的 xai_api_key 等欄位。"""
    import json
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"xai_api_key": "K"}), encoding="utf-8")
    dashboard.save_recent(cfg, ["/a"])
    loaded = json.loads(cfg.read_text(encoding="utf-8"))
    assert loaded["xai_api_key"] == "K"
    assert loaded["recent_episodes"] == ["/a"]


def test_add_recent_prepends_and_dedups(tmp_path: Path):
    cfg = tmp_path / "config.json"
    dashboard.save_recent(cfg, ["/a", "/b"])
    dashboard.add_recent(cfg, "/b")  # 已存在 → 移到最前
    assert dashboard.load_recent(cfg) == ["/b", "/a"]
    dashboard.add_recent(cfg, "/c")  # 新的 → 加最前
    assert dashboard.load_recent(cfg) == ["/c", "/b", "/a"]


def test_add_recent_caps_at_20(tmp_path: Path):
    cfg = tmp_path / "config.json"
    for i in range(25):
        dashboard.add_recent(cfg, f"/p{i}")
    recent = dashboard.load_recent(cfg)
    assert len(recent) == 20
    assert recent[0] == "/p24"  # 最新在最前
    assert recent[-1] == "/p5"  # 最舊的 5 個被砍掉


def test_save_atomic(tmp_path: Path):
    """save 走 .tmp + rename，中間 .tmp 不能殘留。"""
    cfg = tmp_path / "config.json"
    dashboard.save_recent(cfg, ["/a"])
    assert not (tmp_path / "config.json.tmp").exists()
```

- [ ] **Step 3.2: Run，確認 7 個新 test fail**

Run: `pytest tests/test_dashboard.py -v`
Expected: 7 failed（attribute error）

- [ ] **Step 3.3: 在 dashboard.py 加 recent 函式**

加到 `podcast_toolkit/web/dashboard.py` 末尾：
```python
import json
import os

RECENT_KEY = "recent_episodes"
RECENT_MAX = 20


def _load_config_dict(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _atomic_write_json(config_path: Path, data: dict) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, config_path)


def load_recent(config_path: Path) -> list[str]:
    cfg = _load_config_dict(config_path)
    raw = cfg.get(RECENT_KEY) or []
    return [str(p) for p in raw if isinstance(p, str)]


def save_recent(config_path: Path, recent: list[str]) -> None:
    cfg = _load_config_dict(config_path)
    cfg[RECENT_KEY] = recent[:RECENT_MAX]
    _atomic_write_json(config_path, cfg)


def add_recent(config_path: Path, path: str) -> None:
    recent = load_recent(config_path)
    recent = [p for p in recent if p != path]
    recent.insert(0, path)
    save_recent(config_path, recent)
```

- [ ] **Step 3.4: Run，確認 pass**

Run: `pytest tests/test_dashboard.py -v`
Expected: 12 passed

- [ ] **Step 3.5: Commit**

```bash
git add podcast_toolkit/web/dashboard.py tests/test_dashboard.py
git commit -m "feat(dashboard): recent_episodes 讀寫（atomic、最多 20 筆、不破壞既有 config）"
```

---

## Task 4: dashboard.py — list_episodes()

**Files:**
- Modify: `podcast_toolkit/web/dashboard.py`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 4.1: 加 failing tests**

加到 `tests/test_dashboard.py` 末尾：
```python
def _make_initialized_episode(parent: Path, folder_name: str) -> Path:
    """快速建一個 init 過的 episode 資料夾。"""
    import yaml
    ep = parent / folder_name
    ep.mkdir()
    for sub in ("01_母帶", "03_成品", "04_工作檔"):
        (ep / sub).mkdir()
    date, name = folder_name.split(" ", 1)
    (ep / "episode.yaml").write_text(
        yaml.safe_dump(
            {
                "date": int(date),
                "name": name,
                "main_video": "01_母帶/{name}.mp4",
                "main_srt": "01_母帶/{name}.srt",
                "fixes": [], "card_fixes": [],
                "force_break": [], "force_join": [],
            },
            allow_unicode=True, sort_keys=False,
        ),
        encoding="utf-8",
    )
    return ep


def test_list_episodes_from_root(tmp_path: Path):
    root = tmp_path / "Downloads"
    root.mkdir()
    ep1 = _make_initialized_episode(root, "20260601 第一集")
    (ep1 / "01_母帶" / "第一集.mp4").write_bytes(b"X")  # needs_transcribe
    ep2 = _make_initialized_episode(root, "20260608 第二集")  # empty → 不列

    result = dashboard.list_episodes(roots=[str(root)], recent=[])
    assert result["warnings"] == []
    paths = [e["path"] for e in result["episodes"]]
    assert str(ep1) in paths
    assert str(ep2) not in paths  # empty 不列


def test_list_episodes_skips_non_episode_folders(tmp_path: Path):
    root = tmp_path / "Downloads"
    root.mkdir()
    (root / "random_folder").mkdir()
    (root / "file.txt").write_text("X")
    result = dashboard.list_episodes(roots=[str(root)], recent=[])
    assert result["episodes"] == []


def test_list_episodes_warning_for_missing_root(tmp_path: Path):
    missing = tmp_path / "nope"
    result = dashboard.list_episodes(roots=[str(missing)], recent=[])
    assert result["episodes"] == []
    assert len(result["warnings"]) == 1
    assert "nope" in result["warnings"][0]


def test_list_episodes_dedup_across_recent_and_roots(tmp_path: Path):
    root = tmp_path / "Downloads"
    root.mkdir()
    ep1 = _make_initialized_episode(root, "20260601 集A")
    (ep1 / "01_母帶" / "集A.mp4").write_bytes(b"X")

    result = dashboard.list_episodes(roots=[str(root)], recent=[str(ep1)])
    paths = [e["path"] for e in result["episodes"]]
    assert paths.count(str(ep1)) == 1


def test_list_episodes_includes_name_and_stage(tmp_path: Path):
    root = tmp_path / "Downloads"
    root.mkdir()
    ep1 = _make_initialized_episode(root, "20260601 第一集")
    (ep1 / "01_母帶" / "第一集.mp4").write_bytes(b"X")

    result = dashboard.list_episodes(roots=[str(root)], recent=[])
    assert len(result["episodes"]) == 1
    e = result["episodes"][0]
    assert e["name"] == "第一集"
    assert e["stage"] == "needs_transcribe"
    assert "date" in e


def test_list_episodes_recent_includes_paths_outside_roots(tmp_path: Path):
    """recent 裡的路徑不在 roots 也要列出（使用者過去手動開過的）。"""
    elsewhere = tmp_path / "OtherPlace"
    elsewhere.mkdir()
    ep = _make_initialized_episode(elsewhere, "20260615 別處集")
    (ep / "01_母帶" / "別處集.mp4").write_bytes(b"X")

    result = dashboard.list_episodes(roots=[], recent=[str(ep)])
    paths = [e["path"] for e in result["episodes"]]
    assert str(ep) in paths
```

- [ ] **Step 4.2: Run，確認 fail**

Run: `pytest tests/test_dashboard.py -v -k list_episodes`
Expected: 6 failed

- [ ] **Step 4.3: 實作 list_episodes**

加到 `podcast_toolkit/web/dashboard.py` 末尾：
```python
def _episode_meta(ep_dir: Path) -> dict | None:
    """從一個 episode 資料夾抽出 dashboard card 需要的 metadata。
    回 None 代表這資料夾不是 episode 或 stage='empty'（不顯示）。"""
    stage = episode_stage(ep_dir)
    if stage == "empty":
        return None
    name = ep_dir.name
    date = ""
    if " " in name and name[:8].isdigit():
        date = name[:8]
        name = name[9:]
    try:
        mtime = ep_dir.stat().st_mtime
    except OSError:
        mtime = 0
    return {
        "path": str(ep_dir),
        "name": name,
        "date": date,
        "stage": stage,
        "mtime": mtime,
    }


def list_episodes(roots: list[str], recent: list[str]) -> dict:
    """掃 roots + recent，回 {episodes: [...], warnings: [...]}。
    episodes 依 mtime 倒序；同一 path 去重。"""
    warnings: list[str] = []
    seen: dict[str, dict] = {}

    for raw_root in roots:
        root = Path(raw_root).expanduser()
        if not root.is_dir():
            warnings.append(f"找不到資料夾：{raw_root}")
            continue
        try:
            children = list(root.iterdir())
        except PermissionError:
            warnings.append(f"沒有權限讀取：{raw_root}")
            continue
        for child in children:
            if not child.is_dir():
                continue
            if not (child / "episode.yaml").is_file():
                continue
            meta = _episode_meta(child)
            if meta is not None:
                seen[meta["path"]] = meta

    for raw_path in recent:
        ep_dir = Path(raw_path).expanduser()
        if not ep_dir.is_dir():
            continue
        if not (ep_dir / "episode.yaml").is_file():
            continue
        meta = _episode_meta(ep_dir)
        if meta is not None and meta["path"] not in seen:
            seen[meta["path"]] = meta

    episodes = sorted(seen.values(), key=lambda e: e["mtime"], reverse=True)
    return {"episodes": episodes, "warnings": warnings}
```

- [ ] **Step 4.4: Run，確認 pass**

Run: `pytest tests/test_dashboard.py -v`
Expected: 18 passed

- [ ] **Step 4.5: Commit**

```bash
git add podcast_toolkit/web/dashboard.py tests/test_dashboard.py
git commit -m "feat(dashboard): list_episodes() 掃 roots + recent 去重排序"
```

---

## Task 5: api.py — `build_app(ep=None)` + `_require_ep()` guard

**Files:**
- Modify: `podcast_toolkit/web/api.py`
- Modify: `tests/test_api_routes.py`

- [ ] **Step 5.1: 寫 failing test — build_app(None) 不應炸**

加到 `tests/test_api_routes.py` 末尾：
```python
def test_build_app_with_none_ep_does_not_crash():
    from podcast_toolkit.web.api import build_app
    app = build_app(ep=None, shutdown=lambda: None)
    client = TestClient(app)
    # 任一既有 endpoint 在 ep=None 時應該回 409，不是 500
    r = client.get("/api/episode")
    assert r.status_code == 409
    assert "尚未選集" in r.json()["detail"]
```

- [ ] **Step 5.2: Run，確認 fail**

Run: `pytest tests/test_api_routes.py::test_build_app_with_none_ep_does_not_crash -v`
Expected: TypeError or 500

- [ ] **Step 5.3: 改 `build_app` 簽名與 helper**

`podcast_toolkit/web/api.py` 第 144 行 `def build_app(ep: Episode, shutdown: Callable[[], None]) -> FastAPI:` 改成：
```python
def build_app(ep: Episode | None, shutdown: Callable[[], None]) -> FastAPI:
```

同檔 `holder = {"ep": ep}` 行下方（約 148 行附近，在第一個 `@app.get("/")` 之前）插入 helper：
```python
    def _require_ep() -> Episode:
        ep = holder["ep"]
        if ep is None:
            raise HTTPException(status_code=409, detail="尚未選集，請先在 Dashboard 選一集")
        return ep
```

- [ ] **Step 5.4: 把所有 `holder["ep"]` 直接用法改成 `_require_ep()`**

掃描 `podcast_toolkit/web/api.py` 把以下情境的 `holder["ep"]` 改成 `_require_ep()`（**不要動** `holder["ep"] = ...` 這種賦值）：

- `get_episode` (約 158 行)：`return JSONResponse(episode_io.load_state(_require_ep()))`
- `get_video` (約 305-320 行)：`ep = _require_ep()`
- `save` (約 322 行)：`ep = _require_ep()`；`episode_io.save_state(ep, payload)`；`holder["ep"] = Episode(ep.dir)`
- `post_upload` (約 343 行)：`ep = _require_ep()`
- `get_files` (約 369 行)：`ep = _require_ep()`
- `post_transcribe` (約 425 行)：`ep = _require_ep()`
- 所有其他 `holder["ep"]` 讀取處全部替換成 `_require_ep()`

**例外**（不要改）：
- `pick_episode`（用 `ep = holder["ep"]` 拿 `default_dir`，但若無 ep 可 fallback 到 `~/Downloads`）→ 改成：
```python
    @app.post("/api/episode/pick")
    def pick_episode():
        ep = holder["ep"]
        default_dir = str(ep.dir.parent) if ep else str(Path.home() / "Downloads")
        # ... 其餘不變
```
- `new_episode`（`parent = holder["ep"].dir.parent`）→ 改成：
```python
        ep = holder["ep"]
        parent = ep.dir.parent if ep else (Path.home() / "Downloads")
```

- [ ] **Step 5.5: Run 新 test + 既有 test**

Run: `pytest tests/test_api_routes.py -v`
Expected: 全部 pass，新 test 也 pass

- [ ] **Step 5.6: Commit**

```bash
git add podcast_toolkit/web/api.py tests/test_api_routes.py
git commit -m "feat(api): build_app(ep=None) + _require_ep guard（dashboard 模式準備）"
```

---

## Task 6: api.py — `GET /` 依 holder 路由

**Files:**
- Modify: `podcast_toolkit/web/api.py`
- Create: `tests/test_api_dashboard.py`
- Create: `podcast_toolkit/web/static/dashboard.html`（暫放 placeholder）

- [ ] **Step 6.1: 建暫時的 dashboard.html placeholder（讓 GET / 有檔可回）**

`podcast_toolkit/web/static/dashboard.html`:
```html
<!doctype html>
<html lang="zh-Hant">
  <head>
    <meta charset="utf-8" />
    <title>podcast — Dashboard</title>
  </head>
  <body>
    <h1>Dashboard placeholder</h1>
  </body>
</html>
```

- [ ] **Step 6.2: 寫 failing test**

`tests/test_api_dashboard.py`:
```python
"""Dashboard 模式 API 測試（ep=None）。"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from podcast_toolkit.episode import Episode
from podcast_toolkit.web.api import build_app


@pytest.fixture
def dashboard_client():
    app = build_app(ep=None, shutdown=lambda: None)
    return TestClient(app)


@pytest.fixture
def edit_client(tmp_episode_dir: Path):
    main_video = tmp_episode_dir / "01_母帶" / "測試集.mp4"
    main_video.write_bytes(b"FAKE" * 1000)
    ep = Episode(tmp_episode_dir)
    app = build_app(ep=ep, shutdown=lambda: None)
    return TestClient(app)


def test_get_root_serves_dashboard_when_no_ep(dashboard_client):
    r = dashboard_client.get("/")
    assert r.status_code == 200
    assert "Dashboard" in r.text


def test_get_root_serves_edit_ui_when_ep(edit_client):
    r = edit_client.get("/")
    assert r.status_code == 200
    # index.html 標題
    assert "podcast edit" in r.text
```

- [ ] **Step 6.3: Run，確認 fail**

Run: `pytest tests/test_api_dashboard.py -v`
Expected: 2 failed（GET / 仍只回 index.html）

- [ ] **Step 6.4: 改 `GET /`**

在 `podcast_toolkit/web/api.py` 找到原本的：
```python
    @app.get("/")
    def root():
        return FileResponse(STATIC_DIR / "index.html")
```
改成：
```python
    @app.get("/")
    def root():
        if holder["ep"] is None:
            return FileResponse(STATIC_DIR / "dashboard.html")
        return FileResponse(STATIC_DIR / "index.html")
```

- [ ] **Step 6.5: Run，確認 pass**

Run: `pytest tests/test_api_dashboard.py -v && pytest tests/test_api_routes.py -v`
Expected: 全 pass

- [ ] **Step 6.6: Commit**

```bash
git add podcast_toolkit/web/api.py podcast_toolkit/web/static/dashboard.html tests/test_api_dashboard.py
git commit -m "feat(api): GET / 依 holder 路由到 dashboard 或 edit"
```

---

## Task 7: api.py — `GET /api/episodes`

**Files:**
- Modify: `podcast_toolkit/web/api.py`
- Modify: `tests/test_api_dashboard.py`

- [ ] **Step 7.1: 加 failing test**

加到 `tests/test_api_dashboard.py` 末尾：
```python
def test_get_episodes_returns_list(dashboard_client, monkeypatch, tmp_path):
    """掛掉 CONFIG_PATH 與 episode_roots，確保 endpoint 串得通。"""
    from podcast_toolkit.web import api as api_mod
    fake_config = tmp_path / "config.json"
    fake_config.write_text('{"episode_roots": []}', encoding="utf-8")
    monkeypatch.setattr(api_mod, "CONFIG_PATH", fake_config)

    r = dashboard_client.get("/api/episodes")
    assert r.status_code == 200
    body = r.json()
    assert "episodes" in body
    assert "warnings" in body
    assert isinstance(body["episodes"], list)
```

- [ ] **Step 7.2: Run，確認 fail**

Run: `pytest tests/test_api_dashboard.py::test_get_episodes_returns_list -v`
Expected: 404

- [ ] **Step 7.3: 加 endpoint**

在 `podcast_toolkit/web/api.py` 檔頂 import 區加：
```python
from podcast_toolkit.web import dashboard as dashboard_mod
```

在 `@app.get("/api/episode")` 上面（dashboard 相關 endpoint 集中放）加：
```python
    @app.get("/api/episodes")
    def list_episodes():
        cfg = _load_config()
        roots = cfg.get("episode_roots") or [str(Path.home() / "Downloads")]
        recent = dashboard_mod.load_recent(CONFIG_PATH)
        return JSONResponse(dashboard_mod.list_episodes(roots=roots, recent=recent))
```

- [ ] **Step 7.4: Run，確認 pass**

Run: `pytest tests/test_api_dashboard.py -v`
Expected: 3 passed

- [ ] **Step 7.5: Commit**

```bash
git add podcast_toolkit/web/api.py tests/test_api_dashboard.py
git commit -m "feat(api): GET /api/episodes — dashboard 集數列表"
```

---

## Task 8: api.py — `POST /api/episodes/open` / `close`

**Files:**
- Modify: `podcast_toolkit/web/api.py`
- Modify: `tests/test_api_dashboard.py`

- [ ] **Step 8.1: 加 failing tests**

加到 `tests/test_api_dashboard.py` 末尾：
```python
def test_post_open_switches_to_edit_mode(dashboard_client, tmp_episode_dir, monkeypatch, tmp_path):
    """open 後同一 client 的 GET / 應回 edit UI。"""
    from podcast_toolkit.web import api as api_mod
    fake_config = tmp_path / "config.json"
    monkeypatch.setattr(api_mod, "CONFIG_PATH", fake_config)
    (tmp_episode_dir / "01_母帶" / "測試集.mp4").write_bytes(b"X")

    r = dashboard_client.post("/api/episodes/open", json={"path": str(tmp_episode_dir)})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    r2 = dashboard_client.get("/")
    assert "podcast edit" in r2.text

    # recent 已寫入
    import json
    recent = json.loads(fake_config.read_text(encoding="utf-8"))["recent_episodes"]
    assert recent == [str(tmp_episode_dir)]


def test_post_open_400_for_missing_path(dashboard_client, tmp_path):
    r = dashboard_client.post("/api/episodes/open", json={"path": str(tmp_path / "nope")})
    assert r.status_code == 400


def test_post_open_400_for_no_episode_yaml(dashboard_client, tmp_path):
    folder = tmp_path / "not_episode"
    folder.mkdir()
    r = dashboard_client.post("/api/episodes/open", json={"path": str(folder)})
    assert r.status_code == 400


def test_post_close_clears_ep(edit_client):
    r = edit_client.post("/api/episodes/close")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    # 之後 GET / 應該回 dashboard
    r2 = edit_client.get("/")
    assert "Dashboard" in r2.text
```

- [ ] **Step 8.2: Run，確認 fail**

Run: `pytest tests/test_api_dashboard.py -v -k "open or close"`
Expected: 4 failed

- [ ] **Step 8.3: 加 endpoints**

在 `podcast_toolkit/web/api.py` 找個合適位置（建議在 `pick_episode` 附近，約 187 行之前）加：
```python
    @app.post("/api/episodes/open")
    def open_episode(payload: dict):
        raw = (payload.get("path") or "").strip()
        if not raw:
            raise HTTPException(status_code=400, detail="缺少 path")
        target = Path(os.path.expanduser(raw)).resolve()
        if not target.is_dir():
            raise HTTPException(status_code=400, detail=f"資料夾不存在：{target}")
        if not (target / "episode.yaml").is_file():
            raise HTTPException(status_code=400, detail=f"不是 episode 資料夾：{target}")
        try:
            new_ep = Episode(target)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"無法載入 episode：{e}")
        holder["ep"] = new_ep
        dashboard_mod.add_recent(CONFIG_PATH, str(target))
        return JSONResponse({"ok": True})

    @app.post("/api/episodes/close")
    def close_episode():
        holder["ep"] = None
        return JSONResponse({"ok": True})
```

- [ ] **Step 8.4: Run，確認 pass**

Run: `pytest tests/test_api_dashboard.py -v`
Expected: 7 passed

- [ ] **Step 8.5: Commit**

```bash
git add podcast_toolkit/web/api.py tests/test_api_dashboard.py
git commit -m "feat(api): POST /api/episodes/open + /close — dashboard 與 edit 切換"
```

---

## Task 9: api.py — `/api/config` 加 `episode_roots`

**Files:**
- Modify: `podcast_toolkit/web/api.py`
- Modify: `tests/test_api_dashboard.py`

- [ ] **Step 9.1: 加 failing tests**

加到 `tests/test_api_dashboard.py` 末尾：
```python
def test_config_includes_episode_roots(dashboard_client, monkeypatch, tmp_path):
    from podcast_toolkit.web import api as api_mod
    fake_config = tmp_path / "config.json"
    monkeypatch.setattr(api_mod, "CONFIG_PATH", fake_config)

    r = dashboard_client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert "episode_roots" in body
    assert isinstance(body["episode_roots"], list)


def test_post_config_saves_episode_roots(dashboard_client, monkeypatch, tmp_path):
    from podcast_toolkit.web import api as api_mod
    fake_config = tmp_path / "config.json"
    monkeypatch.setattr(api_mod, "CONFIG_PATH", fake_config)

    r = dashboard_client.post("/api/config", json={"episode_roots": ["~/Podcasts", "~/Downloads"]})
    assert r.status_code == 200
    assert r.json()["episode_roots"] == ["~/Podcasts", "~/Downloads"]

    import json
    saved = json.loads(fake_config.read_text(encoding="utf-8"))
    assert saved["episode_roots"] == ["~/Podcasts", "~/Downloads"]


def test_post_config_rejects_non_list_episode_roots(dashboard_client):
    r = dashboard_client.post("/api/config", json={"episode_roots": "not a list"})
    assert r.status_code == 400
```

- [ ] **Step 9.2: Run，確認 fail**

Run: `pytest tests/test_api_dashboard.py -v -k config`
Expected: 3 failed

- [ ] **Step 9.3: 改 `/api/config` GET 與 POST**

在 `podcast_toolkit/web/api.py` 找 `def get_config()`（約 378 行），加 episode_roots 回傳：
```python
    @app.get("/api/config")
    def get_config():
        cfg = _load_config()
        provider = (cfg.get("transcribe") or {}).get("provider") or "xai"
        if provider not in transcribe.PROVIDERS:
            provider = "xai"
        return JSONResponse({
            "has_xai_api_key": bool(cfg.get("xai_api_key")),
            "has_gemini_api_key": bool(cfg.get("gemini_api_key")),
            "provider": provider,
            "episode_roots": cfg.get("episode_roots") or [str(Path.home() / "Downloads")],
        })
```

找 `def post_config(payload)`（約 389 行），在現有 if 區塊後加：
```python
        if "episode_roots" in payload:
            roots = payload.get("episode_roots")
            if not isinstance(roots, list) or not all(isinstance(x, str) for x in roots):
                raise HTTPException(status_code=400, detail="episode_roots 必須是字串陣列")
            cfg["episode_roots"] = [r.strip() for r in roots if r.strip()]
```

並把 return 改成回傳 episode_roots（在最末 return JSONResponse 內加一行）：
```python
            "episode_roots": cfg.get("episode_roots") or [str(Path.home() / "Downloads")],
```

- [ ] **Step 9.4: Run，確認 pass**

Run: `pytest tests/test_api_dashboard.py -v`
Expected: 10 passed

- [ ] **Step 9.5: Commit**

```bash
git add podcast_toolkit/web/api.py tests/test_api_dashboard.py
git commit -m "feat(api): /api/config 加 episode_roots 欄位"
```

---

## Task 10: dashboard.html + dashboard.css

**Files:**
- Modify: `podcast_toolkit/web/static/dashboard.html`（取代 placeholder）
- Create: `podcast_toolkit/web/static/dashboard.css`

- [ ] **Step 10.1: 寫完整 dashboard.html**

取代 `podcast_toolkit/web/static/dashboard.html` 內容：
```html
<!doctype html>
<html lang="zh-Hant">
  <head>
    <meta charset="utf-8" />
    <title>podcast — Dashboard</title>
    <link rel="stylesheet" href="/static/dashboard.css" />
  </head>
  <body>
    <header class="dash-topbar">
      <h1 class="dash-title">Podcast Toolkit</h1>
      <div class="dash-actions">
        <button id="open-folder-btn" type="button">📁 開資料夾</button>
        <button id="new-episode-btn" type="button">📅 新建集</button>
        <button id="settings-btn" type="button">⚙ 設定</button>
      </div>
    </header>

    <main class="dash-body">
      <div id="warnings" class="warnings" hidden></div>
      <div id="loading" class="loading">載入中…</div>
      <div id="empty" class="empty" hidden>
        <p>還沒有任何集數。</p>
        <p>請點上方「📁 開資料夾」選一個 episode、或「📅 新建集」建立新的。</p>
      </div>
      <ul id="episode-list" class="episode-list" hidden></ul>
    </main>

    <dialog id="settings-modal" class="modal">
      <form method="dialog">
        <h2>設定</h2>
        <label>集數根目錄（每行一個路徑，預設 ~/Downloads）：</label>
        <textarea id="roots-input" rows="4" placeholder="~/Downloads&#10;~/Podcasts"></textarea>
        <div class="modal-actions">
          <button type="button" id="settings-save">儲存</button>
          <button value="cancel">關閉</button>
        </div>
      </form>
    </dialog>

    <dialog id="new-episode-modal" class="modal">
      <form method="dialog">
        <h2>新建集</h2>
        <label>日期（YYYYMMDD）<input id="new-date" type="text" maxlength="8" /></label>
        <label>集名<input id="new-name" type="text" /></label>
        <div class="new-ep-error" id="new-ep-error" hidden></div>
        <div class="modal-actions">
          <button type="button" id="new-ep-create">建立</button>
          <button value="cancel">取消</button>
        </div>
      </form>
    </dialog>

    <script src="/static/dashboard.js"></script>
  </body>
</html>
```

- [ ] **Step 10.2: 寫 dashboard.css**

`podcast_toolkit/web/static/dashboard.css`:
```css
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: #f5f5f7;
  color: #1d1d1f;
}
.dash-topbar {
  display: flex; justify-content: space-between; align-items: center;
  padding: 16px 24px;
  background: #fff;
  border-bottom: 1px solid #e5e5ea;
}
.dash-title { margin: 0; font-size: 20px; }
.dash-actions button {
  margin-left: 8px; padding: 8px 14px;
  border: 1px solid #d1d1d6; background: #fff; border-radius: 6px;
  cursor: pointer; font-size: 14px;
}
.dash-actions button:hover { background: #f0f0f5; }
.dash-body { padding: 24px; max-width: 960px; margin: 0 auto; }
.warnings {
  padding: 12px 16px; background: #fff3cd; border: 1px solid #ffe69c;
  border-radius: 6px; margin-bottom: 16px; color: #664d03;
}
.loading, .empty { padding: 48px; text-align: center; color: #6e6e73; }
.episode-list { list-style: none; padding: 0; margin: 0; display: grid; gap: 12px; }
.episode-card {
  display: flex; justify-content: space-between; align-items: center;
  padding: 16px 20px; background: #fff; border: 1px solid #e5e5ea;
  border-radius: 8px; cursor: pointer; transition: transform 0.1s;
}
.episode-card:hover { transform: translateY(-1px); border-color: #007aff; }
.episode-card .ep-name { font-size: 16px; font-weight: 600; margin: 0 0 4px; }
.episode-card .ep-date { font-size: 13px; color: #6e6e73; }
.stage-badge {
  padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 500;
}
.stage-needs_transcribe { background: #e8e8ed; color: #1d1d1f; }
.stage-needs_assemble { background: #fff3cd; color: #664d03; }
.stage-done { background: #d1f4e0; color: #0a5c2e; }
.stage-broken { background: #f8d7da; color: #842029; }
.modal {
  border: none; border-radius: 12px; padding: 24px; min-width: 400px;
}
.modal::backdrop { background: rgba(0,0,0,0.4); }
.modal h2 { margin: 0 0 16px; font-size: 18px; }
.modal label { display: block; margin-bottom: 12px; font-size: 14px; }
.modal input, .modal textarea {
  width: 100%; margin-top: 4px; padding: 8px;
  border: 1px solid #d1d1d6; border-radius: 6px; font-size: 14px;
}
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
.modal-actions button {
  padding: 8px 16px; border: 1px solid #d1d1d6; background: #fff;
  border-radius: 6px; cursor: pointer;
}
.new-ep-error { color: #d70015; font-size: 13px; margin-bottom: 8px; }
```

- [ ] **Step 10.3: Commit**

```bash
git add podcast_toolkit/web/static/dashboard.html podcast_toolkit/web/static/dashboard.css
git commit -m "feat(dashboard): HTML + CSS（episode card / settings modal / new ep modal）"
```

---

## Task 11: dashboard.js

**Files:**
- Create: `podcast_toolkit/web/static/dashboard.js`

- [ ] **Step 11.1: 寫 dashboard.js**

`podcast_toolkit/web/static/dashboard.js`:
```javascript
"use strict";

const STAGE_LABEL = {
  needs_transcribe: "⚪ 未轉字幕",
  needs_assemble: "🟡 未合成",
  done: "🟢 完成",
  broken: "⚠ 損毀",
};

async function loadEpisodes() {
  const loading = document.getElementById("loading");
  const empty = document.getElementById("empty");
  const list = document.getElementById("episode-list");
  const warningsBox = document.getElementById("warnings");

  loading.hidden = false;
  empty.hidden = true;
  list.hidden = true;
  warningsBox.hidden = true;

  try {
    const r = await fetch("/api/episodes");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();

    loading.hidden = true;

    if (data.warnings.length) {
      warningsBox.textContent = data.warnings.join(" / ");
      warningsBox.hidden = false;
    }

    if (data.episodes.length === 0) {
      empty.hidden = false;
      return;
    }

    list.innerHTML = "";
    for (const ep of data.episodes) {
      const li = document.createElement("li");
      li.className = "episode-card";
      li.innerHTML = `
        <div>
          <h3 class="ep-name"></h3>
          <div class="ep-date"></div>
        </div>
        <span class="stage-badge stage-${ep.stage}"></span>
      `;
      li.querySelector(".ep-name").textContent = ep.name;
      li.querySelector(".ep-date").textContent = ep.date || "—";
      li.querySelector(".stage-badge").textContent = STAGE_LABEL[ep.stage] || ep.stage;
      li.addEventListener("click", () => openEpisode(ep.path));
      list.appendChild(li);
    }
    list.hidden = false;
  } catch (err) {
    loading.textContent = "載入失敗：" + err.message;
  }
}

async function openEpisode(path) {
  const r = await fetch("/api/episodes/open", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!r.ok) {
    const detail = await r.json().catch(() => ({ detail: r.statusText }));
    alert("開啟失敗：" + detail.detail);
    loadEpisodes();
    return;
  }
  window.location.href = "/";
}

async function pickFolder() {
  const r = await fetch("/api/episode/pick", { method: "POST" });
  const data = await r.json();
  if (data.cancelled || !data.path) return;

  // 開的可能是 episode（有 yaml）或要 init 的資料夾
  const preview = await fetch("/api/episode/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: data.path }),
  }).then(r => r.json());

  if (preview.has_episode_yaml) {
    await openEpisode(data.path);
    return;
  }

  if (!confirm(`「${preview.folder_name}」還沒初始化。要跑 init 嗎？\n會建立：${preview.subdirs_to_create.join("、")}`)) {
    return;
  }
  const initR = await fetch("/api/episode/init", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: data.path }),
  });
  if (!initR.ok) {
    const d = await initR.json().catch(() => ({}));
    alert("init 失敗：" + (d.detail || initR.statusText));
    return;
  }
  await openEpisode(data.path);
}

function openSettingsModal() {
  const modal = document.getElementById("settings-modal");
  const input = document.getElementById("roots-input");
  fetch("/api/config").then(r => r.json()).then(cfg => {
    input.value = (cfg.episode_roots || []).join("\n");
    modal.showModal();
  });
}

async function saveSettings() {
  const input = document.getElementById("roots-input");
  const roots = input.value.split("\n").map(s => s.trim()).filter(Boolean);
  const r = await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ episode_roots: roots }),
  });
  if (!r.ok) {
    alert("儲存失敗");
    return;
  }
  document.getElementById("settings-modal").close();
  loadEpisodes();
}

function openNewEpisodeModal() {
  const today = new Date();
  const yyyymmdd = today.getFullYear().toString()
    + String(today.getMonth() + 1).padStart(2, "0")
    + String(today.getDate()).padStart(2, "0");
  document.getElementById("new-date").value = yyyymmdd;
  document.getElementById("new-name").value = "";
  document.getElementById("new-ep-error").hidden = true;
  document.getElementById("new-episode-modal").showModal();
}

async function createNewEpisode() {
  const date = document.getElementById("new-date").value.trim();
  const name = document.getElementById("new-name").value.trim();
  const errBox = document.getElementById("new-ep-error");
  errBox.hidden = true;

  const r = await fetch("/api/episode/new", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ date, name }),
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    errBox.textContent = d.detail || r.statusText;
    errBox.hidden = false;
    return;
  }
  // new_episode 已切了 holder["ep"]，直接導向 edit UI
  window.location.href = "/";
}

document.getElementById("open-folder-btn").addEventListener("click", pickFolder);
document.getElementById("new-episode-btn").addEventListener("click", openNewEpisodeModal);
document.getElementById("settings-btn").addEventListener("click", openSettingsModal);
document.getElementById("settings-save").addEventListener("click", saveSettings);
document.getElementById("new-ep-create").addEventListener("click", createNewEpisode);

loadEpisodes();
```

- [ ] **Step 11.2: 手動 smoke test**

```bash
cd /Users/vincentsia/Desktop/vibe-coding\ playground/podcast-toolkit
python3 -c "from podcast_toolkit.web.api import build_app; import uvicorn; uvicorn.run(build_app(None, lambda: None), port=8765)"
```
另開瀏覽器 `http://127.0.0.1:8765/`：
- 看到「Dashboard」字樣
- ~/Downloads 內的 episode 應該被列出（如 `20260508 沈奕妤`，stage badge 應符合預期）
- 點集 card → 跳到 edit UI
- 按 ⚙ → 看到 roots input
- 改 roots 儲存 → 列表 refresh
- Ctrl-C 結束

- [ ] **Step 11.3: Commit**

```bash
git add podcast_toolkit/web/static/dashboard.js
git commit -m "feat(dashboard): JS 互動（列表 / 切集 / 開資料夾 / 新建集 / 設定）"
```

---

## Task 12: edit UI 加「← 回 Dashboard」按鈕

**Files:**
- Modify: `podcast_toolkit/web/static/index.html`
- Modify: `podcast_toolkit/web/static/app.js`

- [ ] **Step 12.1: 加按鈕到 topbar**

在 `podcast_toolkit/web/static/index.html` 找到 `<div class="ep-switch">`，在 `<button id="ep-switch-btn"...>` 前插入：
```html
        <button id="back-to-dash-btn" type="button" title="回集數列表">← 列表</button>
```

- [ ] **Step 12.2: 加 handler 到 app.js**

在 `podcast_toolkit/web/static/app.js` 找到綁定其他 ep-switch 按鈕的地方（grep `ep-switch-btn` 或 `ep-new-btn`），加：
```javascript
document.getElementById("back-to-dash-btn")?.addEventListener("click", async () => {
  const r = await fetch("/api/episodes/close", { method: "POST" });
  if (!r.ok) {
    alert("回 dashboard 失敗");
    return;
  }
  window.location.href = "/";
});
```

- [ ] **Step 12.3: 手動 smoke**

啟動方式：用 `podcast edit "~/Downloads/20260508 沈奕妤"`（CLI 仍可運作），進 edit UI 後點「← 列表」應該跳回 dashboard。

- [ ] **Step 12.4: Commit**

```bash
git add podcast_toolkit/web/static/index.html podcast_toolkit/web/static/app.js
git commit -m "feat(edit-ui): 加「← 列表」按鈕回 dashboard"
```

---

## Task 13: edit.py 重構 — 拆 run_with_ep / run_dashboard + 用 global lockfile

**Files:**
- Modify: `podcast_toolkit/edit.py`
- Modify: `podcast_toolkit/cli.py`（連動）

- [ ] **Step 13.1: 改寫 edit.py**

整個取代 `podcast_toolkit/edit.py`（保留檔頭 docstring）：
```python
"""podcast edit / podcast ui：啟動本機 FastAPI + 開瀏覽器。"""
from __future__ import annotations
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from podcast_toolkit import server_lock
from podcast_toolkit.episode import Episode
from podcast_toolkit.web.api import build_app


LOCK_PATH = Path.home() / ".podcast-toolkit" / ".server.lock"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(ep: Episode | None) -> int:
    """共用 server 啟動邏輯。回傳 exit code。"""
    port = _find_free_port()
    if not server_lock.acquire(LOCK_PATH, port):
        existing = server_lock.read(LOCK_PATH)
        if existing:
            existing_port = existing[1]
            url = f"http://127.0.0.1:{existing_port}"
            print(f"→ 已有 podcast server 在跑，開啟既有 instance：{url}")
            webbrowser.open(url)
            return 0
        print(f"✗ lockfile 異常：{LOCK_PATH}", file=sys.stderr)
        return 1

    server = {"instance": None}

    def shutdown_callback():
        if server["instance"] is not None:
            server["instance"].should_exit = True

    app = build_app(ep, shutdown=shutdown_callback)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server["instance"] = uvicorn.Server(config)

    url = f"http://127.0.0.1:{port}"
    print(f"→ 啟動：{url}")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server["instance"].run()
    finally:
        server_lock.release(LOCK_PATH)
    print("✅ server 已停止")
    return 0


def run_with_ep(episode_dir: Path) -> int:
    """podcast edit <path> — 直接帶集數進 edit 模式。"""
    ep = Episode(episode_dir)
    main_video = ep.main_video()
    if not main_video.exists():
        print(f"✗ main_video 缺失：{main_video}", file=sys.stderr)
        return 3
    v2 = ep.output_v2_srt()
    if not v2.exists():
        print(f"✗ 找不到 _v2.srt：{v2}", file=sys.stderr)
        print(f"  請先跑 podcast resegment {episode_dir}", file=sys.stderr)
        return 3
    return _start_server(ep)


def run_dashboard() -> int:
    """podcast ui — 進 dashboard 模式（無預選集）。"""
    return _start_server(None)


# 兼容舊 entry：cli.py 的 cmd_edit 還在呼叫 edit.run(path)
def run(episode_dir: Path) -> int:
    return run_with_ep(episode_dir)
```

- [ ] **Step 13.2: 跑既有 test 確認沒砍到東西**

Run: `pytest tests/ -v -k "not slow"`
Expected: 全 pass（注意：可能會有部分 test 用到舊 `LOCK_NAME`，需檢視）

如有失敗：搜尋 `LOCK_NAME` 與 `.edit.lock`，更新 test。

- [ ] **Step 13.3: 手動 smoke**

```bash
podcast edit "~/Downloads/20260508 沈奕妤"   # 應該正常進 edit UI
# Ctrl-C 結束後檢查
ls ~/.podcast-toolkit/.server.lock    # 應該不存在
```

- [ ] **Step 13.4: Commit**

```bash
git add podcast_toolkit/edit.py
git commit -m "refactor(edit): 拆 run_with_ep / run_dashboard + global lockfile"
```

---

## Task 14: cli.py — 加 `podcast ui`

**Files:**
- Modify: `podcast_toolkit/cli.py`

- [ ] **Step 14.1: 加 subcommand**

在 `podcast_toolkit/cli.py` 加：
```python
def cmd_ui(args):
    from podcast_toolkit import edit
    return edit.run_dashboard()
```

並在 `build_parser()` 內現有 `pe = sub.add_parser("edit", ...)` 區塊之後加：
```python
    pu = sub.add_parser("ui", help="開啟瀏覽器 dashboard（無預選集）")
    pu.set_defaults(func=cmd_ui)
```

- [ ] **Step 14.2: 手動 smoke**

```bash
podcast ui   # 應該開瀏覽器到 dashboard
```

- [ ] **Step 14.3: Commit**

```bash
git add podcast_toolkit/cli.py
git commit -m "feat(cli): podcast ui — 直接開 dashboard"
```

---

## Task 15: launcher.py — .app 入口

**Files:**
- Create: `podcast_toolkit/launcher.py`

- [ ] **Step 15.1: 寫 launcher.py**

`podcast_toolkit/launcher.py`:
```python
"""macOS .app 入口：起 podcast server + 開瀏覽器。

py2app entry script 直接呼叫 main()。
與 podcast ui CLI 等價（共用 edit.run_dashboard）。
"""
from __future__ import annotations
import sys
import traceback
from pathlib import Path

LOG_PATH = Path.home() / ".podcast-toolkit" / "launcher.log"


def _alert(title: str, message: str) -> None:
    """跳原生 macOS 對話框。"""
    import subprocess
    script = f'display alert "{title}" message "{message}"'
    try:
        subprocess.run(["osascript", "-e", script], timeout=10)
    except Exception:
        pass


def _log_exception(exc: BaseException) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write("=" * 40 + "\n")
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)


def main() -> int:
    try:
        from podcast_toolkit import edit
        return edit.run_dashboard()
    except SystemExit:
        raise
    except BaseException as exc:
        _log_exception(exc)
        _alert(
            "Podcast Toolkit 啟動失敗",
            f"錯誤：{exc}\\n\\n詳細 log：{LOG_PATH}",
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 15.2: 手動 smoke（不打包）**

```bash
python3 -m podcast_toolkit.launcher
# 應該與 podcast ui 行為一致
```

- [ ] **Step 15.3: Commit**

```bash
git add podcast_toolkit/launcher.py
git commit -m "feat: launcher.py — .app entry，失敗時 osascript alert + log"
```

---

## Task 16: setup_app.py — py2app 打包

**Files:**
- Create: `setup_app.py`

- [ ] **Step 16.1: 確認 py2app 已裝**

Run: `pip3 install --user py2app`

- [ ] **Step 16.2: 寫 setup_app.py**

`setup_app.py`（放專案根目錄）:
```python
"""py2app 設定：把 podcast_toolkit 包成 macOS .app。

使用：
    python3 setup_app.py py2app -A      # alias mode（開發用，快）
    python3 setup_app.py py2app          # 正式打包（會把 Python runtime 也包進去）

產物：dist/Podcast.app
"""
from setuptools import setup

APP = ["podcast_toolkit/launcher.py"]
DATA_FILES = [
    ("podcast_toolkit/web/static", [
        "podcast_toolkit/web/static/index.html",
        "podcast_toolkit/web/static/app.css",
        "podcast_toolkit/web/static/app.js",
        "podcast_toolkit/web/static/dashboard.html",
        "podcast_toolkit/web/static/dashboard.css",
        "podcast_toolkit/web/static/dashboard.js",
    ]),
    ("podcast_toolkit/assets", []),  # 留位給未來 intro/outro 包進來
]
OPTIONS = {
    "argv_emulation": False,
    "packages": ["podcast_toolkit", "fastapi", "uvicorn", "pydantic", "starlette"],
    "includes": ["yaml"],
    "plist": {
        "CFBundleName": "Podcast",
        "CFBundleDisplayName": "Podcast Toolkit",
        "CFBundleIdentifier": "com.liweisia.podcast-toolkit",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "LSUIElement": False,  # 顯示在 Dock
        "NSHighResolutionCapable": True,
    },
}

setup(
    name="Podcast",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
```

- [ ] **Step 16.3: 用 alias mode 跑一次（最快）**

```bash
cd /Users/vincentsia/Desktop/vibe-coding\ playground/podcast-toolkit
python3 setup_app.py py2app -A
```
- 預期：`dist/Podcast.app` 產出
- 雙擊 `dist/Podcast.app` 應該開瀏覽器到 dashboard
- 若失敗：看 `~/.podcast-toolkit/launcher.log`

- [ ] **Step 16.4: 加 .gitignore 排除 build / dist**

確認 `.gitignore` 已含（若無則加）：
```
build/
dist/
*.egg-info/
```

- [ ] **Step 16.5: Commit**

```bash
git add setup_app.py .gitignore
git commit -m "feat: setup_app.py — py2app 打包 Podcast.app"
```

---

## Task 17: 收尾驗收 + README 更新

**Files:**
- Modify: `README.md`

- [ ] **Step 17.1: 完整端到端驗收**

依序執行：
1. `python3 setup_app.py py2app -A` → 產 `dist/Podcast.app`
2. 雙擊 `dist/Podcast.app` → 瀏覽器開 dashboard
3. 看到 `~/Downloads/` 內已 init 過的集（如 `20260508 沈奕妤`）
4. 點集 card → 進 edit UI（標題顯示集名）
5. 在 edit UI 按「← 列表」→ 回 dashboard
6. 按「📁 開資料夾」選一個未 init 的資料夾 → 詢問 init → 接受 → 進 edit UI
7. 按「📅 新建集」→ 填日期+名 → 進 edit UI
8. 按「⚙ 設定」→ 加一個自訂 root → 儲存 → 列表 refresh
9. Cmd+Q `Podcast.app` → 檢查 `~/.podcast-toolkit/.server.lock` 已清除
10. 再次雙擊 `Podcast.app` → 正常開
11. 雙擊 `Podcast.app` 兩次 → 第二次應該不起新 server、開瀏覽器到既有 port

- [ ] **Step 17.2: 更新 README.md**

在 `README.md` 的 `## 工作流` 區塊之前插入：
```markdown
## GUI 模式（推薦）

```bash
python3 setup_app.py py2app -A
open dist/Podcast.app
```

雙擊 `Podcast.app` 後在瀏覽器 dashboard 選集、新建集、設定集數根目錄。CLI 仍保留，供腳本化使用。
```

- [ ] **Step 17.3: Commit**

```bash
git add README.md
git commit -m "docs: README 加 GUI 模式說明"
```

---

## 自我審查

**Spec 覆蓋檢查：**
- 1. 架構總覽 → Tasks 5/6/8（build_app(None) + GET / routing + open/close）✅
- 2. 元件拆解 → 全部 16 個 task 都有對應 ✅
- 3. 資料流 序列 A → Tasks 6/7/15（launcher + GET / + GET /api/episodes）✅
- 3. 序列 B → Task 8（open）✅
- 3. 序列 C → Tasks 8/12（close + 回 dashboard button）✅
- 3. 序列 D → Tasks 13/15（lockfile cleanup + launcher main）✅
- 4. 錯誤處理 已有 lock / dead pid → Task 1 ✅
- 4. dashboard warnings → Task 4 list_episodes 回 warnings ✅
- 4. _require_ep 409 → Task 5 ✅
- 4. 啟動失敗 alert + log → Task 15 launcher ✅
- 5. 測試清單 unit / integration / 手動 → Tasks 1/2/3/4 unit, Tasks 6/7/8/9 integration, Task 17 手動 ✅

**Placeholder scan：** 無 TBD / TODO / 「實作後續細節」。所有 code block 都是完整代碼。

**Type consistency：**
- `episode_stage()` 五個 string：broken / empty / needs_transcribe / needs_assemble / done → 在 Task 2 定義、Task 10 CSS 用、Task 11 JS 用 ✅
- `list_episodes()` 回 `{episodes, warnings}` → Task 4 定義、Task 7 endpoint 透傳、Task 11 JS 讀同名 ✅
- `_require_ep()` → Task 5 定義並全檔使用 ✅
- `LOCK_PATH = ~/.podcast-toolkit/.server.lock` → Task 13 定義，Tasks 15/17 引用 ✅
- `episode_roots` → Task 9 定義、Task 7 讀取、Task 11 UI 寫 ✅

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-04-fully-web-ui.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
