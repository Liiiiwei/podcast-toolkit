#!/bin/bash
# podcast-toolkit 打包腳本：把專案包成可在「別台 Apple Silicon Mac」雙擊執行的 .app + DMG。
# 用法：./build_app.sh
#
# 產物：
#   dist/JOIN Podcast Toolkit.app          ── 自帶 Python runtime、ffmpeg、Breeze 語音辨識（含 2.9G 權重）
#   dist/JOIN-Podcast-Toolkit-<ver>.dmg    ── 拖曳安裝用的磁碟映像（內含 app + Applications 捷徑 + 中文安裝說明）
#
# 設計：主 app 走精簡路線（不含 torch），轉字幕交給內附的 Breeze sidecar 子進程。
#       sidecar = 相對化的 CLT Python3.9 framework + arm64 cp39 site-packages + Breeze 程式 + 權重。
#
# ── 先決條件：已組好的 Breeze sidecar 放在 ~/.podcast-toolkit/breeze-stage/ ─────────
#   這份 sidecar「組一次即可」，之後每次改程式重打包都沿用。
#   刻意放在 repo 外：它 3.7G、無法用腳本重建（py-runtime 相對化、arm64 site-packages
#   都是手工組的），放在 git worktree 裡的話那個 worktree 被刪就跟著消失。2026-07-29
#   之前它的實體在 Conductor 的 tashkent worktree、主樹只有 symlink 指過去 —— 刪掉那個
#   workspace 就會靜默壞掉打包。搬到 ~/.podcast-toolkit/（本專案既有的使用者層狀態目錄，
#   config.json / common-glossary.json 也在那）後，任何 worktree 都能打包。
#   找尋順序：$BREEZE_STAGE 環境變數 → ~/.podcast-toolkit/breeze-stage → _pkgbuild/breeze-stage
#   其結構與來源：
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
# sidecar 落腳處：環境變數優先（想暫時換一份時用），否則家目錄的固定家，
# 最後才退回舊的 in-repo 路徑（向後相容，別台機器沿用舊佈局也還能打包）。
STAGE="${BREEZE_STAGE:-}"
if [[ -z "$STAGE" ]]; then
    if [[ -d "$HOME/.podcast-toolkit/breeze-stage" ]]; then
        STAGE="$HOME/.podcast-toolkit/breeze-stage"
    else
        STAGE="_pkgbuild/breeze-stage"
    fi
fi
APP="dist/JOIN Podcast Toolkit.app"
DMG_STAGE="_pkgbuild/dmg-staging"
VER=$(python3 -c "import re;print(re.search(r'CFBundleShortVersionString\"\s*:\s*\"([^\"]+)\"',open('setup_app.py').read()).group(1))" 2>/dev/null || echo "0.1.0")
# build 標記＝日期＋git short SHA（例 20260717-ddfd83d），讓每顆 DMG 檔名/掛載名/
# 安裝說明都認得出是哪天、哪版程式碼打的（同一天多次、同一 commit 重打都不撞名）。
# 非 git 環境或有未提交改動時退回 nogit / 加 -dirty 尾綴。
BUILD_DATE=$(date +%Y%m%d)
GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "nogit")
git diff --quiet 2>/dev/null || GIT_SHA="${GIT_SHA}-dirty"
BUILD_TAG="${BUILD_DATE}-${GIT_SHA}"
DMG="dist/JOIN-Podcast-Toolkit-${VER}-${BUILD_TAG}.dmg"

# ── build 識別碼（單一真實來源）────────────────────────────────────────────────
# 每次 build 都不同：marketing 版號 + git 短碼(+dirty) + build 時間戳，
# 例 0.1.0+g3f9a1c2.20260717T1802。開發期一天可能 build 十次，不用 semver 遞增。
# $BUILD_ID 是這裡唯一的來源，下面兩份檔都從它寫出 → 內容保證一致：
#   _build_info.py            → 會被 py2app 編進記憶體（代表「行程正在跑的版本」）
#   web/static/build-info.json → NoCacheStaticFiles 從磁碟即時送（代表「磁碟上的版本」）
# 兩者刻意分開：前端比對兩邊即可偵測「App 已更新但行程還是舊的」。詳見更新機制需求書。
# 也 export 給 setup_app.py 當 CFBundleVersion（Finder / crash log 認版）。
BUILD_TS=$(date +%Y%m%dT%H%M)
BUILD_ID="${VER}+g${GIT_SHA}.${BUILD_TS}"
export BUILD_ID

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
        echo "  請先依本檔頂端註解組好 ~/.podcast-toolkit/breeze-stage/（組一次即可）。"
        exit 1
    fi
done
echo "  ✓ sidecar 齊備（$(du -sh "$STAGE" | cut -f1)）"

echo "→ [0/5] 寫入 build 識別碼（$BUILD_ID）"
# 兩份都從同一個 $BUILD_ID 寫出，內容保證一致。這兩檔是每次 build 的產物、
# 不進 git（.gitignore 已排除），所以要在 py2app 掃描前先產出。
cat > podcast_toolkit/_build_info.py <<PY
# 本檔由 build_app.sh 於每次打包自動產生 —— 請勿手改、勿提交進 git。
# import 當下即把 BUILD_ID「凍」進記憶體，代表「這個行程正在跑的版本」。
# 對應磁碟端 web/static/build-info.json（內容必須一致）。
BUILD_ID = "$BUILD_ID"
PY
printf '{"build_id": "%s"}\n' "$BUILD_ID" > podcast_toolkit/web/static/build-info.json
echo "  ✓ _build_info.py + static/build-info.json"

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
JOIN Podcast Toolkit 安裝說明
=============================

【系統需求】
• Apple Silicon Mac（M1／M2／M3／M4）
• macOS 11 Big Sur 以上
• 免裝 Python、ffmpeg、Homebrew —— 全部已內附，開箱即用

【安裝步驟】
1. 把左邊的「JOIN Podcast Toolkit.app」拖到右邊的「Applications」資料夾。
2. 到「應用程式」資料夾，第一次啟動請照下方【第一次開啟】做。

【第一次開啟】（重要，只需做一次）
本 App 採 ad-hoc 簽章（未經 Apple 公證），第一次開啟系統會擋下。二選一：

方法 A（滑鼠，推薦）：
  在「JOIN Podcast Toolkit.app」上「按住 Control 點一下」（或按右鍵）→ 選「開啟」→
  跳出警告後再按一次「開啟」。之後就能直接雙擊。

方法 B（終端機）：
  打開「終端機」，貼上這行按 Enter：
    xattr -dr com.apple.quarantine "/Applications/JOIN Podcast Toolkit.app"
  之後直接雙擊即可。

【使用方式】
• 雙擊「JOIN Podcast Toolkit.app」→ 自動開啟瀏覽器進入剪輯介面。
• 若瀏覽器沒自動開，手動輸入畫面上的網址（http://127.0.0.1:…）。

【關於轉字幕】
• 內建 Breeze 語音辨識模型（約 2.9GB），完全離線、不連網、不上傳。
• 這也是 App 較大（約 4GB）的原因。

【疑難排解】
• 若出現「JOIN Podcast Toolkit 已損毀，無法打開」：那是隔離屬性造成，執行上方【方法 B】即可解決。
• 啟動失敗時，錯誤會記在 ~/.podcast-toolkit/launcher.log。
TXT
# build 標記寫進說明檔（版本＋日期＋git SHA），回報問題時報這行就知道是哪顆
printf '\n【版本標記】%s  build %s\n' "$VER" "$BUILD_TAG" >> "$DMG_STAGE/安裝說明.txt"
ditto "$APP" "$DMG_STAGE/JOIN Podcast Toolkit.app"
ln -s /Applications "$DMG_STAGE/Applications"
echo "  ✓ staging 就緒"

echo "→ [5/5] 產生壓縮 DMG（4G，UDZO，需數分鐘）"
rm -f "$DMG"
hdiutil create -volname "JOIN Podcast Toolkit $BUILD_TAG" -srcfolder "$DMG_STAGE" \
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
echo '      xattr -dr com.apple.quarantine "/Applications/JOIN Podcast Toolkit.app"（DMG 內安裝說明已寫）'
