#!/bin/bash
# podcast-toolkit 打包腳本：把專案包成可在「別台 Apple Silicon Mac」雙擊執行的 .app + DMG。
# 用法：./build_app.sh
#
# 產物：
#   dist/Podcast.app                  ── 自帶 Python runtime、ffmpeg、Breeze 語音辨識（含 2.9G 權重）
#   dist/Podcast-Toolkit-<ver>.dmg    ── 拖曳安裝用的磁碟映像（內含 app + Applications 捷徑 + 中文安裝說明）
#
# 設計：主 app 走精簡路線（不含 torch），轉字幕交給內附的 Breeze sidecar 子進程。
#       sidecar = 相對化的 CLT Python3.9 framework + arm64 cp39 site-packages + Breeze 程式 + 權重。
#
# ── 先決條件：已組好的 Breeze sidecar 放在 _pkgbuild/breeze-stage/ ──────────────────
#   這份 sidecar「組一次即可」，之後每次改程式重打包都沿用。其結構與來源：
#     _pkgbuild/breeze-stage/
#       py-runtime/            ← 複製 CLT 的 Python3.framework/Versions/3.9 並相對化（install_name 改 @rpath）
#                                 來源：/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework
#       site-packages/         ← arm64 cp39 wheels：torch、openai-whisper、numpy、jieba…（用 Breeze 專案 .venv 裝好後複製）
#       make_subtitle.py       ┐
#       srt_segment.py         │← 從 ~/Developer/breeze subtitle/Breeze-ASR-25/ 複製
#       rhythm_segment.py      │
#       dict.txt.big           ┘
#       cache/whisper/breeze-asr-25.pt  ← 2.9G 權重（whisper 快取；SHA 對得上就離線唯讀載入，不會重下載）
#   若缺這個資料夾，下面第 2 步會中止並提示。
# ─────────────────────────────────────────────────────────────────────────────────
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
STAGE="_pkgbuild/breeze-stage"
APP="dist/Podcast.app"
DMG_STAGE="_pkgbuild/dmg-staging"
VER=$(python3 -c "import re;print(re.search(r'CFBundleShortVersionString\"\s*:\s*\"([^\"]+)\"',open('setup_app.py').read()).group(1))" 2>/dev/null || echo "0.1.0")
DMG="dist/Podcast-Toolkit-${VER}.dmg"

echo "→ 檢查作業系統與架構"
if [[ "$(uname)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    echo "✗ 這支腳本只在 Apple Silicon（arm64）macOS 上打包"
    exit 1
fi
echo "  ✓ macOS arm64"

echo "→ 檢查 Breeze sidecar（$STAGE）"
for part in py-runtime/bin/python3.9 site-packages make_subtitle.py cache/whisper/breeze-asr-25.pt; do
    if [[ ! -e "$STAGE/$part" ]]; then
        echo "✗ sidecar 缺件：$STAGE/$part"
        echo "  請先依本檔頂端註解組好 _pkgbuild/breeze-stage/（組一次即可）。"
        exit 1
    fi
done
echo "  ✓ sidecar 齊備（$(du -sh "$STAGE" | cut -f1)）"

echo "→ [1/5] py2app 打包精簡主 app"
rm -rf build "$APP"
PYTHONPATH=. python3 setup_app.py py2app >/dev/null
echo "  ✓ $APP（$(du -sh "$APP" | cut -f1)，尚未含 Breeze）"

echo "→ [2/5] 注入 Breeze sidecar 到 Contents/Resources/breeze"
ditto "$STAGE" "$APP/Contents/Resources/breeze"
echo "  ✓ 注入完成（app 現為 $(du -sh "$APP" | cut -f1)）"

echo "→ [3/5] ad-hoc 簽章整個 bundle（含巢狀 python3.9 / torch .so）"
codesign --deep --force -s - "$APP"
codesign --verify --deep --strict "$APP" && echo "  ✓ 簽章驗證通過"

echo "→ [4/5] 組 DMG staging（app + Applications 捷徑 + 中文安裝說明）"
rm -rf "$DMG_STAGE"; mkdir -p "$DMG_STAGE"
cat > "$DMG_STAGE/安裝說明.txt" <<'TXT'
Podcast Toolkit 安裝說明
========================

【系統需求】
• Apple Silicon Mac（M1／M2／M3／M4）
• macOS 11 Big Sur 以上
• 免裝 Python、ffmpeg、Homebrew —— 全部已內附，開箱即用

【安裝步驟】
1. 把左邊的「Podcast.app」拖到右邊的「Applications」資料夾。
2. 到「應用程式」資料夾，第一次啟動請照下方【第一次開啟】做。

【第一次開啟】（重要，只需做一次）
本 App 採 ad-hoc 簽章（未經 Apple 公證），第一次開啟系統會擋下。二選一：

方法 A（滑鼠，推薦）：
  在 Podcast.app 上「按住 Control 點一下」（或按右鍵）→ 選「開啟」→
  跳出警告後再按一次「開啟」。之後就能直接雙擊。

方法 B（終端機）：
  打開「終端機」，貼上這行按 Enter：
    xattr -dr com.apple.quarantine /Applications/Podcast.app
  之後直接雙擊即可。

【使用方式】
• 雙擊 Podcast.app → 自動開啟瀏覽器進入剪輯介面。
• 若瀏覽器沒自動開，手動輸入畫面上的網址（http://127.0.0.1:…）。

【關於轉字幕】
• 內建 Breeze 語音辨識模型（約 2.9GB），完全離線、不連網、不上傳。
• 這也是 App 較大（約 4GB）的原因。

【疑難排解】
• 若出現「Podcast 已損毀，無法打開」：那是隔離屬性造成，執行上方【方法 B】即可解決。
• 啟動失敗時，錯誤會記在 ~/.podcast-toolkit/launcher.log。
TXT
ditto "$APP" "$DMG_STAGE/Podcast.app"
ln -s /Applications "$DMG_STAGE/Applications"
echo "  ✓ staging 就緒"

echo "→ [5/5] 產生壓縮 DMG（4G，UDZO，需數分鐘）"
rm -f "$DMG"
hdiutil create -volname "Podcast Toolkit" -srcfolder "$DMG_STAGE" \
    -fs HFS+ -format UDZO -imagekey zlib-level=6 -ov "$DMG" >/dev/null
echo "  ✓ $DMG（$(du -sh "$DMG" | cut -f1)）"

echo "→ 清理 DMG staging（省 4G 暫存）"
rm -rf "$DMG_STAGE"

echo ""
echo "✅ 打包完成"
echo "   App ：$APP"
echo "   DMG ：$DMG"
echo ""
echo "   ⚠ ad-hoc 簽章未公證：別台 Mac 第一次開啟要「右鍵→開啟」或"
echo "      xattr -dr com.apple.quarantine /Applications/Podcast.app（DMG 內安裝說明已寫）"
