"""編輯器前端煙霧測試：鎖住 UI 的關鍵元素與行為，防止改版誤刪。

「靜態字串斷言」版：讀 index.html / app.js / app.css 的原始碼，比對元素 id、屬性與 CSS 規則。
測不到互動，但足以擋住「元素被誤刪」「前後端預設值漂移」這兩類回歸。
真的會開瀏覽器點下去的門檻在 `tests/test_editor_browser_smoke.py`（Phase 3 拆檔的驗收門檻）。

APP_JS 是**所有編輯器前端模組的串接**，不是單一檔案 —— 拆檔（Phase 3）會讓程式碼換檔，
只讀 app.js 的話「東西只是搬家」會被誤判成「東西被刪了」。
新增模組時加進 conftest.py 的 EDITOR_JS，漏加會被下面第一個測試擋下。

Note: Reels 功能已從 UI 移除（6d02e7e）；旋轉控制項也已移除（保留後端 rotate 欄位）。
"""
import re
from pathlib import Path

import podcast_toolkit.web as web_pkg
from podcast_toolkit import config

from .conftest import EDITOR_JS, editor_js_source

STATIC = Path(web_pkg.__file__).parent / "static"
INDEX_HTML = (STATIC / "index.html").read_text(encoding="utf-8")
APP_CSS = (STATIC / "app.css").read_text(encoding="utf-8")
BUILD_SH = (Path(__file__).parents[1] / "build_app.sh").read_text(encoding="utf-8")

# 下面的斷言一律對「所有編輯器模組串接後的全文」做，才不會把搬家誤判成刪除。
# 模組清單在 conftest.py（不只這個檔要用，見該處註解）。
APP_JS = editor_js_source()

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


def test_editor_js_list_covers_every_module_app_js_imports():
    """EDITOR_JS 漏掉新抽出的模組，本檔所有斷言就會退回「只看 app.js」——
    程式碼只是搬家卻被判成被刪，或反過來，該擋的刪除擋不住。
    所以直接拿 app.js 自己的 import 當事實來源，漏登記就在這裡紅。"""
    imported = set(re.findall(r'from\s+"\./([\w.-]+\.js)"', (STATIC / "app.js").read_text("utf-8")))
    missing = sorted(imported - set(EDITOR_JS))
    assert not missing, f"app.js 匯入了這些模組但沒登記進 EDITOR_JS：{missing}"


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


def test_subtitle_shift_uses_shared_save_recovery():
    """字幕偏移也必須走共用存檔通道，避免 HTTP 409 時漏掉復原流程。"""
    start = APP_JS.index("function setupSrtShift()")
    end = APP_JS.index("async function loadFiles()", start)
    block = APP_JS[start:end]
    assert "await postSave({ subtitle_offset_sec: offset })" in block
    assert 'fetch("/api/save"' not in block


def test_shared_save_sends_episode_identity():
    """共用存檔請求要帶目前集數識別，後端才能拒絕過期分頁。

    postSave 已搬進 api.js（Phase 3 拆檔）；用「到下一個函式定義為止」界定區塊，
    不再依賴它與 setSaveBtnLabel 在同一檔相鄰，避免模組串接順序改變就誤判。
    """
    start = APP_JS.index("function postSave(payload)")
    nxt = re.search(r"\n(?:export )?function ", APP_JS[start + 1:])
    end = start + 1 + nxt.start() if nxt else len(APP_JS)
    block = APP_JS[start:end]
    assert "episode_dir: state.episodeDir" in block


def test_build_marks_untracked_files_dirty():
    """打包識別必須包含未追蹤檔案，避免 App 內容與 Git 版本不一致。"""
    assert "git status --porcelain --untracked-files=normal" in BUILD_SH


def test_transcribe_poll_ignores_status_from_another_episode():
    """背景工作狀態帶錯集時，前端不可把舊集結果顯示在目前集。"""
    start = APP_JS.index("async function _pollTranscribeOnce()")
    end = APP_JS.index("async function finishTranscribe", start)
    block = APP_JS[start:end]
    assert "s.episode_dir !== state.episodeDir" in block


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


def test_mic_srt_existing_reaches_frontend_state():
    """後端算好的「哪幾軌已轉過」要真的進 state —— 漏接的話標記永遠顯示「未轉」。

    state 一律 camelCase、後端欄位 snake_case，中間靠 loadEpisodeState 逐欄轉。
    這條漏了三年也不會壞頁面（`|| []` 吞掉 undefined），只會靜靜地全部標成未轉，
    所以用靜態斷言把「有轉」和「讀對名字」兩件事一起釘住。
    """
    assert "state.micSrtExisting = Array.isArray(data.mic_srt_existing)" in APP_JS, (
        "loadEpisodeState 沒把 mic_srt_existing 接進 state"
    )
    assert "state.mic_srt_existing" not in APP_JS, (
        "還有地方讀 snake_case 的 state.mic_srt_existing（state 是 camelCase）"
    )
    assert APP_JS.count("new Set(state.micSrtExisting || [])") == 3, (
        "分軌 modal 該有三處讀 micSrtExisting（列表、覆寫警告、只選未轉）"
    )


# --- D2：字幕樣式面板（接出後端 build_style_string 讀得到的參數） ---

# 面板上每個欄位的 data-style-key，就是 episode.yaml subtitle_style 的鍵
_STYLE_KEY_RE = re.compile(r'data-style-key="([a-z_]+)"')
# api.js 裡送給後端的鍵白名單
_PAYLOAD_KEYS_RE = re.compile(
    r"const SUBTITLE_STYLE_KEYS = \[(.*?)\];", re.DOTALL
)


def _panel_style_keys() -> set:
    return set(_STYLE_KEY_RE.findall(INDEX_HTML))


def _payload_style_keys() -> set:
    m = _PAYLOAD_KEYS_RE.search(APP_JS)
    assert m, "api.js 找不到 SUBTITLE_STYLE_KEYS —— 存檔白名單被改名或刪掉了"
    return set(re.findall(r'"([a-z_]+)"', m.group(1)))


def test_caption_style_panel_exists():
    """面板本體與觸發鈕在（誤刪就等於整組樣式參數又退回只能手改 yaml）。"""
    for el_id in ("cap-style-btn", "cap-style-panel", "cap-style-scope"):
        assert f'id="{el_id}"' in INDEX_HTML, f"字幕樣式面板缺 #{el_id}"
    assert "setupCaptionStyle()" in APP_JS, "面板沒有被初始化"


def test_caption_style_panel_covers_backend_style_keys():
    """面板 + 字級 ± 鈕要蓋滿 build_style_string 讀得到的全部 9 個鍵。
    少一個＝那個參數只能手改 yaml（D2 要解的就是這件事）。"""
    src = (Path(__file__).parents[1] / "podcast_toolkit" / "assemble.py").read_text(
        encoding="utf-8"
    )
    m = re.search(r"def build_style_string\(.*?\n    return", src, re.DOTALL)
    assert m
    backend_keys = set(re.findall(r"style\['([a-z_]+)'\]", m.group(0)))
    # font_size 不在面板裡：它有專屬的 ± 控制項（刻意不做第二個入口）
    ui_keys = _panel_style_keys() | {"font_size"}
    assert backend_keys <= ui_keys, (
        f"build_style_string 讀得到但 UI 沒開放的樣式鍵：{sorted(backend_keys - ui_keys)}"
    )


def test_caption_style_panel_keys_are_all_saved():
    """面板上調得到的每個鍵都必須在 api.js 的送出白名單裡。
    後端 normalize_style 對未知鍵是靜默過濾 —— 漏列就是「面板上改得動、存了卻沒進 yaml」。"""
    panel = _panel_style_keys()
    payload = _payload_style_keys()
    assert panel <= payload, (
        f"面板有欄位但沒送出去：{sorted(panel - payload)}"
    )
    assert payload <= panel | {"font_size"}, (
        f"送出白名單有 UI 調不到的鍵：{sorted(payload - panel - {'font_size'})}"
    )


def test_caption_style_panel_has_empty_and_error_states():
    """四態：empty（沒開集）與 error（非法輸入）要有可見的 DOM 表現，不能只吞在 console。"""
    assert 'id="cap-style-empty"' in INDEX_HTML
    assert 'id="cap-style-err"' in INDEX_HTML
    assert "setCaptionStyleError(" in APP_JS
    # error 分支必須 return（不把非法值寫進 state）
    commit = re.search(
        r"function commitCaptionStyleField\(.*?\n\}", APP_JS, re.DOTALL
    )
    assert commit, "commitCaptionStyleField 不見了"
    assert commit.group(0).count("return;") >= 3, (
        "非法輸入的分支沒有 return —— NaN / 空字串會被靜默寫進樣式"
    )


def test_caption_style_panel_disclaims_title_cards():
    """force_style 蓋不到標題卡的文字（卡片文字走 override tag，見 assemble.py），
    面板上要講清楚，不然使用者會以為調了沒效。"""
    assert "標題卡" in INDEX_HTML.split('id="cap-style-panel"')[1].split("</div>")[0] or (
        "標題卡的文字不吃這裡的樣式" in INDEX_HTML
    )
