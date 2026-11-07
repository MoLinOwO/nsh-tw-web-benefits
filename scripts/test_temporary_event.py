"""Offline regression tests for the temporary anniversary activity."""
from datetime import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

import sword_session as S


SITE = "anniversary2026"
FAKE_SESSION = {
    "account_token": "fake-account-token",
    "aid": "fake-aid",
    "role_id": "fake-role",
    "server_id": "fake-server",
}


def epoch(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def task(task_id=101, status=2, *, locked=False):
    return {"task_id": task_id, "task_status": status, "locked": locked}


def group(tab_id, tasks, *, locked=False, ended=False):
    return {"tab_id": tab_id, "tasks": tasks, "locked": locked, "ended": ended}


def milestone(milestone_id=201, status=2, *, locked=False):
    return {"milestone_id": milestone_id, "claim_status": status, "locked": locked}


class TestTemporaryEventWindow(unittest.TestCase):
    def test_start_and_exclusive_expiry_boundaries_with_utc_equivalents(self):
        start = "2026-10-30T08:00:00+08:00"
        expiry = "2027-01-16T00:00:00+08:00"
        self.assertEqual(S.site_window_state(SITE, epoch("2026-10-30T07:59:59+08:00")), "not_started")
        self.assertEqual(S.site_window_state(SITE, epoch(start)), "active")
        self.assertEqual(S.site_window_state(SITE, epoch("2026-10-30T00:00:00Z")), "active")
        self.assertEqual(S.site_window_state(SITE, epoch("2027-01-15T23:59:59+08:00")), "active")
        self.assertEqual(S.site_window_state(SITE, epoch(expiry) - 1), "active")
        self.assertEqual(S.site_window_state(SITE, epoch("2027-01-15T16:00:00Z")), "expired")
        self.assertEqual(S.site_window_state(SITE, epoch(expiry)), "expired")

    def test_expired_direct_http_and_session_build_fail_before_network(self):
        expired = epoch("2027-01-16T00:00:00+08:00")
        with patch.object(S.time, "time", return_value=expired), \
                patch.object(S.requests, "request") as request, \
                patch.object(S, "mint_account_token") as mint:
            with self.assertRaises(RuntimeError):
                S.activity_request(SITE, FAKE_SESSION, "GET", "/activity-status?location=hmt")
            with self.assertRaises(RuntimeError):
                S.build_session(SITE, "fake-role-info-token", None)
        request.assert_not_called()
        mint.assert_not_called()

    def test_expiry_between_snapshot_and_post_blocks_the_write(self):
        snapshot = {"phase": 3, "task_groups": [group(1, [task()])], "milestones": []}
        items = S.benefit_items(SITE, snapshot)
        with patch.object(S, "benefit_snapshot", return_value=snapshot), \
                patch.object(S.time, "time", return_value=epoch("2027-01-16T00:00:00+08:00")), \
                patch.object(S.requests, "request") as request:
            with self.assertRaises(RuntimeError):
                S.execute_benefits(SITE, FAKE_SESSION, items, claim=True)
        request.assert_not_called()

    def test_expired_cli_capture_restore_and_benefits_never_connect(self):
        saved = {**FAKE_SESSION, "role_info_token": "fake-role-token"}
        with patch.object(S, "load_store", return_value={SITE: saved}), \
                patch.object(S.time, "time", return_value=epoch("2027-01-16T00:00:00+08:00")), \
                patch.object(S, "jwt_expired", return_value=False), \
                patch.object(S, "Cdp") as cdp, \
                patch.object(S.requests, "request") as request, \
                patch.object(S, "mint_account_token") as mint:
            self.assertEqual(S.main(["benefits", SITE, "--claim"]), 0)
            self.assertEqual(S.main(["capture", SITE]), 1)
            self.assertEqual(S.main(["restore", SITE, "--browser"]), 1)
        cdp.assert_not_called()
        request.assert_not_called()
        mint.assert_not_called()


class TestTemporaryEventBenefits(unittest.TestCase):
    def test_all_includes_configured_active_temporary_site(self):
        store = {key: dict(FAKE_SESSION) for key in S.SITES}
        with patch.object(S, "load_store", return_value=store), \
                patch.object(S.time, "time", return_value=epoch("2026-11-07T12:00:00+08:00")), \
                patch.object(S, "verify_benefit_identity"), \
                patch.object(S, "benefit_snapshot", return_value={}), \
                patch.object(S, "benefit_items", return_value=[]), \
                patch.object(S, "execute_benefits", return_value=[]) as execute:
            self.assertEqual(S.cmd_benefits(SimpleNamespace(site="all", store="unused", claim=False)), 0)
        self.assertEqual([c.args[0] for c in execute.call_args_list], list(S.SITES))

    def test_all_skips_missing_or_expired_temporary_site_and_keeps_regular_sites(self):
        regular_store = {"vip": {"fake": "vip"}, "official": {"fake": "official"}}
        for temporary_state in ("active", "expired"):
            with self.subTest(temporary_state=temporary_state):
                store = dict(regular_store)
                if temporary_state == "expired":
                    store[SITE] = {"fake": "temporary"}
                with patch.object(S, "load_store", return_value=store), \
                        patch.object(S, "site_window_state",
                                     side_effect=lambda key: temporary_state if key == SITE else "active"), \
                        patch.object(S, "verify_benefit_identity") as verify, \
                        patch.object(S, "benefit_snapshot", return_value={}), \
                        patch.object(S, "benefit_items", return_value=[]), \
                        patch.object(S, "execute_benefits", return_value=[]), \
                        patch.object(S, "activity_request") as request:
                    result = S.cmd_benefits(SimpleNamespace(site="all", store="unused", claim=False))
                self.assertEqual(result, 0)
                self.assertEqual([c.args[0] for c in verify.call_args_list], ["vip", "official"])
                request.assert_not_called()

    def test_snapshot_phase_1_and_5_stop_after_status_request(self):
        for phase in (1, 5):
            with self.subTest(phase=phase), patch.object(S, "activity_request", return_value={"phase": phase}) as request:
                snapshot = S.benefit_snapshot(SITE, FAKE_SESSION)
            self.assertEqual(snapshot, {"phase": phase, "task_groups": [], "milestones": []})
            request.assert_called_once_with(SITE, FAKE_SESSION, "GET", "/activity-status?location=hmt")

    def test_snapshot_fetches_lists_only_for_bound_active_phase(self):
        responses = {
            "/activity-status?location=hmt": {"phase": 3, "role_bound": True},
            "/ccc-task/list": {"task_groups": [group(1, [])]},
            "/personal-milestone-list": {"milestones": [milestone(status=3)]},
        }
        with patch.object(S, "activity_request", side_effect=lambda _site, _sess, _method, path: responses[path]) as request:
            snapshot = S.benefit_snapshot(SITE, FAKE_SESSION)
        self.assertEqual(snapshot, {"phase": 3, "task_groups": responses["/ccc-task/list"]["task_groups"],
                                    "milestones": responses["/personal-milestone-list"]["milestones"]})
        self.assertEqual([c.args[3] for c in request.call_args_list], list(responses))

        with patch.object(S, "activity_request", return_value={"phase": 3, "role_bound": False}) as request:
            with self.assertRaises(RuntimeError):
                S.benefit_snapshot(SITE, FAKE_SESSION)
        request.assert_called_once()

    def test_phase_tab_and_group_gates(self):
        source = [group(1, [task(101)]), group(2, [task(102)]), group(3, [task(103)]),
                  group(1, [task(104)], locked=True), group(2, [task(105)], ended=True)]
        expected = {
            2: ["available", "locked", "locked", "locked", "locked"],
            3: ["available", "available", "available", "locked", "locked"],
            4: ["locked", "available", "available", "locked", "locked"],
            6: ["available", "locked", "locked", "locked", "locked"],
        }
        for phase, states in expected.items():
            with self.subTest(phase=phase):
                items = S.benefit_items(SITE, {"phase": phase, "task_groups": source, "milestones": []})
                self.assertEqual([item["state"] for item in items], states)
                self.assertTrue(all(type(item["id"]) is int for item in items))
                self.assertEqual([item["kind"] for item in items], ["event_task"] * len(source))

    def test_task_and_milestone_statuses_and_phase_gates(self):
        items = S.benefit_items(SITE, {
            "phase": 3,
            "task_groups": [group(1, [task(1, 1), task(2, 2), task(3, 3), task(4, 4)])],
            "milestones": [milestone(11, 1), milestone(12, 2), milestone(13, 3)],
        })
        self.assertEqual([x["state"] for x in items], [
            "locked", "available", "already", "ended", "locked", "available", "already",
        ])
        late = S.benefit_items(SITE, {
            "phase": 6, "task_groups": [group(1, [task(1)])], "milestones": [milestone(11)],
        })
        self.assertEqual([x["state"] for x in late], ["available", "locked"])
        for phase, state in ((1, "locked"), (5, "ended")):
            self.assertEqual(S.benefit_items(SITE, {"phase": phase})[0]["state"], state)

    def test_malformed_phase_status_and_ids_fail_closed(self):
        bad = [
            {"phase": True, "task_groups": [], "milestones": []},
            {"phase": 3, "task_groups": [group(1, [task(status=5)])], "milestones": []},
            {"phase": 3, "task_groups": [group(1, [task(task_id=True)])], "milestones": []},
            {"phase": 3, "task_groups": [], "milestones": [milestone(status=4)]},
            {"phase": 3, "task_groups": [group(1, [task()], locked=1)], "milestones": []},
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(RuntimeError):
                S.benefit_items(SITE, data)

    def test_ended_milestone_and_locked_task_are_not_claimable(self):
        ended = {**milestone(), "ended": True}
        items = S.benefit_items(SITE, {"phase": 3,
            "task_groups": [group(1, [task(locked=True)])], "milestones": [ended]})
        self.assertEqual([x["state"] for x in items], ["locked", "ended"])

    def test_task_and_milestone_claim_use_only_documented_post_and_verify_state(self):
        cases = (
            ("event_task", 41, "/ccc-task/receive", {"task_id": 41}),
            ("event_milestone", 42, "/personal-milestone-claim", {"milestone_id": 42}),
        )
        for kind, item_id, post_path, payload in cases:
            with self.subTest(kind=kind):
                state = {"status": 2}

                def activity(_site, _session, method, path, data=None):
                    if method == "POST":
                        self.assertEqual((path, data), (post_path, payload))
                        state["status"] = 3
                        return {}
                    if path == "/activity-status?location=hmt":
                        return {"phase": 3, "role_bound": True}
                    if path == "/ccc-task/list":
                        row = task(item_id, state["status"]) if kind == "event_task" else task(99, 3)
                        return {"task_groups": [group(1, [row])]}
                    if path == "/personal-milestone-list":
                        row = milestone(item_id, state["status"]) if kind == "event_milestone" else milestone(98, 3)
                        return {"milestones": [row]}
                    self.fail(f"unexpected request path: {path}")

                item = {"kind": kind, "id": item_id, "label": "fake reward", "state": "available"}
                with patch.object(S, "activity_request", side_effect=activity) as request:
                    results = S.execute_benefits(SITE, FAKE_SESSION, [item], claim=True)
                self.assertEqual(results[0]["state"], "claimed")
                self.assertEqual([c.args[2:5] for c in request.call_args_list if c.args[2] == "POST"],
                                 [("POST", post_path, payload)])
                self.assertNotIn("/ccc-task/complete", [c.args[3] for c in request.call_args_list])
                self.assertNotIn("/appointment", [c.args[3] for c in request.call_args_list])


if __name__ == "__main__":
    unittest.main()
