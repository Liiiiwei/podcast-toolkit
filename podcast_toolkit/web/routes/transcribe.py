"""轉字幕：單軌/分軌 job 啟動與狀態、錯字字典、詞庫。"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from podcast_toolkit.web import transcribe as transcribe_mod
from podcast_toolkit.web import transcribe_job
from podcast_toolkit.web.shared import (
    TRANSCRIBABLE_EXTS,
    RouteContext,
    _load_common_glossary,
    _load_episode_glossary,
    _normalize_glossary_entries,
    _save_common_glossary,
    _save_episode_glossary,
    validate_episode_path,
)


def register(app: FastAPI, ctx: RouteContext) -> None:

    @app.post("/api/transcribe")
    def post_transcribe(payload: dict):
        """非同步：立即回 202，背景跑壓縮 + STT + resegment。
        前端 poll /api/transcribe/status 拿進度。"""
        ep = ctx.require_ep()
        rel = (payload.get("path") or "").strip()
        if not rel:
            raise HTTPException(status_code=400, detail="缺少 path")

        src = validate_episode_path(ep, rel)
        if not src.is_file():
            raise HTTPException(status_code=404, detail=f"找不到檔案：{rel}")
        if src.suffix.lower() not in TRANSCRIBABLE_EXTS:
            raise HTTPException(status_code=400, detail="不支援的副檔名")

        cfg = ctx.load_config()
        provider = (cfg.get("transcribe") or {}).get("provider") or "gemini"
        if provider == "xai":
            provider = "gemini"
        if provider not in transcribe_mod.PROVIDERS:
            raise HTTPException(
                status_code=400, detail=f"未知的 STT 供應商：{provider}"
            )
        key_map = {
            "xai": "xai_api_key",
            "gemini": "gemini_api_key",
            "openai": "openai_api_key",
        }
        label_map = {
            "xai": "xAI",
            "gemini": "Gemini",
            "openai": "OpenAI",
            "whisper_mlx": "本地 Whisper",
        }
        # 本地 provider 不需 key；雲端 provider 缺 key 直接擋
        api_key = cfg.get(key_map.get(provider, ""), "") or ""
        if provider in key_map and not api_key:
            raise HTTPException(
                status_code=400,
                detail=f"尚未設定 {label_map[provider]} API key",
            )

        # yaml glossary + UI 編輯（全域 + 本集）→ canonical 為主鍵去重
        merged_glossary = _normalize_glossary_entries(
            (ep.cfg.get("glossary") or [])
            + _load_common_glossary()
            + _load_episode_glossary(ep.dir)
        )
        try:
            info = transcribe_job.start_job(
                ep,
                src_rel=rel,
                provider=provider,
                api_key=api_key,
                typo_entries=ctx.load_typo_dict(),
                glossary=merged_glossary,
            )
        except RuntimeError as e:
            # 已有 job 在跑
            raise HTTPException(status_code=409, detail=str(e)) from e

        return JSONResponse(
            {"ok": True, "src_path": info["src_path"]},
            status_code=202,
        )

    @app.post("/api/transcribe/per-mic")
    def post_transcribe_per_mic(payload: dict):
        """分軌轉錄：背景跑 N 路 Gemini 同步 → srt_merge → _final_v2.srt + speakers.json。

        payload: {"speakers": ["a", "b", "c"]} — 必填，要跑的軌子集。
        """
        ep = ctx.require_ep()
        speakers = payload.get("speakers") or []
        if not isinstance(speakers, list) or not all(isinstance(s, str) for s in speakers):
            raise HTTPException(status_code=400, detail="speakers 必須是字串陣列")
        if not speakers:
            raise HTTPException(status_code=400, detail="speakers 不能是空清單")

        cfg = ctx.load_config()
        api_key = cfg.get("gemini_api_key")
        if not api_key:
            raise HTTPException(status_code=400, detail="尚未設定 Gemini API key")
        # transcribe_per_mic 直接讀 env 變數
        os.environ["GEMINI_API_KEY"] = api_key

        try:
            info = transcribe_job.start_per_mic_job(ep, speakers=speakers, force=True)
        except RuntimeError as e:
            # 已有 job 在跑 / 不認得的 speaker / mics 沒設
            raise HTTPException(status_code=409, detail=str(e)) from e

        return JSONResponse(
            {"ok": True, "speakers": info["speakers"]},
            status_code=202,
        )

    @app.post("/api/transcribe/breeze")
    def post_transcribe_breeze(payload: dict | None = None):
        """一鍵 Breeze 轉字幕：背景跑 Breeze ASR(make_subtitle.py) → ingest_breeze → 本地校對。

        payload(皆選填): {"guest": "來賓姓名", "terms": "詞,詞"}。
        terms 沒給 → 用集詞庫 canonical 當提示詞；免 API key（本地引擎）。
        """
        ep = ctx.require_ep()
        payload = payload or {}
        guest = (payload.get("guest") or "").strip()
        terms = (payload.get("terms") or "").strip()
        if not terms:
            gl = ep.cfg.get("glossary") or []
            terms = "、".join(
                str(g.get("canonical")) for g in gl if isinstance(g, dict) and g.get("canonical")
            )
        try:
            info = transcribe_job.start_breeze_job(ep, guest=guest, terms=terms)
        except RuntimeError as e:
            code = 409 if "已有" in str(e) else 400
            raise HTTPException(status_code=code, detail=str(e)) from e
        return JSONResponse({"ok": True, **info}, status_code=202)

    @app.get("/api/transcribe/status")
    def get_transcribe_status():
        return JSONResponse(transcribe_job.get_status())

    @app.post("/api/resegment")
    def post_resegment(payload: dict):
        """同步重新斷句：使用者自帶字幕（不跑雲端 STT）時，只跑 resegment 後處理
        （斷句 + 改錯字 + 反幻覺）。resegment 純 CPU、秒級，不走 transcribe_job 的
        background slot；只複用它的備份邏輯。

        payload: { src_srt?: 相對路徑 }
        src_srt 給定且非 main_srt → 備份後複製到 main_srt 位置再跑。
        """
        from podcast_toolkit import resegment
        from podcast_toolkit.episode import Episode

        ep = ctx.require_ep()
        if transcribe_job.get_status().get("state") == "running":
            raise HTTPException(status_code=409, detail="已有轉字幕正在進行中，請稍候")

        main_srt = ep.main_srt()
        src_srt = (payload.get("src_srt") or "").strip()
        src_path = None
        if src_srt:
            src_path = validate_episode_path(ep, src_srt)
            if not src_path.is_file():
                raise HTTPException(status_code=404, detail=f"找不到字幕：{src_srt}")

        if not src_srt and not main_srt.is_file():
            raise HTTPException(
                status_code=400,
                detail="找不到來源字幕，請先放一份 SRT 到 01_母帶/ 或在『檔案』選一份",
            )

        # 重跑前先備份現有 _v2.srt / main_srt（複用 transcribe_job 的備份；不走 job 編排）
        try:
            backups = transcribe_job._backup_existing_srts(ep)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"備份原 SRT 失敗：{e}") from e

        # 使用者指定的來源 → 覆寫成 main_srt（resegment.run 固定吃 ep.main_srt()）
        if src_path is not None and src_path != main_srt.resolve():
            main_srt.parent.mkdir(parents=True, exist_ok=True)
            main_srt.write_bytes(src_path.read_bytes())

        try:
            rc = resegment.run(ep.dir, force=True)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"重新斷句失敗：{e}") from e
        if rc != 0:
            raise HTTPException(status_code=400, detail=f"重新斷句失敗 (rc={rc})")

        out = ep.output_v2_srt()
        if not out.is_file() or not out.read_text(encoding="utf-8").strip():
            raise HTTPException(
                status_code=400,
                detail="重新斷句後沒有產生字幕，請確認來源 SRT 的時間碼格式正確",
            )

        # 重建 Episode 讓後續 GET /api/episode 反映新 _v2.srt
        ctx.holder["ep"] = Episode(ep.dir)
        return JSONResponse(
            {"ok": True, "out_srt": str(out.relative_to(ep.dir)), "backups": backups}
        )

    @app.get("/api/typo-dict")
    def get_typo_dict():
        return JSONResponse(ctx.load_typo_dict())

    @app.post("/api/typo-dict")
    def post_typo_dict(payload: dict):
        # payload = {"entries": [{"wrong": "...", "right": "...", "note": "..."}]}
        # 整批覆寫（前端先 GET → 編 → POST）。去重以 wrong 為 key，保留最後一筆
        raw = payload.get("entries") or []
        seen: dict[str, dict] = {}
        for e in raw:
            if not isinstance(e, dict):
                continue
            w, r = e.get("wrong"), e.get("right")
            if not w or not r:
                continue
            seen[str(w)] = {
                "wrong": str(w),
                "right": str(r),
                "note": str(e.get("note", "")),
            }
        entries = list(seen.values())
        ctx.save_typo_dict(entries)
        return JSONResponse(entries)

    @app.get("/api/glossary")
    def get_glossary():
        """回傳 {episode: [...], common: [...], yaml: [...]}。
        yaml 段是 episode.yaml + defaults.yaml 內既有的（唯讀，提示用），
        episode / common 是 UI 可編輯的 JSON sidecar。
        """
        ep = ctx.require_ep()
        return JSONResponse({
            "episode": _load_episode_glossary(ep.dir),
            "common": _load_common_glossary(),
            "yaml": ep.cfg.get("glossary") or [],
        })

    @app.post("/api/glossary/common")
    def post_glossary_common(payload: dict):
        entries = _normalize_glossary_entries(payload.get("entries") or [])
        _save_common_glossary(entries)
        return JSONResponse(entries)

    @app.post("/api/glossary/episode")
    def post_glossary_episode(payload: dict):
        ep = ctx.require_ep()
        entries = _normalize_glossary_entries(payload.get("entries") or [])
        _save_episode_glossary(ep.dir, entries)
        return JSONResponse(entries)
