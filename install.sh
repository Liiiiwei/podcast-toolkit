#!/bin/bash
# podcast-toolkit 安裝腳本（僅支援 macOS）
# 用法：./install.sh
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "→ 檢查作業系統"
if [[ "$(uname)" != "Darwin" ]]; then
    echo "✗ install.sh 目前僅支援 macOS（其他系統請手動跟著 README 安裝）"
    exit 1
fi
echo "  ✓ macOS"

echo "→ 檢查 Python 3.9+"
if ! command -v python3 >/dev/null; then
    echo "✗ 找不到 python3。請先裝 Python 3.9+："
    echo "    brew install python3"
    echo "  或從 python.org 下載安裝。"
    exit 1
fi
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
    echo "✗ Python 版本太舊：$PY_VER（需要 3.9+）"
    exit 1
fi
echo "  ✓ Python $PY_VER"

echo "→ 檢查 Homebrew"
if ! command -v brew >/dev/null; then
    echo "✗ 找不到 brew。請先裝 Homebrew："
    echo '    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    exit 1
fi
echo "  ✓ Homebrew"

echo "→ 安裝 ffmpeg（已裝跳過）"
if ! command -v ffmpeg >/dev/null; then
    brew install ffmpeg
else
    echo "  ✓ ffmpeg 已裝"
fi

echo "→ 安裝 Python 套件 pyyaml"
if ! pip3 install --user pyyaml >/dev/null 2>&1; then
    echo "  ⚠ pip3 install 一般模式失敗，改用 --break-system-packages 重試"
    pip3 install --user --break-system-packages pyyaml
fi
echo "  ✓ pyyaml"

echo "→ 安裝 Python 套件 fastapi / uvicorn / python-multipart / pytest / requests / opencc / numpy / google-genai / eval-type-backport"
# eval-type-backport：Python 3.9 跑 fastapi 的 `X | Y` 型別標註必裝，少了它 dashboard 在路由註冊期就炸（整個介面起不來）
PY_PKGS="fastapi uvicorn[standard] python-multipart pytest requests opencc-python-reimplemented numpy google-genai eval-type-backport"
if ! pip3 install --user $PY_PKGS >/dev/null 2>&1; then
    echo "  ⚠ pip3 install 一般模式失敗，改用 --break-system-packages 重試"
    pip3 install --user --break-system-packages $PY_PKGS
fi
echo "  ✓ fastapi / uvicorn / python-multipart / pytest / requests / opencc / numpy / google-genai / eval-type-backport"

echo "→ 選擇 podcast CLI symlink 位置"
TARGETS=("/opt/homebrew/bin" "/usr/local/bin" "$HOME/.local/bin" "$HOME/bin")
TARGET=""
for t in "${TARGETS[@]}"; do
    if [ -d "$t" ] && [ -w "$t" ]; then
        TARGET="$t"
        break
    fi
done
if [ -z "$TARGET" ]; then
    mkdir -p "$HOME/.local/bin"
    TARGET="$HOME/.local/bin"
    echo "  ⚠ 預設目錄都不可寫，已建 $HOME/.local/bin"
    echo "    記得加進 PATH：echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc"
fi
LINK="$TARGET/podcast"
if [ -L "$LINK" ] || [ -e "$LINK" ]; then
    rm "$LINK"
fi
ln -s "$ROOT/bin/podcast" "$LINK"
echo "  ✓ symlink: $LINK → $ROOT/bin/podcast"

echo "→ 驗證 podcast --help"
if "$LINK" --help >/dev/null 2>&1; then
    echo "  ✓ podcast --help 跑得通"
else
    echo "  ✗ podcast --help 失敗。檢查 PATH 是否含 $TARGET："
    echo "    echo \$PATH"
    exit 1
fi

echo "→ 設定 Breeze 轉字幕後端（本地 AI 聽打、免金鑰；強烈建議，但失敗不擋核心安裝）"
# Breeze 是公開 repo（客製 make_subtitle.py＋節奏斷句），toolkit 用 subprocess 呼叫
# 它自己的 .venv。裝到 toolkit 認得的預設路徑 → _breeze_dir() 直接找到，免寫 config。
# 整段包在 set +e：Breeze 裝不起來時只警告（可退回本機 mlx-whisper），不讓 install 中斷。
set +e
BREEZE_PARENT="$HOME/Developer/breeze subtitle"
BREEZE_DIR="$BREEZE_PARENT/Breeze-ASR-25"
BREEZE_OK=0
if [ ! -f "$BREEZE_DIR/make_subtitle.py" ]; then
    echo "  → 下載 Breeze 客製版（公開 repo，含子模組 whisper-patch，免 gh／免登入）"
    mkdir -p "$BREEZE_PARENT"
    git clone --recurse-submodules https://github.com/Liiiiwei/breeze-podcast-subtitle.git "$BREEZE_DIR"
else
    echo "  ✓ Breeze 已存在 → 更新"
    ( cd "$BREEZE_DIR" && git pull --ff-only >/dev/null 2>&1; \
      git submodule update --init --recursive >/dev/null 2>&1 )
fi
if [ -f "$BREEZE_DIR/make_subtitle.py" ]; then
        echo "  → 建 Breeze 專用 venv 並裝相依（首次裝 torch 較久，約數分鐘）"
        [ -x "$BREEZE_DIR/.venv/bin/python" ] || python3 -m venv "$BREEZE_DIR/.venv"
        BPIP="$BREEZE_DIR/.venv/bin/pip"
        "$BPIP" install --quiet --upgrade pip
        # 關鍵：裝『打過補丁的 whisper』(submodule)，它才認得 breeze-asr-25 模型名；
        # 千萬別用 PyPI 的 openai-whisper（那版不認得，一鍵 Breeze 會炸）。torch/numpy 隨它一起裝。
        "$BPIP" install --quiet "$BREEZE_DIR/third_party/whisper-patch-breeze"
        "$BPIP" install --quiet jieba
        # 繁中 jieba 詞典（在 repo 是 gitignore，另抓；沒有也能轉，斷句品質略降）
        if [ ! -f "$BREEZE_DIR/dict.txt.big" ]; then
            echo "  → 下載繁中 jieba 詞典 dict.txt.big（8.5MB）"
            curl -fsSL -o "$BREEZE_DIR/dict.txt.big" \
                "https://github.com/fxsjy/jieba/raw/master/extra_dict/dict.txt.big" \
                || echo "    ⚠ 詞典下載失敗（不影響能否轉字幕，斷句略降）"
        fi
        # 冒煙測試：patched whisper 有沒有把 breeze-asr-25 註冊進去（只查名單、不下載模型）
        if "$BREEZE_DIR/.venv/bin/python" -c \
            "import whisper,sys; sys.exit(0 if 'breeze-asr-25' in whisper.available_models() else 1)" 2>/dev/null; then
            echo "  ✓ Breeze 就緒（首次轉字幕會自動下載 2.9G 模型到 ~/.cache/whisper）"
            BREEZE_OK=1
        else
            echo "  ⚠ Breeze venv 裝好了但認不到 breeze-asr-25；檢查 whisper-patch 子模組是否有抓到"
        fi
    else
        echo "  ⚠ Breeze 下載失敗（跳過；稍後重跑 ./install.sh 可補）"
    fi
set -e

echo "→ 生成雙擊啟動 app（本機 osacompile 生成 → 無 quarantine、不會被 Gatekeeper 擋）"
LAUNCH_SH="$ROOT/scripts/podcast-ui.sh"
# app 安裝位置：優先 /Applications，不可寫則退回 ~/Applications
APP_DIR="/Applications"
if [ ! -w "$APP_DIR" ]; then
    APP_DIR="$HOME/Applications"
    mkdir -p "$APP_DIR"
fi
APP_PATH="$APP_DIR/Podcast.app"
# 舊版存在 → 移到垃圾桶（不用 rm -rf）；同名衝突用 PID 區隔
if [ -e "$APP_PATH" ]; then
    mv "$APP_PATH" "$HOME/.Trash/Podcast.app.old.$$" 2>/dev/null || mv "$APP_PATH" "${APP_PATH}.old.$$"
fi
# 把本機絕對路徑烤進 AppleScript（per-machine 生成、不進 git，故寫死無妨）
# do shell script 雙擊啟動時 CWD=/，podcast-ui.sh 內會自己 cd 進 repo 根
TMP_SCPT="$(mktemp -t podcast-launcher).applescript"
cat >"$TMP_SCPT" <<APPLESCRIPT
-- 由 install.sh 本機生成：啟動 podcast dashboard 並開瀏覽器
do shell script quoted form of "$LAUNCH_SH"
APPLESCRIPT
if osacompile -o "$APP_PATH" "$TMP_SCPT" >/dev/null 2>&1; then
    codesign --force -s - "$APP_PATH" >/dev/null 2>&1 || true
    echo "  ✓ 啟動 app：$APP_PATH"
    APP_OK=1
else
    echo "  ⚠ 啟動 app 生成失敗（不影響 CLI）；改用 podcast ui 也可開介面"
    APP_OK=0
fi
mv "$TMP_SCPT" "$HOME/.Trash/" 2>/dev/null || true

cat <<'EOF'

✅ 安裝完成。
EOF
if [ "${APP_OK:-0}" = "1" ]; then
    echo "  雙擊開介面：$APP_PATH（或 Spotlight 搜 Podcast）"
fi
if [ "${BREEZE_OK:-0}" = "1" ]; then
    echo "  轉字幕後端：Breeze 已就緒（編輯器裡按『一鍵 Breeze』即可，首次會下載 2.9G 模型）"
else
    echo "  ⚠ Breeze 尚未就緒（上方有原因）。排除後重跑 ./install.sh 即可補上；"
    echo "    在此之前可先用本機 mlx-whisper 轉（不標講者）。"
fi
cat <<'EOF'

下一步：
  1. 開新集資料夾（命名 YYYYMMDD 集名，中間要空格）：
       mkdir "$HOME/Downloads/20260601 集名"
  2. cd 進去：
       cd "$HOME/Downloads/20260601 集名"
  3. 跑 init 建子目錄 + symlink + 範本：
       podcast init
  4. 把錄音 / 精剪成片 / whisper 字幕放進對應子資料夾
  5. 跑全流程：
       podcast resegment
       podcast assemble

選裝：本機 mlx-whisper 轉字幕（Apple Silicon、免金鑰、不標講者；Breeze 就緒後不需要）
  pip3 install --user mlx-whisper

EOF
