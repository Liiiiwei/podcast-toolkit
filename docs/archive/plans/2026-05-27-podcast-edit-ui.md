# Podcast Edit UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `podcast-toolkit` 加上 `podcast edit` 子指令，CLI 起本機 FastAPI，瀏覽器內單頁面整合：裁切畫框、字幕卡刪除、SRT 錯字 inline 編輯，存檔後寫入 `episode.yaml` 與 `_v2.srt`，再讓 `podcast assemble` 套用裁切與刪除。

**Architecture:** `podcast edit <path>` 啟動 FastAPI（`127.0.0.1` 隨機 port）→ `webbrowser.open` 開瀏覽器 → 前端純 vanilla HTML/JS 一次性載入完整 episode 資料 → 按「完成並儲存」一次 POST 寫檔後 server 自殺。`episode.yaml` 擴充 `crop` / `deletions` 兩欄位；`assemble.py` 增加 ffmpeg `crop` filter 與基於 `select` 的段落剔除邏輯。

**Tech Stack:** Python 3.9+、FastAPI、uvicorn、PyYAML（既有）、ffmpeg（既有）、pytest（**新增**）；vanilla HTML/JS/CSS（無 build step）。

**Spec：** [`docs/superpowers/specs/2026-05-27-podcast-edit-ui-design.md`](../../superpowers/specs/2026-05-27-podcast-edit-ui-design.md)

---

## File Structure

### 新增

```
podcast_toolkit/
├── edit.py                      # CLI 入口：lockfile / uvicorn / webbrowser / shutdown
├── srt_io.py                    # 共用 srt parse / serialize
└── web/
    ├── __init__.py
    ├── api.py                   # FastAPI app + 5 條路由
    ├── episode_io.py            # load_episode_state / save_episode_state
    └── static/
        ├── index.html
        ├── app.js
        └── app.css
tests/
├── __init__.py
├── conftest.py                  # tmp_episode_dir fixture
├── test_srt_io.py
├── test_episode_io.py
├── test_api_video.py
└── test_assemble_filters.py
```

### 修改

- `podcast_toolkit/cli.py` — 新增 `edit` subparser
- `podcast_toolkit/config.py` — `merge()` 學會 `crop` / `deletions`
- `podcast_toolkit/assemble.py` — filter_complex 加 crop + select
- `install.sh` — 增加 `fastapi`、`uvicorn[standard]`、`pytest` 安裝
- `tests/regression.sh` — 補一個帶 crop/deletions 的 fixture
- `README.md` — 工作流加 5.5 步與手動驗收清單
- `defaults.yaml`（如有需要才動）

---

## Task 1: 安裝相依套件與 pytest 骨架

**Files:**
- Modify: `install.sh`（在 pyyaml 段後加 fastapi/uvicorn/pytest 安裝）
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: 改 `install.sh`，在 pyyaml 區塊之後新增 fastapi/uvicorn/pytest**

在 `echo "→ 安裝 Python 套件 pyyaml"` 那一整段（含 fallback）之後插入：

```bash
echo "→ 安裝 Python 套件 fastapi / uvicorn / pytest"
PY_PKGS="fastapi uvicorn[standard] pytest"
if ! pip3 install --user $PY_PKGS >/dev/null 2>&1; then
    echo "  ⚠ pip3 install 一般模式失敗，改用 --break-system-packages 重試"
    pip3 install --user --break-system-packages $PY_PKGS
fi
echo "  ✓ fastapi / uvicorn / pytest"
```

- [ ] **Step 2: 本機跑 install.sh 驗證套件可裝**

Run: `bash install.sh`
Expected: 含 `✓ fastapi / uvicorn / pytest` 並結束於 `✅ 安裝完成`。

- [ ] **Step 3: 建立空 `tests/__init__.py`**

```python
```

- [ ] **Step 4: 建立 `tests/conftest.py` 提供 fixture**

```python
"""pytest fixtures：建臨時 episode 資料夾。"""
import shutil
from pathlib import Path

import pytest
import yaml


SAMPLE_SRT = """\
1
00:00:00,000 --> 00:00:04,200
大家好歡迎來到我愛上班

2
00:00:04,200 --> 00:00:12,000
今天要聊的是過嗨乳牛這個議題

3
00:00:12,000 --> 00:00:14,000
呃那個

4
00:00:14,000 --> 00:00:22,000
我們從牠的飼料配方開始講起
"""


@pytest.fixture
def tmp_episode_dir(tmp_path: Path) -> Path:
    """建一個最小 episode 資料夾結構，回傳路徑。"""
    ep = tmp_path / "20260601 測試集"
    ep.mkdir()
    for sub in ("01_母帶", "02_片頭片尾", "03_成品", "04_工作檔"):
        (ep / sub).mkdir()

    (ep / "episode.yaml").write_text(
        yaml.safe_dump(
            {
                "date": 20260601,
                "name": "測試集",
                "main_video": "01_母帶/{name}.mp4",
                "main_srt": "01_母帶/{name}.srt",
                "fixes": [],
                "card_fixes": [],
                "force_break": [],
                "force_join": [],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    # 放一個 v2 srt 在 03_成品/
    (ep / "03_成品" / "測試集_final_v2.srt").write_text(SAMPLE_SRT, encoding="utf-8")

    return ep
```

- [ ] **Step 5: 驗證 pytest 跑得起來**

Run: `cd ~/Projects/podcast-toolkit && pytest tests/ -q`
Expected: `no tests ran` 或 0 collected（無錯誤），代表 collect 階段沒問題。

- [ ] **Step 6: Commit**

```bash
git add install.sh tests/__init__.py tests/conftest.py
git commit -m "chore(deps): add fastapi/uvicorn/pytest + pytest scaffold"
```

---

## Task 2: `config.merge()` 學會 `crop` / `deletions`

**Files:**
- Modify: `podcast_toolkit/config.py:33-56`
- Test: `tests/test_config_merge.py` (Create)

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_config_merge.py`:

```python
"""驗 config.merge 對新欄位 crop / deletions 的行為。"""
from podcast_toolkit import config


DEFAULTS = {
    "resegment": {"min_chars": 8},
    "subtitle_style": {"font_size": 28},
    "assets": {"intro": "x"},
    "encode": {"crf": 23},
    "common_fixes": [],
}


def test_merge_crop_missing_returns_none():
    cfg = config.merge(DEFAULTS, {"name": "t"})
    assert cfg["crop"] is None


def test_merge_crop_present_is_preserved():
    cfg = config.merge(
        DEFAULTS,
        {"name": "t", "crop": {"x": 0.1, "y": 0.0, "width": 0.8, "height": 1.0}},
    )
    assert cfg["crop"] == {"x": 0.1, "y": 0.0, "width": 0.8, "height": 1.0}


def test_merge_deletions_missing_returns_empty_list():
    cfg = config.merge(DEFAULTS, {"name": "t"})
    assert cfg["deletions"] == []


def test_merge_deletions_present_preserved_as_list():
    cfg = config.merge(DEFAULTS, {"name": "t", "deletions": [2, 4, 7]})
    assert cfg["deletions"] == [2, 4, 7]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_config_merge.py -v`
Expected: 四個 test 全 FAIL（KeyError on `crop` / `deletions`）。

- [ ] **Step 3: 改 `podcast_toolkit/config.py` 的 `merge()`**

在 `cfg = { ... }` 字典裡面、`"force_join"` 那一行之後加：

```python
        "crop": episode.get("crop"),
        "deletions": list(episode.get("deletions") or []),
```

- [ ] **Step 4: 跑測試確認通過**

Run: `pytest tests/test_config_merge.py -v`
Expected: 四個 test 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add podcast_toolkit/config.py tests/test_config_merge.py
git commit -m "feat(config): support crop / deletions in episode.yaml"
```

---

## Task 3: SRT parse / serialize 工具模組

**Files:**
- Create: `podcast_toolkit/srt_io.py`
- Test: `tests/test_srt_io.py` (Create)

> **背景：** `resegment.py` 已有 `ts2s` / `s2ts`，但沒有完整 srt 解析。我們抽一個共用模組同時給 `web/episode_io.py` 與 `resegment.py` 用（resegment 之後若要 refactor 可改用，這次先不動 resegment）。

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_srt_io.py`:

```python
"""srt_io：解析 srt 為 cards、序列化 cards 為 srt。"""
import pytest
from podcast_toolkit import srt_io


SAMPLE = """\
1
00:00:00,000 --> 00:00:04,200
大家好

2
00:00:04,200 --> 00:00:12,000
今天聊乳牛
"""


def test_parse_returns_cards_in_order():
    cards = srt_io.parse(SAMPLE)
    assert len(cards) == 2
    assert cards[0] == {"idx": 1, "start": 0.0, "end": 4.2, "text": "大家好"}
    assert cards[1]["idx"] == 2
    assert cards[1]["text"] == "今天聊乳牛"


def test_parse_handles_multiline_text():
    text = "1\n00:00:00,000 --> 00:00:01,000\n第一行\n第二行\n"
    cards = srt_io.parse(text)
    assert cards[0]["text"] == "第一行\n第二行"


def test_serialize_roundtrips():
    cards = srt_io.parse(SAMPLE)
    assert srt_io.serialize(cards).strip() == SAMPLE.strip()


def test_serialize_applies_text_overrides():
    cards = srt_io.parse(SAMPLE)
    out = srt_io.serialize(cards, overrides={1: "大家午安"})
    assert "大家午安" in out
    assert "大家好" not in out
    # 第 2 段未動
    assert "今天聊乳牛" in out


def test_parse_skips_blank_blocks():
    text = "\n\n1\n00:00:00,000 --> 00:00:01,000\nA\n\n\n"
    assert len(srt_io.parse(text)) == 1
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_srt_io.py -v`
Expected: 五個 test 全 FAIL（`ModuleNotFoundError: srt_io`）。

- [ ] **Step 3: 實作 `podcast_toolkit/srt_io.py`**

```python
"""SRT 解析與序列化。共用給 web/episode_io.py。"""
from __future__ import annotations
from typing import Iterable


def _ts2s(ts: str) -> float:
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _s2ts(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        s += 1
        ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse(text: str) -> list[dict]:
    """解析 srt 字串 → list of {idx, start, end, text}。idx 為 srt 原本的 1-based 序號。"""
    cards: list[dict] = []
    blocks = [b for b in text.replace("\r\n", "\n").strip().split("\n\n") if b.strip()]
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0].strip())
        except ValueError:
            continue
        start_str, _, end_str = lines[1].partition(" --> ")
        cards.append(
            {
                "idx": idx,
                "start": _ts2s(start_str.strip()),
                "end": _ts2s(end_str.strip()),
                "text": "\n".join(lines[2:]),
            }
        )
    return cards


def serialize(cards: Iterable[dict], overrides: dict[int, str] | None = None) -> str:
    """把 cards 寫回 srt 字串。overrides[idx] 會覆寫對應 card 的文字。"""
    overrides = overrides or {}
    out: list[str] = []
    for c in cards:
        text = overrides.get(c["idx"], c["text"])
        out.append(
            f"{c['idx']}\n{_s2ts(c['start'])} --> {_s2ts(c['end'])}\n{text}\n"
        )
    return "\n".join(out)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `pytest tests/test_srt_io.py -v`
Expected: 五個 test 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add podcast_toolkit/srt_io.py tests/test_srt_io.py
git commit -m "feat(srt-io): add shared srt parse/serialize module"
```

---

## Task 4: `web/episode_io.py` — 讀取編輯狀態

**Files:**
- Create: `podcast_toolkit/web/__init__.py`（空）
- Create: `podcast_toolkit/web/episode_io.py`
- Test: `tests/test_episode_io.py` (Create)

- [ ] **Step 1: 建立空 `podcast_toolkit/web/__init__.py`**

```python
```

- [ ] **Step 2: 寫失敗測試**

Create `tests/test_episode_io.py`:

```python
"""web/episode_io：把 episode 資料夾轉成前端要的 JSON 狀態。"""
from podcast_toolkit.episode import Episode
from podcast_toolkit.web import episode_io


def test_load_state_returns_name_and_cards(tmp_episode_dir):
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    assert state["name"] == "測試集"
    assert state["crop"] is None
    assert state["deletions"] == []
    assert len(state["cards"]) == 4
    assert state["cards"][0]["idx"] == 1
    assert state["cards"][0]["text"] == "大家好歡迎來到我愛上班"


def test_load_state_includes_crop_and_deletions_from_yaml(tmp_episode_dir):
    # 改寫 yaml 加 crop / deletions
    yaml_path = tmp_episode_dir / "episode.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "crop:\n  x: 0.1\n  y: 0.0\n  width: 0.8\n  height: 1.0\n"
        + "deletions: [3]\n",
        encoding="utf-8",
    )
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    assert state["crop"] == {"x": 0.1, "y": 0.0, "width": 0.8, "height": 1.0}
    assert state["deletions"] == [3]
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `pytest tests/test_episode_io.py -v`
Expected: FAIL (`ModuleNotFoundError`)。

- [ ] **Step 4: 實作 `podcast_toolkit/web/episode_io.py`（先做 load_state）**

```python
"""把 Episode 物件 + _v2.srt 組成前端要的 JSON state，並負責寫回。"""
from __future__ import annotations
from pathlib import Path
from typing import Any

import yaml

from podcast_toolkit import srt_io
from podcast_toolkit.episode import Episode


def load_state(ep: Episode) -> dict[str, Any]:
    """讀 episode.yaml + _v2.srt → 給前端的初始狀態。"""
    v2 = ep.output_v2_srt()
    if not v2.exists():
        raise FileNotFoundError(f"找不到 _v2.srt：{v2}（請先跑 podcast resegment）")
    cards = srt_io.parse(v2.read_text(encoding="utf-8"))
    return {
        "name": ep.name,
        "crop": ep.cfg.get("crop"),
        "deletions": list(ep.cfg.get("deletions") or []),
        "cards": cards,
    }
```

- [ ] **Step 5: 跑測試確認通過**

Run: `pytest tests/test_episode_io.py -v`
Expected: 兩個 test 全 PASS。

- [ ] **Step 6: Commit**

```bash
git add podcast_toolkit/web/__init__.py podcast_toolkit/web/episode_io.py tests/test_episode_io.py
git commit -m "feat(web): add episode_io.load_state for edit UI"
```

---

## Task 5: `web/episode_io.py` — 儲存編輯狀態

**Files:**
- Modify: `podcast_toolkit/web/episode_io.py`
- Test: `tests/test_episode_io.py`（追加 test）

- [ ] **Step 1: 在 `tests/test_episode_io.py` 追加失敗測試**

加在檔尾：

```python
def test_save_state_writes_crop_and_deletions_to_yaml(tmp_episode_dir):
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop": {"x": 0.05, "y": 0.05, "width": 0.9, "height": 0.9},
            "deletions": [2, 4],
            "cards": [],
        },
    )
    new_yaml = yaml.safe_load(
        (tmp_episode_dir / "episode.yaml").read_text(encoding="utf-8")
    )
    assert new_yaml["crop"] == {"x": 0.05, "y": 0.05, "width": 0.9, "height": 0.9}
    assert new_yaml["deletions"] == [2, 4]


def test_save_state_overwrites_v2_srt_with_card_text_overrides(tmp_episode_dir):
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop": None,
            "deletions": [],
            "cards": [{"idx": 1, "text": "大家午安歡迎來到我愛上班"}],
        },
    )
    v2 = (tmp_episode_dir / "03_成品" / "測試集_final_v2.srt").read_text(encoding="utf-8")
    assert "大家午安" in v2
    assert "大家好歡迎" not in v2
    # 其他段未動
    assert "今天要聊的是過嗨乳牛" in v2


def test_save_state_removes_crop_when_none(tmp_episode_dir):
    # 先寫 crop 進去
    yaml_path = tmp_episode_dir / "episode.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "crop:\n  x: 0.1\n  y: 0.0\n  width: 0.8\n  height: 1.0\n",
        encoding="utf-8",
    )
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(ep, payload={"crop": None, "deletions": [], "cards": []})
    new_yaml = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert "crop" not in new_yaml or new_yaml["crop"] is None
```

需要在檔頭把 `import yaml` 加上。

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_episode_io.py -v`
Expected: 新增三個 FAIL，舊兩個仍 PASS。

- [ ] **Step 3: 在 `web/episode_io.py` 實作 `save_state`**

加在檔案下方（在 `load_state` 之後）：

```python
def save_state(ep: Episode, payload: dict[str, Any]) -> None:
    """把前端 payload 寫回：episode.yaml 的 crop / deletions、覆寫 _v2.srt。"""
    yaml_path = ep.dir / "episode.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}

    # crop
    crop = payload.get("crop")
    if crop:
        data["crop"] = {
            "x": float(crop["x"]),
            "y": float(crop["y"]),
            "width": float(crop["width"]),
            "height": float(crop["height"]),
        }
    else:
        data.pop("crop", None)

    # deletions
    deletions = list(payload.get("deletions") or [])
    if deletions:
        data["deletions"] = [int(i) for i in deletions]
    else:
        data.pop("deletions", None)

    yaml_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    # _v2.srt 覆寫
    v2 = ep.output_v2_srt()
    cards = srt_io.parse(v2.read_text(encoding="utf-8"))
    overrides = {
        int(c["idx"]): c["text"]
        for c in (payload.get("cards") or [])
        if c.get("text")
    }
    v2.write_text(srt_io.serialize(cards, overrides=overrides), encoding="utf-8")
```

- [ ] **Step 4: 跑測試確認全部通過**

Run: `pytest tests/test_episode_io.py -v`
Expected: 全部五個 test PASS。

- [ ] **Step 5: Commit**

```bash
git add podcast_toolkit/web/episode_io.py tests/test_episode_io.py
git commit -m "feat(web): add episode_io.save_state to persist crop/deletions/srt"
```

---

## Task 6: 影片 Range streaming endpoint

**Files:**
- Create: `podcast_toolkit/web/video.py`
- Test: `tests/test_api_video.py`

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_api_video.py`:

```python
"""影片 Range streaming：支援 HTML5 <video> 分段抓檔。"""
import io
from pathlib import Path

import pytest

from podcast_toolkit.web import video as video_mod


@pytest.fixture
def fake_video(tmp_path: Path) -> Path:
    p = tmp_path / "v.mp4"
    p.write_bytes(b"X" * 10000)
    return p


def test_range_response_returns_206_with_correct_slice(fake_video):
    resp = video_mod.range_response(fake_video, range_header="bytes=0-99")
    assert resp.status_code == 206
    assert resp.headers["content-range"] == "bytes 0-99/10000"
    assert resp.headers["content-length"] == "100"
    body = b"".join(resp.body_iterator) if hasattr(resp, "body_iterator") else resp.body
    assert len(body) == 100


def test_range_response_open_ended(fake_video):
    resp = video_mod.range_response(fake_video, range_header="bytes=9990-")
    assert resp.status_code == 206
    assert resp.headers["content-range"] == "bytes 9990-9999/10000"
    assert resp.headers["content-length"] == "10"


def test_no_range_returns_full_200(fake_video):
    resp = video_mod.range_response(fake_video, range_header=None)
    assert resp.status_code == 200
    assert resp.headers["content-length"] == "10000"


def test_range_out_of_bounds_returns_416(fake_video):
    resp = video_mod.range_response(fake_video, range_header="bytes=99999-")
    assert resp.status_code == 416
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_api_video.py -v`
Expected: FAIL (`ModuleNotFoundError`)。

- [ ] **Step 3: 實作 `podcast_toolkit/web/video.py`**

```python
"""影片 Range streaming，回傳 starlette Response。"""
from __future__ import annotations
from pathlib import Path
import re

from starlette.responses import FileResponse, Response, StreamingResponse


_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def range_response(path: Path, range_header: str | None):
    size = path.stat().st_size

    if not range_header:
        return FileResponse(path, media_type="video/mp4")

    m = _RANGE_RE.match(range_header.strip())
    if not m:
        return Response(status_code=416)

    start_s, end_s = m.group(1), m.group(2)
    start = int(start_s) if start_s else 0
    end = int(end_s) if end_s else size - 1

    if start >= size or start < 0 or end < start:
        return Response(
            status_code=416,
            headers={"content-range": f"bytes */{size}"},
        )
    end = min(end, size - 1)
    length = end - start + 1

    def stream():
        with path.open("rb") as f:
            f.seek(start)
            remaining = length
            chunk = 64 * 1024
            while remaining > 0:
                data = f.read(min(chunk, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    return StreamingResponse(
        stream(),
        status_code=206,
        media_type="video/mp4",
        headers={
            "content-range": f"bytes {start}-{end}/{size}",
            "content-length": str(length),
            "accept-ranges": "bytes",
        },
    )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `pytest tests/test_api_video.py -v`
Expected: 四個 test 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add podcast_toolkit/web/video.py tests/test_api_video.py
git commit -m "feat(web): add video Range streaming for HTML5 video tag"
```

---

## Task 7: FastAPI app 與 5 條路由

**Files:**
- Create: `podcast_toolkit/web/api.py`
- Test: `tests/test_api_routes.py` (Create)

- [ ] **Step 1: 寫失敗測試（TestClient 整合驗 routes）**

Create `tests/test_api_routes.py`:

```python
"""FastAPI 五條路由的整合測試。"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from podcast_toolkit.episode import Episode
from podcast_toolkit.web.api import build_app


@pytest.fixture
def client(tmp_episode_dir: Path):
    # 放一個假 main_video，讓 /api/video 有東西讀
    main_video = tmp_episode_dir / "01_母帶" / "測試集.mp4"
    main_video.write_bytes(b"FAKE" * 1000)

    ep = Episode(tmp_episode_dir)
    app = build_app(ep, shutdown=lambda: None)
    return TestClient(app)


def test_get_root_serves_index_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "<html" in r.text.lower()


def test_get_episode_returns_state(client):
    r = client.get("/api/episode")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "測試集"
    assert isinstance(body["cards"], list)
    assert body["crop"] is None


def test_get_video_with_range_returns_206(client):
    r = client.get("/api/video", headers={"Range": "bytes=0-10"})
    assert r.status_code == 206
    assert "bytes 0-10/" in r.headers["content-range"]


def test_post_save_writes_files_and_signals_shutdown(client, tmp_episode_dir):
    called = {"n": 0}
    # 重建 client 但讓 shutdown 計次
    from podcast_toolkit.web.api import build_app
    ep = Episode(tmp_episode_dir)
    app = build_app(ep, shutdown=lambda: called.__setitem__("n", called["n"] + 1))
    c = TestClient(app)
    r = c.post(
        "/api/save",
        json={"crop": None, "deletions": [3], "cards": []},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    import yaml
    data = yaml.safe_load((tmp_episode_dir / "episode.yaml").read_text(encoding="utf-8"))
    assert data["deletions"] == [3]
    assert called["n"] == 1


def test_post_shutdown_calls_callback(client, tmp_episode_dir):
    called = {"n": 0}
    from podcast_toolkit.web.api import build_app
    ep = Episode(tmp_episode_dir)
    app = build_app(ep, shutdown=lambda: called.__setitem__("n", called["n"] + 1))
    c = TestClient(app)
    r = c.post("/api/shutdown")
    assert r.status_code == 204
    assert called["n"] == 1
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_api_routes.py -v`
Expected: FAIL (`ImportError: build_app`)。

- [ ] **Step 3: 實作 `podcast_toolkit/web/api.py`**

```python
"""FastAPI app 工廠：給 edit.py 起 server 用。"""
from __future__ import annotations
import threading
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from podcast_toolkit.episode import Episode
from podcast_toolkit.web import episode_io, video


STATIC_DIR = Path(__file__).resolve().parent / "static"


def build_app(ep: Episode, shutdown: Callable[[], None]) -> FastAPI:
    """建立 FastAPI app。shutdown 是儲存後/取消時呼叫的 callback。"""
    app = FastAPI(title="podcast-edit")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/api/episode")
    def get_episode():
        return JSONResponse(episode_io.load_state(ep))

    @app.get("/api/video")
    def get_video(request: Request):
        return video.range_response(ep.main_video(), request.headers.get("range"))

    @app.post("/api/save")
    def save(payload: dict):
        episode_io.save_state(ep, payload)
        # 延遲呼叫 shutdown,讓 response 先送出
        threading.Timer(0.3, shutdown).start()
        return {"ok": True}

    @app.post("/api/shutdown")
    def cancel():
        threading.Timer(0.3, shutdown).start()
        return Response(status_code=204)

    return app
```

- [ ] **Step 4: 跑測試確認通過**

Run: `pytest tests/test_api_routes.py -v`
Expected: 五個 test 全 PASS（注意 `test_get_root_serves_index_html` 會失敗到 Task 8 建好 index.html 為止，先讓它 xfail 或暫時新建空殼）。

如果 `index.html` 還沒建，先建一個 placeholder 讓這個 test 跑：

```bash
mkdir -p podcast_toolkit/web/static
echo "<html><body>placeholder</body></html>" > podcast_toolkit/web/static/index.html
```

- [ ] **Step 5: Commit**

```bash
git add podcast_toolkit/web/api.py podcast_toolkit/web/static/index.html tests/test_api_routes.py
git commit -m "feat(web): wire FastAPI routes for edit UI"
```

---

## Task 8: 前端骨架（index.html + app.css）

**Files:**
- Modify: `podcast_toolkit/web/static/index.html`（取代上一 task 的 placeholder）
- Create: `podcast_toolkit/web/static/app.css`

> **No TDD — 前端無測試框架，所有前端任務都是「實作 + 手動驗收」。**

- [ ] **Step 1: 寫 `index.html`**

完整內容：

```html
<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <title>podcast edit</title>
  <link rel="stylesheet" href="/static/app.css">
</head>
<body>
  <header class="topbar">
    <div>
      <div class="title" id="title">載入中…</div>
      <div class="status" id="status"></div>
    </div>
    <div class="actions">
      <button id="cancel-btn">取消</button>
      <button id="save-btn" class="primary" disabled>完成並儲存</button>
    </div>
  </header>

  <main class="body">
    <section class="video-pane">
      <div class="video-wrap">
        <video id="video" src="/api/video" preload="metadata"></video>
        <div class="crop-frame" id="crop-frame">
          <div class="handle tl" data-edge="tl"></div>
          <div class="handle tr" data-edge="tr"></div>
          <div class="handle bl" data-edge="bl"></div>
          <div class="handle br" data-edge="br"></div>
        </div>
      </div>
      <div class="controls">
        <button id="play-btn">▶</button>
        <span id="time">00:00 / 00:00</span>
        <input type="range" id="seek" min="0" max="100" value="0">
      </div>
      <div class="crop-info">
        <span id="crop-text">裁切框：未設定</span>
        <button id="crop-reset">↺ 重設為整張</button>
      </div>
    </section>

    <section class="cards-pane">
      <div id="cards-list"></div>
    </section>
  </main>

  <script src="/static/app.js" type="module"></script>
</body>
</html>
```

- [ ] **Step 2: 寫 `app.css`**

```css
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: #1a1a1a;
  color: #ddd;
  font-family: -apple-system, "Helvetica Neue", "Noto Sans TC", sans-serif;
  font-size: 14px;
  height: 100vh;
  display: flex;
  flex-direction: column;
}
.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 16px;
  background: #222;
  border-bottom: 1px solid #333;
}
.title { font-weight: 600; }
.status { font-size: 11px; color: #888; margin-top: 2px; }
.actions button {
  margin-left: 8px;
  padding: 6px 14px;
  border-radius: 4px;
  border: 1px solid #555;
  background: #2a2a2a;
  color: #ddd;
  cursor: pointer;
}
.actions .primary {
  background: #ff6b35;
  border-color: #ff6b35;
  color: white;
  font-weight: 600;
}
.actions button:disabled { opacity: 0.4; cursor: not-allowed; }

.body {
  display: grid;
  grid-template-columns: 1fr 400px;
  gap: 12px;
  flex: 1;
  padding: 12px;
  overflow: hidden;
}
.video-pane { display: flex; flex-direction: column; }
.video-wrap {
  position: relative;
  background: #000;
  aspect-ratio: 16 / 9;
  width: 100%;
}
.video-wrap video { width: 100%; height: 100%; display: block; }
.crop-frame {
  position: absolute;
  border: 2px solid #ff6b35;
  box-shadow: 0 0 0 9999px rgba(0,0,0,0.45);
  cursor: move;
  /* top/left/width/height 由 JS 設定 (%) */
}
.crop-frame.hidden { display: none; }
.handle {
  position: absolute;
  width: 12px; height: 12px;
  background: white;
  border: 1px solid #ff6b35;
}
.handle.tl { top: -6px; left: -6px; cursor: nwse-resize; }
.handle.tr { top: -6px; right: -6px; cursor: nesw-resize; }
.handle.bl { bottom: -6px; left: -6px; cursor: nesw-resize; }
.handle.br { bottom: -6px; right: -6px; cursor: nwse-resize; }

.controls {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 0;
}
.controls button {
  background: transparent;
  color: #ddd;
  border: none;
  cursor: pointer;
  font-size: 16px;
}
.controls input[type=range] { flex: 1; }
.crop-info {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: #888;
  padding-top: 4px;
}
.crop-info button {
  background: transparent;
  color: #ff6b35;
  border: none;
  cursor: pointer;
}

.cards-pane { overflow-y: auto; background: #0a0a0a; border-radius: 4px; padding: 8px; }
.card {
  display: grid;
  grid-template-columns: 60px 1fr 24px;
  gap: 8px;
  align-items: start;
  background: #1f1f1f;
  border: 1px solid #2a2a2a;
  border-radius: 4px;
  padding: 8px 10px;
  margin-bottom: 6px;
  font-size: 13px;
}
.card.playing { border-color: #ff6b35; background: #2a1f1a; }
.card.deleted { opacity: 0.35; }
.card.deleted .card-text { text-decoration: line-through; color: #666; }
.card-time { color: #888; font-family: monospace; font-size: 11px; line-height: 1.4; cursor: pointer; }
.card-text { color: #ddd; line-height: 1.5; outline: none; }
.card-text.dirty { border-bottom: 1px solid #ff6b35; }
.card-del { background: transparent; color: #666; border: none; cursor: pointer; font-size: 14px; }
.card-del:hover { color: #ff6b35; }
```

- [ ] **Step 3: 手動驗收**

Run（暫時放幾個假資料）：
```bash
# 先靠 python 起一個 server 開瀏覽器看，下一個 task 才真整合
python3 -m http.server --directory podcast_toolkit/web/static 8765
```
Expected：開 `http://localhost:8765`，看到 topbar、左邊黑色 16:9 區、右邊空 cards-pane（沒資料正常）。

- [ ] **Step 4: Commit**

```bash
git add podcast_toolkit/web/static/index.html podcast_toolkit/web/static/app.css
git commit -m "feat(web): add edit UI HTML shell + CSS"
```

---

## Task 9: 前端載入資料、渲染影片與字幕卡

**Files:**
- Create: `podcast_toolkit/web/static/app.js`

- [ ] **Step 1: 寫 `app.js`（載入 + 渲染部分）**

完整內容：

```javascript
// 編輯狀態：全部存在這裡，存檔時一次 POST。
const state = {
  name: "",
  crop: null,
  deletions: new Set(),
  cards: [],
  textOverrides: new Map(), // idx -> text
};

const $ = (sel) => document.querySelector(sel);

function fmtTime(sec) {
  if (!isFinite(sec)) return "00:00";
  const s = Math.floor(sec % 60).toString().padStart(2, "0");
  const m = Math.floor((sec / 60) % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

function renderTopbar() {
  $("#title").textContent = state.name;
  const total = state.cards.length;
  const deleted = state.deletions.size;
  const dirty = state.textOverrides.size;
  $("#status").textContent =
    `字幕卡 ${total} 段 · 已刪 ${deleted} · 已修 ${dirty}`;
  const allDeleted = total > 0 && deleted === total;
  $("#save-btn").disabled = allDeleted;
}

function renderCropInfo() {
  const c = state.crop;
  if (!c) {
    $("#crop-text").textContent = "裁切框：未設定（整張畫面）";
    $("#crop-frame").classList.add("hidden");
    return;
  }
  $("#crop-text").textContent =
    `裁切框：x=${(c.x * 100).toFixed(0)}% y=${(c.y * 100).toFixed(0)}% ` +
    `w=${(c.width * 100).toFixed(0)}% h=${(c.height * 100).toFixed(0)}%`;
  const frame = $("#crop-frame");
  frame.classList.remove("hidden");
  frame.style.left = `${c.x * 100}%`;
  frame.style.top = `${c.y * 100}%`;
  frame.style.width = `${c.width * 100}%`;
  frame.style.height = `${c.height * 100}%`;
}

function renderCards() {
  const list = $("#cards-list");
  list.innerHTML = "";
  for (const c of state.cards) {
    const div = document.createElement("div");
    div.className = "card";
    div.dataset.idx = c.idx;
    if (state.deletions.has(c.idx)) div.classList.add("deleted");

    const time = document.createElement("div");
    time.className = "card-time";
    time.textContent = `${fmtTime(c.start)}\n${fmtTime(c.end)}`;
    time.style.whiteSpace = "pre";
    time.addEventListener("click", () => {
      $("#video").currentTime = c.start;
    });

    const text = document.createElement("div");
    text.className = "card-text";
    text.contentEditable = "true";
    text.textContent = state.textOverrides.get(c.idx) ?? c.text;
    if (state.textOverrides.has(c.idx)) text.classList.add("dirty");
    text.addEventListener("blur", () => {
      const v = text.textContent.trim();
      const original = c.text;
      if (v && v !== original) {
        state.textOverrides.set(c.idx, v);
        text.classList.add("dirty");
      } else {
        state.textOverrides.delete(c.idx);
        text.classList.remove("dirty");
      }
      renderTopbar();
    });

    const del = document.createElement("button");
    del.className = "card-del";
    del.textContent = state.deletions.has(c.idx) ? "↺" : "✕";
    del.addEventListener("click", () => {
      if (state.deletions.has(c.idx)) {
        state.deletions.delete(c.idx);
      } else {
        state.deletions.add(c.idx);
      }
      renderCards();
      renderTopbar();
    });

    div.append(time, text, del);
    list.appendChild(div);
  }
}

async function load() {
  const res = await fetch("/api/episode");
  if (!res.ok) {
    alert("載入 episode 失敗");
    return;
  }
  const data = await res.json();
  state.name = data.name;
  state.crop = data.crop;
  state.deletions = new Set(data.deletions || []);
  state.cards = data.cards || [];
  renderTopbar();
  renderCropInfo();
  renderCards();
}

// 影片時間軸 → highlight 對應卡 + 自動 scroll
$("#video").addEventListener("timeupdate", () => {
  const t = $("#video").currentTime;
  const dur = $("#video").duration;
  $("#time").textContent = `${fmtTime(t)} / ${fmtTime(dur)}`;
  $("#seek").value = dur ? (t / dur) * 100 : 0;

  let active = null;
  for (const c of state.cards) {
    if (t >= c.start && t < c.end) { active = c.idx; break; }
  }
  document.querySelectorAll(".card.playing").forEach((el) => el.classList.remove("playing"));
  if (active != null) {
    const el = document.querySelector(`.card[data-idx="${active}"]`);
    if (el) {
      el.classList.add("playing");
      el.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }
});

$("#play-btn").addEventListener("click", () => {
  const v = $("#video");
  if (v.paused) v.play(); else v.pause();
});

$("#seek").addEventListener("input", (e) => {
  const v = $("#video");
  if (v.duration) v.currentTime = (e.target.value / 100) * v.duration;
});

load();
```

- [ ] **Step 2: 手動驗收（暫時還沒接 CLI，用 uvicorn 手動起）**

Run:
```bash
cd ~/Projects/podcast-toolkit
# 開另一個 terminal 模擬 server，用真實 episode 跑
python3 -c "
import uvicorn
from pathlib import Path
from podcast_toolkit.episode import Episode
from podcast_toolkit.web.api import build_app
ep = Episode(Path('$HOME/Downloads/20260417 過嗨乳牛'))
uvicorn.run(build_app(ep, shutdown=lambda: None), host='127.0.0.1', port=8765)
"
```
開 `http://127.0.0.1:8765`，預期：
- topbar 顯示「過嗨乳牛」+ 卡片總數
- 影片載入並可播
- 右邊字幕卡列表正確顯示
- 點時間欄影片會 seek
- 影片播放時對應卡 highlight + scroll

- [ ] **Step 3: Commit**

```bash
git add podcast_toolkit/web/static/app.js
git commit -m "feat(web): render episode state, video sync, card seek/highlight"
```

---

## Task 10: 前端互動 — 裁切框拖拉

**Files:**
- Modify: `podcast_toolkit/web/static/app.js`

- [ ] **Step 1: 在 `app.js` 檔尾追加裁切框互動程式**

```javascript
// === Crop 框互動 ===
(function setupCrop() {
  const wrap = $(".video-wrap");
  const frame = $("#crop-frame");

  function clamp(v, lo, hi) { return Math.min(Math.max(v, lo), hi); }

  function ensureCrop() {
    if (!state.crop) {
      state.crop = { x: 0.05, y: 0.05, width: 0.9, height: 0.9 };
      renderCropInfo();
    }
  }

  function startDrag(e, mode, edge) {
    e.preventDefault();
    e.stopPropagation();
    ensureCrop();
    const rect = wrap.getBoundingClientRect();
    const startX = e.clientX, startY = e.clientY;
    const c0 = { ...state.crop };

    function onMove(ev) {
      const dx = (ev.clientX - startX) / rect.width;
      const dy = (ev.clientY - startY) / rect.height;
      let { x, y, width, height } = c0;

      if (mode === "move") {
        x = clamp(c0.x + dx, 0, 1 - c0.width);
        y = clamp(c0.y + dy, 0, 1 - c0.height);
      } else {
        if (edge.includes("l")) {
          const nx = clamp(c0.x + dx, 0, c0.x + c0.width - 0.05);
          width = c0.x + c0.width - nx; x = nx;
        }
        if (edge.includes("r")) {
          width = clamp(c0.width + dx, 0.05, 1 - c0.x);
        }
        if (edge.includes("t")) {
          const ny = clamp(c0.y + dy, 0, c0.y + c0.height - 0.05);
          height = c0.y + c0.height - ny; y = ny;
        }
        if (edge.includes("b")) {
          height = clamp(c0.height + dy, 0.05, 1 - c0.y);
        }
      }
      state.crop = { x, y, width, height };
      renderCropInfo();
    }

    function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  frame.addEventListener("mousedown", (e) => {
    if (e.target.classList.contains("handle")) return;
    startDrag(e, "move", null);
  });
  document.querySelectorAll(".handle").forEach((h) => {
    h.addEventListener("mousedown", (e) => startDrag(e, "resize", h.dataset.edge));
  });

  $("#crop-reset").addEventListener("click", () => {
    state.crop = null;
    renderCropInfo();
  });

  // 影片若整張未設過 crop，第一次點影片區自動建一個預設框
  wrap.addEventListener("dblclick", () => ensureCrop());
})();
```

- [ ] **Step 2: 手動驗收**

Run（同 Task 9 啟動方式），預期：
- 雙擊影片區出現裁切框
- 拖中心移動，拖角 handle 縮放
- 按「↺ 重設為整張」隱藏裁切框
- 拖到邊界時被 clamp 不會超出

- [ ] **Step 3: Commit**

```bash
git add podcast_toolkit/web/static/app.js
git commit -m "feat(web): add crop frame drag/resize interaction"
```

---

## Task 11: 前端互動 — 儲存 / 取消

**Files:**
- Modify: `podcast_toolkit/web/static/app.js`

- [ ] **Step 1: 在 `app.js` 檔尾追加儲存與取消按鈕邏輯**

```javascript
// === 儲存 / 取消 ===
$("#save-btn").addEventListener("click", async () => {
  $("#save-btn").disabled = true;
  $("#save-btn").textContent = "儲存中…";
  const payload = {
    crop: state.crop,
    deletions: [...state.deletions].sort((a, b) => a - b),
    cards: [...state.textOverrides.entries()].map(([idx, text]) => ({ idx, text })),
  };
  try {
    const r = await fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    document.body.innerHTML =
      "<div style='padding:40px;text-align:center;font-size:16px'>" +
      "✅ 已儲存,可以關閉這個分頁。" +
      "</div>";
  } catch (e) {
    alert(`儲存失敗：${e.message}`);
    $("#save-btn").disabled = false;
    $("#save-btn").textContent = "完成並儲存";
  }
});

$("#cancel-btn").addEventListener("click", async () => {
  const dirty = state.deletions.size > 0 || state.textOverrides.size > 0 || state.crop != null;
  if (dirty && !confirm("未儲存的修改會丟失,確定取消？")) return;
  try { await fetch("/api/shutdown", { method: "POST" }); } catch (_) {}
  document.body.innerHTML =
    "<div style='padding:40px;text-align:center;font-size:16px'>" +
    "已取消,可以關閉這個分頁。" +
    "</div>";
});
```

- [ ] **Step 2: 手動驗收**

接續用 Task 9 的啟動方式：
- 隨便刪一卡 + 改一段文字 + 拉個 crop → 按「完成並儲存」
- 預期：頁面變「✅ 已儲存」、server log 顯示寫檔、`episode.yaml` 確實有 `crop` / `deletions`、`_v2.srt` 對應行確實被改

- [ ] **Step 3: Commit**

```bash
git add podcast_toolkit/web/static/app.js
git commit -m "feat(web): wire save/cancel buttons to /api endpoints"
```

---

## Task 12: CLI `edit.py`（lockfile + uvicorn + webbrowser + shutdown）

**Files:**
- Create: `podcast_toolkit/edit.py`

- [ ] **Step 1: 實作 `podcast_toolkit/edit.py`**

```python
"""podcast edit：啟動本機 FastAPI + 開瀏覽器 + lockfile。"""
from __future__ import annotations
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from podcast_toolkit.episode import Episode
from podcast_toolkit.web.api import build_app


LOCK_NAME = ".edit.lock"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _acquire_lock(lock_path: Path, port: int) -> bool:
    if lock_path.exists():
        try:
            pid = int(lock_path.read_text(encoding="utf-8").splitlines()[0])
            os.kill(pid, 0)  # 確認 pid 還活著
            return False
        except (ValueError, ProcessLookupError, OSError):
            # 殘留 lockfile,清掉
            try: lock_path.unlink()
            except OSError: pass
    lock_path.write_text(f"{os.getpid()}\n{port}\n", encoding="utf-8")
    return True


def _release_lock(lock_path: Path) -> None:
    try: lock_path.unlink()
    except OSError: pass


def run(episode_dir: Path) -> int:
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

    port = _find_free_port()
    lock_path = ep.subdir("work") / LOCK_NAME
    if not _acquire_lock(lock_path, port):
        print(f"✗ 已有 podcast edit 在跑：{lock_path}", file=sys.stderr)
        return 1

    server = {"instance": None}

    def shutdown_callback():
        if server["instance"] is not None:
            server["instance"].should_exit = True

    app = build_app(ep, shutdown=shutdown_callback)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server["instance"] = uvicorn.Server(config)

    url = f"http://127.0.0.1:{port}"
    print(f"→ 啟動編輯介面：{url}")
    # 延遲 0.5s 開瀏覽器,讓 server 先 ready
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server["instance"].run()
    finally:
        _release_lock(lock_path)

    print("✅ 編輯結束")
    return 0
```

- [ ] **Step 2: 手動驗收（先暫時直接跑函式,Task 13 才接 CLI）**

```bash
python3 -c "
from pathlib import Path
from podcast_toolkit.edit import run
run(Path('$HOME/Downloads/20260417 過嗨乳牛'))
"
```
預期：瀏覽器自動開、編輯、按「完成並儲存」→ CLI 印「✅ 編輯結束」並 exit 0。Lockfile 沒有殘留。

- [ ] **Step 3: Commit**

```bash
git add podcast_toolkit/edit.py
git commit -m "feat(edit): add CLI module with uvicorn / browser / lockfile"
```

---

## Task 13: 把 `edit` 接進 `cli.py`

**Files:**
- Modify: `podcast_toolkit/cli.py`

- [ ] **Step 1: 在 `cli.py` 加入 `cmd_edit` 與 subparser**

在 `def cmd_relink(args):` 後面、`def build_parser():` 前面加：

```python
def cmd_edit(args):
    from podcast_toolkit import edit
    return edit.run(Path(args.path))
```

在 `build_parser()` 函式內、`prl = sub.add_parser("relink", ...)` 之前加：

```python
    pe = sub.add_parser("edit", help="在瀏覽器編輯：裁切 / 刪段 / 改字")
    pe.add_argument("path", nargs="?", default=".", help="集資料夾路徑（預設：當前目錄）")
    pe.set_defaults(func=cmd_edit)
```

- [ ] **Step 2: 手動驗證 CLI 載得到指令**

Run: `podcast --help`
Expected：output 含 `edit` 子指令。

Run: `podcast edit --help`
Expected：印 path 參數說明。

- [ ] **Step 3: 端對端驗收**

```bash
podcast edit "$HOME/Downloads/20260417 過嗨乳牛"
```
預期：瀏覽器自動開、可編輯、按存檔後 CLI exit 0、episode.yaml 真的被改。

- [ ] **Step 4: Commit**

```bash
git add podcast_toolkit/cli.py
git commit -m "feat(cli): wire podcast edit subcommand"
```

---

## Task 14: `assemble.py` — 加 crop filter

**Files:**
- Modify: `podcast_toolkit/assemble.py`
- Test: `tests/test_assemble_filters.py` (Create)

> **策略：** 把 ffmpeg `filter_complex` 字串組裝抽到一個純函式 `build_filter_complex(cfg, main_dur)`,便於測試。

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_assemble_filters.py`:

```python
"""assemble.py 的 filter_complex 字串組裝測試。"""
import pytest

from podcast_toolkit import assemble


BASE_CFG = {
    "encode": {
        "resolution": "1920x1080",
        "framerate": 30,
        "pix_fmt": "yuv420p",
        "audio_sample_rate": 48000,
    },
    "assets": {
        "intro_duration": 5,
        "intro_fade_out": 1,
        "outro_duration": 5,
    },
    "subtitle_style": {
        "font_name": "F", "font_size": 28, "bold": 1,
        "primary_colour": "&H00FFFFFF", "outline_colour": "&H00000000",
        "border_style": 1, "outline": 2, "shadow": 0, "margin_v": 60,
    },
    "crop": None,
    "deletions": [],
}


def test_filter_complex_no_crop_no_deletions(monkeypatch):
    fc = assemble.build_filter_complex(BASE_CFG, main_dur=100.0, srt_rel="x.srt")
    assert "crop=" not in fc
    assert "select=" not in fc


def test_filter_complex_with_crop_adds_crop_filter():
    cfg = {**BASE_CFG, "crop": {"x": 0.1, "y": 0.05, "width": 0.8, "height": 0.9}}
    fc = assemble.build_filter_complex(cfg, main_dur=100.0, srt_rel="x.srt")
    # 1920 * 0.8 = 1536, 1080 * 0.9 = 972, x=192, y=54
    assert "crop=1536:972:192:54" in fc
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_assemble_filters.py -v`
Expected: FAIL (`AttributeError: module 'assemble' has no attribute 'build_filter_complex'`)。

- [ ] **Step 3: 在 `assemble.py` 抽出 `build_filter_complex` 純函式**

把 `run()` 內 `fc = (...)` 那一整塊抽出來變成獨立函式（在檔案上方,`run` 之前）：

```python
def build_filter_complex(cfg: dict, main_dur: float, srt_rel: str) -> str:
    """組裝 ffmpeg filter_complex 字串（含 crop / deletions 支援）。"""
    enc = cfg["encode"]
    res_w, res_h = enc["resolution"].split("x")
    intro_dur = cfg["assets"]["intro_duration"]
    intro_fade_out = cfg["assets"]["intro_fade_out"]
    style_str = build_style_string(cfg["subtitle_style"])

    # main video 前處理 chain：選擇性加 crop
    crop = cfg.get("crop")
    crop_part = ""
    if crop:
        # crop 比例 → px:需在 scale 前算對影片原始尺寸的比例 → 但這裡偷懒,
        # 直接用最終 resolution 換算（影片 scale 後一致）
        cw = int(int(res_w) * crop["width"])
        ch = int(int(res_h) * crop["height"])
        cx = int(int(res_w) * crop["x"])
        cy = int(int(res_h) * crop["y"])
        crop_part = f"crop={cw}:{ch}:{cx}:{cy},"

    return (
        f"[0:v]scale={res_w}:{res_h},setsar=1,fps={enc['framerate']},"
        f"format={enc['pix_fmt']},fade=t=out:st={intro_dur - intro_fade_out}:d={intro_fade_out}[v0];"
        f"[1:v]subtitles={srt_rel}:force_style='{style_str}',"
        f"scale={res_w}:{res_h},{crop_part}setsar=1,"
        f"fps={enc['framerate']},format={enc['pix_fmt']},"
        f"fade=t=in:st=0:d=0.5,fade=t=out:st={main_dur - 0.5}:d=0.5[v1];"
        f"[2:v]scale={res_w}:{res_h},setsar=1,fps={enc['framerate']},"
        f"format={enc['pix_fmt']},fade=t=in:st=0:d=0.5[v2];"
        f"[0:a]aformat=sample_rates={enc['audio_sample_rate']}:channel_layouts=stereo,"
        f"afade=t=out:st={intro_dur - intro_fade_out}:d={intro_fade_out}[a0];"
        f"[1:a]aformat=sample_rates={enc['audio_sample_rate']}:channel_layouts=stereo,"
        f"afade=t=in:st=0:d=0.5,afade=t=out:st={main_dur - 0.5}:d=0.5[a1];"
        f"[3:a]aformat=sample_rates={enc['audio_sample_rate']}:channel_layouts=stereo,"
        f"afade=t=in:st=0:d=0.5[a2];"
        f"[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[v][a]"
    )
```

並把 `run()` 內原本的 `fc = (...)` 換成：
```python
    fc = build_filter_complex(cfg, main_dur=main_dur, srt_rel=srt_rel)
```

刪除舊的 `res_w, res_h = enc["resolution"].split("x")` 與 `style_str = ...` 因為已搬到 `build_filter_complex`。`out_rel` 等變數保留。

- [ ] **Step 4: 跑測試確認通過**

Run: `pytest tests/test_assemble_filters.py -v`
Expected: 兩個 test 全 PASS。

- [ ] **Step 5: 跑 regression 確認既有行為不破**

Run: `bash tests/regression.sh`
Expected: `✅ regression passed`（沒有 crop/deletions 的情況下行為不變）。

- [ ] **Step 6: Commit**

```bash
git add podcast_toolkit/assemble.py tests/test_assemble_filters.py
git commit -m "feat(assemble): support crop filter from episode.yaml"
```

---

## Task 15: `assemble.py` — 段落刪除（基於字幕卡時間區間）

**Files:**
- Modify: `podcast_toolkit/assemble.py`
- Modify: `tests/test_assemble_filters.py`

> **策略：** 刪除段落用 ffmpeg `select` + `setpts` filter（影片）+ `aselect` + `asetpts`（音訊）。需要：
> 1. 從 `_v2.srt` 讀字幕卡 → 得到要刪除的 idx → 對應時間區間
> 2. 構造 `select='not(between(t,a1,b1)+between(t,a2,b2))'` 跳過刪除區間
> 3. 燒字幕前也要把刪除段濾掉,否則畫面跳了字幕還留著（**解法：在原 _v2.srt 之外另寫一個 `_v2_assembled.srt` 給 ffmpeg 用,過濾掉刪除段**）

- [ ] **Step 1: 在 `tests/test_assemble_filters.py` 追加失敗測試**

```python
def test_filter_complex_with_deletions_adds_select():
    cfg = {**BASE_CFG, "deletions": [3]}
    intervals = [(12.0, 14.0)]
    fc = assemble.build_filter_complex(
        cfg, main_dur=100.0, srt_rel="x.srt", deletion_intervals=intervals
    )
    assert "select='not(between(t" in fc
    assert "between(t,12.000,14.000)" in fc.replace(" ", "")
    assert "aselect=" in fc


def test_build_deletion_intervals_returns_card_time_ranges(tmp_episode_dir):
    from podcast_toolkit import assemble as asm
    intervals = asm.build_deletion_intervals(
        v2_srt_path=tmp_episode_dir / "03_成品" / "測試集_final_v2.srt",
        deletions=[3],
    )
    assert intervals == [(12.0, 14.0)]


def test_filter_deletion_srt_writes_clean_srt(tmp_path):
    from podcast_toolkit import assemble as asm
    src = tmp_path / "in.srt"
    src.write_text(
        "1\n00:00:00,000 --> 00:00:04,000\nA\n\n"
        "2\n00:00:04,000 --> 00:00:08,000\nB\n\n"
        "3\n00:00:08,000 --> 00:00:12,000\nC\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.srt"
    asm.filter_deletion_srt(src, out, deletions=[2])
    text = out.read_text(encoding="utf-8")
    assert "B" not in text
    assert "A" in text and "C" in text
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_assemble_filters.py -v`
Expected: 新增三個 FAIL。

- [ ] **Step 3: 在 `assemble.py` 加 `build_deletion_intervals` 與 `filter_deletion_srt`,並擴充 `build_filter_complex`**

在 `build_filter_complex` 上方加：

```python
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
    """把要刪除的字幕段拿掉,寫到 dst（idx 仍維持原樣,ffmpeg 不在意）。"""
    from podcast_toolkit import srt_io
    cards = srt_io.parse(src.read_text(encoding="utf-8"))
    deletion_set = {int(i) for i in deletions or []}
    kept = [c for c in cards if c["idx"] not in deletion_set]
    dst.write_text(srt_io.serialize(kept), encoding="utf-8")
```

把 `build_filter_complex` 改成接 `deletion_intervals` 參數,在主片 chain 裡注入 `select` / `aselect`:

```python
def build_filter_complex(
    cfg: dict,
    main_dur: float,
    srt_rel: str,
    deletion_intervals: list[tuple[float, float]] | None = None,
) -> str:
    enc = cfg["encode"]
    res_w, res_h = enc["resolution"].split("x")
    intro_dur = cfg["assets"]["intro_duration"]
    intro_fade_out = cfg["assets"]["intro_fade_out"]
    style_str = build_style_string(cfg["subtitle_style"])

    crop = cfg.get("crop")
    crop_part = ""
    if crop:
        cw = int(int(res_w) * crop["width"])
        ch = int(int(res_h) * crop["height"])
        cx = int(int(res_w) * crop["x"])
        cy = int(int(res_h) * crop["y"])
        crop_part = f"crop={cw}:{ch}:{cx}:{cy},"

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
```

改 `run()` 內呼叫處：

```python
    deletions = list(cfg.get("deletions") or [])
    deletion_intervals = build_deletion_intervals(srt, deletions) if deletions else []

    # 如果有 deletions,先寫一個過濾後的 srt 給 ffmpeg 燒
    if deletions:
        clean_srt = ep.subdir("work") / "_v2_assembled.srt"
        filter_deletion_srt(srt, clean_srt, deletions)
        srt = clean_srt
        srt_rel = str(srt.relative_to(cwd)) if srt.is_relative_to(cwd) else str(srt)
        # 同時調整 main_dur:扣掉刪除區間總長
        deleted_total = sum(b - a for a, b in deletion_intervals)
        main_dur = main_dur - deleted_total

    fc = build_filter_complex(cfg, main_dur=main_dur, srt_rel=srt_rel,
                              deletion_intervals=deletion_intervals)
```

注意：`main_dur` 在計算 fade-out 時間時用,刪段後的有效時長要扣掉刪除區間總長。

- [ ] **Step 4: 跑測試確認通過**

Run: `pytest tests/test_assemble_filters.py -v`
Expected: 所有測試（含先前的）PASS。

- [ ] **Step 5: 跑 regression 確認既有行為不破**

Run: `bash tests/regression.sh`
Expected: `✅ regression passed`。

- [ ] **Step 6: Commit**

```bash
git add podcast_toolkit/assemble.py tests/test_assemble_filters.py
git commit -m "feat(assemble): support deletions via select filter + srt filtering"
```

---

## Task 16: regression 補帶 crop/deletions 的 fixture

**Files:**
- Modify: `tests/regression.sh`
- Create: `tests/fixtures/edit_episode.yaml`

- [ ] **Step 1: 建一個 fixture episode.yaml（不跑 ffmpeg,只驗 dry-run 輸出）**

Create `tests/fixtures/edit_episode.yaml`:

```yaml
# 這是 regression 用的最小 fixture,只給 assemble --dry-run 檢查 ffmpeg 指令含 crop + select
crop:
  x: 0.05
  y: 0.05
  width: 0.9
  height: 0.9
deletions: [1]
```

- [ ] **Step 2: 在 `tests/regression.sh` 補一段 dry-run 比對**

在原本 diff 段後追加：

```bash
echo "→ 驗 assemble --dry-run 對 crop/deletions 的支援（不真跑 ffmpeg）"
TMP_EP=$(mktemp -d)
mkdir -p "$TMP_EP/01_母帶" "$TMP_EP/02_片頭片尾" "$TMP_EP/03_成品" "$TMP_EP/04_工作檔"
# 複製主檔
cp "$EP/01_母帶/過嗨乳牛.mp4" "$TMP_EP/01_母帶/regression.mp4" 2>/dev/null || \
    ln -s "$EP/01_母帶/過嗨乳牛.mp4" "$TMP_EP/01_母帶/regression.mp4"
cp "$EP/03_成品/過嗨乳牛_final_v2.srt" "$TMP_EP/03_成品/regression_final_v2.srt"
# 連結 toolkit 共用資產
podcast init "$TMP_EP" 2>/dev/null || true
# 蓋掉 episode.yaml 把 fixture 加進去
cat > "$TMP_EP/episode.yaml" <<EOF
date: 20260101
name: regression
main_video: 01_母帶/{name}.mp4
main_srt: 01_母帶/{name}.srt
fixes: []
card_fixes: []
force_break: []
force_join: []
$(cat "$FIXTURE_DIR/edit_episode.yaml")
EOF

OUT=$(podcast assemble "$TMP_EP" --dry-run)
echo "$OUT" | grep -q "crop=" || { echo "✗ 預期 ffmpeg 指令含 crop=,實際沒有"; exit 1; }
echo "$OUT" | grep -q "select=" || { echo "✗ 預期 ffmpeg 指令含 select=,實際沒有"; exit 1; }
trash "$TMP_EP" 2>/dev/null || rm -rf "$TMP_EP"
echo "  ✓ dry-run 含 crop + select"
```

> **注意：** 本機若沒有 `trash` CLI,用 `mv "$TMP_EP" "$HOME/.Trash/"` 或 `rm -rf` 都可（這是測試臨時目錄,user CLAUDE.md 的 `rm -rf` 禁令針對工作檔,測試 fixture 例外）。

- [ ] **Step 2: 跑 regression**

Run: `bash tests/regression.sh`
Expected: 含 `✓ dry-run 含 crop + select` 並結束於 `✅ regression passed`。

- [ ] **Step 3: Commit**

```bash
git add tests/regression.sh tests/fixtures/edit_episode.yaml
git commit -m "test: extend regression to cover crop / deletions in assemble"
```

---

## Task 17: 更新 README + 端對端手動驗收

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 在 README 工作流區插入第 5.5 步**

把這段：

```bash
# 6. 跑 assemble 合成
podcast assemble "$HOME/Downloads/20260601 新集名"
```

改成：

```bash
# 5.5 (可選) 視覺化編輯：裁切畫框 / 刪段 / 改字
podcast edit "$HOME/Downloads/20260601 新集名"

# 6. 跑 assemble 合成
podcast assemble "$HOME/Downloads/20260601 新集名"
```

並在「指令」段加一行：

```
- `podcast edit <path>` — 開瀏覽器視覺化編輯：裁切 / 刪段 / 改字
```

- [ ] **Step 2: 在 README 末段加「手動驗收清單」**

```markdown
## podcast edit 手動驗收

1. `podcast edit <path>` → 瀏覽器自動開、影片可播
2. 雙擊影片區出現裁切框 → 拖角縮放、拖中心移動
3. 點字幕卡時間欄 → 影片跳到對應時間
4. inline 改錯字 → 失焦後文字變橘色底線
5. ✕ 刪卡 → 卡片變灰、再點 ↺ 還原
6. 「完成並儲存」→ 頁面變「✅ 已儲存」、CLI exit 0、`episode.yaml` 與 `_v2.srt` 真的有改
7. `podcast assemble` → 輸出 mp4 解析度、時長、字幕都套用編輯結果
```

- [ ] **Step 3: 端對端跑一次真實 episode 驗收（人工檢查）**

Run:
```bash
podcast edit "$HOME/Downloads/20260417 過嗨乳牛"
# 隨便刪幾卡 + 改一處錯字 + 拉一個 crop → 完成並儲存
git -C "$HOME/Downloads/20260417 過嗨乳牛" diff episode.yaml  # 確認 yaml 改了（如果該資料夾有 git）
# 看 _v2.srt 改了
diff "$HOME/Downloads/20260417 過嗨乳牛/03_成品/過嗨乳牛_final_v2.srt" \
     "$HOME/Projects/podcast-toolkit/tests/fixtures/expected_v2.srt" || true

podcast assemble "$HOME/Downloads/20260417 過嗨乳牛" --force
# 用任何 player 開輸出 mp4 確認 crop + 刪段都套用了
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(README): add podcast edit workflow + manual QA checklist"
```

---

## Self-Review 完成。Spec 覆蓋對照

| Spec 章節 | 對應 Task |
|---|---|
| 整體架構 | Task 12, 13 |
| UI 版面 | Task 8, 9, 10, 11 |
| 後端 API（5 條） | Task 4, 5, 6, 7 |
| 資料模型（crop/deletions） | Task 2, 4, 5 |
| _v2.srt 處理 | Task 3, 5 |
| assemble crop 支援 | Task 14 |
| assemble deletions 支援 | Task 15 |
| 錯誤處理（lockfile、缺檔、port） | Task 12 |
| 單元測試 | Task 2-7, 14-15 |
| Regression | Task 16 |
| 手動驗收清單 | Task 17 |
| 依賴新增（fastapi/uvicorn/pytest） | Task 1 |
| CLI 變更 | Task 13 |

無 placeholder、無 TBD。任務間型別一致（`crop` 為 dict 或 None、`deletions` 為 list[int]、`cards` payload 為 `[{idx, text}, ...]`）。

---

# 上線後追加（Post-launch additions）

原計劃涵蓋 T1~T17。以下任務在主線交付後依使用者回饋追加，原則上不回頭補進前面 Task 編號表，改用「T## 名稱」管理。已完成的不寫細節，只記錄入口點，方便未來 grep 找到實作位置。

## 已完成（commits in `vs/edit-ui`）

| T## | 名稱 | 主要實作位置 |
|---|---|---|
| T15 | Reels crop 後 scale 回 1080×1920 | `podcast_toolkit/assemble.py` filter chain |
| T16 | `_v2.srt` 缺檔時 `/api/episode` 寬容回應 | `episode_io.py::load_state` `needs_transcribe` flag |
| T17 | web transcribe 產出單字 SRT 後補 resegment | `web/api.py` transcribe endpoint |
| T18 | 儲存按鈕不關 server + 儲存成功 highlight 合成按鈕 | `web/static/app.js` save handler |
| T20 | 標紅可疑空拍卡 + checkbox 批次刪 | `episode_io.py::_flag_suspicious_pause` + 前端 sus-toolbar |
| T21 | 影片時間軸頭尾 trim handle | `head_trim_sec` / `tail_trim_sec` in yaml + frontend trim-band |
| T22 | 把開始合成拆成「合成 YT」「合成 Reels」兩個按鈕 | `web/static/index.html` topbar + `assemble-yt-btn` / `assemble-reels-btn` |
| T23 | 雙鏡頭切換討論：episode.yaml schema + UI | spec：`docs/superpowers/specs/2026-05-27-podcast-edit-ui-design.md` 末段 |
| T23a | 雙鏡頭基礎：schema + sidecar + 字幕卡 A/B toggle + ffmpeg 合成 | `cameras_io.py` + `assemble.py` multicam dispatch + `_v2.cameras.json` sidecar |

## T23a-followup：雙鏡頭設定 UI（消除手改 yaml）

**動機：** T23a 上線後使用者明確要求「不希望使用者手改 `episode.yaml`」。`cameras.b` 與 `camera_sync_offset.b` 必須有 UI 入口寫入。

**架構：**
- 前端：topbar 加「🎥 鏡頭」按鈕 → 開 modal → 從 `01_母帶/*.mp4` 下拉選 cam B + 數字輸入 offset → 儲存。
- 後端：擴充現有 `episode_io.py::load_state` / `save_state`，新增 `cam_b_candidates` 欄位（前端下拉用）；payload 新增 `cam_b_path` / `camera_sync_offset_b` 兩 key（用 key-presence 區分「沒動 UI」vs「明確清空」）。
- 無新檔案、無新 endpoint。沿用 `/api/episode` (GET) + `/api/save` (POST)。

**File Structure：**
- Modify: `podcast_toolkit/web/episode_io.py` — `_list_cam_b_candidates()` helper + `load_state` 多回 `cameras` / `camera_sync_offset` / `cam_b_candidates`；`save_state` 處理 `cam_b_path` / `camera_sync_offset_b`
- Modify: `podcast_toolkit/web/static/index.html` — `cam-btn` topbar 按鈕 + `cam-modal` markup（select + number input）
- Modify: `podcast_toolkit/web/static/app.js` — `state.camBCandidates` / `state.camSyncOffsetB` + `openCamModal()` + 儲存 handler
- Test: `tests/test_episode_io.py` — 候選掃描 + payload round-trip

### 進度快照（要 grep 找到接續點）

**已完成：**

- [x] **Backend：** `_list_cam_b_candidates()` 掃 `01_母帶/*.mp4`（排除 cam A）→ 寫進 `load_state` 回傳的 `cam_b_candidates`
- [x] **Backend：** `save_state` 用 `"cam_b_path" in payload` / `"camera_sync_offset_b" in payload` 判 key-presence；空字串 / 0 → 整段移除對應 yaml 欄位
- [x] **Bug fix：** `_list_cam_b_candidates` 原本用 `glob("*.mp4")` 在 macOS APFS 上漏掉大寫 `.MP4`（DJI / iPhone 預設大寫）。改用 `iterdir()` + `entry.suffix.lower() != ".mp4"` 過濾。
- [x] **Test：** `test_load_state_cam_b_candidates_handles_uppercase_extension` — RED→GREEN，確認大小寫副檔名都列出。
- [x] **Frontend：** `index.html` 加 `cam-btn` topbar 按鈕 + `cam-modal` markup（`cam-b-select` 下拉 + `cam-sync-offset-b` number input）
- [x] **Frontend：** `app.js` 加 `state.camBCandidates` / `state.camSyncOffsetB` 同步、`openCamModal()` 函式、modal 儲存按鈕 POST `/api/save` 後 refetch state
- [x] **Tests：** 全測試 118/118 GREEN

**未驗證：**

- [ ] **Browser dogfood：** 起 dev server 走完整 modal flow（開 modal → 選 cam B → 設 offset → 儲存 → 確認 `episode.yaml` 真的寫入 `cameras.b` + `camera_sync_offset.b` → 字幕卡出現 A/B toggle）
- [ ] **Commit：** 目前 4 個檔案 uncommitted

### 接續步驟（新 session pickup）

- [ ] **Step 1：起 dev server 在測試 episode**

```bash
cd "/Users/vincentsia/Desktop/vibe-coding playground/podcast-toolkit"
BROWSER=false PORT=58901 python bin/podcast edit "/tmp/podcast-test-camB/20260604 雙鏡測試"
# fixture 已存在：cam A 1 檔 + cam B 候選 2 檔（含大寫 .MP4）+ 2 張字幕卡
```

如果 fixture 被清掉，重建：

```bash
mkdir -p "/tmp/podcast-test-camB/20260604 雙鏡測試/01_母帶"
mkdir -p "/tmp/podcast-test-camB/20260604 雙鏡測試/03_成品"
# cam A
touch "/tmp/podcast-test-camB/20260604 雙鏡測試/01_母帶/雙鏡測試.mp4"
# cam B 候選（大小寫各一）
touch "/tmp/podcast-test-camB/20260604 雙鏡測試/01_母帶/B-roll.mp4"
touch "/tmp/podcast-test-camB/20260604 雙鏡測試/01_母帶/DJI_001.MP4"
# 最小 episode.yaml
cat > "/tmp/podcast-test-camB/20260604 雙鏡測試/episode.yaml" <<'EOF'
name: 雙鏡測試
main_video: 01_母帶/{name}.mp4
EOF
# 最小 _v2.srt 兩張卡
cat > "/tmp/podcast-test-camB/20260604 雙鏡測試/03_成品/雙鏡測試_final_v2.srt" <<'EOF'
1
00:00:00,000 --> 00:00:03,000
測試卡片一

2
00:00:03,000 --> 00:00:06,000
測試卡片二
EOF
```

- [ ] **Step 2：用 `/browse` skill 走 UI flow**

1. navigate `http://127.0.0.1:58901/`
2. 確認 topbar 看得到「🎥 鏡頭」按鈕
3. click `#cam-btn` → 確認 modal 顯示，`#cam-b-select` 下拉同時列出 `01_母帶/B-roll.mp4` 與 `01_母帶/DJI_001.MP4`
4. 選 `B-roll.mp4`、`#cam-sync-offset-b` 填 1.5 → click `#cam-save`
5. modal 關閉、page refetch
6. 截圖 / network log 留證

- [ ] **Step 3：驗 yaml 寫入**

```bash
cat "/tmp/podcast-test-camB/20260604 雙鏡測試/episode.yaml"
# 預期看到：
# cameras:
#   a: 01_母帶/雙鏡測試.mp4
#   b: 01_母帶/B-roll.mp4
# camera_sync_offset:
#   b: 1.5
```

- [ ] **Step 4：驗 A/B toggle 出現**

回到瀏覽器，確認字幕卡列右側出現 A/B 切換按鈕（multicam mode 啟用條件 = `state.cameras.b` 存在）。

- [ ] **Step 5：驗清空語意**

再開 modal、把 `#cam-b-select` 選回「無」、`#cam-sync-offset-b` 清空 → 儲存。預期：`episode.yaml` 移除 `cameras.b` 與 `camera_sync_offset` 整段，留 `cameras.a`。

- [ ] **Step 6：切兩個 commit（不 push，等使用者）**

```bash
cd "/Users/vincentsia/Desktop/vibe-coding playground/podcast-toolkit"

# Commit 1：後端 bug fix
git add podcast_toolkit/web/episode_io.py tests/test_episode_io.py
git commit -m "fix(t23a-followup): cam B 候選掃描支援大寫 .MP4 (DJI / iPhone)"

# Commit 2：前端 UI
git add podcast_toolkit/web/static/app.js podcast_toolkit/web/static/index.html
git commit -m "feat(t23a-followup): cam B 設定 modal（消除手改 yaml）"

git status  # 確認 clean
git log --oneline -5
```

- [ ] **Step 7：問使用者要不要 push**

CLAUDE.md 要求：push 前必問。準備好 commit 摘要 → 問「兩個 commit 要 push 到 origin/vs/edit-ui 嗎？」

## 待辦（尚未動工）

| T## | 名稱 | 備註 |
|---|---|---|
| T14 | 預覽按鈕點了沒影片：診斷 + 修 | long-stale，使用者可能已不在意；接續前先確認 |
| T19 | Gemini API 取代 xAI Grok：轉錄 + 斷句 + 錯字一次到位 | 新依賴 + key 管理；要先跟使用者對齊 |
| T23b | 自動對齊 L1：音訊互相關計算 offset | 算完直接寫進 `camera_sync_offset.b` |
| T23c | 手動標記 L2：三檔聲音事件標記 | T23b 不準時的 fallback |

接續任一項前都要先跟使用者確認範圍（Scope Discipline）。


