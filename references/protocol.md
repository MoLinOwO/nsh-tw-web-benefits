# 網站 protocol 細節

所有觀察自 `www.swordofjustice.com` 的 H5 活動頁，API host 為 `https://api.swordofjustice.com`。

## 端點

### 簽發 activity account token

```
GET /public/nshm/common_activity_auth/web/login/account
    ?channel=unisdk
    &activityName=<activity>
    &roleInfoToken=<jwt>
```

Headers：`Origin: https://www.swordofjustice.com`、`Referer: <該活動頁網址>`。

回應 JSON `code == 0` 時，`data` 內含 `aid`、`username`、`channelExtra`，後者帶 `roleId`、`roleName`、`serverId`、`serverName`。

**token 本身在 `Set-Cookie` 裡**，不在 JSON body：

```
nshmhmt_common_h5_account_token=<jwt>; domain=.swordofjustice.com; path=/; secure; HttpOnly
```

### 登出

```
GET /public/nshm/common_activity_auth/logout/account
```

帶上 `nshmhmt_common_h5_account_token` cookie。觀察到回應 code == 0，並以 value=deleted; Max-Age=0 要求刪除該 cookie；這不能證明伺服器撤銷了所有舊 token。Python HTTP 呼叫也不會自動更改瀏覽器的 cookie，需另行操作。

本機帳號測試中，此操作後 roleInfoToken 仍能簽發；其他帳號、其他登出流程或未來版本可能不同。

### UniSDK 登入狀態

```
GET /public/share/common_auth/unisdk/getlogin/l36naxx2hmt
```

### 活動首頁 probe

用來檢查 session 是否仍有效：

- vip：`GET /nshm-hmt-vip-center-server/activity/home-page`
- official：`GET /nshm-nshna-officialwebfast-2026/front/home/data`

official 的 API **強制要求 `area: hmt` header**，缺少會回 `code 20002`。

## 站點設定

| key | 活動 | activityName | api base | probe |
|---|---|---|---|---|
| `vip` | 會員中心 | `nshm_viphmt_20260424` | `/nshm-hmt-vip-center-server` | `/activity/home-page` |
| `official` | 週三小確幸 | `nshm_offwebfast2_20260730` | `/nshm-nshna-officialwebfast-2026` | `/front/home/data` |

## roleInfoToken

來源有兩個，內容等價：

1. 封包中 `GET /public/nshm/common_activity_auth/web/login/account?...roleInfoToken=...` 的 query string。
2. localStorage 的 `<activity>_roleToken`（例如 `nshm_viphmt_20260424_roleToken`）。

觀察到 roleInfoToken 的 JWT payload 為 exp 與 roleId。roleName、serverId 等資料來自簽發 API 回應的 channelExtra，aid 位於 data；不要混淆兩種資料。

同一枚 token 已驗證可為本工具的 vip 與 official 簽發 account token；不能據此推論所有活動都支援。

## 瀏覽器端狀態

可能影響頁面登入狀態的項目包括：

- cookie `nshmhmt_common_h5_account_token`（domain `.swordofjustice.com`）
- cookie `L36NAXX2HMT_UNISDK_TOKEN`、`L36NAXX2HMT_UNISDK_AUTH`
- localStorage `netease_global_sauth_json`（以及各活動的 `*_roleToken`、`*_loginAid`）

殘留的 UniSDK 登入或角色資料可能使頁面重載後恢復登入。工具 logout --browser 目前只刪 account cookie 與符合 roleToken / loginAid 的 localStorage key，不會清掉上面所有狀態，不能當成完整帳號切換工具。

## CDP 操作注意

Chrome 與 Edge 的固定 9222 埠使用 --cdp-url http://127.0.0.1:9222，工具從 /json/version 的 webSocketDebuggerUrl 建立連線；失敗時會停止，不會暗中改連另一個 profile。不帶此參數時保留既有的 --edge-udd / DevToolsActivePort 探索方式。

Chromium 的固定埠模式不應依賴 DevToolsActivePort 檔案：目前[上游原始碼](https://chromium.googlesource.com/chromium/src/+/main/chrome/browser/devtools/remote_debugging_server.cc)只在 port=0 時設定輸出目錄。Chrome 136 起也要求[非預設的 User Data 目錄](https://developer.chrome.com/blog/remote-debugging-port)。啟動範例見 [README.md](../README.md#首次綁定或重新擷取)。Chrome 完整綁定流程尚未實機驗證。

用 `Storage.setCookies` 帶 `expires: 1` 寫入過期 cookie，瀏覽器會自動移除它，這是只刪特定網域 cookie 的安全做法。

不要使用全瀏覽器 cookie 清除操作。Storage.clearCookies / Storage.deleteCookies 不是此工具可依賴的 CDP 方法；Network.clearBrowserCookies 則是全瀏覽器範圍，不適合此流程。先前將「方法不存在」與「清除全域 cookie」混寫的說明已修正。

## 驗證與簽章

`roleInfoToken` 的 JWT payload 只有 `exp` 與 `roleId`（不含 aid 或使用者名稱），但簽章由伺服器驗證。

實測：

| 操作 | 結果 |
|---|---|
| 用同一枚 token 簽發 vip 與 official | 兩者 data.aid 及 channelExtra 的角色、伺服器資訊相符 |
| 改 payload 的 `roleId`、保留原簽名 | `code=5`「数据错误」，拒絕 |
| 延長 `exp`、保留原簽名 | 同上被拒 |

此測試僅證明當次修改 payload 且保留原簽章的請求被拒絕。有效 token 的持有者可重用對應活動登入，實際有效性仍取決於伺服器；exp 不是保證。

## 與 MCP 的關係

此 skill 不需要 Chrome DevTools MCP。scripts/sword_session.py 自己實作最小 CDP 客戶端（websocket-client）。capture、帶 --browser 的操作，以及 refresh 退回 capture 時都需要 CDP；restore 不帶 --browser、mint、status 走 HTTP。

外部的 MCP 工具（例如用來在頁面上點擊或檢視渲染結果）可以輔助驗證，但不是必要條件。

## 免費福利 API

2026-10-26 核對網站公開前端：VIP 的 [html0.0f3caa44.js](https://www.swordofjustice.com/pc/zt/20260421095635/static/js/html0.0f3caa44.js) 與 [pages-index-index.5df662ca.js](https://www.swordofjustice.com/pc/zt/20260421095635/static/js/pages-index-index.5df662ca.js)，official 的 [html0.874c9be2.js](https://www.swordofjustice.com/pc/zt/20260714074625/static/js/html0.874c9be2.js)（模組 3da3）與 [pages-index-index.51b29b76.js](https://www.swordofjustice.com/pc/zt/20260714074625/static/js/pages-index-index.51b29b76.js)。檔名含版本雜湊，網站更新後需重新核對。

先以 account cookie 呼叫前述 login/info/account，核對 data.aid、activityName 及 channelExtra.roleId / serverId 與保存資料相符。福利程式使用 SITES 內固定的 API host，不採用 session 檔內的自訂 api_base。

| 站點 | 方法／路徑（接在該站 api base 後） | 參數／狀態 |
|---|---|---|
| official | GET /user/info | signed_today 為布林值；role_id、server_id 需與身份一致 |
| official | POST /checkin/do | 前端不傳 body；signed_today=false 時才執行 |
| vip | GET /activity/home-page | benefits 與 milestones 提供領取狀態 |
| vip | POST /activity/claim-gift-pack | JSON：gift_type，允許 1（日）、2（週）、3（月）、4（賽季） |
| vip | POST /activity/claim-milestone | JSON：login_count，取伺服器目前可領取項目的天數 |

VIP 前端常數：禮包 status=1 未解鎖、2 可領、3 已領、4 可補領差額；里程碑 status=1 未達成、2 可領、3 已領。未知值或重複 ID 會使工具停止，不猜測資格。補領差額與一般領取使用同一個 claim-gift-pack 端點。

官方簽到頁與 VIP 寫入都帶 account cookie、Origin、Referer，official 另帶 area:hmt。回應 code=0 後重新讀狀態，需確認該項為已完成才回報成功；POST 回應的 data 可以為空，不作為唯一成功依據。

領取前會再讀一次該項狀態，避免另一分頁已完成。一次操作不自動重送，不跟隨重導向；POST 逾時或後續狀態未確認時，先唯讀查詢結果。這並非跨程序的原子鎖，仍應避免對同一帳號平行執行多個領取程序。

本機帳號實查 signed_today=true，VIP 日、週禮包與所有登入里程碑為已完成，月、賽季禮包未達條件。因此當日沒有可驗證的新獎勵寫入；自動領取分支以 mock 測試驗證。
