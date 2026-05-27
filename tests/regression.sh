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

echo "→ 驗 assemble --dry-run 對 crop/deletions 的支援（不真跑 ffmpeg）"
TMP_EP=$(mktemp -d)
mkdir -p "$TMP_EP/01_母帶" "$TMP_EP/02_片頭片尾" "$TMP_EP/03_成品" "$TMP_EP/04_工作檔"
# 複製主檔
cp "$EP/01_母帶/過嗨乳牛.mp4" "$TMP_EP/01_母帶/regression.mp4" 2>/dev/null || \
    ln -s "$EP/01_母帶/過嗨乳牛.mp4" "$TMP_EP/01_母帶/regression.mp4"
cp "$EP/03_成品/過嗨乳牛_final_v2.srt" "$TMP_EP/03_成品/regression_final_v2.srt"
# 連結 toolkit 共用資產
podcast init "$TMP_EP" 2>/dev/null || true
# 蓋掉 episode.yaml 把 fixture 加進去
cat > "$TMP_EP/episode.yaml" <<EOF
date: 20260101
name: regression
main_video: 01_母帶/{name}.mp4
main_srt: 01_母帶/{name}.srt
fixes: []
card_fixes: []
force_break: []
force_join: []
$(cat "$FIXTURE_DIR/edit_episode.yaml")
EOF

OUT=$(podcast assemble "$TMP_EP" --dry-run)
echo "$OUT" | grep -q "crop=" || { echo "✗ 預期 ffmpeg 指令含 crop=，實際沒有"; exit 1; }
echo "$OUT" | grep -q "select=" || { echo "✗ 預期 ffmpeg 指令含 select=，實際沒有"; exit 1; }
trash "$TMP_EP" 2>/dev/null || rm -rf "$TMP_EP"
echo "  ✓ dry-run 含 crop + select"

echo "✅ regression passed"
