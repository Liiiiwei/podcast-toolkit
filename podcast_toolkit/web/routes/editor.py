"""編輯工作流：儲存、mics 設定、對齊、靜音偵測、關 server。"""
from __future__ import annotations

import threading

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response

from podcast_toolkit import audio_align, silencedetect
from podcast_toolkit.episode import Episode
from podcast_toolkit.web import episode_io
from podcast_toolkit.web.shared import RouteContext, validate_episode_path


def register(app: FastAPI, ctx: RouteContext) -> None:
    holder = ctx.holder

    @app.post("/api/save")
    def save(payload: dict):
        ep = ctx.require_ep()
        try:
            episode_io.save_state(ep, payload)
        except ValueError as e:
            # 不合法的 card_timings（end<=start / 非數字）等 → 400 帶中文訊息，而非裸 500。
            # save_state 在寫任何檔案前就 raise，所以無檔案損壞之虞。
            raise HTTPException(status_code=400, detail=str(e))
        # 重新 init Episode 讓 cfg 反映剛寫入的 yaml；否則 GET /api/episode
        # 還是拿 build_app 當下 cache 的 cfg，A/B toggle 等依賴 refetch 的 UI 不會更新
        holder["ep"] = Episode(ep.dir)
        return {"ok": True}

    @app.post("/api/episode/mics")
    def post_episode_mics(payload: dict):
        """寫 mics 設定到 episode.yaml。前端在開分軌轉錄前發現 yaml 沒設 mics 時呼叫。

        payload: {
          "mics": {"a": "01_母帶/Track1.wav", "b": "...", "c": "..."},
          "roles": {"a": "host", "b": "host", "c": "guest"},  # optional
          "min_sec": 15,                                       # optional
        }
        - speaker key 必須是 a/b/c/d（三~四軌）
        - path 是相對 episode 根的相對路徑（用既有 audio_candidates 同款格式）
        - 檔案必須存在，且要落在 episode 資料夾內（防 ../ 逸出）
        - 給 roles 時一併寫 camera_rule（cam A=全景 home、guest 軌→cam B）
        """
        ep = ctx.require_ep()
        mics = payload.get("mics") or {}
        if not isinstance(mics, dict) or not mics:
            raise HTTPException(status_code=400, detail="mics 必須是 {speaker: path} 物件")
        allowed = {"a", "b", "c", "d"}
        for sp, path in mics.items():
            if sp not in allowed:
                raise HTTPException(status_code=400, detail=f"speaker {sp!r} 不在允許範圍 {sorted(allowed)}")
            if not isinstance(path, str) or not path.strip():
                raise HTTPException(status_code=400, detail=f"{sp} 的路徑不能空")
            target = validate_episode_path(ep, path, detail_prefix=f"{sp} ")
            if not target.is_file():
                raise HTTPException(status_code=404, detail=f"{sp} 找不到檔案：{path}")
        roles = payload.get("roles")
        if roles is not None:
            if not isinstance(roles, dict):
                raise HTTPException(status_code=400, detail="roles 必須是 {speaker: host|guest} 物件")
            for sp, role in roles.items():
                if sp not in allowed:
                    raise HTTPException(status_code=400, detail=f"roles 的 speaker {sp!r} 不在允許範圍")
                if role not in ("host", "guest"):
                    raise HTTPException(status_code=400, detail=f"role {role!r} 必須是 host 或 guest")
        min_sec = payload.get("min_sec")
        if min_sec is not None:
            try:
                min_sec = float(min_sec)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="min_sec 必須是數字")
        episode_io.save_mics_config(ep, mics, roles=roles, min_sec=min_sec)
        holder["ep"] = Episode(ep.dir)
        return {"ok": True, "mics": dict(sorted(mics.items()))}

    @app.post("/api/cameras-suggest")
    def cameras_suggest_route():
        """依分軌 speakers + camera_rule 自動推時間版 A/B 切換點，覆蓋 cameras.json（先備份 .bak）。
        需要 speakers.json（分軌集才有）；切點放交接靜默中點。回傳切換點數。"""
        ep = ctx.require_ep()
        if not ep.output_v2_speakers_json().exists():
            raise HTTPException(
                status_code=400,
                detail="這集沒有分軌講者資料（speakers.json）；請先用「分軌轉錄」產生講者，才能依講者自動推鏡頭。",
            )
        from podcast_toolkit import cameras_suggest

        rc = cameras_suggest.run(ep, force=True)
        if rc != 0:
            raise HTTPException(status_code=400, detail="自動推鏡頭失敗（缺字幕或講者資料）。")
        holder["ep"] = Episode(ep.dir)
        import json

        cj = ep.output_v2_cameras_json()
        count = 0
        if cj.exists():
            try:
                count = len((json.loads(cj.read_text(encoding="utf-8")) or {}).get("transitions", []))
            except (ValueError, OSError):
                count = 0
        return {"ok": True, "count": count}

    @app.post("/api/shutdown")
    def cancel():
        threading.Timer(0.3, ctx.shutdown).start()
        return Response(status_code=204)

    @app.post("/api/detect-silence")
    def post_detect_silence():
        """智慧建議：跑 ffmpeg silencedetect 看 main_video 開頭靜音長度（秒）。"""
        ep = ctx.require_ep()
        main = ep.main_video()
        if not main.is_file():
            raise HTTPException(
                status_code=400,
                detail=f"找不到 main_video：{main.relative_to(ep.dir)}",
            )
        try:
            head_sec = silencedetect.detect_head_silence(main)
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))
        return JSONResponse({"head_silence_sec": head_sec})

    @app.post("/api/shift-srt")
    def shift_srt_route(payload: dict):
        """把 _v2.srt 整體位移 offset_sec 秒（就地覆寫，先備份 .bak）。
        正值 = 字幕往後延遲（字幕目前出現太早）；負值 = 字幕往前提前。
        """
        ep = ctx.require_ep()
        try:
            offset_sec = float(payload.get("offset_sec") or 0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="offset_sec 必須是數字")
        if offset_sec == 0:
            raise HTTPException(status_code=400, detail="offset_sec 不能為 0")
        v2 = ep.output_v2_srt()
        if not v2.exists():
            raise HTTPException(status_code=404, detail="找不到 _v2.srt，請先轉字幕")
        from podcast_toolkit import srt_io
        cards = srt_io.parse(v2.read_text(encoding="utf-8"))
        backup = v2.with_suffix(v2.suffix + ".bak")
        backup.write_text(v2.read_text(encoding="utf-8"), encoding="utf-8")
        shifted = []
        for c in cards:
            new_end = c["end"] + offset_sec
            if new_end <= 0:
                continue
            shifted.append({**c, "start": max(0.0, c["start"] + offset_sec), "end": new_end})
        v2.write_text(srt_io.serialize(shifted), encoding="utf-8")
        return {"ok": True, "card_count": len(shifted), "offset_sec": offset_sec}

    @app.post("/api/auto-align")
    def auto_align_route(payload: dict | None = None):
        """T23b：前 120 秒做音訊互相關，回傳「對齊對象 相對 cam A」的秒偏移。
        不寫 yaml — 前端拿到值填到 input，使用者按儲存才走 /api/save。

        payload 接兩種對齊模式：
        - {"audio_path": "..."}：對「外接音檔 vs cam A」算偏移
        - 否則：對「cam B vs cam A」；cam B 優先讀 payload['cam_b_path']，
          沒給才 fallback 讀 yaml 裡已存的 cameras.b

        cam A 也走同樣的「payload 優先」邏輯（payload['cam_a_path']），
        讓使用者在 modal 改 cam A 後不必先按儲存就能對齊。
        """
        ep = ctx.require_ep()
        payload = payload or {}
        cameras = ep.cfg.get("cameras") or {}
        cam_a_rel = (payload.get("cam_a_path") or "").strip() \
            or cameras.get("a") or ep.cfg.get("main_video")
        if not cam_a_rel:
            raise HTTPException(status_code=400, detail="缺 cam A，無法對齊")
        cam_a = ep.resolve_episode_path(cam_a_rel)
        if not cam_a.is_file():
            raise HTTPException(
                status_code=404,
                detail=f"找不到 cam A 檔案：{cam_a_rel}（解析後：{cam_a}）",
            )

        audio_path = (payload.get("audio_path") or "").strip()
        if audio_path:
            audio_file = ep.resolve_episode_path(audio_path)
            if not audio_file.is_file():
                raise HTTPException(status_code=404, detail=f"找不到音檔：{audio_path}")
            try:
                offset_sec = audio_align.auto_align(cam_a, audio_file)
            except RuntimeError as e:
                raise HTTPException(status_code=500, detail=str(e))
            return {"ok": True, "offset_sec": offset_sec}

        cam_b_rel = (payload.get("cam_b_path") or "").strip() or cameras.get("b")
        if not cam_b_rel:
            raise HTTPException(status_code=400, detail="請先在鏡頭 modal 選好 cam B 再對齊")
        cam_b = ep.resolve_episode_path(cam_b_rel)
        if not cam_b.is_file():
            raise HTTPException(status_code=404, detail=f"找不到 cam B 檔案：{cam_b_rel}")
        try:
            offset_sec = audio_align.auto_align(cam_a, cam_b)
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))
        return {"ok": True, "offset_sec": offset_sec}

    @app.post("/api/manual-align")
    def manual_align_route(payload: dict):
        """T23c：使用者手動標三組 (a, b) 時間點 → 算 offset + 一致性 deltas。
        不寫 yaml — 前端拿到 offset 填到 #cam-sync-offset-b，使用者按儲存才走 /api/save。"""
        events = payload.get("events")
        try:
            offset_sec, deltas = audio_align.compute_manual_offset(events)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "offset_sec": offset_sec, "deltas": deltas}
