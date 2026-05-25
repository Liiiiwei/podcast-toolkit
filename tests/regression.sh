#!/bin/bash
# 用過嗨乳牛集當 fixture 跑回歸 diff
set -e
EP="$HOME/Downloads/20260417 過嗨乳牛"
FIXTURE_DIR="$(dirname "$0")/fixtures"

if [ ! -d "$EP" ]; then
    echo "✗ fixture 集不存在：$EP"
    exit 1
fi

echo "→ 跑 podcast resegment"
podcast resegment "$EP" --force > /dev/null

echo "→ diff _v2.srt 與 fixture"
diff "$EP/03_成品/過嗨乳牛_final_v2.srt" "$FIXTURE_DIR/expected_v2.srt"

echo "✅ regression passed"
