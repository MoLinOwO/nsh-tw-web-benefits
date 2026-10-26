# 逆水寒台服官網福利工具

`nsh-tw-web-benefits` 面向《逆水寒》台服官網的會員中心、簽到與福利活動。提供 Python 登入 session 擷取、重用與恢復、免費簽到與會員獎勵領取，以及可供代理讀取的 `SKILL.md`。玩家也可以獨立使用 Python CLI，不需要 Agent、MCP、Codex 或 Hermes。

目前沒有桌面 GUI 或 Windows EXE 包裝；玩家需使用 Python CLI。

一般玩家的使用順序是：安裝依賴 → 開啟瀏覽器的 9222 → 手動登入與綁定 → 保存兩站 session → 查詢／執行領取。Windows 玩家依下方 [首次綁定或重新擷取](#首次綁定或重新擷取) 操作，Linux 玩家依 [玩家快速流程](#玩家快速流程linux) 操作；不需安裝任何 Agent。

支援 [VIP 會員中心](https://www.swordofjustice.com/h5/20260424/vip/index.html#/) 與 [週三小確幸](https://www.swordofjustice.com/h5/20260722/officialwebfasthmt/#/)。

**不需要 Chrome DevTools MCP。** 工具直接使用 HTTP 與瀏覽器的 Chrome DevTools Protocol（CDP）。CAPTCHA、登入與角色綁定由使用者手動完成。

## 目前狀態與限制

已在 Windows / Edge 與原開發帳號驗證兩站的恢復流程，包括頁面從未登入恢復顯示角色。WSL Ubuntu 24.04 / Python 3.12.3 的獨立虛擬環境中，48 項離線測試及命令列基本操作通過，包含 POSIX 檔案與目錄權限檢查；尚未驗證 Linux 瀏覽器的完整綁定與實際領獎。Chrome 與其他使用者首次綁定的完整流程也尚未實機驗證。

目前 `capture` 會從 localStorage 取第一枚含 `roleId` 的 `*_roleToken`，**不保證它是本次新綁定或目前帳號的憑證**，也可能讀到過期值。工具尚未實作保存前身份確認或完整帳號隔離。請在專用、乾淨的瀏覽器資料目錄操作，擷取後核對角色資訊。增加 `--wait` 不能解決舊資料被誤選的問題。

`restore` / `mint` 可能從同一份 session 檔的另一站借用角色憑證，因此一份檔案只能保存同一個帳號的兩站資料。`--source` 是來源站點選擇，並非嚴格的帳號隔離機制。

## 安裝

需要 Python 3.10 以上、Git，以及可使用遠端偵錯的 Google Chrome 或 Microsoft Edge。以下以 Windows PowerShell 為例：

```powershell
git clone https://github.com/MoLinOwO/nsh-tw-web-benefits.git
cd nsh-tw-web-benefits
python -m pip install -r requirements.txt
```

未安裝 Git 的玩家也可以在 GitHub 選擇 **Code → Download ZIP**，解壓縮後開啟終端機並進入資料夾，略過上述 clone 與 cd 步驟。

### Linux 桌面安裝

下載 repo 後建立虛擬環境並安裝相同依賴：

```bash
git clone https://github.com/MoLinOwO/nsh-tw-web-benefits.git
cd nsh-tw-web-benefits
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

若已下載 repo，直接在其根目錄執行後兩行。Ubuntu / Debian 若缺少 venv 支援，先依系統套件管理方式安裝 `python3-venv` 再建立環境。

預設 session 路徑為 `${XDG_STATE_HOME:-$HOME/.local/state}/sword-session/sessions.json`。相對的 `XDG_STATE_HOME` 會忽略並退回 `$HOME/.local/state`。POSIX 每次儲存都會將 session 檔設為 `0600`；新建的最末層 store 目錄設為 `0700`，既有目錄權限不會變更。預設路徑變更不會遷移既有自訂 session 檔；若已使用自訂位置（例如先前由 Hermes 工作流程指定的位置），請繼續以 `--store /path/to/your-existing-session.json` 指定該既有路徑。可為不同帳號指定各自的檔案，建議放在 repo 外，例如：

```bash
sessionFile="$HOME/.local/state/sword-session/account-a.json"
cdpUrl='http://127.0.0.1:9222'
```

預設 Edge 設定目錄為 `${XDG_CONFIG_HOME:-$HOME/.config}/microsoft-edge`；相對的 `XDG_CONFIG_HOME` 會忽略並退回 `$HOME/.config`。若想使用獨立瀏覽器資料，按下節選擇 Chrome 或 Edge 並指定專用目錄。

## 玩家快速流程（Linux）

首次綁定需要可操作的 GUI 瀏覽器。先依下一節擇一啟動 Chrome 或 Edge，再於 repo 根目錄執行：

```bash
.venv/bin/python scripts/sword_session.py --cdp-url "$cdpUrl" --store "$sessionFile" capture vip --wait 300
```

在開啟的活動頁以自己的帳號登入、完成驗證及角色綁定；完成後核對輸出的身份。接著簽發 official session，先查詢兩站福利，再由玩家明確決定是否執行領取：

```bash
.venv/bin/python scripts/sword_session.py --store "$sessionFile" mint official --source vip
.venv/bin/python scripts/sword_session.py --store "$sessionFile" benefits all
.venv/bin/python scripts/sword_session.py --store "$sessionFile" benefits all --claim
```

`benefits all` 預設唯讀；`--claim` 才會送出簽到或已符合條件的免費獎勵請求。這些命令是單次執行，不會建立 cron 工作。首次 `capture` 需要 GUI，session 有效後，Linux headless 環境可透過 HTTP 執行 `restore`（不帶 `--browser`）、`status`、`mint` 與 `benefits`，不需 CDP。請先閱讀上方的 [目前狀態與限制](#目前狀態與限制)；成功分支尚未在真實可領獎帳號上驗證，不保證可領資格或遊戲內入帳。

沒有桌面的 Linux 主機可先在自己的桌面電腦完成綁定，再將自己的 session 檔安全移至主機，設為 `0600` 權限，並用 `--store` 指向該檔案。憑證失效後仍需回到可操作瀏覽器的環境重新綁定。

## 首次綁定或重新擷取

準備專用的瀏覽器 User Data 目錄及自己的 session 檔案。這兩個路徑應位於 repo 以外：

```powershell
$sessionFile = Join-Path $env:LOCALAPPDATA 'sword-session\account-a.json'
$cdpUrl = 'http://127.0.0.1:9222'
```

### 開啟 Chrome 的 9222

先確定 9222 未被其他瀏覽器占用，再執行：

```powershell
$browserData = Join-Path $env:LOCALAPPDATA 'SwordSessionBrowser\chrome-account-a'
$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
Start-Process -FilePath $chrome -ArgumentList @(
    '--remote-debugging-port=9222',
    "--user-data-dir=`"$browserData`"",
    '--remote-allow-origins=http://127.0.0.1:9222,http://localhost:9222'
) -WindowStyle Normal
```

Chrome 若不是安裝在上述位置，也可能位於 `C:\Program Files (x86)\Google\Chrome\Application\chrome.exe` 或 `%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe`，請調整 `$chrome`。allow-origins 只允許本工具使用的本機 Origin，不要改成 `*`。

Chrome 136 起，遠端偵錯旗標必須搭配非預設的 User Data 目錄；上述指令會使用專用目錄，需要在其中重新登入。見 [Chrome 官方說明](https://developer.chrome.com/blog/remote-debugging-port)。

Linux 上可用安裝的 `google-chrome` 執行檔。請確認 9222 空閒，再選 Chrome 或 Edge 其中一個啟動：

```bash
browserData="$HOME/.local/share/sword-session/chrome-account-a"
google-chrome --remote-debugging-port=9222 \
  --user-data-dir="$browserData" \
  --remote-allow-origins=http://127.0.0.1:9222,http://localhost:9222 &
```

`--remote-allow-origins` 限定本機來源，不要改為 `*`。該目錄是專用瀏覽器 profile，需要在其中登入。

### 開啟 Edge 的 9222

Chrome 與 Edge 使用同一個 9222 埠時，請擇一啟動；切換前先關閉占用該埠的偵錯瀏覽器。Edge 指令為：

```powershell
$browserData = Join-Path $env:LOCALAPPDATA 'SwordSessionBrowser\edge-account-a'
$edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
Start-Process -FilePath $edge -ArgumentList @(
    '--remote-debugging-port=9222',
    "--user-data-dir=`"$browserData`""
) -WindowStyle Normal
```

Edge 若安裝在 `C:\Program Files\Microsoft\Edge\Application\msedge.exe`，請調整 `$edge`。參考 [Edge 官方 DevTools Protocol 說明](https://learn.microsoft.com/en-us/microsoft-edge/devtools/protocol/)。

Linux 上可改用 `microsoft-edge-stable`，並使用獨立資料目錄：

```bash
browserData="$HOME/.local/share/sword-session/edge-account-a"
microsoft-edge-stable --remote-debugging-port=9222 \
  --user-data-dir="$browserData" &
```

Chrome 與 Edge 共用 9222，擇一啟動；切換前先關閉目前的偵錯瀏覽器。確認本機 CDP 可用後，依玩家快速流程使用 `--cdp-url http://127.0.0.1:9222` 擷取。Linux 預設 Edge 設定目錄為 `${XDG_CONFIG_HOME:-$HOME/.config}/microsoft-edge`；使用 `--user-data-dir` 時，上例專用目錄會取代該預設值。

### 確認連線並擷取

兩種瀏覽器都可以用以下指令確認 CDP 位址：

```powershell
Invoke-RestMethod "$cdpUrl/json/version" | Select-Object Browser, webSocketDebuggerUrl
```

工具不會自動啟動瀏覽器。`--cdp-url` 明確指定本機 CDP HTTP 位址，優先於 `--edge-udd`，不需要 DevToolsActivePort 檔案。務必核對回應的 Browser 與實際啟動的瀏覽器相符。

```powershell
python scripts/sword_session.py --cdp-url $cdpUrl --store $sessionFile capture vip --wait 300
```

若使用 Edge 的「允許遠端偵錯」模式，HTTP 端點可能回 404，但 User Data 目錄有有效的 DevToolsActivePort，可改用 `--edge-udd $browserData` 取代 `--cdp-url $cdpUrl`。雖然參數名稱保留 edge，實際可指定 Chrome 或 Edge 的 User Data 根目錄；不能指向其下的 Default 子目錄。Chrome 固定埠模式應使用 --cdp-url，不要依賴 port 檔案。

在工具開啟的頁面登入自己的帳號、完成驗證與角色綁定。`capture` 最多等待指定秒數，取得憑證後會嘗試簽發並保存 session。它會監看請求並讀取 localStorage，不是系統層級的封包側錄器。

擷取完成後，將下列輸出的 `aid`、`role_id`、`server_id` 與自己的帳號資料核對，並確認頁面上的角色名稱：

```powershell
python scripts/sword_session.py --store $sessionFile list
python scripts/sword_session.py --store $sessionFile status
```

若身份不符，停止使用該 session；不要注入或接續活動操作。若簽發失敗，也不能視為已重新綁定成功。

同一枚有效角色憑證已驗證可用於本工具支援的兩站。核對完成後，可為另一站簽發：

```powershell
python scripts/sword_session.py --store $sessionFile mint official --source vip
```

## 恢復登入

只更新本地 session、供 HTTP 自動化使用，不需要瀏覽器：

```powershell
python scripts/sword_session.py --store $sessionFile restore vip
python scripts/sword_session.py --store $sessionFile status
```

同時讓瀏覽器頁面顯示登入，需要前述 Chrome 或 Edge 的 CDP 連線：

```powershell
python scripts/sword_session.py --cdp-url $cdpUrl --store $sessionFile restore vip --browser
python scripts/sword_session.py --cdp-url $cdpUrl --store $sessionFile restore official --browser
```

`--browser` 會注入活動 account cookie，並重新載入對應頁面。請另行核對頁面角色與「登出」入口；`status` 的 `code=0` 只表示 API 接受 session，不代表已驗證頁面顯示。

角色憑證過期或被伺服器拒絕時，需要重新手動綁定及 `capture`。不要反覆重試簽發，也不要為了重綁而清除整個日常瀏覽器的 cookie。

## 自動簽到與免費領獎

使用已保存的 session 即可，不需要瀏覽器、CDP 或 MCP。預設只查狀態：

```powershell
python scripts/sword_session.py --store $sessionFile benefits all
```

加上 `--claim` 才會執行簽到與領獎，也可將 all 換成 vip 或 official：

```powershell
python scripts/sword_session.py --store $sessionFile benefits all --claim
```

支援項目：

- 週三小確幸：今日簽到；網站回傳 `signed_today=true` 時跳過。
- VIP 會員中心：已解鎖的日、週、月、賽季禮包、可補領差額，以及可領取的累計登入天數獎勵。

已領取與未達條件的項目會顯示狀態並跳過。此指令只處理上述免費項目，且是單次執行，不會自動設定排程。

每次執行先向網站核對 aid、角色、伺服器與活動。選擇 all 時，兩站必須是相同帳號與角色，且兩站的身份及狀態格式都確認通過後才開始領取；這項檢查不代表 capture 已具有保存前身份確認功能。

每個可領項目在送出前會重新查一次狀態，成功後再查是否已標記完成。一次 POST 失敗、逾時、要求重新驗證或後續狀態無法確認時會停止，不自動重送；重新執行前請先用不帶 --claim 的指令查詢狀態。網站標記已領取與遊戲信箱實際入帳是不同層次，後者需在遊戲內確認。

2026-10-26 的實機查詢顯示，原開發帳號的今日簽到、日／週禮包及登入里程碑已完成，其他禮包未達條件。當日沒有可新增領取的項目；實機驗證涵蓋查詢及跳過，實際可領取分支以 mock 回歸測試驗證，不能宣稱已成功發送新獎勵。

## 指令參考

全域參數 `--store`、`--cdp-url` 與 `--edge-udd` 放在子指令之前。使用 --cdp-url 時，工具直接連該位址，不會核對它是否屬於 --edge-udd 所指的目錄。

| 指令 | 用途 | 需要瀏覽器／CDP |
|---|---|---|
| `capture vip --wait 300` | 取得角色憑證並保存 session | 是 |
| `restore vip` | 用既有角色憑證重新簽發 | 否 |
| `restore vip --browser` | 重簽、注入 cookie 並重載頁面 | 是 |
| `mint official --source vip` | 為另一站簽發 | 否 |
| `refresh vip` | 嘗試重簽，失敗或過期時退回 capture | 退回 capture 時需要 |
| `status` | 呼叫兩站首頁 API 檢查 session | 否 |
| `list` | 顯示保存的身份、時間與憑證到期資訊 | 否 |
| `benefits all` | 只查簽到與免費會員獎勵狀態 | 否 |
| `benefits all --claim` | 執行符合條件的免費簽到／領獎 | 否 |
| `logout vip --browser` | 呼叫登出 API 並清除部分頁面登入資料 | 是 |

`logout --browser` 會影響 `.swordofjustice.com` 共用的活動 cookie，並移除該網頁 origin 下符合 `roleToken` / `loginAid` 的 localStorage key，可能影響同源的其他活動。它不會完整清除 UniSDK 登入，不能當成完整的帳號切換工具。

## 分享與多使用者

可以分享此 repo 或將整個 skill 資料夾交給其他使用者；每個人必須用自己的瀏覽器與帳號完成綁定。不要分享 `sessions.json`、自訂 session 檔、備份、HAR、token 或瀏覽器資料目錄。

有效的 `roleInfoToken` 持有者可重用對應角色的活動登入權限。這不等於已驗證具備整個遊戲帳號的控制權，但仍應將它視為憑證。

同一個 Windows 使用者管理多個帳號時，為每個帳號分別指定 `$browserData` 與 `$sessionFile`；只分開 `--store` 不會隔離瀏覽器。Windows 預設 session 為 `%LOCALAPPDATA%\sword-session\sessions.json`；若 `LOCALAPPDATA` 未設定，退回使用者 home 下的 `AppData/Local`。Linux 預設 session 為 `${XDG_STATE_HOME:-$HOME/.local/state}/sword-session/sessions.json`，Edge 設定目錄為 `${XDG_CONFIG_HOME:-$HOME/.config}/microsoft-edge`；相對 XDG 路徑會忽略。兩平台都是每位 OS 使用者一份，不是每個遊戲帳號一份。同一使用者管理多個遊戲帳號時，為各帳號指定獨立 `--store` 與瀏覽器資料目錄。

session 檔包含明文憑證。POSIX 每次儲存都將檔案設為 `0600`；新建的最末層 store 目錄設為 `0700`，既有目錄權限不變。這不是加密。Windows ACL 設定仍依本機實作狀態處理，不應把 session 當成加密資料。

## 作為 Skill 使用

將 repo 放到代理支援的 skills 目錄，資料夾名稱為 `swordofjustice-session`，並讀取 [SKILL.md](SKILL.md)。Codex 預設位置為 `%USERPROFILE%\.codex\skills\swordofjustice-session`；安裝後需依客戶端方式重新載入 skills。

只執行 Python 工具不需要 Codex、Hermes 或 MCP。詳細網站欄位與觀察記錄見 [references/protocol.md](references/protocol.md)。這些網站介面為當次觀察，未來可能改變。

## 測試

```powershell
python -m unittest discover -s scripts -p 'test_*.py' -v
```

測試使用假資料與 mock，不會呼叫網站或修改瀏覽器；涵蓋 CDP 位址探索、身份核對、已完成項目跳過、符合條件時的領取參數、領取後驗證，以及失敗不重送。測試通過不代表 Chrome 或新使用者的首次綁定已通過實機驗證，也不代表遊戲內實際收到獎勵。

## 連線排查

- 找不到 `DevToolsActivePort`：固定 9222 埠請使用 --cdp-url http://127.0.0.1:9222；只有使用 --edge-udd 的探索路徑才依賴此檔案。
- CDP 回 403：確認瀏覽器允許偵錯連線，並確認埠與 User Data 目錄相符。
- `DevToolsActivePort` 存在仍連不上：檔案可能殘留，不代表瀏覽器目前可連線。不要只憑 `/json/version` 回 404 就判定 CDP 未啟用；本機曾在該端點回 404 時仍能連到檔案記錄的 WebSocket。
- API 連線失敗：先排查網路與服務回應，不能直接推定 token 過期。
