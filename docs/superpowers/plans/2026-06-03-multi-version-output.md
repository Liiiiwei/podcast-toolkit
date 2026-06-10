# 多版本輸出（YT 16:9 + Reels 9:16）+ 檔案分類 UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 同時輸出 YT 16:9 完整版 + Reels 9:16 短版兩個檔案，UI 提供版本切換與獨立 crop 設定；檔案列表分類成 6 區（主影片 / 字幕 / 合成輸出 / 片頭片尾 / 母帶 / 工作檔），並用 tmp+rename 避免 ffmpeg 中斷破壞舊輸出。

**Architecture:** Backend 用 `output_kind="yt"|"reels"` 參數讓 `prepare_assembly` 分支生成兩種 ffmpeg cmd（Reels = 1080×1920、無片頭片尾、用 `crop_reels`）；`assemble_job` 改成 queue 跑多個 target，每個 job 用 tmp 檔輸出、成功才 rename 蓋掉成品。Frontend 加版本 tab，每個版本 state 獨立記 `cropYt`/`cropReels`，組態存 episode.yaml 時兩份分開存。檔案列表後端打 `kind` 標籤，前端 group-by 渲染六大區塊（含 localStorage 折疊狀態）。

**Tech Stack:** FastAPI + uvicorn / ffmpeg filter_complex / Python subprocess.Popen + threading / 原生 JS + DOM（沒有框架）/ pytest

**Boundary decisions（已確認）:**
- Reels 使用與 YT 相同的 `subtitle_style` 燒字幕（不另做樣式）
- Reels = 完整長度（不裁時間，沿用 deletions）
- Reels crop 預設置中（x=0, y=0, width=1, height=1 等於不裁）
- 既有 `crop` 欄位視為 `crop_yt` 一次性遷移；舊 episode.yaml 自動相容

---

## File Structure

**Backend modified:**
- `podcast_toolkit/config.py` — `merge()` 加 `crop_yt` / `crop_reels` 兩欄位 + legacy `crop` 遷移
- `podcast_toolkit/episode.py` — `output_reels_video()` 新方法
- `podcast_toolkit/assemble.py` — `prepare_assembly(ep_dir, output_kind, force)` 分支、`build_filter_complex` 拆 YT/Reels、tmp_out 寫到 work/ 暫存
- `podcast_toolkit/web/assemble_job.py` — `start_job(ep, targets, force)` 接受 list、_STATE 改成 queue + current + history
- `podcast_toolkit/web/api.py` — `/api/assemble` 接受 `{targets, force}` body、`_list_episode_files` 加 `kind`
- `podcast_toolkit/web/episode_io.py` — `load_state` 回 `crop_yt`/`crop_reels`、`save_state` 寫回兩欄

**Frontend modified:**
- `podcast_toolkit/web/static/index.html` — 版本 tab + assemble-modal 加 checkbox
- `podcast_toolkit/web/static/app.js` — state 拆 `cropYt`/`cropReels`/`activeVersion`、tab 切換、queue 進度渲染、`renderFiles` group-by
- `podcast_toolkit/web/static/app.css` — tab 樣式、checkbox、queue 進度前綴、檔案分區折疊

**Tests modified:**
- `tests/test_assemble_filters.py` — 新增 reels 分支測試
- `tests/test_config_merge.py` — `crop_yt`/`crop_reels` + legacy 遷移測試
- `tests/test_api_routes.py` — `/api/assemble` 接 targets list 測試
- `tests/test_episode_io.py` — load/save 兩個 crop 欄位測試

---

# Phase 1：Backend 多版本基礎建設

---

### Task 1: 設定 schema 加 crop_yt / crop_reels + Episode.output_reels_video()

**Files:**
- Modify: `podcast_toolkit/config.py:33-58`
- Modify: `podcast_toolkit/episode.py:42-46`
- Test: `tests/test_config_merge.py`
- Test: `tests/test_episode_io.py`

- [ ] **Step 1: 寫測試 `test_config_merge_crop_yt_and_reels`**

```python
# tests/test_config_merge.py 新增
def test_merge_crop_yt_and_reels():
    defaults = {
        "resegment": {}, "subtitle_style": {}, "assets": {}, "encode": {},
        "common_fixes": [],
    }
    episode = {
        "crop_yt": {"x": 0.1, "y": 0.0, "width": 0.8, "height": 1.0},
        "crop_reels": {"x": 0.3, "y": 0.0, "width": 0.4, "height": 1.0},
    }
    cfg = config.merge(defaults, episode)
    assert cfg["crop_yt"] == {"x": 0.1, "y": 0.0, "width": 0.8, "height": 1.0}
    assert cfg["crop_reels"] == {"x": 0.3, "y": 0.0, "width": 0.4, "height": 1.0}
    assert cfg.get("crop") is None  # 舊欄位不再透出


def test_merge_legacy_crop_migrated_to_crop_yt():
    """舊 episode.yaml 只有 crop，自動視為 crop_yt。"""
    defaults = {
        "resegment": {}, "subtitle_style": {}, "assets": {}, "encode": {},
        "common_fixes": [],
    }
    episode = {"crop": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 0.5625}}
    cfg = config.merge(defaults, episode)
    assert cfg["crop_yt"] == {"x": 0.0, "y": 0.0, "width": 1.0, "height": 0.5625}
    assert cfg["crop_reels"] is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_config_merge.py::test_merge_crop_yt_and_reels tests/test_config_merge.py::test_merge_legacy_crop_migrated_to_crop_yt -v`
Expected: FAIL（cfg["crop_yt"] 不存在或仍是 None）

- [ ] **Step 3: 改 `merge()` 加兩欄位 + legacy 遷移**

```python
# podcast_toolkit/config.py:33-58 取代 merge() 整個函式
def merge(defaults: dict, episode: dict) -> dict:
    cfg = {
        "resegment": {**defaults["resegment"], **(episode.get("resegment") or {})},
        "subtitle_style": {**defaults["subtitle_style"], **(episode.get("subtitle_style") or {})},
        "assets": dict(defaults["assets"]),
        "encode": dict(defaults["encode"]),
        "fixes": list(defaults.get("common_fixes") or []) + list(episode.get("fixes") or []),
        "card_fixes": list(episode.get("card_fixes") or []),
        "date": episode.get("date"),
        "name": episode.get("name"),
        "main_video": episode.get("main_video"),
        "main_srt": episode.get("main_srt"),
        "force_break": set(episode.get("force_break") or []),
        "force_join": set(episode.get("force_join") or []),
        "crop_yt": episode.get("crop_yt"),
        "crop_reels": episode.get("crop_reels"),
        "deletions": list(episode.get("deletions") or []),
    }
    # legacy 遷移：episode.yaml 還在用 crop → 視為 crop_yt
    if cfg["crop_yt"] is None and episode.get("crop") is not None:
        cfg["crop_yt"] = episode["crop"]
    return cfg
```

- [ ] **Step 4: 跑測試確認通過**

Run: `pytest tests/test_config_merge.py -v`
Expected: PASS

- [ ] **Step 5: 寫測試 `test_episode_output_reels_video`**

```python
# tests/test_episode_io.py 新增
def test_episode_output_reels_video(tmp_episode):
    ep = Episode(tmp_episode)
    out = ep.output_reels_video()
    assert out.name.endswith("_Reels.mp4")
    assert out.parent.name == "03_成品"
```

- [ ] **Step 6: 跑測試確認失敗**

Run: `pytest tests/test_episode_io.py::test_episode_output_reels_video -v`
Expected: FAIL（output_reels_video 不存在）

- [ ] **Step 7: 加 Episode.output_reels_video()**

```python
# podcast_toolkit/episode.py:45-46 之後新增
def output_reels_video(self) -> Path:
    return self.subdir("output") / f"{self.name}_Reels.mp4"
```

- [ ] **Step 8: 跑測試確認通過**

Run: `pytest tests/test_episode_io.py -v tests/test_config_merge.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add podcast_toolkit/config.py podcast_toolkit/episode.py tests/test_config_merge.py tests/test_episode_io.py
git commit -m "feat: 設定加 crop_yt/crop_reels 雙欄位 + Episode.output_reels_video()

舊 episode.yaml 的 crop 欄位自動視為 crop_yt（一次性遷移），
之後寫回時用 crop_yt 取代 crop。新增 Reels 輸出檔名固定為
{name}_Reels.mp4，與 YT 完整版分開存放於 03_成品/。"
```

---

### Task 2: prepare_assembly 分 YT / Reels 分支

**Files:**
- Modify: `podcast_toolkit/assemble.py:64-218`
- Test: `tests/test_assemble_filters.py`

- [ ] **Step 1: 寫測試 `test_prepare_assembly_yt_default`**

```python
# tests/test_assemble_filters.py 新增
def test_prepare_assembly_yt_uses_yt_output(tmp_episode_full):
    """output_kind='yt' 時輸出檔是 _YT完整版.mp4。"""
    plan = prepare_assembly(tmp_episode_full, output_kind="yt", force=True)
    assert plan["out"].name.endswith("_YT完整版.mp4")
    # cmd 包含 intro 和 outro
    assert any("intro" in str(a) for a in plan["cmd"])


def test_prepare_assembly_reels_skips_intro_outro(tmp_episode_full):
    """output_kind='reels' 時 ffmpeg cmd 不含 intro / outro 輸入。"""
    plan = prepare_assembly(tmp_episode_full, output_kind="reels", force=True)
    assert plan["out"].name.endswith("_Reels.mp4")
    # Reels cmd 只有 1 個 -i（main video），不含 intro/outro
    i_count = sum(1 for a in plan["cmd"] if a == "-i")
    assert i_count == 1
    # filter_complex 應該沒有 concat
    fc_idx = plan["cmd"].index("-filter_complex")
    assert "concat" not in plan["cmd"][fc_idx + 1]


def test_prepare_assembly_reels_uses_crop_reels(tmp_episode_full):
    """Reels 分支讀 cfg['crop_reels'] 而非 crop_yt。"""
    ep_yaml = tmp_episode_full / "episode.yaml"
    data = yaml.safe_load(ep_yaml.read_text(encoding="utf-8"))
    data["crop_yt"] = {"x": 0.1, "y": 0.0, "width": 0.8, "height": 1.0}
    data["crop_reels"] = {"x": 0.3, "y": 0.0, "width": 0.4, "height": 1.0}
    ep_yaml.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    plan = prepare_assembly(tmp_episode_full, output_kind="reels", force=True)
    fc_idx = plan["cmd"].index("-filter_complex")
    fc = plan["cmd"][fc_idx + 1]
    # Reels 解析度 1080x1920，crop_reels width=0.4 → 432px
    assert "crop=432:1920:324:0" in fc


def test_prepare_assembly_reels_resolution_1080x1920(tmp_episode_full):
    plan = prepare_assembly(tmp_episode_full, output_kind="reels", force=True)
    fc_idx = plan["cmd"].index("-filter_complex")
    fc = plan["cmd"][fc_idx + 1]
    assert "scale=1080:1920" in fc
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_assemble_filters.py -v`
Expected: FAIL（prepare_assembly 沒接 output_kind 參數）

- [ ] **Step 3: 拆 `build_filter_complex` 為 YT / Reels 兩版**

```python
# podcast_toolkit/assemble.py 把舊 build_filter_complex 改名為 build_filter_complex_yt
# 並新增 build_filter_complex_reels 與 dispatcher

def build_filter_complex_yt(
    cfg: dict,
    main_dur: float,
    srt_rel: str,
    deletion_intervals: list[tuple[float, float]] | None = None,
) -> str:
    """YT 16:9：原本的三段 concat（intro + main + outro card）。"""
    enc = cfg["encode"]
    res_w, res_h = enc["resolution"].split("x")
    intro_dur = cfg["assets"]["intro_duration"]
    intro_fade_out = cfg["assets"]["intro_fade_out"]
    style_str = build_style_string(cfg["subtitle_style"])

    crop = cfg.get("crop_yt")
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


def build_filter_complex_reels(
    cfg: dict,
    main_dur: float,
    srt_rel: str,
    deletion_intervals: list[tuple[float, float]] | None = None,
) -> str:
    """Reels 9:16：只有主影片，1080x1920，用 crop_reels。"""
    enc = cfg["encode"]
    res_w, res_h = 1080, 1920
    style_str = build_style_string(cfg["subtitle_style"])

    crop = cfg.get("crop_reels")
    crop_part = ""
    if crop:
        cw = int(res_w * crop["width"])
        ch = int(res_h * crop["height"])
        cx = int(res_w * crop["x"])
        cy = int(res_h * crop["y"])
        crop_part = f"crop={cw}:{ch}:{cx}:{cy},"

    select_v, select_a = "", ""
    if deletion_intervals:
        ranges = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in deletion_intervals)
        select_v = f"select='not({ranges})',setpts=N/FRAME_RATE/TB,"
        select_a = f"aselect='not({ranges})',asetpts=N/SR/TB,"

    return (
        f"[0:v]subtitles={srt_rel}:force_style='{style_str}',"
        f"scale={res_w}:{res_h},{crop_part}{select_v}setsar=1,"
        f"fps={enc['framerate']},format={enc['pix_fmt']},"
        f"fade=t=in:st=0:d=0.5,fade=t=out:st={main_dur - 0.5}:d=0.5[v];"
        f"[0:a]aformat=sample_rates={enc['audio_sample_rate']}:channel_layouts=stereo,"
        f"{select_a}afade=t=in:st=0:d=0.5,afade=t=out:st={main_dur - 0.5}:d=0.5[a]"
    )


# 保留舊名做相容呼叫
def build_filter_complex(cfg, main_dur, srt_rel, deletion_intervals=None):
    return build_filter_complex_yt(cfg, main_dur, srt_rel, deletion_intervals)
```

- [ ] **Step 4: 改 `prepare_assembly` 加 output_kind 分支**

```python
# podcast_toolkit/assemble.py:122 取代整個 prepare_assembly
def prepare_assembly(
    episode_dir: Path,
    output_kind: str = "yt",
    force: bool = False,
) -> dict:
    """檢查資產 → 算 ffmpeg cmd / cwd / out / 時長。

    output_kind = 'yt' 或 'reels'：
      - yt：1920x1080，含 intro + outro，用 crop_yt
      - reels：1080x1920，只含主影片，用 crop_reels
    """
    if output_kind not in ("yt", "reels"):
        raise AssembleError(f"未知 output_kind={output_kind}")

    if not shutil.which("ffmpeg"):
        raise AssembleError("找不到 ffmpeg，請 brew install ffmpeg")
    if not shutil.which("ffprobe"):
        raise AssembleError("找不到 ffprobe（隨 ffmpeg 安裝）")

    ep = Episode(episode_dir)
    cfg = ep.cfg

    main_video = ep.main_video()
    if not main_video.exists():
        raise AssembleError(f"找不到正片：{main_video}", exit_code=3)

    srt = ep.output_v2_srt()
    if not srt.exists():
        srt = ep.main_srt()
        if not srt.exists():
            raise AssembleError("找不到字幕（_v2 或原 srt）", exit_code=3)

    # 輸出路徑分支
    if output_kind == "yt":
        out = ep.output_yt_video()
    else:
        out = ep.output_reels_video()

    if out.exists() and not force:
        raise AssembleError(f"輸出已存在：{out}（加 --force 覆寫）", exit_code=1)

    # YT 需要片頭片尾資產
    if output_kind == "yt":
        intro = ep.asset_path("intro")
        outro_audio = ep.asset_path("outro_audio")
        outro_image = ep.asset_path("outro_image")
        for p, label in [(intro, "intro"), (outro_audio, "outro_audio"), (outro_image, "outro_image")]:
            if not p.exists():
                raise AssembleError(
                    f"共用資產缺失：{label} = {p}（跑 podcast relink {episode_dir} 試試）",
                    exit_code=3,
                )

    main_dur = ffprobe_duration(main_video)
    enc = cfg["encode"]

    cwd = ep.subdir("output")
    main_rel = str(main_video.relative_to(cwd)) if main_video.is_relative_to(cwd) else str(main_video)
    srt_rel = str(srt.relative_to(cwd)) if srt.is_relative_to(cwd) else str(srt)

    deletions = list(cfg.get("deletions") or [])
    deletion_intervals = build_deletion_intervals(srt, deletions) if deletions else []

    if deletions:
        clean_srt = ep.subdir("work") / f"_v2_assembled_{output_kind}.srt"
        filter_deletion_srt(srt, clean_srt, deletions)
        srt = clean_srt
        srt_rel = str(srt.relative_to(cwd)) if srt.is_relative_to(cwd) else str(srt)
        deleted_total = sum(b - a for a, b in deletion_intervals)
        main_dur = main_dur - deleted_total

    # tmp_out 寫在 work/，成功後 rename 到 out
    tmp_out = ep.subdir("work") / f".{out.name}.tmp"
    tmp_out_rel = str(tmp_out.relative_to(cwd)) if tmp_out.is_relative_to(cwd) else str(tmp_out)

    if output_kind == "yt":
        intro_dur = cfg["assets"]["intro_duration"]
        outro_dur = cfg["assets"]["outro_duration"]
        fc = build_filter_complex_yt(cfg, main_dur=main_dur, srt_rel=srt_rel,
                                     deletion_intervals=deletion_intervals)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(intro),
            "-i", main_rel,
            "-loop", "1", "-t", str(outro_dur), "-i", str(outro_image),
            "-i", str(outro_audio),
            "-filter_complex", fc,
            "-map", "[v]", "-map", "[a]",
            "-c:v", enc["video_codec"], "-crf", str(enc["crf"]),
            "-preset", enc["preset"], "-pix_fmt", enc["pix_fmt"],
            "-c:a", enc["audio_codec"], "-b:a", enc["audio_bitrate"],
            "-ar", str(enc["audio_sample_rate"]),
            "-movflags", "+faststart",
            tmp_out_rel,
        ]
        total_dur = intro_dur + main_dur + outro_dur
    else:
        fc = build_filter_complex_reels(cfg, main_dur=main_dur, srt_rel=srt_rel,
                                        deletion_intervals=deletion_intervals)
        cmd = [
            "ffmpeg", "-y",
            "-i", main_rel,
            "-filter_complex", fc,
            "-map", "[v]", "-map", "[a]",
            "-c:v", enc["video_codec"], "-crf", str(enc["crf"]),
            "-preset", enc["preset"], "-pix_fmt", enc["pix_fmt"],
            "-c:a", enc["audio_codec"], "-b:a", enc["audio_bitrate"],
            "-ar", str(enc["audio_sample_rate"]),
            "-movflags", "+faststart",
            tmp_out_rel,
        ]
        total_dur = main_dur

    return {
        "cmd": cmd,
        "cwd": cwd,
        "out": out,
        "tmp_out": tmp_out,
        "main_dur": main_dur,
        "total_dur": total_dur,
        "output_kind": output_kind,
    }
```

- [ ] **Step 5: 改 CLI `run()` 維持向後相容（預設 yt）**

```python
# podcast_toolkit/assemble.py:221 把 run() 改成
def run(episode_dir: Path, dry_run: bool = False, force: bool = False,
        output_kind: str = "yt") -> int:
    try:
        plan = prepare_assembly(episode_dir, output_kind=output_kind, force=force)
    except AssembleError as e:
        print(f"✗ {e}", file=sys.stderr)
        return e.exit_code
    # 後續維持原樣（dry_run 印 cmd / 否則 subprocess.run）
    # 但成功後要 rename tmp_out → out
    # ...（原本 subprocess.run 那段不動，加上後處理）
```

成功後加 rename（在原本 subprocess.run 成功之後）：

```python
    # 成功才把 tmp_out rename 到 out
    if plan["tmp_out"].exists():
        plan["tmp_out"].replace(plan["out"])
```

- [ ] **Step 6: 跑測試確認通過**

Run: `pytest tests/test_assemble_filters.py -v`
Expected: PASS（4 個新測試 + 既有 yt 測試）

- [ ] **Step 7: Commit**

```bash
git add podcast_toolkit/assemble.py tests/test_assemble_filters.py
git commit -m "feat: prepare_assembly 加 output_kind=yt/reels 分支

Reels 分支跳過 intro/outro，輸出 1080x1920、用 crop_reels；
filter_complex 拆成 build_filter_complex_yt / _reels 兩個函式。
tmp_out 路徑寫到 04_工作檔/.{name}_xxx.mp4.tmp，由呼叫端負責
rename 到 03_成品/（下一個 task 接手）。"
```

---

### Task 3: tmp+rename 原子寫入 assemble_job 接手

**Files:**
- Modify: `podcast_toolkit/web/assemble_job.py:92-139`
- Test: `tests/test_api_routes.py`

- [ ] **Step 1: 寫測試 `test_assemble_job_renames_on_success`**

```python
# tests/test_api_routes.py 加（用 mock subprocess 模擬 ffmpeg）
def test_pump_progress_renames_tmp_to_out_on_success(monkeypatch, tmp_path):
    """ffmpeg 結束碼 0 且 tmp_out 存在 → rename 到 out。"""
    from podcast_toolkit.web import assemble_job

    tmp_out = tmp_path / ".final.mp4.tmp"
    tmp_out.write_bytes(b"fake video bytes")
    out = tmp_path / "final.mp4"

    class FakeProc:
        stdout = iter(["progress=end\n"])
        stderr = type("S", (), {"read": lambda self: ""})()
        def wait(self): return 0

    assemble_job._pump_progress(FakeProc(), total_dur=10.0,
                                 out_path=out, tmp_out=tmp_out)
    assert out.exists()
    assert not tmp_out.exists()


def test_pump_progress_does_not_overwrite_on_failure(tmp_path):
    """ffmpeg 失敗 → tmp_out 砍掉、out 保持原狀。"""
    from podcast_toolkit.web import assemble_job

    out = tmp_path / "final.mp4"
    out.write_bytes(b"existing good output")
    tmp_out = tmp_path / ".final.mp4.tmp"
    tmp_out.write_bytes(b"half-baked output")

    class FakeProc:
        stdout = iter([])
        stderr = type("S", (), {"read": lambda self: "ffmpeg crashed"})()
        def wait(self): return 1

    assemble_job._pump_progress(FakeProc(), total_dur=10.0,
                                 out_path=out, tmp_out=tmp_out)
    assert out.read_bytes() == b"existing good output"
    assert not tmp_out.exists()  # tmp 要被清掉
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_api_routes.py::test_pump_progress_renames_tmp_to_out_on_success tests/test_api_routes.py::test_pump_progress_does_not_overwrite_on_failure -v`
Expected: FAIL（_pump_progress 沒接 tmp_out 參數）

- [ ] **Step 3: 改 `_pump_progress` 接 tmp_out**

```python
# podcast_toolkit/web/assemble_job.py:92 取代整個 _pump_progress
def _pump_progress(proc: Popen, total_dur: float, out_path: Path,
                   tmp_out: Path) -> None:
    """讀 ffmpeg -progress pipe:1，算 percent + ETA；成功 rename，失敗清 tmp。"""
    started = monotonic()
    last_out_time_us = 0

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key == "out_time_us":
                try:
                    last_out_time_us = int(value)
                except ValueError:
                    continue
                current_s = last_out_time_us / 1_000_000
                percent = min(100.0, (current_s / total_dur) * 100.0) if total_dur > 0 else 0.0
                elapsed = monotonic() - started
                if percent > 1.0:
                    eta_s = max(0, int(elapsed * (100.0 - percent) / percent))
                else:
                    eta_s = None
                _set(percent=percent, eta_s=eta_s)
            elif key == "progress" and value == "end":
                _set(percent=100.0, eta_s=0)
    except Exception as e:
        _set(error=f"讀取進度失敗：{e}")

    stderr_tail = ""
    try:
        assert proc.stderr is not None
        stderr_tail = proc.stderr.read() or ""
    except Exception:
        pass

    returncode = proc.wait()
    if returncode == 0 and tmp_out.exists():
        # 成功才覆寫舊輸出
        tmp_out.replace(out_path)
        _set(state="done", percent=100.0, eta_s=0)
    else:
        # 失敗清 tmp，保留舊 out
        try:
            if tmp_out.exists():
                tmp_out.unlink()
        except OSError:
            pass
        tail = "\n".join((stderr_tail or "").strip().splitlines()[-5:])
        _set(
            state="error",
            error=f"ffmpeg 結束碼 {returncode}：{tail}" if tail else f"ffmpeg 結束碼 {returncode}",
        )
```

- [ ] **Step 4: 改 `start_job` 把 tmp_out 傳給 thread（暫時保持單 job）**

```python
# podcast_toolkit/web/assemble_job.py:54 改 start_job
def start_job(ep: Episode, force: bool = False) -> dict[str, Any]:
    with _LOCK:
        if _STATE["state"] == "running":
            raise RuntimeError("已有合成正在進行中")

    plan = prepare_assembly(ep.dir, output_kind="yt", force=force)

    _reset(
        state="running",
        percent=0.0,
        eta_s=None,
        out_path=str(plan["out"]),
        started_at=monotonic(),
    )

    cmd = list(plan["cmd"]) + ["-progress", "pipe:1", "-nostats"]
    proc = Popen(cmd, cwd=plan["cwd"], stdout=PIPE, stderr=PIPE,
                 text=True, bufsize=1)

    t = threading.Thread(
        target=_pump_progress,
        args=(proc, plan["total_dur"], plan["out"], plan["tmp_out"]),
        daemon=True,
    )
    t.start()
    return {"out_path": str(plan["out"]), "total_dur": plan["total_dur"]}
```

- [ ] **Step 5: 跑測試確認通過**

Run: `pytest tests/test_api_routes.py -v -k pump_progress`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add podcast_toolkit/web/assemble_job.py tests/test_api_routes.py
git commit -m "feat: assemble job 用 tmp+rename 原子寫入

ffmpeg 寫到 04_工作檔/.{name}_xxx.mp4.tmp，returncode=0 才
rename 蓋掉成品；失敗清 tmp 保留舊輸出。避免上次 ffmpeg 中斷
把 _YT完整版.mp4 破壞成 0 bytes 的事故重演。"
```

---

### Task 4: assemble_job 加 queue 支援多 target 串行

**Files:**
- Modify: `podcast_toolkit/web/assemble_job.py:18-89`
- Test: `tests/test_api_routes.py`

- [ ] **Step 1: 寫測試 `test_start_job_accepts_targets_list`**

```python
# tests/test_api_routes.py 加
def test_start_job_with_two_targets_queues_both(monkeypatch, tmp_episode_full):
    """start_job 接 ['yt', 'reels'] → 兩個都進 queue。"""
    from podcast_toolkit.web import assemble_job
    from podcast_toolkit.episode import Episode

    spawned = []
    monkeypatch.setattr(assemble_job, "Popen",
                        lambda *a, **k: spawned.append(a[0]) or _FakeProc())
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)

    ep = Episode(tmp_episode_full)
    info = assemble_job.start_job(ep, targets=["yt", "reels"], force=True)

    state = assemble_job.get_status()
    assert state["queue"] == ["yt", "reels"]
    assert state["current"] == "yt"
    assert state["index"] == 0
    assert state["total"] == 2


def test_start_job_rejects_when_running(tmp_episode_full):
    from podcast_toolkit.web import assemble_job
    assemble_job._STATE["state"] = "running"
    try:
        with pytest.raises(RuntimeError, match="已有"):
            assemble_job.start_job(Episode(tmp_episode_full),
                                   targets=["yt"], force=True)
    finally:
        assemble_job._STATE["state"] = "idle"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_api_routes.py -v -k start_job`
Expected: FAIL（start_job 不接 targets 參數）

- [ ] **Step 3: _STATE shape 改成 queue 版本**

```python
# podcast_toolkit/web/assemble_job.py:18-46 取代
_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "state": "idle",           # idle | running | done | error
    "queue": [],               # 例如 ["yt", "reels"]
    "current": None,           # 目前在跑的 target
    "index": 0,                # 第幾個（0-based）
    "total": 0,                # queue 長度
    "percent": 0.0,
    "eta_s": None,
    "out_path": None,          # 目前這個 target 的輸出
    "output_files": [],        # 已完成的輸出路徑 list
    "error": None,
    "started_at": None,
}


def get_status() -> dict[str, Any]:
    with _LOCK:
        return dict(_STATE)


def _reset(**kwargs) -> None:
    with _LOCK:
        _STATE.update({
            "state": "idle",
            "queue": [],
            "current": None,
            "index": 0,
            "total": 0,
            "percent": 0.0,
            "eta_s": None,
            "out_path": None,
            "output_files": [],
            "error": None,
            "started_at": None,
        })
        _STATE.update(kwargs)


def _set(**kwargs) -> None:
    with _LOCK:
        _STATE.update(kwargs)
```

- [ ] **Step 4: 改 `start_job` 接 targets list + spawn coordinator thread**

```python
# podcast_toolkit/web/assemble_job.py:54 取代 start_job
def start_job(ep: Episode, targets: list[str], force: bool = False) -> dict[str, Any]:
    """開新 job；targets 例如 ['yt', 'reels']。"""
    if not targets:
        raise ValueError("targets 不能為空")
    for t in targets:
        if t not in ("yt", "reels"):
            raise ValueError(f"未知 target={t}")

    with _LOCK:
        if _STATE["state"] == "running":
            raise RuntimeError("已有合成正在進行中")

    # 預先檢查所有 target：任一失敗就整批拒絕（不要跑一半才報錯）
    plans = []
    for t in targets:
        plans.append(prepare_assembly(ep.dir, output_kind=t, force=force))

    _reset(
        state="running",
        queue=list(targets),
        current=targets[0],
        index=0,
        total=len(targets),
        percent=0.0,
        eta_s=None,
        out_path=str(plans[0]["out"]),
        output_files=[],
        started_at=monotonic(),
    )

    coordinator = threading.Thread(
        target=_run_queue,
        args=(plans,),
        daemon=True,
    )
    coordinator.start()

    return {
        "targets": list(targets),
        "out_paths": [str(p["out"]) for p in plans],
    }


def _run_queue(plans: list[dict]) -> None:
    """coordinator：依序跑 plans，任一失敗就停止後續。"""
    for i, plan in enumerate(plans):
        _set(
            current=plan["output_kind"],
            index=i,
            out_path=str(plan["out"]),
            percent=0.0,
            eta_s=None,
        )
        cmd = list(plan["cmd"]) + ["-progress", "pipe:1", "-nostats"]
        proc = Popen(cmd, cwd=plan["cwd"], stdout=PIPE, stderr=PIPE,
                     text=True, bufsize=1)
        _pump_progress(proc, plan["total_dur"], plan["out"], plan["tmp_out"])

        # _pump_progress 內部會 set state=done 或 error
        with _LOCK:
            cur_state = _STATE["state"]
        if cur_state == "error":
            return  # 中止後續
        # 把成功的輸出加進 output_files
        with _LOCK:
            _STATE["output_files"].append(str(plan["out"]))
            if i < len(plans) - 1:
                # 還有下一個 → 維持 running
                _STATE["state"] = "running"
    # 全部跑完
    _set(state="done", percent=100.0, eta_s=0)
```

- [ ] **Step 5: 跑測試確認通過**

Run: `pytest tests/test_api_routes.py -v -k start_job`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add podcast_toolkit/web/assemble_job.py tests/test_api_routes.py
git commit -m "feat: assemble job 改 queue 跑多 target 串行

start_job(ep, targets=['yt', 'reels']) 開 coordinator thread
依序跑兩個輸出；任一失敗中止後續、output_files 記錄已完成。
_STATE 加 queue/current/index/total/output_files 給前端 poll。"
```

---

### Task 5: api.py 接 targets + episode_io 存兩個 crop

**Files:**
- Modify: `podcast_toolkit/web/api.py:318-335`
- Modify: `podcast_toolkit/web/episode_io.py:12-67`
- Test: `tests/test_api_routes.py`
- Test: `tests/test_episode_io.py`

- [ ] **Step 1: 寫測試 `test_assemble_endpoint_accepts_targets`**

```python
# tests/test_api_routes.py 加
def test_assemble_endpoint_requires_targets(client):
    r = client.post("/api/assemble", json={"force": True})
    assert r.status_code == 400
    assert "targets" in r.json()["detail"]


def test_assemble_endpoint_with_yt_reels(client, monkeypatch):
    from podcast_toolkit.web import assemble_job
    monkeypatch.setattr(assemble_job, "start_job",
                        lambda ep, targets, force: {
                            "targets": targets,
                            "out_paths": [f"/fake/{t}.mp4" for t in targets],
                        })
    r = client.post("/api/assemble",
                    json={"targets": ["yt", "reels"], "force": True})
    assert r.status_code == 200
    assert r.json()["targets"] == ["yt", "reels"]
    assert len(r.json()["out_paths"]) == 2


def test_episode_io_load_returns_crop_yt_and_reels(tmp_episode_with_crops):
    from podcast_toolkit.web import episode_io
    from podcast_toolkit.episode import Episode
    ep = Episode(tmp_episode_with_crops)
    state = episode_io.load_state(ep)
    assert "crop_yt" in state
    assert "crop_reels" in state
    assert "crop" not in state  # 舊欄位不再透出


def test_episode_io_save_writes_both_crops(tmp_episode_full):
    from podcast_toolkit.web import episode_io
    from podcast_toolkit.episode import Episode
    import yaml
    ep = Episode(tmp_episode_full)
    episode_io.save_state(ep, {
        "crop_yt": {"x": 0.1, "y": 0.0, "width": 0.8, "height": 1.0},
        "crop_reels": {"x": 0.3, "y": 0.0, "width": 0.4, "height": 1.0},
        "deletions": [],
        "cards": [],
    })
    data = yaml.safe_load((tmp_episode_full / "episode.yaml").read_text(encoding="utf-8"))
    assert data["crop_yt"]["width"] == 0.8
    assert data["crop_reels"]["width"] == 0.4
    assert "crop" not in data  # 舊欄位被清掉
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_api_routes.py tests/test_episode_io.py -v -k "targets or crop_yt or crop_reels"`
Expected: FAIL

- [ ] **Step 3: 改 `/api/assemble` 接 targets**

```python
# podcast_toolkit/web/api.py:318 取代 post_assemble
@app.post("/api/assemble")
def post_assemble(payload: dict):
    ep = holder["ep"]
    targets = payload.get("targets") or []
    if not targets or not isinstance(targets, list):
        raise HTTPException(status_code=400, detail="缺少 targets（list，例如 ['yt', 'reels']）")
    force = bool(payload.get("force"))
    try:
        info = assemble_job.start_job(ep, targets=targets, force=force)
    except AssembleError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse({
        "ok": True,
        "targets": info["targets"],
        "out_paths": info["out_paths"],
    })
```

- [ ] **Step 4: 改 `episode_io.load_state` 回 crop_yt / crop_reels**

```python
# podcast_toolkit/web/episode_io.py:12 取代 load_state
def load_state(ep: Episode) -> dict[str, Any]:
    v2 = ep.output_v2_srt()
    if not v2.exists():
        raise FileNotFoundError(f"找不到 _v2.srt：{v2}（請先跑 podcast resegment）")
    cards = srt_io.parse(v2.read_text(encoding="utf-8"))
    return {
        "name": ep.name,
        "crop_yt": ep.cfg.get("crop_yt"),
        "crop_reels": ep.cfg.get("crop_reels"),
        "deletions": list(ep.cfg.get("deletions") or []),
        "cards": cards,
    }
```

- [ ] **Step 5: 改 `episode_io.save_state` 寫回兩個 crop + 清掉舊 crop**

```python
# podcast_toolkit/web/episode_io.py:26 取代 save_state 的 crop 段
def save_state(ep: Episode, payload: dict[str, Any]) -> None:
    yaml_path = ep.dir / "episode.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}

    # 清掉舊欄位（一次性遷移）
    data.pop("crop", None)

    for key in ("crop_yt", "crop_reels"):
        crop = payload.get(key)
        if crop:
            data[key] = {
                "x": float(crop["x"]),
                "y": float(crop["y"]),
                "width": float(crop["width"]),
                "height": float(crop["height"]),
            }
        else:
            data.pop(key, None)

    deletions = list(payload.get("deletions") or [])
    if deletions:
        data["deletions"] = [int(i) for i in deletions]
    else:
        data.pop("deletions", None)

    yaml_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    v2 = ep.output_v2_srt()
    original = v2.read_text(encoding="utf-8")
    backup = v2.with_suffix(v2.suffix + ".bak")
    backup.write_text(original, encoding="utf-8")

    cards = srt_io.parse(original)
    overrides = {
        int(c["idx"]): c["text"]
        for c in (payload.get("cards") or [])
        if c.get("text")
    }
    v2.write_text(srt_io.serialize(cards, overrides=overrides), encoding="utf-8")
```

- [ ] **Step 6: 跑測試確認通過**

Run: `pytest tests/test_api_routes.py tests/test_episode_io.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add podcast_toolkit/web/api.py podcast_toolkit/web/episode_io.py tests/
git commit -m "feat: /api/assemble 接 targets list + episode_io 存兩個 crop

POST /api/assemble body 改成 {targets: ['yt'|'reels'], force}；
load_state 回 crop_yt/crop_reels（舊 crop 欄位不透出）；
save_state 寫回兩欄並清掉舊 crop（一次性遷移）。"
```

---

# Phase 2：Frontend 版本切換 UI

---

### Task 6: index.html 加版本 tab + assemble-modal checkbox

**Files:**
- Modify: `podcast_toolkit/web/static/index.html`

- [ ] **Step 1: video-wrap 上方加版本 tab**

```html
<!-- 取代 index.html 大概 line 25-37 的 <div class="video-wrap"> 區塊 -->
<div class="video-area">
  <div class="version-tabs" id="version-tabs">
    <button class="version-tab active" data-version="yt" type="button">
      YT 16:9 完整版
    </button>
    <button class="version-tab" data-version="reels" type="button">
      Reels 9:16 短版
    </button>
  </div>
  <div class="video-wrap" id="video-wrap">
    <video id="video" controls preload="metadata"></video>
    <div class="crop-frame" id="crop-frame">
      <div class="crop-handle" data-handle="nw"></div>
      <div class="crop-handle" data-handle="ne"></div>
      <div class="crop-handle" data-handle="sw"></div>
      <div class="crop-handle" data-handle="se"></div>
    </div>
  </div>
  <div class="crop-controls">
    <span class="crop-label" id="crop-label">YT 比例（16:9）</span>
    <button id="crop-reset" type="button">重置 crop</button>
  </div>
</div>
```

- [ ] **Step 2: assemble-modal 加 checkbox**

```html
<!-- 找到 assemble-modal 內容（大概 line 145-165）並改成： -->
<div id="assemble-modal" class="modal hidden">
  <div class="modal-content">
    <h2>合成輸出</h2>
    <p>選擇要合成的版本：</p>
    <label class="checkbox-row">
      <input type="checkbox" id="assemble-yt" checked />
      <span>YT 16:9 完整版（含片頭片尾）</span>
    </label>
    <label class="checkbox-row">
      <input type="checkbox" id="assemble-reels" />
      <span>Reels 9:16 短版（無片頭片尾）</span>
    </label>
    <label class="checkbox-row">
      <input type="checkbox" id="assemble-force" />
      <span>覆寫既有檔案（--force）</span>
    </label>
    <div class="modal-actions">
      <button id="assemble-cancel" type="button">取消</button>
      <button id="assemble-confirm" type="button" class="primary">開始合成</button>
    </div>
    <div id="assemble-progress" class="hidden">
      <div class="progress-line">
        <span id="assemble-current-label">準備中…</span>
        <span id="assemble-eta"></span>
      </div>
      <div class="progress-bar">
        <div class="progress-fill" id="assemble-fill"></div>
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 3: 開瀏覽器確認版面**

Run: 跑 server 並用 `/browse` 打開 http://127.0.0.1:PORT，截圖確認 tab 出現、checkbox 出現。
Expected: 看到兩個 tab、三個 checkbox

- [ ] **Step 4: Commit**

```bash
git add podcast_toolkit/web/static/index.html
git commit -m "feat: 影片區加版本 tab + 合成 modal 加 checkbox

YT/Reels 兩個 tab 切換顯示哪個版本的 crop；合成 modal 改成
checkbox 讓使用者勾選要輸出 YT、Reels、或兩個都跑。預設勾 YT。"
```

---

### Task 7: app.js state 拆 cropYt/cropReels + tab 切換 + per-version drag

**Files:**
- Modify: `podcast_toolkit/web/static/app.js`（state、cropForRatio、drag 處理、tab 切換）

- [ ] **Step 1: state 加 activeVersion + 拆 crop 為 cropYt/cropReels**

```javascript
// app.js 找到 state 物件（大概在最上方），改成：
const state = {
  episode: null,
  cards: [],
  deletions: new Set(),
  activeVersion: "yt",   // "yt" | "reels"
  cropYt: null,          // {x, y, width, height}（比例）
  cropReels: null,
  cropRatioYt: null,     // 顯示用
  cropRatioReels: null,
  files: [],
  assembleState: null,   // poll 結果快取
};
```

- [ ] **Step 2: 加 helper `getActiveCrop()` / `setActiveCrop()`**

```javascript
function getActiveCrop() {
  return state.activeVersion === "yt" ? state.cropYt : state.cropReels;
}

function setActiveCrop(crop) {
  if (state.activeVersion === "yt") {
    state.cropYt = crop;
  } else {
    state.cropReels = crop;
  }
}

function getActiveVersionAspect() {
  // YT 16:9，Reels 9:16；用來限制 crop-frame 的比例（後續 redrawCropFrame 用）
  return state.activeVersion === "yt" ? 16 / 9 : 9 / 16;
}
```

- [ ] **Step 3: 改 `loadEpisode()` 把 cropYt/cropReels 從 API 寫進 state**

```javascript
// 找到 fetch('/api/episode') 那段，response 解析時改：
async function loadEpisode() {
  const res = await fetch("/api/episode");
  if (!res.ok) throw new Error(`/api/episode ${res.status}`);
  const data = await res.json();
  state.episode = data;
  state.cards = data.cards || [];
  state.deletions = new Set(data.deletions || []);
  state.cropYt = data.crop_yt || null;
  state.cropReels = data.crop_reels || null;
  renderAll();
}
```

- [ ] **Step 4: 加 tab 切換 handler**

```javascript
function setupVersionTabs() {
  document.querySelectorAll(".version-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      const v = btn.dataset.version;
      if (v === state.activeVersion) return;
      state.activeVersion = v;
      document.querySelectorAll(".version-tab").forEach((b) => {
        b.classList.toggle("active", b.dataset.version === v);
      });
      const label = document.getElementById("crop-label");
      label.textContent = v === "yt" ? "YT 比例（16:9）" : "Reels 比例（9:16）";
      redrawCropFrame();   // 重畫 crop-frame 顯示 active 版本的 crop
    });
  });
}
// init 階段呼叫 setupVersionTabs()
```

- [ ] **Step 5: drag handler 改成寫入 active 版本的 crop**

```javascript
// 找到原本 drag 結束時設定 state.crop 的程式碼，改成：
// 例如 onDragEnd 內：
function onDragEnd() {
  const crop = computeCropFromFrame();   // 既有函式
  setActiveCrop(crop);
  // ...其他 redraw 邏輯
}
```

- [ ] **Step 6: `redrawCropFrame()` 用 active 版本的 crop**

```javascript
function redrawCropFrame() {
  const crop = getActiveCrop();
  const frame = document.getElementById("crop-frame");
  if (!crop) {
    frame.classList.add("hidden");
    return;
  }
  frame.classList.remove("hidden");
  // 套用 crop 比例到 frame style
  frame.style.left = `${crop.x * 100}%`;
  frame.style.top = `${crop.y * 100}%`;
  frame.style.width = `${crop.width * 100}%`;
  frame.style.height = `${crop.height * 100}%`;
}
```

- [ ] **Step 7: `cropResetBtn` 改清 active 版本**

```javascript
document.getElementById("crop-reset").addEventListener("click", () => {
  setActiveCrop(null);
  redrawCropFrame();
});
```

- [ ] **Step 8: `saveState()` POST 改傳兩個 crop**

```javascript
async function saveState() {
  const body = {
    crop_yt: state.cropYt,
    crop_reels: state.cropReels,
    deletions: Array.from(state.deletions),
    cards: state.cards.filter((c) => c.dirty).map((c) => ({
      idx: c.idx, text: c.text,
    })),
  };
  const res = await fetch("/api/episode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`save ${res.status}`);
}
```

- [ ] **Step 9: 跑 server 用瀏覽器手動驗證**

Run: 跑 server，點 YT tab 拖 crop → 切到 Reels tab → 應該看到 crop 不見（Reels 還沒設）→ 在 Reels 拖一個 → 切回 YT 看到 YT 的還在。
Expected: 兩個版本的 crop 互不影響

- [ ] **Step 10: Commit**

```bash
git add podcast_toolkit/web/static/app.js
git commit -m "feat: app.js state 拆 cropYt/cropReels 跟版本 tab 互動

activeVersion 控制 crop-frame 顯示誰；切 tab 重畫 frame；
drag/reset 寫入 active 版本；saveState 把兩份 crop 一起送回。"
```

---

### Task 8: app.js assemble 流程改 POST targets + 渲染 queue 進度

**Files:**
- Modify: `podcast_toolkit/web/static/app.js`（合成按鈕、modal、pollAssemble）

- [ ] **Step 1: 「合成」按鈕改開 modal 不直接 POST**

```javascript
function setupAssembleButton() {
  document.getElementById("assemble-btn").addEventListener("click", () => {
    document.getElementById("assemble-modal").classList.remove("hidden");
    document.getElementById("assemble-progress").classList.add("hidden");
  });
  document.getElementById("assemble-cancel").addEventListener("click", () => {
    document.getElementById("assemble-modal").classList.add("hidden");
  });
  document.getElementById("assemble-confirm").addEventListener("click", onAssembleConfirm);
}
```

- [ ] **Step 2: `onAssembleConfirm()` 收集 targets + POST**

```javascript
async function onAssembleConfirm() {
  const targets = [];
  if (document.getElementById("assemble-yt").checked) targets.push("yt");
  if (document.getElementById("assemble-reels").checked) targets.push("reels");
  if (targets.length === 0) {
    alert("至少要勾一個版本");
    return;
  }
  const force = document.getElementById("assemble-force").checked;

  // 先存目前的 crop / deletions
  try { await saveState(); } catch (e) { /* 已存過就略過 */ }

  const res = await fetch("/api/assemble", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ targets, force }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(`合成失敗：${err.detail || res.status}`);
    return;
  }
  document.getElementById("assemble-progress").classList.remove("hidden");
  pollAssembleStatus();
}
```

- [ ] **Step 3: 改 `pollAssembleStatus()` 渲染 queue 資訊**

```javascript
async function pollAssembleStatus() {
  const tick = async () => {
    const res = await fetch("/api/assemble/status");
    const s = await res.json();
    state.assembleState = s;

    const label = document.getElementById("assemble-current-label");
    const eta = document.getElementById("assemble-eta");
    const fill = document.getElementById("assemble-fill");

    if (s.state === "idle") {
      return;
    }
    if (s.state === "running") {
      const prefix = s.total > 1
        ? `[${s.index + 1}/${s.total}] ${s.current === "yt" ? "YT" : "Reels"} 合成中…`
        : `${s.current === "yt" ? "YT" : "Reels"} 合成中…`;
      label.textContent = `${prefix} ${s.percent.toFixed(1)}%`;
      eta.textContent = s.eta_s != null ? `剩 ~${Math.round(s.eta_s)}s` : "";
      fill.style.width = `${s.percent}%`;
      setTimeout(tick, 1000);
    } else if (s.state === "done") {
      label.textContent = `完成（${s.output_files.length} 個檔案）`;
      eta.textContent = "";
      fill.style.width = "100%";
      // 重新讀檔案列表
      await loadFiles();
      renderFiles();
    } else if (s.state === "error") {
      label.textContent = `失敗：${s.error}`;
      eta.textContent = "";
    }
  };
  tick();
}
```

- [ ] **Step 4: 跑 server 手動驗證**

Run: 跑 server，點合成 → 勾兩個 → 確認 → 看到 `[1/2] YT 合成中…` `[2/2] Reels 合成中…`
Expected: queue 進度文字正確切換

- [ ] **Step 5: Commit**

```bash
git add podcast_toolkit/web/static/app.js
git commit -m "feat: 合成流程改走 modal 收 targets + 渲染 queue 進度

modal 勾選 YT/Reels/force → POST /api/assemble {targets, force}；
pollAssembleStatus 用 index/total/current 顯示 [1/2] YT 合成中。"
```

---

### Task 9: app.css 加 tab、checkbox、queue 進度樣式

**Files:**
- Modify: `podcast_toolkit/web/static/app.css`

- [ ] **Step 1: 加 version-tabs 樣式**

```css
/* app.css 末尾追加 */
.version-tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 8px;
}

.version-tab {
  flex: 1;
  padding: 8px 12px;
  background: #2a2a2a;
  color: #aaa;
  border: 1px solid #444;
  border-radius: 4px 4px 0 0;
  cursor: pointer;
  font-size: 14px;
}

.version-tab.active {
  background: #1e88e5;
  color: #fff;
  border-color: #1e88e5;
  font-weight: 600;
}

.crop-label {
  color: #888;
  font-size: 13px;
  margin-right: 12px;
}
```

- [ ] **Step 2: 加 checkbox-row 樣式**

```css
.checkbox-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 8px 0;
  cursor: pointer;
  user-select: none;
}

.checkbox-row input[type="checkbox"] {
  width: 18px;
  height: 18px;
}
```

- [ ] **Step 3: 加 progress 樣式**

```css
.progress-line {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  margin: 12px 0 6px;
}

.progress-bar {
  background: #333;
  border-radius: 3px;
  height: 8px;
  overflow: hidden;
}

.progress-fill {
  background: #1e88e5;
  height: 100%;
  width: 0;
  transition: width 0.4s ease;
}
```

- [ ] **Step 4: 跑 server 手動驗證樣式**

Run: 截圖比對 tab 有 active 高亮、checkbox 對齊、progress bar 寬度動畫平滑。
Expected: UI 視覺正常

- [ ] **Step 5: Commit**

```bash
git add podcast_toolkit/web/static/app.css
git commit -m "feat: version tabs / checkbox / progress bar 樣式"
```

---

# Phase 3：檔案分類

---

### Task 10: api.py `_list_episode_files` 加 kind 標籤

**Files:**
- Modify: `podcast_toolkit/web/api.py:77-97`
- Test: `tests/test_api_routes.py`

- [ ] **Step 1: 寫測試 `test_list_episode_files_with_kind`**

```python
# tests/test_api_routes.py 新增
def test_list_episode_files_classifies_by_kind(tmp_episode_full):
    from podcast_toolkit.web.api import _list_episode_files
    files = _list_episode_files(tmp_episode_full)
    by_path = {f["path"]: f for f in files}

    # 主影片
    assert by_path.get(f"03_成品/{NAME}_final.mp4", {}).get("kind") == "main_video"
    # 主字幕（_v2 active）
    v2 = by_path.get(f"03_成品/{NAME}_final_v2.srt")
    assert v2["kind"] == "subtitle"
    assert v2["is_active_srt"] is True
    # 原始字幕（is_main_srt_backup）
    raw = by_path.get(f"03_成品/{NAME}_final.srt")
    assert raw["kind"] == "subtitle"
    assert raw["is_main_srt_backup"] is True
    # 合成輸出
    yt = by_path.get(f"03_成品/{NAME}_YT完整版.mp4")
    assert yt["kind"] == "composite"
    # 片頭片尾
    assert by_path.get("02_片頭片尾/intro.mp4", {}).get("kind") == "intro_outro"
    # 母帶
    assert by_path.get(f"01_母帶/track1.wav", {}).get("kind") == "master"
    # 工作檔
    assert by_path.get("04_工作檔/switch_list.json", {}).get("kind") == "work"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_api_routes.py -v -k list_episode_files`
Expected: FAIL（kind 不存在）

- [ ] **Step 3: 改 `_list_episode_files`**

```python
# podcast_toolkit/web/api.py:77 取代
def _list_episode_files(root: Path) -> list[dict]:
    """遞迴列出集資料夾內所有檔案，標註 kind / 字幕角色。"""
    files: list[dict] = []
    # 從 episode.yaml 拿 name 做主影片 / 主字幕識別
    try:
        from podcast_toolkit.episode import Episode
        ep = Episode(root)
        ep_name = ep.name
        main_video_path = ep.main_video()
        main_srt_path = ep.main_srt()
        v2_srt_path = ep.output_v2_srt()
        yt_out = ep.output_yt_video()
        reels_out = ep.output_reels_video()
    except Exception:
        ep_name = None
        main_video_path = main_srt_path = v2_srt_path = None
        yt_out = reels_out = None

    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS or part.startswith(".") for part in p.relative_to(root).parts):
            continue
        rel = str(p.relative_to(root))
        try:
            size = p.stat().st_size
        except OSError:
            size = 0

        # kind 分類
        first = p.relative_to(root).parts[0] if p.relative_to(root).parts else ""
        kind = "other"
        is_active_srt = False
        is_main_srt_backup = False

        if main_video_path and p == main_video_path:
            kind = "main_video"
        elif v2_srt_path and p == v2_srt_path:
            kind = "subtitle"
            is_active_srt = True
        elif main_srt_path and p == main_srt_path:
            kind = "subtitle"
            is_main_srt_backup = True
        elif yt_out and p == yt_out:
            kind = "composite"
        elif reels_out and p == reels_out:
            kind = "composite"
        elif p.suffix.lower() == ".srt":
            kind = "subtitle"
        elif first == "01_母帶":
            kind = "master"
        elif first == "02_片頭片尾":
            kind = "intro_outro"
        elif first == "04_工作檔":
            kind = "work"
        elif first == "03_成品":
            # 03_成品 但沒命中上面任何規則 → 視為 composite（其他輸出）
            kind = "composite"

        files.append({
            "path": rel,
            "size": size,
            "transcribable": p.suffix.lower() in TRANSCRIBABLE_EXTS,
            "previewable": p.suffix.lower() in PREVIEWABLE_EXTS,
            "kind": kind,
            "is_active_srt": is_active_srt,
            "is_main_srt_backup": is_main_srt_backup,
        })
    return files
```

- [ ] **Step 4: 跑測試確認通過**

Run: `pytest tests/test_api_routes.py -v -k list_episode_files`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add podcast_toolkit/web/api.py tests/test_api_routes.py
git commit -m "feat: _list_episode_files 加 kind 分類 + 字幕角色標籤

每個檔案多回 kind（main_video/subtitle/composite/intro_outro/
master/work/other）+ is_active_srt + is_main_srt_backup，
讓前端可以分區顯示。"
```

---

### Task 11: app.js renderFiles 改成六大區塊 group-by + localStorage 折疊

**Files:**
- Modify: `podcast_toolkit/web/static/app.js:613-676`（renderFiles）
- Modify: `podcast_toolkit/web/static/app.css`

- [ ] **Step 1: 加 kind label 表 + section 順序**

```javascript
// app.js 加常數
const FILE_SECTIONS = [
  { kind: "main_video",  label: "主影片",         icon: "🎬" },
  { kind: "subtitle",    label: "字幕",           icon: "💬" },
  { kind: "composite",   label: "合成輸出",       icon: "📦" },
  { kind: "intro_outro", label: "片頭片尾",       icon: "🎵" },
  { kind: "master",      label: "母帶",           icon: "🎙️" },
  { kind: "work",        label: "工作檔",         icon: "🛠️" },
  { kind: "other",       label: "其他",           icon: "📄" },
];

const COLLAPSE_KEY = "podcast-edit-collapsed-sections";

function loadCollapsedSections() {
  try {
    return new Set(JSON.parse(localStorage.getItem(COLLAPSE_KEY) || "[]"));
  } catch (e) {
    return new Set();
  }
}

function saveCollapsedSections(set) {
  localStorage.setItem(COLLAPSE_KEY, JSON.stringify(Array.from(set)));
}
```

- [ ] **Step 2: `renderFiles()` 改成 group-by**

```javascript
// 取代原本 renderFiles
function renderFiles() {
  const container = document.getElementById("files-list");
  container.innerHTML = "";

  const groups = new Map();
  for (const f of state.files) {
    if (!groups.has(f.kind)) groups.set(f.kind, []);
    groups.get(f.kind).push(f);
  }

  const collapsed = loadCollapsedSections();

  for (const section of FILE_SECTIONS) {
    const items = groups.get(section.kind) || [];
    if (items.length === 0) continue;

    const wrap = document.createElement("section");
    wrap.className = "file-section";
    wrap.dataset.kind = section.kind;

    const header = document.createElement("header");
    header.className = "file-section-header";
    const isCollapsed = collapsed.has(section.kind);
    header.innerHTML = `
      <span class="caret">${isCollapsed ? "▶" : "▼"}</span>
      <span class="section-icon">${section.icon}</span>
      <span class="section-label">${section.label}</span>
      <span class="section-count">${items.length}</span>
    `;
    header.addEventListener("click", () => {
      const cur = loadCollapsedSections();
      if (cur.has(section.kind)) cur.delete(section.kind);
      else cur.add(section.kind);
      saveCollapsedSections(cur);
      renderFiles();
    });
    wrap.appendChild(header);

    const list = document.createElement("ul");
    list.className = "file-list" + (isCollapsed ? " hidden" : "");
    for (const f of items) {
      const li = document.createElement("li");
      li.className = "file-row";
      // 字幕角色 badge
      let badges = "";
      if (f.is_active_srt) badges += `<span class="badge active">使用中</span>`;
      if (f.is_main_srt_backup) badges += `<span class="badge muted">原始備份</span>`;
      li.innerHTML = `
        <span class="file-path">${escapeHtml(f.path)}</span>
        ${badges}
        <span class="file-size">${formatSize(f.size)}</span>
      `;
      list.appendChild(li);
    }
    wrap.appendChild(list);
    container.appendChild(wrap);
  }
}

function escapeHtml(s) {
  return s.replace(/[&<>"]/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
  }[c]));
}

function formatSize(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
```

- [ ] **Step 3: app.css 加分區樣式**

```css
.file-section {
  margin-bottom: 12px;
  border: 1px solid #333;
  border-radius: 4px;
  overflow: hidden;
}

.file-section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #2a2a2a;
  cursor: pointer;
  user-select: none;
}

.file-section-header:hover { background: #333; }

.caret { font-size: 10px; color: #888; width: 12px; }
.section-icon { font-size: 16px; }
.section-label { font-weight: 600; flex: 1; }
.section-count {
  background: #444;
  color: #ccc;
  font-size: 12px;
  padding: 2px 6px;
  border-radius: 8px;
}

.file-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.file-list.hidden { display: none; }

.file-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  font-size: 13px;
  border-top: 1px solid #2a2a2a;
}

.file-row:hover { background: #252525; }

.file-path { flex: 1; font-family: monospace; }
.file-size { color: #888; font-size: 12px; }

.badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 8px;
}

.badge.active { background: #1e88e5; color: #fff; }
.badge.muted { background: #444; color: #aaa; }
```

- [ ] **Step 4: 跑 server 手動驗證**

Run: 跑 server，看檔案列表分成 6 區、可折疊、字幕區「使用中」badge 出現在 _v2.srt 上、refresh 後折疊狀態還在。
Expected: 6 個區塊正確 + 折疊持久化

- [ ] **Step 5: Commit**

```bash
git add podcast_toolkit/web/static/app.js podcast_toolkit/web/static/app.css
git commit -m "feat: 檔案列表 group-by 六大區塊 + localStorage 折疊狀態

renderFiles 用 backend 給的 kind 分主影片/字幕/合成/片頭片尾/
母帶/工作檔/其他七區，每區可折疊；字幕區顯示「使用中」「原始備份」
badge。折疊狀態存 localStorage 重新整理後保留。"
```

---

# Phase 4：E2E 驗證

---

### Task 12: /browse E2E 真實一集走兩版本完整流程

**Files:**
- Manual：`/browse` 加上真實 episode 目錄

- [ ] **Step 1: 起 server 指向真實 podcast 集**

```bash
cd /Users/vincentsia/Desktop/vibe-coding\ playground/podcast-toolkit
python -m podcast_toolkit.web "/Users/vincentsia/Desktop/vibe-coding playground/podcast剪輯"
```
Expected: 印出 port，可以打開瀏覽器

- [ ] **Step 2: /browse 開瀏覽器，截圖驗證 tab + 檔案分區**

用 `/browse` skill：navigate → snapshot → 確認看到 YT/Reels 兩 tab、檔案列表分成六區。

- [ ] **Step 3: YT tab 拉一個 crop → 切到 Reels tab → 拉另一個 crop**

`/browse` click → drag → 確認 crop-frame 切版本時值不互相覆蓋。

- [ ] **Step 4: 開合成 modal 勾兩個 → 確認**

`/browse` click 合成 → 勾 YT + Reels + force → 點確認 → 觀察 `[1/2] YT 合成中…` → `[2/2] Reels 合成中…`

- [ ] **Step 5: 驗證 03_成品/ 有 2 個輸出檔**

```bash
ls -la "/Users/vincentsia/Desktop/vibe-coding playground/podcast剪輯/03_成品/" | grep -E "(YT完整版|Reels)\.mp4"
```
Expected:
- `{name}_YT完整版.mp4`（非 0 bytes）
- `{name}_Reels.mp4`（非 0 bytes）

- [ ] **Step 6: ffprobe 驗證解析度**

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=width,height \
  -of csv=p=0 "/Users/vincentsia/Desktop/vibe-coding playground/podcast剪輯/03_成品/過嗨乳牛_YT完整版.mp4"
# Expected: 1920,1080

ffprobe -v error -select_streams v:0 -show_entries stream=width,height \
  -of csv=p=0 "/Users/vincentsia/Desktop/vibe-coding playground/podcast剪輯/03_成品/過嗨乳牛_Reels.mp4"
# Expected: 1080,1920
```

- [ ] **Step 7: 模擬中斷確認 tmp+rename 保護**

```bash
# 用 force=false 跑 Reels 應該失敗（已存在）
curl -X POST http://127.0.0.1:PORT/api/assemble \
  -H "Content-Type: application/json" \
  -d '{"targets":["reels"],"force":false}'
# Expected: 400 "輸出已存在"

# Reels 檔案大小應該不變
```

- [ ] **Step 8: 寫驗證報告 + Commit 任何發現的最後修正**

把截圖、ffprobe 輸出貼到 commit message。

```bash
git add -A
git commit -m "test: E2E 驗收多版本輸出 + 檔案分類 UI

驗證證據：
- YT 1920x1080、Reels 1080x1920 兩個輸出檔大小正常
- queue 進度 [1/2] → [2/2] 正確切換
- 檔案分區六區渲染、折疊狀態 localStorage 持久化
- tmp+rename 保護驗證：already-exists 失敗時舊輸出不變"
```

---

## 驗收標準（全綠才算完成）

- [ ] 所有 12 個 task 的 commit 都成立
- [ ] `pytest tests/ -v` 全綠
- [ ] `/browse` 截圖：兩 tab + 六分區 + queue 進度顯示正常
- [ ] `03_成品/` 真實生成兩個非 0 bytes 的輸出檔
- [ ] ffprobe 證實 1920×1080 + 1080×1920
- [ ] 舊 episode.yaml（只有 `crop` 欄位）載入後自動視為 `crop_yt`、不報錯
