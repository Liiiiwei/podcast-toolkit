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

# 由腳本自身位置推導 repo 根目錄（scripts/ 的上一層），不寫死絕對路徑 → 可攜到任何機器/路徑
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOCK_PATH="$HOME/.podcast-toolkit/.server.lock"
LAUNCH_LOG="$HOME/.podcast-toolkit/launcher.log"
DEATH_MARKER="[podcast-ui.sh] SERVER_EXITED"
TIMEOUT_SEC=20
# homebrew 優先；/usr/local/bin 可能有 python 3.6 老符號連結會吃掉 import
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

mkdir -p "$(dirname "$LAUNCH_LOG")"

# 先進專案目錄再做任何 python import：applet 雙擊啟動時 do shell script 的 CWD 是 /，
# 而 podcast_toolkit 未 pip 安裝、只能靠 CWD 在 sys.path，故依賴檢查前必須先 cd
cd "$PROJECT_DIR" || exit 1

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

# HTTP probe；給定 port，回 200 *且* body 完整傳完才算就緒
# 用 / 而非 /api/* — API 要求選集才回 200，dashboard.html 任何狀態下都回 200
# -f：HTTP error 視為失敗；--max-time 4：壞掉/卡住的 server 會即刻 partial(exit 18) 而非空等
# （曾踩雷：半死 server 會回 200 + content-length 但 body 0 bytes，curl -f 會以 exit 18 失敗 → 正確判定不可用）
http_ok() {
  local port=$1
  curl -sf --max-time 4 "http://127.0.0.1:$port/" -o /dev/null
}

# 找出「真的裝了 fastapi / uvicorn / podcast_toolkit」的 python3。
# 不能只信 PATH 順序：本機 homebrew python3 可能沒裝套件、系統 /usr/bin/python3 才有。
# 逐一試 import，回傳第一個能跑的解譯器絕對路徑；都不行回非 0。
# 須在 cd 進 PROJECT_DIR 後呼叫（podcast_toolkit 靠 CWD 在 sys.path）。
find_python() {
  local py
  for py in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 "$(command -v python3 2>/dev/null)"; do
    [ -n "$py" ] && [ -x "$py" ] || continue
    if "$py" -c "import fastapi, uvicorn, podcast_toolkit" 2>/dev/null; then
      echo "$py"
      return 0
    fi
  done
  return 1
}

# ---- 1. 已有活著的 instance → 直接開瀏覽器 -----------------------------------

existing_pid=$(read_lock_pid)
existing_port=$(read_lock_port)
if pid_alive "$existing_pid" && [ -n "$existing_port" ] && http_ok "$existing_port"; then
  echo "→ 已有 podcast server 在跑（PID=${existing_pid}, port=${existing_port}），開瀏覽器…"
  open "http://127.0.0.1:${existing_port}"
  exit 0
fi

# ---- 2. 殘留死 lockfile / 半死 server 清理 -----------------------------------
# 走到這代表 step 1 沒過：要嘛 PID 已死、要嘛 PID 活著但 http_ok 失敗（半死/卡住）。
# 兩種都得清乾淨再重啟——否則新 python 進程會因 lockfile 還在而 defer 回壞掉的 instance。

if [ -f "$LOCK_PATH" ]; then
  if ! pid_alive "$existing_pid"; then
    echo "→ 偵測到殘留 lockfile（PID=${existing_pid} 已死），清除"
    rm -f "$LOCK_PATH"
  else
    echo "→ 既有 server（PID=${existing_pid}, port=${existing_port}）無回應/半死，結束它再重啟"
    kill "$existing_pid" 2>/dev/null
    for _ in $(seq 1 10); do pid_alive "$existing_pid" || break; sleep 0.3; done
    pid_alive "$existing_pid" && kill -9 "$existing_pid" 2>/dev/null
    rm -f "$LOCK_PATH"
  fi
fi

# ---- 3. 找出有裝套件的 python3 -----------------------------------------------

PYTHON_BIN="$(find_python)"
if [ -z "$PYTHON_BIN" ]; then
  echo "✗ 找不到裝了 fastapi / uvicorn / podcast_toolkit 的 python3"
  echo "  請先跑：cd \"$PROJECT_DIR\" && pip3 install -e ."
  exit 1
fi
echo "→ 使用 python：$PYTHON_BIN"

# ---- 4. 背景啟動 + 死訊偵測 ---------------------------------------------------

: >"$LAUNCH_LOG"

# 用 `-c` 形式避開 `cli ui` 背景化會立刻 exit 的 bug
# 包一層 bash 在 python 結束時補寫死訊標記
nohup bash -c "
  '$PYTHON_BIN' -c 'from podcast_toolkit import edit; edit.run_dashboard()'
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
