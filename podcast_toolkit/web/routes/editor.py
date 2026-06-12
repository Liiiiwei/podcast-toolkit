"""編輯工作流：儲存、mics 設定、對齊、靜音偵測、關 server。"""
from __future__ import annotations

import threading

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response

from podcast_toolkit import audio_align
from podcast_toolkit.episode import Episode
from podcast_toolkit.web import episode_io, silencedetect
from podcast_toolkit.web.shared import RouteContext, validate_episode_path


def register(app: FastAPI, ctx: RouteContext) -> None:
    holder = ctx.holder

    @app.post("/api/save")
    def save(payload: dict):
        ep = ctx.require_ep()
        episode_io.save_state(ep, payload)
        # 重新 init Episode 讓 cfg 反映剛寫入的 yaml；否則 GET /api/episode
        # 還是拿 build_app 當下 cache 的 cfg，A/B toggle 等依賴 refetch 的 UI 不會更新
        holder["ep"] = Episode(ep.dir)
        return {"ok": True}

    @app.post("/api/episode/mics")
    def post_episode_mics(payload: dict):
        """寫 mics 設定到 episode.yaml。前端在開分軌轉錄前發現 yaml 沒設 mics 時呼叫。

        payload: {"mics": {"a": "01_母帶/Track1.wav", "b": "...", "c": "..."}}
        - speaker key 必須是 a/b/c
        - path 是相對 episode 根的相對路徑（用既有 audio_candidates 同款格式）
        - 檔案必須存在，且要落在 episode 資料夾內（防 ../ 逸出）
        """
        ep = ctx.require_ep()
        mics = payload.get("mics") or {}
        if not isinstance(mics, dict) or not mics:
            raise HTTPException(status_code=400, detail="mics 必須是 {speaker: path} 物件")
        allowed = {"a", "b", "c"}
        for sp, path in mics.items():
            if sp not in allowed:
                raise HTTPException(status_code=400, detail=f"speaker {sp!r} 不在允許範圍 {sorted(allowed)}")
            if not isinstance(path, str) or not path.strip():
                raise HTTPException(status_code=400, detail=f"{sp} 的路徑不能空")
            target = validate_episode_path(ep, path, detail_prefix=f"{sp} ")
            if not target.is_file():
                raise HTTPException(status_code=404, detail=f"{sp} 找不到檔案：{path}")
        episode_io.save_mics_config(ep, mics)
        holder["ep"] = Episode(ep.dir)
        return {"ok": True, "mics": dict(sorted(mics.items()))}

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
