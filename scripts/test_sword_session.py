"""Regression checks for sword_session helpers (no network required)."""
import base64
import json
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import sword_session as S


def make_jwt(payload: dict) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{raw}.sig"


class TestJwt(unittest.TestCase):
    def test_payload_roundtrip(self):
        token = make_jwt({"exp": 1798167778, "roleId": "1234567890"})
        self.assertEqual(S.jwt_payload(token)["roleId"], "1234567890")

    def test_expired_detection(self):
        past = make_jwt({"exp": int(time.time()) - 10})
        future = make_jwt({"exp": int(time.time()) + 3600})
        self.assertTrue(S.jwt_expired(past))
        self.assertFalse(S.jwt_expired(future))

    def test_garbage_token_counts_as_expired(self):
        self.assertTrue(S.jwt_expired("not-a-jwt"))
        self.assertTrue(S.jwt_expired(""))


class TestMask(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(S.mask(""), "(empty)")

    def test_long_value_is_truncated(self):
        out = S.mask("a" * 100)
        self.assertIn("len100", out)
        self.assertNotIn("a" * 100, out)


class TestSites(unittest.TestCase):
    def test_every_site_has_required_fields(self):
        for key, site in S.SITES.items():
            for field in ("page", "activity", "api_base", "probe", "headers"):
                self.assertIn(field, site, f"{key} missing {field}")

    def test_official_site_requires_area_header(self):
        # The officialwebfast API rejects requests without area: hmt (code 20002).
        self.assertEqual(S.SITES["official"]["headers"].get("area"), "hmt")

    def test_vip_site_needs_no_extra_header(self):
        self.assertEqual(S.SITES["vip"]["headers"], {})


class TestStore(unittest.TestCase):
    def test_roundtrip(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sessions.json"
            data = {"official": {"account_token": "abc"}}
            S.save_store(path, data)
            self.assertEqual(S.load_store(path), data)

    def test_missing_store_is_empty(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(S.load_store(Path(tmp) / "nope.json"), {})


class TestBrowserDiscovery(unittest.TestCase):
    @patch.object(S.requests, "get")
    def test_explicit_endpoint_works_without_port_file(self, get):
        response = Mock()
        response.json.return_value = {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/test"}
        get.return_value = response
        self.assertEqual(S.browser_ws(Path("unused"), "http://127.0.0.1:9222/"),
                         "ws://127.0.0.1:9222/devtools/browser/test")
        get.assert_called_once_with("http://127.0.0.1:9222/json/version", timeout=5)
        response.raise_for_status.assert_called_once()

    @patch.object(S.requests, "get")
    def test_endpoint_failure_stops_without_profile_fallback(self, get):
        get.side_effect = S.requests.ConnectionError("unavailable")
        with self.assertRaises(SystemExit):
            S.browser_ws(Path("unused"), "http://127.0.0.1:9222")

    @patch.object(S.requests, "get")
    def test_missing_websocket_is_rejected(self, get):
        for metadata in ({}, [], None):
            with self.subTest(metadata=metadata):
                get.return_value.json.return_value = metadata
                with self.assertRaises(SystemExit):
                    S.browser_ws(Path("unused"), "http://127.0.0.1:9222")

    @patch.object(S.requests, "get")
    def test_remote_endpoint_is_rejected_before_request(self, get):
        with self.assertRaises(SystemExit):
            S.browser_ws(Path("unused"), "https://example.com")
        get.assert_not_called()

    @patch.object(S.requests, "get")
    def test_remote_websocket_is_rejected(self, get):
        get.return_value.json.return_value = {"webSocketDebuggerUrl": "ws://example.com/browser/test"}
        with self.assertRaises(SystemExit):
            S.browser_ws(Path("unused"), "http://127.0.0.1:9222")

    def test_existing_port_file_discovery_still_works(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            udd = Path(tmp)
            (udd / "DevToolsActivePort").write_text("9222\n/devtools/browser/test\n", encoding="utf-8")
            self.assertEqual(S.browser_ws(udd), "ws://127.0.0.1:9222/devtools/browser/test")


class TestHarvest(unittest.TestCase):
    """Capture must not depend on the user re-running the login flow while
    the watcher is active: the page stores the token in localStorage."""

    class FakeCdp:
        def __init__(self, local_storage, cookies=None, events=None):
            self.ls = local_storage
            self.cookies = cookies or []
            self.events = events or []

        def drain(self, seconds):
            events, self.events = self.events, []
            return events

        def send(self, method, params=None, sid=None):
            if method == "Storage.getCookies":
                return {"cookies": self.cookies}
            if method == "Runtime.evaluate":
                return {"result": {"value": json.dumps(self.ls)}}
            return {}

    def test_finds_token_in_local_storage(self):
        token = make_jwt({"exp": int(time.time()) + 3600, "roleId": "1234567890"})
        cdp = self.FakeCdp({"nshm_viphmt_20260424_roleToken": token})
        found = S.harvest_from_traffic(cdp, "sid", 2, S.SITES["vip"])
        self.assertEqual(found["role_info_token"], token)

    def test_ignores_local_storage_entries_without_role_id(self):
        cdp = self.FakeCdp({"UDID": "abc", "other_roleToken": "not-a-jwt"})
        found = S.harvest_from_traffic(cdp, "sid", 2, S.SITES["vip"])
        self.assertIsNone(found["role_info_token"])

    def test_captures_role_cookie_alongside_token(self):
        token = make_jwt({"exp": int(time.time()) + 3600, "roleId": "1"})
        cdp = self.FakeCdp({"x_roleToken": token},
                           cookies=[{"name": S.ROLE_COOKIE, "value": "c"}])
        found = S.harvest_from_traffic(cdp, "sid", 2, S.SITES["vip"])
        self.assertEqual(found["role_cookie"], "c")


class TestRestore(unittest.TestCase):
    """The restore path reuses roleInfoToken, which is the only artefact that
    survives a logout. These checks keep that invariant pinned down."""

    def test_role_info_token_is_the_source_of_truth(self):
        source = {"role_info_token": make_jwt({"exp": int(time.time()) + 3600,
                                                "roleId": "1234567890"})}
        self.assertFalse(S.jwt_expired(source["role_info_token"]))

    def test_expired_role_info_token_blocks_restore(self):
        source = {"role_info_token": make_jwt({"exp": int(time.time()) - 1})}
        self.assertTrue(S.jwt_expired(source["role_info_token"]))

    def test_account_cookie_name_matches_server(self):
        # The mint endpoint sets this exact cookie name; restore injects it.
        self.assertEqual(S.ACCOUNT_COOKIE, "nshmhmt_common_h5_account_token")

    def test_logout_uses_the_site_logout_endpoint(self):
        # The server-side logout revokes the account token but leaves
        # roleInfoToken usable, which is what makes restore work afterwards.
        self.assertTrue(S.LOGOUT_PATH.endswith("/common_activity_auth/logout/account"))
        self.assertNotEqual(S.LOGOUT_PATH, S.MINT_PATH)

    def test_logout_is_registered_as_a_subcommand(self):
        with self.assertRaises(SystemExit) as ctx:
            S.main(["logout", "vip", "--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_pick_source_prefers_requested_site(self):
        alive = make_jwt({"exp": int(time.time()) + 3600})
        store = {"vip": {"role_info_token": alive},
                 "official": {"role_info_token": alive}}
        self.assertIs(S.pick_source(store, "official"), store["official"])

    def test_pick_source_falls_back_when_preferred_is_expired(self):
        alive = make_jwt({"exp": int(time.time()) + 3600})
        dead = make_jwt({"exp": int(time.time()) - 10})
        store = {"vip": {"role_info_token": dead},
                 "official": {"role_info_token": alive}}
        self.assertIs(S.pick_source(store, "vip"), store["official"])

    def test_pick_source_returns_none_without_tokens(self):
        self.assertIsNone(S.pick_source({}, "vip"))

    def test_restore_is_registered_as_a_subcommand(self):
        # argparse raises SystemExit(0) when --help is parsed.
        with self.assertRaises(SystemExit) as ctx:
            S.main(["restore", "vip", "--help"])
        self.assertEqual(ctx.exception.code, 0)


class TestDefaultPaths(unittest.TestCase):
    def paths(self, platform, environment):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            # ABS: values become absolute directories on the current test OS.
            env = {key: str(home / value[4:]) if value.startswith("ABS:") else value
                   for key, value in environment.items()}
            with patch.object(S.Path, "home", return_value=home), patch.dict(S.os.environ, env, clear=True):
                return home, S.default_paths(platform)

    def test_windows_uses_local_app_data(self):
        home, (browser, store) = self.paths("win32", {"LOCALAPPDATA": "ABS:local"})
        self.assertEqual(browser, home / "local" / "Microsoft" / "Edge" / "User Data")
        self.assertEqual(store, home / "local" / "sword-session" / "sessions.json")

    def test_windows_missing_environment_does_not_use_working_directory(self):
        home, (_, store) = self.paths("win32", {})
        self.assertEqual(store, home / "AppData" / "Local" / "sword-session" / "sessions.json")

    def test_windows_relative_environment_uses_home_fallback(self):
        home, (_, store) = self.paths("win32", {"LOCALAPPDATA": "relative"})
        self.assertEqual(store, home / "AppData" / "Local" / "sword-session" / "sessions.json")

    def test_linux_defaults_ignore_windows_environment(self):
        home, (browser, store) = self.paths("linux", {"LOCALAPPDATA": "ABS:windows"})
        self.assertEqual(browser, home / ".config" / "microsoft-edge")
        self.assertEqual(store, home / ".local" / "state" / "sword-session" / "sessions.json")

    def test_linux_respects_absolute_xdg_paths(self):
        home, (browser, store) = self.paths("linux", {"XDG_CONFIG_HOME": "ABS:config", "XDG_STATE_HOME": "ABS:state"})
        self.assertEqual(browser, home / "config" / "microsoft-edge")
        self.assertEqual(store, home / "state" / "sword-session" / "sessions.json")

    def test_linux_ignores_relative_xdg_paths(self):
        home, (browser, store) = self.paths("linux", {"XDG_CONFIG_HOME": "relative", "XDG_STATE_HOME": "relative"})
        self.assertEqual(browser, home / ".config" / "microsoft-edge")
        self.assertEqual(store, home / ".local" / "state" / "sword-session" / "sessions.json")

    @unittest.skipIf(S.os.name == "nt", "POSIX permission bits require Linux")
    def test_posix_store_file_and_new_directory_are_private(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "new-state-directory" / "sessions.json"
            S.save_store(path, {"official": {"account_token": "fake"}})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
