"""Offline regression tests for daily reveal completion and reward claiming."""
from contextlib import redirect_stdout
from datetime import datetime
import io
import unittest
from unittest.mock import patch

import sword_session as S


SITE = "anniversary2026"
FAKE_SESSION = {
    "account_token": "fake-account-token",
    "aid": "fake-aid",
    "role_id": "fake-role",
    "server_id": "fake-server",
}
FIXED_NOW = datetime.fromisoformat("2026-11-07T12:00:00+08:00").timestamp()
NEWS = {"title": "Fake daily reveal", "content": "Fake public content",
        "image_url": "", "link_url": ""}


def daily_data(status=1, *, phase=2, group_locked=False, group_ended=False,
               row_locked=False, row_ended=False, task_id=1001, task_type="daily"):
    return {
        "phase": phase,
        "task_groups": [{
            "tab_id": 1,
            "locked": group_locked,
            "ended": group_ended,
            "tasks": [{"task_id": task_id, "task_type": task_type,
                       "task_status": status, "locked": row_locked, "ended": row_ended}],
        }],
        "milestones": [],
    }


def planned_item(data):
    return next(item for item in S.benefit_items(SITE, data)
                if item["kind"] == "event_task" and item["id"] == 1001)


class TestDailyRevealPlanning(unittest.TestCase):
    def test_only_eligible_daily_task_1001_gets_completion_marker_and_label(self):
        item = planned_item(daily_data(status=1))
        self.assertEqual(item["label"], "查看今日爆料")
        self.assertTrue(item["can_complete"])

        ineligible = (
            daily_data(status=1, phase=3),
            daily_data(status=1, phase=4),
            daily_data(status=1, phase=5),
            daily_data(status=1, group_locked=True),
            daily_data(status=1, group_ended=True),
            daily_data(status=1, row_locked=True),
            daily_data(status=1, row_ended=True),
            daily_data(status=2),
            daily_data(status=3),
            daily_data(status=1, task_id=1002),
            daily_data(status=1, task_type="weekly"),
        )
        for data in ineligible:
            with self.subTest(data=data):
                items = S.benefit_items(SITE, data)
                row = next((x for x in items if x["kind"] == "event_task"), None)
                self.assertFalse(row and row.get("can_complete", False))


class TestDailyRevealExecution(unittest.TestCase):
    def setUp(self):
        self.status = 1
        self.phase = 2
        self.events = []
        self.news = NEWS
        self.news_hook = None
        self.complete_behavior = "succeed"

        def snapshot(_site, _session):
            self.events.append(("snapshot", self.status))
            return daily_data(self.status, phase=self.phase)

        def request(_site, _session, method, path, payload=None):
            self.events.append((method, path, payload))
            if path == "/news/info?lang=zh_hant":
                if self.news_hook:
                    self.news_hook()
                return {"daily": self.news}
            if path == "/ccc-task/complete":
                if self.complete_behavior == "timeout":
                    raise S.requests.Timeout("fake timeout")
                if self.complete_behavior == "succeed":
                    self.status = 2
                return {}
            if path == "/ccc-task/receive":
                self.status = 3
                return {}
            self.fail(f"unexpected fake request: {method} {path}")

        self.snapshot_patch = patch.object(S, "benefit_snapshot", side_effect=snapshot)
        self.snapshot = self.snapshot_patch.start()
        self.addCleanup(self.snapshot_patch.stop)
        self.request_patch = patch.object(S, "activity_request", side_effect=request)
        self.request = self.request_patch.start()
        self.addCleanup(self.request_patch.stop)
        self.clock = patch.object(S.time, "time", return_value=FIXED_NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.output = io.StringIO()

    def run_claim(self, *, claim=True, initial_status=1, phase=2):
        self.status = initial_status
        self.phase = phase
        items = S.benefit_items(SITE, daily_data(initial_status, phase=phase))
        with redirect_stdout(self.output):
            result = S.execute_benefits(SITE, FAKE_SESSION, items, claim=claim)
        return result

    def posts(self, path=None):
        return [event for event in self.events
                if len(event) == 3 and event[0] == "POST" and (path is None or event[1] == path)]

    def test_claim_runs_news_completion_recheck_then_receive_and_verify_in_order(self):
        result = self.run_claim()
        self.assertEqual(result[0]["state"], "claimed")
        self.assertEqual(self.posts(), [
            ("POST", "/ccc-task/complete", {"task_id": 1001}),
            ("POST", "/ccc-task/receive", {"task_id": 1001}),
        ])
        self.assertEqual(self.events, [
            ("snapshot", 1),
            ("GET", "/news/info?lang=zh_hant", None),
            ("snapshot", 1),
            ("POST", "/ccc-task/complete", {"task_id": 1001}),
            ("snapshot", 2),
            ("snapshot", 2),
            ("POST", "/ccc-task/receive", {"task_id": 1001}),
            ("snapshot", 3),
        ])

    def test_dry_run_does_not_read_news_or_post(self):
        result = self.run_claim(claim=False)
        self.assertEqual(result[0]["state"], "locked")
        self.assertEqual(self.events, [])

    def test_already_complete_daily_task_does_not_read_news_or_recomplete(self):
        result = self.run_claim(initial_status=3)
        self.assertEqual(result[0]["state"], "already")
        self.assertEqual(self.events, [])

    def test_status_two_claims_directly_without_daily_completion(self):
        result = self.run_claim(initial_status=2, phase=3)
        self.assertEqual(result[0]["state"], "claimed")
        self.assertEqual(self.posts(), [("POST", "/ccc-task/receive", {"task_id": 1001})])
        self.assertFalse(any(event[0] == "GET" for event in self.events))

    def test_missing_daily_news_skips_completion(self):
        for value in (None, {}):
            with self.subTest(daily=value):
                self.status = 1
                self.events.clear()
                self.news = value
                result = self.run_claim()
                self.assertEqual(result[0]["state"], "locked")
                self.assertEqual(self.posts(), [])
                self.assertEqual([event[1] for event in self.events if event[0] == "GET"],
                                 ["/news/info?lang=zh_hant"])

    def test_malformed_daily_news_stops_before_completion_and_claim(self):
        malformed = (["not", "an", "object"], {"content": "no title"},
                     {"title": " ", "content": "content"},
                     {"title": "title", "content": None},
                     {"title": "title", "content": "content", "image_url": 9})
        for daily in malformed:
            with self.subTest(daily=daily):
                self.status = 1
                self.events.clear()
                self.news = daily
                with redirect_stdout(self.output), self.assertRaises(RuntimeError):
                    S.execute_benefits(SITE, FAKE_SESSION,
                                       S.benefit_items(SITE, daily_data(1)), claim=True)
                self.assertEqual(self.posts(), [])

    def test_complete_timeout_is_not_retried_and_never_proceeds_to_receive(self):
        self.complete_behavior = "timeout"
        with redirect_stdout(self.output), self.assertRaises(S.requests.Timeout):
            self.run_claim()
        self.assertEqual(self.posts(), [("POST", "/ccc-task/complete", {"task_id": 1001})])
        self.assertFalse(any(event[1] == "/ccc-task/receive" for event in self.events
                             if len(event) == 3))

    def test_unconfirmed_completion_never_proceeds_to_receive(self):
        self.complete_behavior = "unchanged"
        with redirect_stdout(self.output), self.assertRaisesRegex(RuntimeError, "尚未確認爆料任務完成"):
            self.run_claim()
        self.assertEqual(self.posts(), [("POST", "/ccc-task/complete", {"task_id": 1001})])
        self.assertFalse(any(event[1] == "/ccc-task/receive" for event in self.events
                             if len(event) == 3))

    def test_completion_cutoff_skips_daily_completion(self):
        cutoff = datetime.fromisoformat(S.SITES[SITE]["daily_reveal_end_at"]).timestamp()
        with patch.object(S.time, "time", return_value=cutoff):
            result = self.run_claim()
        self.assertEqual(result[0]["state"], "locked")
        self.assertEqual(self.events, [])

    def test_news_request_finishing_at_cutoff_prevents_completion_post(self):
        cutoff = datetime.fromisoformat(S.SITES[SITE]["daily_reveal_end_at"]).timestamp()
        clock = {"now": cutoff - 1}
        self.news_hook = lambda: clock.update(now=cutoff)
        with patch.object(S.time, "time", side_effect=lambda: clock["now"]), \
                redirect_stdout(self.output), self.assertRaisesRegex(RuntimeError, "已截止"):
            self.run_claim()
        self.assertEqual(self.posts(), [])

    def test_second_tab_claiming_during_news_recheck_prevents_every_post(self):
        self.news_hook = lambda: setattr(self, "status", 3)
        result = self.run_claim()
        self.assertEqual(result[0]["state"], "already")
        self.assertEqual(self.posts(), [])
        self.assertEqual([event[1] for event in self.events if event[0] == "GET"],
                         ["/news/info?lang=zh_hant"])

    def test_taipei_midnight_before_completion_stops_before_complete_post(self):
        with patch.object(S, "daily_reveal_day",
                          side_effect=["2026-11-07", "2026-11-07", "2026-11-08"]), \
                redirect_stdout(self.output), self.assertRaisesRegex(RuntimeError, "已跨日"):
            self.run_claim()
        self.assertEqual(self.posts(), [])

    def test_taipei_midnight_before_receive_stops_receive(self):
        with patch.object(S, "daily_reveal_day",
                          side_effect=["2026-11-07", "2026-11-07", "2026-11-07", "2026-11-08"]), \
                redirect_stdout(self.output), self.assertRaisesRegex(RuntimeError, "已跨日"):
            self.run_claim()
        self.assertEqual(self.posts(), [("POST", "/ccc-task/complete", {"task_id": 1001})])
        self.assertFalse(any(event[1] == "/ccc-task/receive" for event in self.events
                             if len(event) == 3))

    def test_taipei_midnight_after_receive_does_not_report_claimed(self):
        with patch.object(S, "daily_reveal_day",
                          side_effect=["2026-11-07", "2026-11-07", "2026-11-07",
                                       "2026-11-07", "2026-11-08"]), \
                redirect_stdout(self.output), self.assertRaisesRegex(RuntimeError, "已跨日"):
            self.run_claim()
        self.assertEqual(self.posts(), [
            ("POST", "/ccc-task/complete", {"task_id": 1001}),
            ("POST", "/ccc-task/receive", {"task_id": 1001}),
        ])

    def test_claiming_already_completed_reward_does_not_apply_completion_cutoff(self):
        cutoff = datetime.fromisoformat(S.SITES[SITE]["daily_reveal_end_at"]).timestamp()
        self.status = 2
        items = S.benefit_items(SITE, daily_data(2))
        with patch.object(S.time, "time", return_value=cutoff):
            with redirect_stdout(self.output):
                result = S.execute_benefits(SITE, FAKE_SESSION, items, claim=True)
        self.assertEqual(result[0]["state"], "claimed")
        self.assertEqual(self.posts(), [("POST", "/ccc-task/receive", {"task_id": 1001})])


if __name__ == "__main__":
    unittest.main()
