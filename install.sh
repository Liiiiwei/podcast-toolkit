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

echo "→ 安裝 Python 套件 fastapi / uvicorn / python-multipart / pytest / requests / opencc / numpy"
PY_PKGS="fastapi uvicorn[standard] python-multipart pytest requests opencc-python-reimplemented numpy"
if ! pip3 install --user $PY_PKGS >/dev/null 2>&1; then
    echo "  ⚠ pip3 install 一般模式失敗，改用 --break-system-packages 重試"
    pip3 install --user --break-system-packages $PY_PKGS
fi
echo "  ✓ fastapi / uvicorn / python-multipart / pytest / requests / opencc / numpy"

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

cat <<'EOF'

✅ 安裝完成。

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

選裝：本機 whisper 轉錄字幕（模型約 3GB）
  pip3 install --user openai-whisper
  # 跑：python3 -m whisper <audio.mp4> --model large-v3 --language zh --output_format srt

EOF
