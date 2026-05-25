# podcast-toolkit

剪輯 podcast「我愛上班」的 CLI 工具。

## 安裝

```bash
cd ~/Projects
git clone <repo-url> podcast-toolkit
cd podcast-toolkit
pip3 install pyyaml
brew install ffmpeg   # 已裝過跳過
ln -s "$(pwd)/bin/podcast" /usr/local/bin/podcast
podcast --help
```

## 工作流

```bash
# 1. 在 ~/Downloads/ 建集資料夾（命名：YYYYMMDD 集名）
mkdir "$HOME/Downloads/20260601 新集名"

# 2. 跑 init 建子目錄 + symlink + 範本
podcast init "$HOME/Downloads/20260601 新集名"

# 3. 把錄音放進 01_母帶/、whisper 字幕放進 03_成品/新集名_final.srt

# 4. 跑 resegment 重新斷句
podcast resegment "$HOME/Downloads/20260601 新集名"

# 5. 人工檢查 04_工作檔/_resegment_review.txt，必要時改 episode.yaml 的 force_break/force_join 重跑

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

Exit codes：0 成功、1 輸出已存在、2 設定錯誤、3 檔案缺失、4 ffmpeg 失敗。

## 回歸測試

```bash
bash tests/regression.sh
```

用「過嗨乳牛」這集 diff 確保 toolkit 改動不會破壞既有行為。

## Roadmap（未做）

- `podcast podcast-audio` — 純音檔輸出（mp3 320k，-16 LUFS）
- `podcast multicam` — 多機切鏡（讀 switch_list.json）
- 字幕轉錄整合（直接接 whisper-guard 4 層防護）
