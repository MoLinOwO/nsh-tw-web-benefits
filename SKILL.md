---
name: swordofjustice-session
description: 管理逆水寒台服官網會員中心與簽到福利活動的已登入 session。當需要重新取得 VIP 會員中心或「週三小確幸」的登入狀態、恢復既有授權或重新綁定自己的帳號時使用。目前提供登入管理，尚未實作自動簽到或領獎。
---

# 逆水寒台服官網福利：登入管理

用 `scripts/sword_session.py` 管理 swordofjustice.com 台服官網會員中心與簽到福利活動頁（vip、official）的已登入 session。Repository 名稱為 nsh-tw-web-benefits，skill 識別名稱為 swordofjustice-session。

## 適用範圍

只處理使用者自己的帳號與已授權的角色綁定。工具**不繞過 CAPTCHA 或 NetEase 驗證**：角色綁定與驗證一律由使用者手動完成，工具只擷取與重用既有的授權憑證。

## 關鍵機制

本機帳號的測試顯示，登出後仍可用既有 `roleInfoToken` 簽發活動 session。這是當次觀察，不是網站保證的長期介面契約：

| 層 | 登出後 | 備註 |
|---|---|---|
| `L36NAXX2HMT_UNISDK_TOKEN` / `_AUTH` | 依網站登入流程而異 | 工具的 logout 不會主動清除這兩枚 cookie |
| `nshmhmt_common_h5_account_token` | 登出 API 回應要求刪除 | 不能僅憑 Set-Cookie 判定伺服器是否撤銷舊 token |
| `roleInfoToken` | 測試中仍有效 | 觀察到的 JWT payload 為 exp / roleId |

恢復路徑是拿仍有效的 `roleInfoToken` 換一份新的 activity account token，再按需要寫回瀏覽器 cookie。token 過期、被拒絕或網站重新要求驗證時，需要使用者手動綁定；不能只靠本地 exp 推定伺服器一定接受。

詳細 API 與欄位見 [references/protocol.md](references/protocol.md)。

## 常用指令

```powershell
python scripts/sword_session.py capture vip --wait 180   # 開頁面監封包，等使用者手動綁定
python scripts/sword_session.py restore vip --browser    # 重簽 token 並注入 Edge、重新載入頁面
python scripts/sword_session.py logout vip --browser     # 伺服器登出 + 清 Cookie 與 localStorage
python scripts/sword_session.py status                  # 用已保存 session 打 API 檢查有效性
python scripts/sword_session.py list                    # 列出各站 session 與 roleInfoToken 有效期
python scripts/sword_session.py mint official            # 用已擷取的 roleInfoToken 為另一站簽發
python scripts/sword_session.py refresh vip              # 失效時重簽，失敗則退回要求手動綁定
```

站點 key 只有 `vip`（會員中心）與 `official`（週三小確幸）。以上指令從 skill 根目錄執行，或把腳本路徑換成安裝位置的絕對路徑。

## 執行前提

- capture 與帶有 --browser 的指令需要 Chrome 或 Edge 開啟遠端偵錯。固定 9222 埠使用全域參數 --cdp-url http://127.0.0.1:9222，透過 /json/version 取得 WebSocket 位址。不帶 --cdp-url 時才讀 --edge-udd 所指目錄的 DevToolsActivePort；參數名稱保留 edge，但目錄也可屬於 Chrome。--cdp-url 優先，工具不會核對位址與資料目錄是否相符。
- Chrome 與 Edge 的 9222 啟動指令及檢查方式見 [README.md](README.md#首次綁定或重新擷取)。Chrome 136 起需使用非預設的 --user-data-dir；兩種瀏覽器不能同時占用 9222。只允許本機 CDP 位址。
- 需要 `requests` 與 `websocket-client`：`pip install requests websocket-client`。
- 首次使用需要 `capture`：由使用者在瀏覽器手動登入、驗證並綁定角色，工具從封包或 localStorage 取得 `roleInfoToken`。先讀下方「重新擷取與目前限制」。

## MCP、瀏覽器與 HTTP 的依賴

這個 skill 不使用 Chrome DevTools MCP，也不依賴 MCP 伺服器存在。所有能力都在 `scripts/sword_session.py` 內，用 `requests` 直接打 API、用 `websocket-client` 直接講 CDP。

其中**恢復路徑完全不需要瀏覽器**：

```python
import sword_session as S
store = S.load_store(S.DEFAULT_STORE)
session = S.build_session("vip", store["vip"]["role_info_token"], None)
```

`build_session` 只做 HTTP 呼叫，簽發出來的 `account_token` 拿去打活動 API 即可，完全不碰 CDP。`--browser` 只是選擇要不要順便把 cookie 注入 Edge 讓頁面顯示登入狀態。

各指令需要的條件：

| 操作 | 瀏覽器／CDP | MCP |
|---|---|---|
| capture、重新手動綁定 | 需要 | 不需要 |
| restore 不帶 --browser、mint、status | 不需要，只需有效的本地憑證 | 不需要 |
| restore --browser、logout --browser | 需要 | 不需要 |
| refresh | 重簽成功時不需要；退回 capture 時需要 | 不需要 |

## 重新擷取與目前限制

同一帳號的 session 失效時，可先嘗試一次 restore；若角色憑證被拒絕，改為手動綁定並 capture，不要反覆重簽。重新擷取指令為：

```powershell
python scripts/sword_session.py capture vip --wait 300
```

目前 capture 會取 localStorage 裡第一枚含 roleId 的 *_roleToken，沒有依活動、到期時間或本次登入篩選，也沒有保存前的身份確認。因此它可能在使用者完成新綁定前，就讀到舊帳號或過期 token；增加 --wait 不會解決這點。不能把 capture 成功解讀成「已取得本次新憑證」。

重新綁定時先在使用者授權的乾淨、專用瀏覽器資料目錄操作，避免舊登入殘留；擷取後用 list 核對 aid、role_id、server_id，並與頁面上的角色核對。身份不符就停止，不要注入或執行後續活動。不能為了排查清除整個日常瀏覽器的 cookie。

目前尚未實作：只接受本次新綁定憑證、保存前身份確認、帳號隔離。現有測試沒有涵蓋真實的新使用者首次綁定，不得宣稱已完成該流程的端到端驗證。

## 換人使用會發生什麼

`roleInfoToken` 是伺服器簽發給**特定帳號**的 JWT，payload 含 `roleId` 與 `exp`，簽章由伺服器驗證。實測結果：

- 拿 A 帳號的 `roleInfoToken` 簽發，得到的 `channelExtra` 一律是 A 的 `roleId` / `roleName` / `serverId`，換 activity 也一樣（`roleInfoToken` 不分站點）。
- 偽造 JWT（改 `roleId` 但保留原簽名）會被伺服器拒絕：`code=5`「数据错误」。

持有有效 roleInfoToken 的人能重用對應角色的活動登入權限。不要分享 token、sessions.json、其備份或瀏覽器 profile；不要把一次篡改被拒絕的測試擴大解讀為完整的安全保證。

其他人在自己電腦使用時，交付整個 skill 目錄即可。他們需安裝 Python、requests、websocket-client，以自己的 Chrome 或 Edge 完成手動綁定並保存自己的 session。Chrome 完整登入流程尚未實機驗證；腳本預設路徑以 Windows 為主，其他平台也尚未驗證。

預設 session 存在目前作業系統使用者的 %LOCALAPPDATA%\sword-session\sessions.json；這是「每個 Windows 使用者一份」，不是「每個遊戲帳號一份」。同一個 Windows 使用者切換遊戲帳號會共用檔案。

在同一台電腦管理多個人的帳號時，至少要分開瀏覽器 User Data 目錄與 --store 檔案，所有指令都指定相同的一組路徑；--store 本身不會隔離瀏覽器：

```powershell
python scripts/sword_session.py --edge-udd 'D:\SwordProfiles\user-a' --store 'D:\SwordSessions\user-a.json' capture vip --wait 300
python scripts/sword_session.py --store 'D:\SwordSessions\user-a.json' mint official --source vip
python scripts/sword_session.py --edge-udd 'D:\SwordProfiles\user-a' --store 'D:\SwordSessions\user-a.json' restore vip --browser
```

路徑為示例，瀏覽器需事先以對應 User Data 目錄開啟遠端偵錯。工具不會自動建立或啟動這些 profile。同一份 store 只能保存同一個帳號的資料：restore / mint 的來源選擇會退回另一個活動的 token，沒有核對是否同一帳號。

## 重要行為細節

- 同一枚 roleInfoToken 已驗證可用於本工具支援的兩站；其他活動未驗證。
- logout --browser 會刪除共用的活動 account cookie，以及目前網頁 origin 下所有符合 roleToken / loginAid 的 localStorage key，可能影響同源的其他活動；它不會完整清除 UniSDK 登入。只有使用者要求登出或測試時才執行。
- 不使用全瀏覽器 cookie 清除操作，也不使用未確認存在的 Storage.clearCookies 方法。
- save_store 會嘗試設定檔案 ACL，授權目前使用者、SYSTEM 與 Administrators；程式沒有檢查 icacls 是否成功，不能宣稱只有本人能讀或已驗證保護成功。
- status 的 code=0 代表該 API 接受 session；不能單靠它證明瀏覽器頁面顯示登入。restore --browser 後另行讀取頁面，核對角色及登出入口。
- 連線失敗先查網路或 CDP，不要直接推定 token 過期。需要重新啟動瀏覽器時，保留使用者原有分頁與 profile，遵循當前授權。

## 驗證

```powershell
cd scripts
python -m unittest test_sword_session -v
```

單元測試不需網路，涵蓋 JWT 解析、來源選擇、指令註冊與 cookie 名稱等不變量。改動腳本後必須跑一次。

## 排查

- 頁面顯示「未登入」但 `status` 正常：瀏覽器端 cookie 沒注入，用 `restore --browser` 重跑。
- `restore` 報簽發失敗：多半是 `roleInfoToken` 過期，需要使用者重新手動綁定並再 `capture`。
- `capture` 抓到 token 但角色是「未綁定」：使用者只登入了 UniSDK，還沒綁定角色，請在頁面上完成綁定。
