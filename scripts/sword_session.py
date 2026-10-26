#!/usr/bin/env python3
"""Capture and reuse a logged-in session for the swordofjustice.com H5 activities.

The user logs in and binds their own game role manually in Chrome or Edge. This tool
attaches to the running browser over CDP, watches the traffic, and stores
the resulting session material locally so later automation can reuse it.

It does not bypass the NetEase verification step: the role binding and any
captcha stay manual, only the resulting tokens are harvested.

Restore path
------------
Logging out of the site only clears browser-side state. The server-issued
``roleInfoToken`` survives the logout, so a full session can be rebuilt without
any further verification:

1. ``roleInfoToken`` (captured once, valid until its ``exp``) is exchanged for
   an activity-scoped account token via
   ``/public/nshm/common_activity_auth/web/login/account``.
2. That account token is written back into the browser as the
   ``nshmhmt_common_h5_account_token`` cookie.

The activity account token carries the bound role, so the page comes back fully
logged in. This reuses an already-granted authorisation; it does not defeat the
captcha, and it stops working once ``roleInfoToken`` itself expires.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import queue
import sys
import threading
import time
import urllib.parse
from pathlib import Path

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("需要 requests：pip install requests")

try:
    import websocket
except ImportError:  # pragma: no cover
    sys.exit("需要 websocket-client：pip install websocket-client")


API = "https://api.swordofjustice.com"
MINT_PATH = "/public/nshm/common_activity_auth/web/login/account"
LOGOUT_PATH = "/public/nshm/common_activity_auth/logout/account"
ACCOUNT_COOKIE = "nshmhmt_common_h5_account_token"
LOGIN_INFO_PATH = "/public/nshm/common_activity_auth/login/info/account"
ROLE_COOKIE = "gdxidpyhxdE"


def default_paths(platform: str | None = None) -> tuple[Path, Path]:
    home = Path.home()
    if (platform or sys.platform) == "win32":
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        if not local.is_absolute():
            local = home / "AppData" / "Local"
        return local / "Microsoft" / "Edge" / "User Data", local / "sword-session" / "sessions.json"
    config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    state = Path(os.environ.get("XDG_STATE_HOME") or home / ".local" / "state")
    if not config.is_absolute():
        config = home / ".config"
    if not state.is_absolute():
        state = home / ".local" / "state"
    return config / "microsoft-edge", state / "sword-session" / "sessions.json"


DEFAULT_EDGE_UDD, DEFAULT_STORE = default_paths()

SITES = {
    "vip": {
        "label": "會員中心 (viphmt)",
        "page": "https://www.swordofjustice.com/h5/20260424/vip/index.html#/",
        "activity": "nshm_viphmt_20260424",
        "api_base": API + "/nshm-hmt-vip-center-server",
        "probe": "/activity/home-page",
        "headers": {},
    },
    "official": {
        "label": "週三小確幸 (officialwebfast)",
        "page": "https://www.swordofjustice.com/h5/20260722/officialwebfasthmt/#/",
        "activity": "nshm_offwebfast2_20260730",
        "api_base": API + "/nshm-nshna-officialwebfast-2026",
        "probe": "/front/home/data",
        # ponytail: 這個站點的 API 強制要求 area header，缺了會回 code 20002。
        "headers": {"area": "hmt"},
    },
}


def mask(value: str, keep: int = 8) -> str:
    if not value:
        return "(empty)"
    if len(value) <= keep * 2:
        return f"{value[:keep]}...len{len(value)}"
    return f"{value[:keep]}...{value[-keep:]} (len{len(value)})"


def jwt_payload(token: str) -> dict:
    try:
        raw = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except Exception:
        return {}


def jwt_expired(token: str) -> bool:
    exp = jwt_payload(token).get("exp")
    if not isinstance(exp, (int, float)):
        return True
    return time.time() >= exp


class Cdp:
    """Minimal browser-level CDP client."""

    def __init__(self, ws_url: str, timeout: int = 30):
        # Edge rejects the handshake unless Origin matches the debug port.
        host = ws_url.split("//", 1)[1].split("/", 1)[0]
        self.ws = websocket.create_connection(
            ws_url, timeout=timeout, origin=f"http://{host}",
            max_size=64 * 1024 * 1024,
        )
        self._id = 0
        self._events: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self) -> None:
        while True:
            try:
                msg = json.loads(self.ws.recv())
            except Exception:
                break
            self._events.put(("resp" if "id" in msg else "event", msg))

    def send(self, method: str, params: dict | None = None, session_id: str | None = None,
             timeout: int = 30) -> dict:
        with self._lock:
            self._id += 1
            mid = self._id
        payload = {"id": mid, "method": method, "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        self.ws.send(json.dumps(payload))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                kind, msg = self._events.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                continue
            if kind == "resp" and msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(method)

    def drain(self, seconds: float = 0.5) -> list[dict]:
        out = []
        end = time.time() + seconds
        while time.time() < end:
            try:
                kind, msg = self._events.get(timeout=0.05)
            except queue.Empty:
                continue
            if kind == "event":
                out.append(msg)
        return out

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def browser_ws(udd: Path, cdp_url: str | None = None) -> str:
    if cdp_url:
        endpoint = urllib.parse.urlparse(cdp_url)
        if (endpoint.scheme not in ("http", "https")
                or endpoint.hostname not in ("localhost", "127.0.0.1", "::1")
                or endpoint.username or endpoint.password
                or endpoint.path not in ("", "/") or endpoint.query or endpoint.fragment):
            raise SystemExit("--cdp-url 需為本機 HTTP 位址，例如 http://127.0.0.1:9222")
        try:
            response = requests.get(cdp_url.rstrip("/") + "/json/version", timeout=5)
            response.raise_for_status()
            metadata = response.json()
            if not isinstance(metadata, dict):
                raise ValueError("CDP /json/version 回應需為 JSON 物件")
            ws_url = metadata.get("webSocketDebuggerUrl")
            parsed = urllib.parse.urlparse(ws_url) if isinstance(ws_url, str) else None
            if (not parsed or parsed.scheme not in ("ws", "wss")
                    or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
                    or parsed.username or parsed.password):
                raise ValueError("未取得有效的本機 webSocketDebuggerUrl")
            return ws_url
        except (requests.RequestException, ValueError) as exc:
            raise SystemExit(f"無法連接指定的 CDP 位址：{exc}") from exc
    pf = udd / "DevToolsActivePort"
    if not pf.exists():
        raise SystemExit(
            f"找不到 {pf}\n請確認 --edge-udd 目錄與遠端偵錯設定。"
            "若 Chrome 或 Edge 以固定 9222 埠啟動，請指定 --cdp-url http://127.0.0.1:9222。"
        )
    port, path = pf.read_text().splitlines()[:2]
    return f"ws://127.0.0.1:{port.strip()}{path.strip()}"


def open_page(cdp: Cdp, url: str) -> str:
    tid = cdp.send("Target.createTarget", {"url": "about:blank"})["targetId"]
    sid = cdp.send("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
    cdp.send("Network.enable", {}, sid)
    cdp.send("Page.enable", {}, sid)
    cdp.send("Runtime.enable", {}, sid)
    cdp.send("Page.navigate", {"url": url}, sid)
    return sid


def find_or_open_page(cdp: Cdp, url: str) -> str:
    """Attach to an existing tab showing the same page, else open a new one."""
    path = urllib.parse.urlparse(url).path
    for target in cdp.send("Target.getTargets").get("targetInfos", []):
        if target.get("type") == "page" and path in target.get("url", ""):
            sid = cdp.send("Target.attachToTarget", {
                "targetId": target["targetId"], "flatten": True})["sessionId"]
            cdp.send("Network.enable", {}, sid)
            cdp.send("Page.enable", {}, sid)
            cdp.send("Runtime.enable", {}, sid)
            return sid
    return open_page(cdp, url)


def evaluate(cdp: Cdp, sid: str, expr: str) -> object:
    res = cdp.send("Runtime.evaluate", {"expression": expr, "returnByValue": True}, sid)
    return res.get("result", {}).get("value")


def all_cookies(cdp: Cdp) -> dict:
    res = cdp.send("Storage.getCookies")
    return {c["name"]: c["value"] for c in res.get("cookies", [])}


def local_storage(cdp: Cdp, sid: str) -> dict:
    raw = evaluate(cdp, sid, "JSON.stringify(localStorage)")
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


def find_role_info_token(cdp: Cdp, sid: str) -> tuple[str | None, str | None]:
    """Return (roleInfoToken, activityName) harvested from the page or traffic."""
    ls = local_storage(cdp, sid)
    # The unisdk role cookie carries the same roleInfoToken payload.
    for key, value in ls.items():
        if key.endswith("_roleToken") and value and value.startswith("eyJ"):
            payload = jwt_payload(value)
            if "roleId" in payload:
                return value, None
    return None, None


def harvest_from_traffic(cdp: Cdp, sid: str, seconds: float, site: dict) -> dict:
    """Collect a roleInfoToken from the page or from network traffic.

    The page's own login flow never calls the mint endpoint, so watching
    traffic alone misses the token. The reliable source is the activity's
    ``*_roleToken`` localStorage entry, which holds the same roleInfoToken.
    Traffic is still watched because it catches the token mid-flight when the
    user binds a different role.
    """
    found = {"role_info_token": None, "mint_activity": None, "role_cookie": None}
    deadline = time.time() + seconds
    while time.time() < deadline:
        for msg in cdp.drain(0.5):
            if msg.get("method") != "Network.requestWillBeSent":
                continue
            req = msg["params"]["request"]
            url = req.get("url", "")
            if MINT_PATH in url:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                token = (q.get("roleInfoToken") or [None])[0]
                if token:
                    found["role_info_token"] = token
                    found["mint_activity"] = (q.get("activityName") or [None])[0]
                    print(f"  [封包] roleInfoToken 已擷取 (activity={found['mint_activity']})")
            if "common_role/rolelist" in url:
                # POST body carries roleId; the role cookie carries the token itself.
                print("  [封包] 角色清單請求出現")
        if not found["role_info_token"]:
            stored, _ = find_role_info_token(cdp, sid)
            if stored:
                found["role_info_token"] = stored
                print("  [頁面] 已從 localStorage 取得 roleInfoToken")
        cookies = all_cookies(cdp)
        if ROLE_COOKIE in cookies:
            found["role_cookie"] = cookies[ROLE_COOKIE]
        if found["role_info_token"]:
            break
    return found


def mint_account_token(role_info_token: str, activity: str, referer: str) -> dict:
    """Exchange roleInfoToken for a per-activity account token cookie."""
    url = API + MINT_PATH
    resp = requests.get(
        url,
        params={"channel": "unisdk", "activityName": activity, "roleInfoToken": role_info_token},
        headers={"Origin": "https://www.swordofjustice.com", "Referer": referer,
                 "Accept": "application/json, text/javascript, */*; q=0.01"},
        timeout=20,
    )
    try:
        body = resp.json()
    except ValueError:
        body = {"code": -1, "message": resp.text[:200]}
    token = None
    for chunk in resp.headers.get("set-cookie", "").split(","):
        if ACCOUNT_COOKIE in chunk:
            token = chunk.split(ACCOUNT_COOKIE + "=", 1)[1].split(";", 1)[0]
            break
    return {"code": body.get("code"), "message": body.get("message"), "token": token,
            "data": body.get("data")}


def logout_account_token(account_token: str, referer: str) -> dict:
    """Invalidate the server-side account token (the site's own logout API)."""
    resp = requests.get(
        API + LOGOUT_PATH,
        headers={"Origin": "https://www.swordofjustice.com", "Referer": referer,
                 "Cookie": f"{ACCOUNT_COOKIE}={account_token}",
                 "Accept": "application/json, text/javascript, */*; q=0.01"},
        timeout=20,
    )
    try:
        body = resp.json()
    except ValueError:
        body = {"code": -1, "message": resp.text[:200]}
    return {"code": body.get("code"), "message": body.get("message")}


def load_store(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def save_store(path: Path, store: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    if os.name == "nt":
        # Restrict the credential file to the current user only.
        # ponytail: 必須帶完整網域 (DOMAIN\user)，否則 icacls 會截斷成
        # "user\:(F)" 而讓連本人也讀不到。
        account = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}"
        os.system(
            f'icacls "{path}" /inheritance:r /grant:r "{account}:F" "SYSTEM:F" "Administrators:F" >nul 2>&1'
        )
    else:
        os.chmod(path, 0o600)


def build_session(site_key: str, role_info_token: str, role_cookie: str | None) -> dict:
    site = SITES[site_key]
    print(f"  以 roleInfoToken 為 {site['label']} 簽發專屬 token ...")
    minted = mint_account_token(role_info_token, site["activity"], site["page"])
    if minted["code"] != 0 or not minted["token"]:
        raise SystemExit(f"簽發失敗：code={minted['code']} message={minted['message']}")
    data = minted["data"] or {}
    extra = data.get("channelExtra") or {}
    return {
        "site": site_key,
        "label": site["label"],
        "page": site["page"],
        "activity": site["activity"],
        "api_base": site["api_base"],
        "probe": site["probe"],
        "headers": dict(site["headers"]),
        "account_token": minted["token"],
        "role_info_token": role_info_token,
        "role_cookie": role_cookie,
        "aid": data.get("aid"),
        "username": data.get("username"),
        "role_id": extra.get("roleId"),
        "server_id": extra.get("serverId"),
        "role_name": extra.get("roleName"),
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "role_info_exp": jwt_payload(role_info_token).get("exp"),
    }


def cmd_capture(args) -> int:
    site = SITES[args.site]
    print(f"== capture {site['label']} ==")
    cdp = Cdp(browser_ws(args.edge_udd, args.cdp_url))
    try:
        sid = open_page(cdp, site["page"])
        print(f"已開啟：{site['page']}")
        print("請在該分頁手動完成登入、驗證與角色綁定（工具不會繞過驗證）。")
        print(f"監看封包中，最多等待 {args.wait} 秒 ...\n")
        found = harvest_from_traffic(cdp, sid, args.wait, site)
        if not found["role_info_token"]:
            print("未擷取到 roleInfoToken。請確認已在頁面上完成綁定，或增加 --wait 秒數。")
            return 2
        role_cookie = found["role_cookie"]
        session = build_session(args.site, found["role_info_token"], role_cookie)
        store = load_store(args.store)
        store[args.site] = session
        save_store(args.store, store)
        print(f"\n已保存到 {args.store}")
        print(f"  aid={session['aid']} role_id={session['role_id']} server_id={session['server_id']}")
        print(f"  account_token={mask(session['account_token'])}")
        print(f"  roleInfoToken={mask(session['role_info_token'])}")
        return 0
    finally:
        cdp.close()


def cmd_status(args) -> int:
    store = load_store(args.store)
    if not store:
        print(f"{args.store} 沒有任何 session，先執行 capture。")
        return 2
    rc = 0
    for key, session in store.items():
        site = SITES.get(key, {})
        print(f"== {session.get('label', key)} ==")
        rit = session.get("role_info_token")
        if rit and jwt_expired(rit):
            print("  roleInfoToken 已過期，需要重新 capture。")
            rc = 1
            continue
        headers = {
            "Origin": "https://www.swordofjustice.com",
            "Referer": session.get("page", "https://www.swordofjustice.com/"),
            "Cookie": f"{ACCOUNT_COOKIE}={session['account_token']}",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
        headers.update(session.get("headers") or {})
        url = session["api_base"] + session["probe"]
        try:
            resp = requests.get(url, headers=headers, timeout=20)
            body = resp.json()
        except Exception as exc:
            print(f"  請求失敗：{exc}")
            rc = 1
            continue
        code = body.get("code")
        if code == 0:
            print(f"  有效（code=0）  role_id={session.get('role_id')} server_id={session.get('server_id')}")
        else:
            print(f"  無效：code={code} message={body.get('message')}")
            rc = 1
    return rc


def cmd_refresh(args) -> int:
    """Re-mint from a still-valid roleInfoToken, otherwise fall back to capture."""
    store = load_store(args.store)
    session = store.get(args.site)
    if session and session.get("role_info_token") and not jwt_expired(session["role_info_token"]):
        print(f"== refresh {session.get('label')} ==")
        try:
            fresh = build_session(args.site, session["role_info_token"], session.get("role_cookie"))
        except SystemExit as exc:
            print(f"  重新簽發失敗：{exc}")
            return cmd_capture(args)
        store[args.site] = fresh
        save_store(args.store, store)
        print(f"已更新 {args.store}")
        print(f"  account_token={mask(fresh['account_token'])}")
        return 0
    print("roleInfoToken 已失效或不存在，改為要求手動綁定。")
    return cmd_capture(args)


def pick_source(store: dict, preferred: str | None) -> dict | None:
    """Choose the session to borrow roleInfoToken from.

    The preferred site wins when its token is still usable; otherwise any site
    with a live token is used, so one expired activity does not block the rest.
    """
    candidates = [s for s in store.values() if s.get("role_info_token")]
    if not candidates:
        return None
    alive = [s for s in candidates if not jwt_expired(s["role_info_token"])]
    pool = alive or candidates
    if preferred and preferred in store:
        for entry in pool:
            if entry is store[preferred]:
                return entry
    return pool[0]


def cmd_mint(args) -> int:
    """Mint a session for one site using the roleInfoToken captured for another.

    The roleInfoToken is not site specific, so a single manual binding is enough
    to produce valid sessions for every supported activity.
    """
    store = load_store(args.store)
    source = pick_source(store, args.source)
    if not source or not source.get("role_info_token"):
        print("沒有可用的 roleInfoToken，先執行 capture。")
        return 2
    rit = source["role_info_token"]
    if jwt_expired(rit):
        print("roleInfoToken 已過期，請執行 capture 重新綁定。")
        return 2
    session = build_session(args.site, rit, source.get("role_cookie"))
    store[args.site] = session
    save_store(args.store, store)
    print(f"已保存 {args.site} 到 {args.store}")
    print(f"  account_token={mask(session['account_token'])}")
    return 0


def cmd_list(args) -> int:
    store = load_store(args.store)
    if not store:
        print("尚無 session。")
        return 0
    for key, session in store.items():
        rit = session.get("role_info_token") or ""
        exp = jwt_payload(rit).get("exp")
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(exp)) if exp else "?"
        print(f"{key}: {session.get('label')}")
        print(f"  aid={session.get('aid')} role_id={session.get('role_id')} server_id={session.get('server_id')}")
        print(f"  captured_at={session.get('captured_at')} roleInfoToken 有效期至 {when}")
        print(f"  account_token={mask(session.get('account_token', ''))}")
    return 0


def activity_request(site_key: str, session: dict, method: str, path: str,
                     payload: dict | None = None) -> dict:
    """One HTTP attempt; never replay a possibly completed claim."""
    site = SITES[site_key]
    base = API if path == LOGIN_INFO_PATH else site["api_base"]
    headers = {"Origin": "https://www.swordofjustice.com", "Referer": site["page"],
               "Cookie": f"{ACCOUNT_COOKIE}={session['account_token']}",
               "Accept": "application/json", **site["headers"]}
    try:
        response = requests.request(method, base + path, headers=headers, json=payload,
                                    timeout=20, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError(f"HTTP {response.status_code}")
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        hint = "；操作結果未確認，先查狀態，勿直接重送" if method == "POST" else ""
        raise RuntimeError(f"{method} {path} 請求失敗（{type(exc).__name__}）{hint}") from exc
    if not isinstance(body, dict) or type(body.get("code")) is not int or body["code"] != 0:
        code = body.get("code") if isinstance(body, dict) else "?"
        raise RuntimeError(f"{path} 被網站拒絕，code={code}；請檢查登入或手動驗證")
    if method == "POST":
        return body["data"] if isinstance(body.get("data"), dict) else {}
    if not isinstance(body.get("data"), dict):
        raise RuntimeError(f"{path} 回應格式改變，停止操作")
    return body["data"]


def verify_benefit_identity(site_key: str, session: dict) -> None:
    for field in ("account_token", "aid", "role_id", "server_id"):
        if not session.get(field):
            raise RuntimeError(f"{site_key} 缺少 {field}，請先完成綁定")
    info = activity_request(site_key, session, "GET", LOGIN_INFO_PATH)
    extra = info.get("channelExtra")
    if not isinstance(extra, dict):
        raise RuntimeError(f"{site_key} 身份資料格式改變，停止操作")
    if (str(info.get("aid")) != str(session["aid"])
            or str(extra.get("roleId")) != str(session["role_id"])
            or str(extra.get("serverId")) != str(session["server_id"])
            or info.get("activityName") != SITES[site_key]["activity"]):
        raise RuntimeError(f"{site_key} 帳號、角色、伺服器或活動不符，停止操作")


def benefit_snapshot(site_key: str, session: dict) -> dict:
    path = "/activity/home-page" if site_key == "vip" else "/user/info"
    data = activity_request(site_key, session, "GET", path)
    if site_key == "official" and (
            str(data.get("role_id")) != str(session["role_id"])
            or str(data.get("server_id")) != str(session["server_id"])):
        raise RuntimeError("簽到狀態的角色或伺服器不符，停止操作")
    return data


def benefit_items(site_key: str, data: dict) -> list[dict]:
    """Plan only documented free gifts, login milestones and today's check-in."""
    if not isinstance(data, dict):
        raise RuntimeError("福利狀態格式改變，停止操作")
    if site_key == "official":
        if type(data.get("signed_today")) is not bool:
            raise RuntimeError("signed_today 格式改變，停止操作")
        return [{"kind": "checkin", "id": 0, "label": "今日簽到",
                 "state": "already" if data["signed_today"] else "available"}]
    items = []
    for field, kind, id_key in (("benefits", "gift", "gift_type"),
                                ("milestones", "milestone", "login_count")):
        rows = data.get(field)
        if not isinstance(rows, list):
            raise RuntimeError(f"{field} 格式改變，停止操作")
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError(f"{field} 項目格式改變，停止操作")
            item_id, status = row.get(id_key), row.get("status")
            allowed_status = (1, 2, 3, 4) if kind == "gift" else (1, 2, 3)
            if (type(item_id) is not int or item_id <= 0 or item_id in seen
                    or (kind == "gift" and item_id not in (1, 2, 3, 4))
                    or type(status) is not int or status not in allowed_status):
                raise RuntimeError(f"{field} ID 或狀態未知，停止操作")
            seen.add(item_id)
            label = row.get("gift_name") if kind == "gift" else f"登入 {item_id} 天獎勵"
            items.append({"kind": kind, "id": item_id,
                          "label": label or f"禮包 {item_id}",
                          "state": {1: "locked", 2: "available", 3: "already", 4: "available"}[status]})
    # ponytail: 單次最多 32 項；網站擴充獎勵種類時先檢查前端與 API 契約再調整。
    if len(items) > 32:
        raise RuntimeError("獎勵數量超出已支援範圍，停止操作")
    return items


def execute_benefits(site_key: str, session: dict, items: list[dict], claim: bool = False) -> list[dict]:
    results = []
    for item in items:
        result = dict(item)
        if claim and item["state"] == "available":
            # Recheck before each write: another tab may already have claimed it.
            fresh = benefit_items(site_key, benefit_snapshot(site_key, session))
            current = next((x for x in fresh if (x["kind"], x["id"]) == (item["kind"], item["id"])), None)
            if not current:
                raise RuntimeError("獎勵項目已改變，停止操作")
            result = dict(current)
            if current["state"] == "available":
                if item["kind"] == "checkin":
                    path, payload = "/checkin/do", None
                elif item["kind"] == "gift":
                    path, payload = "/activity/claim-gift-pack", {"gift_type": item["id"]}
                elif item["kind"] == "milestone":
                    path, payload = "/activity/claim-milestone", {"login_count": item["id"]}
                else:
                    raise RuntimeError("不支援的獎勵種類，停止操作")
                activity_request(site_key, session, "POST", path, payload)
                after = benefit_items(site_key, benefit_snapshot(site_key, session))
                verified = next((x for x in after if (x["kind"], x["id"]) == (item["kind"], item["id"])), None)
                if not verified or verified["state"] != "already":
                    raise RuntimeError("網站尚未確認已領取；先查狀態，勿直接重送")
                result["state"] = "claimed"
                print(f"  已完成並確認：{result['label']}")
        results.append(result)
    return results


def cmd_benefits(args) -> int:
    store = load_store(args.store)
    keys = list(SITES) if args.site == "all" else [args.site]
    try:
        if any(not isinstance(store.get(k), dict) for k in keys):
            raise RuntimeError("所選站點缺少有效 session，請先 capture 或 mint")
        identities = {(str(store[k].get("aid")), str(store[k].get("role_id")),
                       str(store[k].get("server_id"))) for k in keys if k in store}
        if len(identities) > 1:
            raise RuntimeError("同一份 store 的兩站身份不同，請分開帳號資料")
        prepared = []
        # Verify every selected identity and schema before the first write.
        for key in keys:
            if key not in store:
                raise RuntimeError(f"{key} 尚無 session，請先 capture 或 mint")
            verify_benefit_identity(key, store[key])
            prepared.append((key, benefit_items(key, benefit_snapshot(key, store[key]))))
        labels = {"locked": "未達條件", "available": "可領取", "already": "已完成", "claimed": "本次完成"}
        for key, items in prepared:
            print(f"== {SITES[key]['label']}：{'執行' if args.claim else '只查詢'} ==")
            for item in execute_benefits(key, store[key], items, args.claim):
                print(f"  {item['label']}：{labels[item['state']]}")
        return 0
    except RuntimeError as exc:
        print(f"停止：{exc}")
        return 1


def cmd_restore(args) -> int:
    """Rebuild a logged-in browser session from a stored roleInfoToken.

    The logout endpoint only clears client state; roleInfoToken stays valid on
    the server. Re-minting an activity account token from it and writing that
    cookie back restores the page without any verification step.
    """
    store = load_store(args.store)
    source = pick_source(store, args.site)
    if not source or not source.get("role_info_token"):
        print("沒有可用的 roleInfoToken，先執行 capture。")
        return 2
    rit = source["role_info_token"]
    if jwt_expired(rit):
        print("roleInfoToken 已過期，需要使用者重新手動綁定（capture）。")
        return 2

    site = SITES[args.site]
    print(f"== restore {site['label']} ==")
    try:
        session = build_session(args.site, rit, source.get("role_cookie"))
    except SystemExit as exc:
        print(f"  簽發失敗：{exc}")
        return 1

    if args.browser:
        cdp = Cdp(browser_ws(args.edge_udd, args.cdp_url))
        try:
            sid = find_or_open_page(cdp, site["page"])
            cdp.send("Storage.setCookies", {"cookies": [{
                "name": ACCOUNT_COOKIE,
                "value": session["account_token"],
                "domain": ".swordofjustice.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
            }]})
            cdp.send("Page.navigate", {"url": site["page"]}, sid)
            print(f"  已注入 {ACCOUNT_COOKIE} 並重新載入 {site['page']}")
        finally:
            cdp.close()

    store[args.site] = session
    save_store(args.store, store)
    print(f"已更新 {args.store}")
    print(f"  role_id={session['role_id']} server_id={session['server_id']} "
          f"role_name={session['role_name']}")
    return 0


def cmd_logout(args) -> int:
    """Log the site out on the server and clear the browser-side session.

    Only the account token is revoked. The stored roleInfoToken stays valid, so
    ``restore`` can bring the session back without any verification step.
    """
    store = load_store(args.store)
    session = store.get(args.site)
    if not session or not session.get("account_token"):
        print(f"{args.site} 沒有已保存的 session。")
        return 2

    site = SITES[args.site]
    print(f"== logout {site['label']} ==")
    result = logout_account_token(session["account_token"], session.get("page", site["page"]))
    print(f"  伺服器登出：code={result['code']} message={result['message']}")

    if args.browser:
        cdp = Cdp(browser_ws(args.edge_udd, args.cdp_url))
        try:
            sid = find_or_open_page(cdp, site["page"])
            # Overwrite with an expired cookie so the browser drops it.
            cdp.send("Storage.setCookies", {"cookies": [{
                "name": ACCOUNT_COOKIE,
                "value": "deleted",
                "domain": ".swordofjustice.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "expires": 1,
            }]})
            # Drop the cached role tokens, otherwise the page re-authenticates
            # itself through UniSDK and never shows the logged-out state.
            evaluate(cdp, sid, """(() => {
                for (const key of Object.keys(localStorage)) {
                    if (/roleToken|loginAid/i.test(key)) localStorage.removeItem(key);
                }
            })()""")
            cdp.send("Page.navigate", {"url": site["page"]}, sid)
            print(f"  已清除 {ACCOUNT_COOKIE} 與 localStorage 角色 token 並重新載入")
        finally:
            cdp.close()

    print("  roleInfoToken 仍有效，隨時可用 restore 恢復。")
    return 0


def main(argv=None) -> int:
    # Windows 主控台預設 cp950，無法印出角色名等簡體字，改用 UTF-8 輸出。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    parser = argparse.ArgumentParser(description="swordofjustice.com 已登入 session 擷取與重用工具")
    parser.add_argument("--edge-udd", type=Path, default=DEFAULT_EDGE_UDD,
                        help="瀏覽器 User Data 目錄（預設 Edge，Chrome 也可指定）")
    parser.add_argument("--cdp-url", help="明確指定本機 CDP HTTP 位址，例如 http://127.0.0.1:9222；優先於 --edge-udd")
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE,
                        help="session 儲存檔位置（預設：%(default)s）")
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", help="開啟頁面並監看封包，擷取綁定後的 session")
    cap.add_argument("site", choices=sorted(SITES))
    cap.add_argument("--wait", type=int, default=180, help="等待使用者完成綁定的秒數")
    cap.set_defaults(func=cmd_capture)

    sta = sub.add_parser("status", help="用已保存的 session 呼叫 API 檢查是否仍有效")
    sta.set_defaults(func=cmd_status)

    ref = sub.add_parser("refresh", help="session 失效時重新簽發或要求手動綁定")
    ref.add_argument("site", choices=sorted(SITES))
    ref.add_argument("--wait", type=int, default=180)
    ref.set_defaults(func=cmd_refresh)

    mint = sub.add_parser("mint", help="用已擷取的 roleInfoToken 為另一個站簽發 session")
    mint.add_argument("site", choices=sorted(SITES))
    mint.add_argument("--source", choices=sorted(SITES),
                      help="提供 roleInfoToken 的站，預設取第一個有值的 session")
    mint.set_defaults(func=cmd_mint)

    ls = sub.add_parser("list", help="列出已保存的 session")
    ls.set_defaults(func=cmd_list)

    res = sub.add_parser("restore", help="用 roleInfoToken 重新簽發並注入瀏覽器 Cookie，恢復登入")
    res.add_argument("site", choices=sorted(SITES))
    res.add_argument("--browser", action="store_true",
                     help="同時把簽發的 token 注入指定瀏覽器並重新載入頁面")
    res.set_defaults(func=cmd_restore)

    out = sub.add_parser("logout", help="伺服器端登出並清除瀏覽器 session（roleInfoToken 保留）")
    out.add_argument("site", choices=sorted(SITES))
    out.add_argument("--browser", action="store_true",
                     help="同時清除瀏覽器 Cookie 與 localStorage 角色 token")
    out.set_defaults(func=cmd_logout)

    benefits = sub.add_parser("benefits", help="查詢免費簽到與會員獎勵；加 --claim 才執行")
    benefits.add_argument("site", choices=["all", *sorted(SITES)])
    benefits.add_argument("--claim", action="store_true", help="簽到並領取已達條件的免費獎勵，逐項確認、不重送")
    benefits.set_defaults(func=cmd_benefits)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
