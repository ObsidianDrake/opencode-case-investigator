# OpenCode Case Investigator

使用者選擇程式、填寫執行位置與異常現象後，便可在案件中與查案助理對話，並附上或貼上 PNG、JPEG 圖片。所有已登入的內部帳號都能瀏覽案件列表與對話。開啟案件後可直接複製網址（例如 `/tools/cases/?case=ses_abc123`）分享，收件者登入後會回到該案。只有建立者或管理員可追問、重試或終止查案，也可從左側案件列右端的「⋯」重新命名或刪除對話。主標題與副標題可分別設定，留空時沿用程式名稱與 OpenCode session 名稱。刪除範圍為查案服務中的案件及訊息，run root、案件工作檔及上游 OpenCode session 保留。通過內部身分判定的 Gitea 管理員可接續案件及維護程式索引。

## 專案結構

- `frontend/src/` 的 React 元件負責登入、報案表單、案件對話和管理介面。Vite 將程式和素材建置到 `frontend/dist/`，使用相對資源路徑以支援 Traefik 子路徑。
- `backend/` 包含 Flask API、Gitea OAuth、SQLite、Git 同步和 OpenCode V1 session 整合。
- `deploy/route.py` 依部署參數產生 Traefik file provider 的動態設定。

## 建置與執行

開發版本為 Python 3.9。Makefile 預設使用 PATH 中的 `python3.9`，接受所有 3.9 修訂版本。也可以用 `make PYTHON=/path/to/python3 test` 指定 Python 3.9 以上的直譯器。現有的 `backend/venv` 須與指定直譯器的主、次版本相同。打包使用 PyInstaller 6.22.3，支援 Python 3.9.10 靜態連結且另外提供 shared library 的安裝方式。打包主機需有與直譯器版本相符的 shared library（Python 3.9 為 `libpython3.9.so.1.0`），並提供 SQLite、SSL 和 ctypes。若主機只有靜態庫 `libpython3.9.a`，可直接使用下方 GitHub Actions 建好的 RHEL 8 執行檔。執行服務的帳號需要 Git、repository 唯讀權限與執行目錄讀寫及進入權限。

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
- **Session 簽章**保護瀏覽器中本服務的登入 cookie。登入有效期為 12 小時，瀏覽器每次向查案服務發出請求都會續期。首次啟動會在 `server.state_dir` 下的 `cases.sqlite` 自動建立隨機金鑰，後續重啟沿用。此目錄及備份限服務帳號存取。既有部署若要繼續使用原金鑰，可將舊 `SESSION_SECRET` 放入選用欄位 `server.session_secret`。更新前簽發的 cookie 沒有 email 欄位，需重新登入一次；日後更新內部帳號規則並重啟，既有 session 在下次 API 請求也會套用新規則。
- **Gitea repository 搜尋**使用部署設定的 `gitea.token` 個人存取權杖，列出該 token 可讀取的公開與私有 repository。未設定時，使用目前登入管理員的 Gitea OAuth token；更新後既有管理員需重新登入以授權 `read:repository`。Git 抓取及更新原始碼仍使用 `gitea.token` 或服務帳號既有的 Git 認證。
- **Gitea 登入回呼**為 `server.public_origin` 加 `server.prefix` 加 `/auth/callback`。將完整回呼登錄到 Gitea OAuth 應用程式，並填入 `gitea.client_id`、`gitea.client_secret`。
- **上游服務**在 `opencode.url` 填專用 OpenCode Server 的基底位址。可用 `opencode.model` 指定每回合的模型，格式為 `providerID/modelID`，僅以第一個斜線分隔。管理員可在側欄「查案模型設定」從 OpenCode 的 `GET /config/providers` 清單選取模型，寫入 SQLite 後優先於 YAML。新案、追問與重試會在每次送出前讀取，免重啟。兩處均未設定時交由 OpenCode 選擇。若啟用 Basic Auth，設定 `opencode.password` 與選用的 `opencode.username`。後端與 OpenCode 需能以相同絕對路徑存取 run root，OpenCode 也須能讀取案件子目錄（例如以相同服務帳號執行）。

## 服務日誌

服務同步輸出到終端機 stderr 和 `server.state_dir/logs/case-investigator.log`。可用 `logging.directory` 指定目錄。日誌包含啟動及監聽位址、HTTP 狀態與耗時、案件建立、Git 同步、OpenCode 等待與回覆。

以主機當地午夜為切點，跨日後的第一筆紀錄會將舊檔更名為 `case-investigator.log.YYYY-MM-DD`。服務在啟動和輪替時清理超過 `logging.retention_days` 的日期檔，預設保留前 14 天及當天。可用 `tail -F` 持續查看目前的日誌。

## Traefik

設定 `server.prefix` 與 `traefik.upstream` 後，執行 `make route > deploy/cases.toml`，自訂檔案路徑可加 `CONFIG=/path/to/config.yml`。將產物放入現有 Traefik file provider 目錄。使用專用 router 或沿用環境的 entrypoint 與 TLS 設定。此 route 保留 CASE_PREFIX，請勿套用 StripPrefix。瀏覽器開啟 CASE_PREFIX 時，後端會導向帶尾斜線的入口，靜態資源、API 與登入回呼皆經過該 route。上游逾時應涵蓋 Git 同步與模型回覆時間。

## 查案資料與執行方式

1. 管理員在「管理程式索引」須填寫程式名稱與含檔名的絕對路徑（後端確認檔案存在後才儲存），可搜尋 Gitea repository。選取 repository 時須指定 main 或 master，並可填入口檔。未指定 repository 時，直接依 run root 的內容查案。Repository 搜尋使用已設定的 Gitea token，未設定時查詢公開清單。同步使用服務帳號預設的 Git credential helper。
2. 開案時，服務先確認服務帳號可讀取與進入填寫的執行目錄。有選取 repository 時，才將指定分支淺層 clone 至該位置的 `.opencode-tmp/case-*/source/`，每個案件使用獨立目錄，並記錄當時的 commit。未填入口檔時，依程式檔名在對應 repository 內找唯一符合的檔案。
   新建立的 `.opencode-tmp`、案件目錄與其子目錄使用 run root 的群組，權限為 `770`。一般檔案及附件為 `660`，原本可執行的程式為 `770`。同群組成員可修改檔案、清空案件目錄。刪除 run root 本身還需要其上一層目錄的刪除權限。各站點的服務帳號各自須能將產物設成對應 run root 的群組。
   舊版已建立的 clone 保留原權限。若要清理含舊案件的 run root，請由產生檔案的服務帳號在對應主機上將舊案件子目錄改成 run root 的群組並給予群組讀、寫、進入權限。
3. 案件擷取執行位置第一層最多六個 log、err、out 檔，各取末尾 6000 bytes。程式入口最多擷取前 32000 bytes。OpenCode 可以從 run root 檢閱執行資料，也可以依案件提供的原始碼路徑讀取完整 checkout。
4. 每案有獨立 OpenCode session。首次查案、使用者追問和管理員接手時，後端都指定 `investigate-web` agent。部署時須讓 OpenCode 從使用者提供的 run root 也能載入這個 agent。管理員可在程式索引選填 OpenCode Skills。新案件會將索引中的程式路徑傳給 agent，並允許 `read`、`glob`、`grep` 存取程式所在目錄及子目錄。管理員儲存索引時，也會記錄 symlink 解析後的程式目錄。若舊索引的 symlink 指向另一個目錄，或部署目標更換目錄，請重新儲存索引並開新案。另依所選 Skill 授權其名稱與附檔目錄。Skill 名稱沿用開案時的設定，Bash 指令會直接被拒絕。部署時使用專用且受限的 OpenCode 作業系統帳號，限制可讀取的部署資料範圍。升級前建立的 session 仍使用原快照目錄；要調查 run root 請重新開案。模型和 provider 憑證由 OpenCode 自己管理。每個有圖片的回合會先把原圖寫入該案子目錄下的 `attachments/message-<訊息編號>.png` 或 `.jpg`，再傳送文字路徑請 agent 用 `read` 開啟。聊天室預覽沿用 SQLite 中的附件。圖片辨識仍取決於工具與模型的影像能力。
5. 瀏覽器先儲存使用者訊息，再等待該回合回覆。服務使用本機檔案鎖避免同案並行送出。等待 OpenCode 調查回覆沒有應用程式時限。案件擁有者或管理員可以點「終止查案」中止該次回合，再於同案繼續提問。代理層的連線逾時需另行設定。案件及附件保存在部署環境。此版以單機部署為目標。

## 跨主機查案

網站、Gitea 登入、程式索引與 `cases.sqlite` 集中在網站主機。在各站點分別啟動 OpenCode，各自設定獨立的本機資料庫，並部署 `investigate-web`、所選模型與 Skills。管理頁面的模型及 Skill 選項來自本機 OpenCode，建案時會向選定主機確認所選的 Skill。

1. 遠端主機只需啟動 OpenCode。網站主機透過 SSH 使用遠端的 `python3` 和標準函式庫處理檔案，操作結束後程序即退出。遠端的 SSH 帳號應與 OpenCode 使用相同 UID，以便存取案件目錄。有設定 Gitea repository 的案件還需要遠端可執行 Git。
2. 在網站主機的 `config.yml` 以 `opencode.local_site` 設定本機顯示名稱（省略時為 `local`）。在 `opencode.ssh_sites` 下為每個遠端建立一個固定代號，如 `remote`。可用該項的 `name` 指定顯示名稱，省略時使用代號。各站顯示名稱須唯一。依範例填入 OpenCode URL 和 SSH 目的地。遠端需要 SSH 密碼時設定 `ssh_password`，留空時沿用 SSH key 或 agent。該站的 `username`、`password` 用於 OpenCode HTTP API。含密碼的設定檔請限服務帳號讀取。
   - 若遠端的非互動式 SSH 環境找不到 `python3`，在該站設定 `python: "/usr/local/python/3.9.10/bin/python3.9"`，換成遠端主機上實際可執行的絕對路徑。省略時沿用 `python3`。
   - 預設 `host_key_checking: strict`，使用網站服務帳號原有的 OpenSSH 信任設定，主機金鑰須事先登錄。
   - 設為 `host_key_checking: accept-new` 可在首次連線時信任並保存主機金鑰。預設檔案為網站主機的 `<server.state_dir>/ssh_known_hosts`。兩種模式都會拒絕已變更的金鑰，都可用 `known_hosts` 指定檔案。相對路徑以 `config.yml` 所在目錄為準。
   - 修改設定後重啟查案網站。若連線失敗，可在 `case-investigator.log` 查詢 SSH 目的地、操作與失敗原因。
3. 升級時沿用原本的 `server.state_dir`。啟動時在交易內為案件新增站點欄位，尚無站點欄位的舊案件歸屬本機，已有站點的案件保留原有對應，既有程式索引繼續使用。若遠端的程式是指向其他目錄的 symlink，管理員只需重新儲存該筆索引，即可記錄遠端的實體程式目錄並授予讀取權限。升級時保留現有 `ssh_sites` 的鍵及其對應的 OpenCode 資料庫。日後要更名，只改本機的 `local_site` 或遠端的 `name`，既有案件與程式索引會沿用原站點。

使用者報案時選擇執行主機並填寫該機上的絕對路徑。群組寫入權限、log、Gitea clone 與圖片附件均由該機處理。追問、查詢思考過程、終止與刪除都連回案件原站點。遠端 clone 須能從遠端主機存取設定的 Gitea 網址。
