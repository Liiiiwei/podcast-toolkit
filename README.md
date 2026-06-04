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

## GUI 模式（推薦）

```bash
python3 setup_app.py py2app -A
open dist/Podcast.app
```

雙擊 `Podcast.app` 後在瀏覽器 dashboard 選集、新建集、設定集數根目錄。CLI 仍保留，供腳本化使用。

## 工作流

```bash
# 1. 在 ~/Downloads/ 建集資料夾（命名：YYYYMMDD 集名）
mkdir "$HOME/Downloads/20260601 新集名"

# 2. 跑 init 建子目錄 + 範本（片頭片尾共用 toolkit/assets/ 內的 intro / outro / subscribe_card，不在每集資料夾內複製）
podcast init "$HOME/Downloads/20260601 新集名"

# 3. 把錄音放進 01_母帶/
#    字幕可選兩條路：
#    (a) 本機 whisper：python3 -m whisper ... → 03_成品/新集名_final.srt
#    (b) 跑 edit 後在瀏覽器按「轉字幕」（走 xAI Grok STT，要先在 ~/.podcast-toolkit/config.json 放 xai_api_key）

# 4. 跑 resegment 重新斷句（用 (b) 走 web 轉字幕已內建這步）
podcast resegment "$HOME/Downloads/20260601 新集名"

# 5. 人工檢查 04_工作檔/_resegment_review.txt，必要時改 episode.yaml 的 force_break/force_join 重跑（重跑要加 --force 蓋掉舊 v2.srt）

# 5.5 (可選) 視覺化編輯：裁切畫框 / 刪段 / 改字
podcast edit "$HOME/Downloads/20260601 新集名"

# 6. 跑 assemble 合成（CLI 預設 YT 完整版；web 端可勾選 YT + Reels 一起跑）
podcast assemble "$HOME/Downloads/20260601 新集名"
```

## episode.yaml 欄位

| 欄位 | 必填 | 說明 |
|------|------|------|
| `date` | 是 | 集日期 YYYYMMDD（init 自動填） |
| `name` | 是 | 集名（init 自動填） |
| `main_video` | 是 | 正片 mp4，路徑相對於集資料夾，可用 `{name}` |
| `main_srt` | 是 | 原始字幕 srt，同上 |
| `crop_yt` | 否 | YT 完整版裁切框 `{x,y,width,height}`（0-1 比例，web edit 設） |
| `crop_reels` | 否 | Reels 直式裁切框（同上） |
| `deletions` | 否 | 要刪掉的 segment idx 列表 |
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
- `podcast assemble <path> [--dry-run] [--force]` — 合成 YT 完整版（Reels 走 web 端 modal 勾選）
- `podcast edit <path>` — 開瀏覽器視覺化編輯：裁切 / 刪段 / 改字

Exit codes：0 成功、1 輸出已存在、3 檔案缺失、4 ffmpeg 失敗。

## 回歸測試

```bash
bash tests/regression.sh
```

用「過嗨乳牛」這集 diff 確保 toolkit 改動不會破壞既有行為。

## podcast edit 手動驗收

1. `podcast edit <path>` → 瀏覽器自動開、影片可播
2. 新集沒字幕時前端引導「轉字幕」→ 按下去走 xAI Grok STT → 自動跑 resegment → 字幕變句子層（不會一字一段）
3. YT / Reels 影片版本 tab 切換 → 各自獨立的裁切框
4. 雙擊影片區出現裁切框 → 拖角縮放、拖中心移動
5. 比例按鈕（9:16 / 4:5 / 16:9 / 自訂）→ 點下去裁切框立刻切到對應比例
6. 啟用裁切後 → 字幕 overlay 自動鎖在裁切框內、不會跑出框外
7. 📁 開啟集資料夾 → 跳 macOS 原生 Finder 選資料夾 → 直接切到該集
8. 點字幕卡時間欄 → 影片跳到對應時間
9. inline 改錯字 → 失焦後文字變橘色底線
10. ✕ 刪卡 → 卡片變灰、再點 ↺ 還原
11. 「完成並儲存」→ 頁面變「✅ 已儲存」、CLI exit 0、`episode.yaml` 與 `_v2.srt` 真的有改
12. 「合成」modal 勾 YT + Reels → queue 進度條依序跑兩個 target，輸出 `_YT完整版.mp4` 與 `_Reels.mp4`（1080×1920）
13. 檔案列表 group-by 七大區塊（母帶 / 片頭片尾 / 成品 / 工作檔 ...）、折疊狀態存 localStorage

## Roadmap（未做）

- `podcast podcast-audio` — 純音檔輸出（mp3 320k，-16 LUFS）
- `podcast multicam` — 多機切鏡（讀 switch_list.json）
