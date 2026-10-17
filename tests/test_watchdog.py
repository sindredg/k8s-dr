from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest import mock
import urllib.parse

from scripts import watchdog as module

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
PRIMARY = "198.51.100.7"
RECOVERY = "203.0.113.10"
LOCATIONS = ("usa-virginia", "eur-belgium", "apac-singapore")


def points(minutes_failed: float, minutes_total: float = 8):
    """Results every 20 seconds, newest first: failed for the last minutes_failed minutes."""
    result, offset = [], 20
    while offset <= minutes_total * 60:
        time = NOW - timedelta(seconds=offset)
        result.append({"interval": {"endTime": time.strftime("%Y-%m-%dT%H:%M:%S.123456Z")},
                       "value": {"boolValue": offset > minutes_failed * 60}})
        offset += 20
    return result


class FakeSystem(module.System):
    """A primary that is down everywhere, unless told otherwise."""

    def __init__(self):
        super().__init__({
            "AUTO_FAILOVER": "true", "GITHUB_REPOSITORY": "example/repo", "GITHUB_TOKEN": "token",
            "GCP_PROJECT_ID": "example",
        })
        self.health = {"git.sindrg.com": None, "git-primary.sindrg.com": None}
        self.dns = {"git.sindrg.com": [PRIMARY], "git-primary.sindrg.com": [PRIMARY]}
        self.runs = [{"id": 1, "status": "completed", "conclusion": "success",
                      "triggering_actor": {"login": "operator"}}]
        self.returns: list[dict] = []
        self.series = {location: points(10) for location in LOCATIONS}
        self.checks = [{"name": "projects/example/uptimeCheckConfigs/git-healthz-abc",
                        "monitoredResource": {"labels": {"host": "git.sindrg.com"}}}]
        self.monitoring_status = 200
        self.dispatches: list[tuple[str, dict]] = []
        self.dispatch_status = 204
        self.signed_in = False
        self.query = ""

    def get(self, url, headers=None):
        parsed = urllib.parse.urlparse(url)
        if url.endswith("/api/healthz"):
            return module.Response(self.health[parsed.hostname])
        if url.startswith(module.DOH):
            name = urllib.parse.parse_qs(parsed.query)["name"][0]
            return module.Response(200, json.dumps({"Answer": [
                {"type": 1, "data": address} for address in self.dns[name]]}))
        if "/actions/workflows/recover.yml/runs" in url:
            return module.Response(200, json.dumps({"workflow_runs": self.runs}))
        if "/actions/workflows/return.yml/runs" in url:
            return module.Response(200, json.dumps({"workflow_runs": self.returns}))
        if url.endswith("/uptimeCheckConfigs"):
            return module.Response(self.monitoring_status, json.dumps({"uptimeCheckConfigs": self.checks}))
        if "/timeSeries?" in url:
            self.query = urllib.parse.unquote(parsed.query)
            return module.Response(self.monitoring_status, json.dumps({"timeSeries": [
                {"metric": {"labels": {"checker_location": location}}, "points": series}
                for location, series in self.series.items()]}))
        raise AssertionError(f"unexpected request {url}")

    def post(self, url, body, headers):
        self.dispatches.append((url, body))
        return module.Response(self.dispatch_status)

    def access_token(self):
        self.signed_in = True
        return "cloud-token"

    def now(self):
        return NOW


class WatchdogTestCase(unittest.TestCase):
    def setUp(self):
        self.system = FakeSystem()
        self.output = []

    def watchdog(self, *args):
        with mock.patch("builtins.print", side_effect=lambda *a, **k: self.output.append(" ".join(map(str, a)))):
            return module.main(list(args), system=self.system)

    def candidate(self):
        self.assertEqual(self.watchdog("local"), 0)
        return [line for line in self.output if line.startswith("candidate=")] == ["candidate=true"]

    def started(self):
        self.assertEqual(self.watchdog("confirm", "--start"), 0)
        return bool(self.system.dispatches)


class LocalTests(WatchdogTestCase):
    def test_a_primary_down_everywhere_is_a_candidate(self):
        self.assertTrue(self.candidate())
        self.assertFalse(self.system.signed_in)

    def test_the_switch_off_stops_before_any_request(self):
        for value in (None, "false", "TRUE", "1"):
            with self.subTest(value=value):
                self.system.environ["AUTO_FAILOVER"] = value
                if value is None:
                    del self.system.environ["AUTO_FAILOVER"]
                self.output.clear()
                self.assertFalse(self.candidate())

    def test_a_public_name_that_answers_is_no_candidate(self):
        self.system.health["git.sindrg.com"] = 200
        self.assertFalse(self.candidate())

    def test_a_primary_name_that_answers_is_no_candidate(self):
        self.system.health["git-primary.sindrg.com"] = 200
        self.assertFalse(self.candidate())

    def test_an_error_status_counts_as_down(self):
        self.system.health = {"git.sindrg.com": 502, "git-primary.sindrg.com": 404}
        self.assertTrue(self.candidate())

    def test_a_public_name_on_another_address_is_no_candidate(self):
        self.system.dns["git.sindrg.com"] = [RECOVERY]
        self.assertFalse(self.candidate())

    def test_an_unresolved_public_name_is_no_candidate(self):
        self.system.dns = {"git.sindrg.com": [], "git-primary.sindrg.com": []}
        self.assertFalse(self.candidate())

    def test_a_running_recovery_is_no_candidate(self):
        for status in module.ACTIVE:
            with self.subTest(status=status):
                self.system.runs = [{"id": 2, "status": status, "conclusion": None,
                                     "triggering_actor": {"login": "operator"}}]
                self.output.clear()
                self.assertFalse(self.candidate())

    def test_a_running_return_is_no_candidate(self):
        # A return past its DNS step moves the public name and removes the recovery cluster.
        self.system.returns = [{"id": 3, "status": "in_progress", "conclusion": None}]
        self.assertFalse(self.candidate())
        self.system.returns = [{"id": 3, "status": "completed", "conclusion": "success"}]
        self.output.clear()
        self.assertTrue(self.candidate())

    def test_a_failed_automatic_start_waits_for_a_person(self):
        self.system.runs = [{"id": 2, "status": "completed", "conclusion": "failure",
                             "triggering_actor": {"login": module.BOT}}]
        self.assertFalse(self.candidate())

    def test_a_failed_run_started_by_a_person_does_not_block(self):
        self.system.runs = [{"id": 2, "status": "completed", "conclusion": "failure",
                             "triggering_actor": {"login": "operator"}}]
        self.assertTrue(self.candidate())


class ConfirmTests(WatchdogTestCase):
    def test_every_checker_failed_for_the_window_starts_one_full_recovery(self):
        self.assertTrue(self.started())
        url, body = self.system.dispatches[0]
        self.assertEqual(url, "https://api.github.com/repos/example/repo/actions/workflows/recover.yml/dispatches")
        self.assertEqual(body, {"ref": "main", "inputs": {"through": "all", "reset": True}})
        self.assertIn('metric.labels.check_id="git-healthz-abc"', self.system.query)

    def test_a_failure_shorter_than_the_window_starts_nothing(self):
        self.system.series = {location: points(4.5) for location in LOCATIONS}
        self.assertFalse(self.started())

    def test_failures_that_do_not_reach_back_to_the_window_start_start_nothing(self):
        # No result passed, but the oldest result is newer than the window.
        self.system.series["usa-virginia"] = points(10, minutes_total=3)
        self.assertFalse(self.started())

    def test_one_passing_checker_starts_nothing(self):
        self.system.series["eur-belgium"] = points(2)
        self.assertFalse(self.started())

    def test_a_checker_without_results_in_the_window_starts_nothing(self):
        self.system.series["apac-singapore"] = []
        self.assertFalse(self.started())

    def test_results_that_end_before_the_window_start_nothing(self):
        self.system.series["apac-singapore"] = [p for p in points(10) if p["value"]["boolValue"] is False][-3:]
        self.assertFalse(self.started())

    def test_a_checker_that_stopped_reporting_starts_nothing(self):
        # Failed results reach back to the window start, then stop four minutes ago.
        self.system.series["eur-belgium"] = [
            p for p in points(10) if module.point_time(p) <= NOW - timedelta(minutes=4)]
        self.assertFalse(self.started())

    def test_fewer_checkers_than_regions_start_nothing(self):
        del self.system.series["eur-belgium"]
        self.assertFalse(self.started())

    def test_a_failed_monitoring_read_starts_nothing(self):
        self.system.monitoring_status = 403
        self.assertFalse(self.started())

    def test_an_ambiguous_uptime_check_starts_nothing(self):
        self.system.checks = self.system.checks * 2
        self.assertFalse(self.started())

    def test_confirm_repeats_the_local_checks(self):
        self.system.health["git.sindrg.com"] = 200
        self.assertFalse(self.started())
        self.assertFalse(self.system.signed_in)

    def test_without_start_nothing_is_dispatched(self):
        self.assertEqual(self.watchdog("confirm"), 0)
        self.assertEqual(self.system.dispatches, [])

    def test_a_failed_dispatch_fails_the_job(self):
        self.system.dispatch_status = 403
        with self.assertRaises(RuntimeError):
            self.watchdog("confirm", "--start")


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        import yaml
        from pathlib import Path
        self.workflow = yaml.safe_load(Path(".github/workflows/watchdog.yml").read_text())

    def test_runs_every_five_minutes_and_by_hand(self):
        self.assertEqual(self.workflow["on"]["schedule"], [{"cron": "*/5 * * * *"}])
        self.assertIn("workflow_dispatch", self.workflow["on"])
        self.assertNotIn("pull_request", self.workflow["on"])

    def test_only_the_confirm_job_signs_in_to_google_cloud(self):
        local, confirm = self.workflow["jobs"]["local"], self.workflow["jobs"]["confirm"]
        self.assertNotIn("environment", local)
        self.assertEqual(local["permissions"], {"contents": "read", "actions": "read"})
        self.assertEqual(confirm["environment"], "recovery")
        self.assertEqual(confirm["needs"], "local")
        self.assertEqual(confirm["if"], "needs.local.outputs.candidate == 'true'")
        self.assertEqual(confirm["permissions"], {"contents": "read", "id-token": "write", "actions": "write"})
        self.assertEqual(self.workflow["permissions"], {})

    def test_both_jobs_read_the_switch(self):
        for name, job in self.workflow["jobs"].items():
            with self.subTest(job=name):
                self.assertEqual(job["env"]["AUTO_FAILOVER"], "${{ vars.AUTO_FAILOVER }}")


if __name__ == "__main__":
    unittest.main()
