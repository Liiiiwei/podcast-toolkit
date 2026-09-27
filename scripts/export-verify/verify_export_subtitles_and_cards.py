#!/usr/bin/env python3
"""端到端匯出走查：字幕與標題卡是否真的燒進成品畫面（像素級對照組）。

為什麼要像素級：ASS 檔寫得出來 ≠ 畫面上看得到（libass 沒吃到、Layer 被蓋掉、
force_style 蓋掉 drawing、濾鏡鏈漏接，全都會讓「檔案正確、畫面空白」）。
所以每一項都綁布林斷言，並用三輪對照組互相證明：

  A  burn   + 3 張標題卡 → 主體
  B  burn   + 無標題卡   → 對照組（證明 A 的像素差真的來自標題卡）
  C  sidecar + 3 張標題卡 → 驗外掛 .srt；同時查「sidecar 模式的標題卡去哪了」

用法：
  /usr/bin/python3 -u scripts/export-verify/verify_export_subtitles_and_cards.py
輸出：
  scripts/export-verify/verify_export_result.json（機器讀）＋ stdout（人讀）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

STATIC = REPO / "podcast_toolkit" / "web" / "static"
SANDBOX_ROOT = Path("/private/tmp/pt-export-verify")
NAME = "匯出驗證集"
EP_DIR = SANDBOX_ROOT / "episode" / f"20260927 {NAME}"
OUT_JSON = Path(__file__).resolve().parent / "verify_export_result.json"

# 標題卡：三種版型各一張，時間錯開、位置全放畫面上半（避開底部字幕帶，才分得出誰是誰）
CARDS = [
    {"id": "c-big", "tpl": "big", "text": "大字報測試",
     "start": 1.0, "end": 3.0, "x": 0.5, "y": 0.28, "scale": 100},
    {"id": "c-lower", "tpl": "lower", "text": "下標條測試",
     "start": 8.0, "end": 11.0, "x": 0.2, "y": 0.18, "scale": 100},
    {"id": "c-quote", "tpl": "quote", "text": "引言框測試",
     "start": 14.0, "end": 17.0, "x": 0.5, "y": 0.42, "scale": 100},
]
# 抽幀點（正片時間軸）：三張卡的中點 + 一個「只有字幕沒有卡」的時刻
T_BIG, T_LOWER, T_QUOTE = 2.0, 9.5, 15.5
T_SUB = 5.5  # sample-subtitles.json 第 3 句 4.25-6.65 覆蓋，且不在任何卡的區間內

RESULTS: list[dict] = []


def check(name: str, ok: bool, got, want) -> bool:
    """每個走查項都必須經過這裡 —— 只 print 不斷言的行一律視為未測。"""
    RESULTS.append({"name": name, "ok": bool(ok), "got": got, "want": want})
    print(f"{'✓' if ok else '✗'} {name}\n    got={got}\n    want={want}")
    return bool(ok)


def bin_path(name: str) -> str:
    """挑一顆能用的 ffmpeg／ffprobe：env > 專案 ffmpeg_bin() > PATH（不寫死路徑）。"""
    env = os.environ.get(name.upper())
    if env:
        return env
    if name == "ffmpeg":
        try:
            from podcast_toolkit.assemble import ffmpeg_bin
            return ffmpeg_bin()
        except Exception:
            pass
    return shutil.which(name) or name


FFMPEG = bin_path("ffmpeg")
FFPROBE = bin_path("ffprobe")


def retire_dir(d: Path) -> None:
    """清舊沙盒：優先 trash CLI，沒有就搬進 _graveyard/。禁用 rm -rf／shutil.rmtree。"""
    if shutil.which("trash"):
        subprocess.run(["trash", str(d)], check=True)
        return
    grave = SANDBOX_ROOT / "_graveyard"
    grave.mkdir(parents=True, exist_ok=True)
    shutil.move(str(d), str(grave / f"{d.name}-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"))


def sec_to_srt_ts(t: float) -> str:
    ms = round(t * 1000)
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt_ts_to_sec(ts: str) -> float:
    hms, ms = ts.strip().split(",")
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s + int(ms) / 1000


def parse_srt(text: str) -> list[dict]:
    out = []
    for blk in text.strip().split("\n\n"):
        lines = [ln for ln in blk.splitlines() if ln.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        a, b = lines[1].split("-->")
        out.append({"start": srt_ts_to_sec(a), "end": srt_ts_to_sec(b),
                    "text": "\n".join(lines[2:])})
    return out


def write_episode_yaml(with_cards: bool) -> None:
    """把 title_cards 寫進／拿掉 episode.yaml（B 輪的對照組就靠這個切）。"""
    cards_yaml = ""
    if with_cards:
        cards_yaml = "title_cards:\n" + "".join(
            f"  - id: {c['id']}\n"
            f"    tpl: {c['tpl']}\n"
            f"    text: \"{c['text']}\"\n"
            f"    start: {c['start']}\n"
            f"    end: {c['end']}\n"
            f"    x: {c['x']}\n"
            f"    y: {c['y']}\n"
            f"    scale: {c['scale']}\n"
            for c in CARDS
        )
    else:
        cards_yaml = "title_cards: []\n"
    (EP_DIR / "episode.yaml").write_text(
        f"date: 20260927\n"
        f"name: {NAME}\n"
        f'main_video: "01_母帶/{{name}}.mp4"\n'
        f'main_srt: "03_成品/{{name}}_final.srt"\n'
        f"fixes: []\ncard_fixes: []\nforce_break: []\nforce_join: []\n"
        f"{cards_yaml}",
        encoding="utf-8",
    )


def build_sandbox() -> list[dict]:
    if EP_DIR.exists():
        retire_dir(EP_DIR)
    for sub in ("01_母帶", "03_成品", "04_工作檔"):
        (EP_DIR / sub).mkdir(parents=True, exist_ok=True)

    src_video = STATIC / "sample-video.mp4"
    if not src_video.exists():
        sys.exit(f"找不到 {src_video}")
    shutil.copyfile(src_video, EP_DIR / "01_母帶" / f"{NAME}.mp4")

    subs = json.loads((STATIC / "sample-subtitles.json").read_text(encoding="utf-8"))["subs"]
    lines = []
    for i, s in enumerate(subs, 1):
        lines += [str(i), f"{sec_to_srt_ts(s['start'])} --> {sec_to_srt_ts(s['end'])}",
                  s["text"], ""]
    srt_text = "\n".join(lines) + "\n"
    (EP_DIR / "03_成品" / f"{NAME}_final_v2.srt").write_text(srt_text, encoding="utf-8")
    (EP_DIR / "03_成品" / f"{NAME}_final.srt").write_text(srt_text, encoding="utf-8")

    write_episode_yaml(with_cards=True)
    print(f"沙盒集：{EP_DIR}（ffmpeg={FFMPEG}）字幕 {len(subs)} 句、標題卡 {len(CARDS)} 張")
    return subs


def run_assemble(out_path: Path, subtitle_mode: str) -> int:
    from podcast_toolkit import assemble
    print(f"\n── 合成：mode={subtitle_mode} → {out_path.name}")
    t0 = time.time()
    rc = assemble.run(EP_DIR, force=True, output_kind="yt",
                      subtitle_mode=subtitle_mode, out_override=out_path)
    print(f"   exit={rc}，{time.time() - t0:.1f}s")
    return rc


def probe(video: Path) -> dict:
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries",
         "stream=codec_type,width,height:format=duration",
         "-of", "json", str(video)], capture_output=True, text=True)
    d = json.loads(r.stdout or "{}")
    kinds = sorted({s.get("codec_type") for s in d.get("streams", [])})
    vs = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    return {"kinds": kinds, "w": int(vs.get("width") or 0), "h": int(vs.get("height") or 0),
            "dur": float((d.get("format") or {}).get("duration") or 0)}


def grab(video: Path, t: float, w: int, h: int):
    """抽一幀成 rawvideo rgb24。長度不等於 w*h*3 就是抽失敗，不能當成黑畫面吞掉。"""
    import numpy as np
    r = subprocess.run(
        [FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", str(video),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True)
    raw = r.stdout
    if len(raw) != w * h * 3:
        raise RuntimeError(f"抽幀失敗 t={t}: {len(raw)} bytes（應 {w*h*3}）{r.stderr[-300:]!r}")
    return np.frombuffer(raw, dtype="uint8").reshape(h, w, 3)


def orange_in_mask(img, mask) -> int:
    """在指定 mask（＝與對照組的差異區塊）內數橘色像素。

    全畫面數橘色不可用：sample-video 背景本身就有大量橘系像素（實測 B 輪無卡 1187 px
    比 A 輪有卡 1095 px 還多）。把範圍縮到「有卡才會變的那塊」才是色條的訊號。
    """
    r, g, b = img[:, :, 0].astype(int), img[:, :, 1].astype(int), img[:, :, 2].astype(int)
    hit = ((r > 215) & (g > 85) & (g < 160) & (b > 30) & (b < 115)
           & (r - g > 70) & (g - b > 15))
    return int((hit & mask).sum())


def white_pixels_band(img, y0: float, y1: float) -> int:
    h = img.shape[0]
    band = img[int(h * y0):int(h * y1), :, :].astype(int)
    return int((band.min(axis=2) > 225).sum())


def diff_bbox(a, b, thr: int = 26):
    """A 與 B 同一幀的差異區塊 bbox（正規化座標）＋差異像素數。"""
    import numpy as np
    d = np.abs(a.astype(int) - b.astype(int)).max(axis=2)
    mask = d > thr
    n = int(mask.sum())
    if n == 0:
        return n, None
    h, w = mask.shape
    # 先濾掉「零星雜訊列」：編碼雜訊會散布全畫面，union bbox 的中心會被它拉到 0.5，
    # 量到的 cy 就跟卡實際位置無關（前科：big 卡 \pos y=0.28，bbox 中心量到 0.462）。
    # 只保留像素數達「最多那列的 10%」且至少 8 px 的列，再取「質心」（非 bbox 中心）。
    row_counts = mask.sum(axis=1)
    sig_rows = np.nonzero(row_counts >= max(8, row_counts.max() * 0.1))[0]
    sig = np.zeros_like(mask)
    sig[sig_rows, :] = mask[sig_rows, :]
    if not sig.any():
        sig = mask
    ys, xs = np.nonzero(sig)
    return n, {"x0": xs.min() / w, "x1": xs.max() / w,
               "y0": ys.min() / h, "y1": ys.max() / h,
               "cx": float(xs.mean()) / w, "cy": float(ys.mean()) / h,
               "sig_px": int(sig.sum()), "mask": sig}


def main() -> int:
    import numpy as np  # noqa: F401  （先炸在這裡比炸在抽幀時好讀）

    subs = build_sandbox()
    from podcast_toolkit.assemble import Episode
    cfg = Episode(EP_DIR).cfg
    intro_offset = float(cfg["assets"]["intro_duration"])
    # 倍速：照 assemble.py 的讀法（enabled/factor，夾 0.5–2.0），不要寫死 1.0。
    # 前科：抽幀直接用 intro+正片時間，但成品正片被加速 1.1 倍 → 抽到的是別的時刻，
    # quote 卡（正片 14-17s → 成品 19.77-22.49s）抽 22.54s 已經過界，量到「卡不見了」。
    _sp = cfg.get("speed") or {}
    speed = 1.0
    if _sp.get("enabled"):
        try:
            speed = min(2.0, max(0.5, float(_sp.get("factor") or 1.0)))
        except (TypeError, ValueError):
            speed = 1.0

    def out_t(t: float) -> float:
        """正片（字幕／卡的來源軸）時間 → 成品時間軸。本集無刪段，只有片頭位移＋倍速。"""
        return intro_offset + t / speed

    work = EP_DIR / "04_工作檔"
    prod = EP_DIR / "03_成品"

    # ── A 輪：burn + 標題卡 ────────────────────────────────────────────
    out_a = prod / "A_burn_cards.mp4"
    rc_a = run_assemble(out_a, "burn")
    check("A1 burn 合成 exit 0", rc_a == 0, rc_a, 0)
    if rc_a != 0:
        return 1
    ass_a = work / "_v2_aligned_1920x1080.ass"
    ass_a_text = ass_a.read_text(encoding="utf-8") if ass_a.exists() else ""
    shutil.copyfile(ass_a, work / "_keep_A.ass") if ass_a.exists() else None
    info_a = probe(out_a)

    check("E1 A 成品有 video+audio 兩軌", info_a["kinds"] == ["audio", "video"],
          info_a["kinds"], ["audio", "video"])
    want_dur = out_t(24.0)  # 正片 24s 除以倍速 ＋ 片頭位移（＋片尾卡故下面用下界）
    check(f"E2 A 成品長度 ≥ 片頭+正片/{speed}倍速（含片尾卡故用下界）",
          info_a["dur"] >= want_dur - 0.5, round(info_a["dur"], 3), f">= {want_dur - 0.5:.3f}")
    check("E3 burn 模式產出 ASS 工作檔", ass_a.exists(), str(ass_a), "存在")

    dlg = [ln for ln in ass_a_text.splitlines() if ln.startswith("Dialogue:")]
    layer0 = [ln for ln in dlg if ln.split(",", 1)[0] == "Dialogue: 0"]
    check("E4 ASS 的 Layer 0 字幕事件數 == SRT 卡數",
          len(layer0) == len(subs), len(layer0), len(subs))
    draw = [ln for ln in dlg if "\\p1" in ln and ln.split(",", 1)[0] != "Dialogue: 0"]
    # 三張卡：big=底框+字、lower=底框+色條+字、quote=底框+外框+字 → drawing 事件 ≥ 4
    texts = [c["text"] for c in CARDS]
    have_txt = all(any(t in ln for ln in dlg) for t in texts)
    check("E5 三張標題卡的文字都進了 ASS，且有 Layer>0 的 drawing 事件",
          have_txt and len(draw) >= 4, {"文字齊": have_txt, "drawing 事件": len(draw)},
          {"文字齊": True, "drawing 事件": ">= 4"})
    check("E6 lower 色條用 #ff7849（ASS 是 BGR：\\1c&H4978FF&）",
          "\\1c&H4978FF&" in ass_a_text, "\\1c&H4978FF&" in ass_a_text, True)

    w, h = info_a["w"], info_a["h"]
    fa = {k: grab(out_a, out_t(t), w, h)
          for k, t in (("big", T_BIG), ("lower", T_LOWER), ("quote", T_QUOTE), ("sub", T_SUB))}

    # ── B 輪：burn + 無標題卡（對照組）────────────────────────────────
    write_episode_yaml(with_cards=False)
    out_b = prod / "B_burn_nocards.mp4"
    rc_b = run_assemble(out_b, "burn")
    check("B1 對照組合成 exit 0", rc_b == 0, rc_b, 0)
    if rc_b != 0:
        return 1
    ass_b_text = (work / "_v2_aligned_1920x1080.ass").read_text(encoding="utf-8")
    check("B2 對照組 ASS 沒有任何 drawing 事件（確認對照組真的沒卡）",
          "\\p1" not in ass_b_text, ass_b_text.count("\\p1"), 0)
    info_b = probe(out_b)
    fb = {k: grab(out_b, out_t(t), w, h)
          for k, t in (("big", T_BIG), ("lower", T_LOWER), ("quote", T_QUOTE), ("sub", T_SUB))}

    # E8/E9 逐張卡：A−B 差異必須存在，且差異質心落在該卡宣告的 (x, y) 附近
    boxes = {}
    for key, card in (("big", CARDS[0]), ("lower", CARDS[1]), ("quote", CARDS[2])):
        n, box = diff_bbox(fa[key], fb[key])
        boxes[key] = box
        near = bool(box) and abs(box["cy"] - card["y"]) <= 0.14
        check(f"E8-{key} A−B 有差異像素（標題卡真的燒進畫面）", n >= 2000, n, ">= 2000")
        check(f"E9-{key} 差異質心的 y 落在卡宣告的 y={card['y']} 附近（±0.14）",
              near, None if not box else round(box["cy"], 3), card["y"])

    # E7 橘色色條：只在「A−B 差集」內數（全畫面數會被背景橘系像素蓋掉，見 orange_in_mask）
    lower_mask = boxes["lower"]["mask"] if boxes["lower"] else None
    oa = orange_in_mask(fa["lower"], lower_mask) if lower_mask is not None else 0
    ob = orange_in_mask(fb["lower"], lower_mask) if lower_mask is not None else 0
    check("E7 lower 色條橘色像素（在 A−B 差集內）：A 有、B 無",
          oa >= 300 and ob <= max(20, oa // 10),
          {"A": oa, "B": ob}, {"A": ">= 300", "B": f"<= {max(20, oa // 10)}"})

    # E10 字幕帶：A 在「只有字幕」的時刻，底部 0.82-0.99 帶要有白字像素
    wa = white_pixels_band(fa["sub"], 0.82, 0.99)
    n_sub, _ = diff_bbox(fa["sub"], fb["sub"])
    check("E10 A 在無卡時刻底部有燒上的白色字幕像素", wa >= 300, wa, ">= 300")
    check("E10b 無卡時刻 A 與 B 幾乎相同（證明白像素來自字幕不是卡）",
          n_sub <= 2000, n_sub, "<= 2000")

    # ── C 輪：sidecar + 標題卡 ────────────────────────────────────────
    write_episode_yaml(with_cards=True)
    out_c = prod / "C_sidecar_cards.mp4"
    rc_c = run_assemble(out_c, "sidecar")
    check("C1 sidecar 合成 exit 0", rc_c == 0, rc_c, 0)
    if rc_c != 0:
        return 1
    side = out_c.with_suffix(".srt")
    check("E11 sidecar 模式產出外掛 .srt", side.exists(), str(side.name), "存在")
    side_cards = parse_srt(side.read_text(encoding="utf-8")) if side.exists() else []
    check("E11b 外掛 .srt 卡數 == 原字幕卡數", len(side_cards) == len(subs),
          len(side_cards), len(subs))
    ok_shift = bool(side_cards) and all(
        abs(sc["start"] - out_t(s["start"])) <= 0.05
        and abs(sc["end"] - out_t(s["end"])) <= 0.05
        for sc, s in zip(side_cards, subs))
    check(f"E11c 外掛 .srt 時間 = 片頭 {intro_offset}s + 原時間/{speed}倍速（逐句比對）", ok_shift,
          None if not side_cards else [round(side_cards[0]["start"], 3),
                                       round(side_cards[-1]["end"], 3)],
          [round(out_t(subs[0]["start"]), 3), round(out_t(subs[-1]["end"]), 3)])

    info_c = probe(out_c)
    fc = {k: grab(out_c, out_t(t), w, h)
          for k, t in (("lower", T_LOWER), ("sub", T_SUB))}
    wc = white_pixels_band(fc["sub"], 0.82, 0.99)
    check("E12 sidecar 影片畫面沒有燒字幕（白像素遠少於 burn）",
          wc <= max(50, wa // 10), {"sidecar": wc, "burn": wa}, f"<= {max(50, wa // 10)}")
    # E13 sidecar 的標題卡：拿 B 輪（burn 無卡）同一幀的「上半畫面」當對照。
    # 只取上半是因為 B 有燒字幕、C 沒有，底部字幕帶的差異會把質心拉歪；lower 卡在 y=0.18，
    # 上半區兩輪的影片內容／片頭／倍速全同 → 差異只可能來自標題卡。
    half = int(h * 0.6)
    n_c, box_c = diff_bbox(fc["lower"][:half], fb["lower"][:half])
    cy_c = box_c["cy"] * 0.6 if box_c else None
    oc = orange_in_mask(fc["lower"][:half], box_c["mask"]) if box_c else 0
    card_lower = CARDS[1]
    check("E13 sidecar 模式的標題卡仍燒進畫面（上半畫面 C−B 有差異像素）",
          n_c >= 2000, n_c, ">= 2000")
    check("E13b 該差異是 lower 色條（差集內橘色 #ff7849 像素）", oc >= 300, oc, ">= 300")
    check(f"E13c 差異質心 y 落在卡宣告的 y={card_lower['y']} 附近（±0.14）",
          cy_c is not None and abs(cy_c - card_lower["y"]) <= 0.14,
          None if cy_c is None else round(cy_c, 3), card_lower["y"])

    # E14 sidecar 寫的是「只有卡、沒有字幕」的 cards-only ASS，且不覆蓋 burn 版的 ASS
    ass_c = work / "_v2_aligned_1920x1080_cardsonly.ass"
    ass_c_text = ass_c.read_text(encoding="utf-8") if ass_c.exists() else ""
    check("E14 sidecar 產出 cards-only ASS 工作檔", ass_c.exists(), ass_c.name, "存在")
    check("E14b cards-only ASS 有 drawing 事件但沒有 Layer 0 字幕事件",
          "\\p1" in ass_c_text and "Dialogue: 0," not in ass_c_text,
          {"drawing": ass_c_text.count("\\p1"), "layer0": ass_c_text.count("Dialogue: 0,")},
          {"drawing": "> 0", "layer0": 0})
    ass_burn_now = (work / "_v2_aligned_1920x1080.ass").read_text(encoding="utf-8")
    check("E14c burn 版 ASS 沒被 sidecar 那輪覆蓋（兩種模式分檔存）",
          ass_burn_now == ass_b_text, ass_burn_now == ass_b_text, True)

    ok = all(r["ok"] for r in RESULTS)
    payload = {
        "ok": ok,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ffmpeg": FFMPEG,
        "episode": str(EP_DIR),
        "intro_offset": intro_offset,
        "speed": speed,
        "frames_sampled_source": {"big": T_BIG, "lower": T_LOWER, "quote": T_QUOTE, "sub": T_SUB},
        "frames_sampled_output": {k: round(out_t(t), 3) for k, t in
                                  (("big", T_BIG), ("lower", T_LOWER),
                                   ("quote", T_QUOTE), ("sub", T_SUB))},
        "probe": {"A": info_a, "B": info_b, "C": info_c},
        "checks": RESULTS,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    bad = [r["name"] for r in RESULTS if not r["ok"]]
    print(f"\n{'='*60}\n{'全部通過' if ok else '未通過：' + ', '.join(bad)}")
    print(f"結果：{OUT_JSON}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
