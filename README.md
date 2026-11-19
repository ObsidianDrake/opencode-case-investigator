# OpenCode Case Investigator

使用者選擇程式、填寫執行位置與異常現象後，便可在案件中與查案助理對話，並附上或貼上 PNG、JPEG 圖片。所有已登入的內部帳號都能瀏覽案件列表與對話。開啟案件後可直接複製網址（例如 `/tools/cases/?case=42`）分享，收件者登入後會回到該案。只有建立者或管理員可追問、重試或終止查案，也可從左側案件列右端的「⋯」重新命名或刪除對話。主標題與副標題可分別設定，留空時沿用程式名稱與 OpenCode session 名稱。刪除範圍為查案服務中的案件及訊息，run root、案件工作檔及上游 OpenCode session 保留。通過內部身分判定的 Gitea 管理員可接續案件及維護程式索引。

## 專案結構

- `frontend/src/` 的 React 元件負責登入、報案表單、案件對話和管理介面。Vite 將程式和素材建置到 `frontend/dist/`，使用相對資源路徑以支援 Traefik 子路徑。
- `backend/` 包含 Flask API、Gitea OAuth、SQLite、Git 同步和 OpenCode V1 session 整合。
- `deploy/route.py` 依部署參數產生 Traefik file provider 的動態設定。

## 建置與執行

在目標 Linux 發行版使用 Python 3.9.10。Makefile 預設使用 `/usr/local/python/3.9.10/bin/python3.9`。安裝位置不同時，可用 `PYTHON` 指向同版本的直譯器。建置、測試與啟動前會檢查直譯器和現有 venv 的版本。打包使用 PyInstaller 6.22.3，支援 Python 3.9.10 靜態連結且另外提供 shared library 的安裝方式。打包主機需有對應的 `libpython3.9.so.1.0`，並提供 SQLite、SSL 和 ctypes。若主機只有靜態庫 `libpython3.9.a`，可直接使用下方 GitHub Actions 建好的 RHEL 8 執行檔。執行服務的帳號需要 Git、repository 唯讀權限與執行目錄讀寫及進入權限。

建置主機需提供 Node.js（20.19 以上的 20.x，或 22.12 以上）及 npm。`make frontend` 依 lockfile 安裝套件並執行 Vite。`make run`、`make test`、`make executable` 都會先建置前端。單檔執行檔已內含建置產物，可在未安裝 Node.js 的主機執行。開發約定見 [AGENTS.md](AGENTS.md)。

```sh
make backend
make test
make executable
```

### 從 GitHub Actions 下載 RHEL 8 執行檔

推送到 `main`、推送 `v` 開頭的 tag，或手動點選 **Actions → Build RHEL 8 binary → Run workflow**，都會建置 x86_64 版本。流程在 AlmaLinux 8.10 使用 Python 3.9.10 執行後端測試與打包，再以一般使用者在 Red Hat UBI 8.10 啟動成品。首頁、前端 JavaScript 和登入前的 API 回應通過檢查後，成品會出現在該次執行的 **Artifacts**。

下載並解開 `case-investigator-rhel8-x86_64` artifact 的 ZIP，在 RHEL 8 x86_64 主機執行以下指令。執行檔已包含 Python 與前端。服務帳號的環境需提供 Git，以供開案時更新 repository。

```sh
sha256sum -c case-investigator-rhel8-x86_64.tar.gz.sha256
tar xzf case-investigator-rhel8-x86_64.tar.gz
```

首次部署請依下節建立 `config.yml`。若 Gitea 或 OpenCode 使用公司 HTTPS 憑證，請確認公司 CA 已加入 RHEL 的信任清單，並在啟動服務的環境設定 `SSL_CERT_FILE=/etc/pki/tls/certs/ca-bundle.crt`。已有設定檔的部署可直接執行下列指令。

```sh
./case-investigator --config /path/to/config.yml
```

## 部署設定

在專案根目錄執行以下指令。將範例的主機、目錄、網域和 Gitea OAuth 憑證替換成部署環境的值。`config.yml` 已由 Git 忽略。

```sh
cp config.example.yml config.yml
chmod 600 config.yml
vi config.yml
make run
```

預設從啟動時的工作目錄讀取 `config.yml`。其他位置可用 `make run CONFIG=/path/to/config.yml`，或 `backend/dist/case-investigator --config /path/to/config.yml`。修改設定後重新啟動服務。指定檔案時，所有服務設定都取自該檔。既有部署在未指定檔案且工作目錄沒有 `config.yml` 時，仍可使用舊版環境變數。

- **程式執行位置**由使用者在報案時填寫絕對路徑。後端以實際執行服務的帳號檢查目錄的群組寫入位元（g+w），並確認服務帳號可讀、可寫、可進入；若權限不足，前端顯示權限錯誤並保留輸入，服務會在同步 Git 或呼叫 OpenCode 前停止。
- **內部身分**依 LSF Dashboard 的 `access_policy.account_email_domains` 與 `excluded_account_prefixes` 判定。完整比對 Gitea email 網域，先排除 email 帳號部分以指定字串開頭者，兩者都忽略大小寫。例如網域設為 `example.invalid`、前綴設為 `y_` 時，`alice@example.invalid` 可登入，`Y_alice@example.invalid` 和 `alice@sub.example.invalid` 都會被拒絕。前綴針對 email 的 @ 左側。只有通過內部身分判定後才繼承 Gitea admin。
- **Session 簽章**保護瀏覽器中本服務的登入 cookie。首次啟動會在 `server.state_dir` 下的 `cases.sqlite` 自動建立隨機金鑰，後續重啟沿用。此目錄及備份限服務帳號存取。既有部署若要繼續使用原金鑰，可將舊 `SESSION_SECRET` 放入選用欄位 `server.session_secret`。更新前簽發的 cookie 沒有 email 欄位，需重新登入一次；日後更新內部帳號規則並重啟，既有 session 在下次 API 請求也會套用新規則。
- 私有 repository 請在部署用的 `config.yml` 填入 `gitea.token`。使用能讀取目標 repository 的服務帳號 personal access token。首次抓取與後續更新共用此 token；留空時沿用原本的 Git 認證方式。修改後重啟服務。
- **Gitea 登入回呼**為 `server.public_origin` 加 `server.prefix` 加 `/auth/callback`。將完整回呼登錄到 Gitea OAuth 應用程式，並填入 `gitea.client_id`、`gitea.client_secret`。
- **上游服務**在 `opencode.url` 填專用 OpenCode Server 的基底位址。可用 `opencode.model` 指定每回合的模型，格式為 `providerID/modelID`，僅以第一個斜線分隔。管理員可在側欄「查案模型設定」從 OpenCode 的 `GET /config/providers` 清單選取模型，寫入 SQLite 後優先於 YAML。新案、追問與重試會在每次送出前讀取，免重啟。兩處均未設定時交由 OpenCode 選擇。若啟用 Basic Auth，設定 `opencode.password` 與選用的 `opencode.username`。後端與 OpenCode 需能以相同絕對路徑存取 run root，OpenCode 也須能讀取案件的私有子目錄（例如以相同服務帳號執行）。

## 服務日誌

服務同步輸出到終端機 stderr 和 `server.state_dir/logs/case-investigator.log`。可用 `logging.directory` 指定目錄。日誌包含啟動及監聽位址、HTTP 狀態與耗時、案件建立、Git 同步、OpenCode 等待與回覆。

以主機當地午夜為切點，跨日後的第一筆紀錄會將舊檔更名為 `case-investigator.log.YYYY-MM-DD`。服務在啟動和輪替時清理超過 `logging.retention_days` 的日期檔，預設保留前 14 天及當天。可用 `tail -F` 持續查看目前的日誌。

## Traefik

設定 `server.prefix` 與 `traefik.upstream` 後，執行 `make route > deploy/cases.toml`，自訂檔案路徑可加 `CONFIG=/path/to/config.yml`。將產物放入現有 Traefik file provider 目錄。使用專用 router 或沿用環境的 entrypoint 與 TLS 設定。此 route 保留 CASE_PREFIX，請勿套用 StripPrefix。瀏覽器開啟 CASE_PREFIX 時，後端會導向帶尾斜線的入口，靜態資源、API 與登入回呼皆經過該 route。上游逾時應涵蓋 Git 同步與模型回覆時間。

## 查案資料與執行方式

1. 管理員在「管理程式索引」建立程式、owner/repository、main 或 master，以及可選的入口檔。同步使用服務帳號預設的 Git credential helper。
2. 開案時，服務先確認服務帳號可讀取與進入填寫的執行目錄，再將指定分支淺層 clone 至該位置的 `.opencode-tmp/case-*/source/`，每個案件使用獨立目錄。每案記錄當時 commit。未填入口檔時，依程式檔名在對應 repository 內找唯一符合的檔案。
3. 案件擷取執行位置第一層最多六個 log、err、out 檔，各取末尾 6000 bytes。程式入口最多擷取前 32000 bytes。OpenCode 可以從 run root 檢閱執行資料，也可以依案件提供的原始碼路徑讀取完整 checkout。
4. 每案有獨立 OpenCode session。首次查案、使用者追問和管理員接手時，後端都指定 `investigate-web` agent。部署時須讓 OpenCode 從使用者提供的 run root 也能載入這個 agent。案件 session 只開放 `read`、`glob`、`grep`，Bash 指令會直接被拒絕。部署時使用專用且受限的 OpenCode 作業系統帳號，限制可讀取的部署資料範圍。升級前建立的 session 仍使用原快照目錄；要調查 run root 請重新開案。模型和 provider 憑證由 OpenCode 自己管理。每個有圖片的回合會先把原圖寫入該案私有子目錄下的 `attachments/message-<訊息編號>.png` 或 `.jpg`，再傳送文字路徑請 agent 用 `read` 開啟。聊天室預覽沿用 SQLite 中的附件。圖片辨識仍取決於工具與模型的影像能力。
5. 瀏覽器先儲存使用者訊息，再等待該回合回覆。服務使用本機檔案鎖避免同案並行送出。等待 OpenCode 調查回覆沒有應用程式時限。案件擁有者或管理員可以點「終止查案」中止該次回合，再於同案繼續提問。代理層的連線逾時需另行設定。案件及附件保存在部署環境。此版以單機部署為目標。
