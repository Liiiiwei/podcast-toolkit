# 梯次 B（最小版）獨立驗收結果（CDP 真事件走查）

驗收者：fresh-context，未參與實作。日期 2026-09-24。
走查腳本：`verify_b.py`（每個 ✓ 綁布林斷言 ok=實得==期待；拖曳走完整 down→多次 move→up；
seek 前先斷言 seekable.length>0；可點元素驗 elementFromPoint 最上層）。

## 環境
- serve_podcast :8795（服務 live working tree），/api/video 回 206、video seekable.length=1、duration=24
- range_server :8796 ROOT=static（服務 video-edit-prototype 與 timeline-core.js 原始碼），206 Range
- headless Chrome CDP :9331
- 沙箱集：/private/tmp/pt-timeline-baseline/episode/20260601 時間軸基準集（10+ 字幕塊）

## 基準（全綠）
`verify_b.py`（全六項）：34/34 通過。

## 六項突變測試（真突變：改回舊行為→對應斷言變紅→還原）
| 項 | 突變點（產品碼） | 改法 | 變紅的斷言 |
|----|----------------|------|-----------|
| B3a | timeline.js:152 `bindPodcastScrub(grip)` | 註解掉 | B3a.3/4/5（currentTime 不再隨拖動變化，got=0） |
| B3b | timeline.js:117 `startTimelineDrag(e,r,"move")` | 註解掉 | B3b.2（塊左緣位移 got=0，平移失效）；B3b.5 純點擊仍綠 |
| B2  | api.js title_cards `.filter` | 改成恆 false | B2.4（存檔→重載後標題卡消失）；B2.1 記憶體仍綠 → 精準隔離「持久化」 |
| B4  | app.js:2328 `setCardTrackVisible(layoutMode==="video")` | 改成 `false` | B4.2（video 態軌道不再顯示）；B4.5 podcast 態仍綠 |
| B3c | timeline.js:77 `bindTimelineMarquee(tl)` | 註解掉 | B3c.3（框選後 deleted 數=0，未進 deletions） |
| video | timeline-core.js:404 `c.end=clamp(...)` | 改成 `c.end=e0` | V.4（右端點拖曳後 end 不變 7→7）→ 證明 video 測試走的是 podcast 共用的 timeline-core |

全部突變後對應斷言確實變紅、還原後重跑 34/34 全綠、無 MUT 殘留。

## 其他
- tests/test_config_roundtrip.py：23 passed。
- B4 layout_mode 經 POST /api/save round-trip：video→顯示、podcast→隱藏，兩態各驗一次；yaml 實寫入。
- 收尾已把沙箱後端 title_cards 清空、layout_mode 還原 podcast。

## 重要 git 事實
梯次 B 全部實作（timeline.js/timeline-core.js/api.js/app.js/episode_io.py/config.py/
defaults.yaml/templates/episode.yaml/tests）目前是**未提交的工作區改動**，HEAD(235aa0d) 不含
api.js 的 title_cards 等程式碼（`git show HEAD:...api.js | grep title_cards` 無命中）。
功能在 working tree 實際生效（serve_podcast 服務工作區），但尚未 commit。
