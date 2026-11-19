# DRC 查案助手開發約定

## 架構與交付

- 前端使用 React 元件與 Vite，程式放在 `frontend/src/`。由 React state 管理表單、附件及案件狀態。保留繁體中文、移動城堡視覺，以及等待查案時跳動的稻草人。
- 後端位於 `backend/`，使用 Python **3.9.10**、Flask、Waitress 和 SQLite。建置、測試、啟動與 PyInstaller 打包都透過根目錄 Makefile，Python 虛擬環境放在 `backend/venv/`。
- Vite 產出 `frontend/dist/`。原始碼啟動與單檔執行檔均提供同一份產物。頁面、素材、API、OAuth callback 共用可設定的 Traefik 子路徑 `server.prefix`。請用多層子路徑驗證相對網址。
- 公開 repository 只收程式與可公開的文件。公司網址、帳密、token、實際案件內容、repo 快取和本機設定留在部署環境。

部署設定由根目錄的 `config.example.yml` 示範。`config.yml`、原始碼啟動與單檔啟動的設定欄位，以及 Traefik route 所讀取的欄位須一致。

## 案件與權限

- 登入走 Gitea OAuth，只開放符合 internal 規則的帳號。所有登入帳號可讀取所有案件；追問、重試、終止與刪除案件由後端限建立者與管理員操作。Gitea admin 可以接手使用者的對話，並新增、修改、刪除資料庫中的程式索引。
- 初次報案必填執行的程式、程式**執行位置**與遇到的狀況。後端以服務帳號實際讀取及進入使用者填寫的執行目錄，成功後才同步 Git 並呼叫 OpenCode。權限不足時前端顯示原因並保留表單。報案及追問都支援附件和在文字欄位直接貼圖。
- 程式索引必填程式名稱與程式路徑。有設定 repository 時，開案由後端更新對應的 Gitea main/master 分支，保留 commit。案件對話主標題用程式名稱，副標題用 OpenCode session title，搜尋支援這兩個欄位。
- 每案一個 OpenCode session。首次訊息、追問、重試與管理員接手都明確指定 `investigate-web`。由 OpenCode 呼叫模型；web service 只提供調查結果。模型 API 與憑證由 OpenCode 管理。
- 新案件的 OpenCode directory 是報案時提供的 run root。建立案件前檢查群組寫入位元及服務帳號的實際存取權。有指定 repository 的案件獨立 clone 至 run root 下的 `.opencode-tmp/case-*/source/`，附件放在同案的 `attachments/`。既有 snapshot session 沿用原目錄。案件開放 read、grep、glob，並依程式設定放行所選 Skill 與其附檔目錄。

## 驗證

- 使用 Python 3.9.10 執行 `make test`。前端或打包流程變更時，執行 `make executable` 並驗證執行檔能在指定子路徑載入 Vite 產物。
- 涉及查案介面的變更，驗證登入、必填欄位、貼圖、案件搜尋、接續對話和管理介面。明確區分模擬上游的測試與公司環境的實際整合驗證。
