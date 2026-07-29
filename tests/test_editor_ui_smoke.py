"""編輯器前端煙霧測試：鎖住 UI 的關鍵元素與行為，防止改版誤刪。

本機沒有 node / 瀏覽器自動化，所以用「靜態字串斷言」代替 DOM 測試：
讀 index.html / app.js / app.css 的原始碼，比對元素 id、屬性與 CSS 規則。
測不到互動，但足以擋住「元素被誤刪」「前後端預設值漂移」這兩類回歸。

Note: Reels 功能已從 UI 移除（6d02e7e）；旋轉控制項也已移除（保留後端 rotate 欄位）。
"""
import re
from pathlib import Path

import podcast_toolkit.web as web_pkg
from podcast_toolkit import config

STATIC = Path(web_pkg.__file__).parent / "static"
INDEX_HTML = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
APP_CSS = (STATIC / "app.css").read_text(encoding="utf-8")

# 現行輸出選單（YT 完整版／原速 MP3／5 分鐘預覽）
OUTPUT_BUTTON_IDS = [
    "assemble-yt-btn",
    "assemble-mp3-btn",
    "assemble-preview-btn",
]


def _dialog_block(dialog_id: str) -> str:
    """取出某個 <dialog> 的內容（到第一個 </dialog> 為止），用來驗元素歸屬。"""
    start = INDEX_HTML.index(f'id="{dialog_id}"')
    end = INDEX_HTML.index("</dialog>", start)
    return INDEX_HTML[start:end]


def _input_block(elem_id: str) -> str:
    """取出含指定 id 的 <input …/> 整段（屬性可能跨行），用來驗屬性值。"""
    pos = INDEX_HTML.index(f'id="{elem_id}"')
    start = INDEX_HTML.rindex("<input", 0, pos)
    return INDEX_HTML[start : INDEX_HTML.index("/>", pos)]


def _css_rule(selector: str) -> str:
    """取出某個 CSS 選擇器的宣告區塊。選擇器必須從行首開始，否則 `.card-text`
    會誤命中 `.card.deleted .card-text` 這種後代選擇器。"""
    m = re.search(r"^" + re.escape(selector) + r"\s*\{([^}]*)\}", APP_CSS, re.MULTILINE)
    assert m, f"app.css 找不到規則：{selector}"
    return m.group(1)


def test_output_menu_buttons_all_present():
    missing = [bid for bid in OUTPUT_BUTTON_IDS if f'id="{bid}"' not in INDEX_HTML]
    assert not missing, f"輸出選單缺按鈕：{missing}"


def test_output_menu_buttons_all_bound_in_app_js():
    missing = [bid for bid in OUTPUT_BUTTON_IDS if f'"#{bid}"' not in APP_JS]
    assert not missing, f"app.js 缺按鈕綁定：{missing}"


def test_no_reels_button_in_index_html():
    # Reels 已移除，確保不被誤加回來
    assert 'id="assemble-reels-btn"' not in INDEX_HTML, "Reels 按鈕不應存在於 UI"


# --- 存檔行為：不打斷編輯節奏 ---


def test_save_does_not_auto_open_output_menu():
    """存檔是高頻操作，成功後不得自動展開「輸出」下拉或閃爍合成鈕。
    唯一允許的 popover 程式呼叫是 close()（開合成設定視窗前收下拉）。"""
    assert not re.search(r"_popover\s*\??\.\s*open\s*\(", APP_JS), (
        "app.js 不應主動展開輸出下拉"
    )
    assert 'classList.add("pulse")' not in APP_JS, "存檔後不應高亮閃爍合成鈕"


# --- 預設值：defaults.yaml 與前端表單必須一致 ---


def test_default_speed_matches_speed_input_value():
    """倍速預設 1.1x：defaults.yaml 與輸出設定表單的預設值不能各走各的，
    否則使用者看到 1.1 但實際套用另一個數字。"""
    defaults = config.load_defaults()
    assert defaults["speed"]["enabled"] is True
    assert defaults["speed"]["factor"] == 1.1
    assert 'value="1.1"' in _input_block("speed-factor")


def test_default_subtitle_font_size_is_60():
    """YT 字幕預設字級 60px（原 48 太小）。"""
    assert config.load_defaults()["subtitle_style"]["font_size"] == 60


def test_no_stale_speed_fallback_in_app_js():
    """app.js 裡的死 fallback 若還停在舊的 1.15，episode.yaml 沒設 speed 的集
    會走到跟 defaults.yaml 不同的倍速 —— 這種漂移很難從畫面上看出來。"""
    assert "1.15" not in APP_JS, "app.js 仍殘留舊倍速預設 1.15"


# --- 時間軸精度 ---


def test_seek_has_subsecond_step():
    """原生 range 不寫 step 預設是 1（整條軸只有 101 格），長片根本對不準。"""
    assert 'step="0.01"' in _input_block("seek")


def test_seek_tooltip_present_and_bound():
    """播放頭要有時間標籤才能精準定位；四態由 app.js 的 showSeekTip/hideSeekTip 切換。"""
    assert 'id="seek-tooltip"' in INDEX_HTML
    assert "showSeekTip" in APP_JS and "hideSeekTip" in APP_JS
    assert ".seek-tooltip" in APP_CSS


# --- 字幕卡資訊層級 ---


def test_card_text_is_the_visual_primary():
    """字幕本文是卡片的主角：字級用 --text-lg（15px），不是預設的 13px。"""
    rule = _css_rule(".card-text")
    assert "var(--text-lg)" in rule, f".card-text 字級被改小了：{rule.strip()}"


# --- 旋轉控制項已移除（後端 rotate 欄位保留）---


def test_rotation_controls_removed_from_ui():
    removed_ids = ["rotate-slider", "rotate-input", "rotate-reset", "rotate-cam-badge"]
    present = [i for i in removed_ids if f'id="{i}"' in INDEX_HTML]
    assert not present, f"旋轉控制項不應存在於 UI：{present}"
    for i in removed_ids:
        assert f'"#{i}"' not in APP_JS, f"app.js 仍綁著已移除的 #{i}"


def test_rotation_backend_compat_preserved():
    """UI 拿掉了，但舊集的 episode.yaml 可能手寫過角度 → 預覽仍要照著轉。"""
    assert "state.rotate" in APP_JS
    assert "applyRotationPreview" in APP_JS


# --- 節目封面併入輸出設定 ---


def test_cover_toggle_lives_in_assemble_setup_modal():
    """封面勾選從影片框下方搬進「合成設定」視窗，跟其他輸出選項放一起。"""
    assert 'id="cover-toggle"' in _dialog_block("assemble-setup-modal")
    assert 'class="framing-row"' not in INDEX_HTML, "舊的 framing-row 應已移除"


# --- 轉字幕：音檔偵測面板 ---


def test_transcribe_track_detect_panel_present():
    """開轉字幕只看到一顆按鈕、不知道系統認出哪幾軌 —— 面板負責把偵測結果攤開。"""
    for elem_id in (
        "transcribe-tracks",
        "transcribe-tracks-summary",
        "transcribe-tracks-list",
        "transcribe-tracks-hint",
    ):
        assert f'id="{elem_id}"' in INDEX_HTML, f"轉字幕偵測面板缺 #{elem_id}"
    assert "renderTranscribeTracks" in APP_JS
    assert ".track-detect" in APP_CSS


def test_transcribe_track_detect_covers_four_states():
    """loading / empty / ready / done 四態：app.js 每一態都要寫得出來，
    ready/done/empty 三態另有配色（loading 用 .track-detect 的預設樣式）。"""
    for st in ("loading", "ready", "done", "empty"):
        assert f'dataset.state = "{st}"' in APP_JS, f"app.js 沒有 {st} 態"
    for st in ("ready", "done", "empty"):
        assert f'[data-state="{st}"]' in APP_CSS, f"app.css 缺 {st} 態配色"
    assert 'data-state="loading"' in INDEX_HTML, "面板初始態應是 loading"


def test_mic_setup_reachable_after_already_configured():
    """設定過分軌的集也要能改回來：按鈕文案切成「重新設定分軌」，
    而不是像舊版那樣（canSetupMics 加了 !hasMics）永遠隱藏、只能手改 episode.yaml。"""
    assert "重新設定分軌" in APP_JS
    assert "const canSetupMics = candidates.length >= 2;" in APP_JS
