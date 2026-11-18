# OpenCode Case Investigator

使用者選擇程式、填寫執行位置與異常現象後，便可在案件中與查案助理對話，並附上或貼上 PNG、JPEG 圖片。Gitea 內部帳號可查看自己的案件。通過內部身分判定的 Gitea 管理員可接續案件及維護程式索引。

## 專案結構

- `frontend/` 包含繁體中文介面、樣式與瀏覽器端程式。使用相對路徑，不需要 Node 建置。
- `backend/` 包含 Flask API、Gitea OAuth、SQLite、Git 同步和 OpenCode V1 session 整合。
- `deploy/route.py` 依部署參數產生 Traefik file provider 的動態設定。

## 建置與執行

在目標 Linux 發行版使用 Python 3.9.10。Makefile 預設使用 `/usr/local/python/3.9.10/bin/python3.9`。安裝位置不同時，可用 `PYTHON` 指向同版本的直譯器。建置、測試與啟動前會檢查直譯器和現有 venv 的版本。打包環境需要啟用 shared library 的 Python，以及 SQLite、SSL、ctypes。執行服務的帳號需要 Git、repository 唯讀權限與執行目錄讀取權限。

```sh
make backend
make test
make executable
```

`backend/venv` 存放虛擬環境。設定下列表格的環境變數後，可執行 `backend/dist/case-investigator`，或用 `make run` 從原始碼啟動。`make clean` 清除建置產物。

| 變數 | 用途 |
| --- | --- |
| CASE_STATE | 本機持久化目錄的絕對路徑，存放案件、附件、快照、Git 快取。使用服務專用目錄，限制其他帳號存取。 |
| CASE_PREFIX | 對外子路徑，預設為 `/case-investigator`。 |
| PUBLIC_ORIGIN | 使用者瀏覽器存取的協定與主機，包含必要的連接埠。 |
| GITEA_BASE | Gitea 基底位址，包含其部署子路徑。 |
| GITEA_CLIENT_ID、GITEA_CLIENT_SECRET | Gitea OAuth 應用設定。回呼為 PUBLIC_ORIGIN 加 CASE_PREFIX 加 `/auth/callback`，授權範圍為 read:user。 |
| SESSION_SECRET | 部署時產生的高熵隨機值，各次啟動沿用。 |
| INTERNAL_DOMAINS、EXCLUDED_PREFIXES | 完整 email 網域白名單及排除的帳號前綴，分別以逗號分隔。前綴同時檢查 login 與 email 帳號部分，預設排除 y_。角色於登入時取得，有效期一小時。 |
| RUN_ROOTS | 允許提交的絕對目錄，以冒號分隔。每個人的私人範圍請使用 `{login}` 路徑元件。沒有此元件的目錄視為所有內部使用者共用。 |
| OPENCODE_BASE | 專用 OpenCode V1 server 的基底位址。OpenCode 和服務必須以相同路徑存取案件快照。 |
| OPENCODE_SERVER_PASSWORD、OPENCODE_SERVER_USERNAME | OpenCode 開啟 Basic Auth 時設定，預設使用者名稱為 opencode。 |
| BIND_HOST、PORT | 後端監聽介面和連接埠，預設限本機。遠端 Traefik 需能連線此介面。 |
| TRAEFIK_UPSTREAM | Traefik 可連線的後端完整位址，用於產生 route。 |

## Traefik

設好部署變數後，執行 `make route > deploy/cases.toml`，將產物放入現有 Traefik file provider 目錄。使用專用 router 或沿用環境的 entrypoint 與 TLS 設定。此 route 保留 CASE_PREFIX，請勿套用 StripPrefix。瀏覽器開啟 CASE_PREFIX 時，後端會導向帶尾斜線的入口，靜態資源、API 與登入回呼皆經過該 route。上游逾時應涵蓋 Git 同步與模型回覆時間。

## 查案資料與執行方式

1. 管理員在「管理程式索引」建立程式、owner/repository、main 或 master，以及可選的入口檔。同步使用服務帳號預設的 Git credential helper。
2. 開案時，服務先檢查執行位置在允許的目錄內，且可讀取與進入，再以 Git fetch 更新指定分支。每案記錄當時 commit。未填入口檔時，依程式檔名在對應 repository 內找唯一符合的檔案。
3. 案件擷取執行位置第一層最多六個 log、err、out 檔，各取末尾 6000 bytes。程式入口最多擷取前 32000 bytes。另將常見程式碼檔案匯出為文字快照，最多 256 個檔案、總計 8 MB，排除隱藏路徑、符號連結及超過 256 KB 的檔案。
4. 每案有獨立 OpenCode session。首次查案、使用者追問和管理員接手時，後端都指定 `investigate-web` agent。部署時須讓 OpenCode 從案件快照目錄也能載入這個 agent。案件 session 只開放 `read`、`glob`、`grep`，Bash 指令會直接被拒絕。部署時使用專用且受限的 OpenCode 作業系統帳號，僅授予案件快照所需的讀取權限。模型和 provider 憑證由 OpenCode 自己管理。圖片分析需要支援影像輸入的模型。
5. 瀏覽器先儲存使用者訊息，再等待該回合回覆。服務使用本機檔案鎖避免同案並行送出。上游逾時後可重新取得回覆，若上游其實已完成，重試可能造成同一訊息再次送達。案件及附件保存在部署環境。此版以單機部署為目標。
