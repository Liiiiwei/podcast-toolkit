"""cam-modal 存檔 payload 防漂移測試：鎖住「全 app 只有一套 save payload builder」。

背景（掃描 H1，資料遺失級 bug）：cam-modal 曾有自己的 `_buildCamModalSavePayload`，
相比主存檔 buildSavePayload 漏送 merges / time_overrides / new_cards / reels_clips /
rotate / cover_enabled / subtitle_style / silence_trim；存檔成功後 loadEpisodeState()
重載，主編輯器尚未儲存的編輯被靜默清掉。修法：cam-modal 一律以 buildSavePayload()
為基底 spread、只覆蓋 cam 相關欄位。此檔以純文字斷言鎖住該約定 —— 未來若有人再
手寫第二套 payload 欄位清單，這裡會紅。
"""
import re
from pathlib import Path

import podcast_toolkit.web as web_pkg

STATIC = Path(web_pkg.__file__).parent / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")

# 這些是主存檔 payload 的專屬 key 字面量（形如 "key:" 的物件欄位）。
# 全檔只允許出現一次（在 buildSavePayload 裡）；出現第二次＝有人又手寫了
# 第二套 builder，會重演「漏欄位→存檔清資料」的 H1 bug。
SAVE_PAYLOAD_UNIQUE_KEYS = [
    "crop_yt:",
    "crop_reels:",
    "cameras_mapping:",
    "speakers_mapping:",
    "splits:",
    "card_timings:",
    "merges:",
    "time_overrides:",
    "new_cards:",
    "reels_clips:",
    "silence_trim:",
    "subtitle_style:",
    "cover_enabled:",
]


def _handler_block(element_id: str) -> str:
    """取出 $(\"#id\").addEventListener(...) 到下一個頂層 $(\"#...\") 綁定之間的原始碼。"""
    start = APP_JS.index(f'$("#{element_id}").addEventListener')
    nxt = APP_JS.find('\n$("#', start + 1)
    return APP_JS[start : nxt if nxt != -1 else len(APP_JS)]


def test_old_cam_modal_builder_removed():
    # 舊的第二套 builder 名稱不得復活
    assert "_buildCamModalSavePayload" not in APP_JS, (
        "_buildCamModalSavePayload 又出現了 —— cam-modal 存檔必須以 "
        "buildSavePayload() 為基底，不得手寫第二套欄位清單"
    )


def test_save_payload_keys_defined_exactly_once():
    # 任何第二套 builder 都得重寫這些 key → 出現次數 >1 即紅
    dup = {k: APP_JS.count(k) for k in SAVE_PAYLOAD_UNIQUE_KEYS if APP_JS.count(k) != 1}
    assert not dup, (
        f"save payload key 在 app.js 出現次數 != 1：{dup}；"
        "疑似有人新增了第二套 payload builder（H1 資料遺失 bug 的根源）"
    )


def test_cam_modal_payload_spreads_build_save_payload():
    # _camModalSavePayload 必須 spread buildSavePayload()，且 cam 覆蓋欄位放在 spread 之後
    m = re.search(
        r"function _camModalSavePayload\(\)\s*\{(.*?)\n\}", APP_JS, re.DOTALL
    )
    assert m, "app.js 缺 _camModalSavePayload()（cam-modal 存檔的共用 payload 函式）"
    body = m.group(1)
    assert "...buildSavePayload()" in body, (
        "_camModalSavePayload 沒有以 buildSavePayload() 為基底"
    )
    spread_at = body.index("...buildSavePayload()")
    for field in ["cam_a_path:", "cam_b_path:", "camera_sync_offset_b:", "srt_path:"]:
        assert field in body, f"_camModalSavePayload 缺 cam 覆蓋欄位 {field}"
        assert body.index(field) > spread_at, (
            f"{field} 必須放在 ...buildSavePayload() 之後，否則會被 spread 蓋掉"
        )


def test_cam_modal_save_paths_use_shared_builder():
    # cam-modal 兩條存檔路徑（儲存鈕、一鍵對齊）都要走 _camModalSavePayload()
    for element_id in ["cam-save", "align-all"]:
        block = _handler_block(element_id)
        assert "_camModalSavePayload()" in block, (
            f"#{element_id} 的存檔沒有走 _camModalSavePayload()，"
            "可能又內聯自建 payload（會漏欄位）"
        )
