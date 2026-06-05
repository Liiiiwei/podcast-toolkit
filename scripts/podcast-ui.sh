#!/bin/bash
# 啟動 podcast-toolkit 本機 dashboard 並開瀏覽器
# 規則：
#   1. 若已有活著的 instance → 直接開瀏覽器（讀 ~/.podcast-toolkit/.server.lock）
#   2. 若 lockfile 殘留死 PID → 自動清掉，重啟
#   3. 啟動採 `python3 -c "from podcast_toolkit import edit; edit.run_dashboard()"`
#      （已驗證可背景化；直接跑 `cli ui` 背景化會立刻 exit，這是已知 bug 的暫解）
#   4. 啟動後 polling lockfile 拿真實 port，再 HTTP probe 健康度，最後 open
#   5. python 進程提早 exit（死訊偵測）→ 立刻失敗印 log，不空等到 timeout

# 故意不開 set -u；macOS 預設 bash 3.2 在 set -u + $() 空字串時會誤報 unbound
# （所有變數仍會顯式初始化，可讀性不受影響）

PROJECT_DIR="$HOME/Desktop/vibe-coding playground/podcast-toolkit"
LOCK_PATH="$HOME/.podcast-toolkit/.server.lock"
LAUNCH_LOG="$HOME/.podcast-toolkit/launcher.log"
DEATH_MARKER="[podcast-ui.sh] SERVER_EXITED"
TIMEOUT_SEC=20
# homebrew 優先；/usr/local/bin 可能有 python 3.6 老符號連結會吃掉 import
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

mkdir -p "$(dirname "$LAUNCH_LOG")"

# ---- helpers ----------------------------------------------------------------

# 從 lockfile 讀 port（line 2）；無檔或格式錯回空字串
read_lock_port() {
  [ -f "$LOCK_PATH" ] || return 0
  sed -n '2p' "$LOCK_PATH" 2>/dev/null | tr -d '[:space:]'
}

# 從 lockfile 讀 PID（line 1）；無檔或格式錯回空字串
read_lock_pid() {
  [ -f "$LOCK_PATH" ] || return 0
  sed -n '1p' "$LOCK_PATH" 2>/dev/null | tr -d '[:space:]'
}

# 檢查 PID 是否還活著
pid_alive() {
  local pid=$1
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

# HTTP probe；給定 port，2 秒內回 200 才算就緒
# 用 / 而非 /api/* — API 要求選集才回 200，dashboard.html 任何狀態下都回 200
http_ok() {
  local port=$1
  curl -sf --max-time 2 "http://127.0.0.1:$port/" -o /dev/null
}

# ---- 1. 已有活著的 instance → 直接開瀏覽器 -----------------------------------

existing_pid=$(read_lock_pid)
existing_port=$(read_lock_port)
if pid_alive "$existing_pid" && [ -n "$existing_port" ] && http_ok "$existing_port"; then
  echo "→ 已有 podcast server 在跑（PID=${existing_pid}, port=${existing_port}），開瀏覽器…"
  open "http://127.0.0.1:${existing_port}"
  exit 0
fi

# ---- 2. 殘留死 lockfile 自動清掉 ---------------------------------------------

if [ -f "$LOCK_PATH" ] && ! pid_alive "$existing_pid"; then
  echo "→ 偵測到殘留 lockfile（PID=${existing_pid} 已死），清除"
  rm -f "$LOCK_PATH"
fi

# ---- 3. 依賴自我檢查 ---------------------------------------------------------

if ! python3 -c "import fastapi, uvicorn, podcast_toolkit" 2>/dev/null; then
  echo "✗ 缺套件（fastapi / uvicorn / podcast_toolkit）"
  echo "  請先跑：cd \"$PROJECT_DIR\" && pip install -e ."
  exit 1
fi

# ---- 4. 背景啟動 + 死訊偵測 ---------------------------------------------------

cd "$PROJECT_DIR" || exit 1
: >"$LAUNCH_LOG"

# 用 `-c` 形式避開 `cli ui` 背景化會立刻 exit 的 bug
# 包一層 bash 在 python 結束時補寫死訊標記
nohup bash -c "
  python3 -c 'from podcast_toolkit import edit; edit.run_dashboard()'
  echo \"$DEATH_MARKER code=\$?\"
" >>"$LAUNCH_LOG" 2>&1 &
launcher_pid=$!

# ---- 5. Polling lockfile + HTTP probe ----------------------------------------

for _ in $(seq 1 $((TIMEOUT_SEC * 2))); do
  # 5a. 進程已死 → 立刻失敗
  if grep -qF "$DEATH_MARKER" "$LAUNCH_LOG" 2>/dev/null; then
    echo "✗ podcast server 啟動失敗（python 進程已退出）。log 末段："
    tail -15 "$LAUNCH_LOG"
    echo "完整 log：$LAUNCH_LOG"
    exit 1
  fi
  # 5b. lockfile 寫入 + HTTP 就緒 → 開瀏覽器
  port=$(read_lock_port)
  if [ -n "$port" ] && http_ok "$port"; then
    echo "→ podcast server 就緒（PID=${launcher_pid}, port=${port}），開瀏覽器…"
    open "http://127.0.0.1:${port}"
    exit 0
  fi
  sleep 0.5
done

echo "✗ 啟動逾時（${TIMEOUT_SEC} 秒未就緒）。log 末段："
tail -15 "$LAUNCH_LOG"
echo "完整 log：$LAUNCH_LOG"
exit 1
