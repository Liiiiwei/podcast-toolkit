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

`install.sh` 自動檢查 Python 3.9+、Homebrew、裝 ffmpeg、裝 pyyaml 等套件、建 `podcast` symlink，並**本機生成雙擊啟動 app 到 `/Applications/Podcast.app`**（本機生成 → 無 quarantine、不會被 Gatekeeper 攔），裝完直接雙擊開介面。

選裝：本機 whisper 轉錄字幕（模型約 3GB）

```bash
pip3 install --user openai-whisper
# 跑：python3 -m whisper <audio.mp4> --model large-v3 --language zh --output_format srt
```

其他系統請手動跟著上面四步走（pip3 install pyyaml、brew/apt install ffmpeg、`ln -s "$(pwd)/bin/podcast" /usr/local/bin/podcast`）。

## 怎麼打開來用

> 第一次拿到電腦的人，請先跑過一次上面的「安裝」。裝完之後，以後每次要用，照下面做就好。

**最簡單的方法：**

1. 按鍵盤左下角的 `⌘`（command）鍵不放，再按一下空白鍵 → 螢幕中間會跳出一個搜尋框。
2. 在框裡打「Podcast」這幾個字。
3. 看到 Podcast 的圖示後，按一下鍵盤的 Enter（換行鍵）。
4. 等一下下（大概 2～5 秒），瀏覽器會自己打開，就可以開始用了。

**也可以這樣（喜歡用滑鼠的話）：**

1. 點螢幕下方工具列最左邊的「Finder」（藍白笑臉圖示）。
2. 左邊欄位點「應用程式」。
3. 在裡面找到 **Podcast**，連點兩下。

**遇到狀況怎麼辦：**

- **等了一下瀏覽器沒跳出來？** 再打開一次 Podcast 就好（重複上面的步驟），它會自己接上、幫你打開瀏覽器。
- **想之後更快打開？** 第一次打開後，工具列（Dock）上會出現 Podcast 圖示，在它上面按右鍵 →「選項」→ 點「在 Dock 中保留」。以後點工具列那顆圖示就能開。
- **用完要關？** 直接把瀏覽器分頁關掉就好；不想讓它一直在背景跑的話，在工具列的 Podcast 圖示按右鍵 →「結束」。

> 進階使用者：也可以在「終端機」輸入 `podcast ui` 開同一個畫面；其他指令（`podcast init / resegment / assemble …`）一樣保留。
> 如果之後把專案資料夾搬到別的位置，重跑一次 `./install.sh` 就會修好。

## 工作流

```bash
# 1. 在 ~/Downloads/ 建集資料夾（命名：YYYYMMDD 集名）
mkdir "$HOME/Downloads/20260601 新集名"

# 2. 跑 init 建子目錄 + 範本（片頭片尾共用 toolkit/assets/ 內的 intro / outro / subscribe_card，不在每集資料夾內複製）
podcast init "$HOME/Downloads/20260601 新集名"

# 3. 把錄音放進 01_母帶/
#    字幕可選兩條路：
#    (a) 本機 whisper：python3 -m whisper ... → 03_成品/新集名_final.srt
#    (b) 跑 edit 後在瀏覽器按「轉字幕」→ STT 三選一：xAI Grok / Gemini 2.5 Flash / OpenAI Whisper-1
#        在右上「設定」modal 填對應 api key；每家都吃詞庫提詞偏值（見下方「詞庫」段）

# 4. 跑 resegment 重新斷句（用 (b) 走 web 轉字幕已內建這步）
podcast resegment "$HOME/Downloads/20260601 新集名"

# 5. 人工檢查 04_工作檔/_resegment_review.txt，必要時改 episode.yaml 的 force_break/force_join 重跑（重跑要加 --force 蓋掉舊 v2.srt）

# 5.5 (可選) 視覺化編輯：裁切畫框 / 刪段 / 改字
podcast edit "$HOME/Downloads/20260601 新集名"

# 6. 跑 assemble 合成
#    CLI：預設 YT 完整版；web 端可勾 YT 完整版 / Reels 直式 / 多鏡頭（搭配 cam B 來源）
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
| `cameras` | 否 | 多鏡頭：`{a: 主鏡頭路徑, b: 副鏡頭路徑}`，舊集只有 `main_video` 時自動視為 cameras.a |
| `head_trim_sec` | 否 | 從正片開頭裁掉的秒數（不影響字幕時軸；assemble 階段才裁） |
| `tail_trim_sec` | 否 | 從正片結尾裁掉的秒數（同上） |
| `glossary` | 否 | 本集詞庫（見下方「詞庫」段）；會與 toolkit `common_glossary` 合併 |
| `subtitle_style` | 否 | YT 字幕樣式覆寫（合進 defaults.subtitle_style） |
| `subtitle_style_reels` | 否 | Reels 字幕樣式覆寫（合進 defaults.subtitle_style_reels） |

## defaults.yaml

全域預設值，改了會影響所有集。主要區段：

- `resegment` — 斷句長度 / dangle endings / reaction words
- `suspicious_pause` — 編輯介面標紅「可疑空拍卡」門檻
- `gemini` — Gemini STT 字幕格式規則（會注入 prompt）
- `common_glossary` — 全域詞庫（見下方「詞庫」段）
- `subtitle_style` — YT 16:9 字幕樣式（font/size/outline/shadow/margin）
- `subtitle_style_reels` — Reels 9:16 字幕樣式（手機豎屏 + 動作列遮擋專用，缺欄位自動回退到 subtitle_style）
- `watermark` — 影片浮水印 logo overlay（assemble 階段 ffmpeg 燒上去；enabled=false 時整段 no-op）
- `assets` — 片頭片尾 / 浮水印 logo 路徑（toolkit/assets/）
- `encode` — ffmpeg 編碼參數（codec、crf、preset、resolution、framerate）

## 詞庫

把固定的專有名詞、暱稱、人名統一寫成「詞庫」，STT 階段以 prompt 偏值降低誤聽，後處理階段把誤聽自動改正。三層合併：

1. `defaults.yaml` 的 `common_glossary` — toolkit 全域共用
2. web 端「字典」分頁的全域字典 — 跨集共用、可即時編輯
3. `episode.yaml` 的 `glossary` — 只影響本集

格式：

```yaml
glossary:
  - canonical: "Liwei Sia"
    sounds_like: ["你為夏", "你為下"]
    note: "節目主持人"
  - "我愛上班"   # 純字串 = 只當提詞、不展開誤聽
```

## 指令

- `podcast init <path>` — 腳手架
- `podcast resegment <path> [--force]` — 字幕重新斷句
- `podcast proofread <path> [--provider claude_code|gemini|off]` — 字幕語意校對（預設用本地 Claude Code，不外聯 API；非 CC 使用者自動退回 Gemini / 跳過）
- `podcast auto <path> [--no-proofread|--no-camera|--no-trim] [--provider ...] [--force]` — 一鍵自動後製：校對字幕 + 對應鏡頭（分軌集才有講者資料）+ 去頭去尾（偵測頭尾靜音補 trim），盡量自動只剩人工確認 + 輸出
- `podcast ingest-breeze <path> [--srt ...]` — 匯入 Breeze ASR 字幕（含講者 `[MicN]`）：去標籤寫 `_final_v2.srt` + 拆 MicN→speaker 寫 `speakers.json`（本地轉錄前端的交接入口）
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
14. 底部抽屜（drawer）：「專案檔案 / 字典」分頁切換 + 右側收合按鈕 → 收合 / 展開狀態與當前分頁存 localStorage、重新整理後還原

## Roadmap（未做）

- `podcast podcast-audio` — 純音檔輸出（mp3 320k，-16 LUFS）
