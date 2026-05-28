# podcast-toolkit

剪輯 podcast「我愛上班」的 CLI 工具。

## 安裝（macOS）

```bash
cd ~/Projects
git clone https://github.com/Liiiiwei/podcast-toolkit.git
cd podcast-toolkit
./install.sh
podcast --help
```

`install.sh` 自動檢查 Python 3.9+、Homebrew、裝 ffmpeg、裝 pyyaml、建 `podcast` symlink。

選裝：本機 whisper 轉錄字幕（模型約 3GB）

```bash
pip3 install --user openai-whisper
# 跑：python3 -m whisper <audio.mp4> --model large-v3 --language zh --output_format srt
```

其他系統請手動跟著上面四步走（pip3 install pyyaml、brew/apt install ffmpeg、`ln -s "$(pwd)/bin/podcast" /usr/local/bin/podcast`）。

## 工作流

```bash
# 1. 在 ~/Downloads/ 建集資料夾（命名：YYYYMMDD 集名）
mkdir "$HOME/Downloads/20260601 新集名"

# 2. 跑 init 建子目錄 + symlink + 範本（會在 02_片頭片尾/ 建 4 個 symlink：intro.mp4 / intro_music.m4a / outro.mp3 / subscribe_card.png 指回 toolkit/assets/）
podcast init "$HOME/Downloads/20260601 新集名"

# 3. 把錄音放進 01_母帶/、whisper 字幕放進 03_成品/新集名_final.srt

# 4. 跑 resegment 重新斷句
podcast resegment "$HOME/Downloads/20260601 新集名"

# 5. 人工檢查 04_工作檔/_resegment_review.txt，必要時改 episode.yaml 的 force_break/force_join 重跑（重跑要加 --force 蓋掉舊 v2.srt）

# 5.5 (可選) 視覺化編輯：裁切畫框 / 刪段 / 改字
podcast edit "$HOME/Downloads/20260601 新集名"

# 6. 跑 assemble 合成
podcast assemble "$HOME/Downloads/20260601 新集名"
```

## episode.yaml 欄位

| 欄位 | 必填 | 說明 |
|------|------|------|
| `date` | 是 | 集日期 YYYYMMDD（init 自動填） |
| `name` | 是 | 集名（init 自動填） |
| `main_video` | 是 | 正片 mp4，路徑相對於集資料夾，可用 `{name}` |
| `main_srt` | 是 | 原始字幕 srt，同上 |
| `fixes` | 否 | 本集 whisper 誤聽錯字 `[[找, 改], ...]` |
| `card_fixes` | 否 | 合併後跨段落錯字（同格式） |
| `force_break` | 否 | 強制斷句的 whisper 段落 index 列表 |
| `force_join` | 否 | 強制合併的 whisper 段落 index 列表 |
| `resegment` | 否 | 覆寫 toolkit defaults（極少用） |

## defaults.yaml

全域預設值，包含字幕樣式、長度參數、ffmpeg 編碼參數。改了會影響所有集。

## 指令

- `podcast init <path>` — 腳手架
- `podcast resegment <path> [--force]` — 字幕重新斷句
- `podcast assemble <path> [--dry-run] [--force]` — 合成 YT 完整版
- `podcast relink <path>` — 修復斷掉的 symlink
- `podcast edit <path>` — 開瀏覽器視覺化編輯：裁切 / 刪段 / 改字

Exit codes：0 成功、1 輸出已存在、3 檔案缺失、4 ffmpeg 失敗。

## 回歸測試

```bash
bash tests/regression.sh
```

用「過嗨乳牛」這集 diff 確保 toolkit 改動不會破壞既有行為。

## podcast edit 手動驗收

1. `podcast edit <path>` → 瀏覽器自動開、影片可播
2. 雙擊影片區出現裁切框 → 拖角縮放、拖中心移動
3. 比例按鈕（9:16 / 4:5 / 16:9 / 自訂）→ 點下去裁切框立刻切到對應比例
4. 啟用裁切後 → 字幕 overlay 自動鎖在裁切框內、不會跑出框外
5. 📁 開啟集資料夾 → 跳 macOS 原生 Finder 選資料夾 → 直接切到該集
6. 點字幕卡時間欄 → 影片跳到對應時間
7. inline 改錯字 → 失焦後文字變橘色底線
8. ✕ 刪卡 → 卡片變灰、再點 ↺ 還原
9. 「完成並儲存」→ 頁面變「✅ 已儲存」、CLI exit 0、`episode.yaml` 與 `_v2.srt` 真的有改
10. `podcast assemble` → 輸出 mp4 解析度、時長、字幕都套用編輯結果

## Roadmap（未做）

- `podcast podcast-audio` — 純音檔輸出（mp3 320k，-16 LUFS）
- `podcast multicam` — 多機切鏡（讀 switch_list.json）
- 字幕轉錄整合（直接接 whisper-guard 4 層防護）
