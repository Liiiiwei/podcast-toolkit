# Podcast Toolkit 工作記憶

## 專案規則

- 開發分支使用 `vs/{description}`，提交採 Conventional Commits。
- 字幕修改先保留原檔備份，再以測試驗證時間戳、卡號與講者映射。
- 跨電腦開發依賴 Git 分支與明確的 Breeze sidecar 資產，不把大型模型提交進版本庫。

## 本次學習

- 背景工作的成功摘要與警告必須是不同欄位，否則後續階段會清掉前面錯誤。
- 全域目前集數不足以支援多分頁；所有寫入請求都需要帶集數識別。
- Python 3.13 以上不可依賴 `audioop`，既有 `numpy` 可作為波形峰值替代方案。
- 打包髒狀態判斷必須包含未追蹤檔案，單看 `git diff` 會漏掉新程式碼。
- 長片的 `faststart` 是完成編碼後的第二階段原地重寫；上傳用途不需要時，不應讓這個可選步驟決定整次合成成敗。
- 打包前必須驗證 Breeze 與本機模型的固定 stage；僅從已安裝 App 借用資源可以救急，但不是可重現建置來源。
- 使用者偏好先找出最佳離線免費方案後直接執行，並持續做到有實際 App、API、日誌或畫面證據可測。

## 下一階段

- 建立固定 Breeze stage，恢復一條指令即可重現完整 App 打包。
- 在磁碟至少有 10 GiB 可用空間時，以實際長片驗收合成。
- 凱特王專案補入正式影片後，驗收新版 App 的完整輸出。

## Content Drafts (中央: ~/Desktop/Obsidian FlowPilot/03-Content/Threads/)

- 2026-09-06 optional-finalization-breaks-render — 可選收尾步驟不該讓已完成的長片整體失敗
