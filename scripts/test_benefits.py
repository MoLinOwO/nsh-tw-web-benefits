"""Offline regression tests for benefits planning and claim behavior."""
import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

import sword_session as S


def session(site="official", *, api_base="https://untrusted.invalid"):
    return {
        "account_token": "fake-account-token",
        "aid": "fake-aid",
        "role_id": "fake-role",
        "server_id": "fake-server",
        "api_base": api_base,
    }


def identity_info():
    return {
        "aid": "fake-aid",
        "channelExtra": {"roleId": "fake-role", "serverId": "fake-server"},
        "activityName": S.SITES["official"]["activity"],
    }


def vip_snapshot(benefits=None, milestones=None):
    return {"benefits": benefits or [], "milestones": milestones or []}


class TestBenefitsCommand(unittest.TestCase):
    def setUp(self):
        self.store = {"official": session()}
        self.load = patch.object(S, "load_store", return_value=self.store)
        self.load.start()
        self.addCleanup(self.load.stop)
        self.request = patch.object(S, "activity_request")
        self.activity = self.request.start()
        self.addCleanup(self.request.stop)
        self.activity.side_effect = self.default_activity

    def default_activity(self, site, sess, method, path, payload=None):
        if path == S.LOGIN_INFO_PATH:
            return identity_info()
        if path == "/user/info":
            return {"role_id": "fake-role", "server_id": "fake-server", "signed_today": False}
        if path == "/activity/home-page":
            return vip_snapshot()
        return {}

    def run_command(self, site="official", claim=False):
        return S.cmd_benefits(SimpleNamespace(site=site, store="unused", claim=claim))

    def posts(self):
        return [c for c in self.activity.call_args_list if len(c.args) >= 4 and c.args[2] == "POST"]

    def test_default_command_is_read_only(self):
        self.assertEqual(self.run_command(), 0)
        self.assertEqual(self.posts(), [])

    def test_already_signed_claimed_and_locked_are_skipped_with_claim(self):
        self.store["official"] = session()
        self.store["vip"] = session("vip")

        def activity(site, sess, method, path, payload=None):
            if path == S.LOGIN_INFO_PATH:
                result = identity_info()
                if site == "vip":
                    result["activityName"] = S.SITES["vip"]["activity"]
                return result
            if path == "/user/info":
                return {"role_id": "fake-role", "server_id": "fake-server", "signed_today": True}
            if path == "/activity/home-page":
                return vip_snapshot(
                    [{"gift_type": 1, "status": 3}, {"gift_type": 2, "status": 1}],
                    [{"login_count": 7, "status": 3}],
                )
            return {}

        self.activity.side_effect = activity
        self.assertEqual(self.run_command("all", claim=True), 0)
        self.assertEqual(self.posts(), [])

    def test_unsigned_official_sends_one_bodyless_post_and_verifies(self):
        state = {"signed": False}

        def activity(site, sess, method, path, payload=None):
            if path == S.LOGIN_INFO_PATH:
                return identity_info()
            if path == "/user/info":
                return {"role_id": "fake-role", "server_id": "fake-server", "signed_today": state["signed"]}
            if method == "POST":
                state["signed"] = True
                return {}
            return {}

        self.activity.side_effect = activity
        self.assertEqual(self.run_command(claim=True), 0)
        self.assertEqual(self.posts(), [call("official", self.store["official"], "POST", "/checkin/do", None)])
        self.assertEqual(sum(1 for c in self.activity.call_args_list if len(c.args) >= 4 and c.args[3] == "/user/info"), 3)

    def test_gift_and_milestone_payloads_are_sent_once_and_status_confirmed(self):
        vip = session("vip")
        state = {"gift": 2, "milestone": 2}
        items = S.benefit_items("vip", vip_snapshot(
            [{"gift_type": 4, "status": state["gift"], "gift_name": "Fake gift"}],
            [{"login_count": 8, "status": state["milestone"]}],
        ))

        def activity(site, sess, method, path, payload=None):
            if method == "POST":
                if path == "/activity/claim-gift-pack":
                    state["gift"] = 3
                elif path == "/activity/claim-milestone":
                    state["milestone"] = 3
                return {}
            return vip_snapshot(
                [{"gift_type": 4, "status": state["gift"], "gift_name": "Fake gift"}],
                [{"login_count": 8, "status": state["milestone"]}],
            )

        self.activity.side_effect = activity
        result = S.execute_benefits("vip", vip, items, claim=True)
        self.assertEqual([(c.args[3], c.args[4]) for c in self.posts()], [
            ("/activity/claim-gift-pack", {"gift_type": 4}),
            ("/activity/claim-milestone", {"login_count": 8}),
        ])
        self.assertEqual([x["state"] for x in result], ["claimed", "claimed"])

    def test_failed_and_timeout_posts_are_not_replayed(self):
        for failure in (RuntimeError("failed"), S.requests.Timeout("timeout")):
            with self.subTest(failure=type(failure).__name__):
                self.activity.reset_mock()
                def activity(site, sess, method, path, payload=None):
                    if method == "POST":
                        raise failure
                    return {"role_id": "fake-role", "server_id": "fake-server", "signed_today": False}

                self.activity.side_effect = activity
                with self.assertRaises((RuntimeError, S.requests.Timeout)):
                    S.execute_benefits("official", session(), [
                        {"kind": "checkin", "id": 0, "label": "fake", "state": "available"}
                    ], claim=True)
                self.assertEqual(sum(1 for c in self.activity.call_args_list if c.args[2] == "POST"), 1)

    def test_success_without_status_change_stops_without_retry(self):
        def activity(site, sess, method, path, payload=None):
            if method == "POST":
                return {}
            if path == "/user/info":
                return {"role_id": "fake-role", "server_id": "fake-server", "signed_today": False}
            return identity_info()

        self.activity.side_effect = activity
        with self.assertRaisesRegex(RuntimeError, "勿直接重送"):
            S.execute_benefits("official", session(), [
                {"kind": "checkin", "id": 0, "label": "fake", "state": "available"}
            ], claim=True)
        self.assertEqual(len([c for c in self.activity.call_args_list if c.args[2] == "POST"]), 1)

    def test_all_identity_mismatch_anywhere_means_zero_posts(self):
        self.store["official"] = session()
        self.store["vip"] = session("vip")
        self.store["vip"]["role_id"] = "different-fake-role"
        self.assertEqual(self.run_command("all", claim=True), 1)
        self.assertEqual(self.posts(), [])

    def test_second_site_server_identity_failure_blocks_first_site_claim(self):
        self.store["vip"] = session("vip")

        def activity(site, sess, method, path, payload=None):
            if path == S.LOGIN_INFO_PATH:
                info = identity_info()
                info["activityName"] = S.SITES[site]["activity"]
                if site == "official":
                    info["channelExtra"]["roleId"] = "other-fake-role"
                return info
            return vip_snapshot([{"gift_type": 1, "status": 2}])

        self.activity.side_effect = activity
        self.assertEqual(self.run_command("all", claim=True), 1)
        self.assertEqual(self.posts(), [])

    def test_recheck_skips_item_claimed_by_another_tab(self):
        self.activity.return_value = None
        self.activity.side_effect = lambda *args: {
            "role_id": "fake-role", "server_id": "fake-server", "signed_today": True}
        results = S.execute_benefits("official", session(), [
            {"kind": "checkin", "id": 0, "label": "fake", "state": "available"}
        ], claim=True)
        self.assertEqual(results[0]["state"], "already")
        self.assertEqual(self.posts(), [])

    def test_hmt_header_fixed_api_host_and_untrusted_saved_base_ignored(self):
        response = SimpleNamespace(status_code=200, json=lambda: {"code": 0, "data": {}})
        self.request.stop()
        with patch.object(S.requests, "request", return_value=response) as request:
            S.activity_request("official", session(api_base="https://untrusted.invalid"), "POST", "/checkin/do")
        args, kwargs = request.call_args
        self.assertEqual(args[:2], ("POST", S.API + "/nshm-nshna-officialwebfast-2026/checkin/do"))
        self.assertEqual(kwargs["headers"].get("area"), "hmt")
        self.assertIsNone(kwargs["json"])
        self.assertNotIn("untrusted.invalid", args[1])

    def test_malformed_schema_fails_before_any_write(self):
        self.store["official"] = session()
        self.activity.side_effect = lambda site, sess, method, path, payload=None: (
            identity_info() if path == S.LOGIN_INFO_PATH else
            {"role_id": "fake-role", "server_id": "fake-server", "signed_today": "false"}
        )
        self.assertEqual(self.run_command(claim=True), 1)
        self.assertEqual(self.posts(), [])


class TestBenefitsHttp(unittest.TestCase):
    def test_actual_http_timeout_is_not_replayed(self):
        with patch.object(S.requests, "request", side_effect=S.requests.Timeout("private detail")) as request:
            with self.assertRaisesRegex(RuntimeError, "勿直接重送") as error:
                S.activity_request("official", session(), "POST", "/checkin/do")
        self.assertEqual(request.call_count, 1)
        self.assertNotIn("private detail", str(error.exception))
        self.assertFalse(request.call_args.kwargs["allow_redirects"])

    def test_success_with_empty_post_data_allows_followup_verification(self):
        response = SimpleNamespace(status_code=200, json=lambda: {"code": 0, "data": None})
        with patch.object(S.requests, "request", return_value=response):
            self.assertEqual(S.activity_request("official", session(), "POST", "/checkin/do"), {})


if __name__ == "__main__":
    unittest.main()
