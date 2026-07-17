"""編輯器前端煙霧測試：鎖住輸出選單入口元素，防止改版誤刪。

Note: Reels 功能已從 UI 移除（6d02e7e）；本檔只斷言現行仍存在的三個輸出動作。
"""
import re
from pathlib import Path

import podcast_toolkit.web as web_pkg

STATIC = Path(web_pkg.__file__).parent / "static"
INDEX_HTML = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")

# 現行輸出選單（YT 完整版／原速 MP3／5 分鐘預覽）
OUTPUT_BUTTON_IDS = [
    "assemble-yt-btn",
    "assemble-mp3-btn",
    "assemble-preview-btn",
]


def test_output_menu_buttons_all_present():
    missing = [bid for bid in OUTPUT_BUTTON_IDS if f'id="{bid}"' not in INDEX_HTML]
    assert not missing, f"輸出選單缺按鈕：{missing}"


def test_output_menu_buttons_all_bound_in_app_js():
    missing = [bid for bid in OUTPUT_BUTTON_IDS if f'"#{bid}"' not in APP_JS]
    assert not missing, f"app.js 缺按鈕綁定：{missing}"


def test_no_reels_button_in_index_html():
    # Reels 已移除，確保不被誤加回來
    assert 'id="assemble-reels-btn"' not in INDEX_HTML, "Reels 按鈕不應存在於 UI"
