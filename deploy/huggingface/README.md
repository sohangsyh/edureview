---
title: HK Edu Reviewer
sdk: docker
app_port: 8000
startup_duration_timeout: 1h
pinned: false
---

# 香港初中教材 AI 審查平台

上傳中一至中三教材，對照教育局課程文件審查。原始碼及說明見 GitHub 倉庫。

Space 的 Settings → Variables and secrets 須設定：

- `ANTHROPIC_API_KEY`（secret）：AI 審查
- `ACCESS_CODE`（secret）：網站存取碼，公開前必須設定

免費 Space 重新啟動後，審查記錄會清空。
