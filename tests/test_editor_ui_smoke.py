"""編輯器前端煙霧測試：鎖住 Reels 全流程 UI 的入口元素。

背景：commit 6d02e7e 誤刪 index.html 的 version-tabs 區塊與「合成 Reels」按鈕，
導致 setupVersionTabs() 綁不到元素、activeVersion 卡在 "yt"，Reels 裁切／片段
面板整條 UI 不可達（後端 assemble 其實支援 reels target）。此檔以純文字斷言
鎖住這些元素，防止再次被改版誤刪。
"""
import re
from pathlib import Path

import podcast_toolkit.web as web_pkg

STATIC = Path(web_pkg.__file__).parent / "static"
INDEX_HTML = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")

# 輸出選單四個動作（對照 README「輸出動作」段：合成 YT／合成 Reels／原速 MP3／5 分鐘預覽）
OUTPUT_BUTTON_IDS = [
    "assemble-yt-btn",
    "assemble-reels-btn",
    "assemble-mp3-btn",
    "assemble-preview-btn",
]


def test_version_tabs_exist_in_index_html():
    # setupVersionTabs() 用 querySelectorAll(".version-tab") + dataset.version 綁定，
    # 兩顆 tab（yt / reels）缺一不可
    assert 'id="version-tabs"' in INDEX_HTML, "version-tabs 容器被刪了"
    tabs = re.findall(
        r'<button[^>]*class="version-tab[^"]*"[^>]*data-version="([^"]+)"',
        INDEX_HTML,
    )
    assert sorted(tabs) == ["reels", "yt"], f"version-tab 應含 yt 與 reels，實際：{tabs}"


def test_version_tabs_selector_matches_app_js():
    # app.js 的綁定選擇器若改名，index.html 要跟著改（反之亦然）
    assert 'querySelectorAll(".version-tab")' in APP_JS


def test_output_menu_buttons_all_present():
    missing = [bid for bid in OUTPUT_BUTTON_IDS if f'id="{bid}"' not in INDEX_HTML]
    assert not missing, f"輸出選單缺按鈕：{missing}"


def test_output_menu_buttons_all_bound_in_app_js():
    # 每顆輸出鈕在 app.js 都要有對應的 click 綁定
    missing = [bid for bid in OUTPUT_BUTTON_IDS if f'"#{bid}"' not in APP_JS]
    assert not missing, f"app.js 缺按鈕綁定：{missing}"


def test_reels_assemble_launch_target():
    # 合成 Reels 必須以 "reels" target 呼叫 launch（後端 assemble_job 依此走 9:16 短版）
    assert re.search(r'launch\(\s*\["reels"\]', APP_JS), "app.js 缺 launch([\"reels\"]) 呼叫"
