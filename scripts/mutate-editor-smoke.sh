#!/bin/bash
# 瀏覽器煙霧測試（tests/test_editor_browser_smoke.py）的突變測試。
#
# 為什麼要有這支：那支煙霧測試是 Phase 3「拆 app.js」的驗收門檻，
# 門檻本身必須先被證明「不是死的」。做法是把 app.js 的每個受測部件**逐一**關掉，
# 確認各自有對應的斷言會紅（只有「全開 vs 全關」抓不到死碼 —— 2026-08-08 教訓）。
#
# 跑法：bash scripts/mutate-editor-smoke.sh
# 需要 /usr/bin/python3（3.9.6，有 fastapi/uvicorn/websockets）＋一份 Chrome。
# 每個突變會實際起後端 + headless Chrome 跑一次，全程約 40 秒。
set -u
cd "$(dirname "$0")/.." || exit 1
AJ="podcast_toolkit/web/static/app.js"
BK=$(mktemp -d)
cp "$AJ" "$BK/app.js"
restore() { cp "$BK/app.js" "$AJ"; }
trap restore EXIT

T="tests/test_editor_browser_smoke.py"

run() { # $1=標籤  $2=期待會紅的 test 函式名
  out=$(PATH="/usr/bin:$PATH" /usr/bin/python3 -m pytest "$T::$2" -q 2>&1 | tail -3)
  if echo "$out" | grep -q "1 passed"; then
    echo "✗ $1 → 突變後仍然綠（該斷言是死的）"
  else
    echo "✓ $1 → 突變後變紅：$2"
  fi
  restore
}

echo "== 突變測試：逐一關掉 app.js 的每個受測部件 =="

# M1 模組整包載不起來（模擬拆檔後少了一個符號 / 路徑寫錯）
perl -0pi -e 's/\A/__MUTANT_NO_SUCH_FN__();\n/' "$AJ"
run "M1 模組載入即拋例外" "test_s1_no_uncaught_js_exception_on_load"

# M2 renderCards 不渲染任何卡（字幕卡那條鏈斷掉）
perl -0pi -e 's/^function renderCards\(\) \{$/function renderCards() {\n  return;  \/\/ MUTANT/m' "$AJ"
run "M2 renderCards 不渲染" "test_s2_cards_rendered"

# M3 時間軸區塊 class 改名（區塊還在，但選擇器找不到＝等同沒畫）
perl -0pi -e 's/    block\.className = "tl-block";/    block.className = "tl-blockX";  \/\/ MUTANT/' "$AJ"
run "M3 時間軸不畫區塊" "test_s3_timeline_rendered"

# M4 存檔 payload 不帶 cards（改的字送不出去＝「寫得進讀不回」的前科形狀）
perl -0pi -e 's/    cards: \[\.\.\.state\.textOverrides\.entries\(\)\]/    cards: [] \/* MUTANT *\//' "$AJ"
run "M4 存檔不帶改過的字" "test_s4_edit_and_save_round_trip"

# M5 未存徽章恆亮（髒值判斷失效 → 沒改東西也顯示有未存）
perl -0pi -e 's/    const n = unsavedCount\(\);/    const n = 1;  \/\/ MUTANT/' "$AJ"
run "M5 未存徽章恆亮" "test_s4_edit_and_save_round_trip"

echo "== 還原後確認全綠 =="
PATH="/usr/bin:$PATH" /usr/bin/python3 -m pytest "$T" -q 2>&1 | tail -2
