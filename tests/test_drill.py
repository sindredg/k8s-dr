from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.parse

import yaml

from scripts import drill as module
from scripts import drill_report, recover, watchdog

START = datetime(2026, 10, 19, 6, 17, tzinfo=timezone.utc)
PRIMARY = "198.51.100.7"
BOT = {"login": "github-actions[bot]"}
LOCATIONS = ("usa-virginia", "eur-belgium", "apac-singapore")
HEAD = "b" * 40
SET = "20261019T063201Z"
FORMAT = drill_report.TIME_FORMAT


def at(minutes: float) -> datetime:
    return START + timedelta(minutes=minutes)


def ok(stdout=""):
    return recover.Result(0, stdout, "")


class FakeSystem(module.System):
    """A drill in which every check passes and the watchdog recovers, unless told otherwise."""

    def __init__(self, work_dir: Path):
        super().__init__({
            "SCHEDULED_DRILLS": "true", "AUTO_FAILOVER": "true", "GITHUB_REPOSITORY": "example/repo",
            "GITHUB_TOKEN": "token", "GCP_PROJECT_ID": "example-project", "STATE_BUCKET": "example-state",
            "BACKUP_BUCKET": "example-backups", "RUN_URL": "https://github.com/example/repo/actions/runs/7",
            "GITHUB_EVENT_NAME": "schedule",
        })
        self.work_dir = work_dir
        self.clock = START
        self.calls: list[list[str]] = []
        self.failing: set[str] = set()
        self.outputs: dict[str, str] = {}
        self.sleeps: list[float] = []
        self.posts: list[tuple[str, dict]] = []
        self.post_status = {"dispatches": 204, "issues": 201}
        self.dns = {"git.sindrg.com": [PRIMARY], "git-primary.sindrg.com": [PRIMARY]}
        self.recover_runs: list[dict] = []
        self.return_runs: list[dict] = []
        self.github_status = 200
        self.newest_set = "20261019T060701Z"
        self.isolated_minute = None
        # Written by the Recover run that the watchdog starts.
        self.recovered = {"started": None, "passed": list(recover.STAGE_NAMES),
                          "values": {"restored_head": HEAD, "backup_set": SET, "bucket": "example-backups",
                                     "recovery_address": "203.0.113.10"}}
        self.timeline = ""
        self.series: dict[str, list] = {}
        # When the scripted Recover and Return runs appear and end, in minutes after START.
        self.recovery_plan = {"created": 50, "ends": 70, "conclusion": "success"}
        self.return_plan = {"ends": 20, "conclusion": "success"}

    # Commands

    def call(self, argv, *, capture=False, echo=True, env=None):
        self.calls.append(list(argv))
        line = " ".join(argv)
        for needle in self.failing:
            if needle in line:
                return recover.Result(1, "", f"{needle} failed")
        if line in self.outputs:
            return ok(self.outputs[line])
        if line.endswith("state list"):
            return ok("")
        if line.startswith("gcloud storage ls gs://example-backups/primary/"):
            return ok(f"gs://example-backups/primary/{self.newest_set}/manifest.json\n")
        if "dataset.py check" in line:
            return ok("dataset: 8 repositories at the expected head, 200 issues in dataset-01")
        if "write-check" in line:
            log = Path(next(arg for arg in argv if arg.startswith("WRITE_LOG=")).split("=", 1)[1])
            with log.open("a") as handle:
                handle.write(f"{self.clock.strftime(FORMAT)}\t{self.sha(len(self.calls))}\thttps://git.sindrg.com\n")
            return ok()
        if "output -json instance_names" in line:
            return ok(json.dumps({"control-plane": "primary-cp", "worker": "primary-worker"}))
        if line.endswith("output -raw zone"):
            return ok("europe-north1-a")
        if line.endswith("output -raw project_id"):
            return ok("example-project")
        if line.startswith("gcloud compute instances stop"):
            self.isolated_minute = (self.clock - START).total_seconds() / 60
            return ok()
        if line.startswith("gcloud storage cp gs://example-state/pipeline/drill/"):
            name, target = argv[3].rsplit("/", 1)[-1], Path(argv[4])
            target.write_text(json.dumps(self.recovered) if name == recover.STATE_FILE else self.timeline)
            return ok()
        return ok()

    def sha(self, number: int) -> str:
        # The restored HEAD is the third write.
        return HEAD if number == self.third_write_call() else f"{number:040x}"

    def third_write_call(self) -> int:
        writes = [index for index, argv in enumerate(self.calls, 1) if "write-check" in " ".join(argv)]
        return writes[2] if len(writes) >= 3 else -1

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.clock += timedelta(seconds=seconds)

    def now(self):
        return self.clock

    def access_token(self):
        return "cloud-token"

    # Requests

    def scripted_runs(self, workflow: str) -> list[dict]:
        minute = (self.clock - START).total_seconds() / 60
        if workflow == "recover.yml":
            runs = list(self.recover_runs)
            plan = self.recovery_plan
            if self.isolated_minute is not None and plan and minute >= plan["created"]:
                done = minute >= plan["ends"]
                runs.insert(0, {"id": 900, "status": "completed" if done else "in_progress",
                                "conclusion": plan["conclusion"] if done else None,
                                "created_at": at(plan["created"]).strftime(FORMAT),
                                "html_url": "https://github.com/example/repo/actions/runs/900",
                                "triggering_actor": BOT})
            return runs
        runs = list(self.return_runs)
        dispatch = next((index for index, (url, _) in enumerate(self.posts) if "return.yml" in url), None)
        if dispatch is not None:
            created = self.return_dispatched
            done = self.clock >= created + timedelta(minutes=self.return_plan["ends"])
            runs.insert(0, {"id": 901, "status": "completed" if done else "queued",
                            "conclusion": self.return_plan["conclusion"] if done else None,
                            "created_at": (created + timedelta(seconds=5)).strftime(FORMAT),
                            "html_url": "https://github.com/example/repo/actions/runs/901",
                            "triggering_actor": BOT})
        return runs

    def get(self, url, headers=None):
        parsed = urllib.parse.urlparse(url)
        if url.startswith(watchdog.DOH):
            name = urllib.parse.parse_qs(parsed.query)["name"][0]
            return watchdog.Response(200, json.dumps({"Answer": [{"type": 1, "data": a} for a in self.dns[name]]}))
        for workflow in ("recover.yml", "return.yml"):
            if f"/actions/workflows/{workflow}/runs" in url:
                if self.github_status != 200:
                    return watchdog.Response(self.github_status)
                return watchdog.Response(200, json.dumps({"workflow_runs": self.scripted_runs(workflow)}))
        if url.endswith("/uptimeCheckConfigs"):
            return watchdog.Response(200, json.dumps({"uptimeCheckConfigs": [
                {"name": "projects/example-project/uptimeCheckConfigs/git-healthz-abc",
                 "monitoredResource": {"labels": {"host": "git.sindrg.com"}}}]}))
        if "/timeSeries?" in url:
            return watchdog.Response(200, json.dumps({"timeSeries": [
                {"metric": {"labels": {"checker_location": location}}, "points": points}
                for location, points in self.series.items()]}))
        raise AssertionError(f"unexpected request {url}")

    def post(self, url, body, headers):
        self.posts.append((url, body))
        if url.endswith("return.yml/dispatches"):
            self.return_dispatched = self.clock
        return watchdog.Response(self.post_status["issues" if url.endswith("/issues") else "dispatches"])


def uptime(first_failed_minute: float, recovered_minute: float) -> list[dict]:
    """One result a minute from START, failed between the two minutes."""
    result = []
    for minute in range(0, int(recovered_minute) + 5):
        result.append({"interval": {"endTime": at(minute).strftime("%Y-%m-%dT%H:%M:%S.000Z")},
                       "value": {"boolValue": not (first_failed_minute <= minute < recovered_minute)}})
    return result


class DrillTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.work_dir = Path(self.directory.name) / "drill"
        self.system = FakeSystem(self.work_dir)
        self.errors: list[str] = []
        self.output: list[str] = []

    def step(self, name: str) -> int:
        def record(*args, **kwargs):
            (self.errors if kwargs.get("file") is not None else self.output).append(" ".join(map(str, args)))
        with mock.patch("builtins.print", side_effect=record), mock.patch.object(module.sys, "stderr"):
            return module.main(["--work-dir", str(self.work_dir), name], system=self.system)

    def state(self) -> dict:
        return json.loads((self.work_dir / module.STATE_FILE).read_text())

    def ran(self, needle: str) -> bool:
        return any(needle in " ".join(argv) for argv in self.system.calls)

    def through(self, last: str) -> None:
        for name in module.STEPS[: module.STEPS.index(last) + 1]:
            self.assertEqual(self.step(name), 0, (name, self.errors))

    def script_recovery(self) -> None:
        """A Recover run started 50 minutes in, whose stages end 69 minutes in."""
        events = [(at(51), "preflight", "start", ""), (at(52), "preflight", "end", ""),
                  (at(53), "bootstrap", "start", ""), (at(61), "bootstrap", "end", ""),
                  (at(62), "restore", "start", ""), (at(64), "restore", "end", ""),
                  (at(65), "probe-recovery", "start", ""), (at(69), "probe-recovery", "end", "")]
        self.system.timeline = "".join(f"{time.strftime(FORMAT)}\t{stage}\t{event}\t{note}\n"
                                       for time, stage, event, note in events)
        self.system.recovered["started"] = at(51).strftime(FORMAT)
        # The isolation is at minute 25, after the six writes.
        self.system.series = {location: uptime(26 + offset, 69) for offset, location in enumerate(LOCATIONS)}


class GuardTests(DrillTestCase):
    def test_a_ready_primary_passes_without_any_change(self):
        self.assertEqual(self.step("guard"), 0, self.errors)
        self.assertEqual(self.state()["steps"], {"guard": "passed"})
        self.assertTrue(self.ran("make --no-print-directory preflight BIN="))
        self.assertTrue(self.ran("dataset.py check --host git.sindrg.com"))
        self.assertFalse(self.ran("instances stop"))
        self.assertEqual(self.system.posts, [])
        self.assertTrue(self.ran("gcloud storage rsync --recursive"))

    def refused(self, reason: str) -> None:
        self.assertEqual(self.step("guard"), 1)
        self.assertEqual(self.state()["failed"]["step"], "guard")
        self.assertIn(reason, self.state()["failed"]["reason"])
        self.assertFalse(self.ran("instances stop"))

    def test_either_switch_off_refuses(self):
        for name in ("SCHEDULED_DRILLS", "AUTO_FAILOVER"):
            with self.subTest(name=name):
                self.setUp()
                self.system.environ[name] = "false"
                self.refused(name)
                self.assertFalse(self.ran("preflight"))

    def test_an_active_recovery_or_return_refuses(self):
        for attribute, label in (("recover_runs", "recovery run"), ("return_runs", "return run")):
            with self.subTest(workflow=attribute):
                self.setUp()
                setattr(self.system, attribute, [{"id": 5, "status": "in_progress", "conclusion": None,
                                                  "triggering_actor": {"login": "operator"}}])
                self.refused(label)

    def test_a_failed_automatic_recovery_refuses(self):
        self.system.recover_runs = [{"id": 5, "status": "completed", "conclusion": "failure",
                                     "triggering_actor": BOT}]
        self.refused("ended with failure")

    def test_resources_in_the_recovery_state_refuse(self):
        self.system.outputs["terraform -chdir=infra/recovery state list"] = "google_compute_network.recovery\n"
        self.refused("infra/recovery holds 1 resources")

    def test_a_public_name_off_the_primary_refuses(self):
        self.system.dns["git.sindrg.com"] = ["203.0.113.10"]
        self.refused("does not resolve to the address")

    def test_a_set_two_intervals_old_refuses(self):
        self.system.newest_set = "20261019T054700Z"
        self.refused("30 minutes old")

    def test_a_set_just_under_two_intervals_old_passes(self):
        self.system.newest_set = "20261019T054701Z"
        self.assertEqual(self.step("guard"), 0, self.errors)

    def test_a_failed_preflight_or_dataset_check_refuses(self):
        for needle in ("preflight", "dataset.py check"):
            with self.subTest(needle=needle):
                self.setUp()
                self.system.failing.add(needle)
                self.refused("exited 1")

    def test_a_second_guard_in_the_same_work_directory_refuses(self):
        self.assertEqual(self.step("guard"), 0, self.errors)
        self.assertEqual(self.step("guard"), 1)
        self.assertIn("earlier drill", self.errors[-1])


class StepTests(DrillTestCase):
    def test_writes_push_six_checks_five_minutes_apart(self):
        self.through("writes")
        self.assertEqual(sum("write-check" in " ".join(argv) for argv in self.system.calls), 6)
        self.assertEqual(self.system.sleeps, [300] * 5)
        lines = (self.work_dir / module.WRITES_FILE).read_text().splitlines()
        self.assertEqual(len(lines), 6)

    def test_a_failed_write_stops_before_the_isolation(self):
        self.assertEqual(self.step("guard"), 0)
        self.system.failing.add("write-check")
        self.assertEqual(self.step("writes"), 1)
        self.assertEqual(self.step("isolate"), 1)
        self.assertIn("every earlier step", self.errors[-1])
        self.assertFalse(self.ran("instances stop"))

    def test_isolation_records_the_time_before_it_stops_both_vms(self):
        self.through("isolate")
        self.assertEqual(self.state()["isolated_at"], at(25).strftime(FORMAT))
        stop = next(argv for argv in self.system.calls if argv[:4] == ["gcloud", "compute", "instances", "stop"])
        self.assertEqual(stop[4:6], ["primary-cp", "primary-worker"])
        self.assertEqual(stop[6:], ["--zone", "europe-north1-a", "--project", "example-project"])

    def test_a_failed_copy_to_the_bucket_does_not_stop_the_drill(self):
        self.system.failing.add("storage rsync")
        self.through("isolate")
        self.assertTrue(self.ran("instances stop"))
        self.assertEqual(self.step("finish"), 1)

    def test_a_step_does_not_run_out_of_order(self):
        self.assertEqual(self.step("isolate"), 1)
        self.assertFalse(self.ran("instances stop"))

    def test_wait_passes_once_the_watchdog_run_succeeds(self):
        self.through("wait")
        self.assertEqual(self.state()["recovery_run"]["id"], 900)
        self.assertGreaterEqual(self.system.clock, at(70))
        self.assertEqual(self.system.posts, [])

    def test_wait_ignores_runs_started_by_a_person_or_before_the_isolation(self):
        self.system.recovery_plan = None
        self.system.recover_runs = [
            {"id": 1, "status": "completed", "conclusion": "success", "created_at": at(30).strftime(FORMAT),
             "html_url": "x", "triggering_actor": {"login": "operator"}},
            {"id": 2, "status": "completed", "conclusion": "success", "created_at": at(10).strftime(FORMAT),
             "html_url": "x", "triggering_actor": BOT}]
        self.through("isolate")
        self.assertEqual(self.step("wait"), 1)
        self.assertIn("no run started by 2026-10-19T10:42:00Z", self.state()["failed"]["reason"])

    def test_a_failed_watchdog_run_fails_the_wait(self):
        self.system.recovery_plan["conclusion"] = "failure"
        self.through("isolate")
        self.assertEqual(self.step("wait"), 1)
        self.assertIn("ended with failure", self.state()["failed"]["reason"])

    def test_a_github_error_while_waiting_only_waits(self):
        self.through("isolate")
        original = self.system.get
        calls = {"count": 0}

        def flaky(url, headers=None):
            calls["count"] += 1
            if calls["count"] == 1:
                return watchdog.Response(502)
            return original(url, headers)
        self.system.get = flaky
        self.assertEqual(self.step("wait"), 0, self.errors)


class ReportTests(DrillTestCase):
    def setUp(self):
        super().setUp()
        self.script_recovery()

    def test_recovery_time_runs_from_the_first_failed_probe_to_the_checks(self):
        self.through("report")
        result = self.state()["result"]
        self.assertEqual(result["first_failed_probe"], at(26).strftime(FORMAT))
        self.assertEqual(result["recovery_seconds"], 43 * 60)
        self.assertEqual(result["detection_seconds"], 24 * 60)
        self.assertEqual(result["stages"]["bootstrap"], 8 * 60)
        self.assertTrue(result["met"])
        self.assertIn("8 repositories", self.state()["dataset_restored"])
        self.assertNotIn("bucket", json.dumps(result))

    def test_data_loss_runs_from_the_restored_write_to_the_last_one(self):
        self.through("report")
        result = self.state()["result"]
        self.assertEqual(result["restored_head"], HEAD)
        self.assertEqual(result["data_loss_seconds"], 15 * 60)
        self.assertEqual(result["writes_lost"], 3)
        self.assertEqual(result["backup_age_seconds"], 9 * 60 + 59)

    def test_a_failed_probe_before_the_isolation_does_not_count(self):
        for points in self.system.series.values():
            points[5]["value"]["boolValue"] = False
        self.through("report")
        self.assertEqual(self.state()["result"]["first_failed_probe"], at(26).strftime(FORMAT))

    def test_fewer_than_three_failed_checkers_fail_the_report(self):
        del self.system.series["apac-singapore"]
        self.through("wait")
        self.assertEqual(self.step("report"), 1)
        self.assertIn("2 checkers reported a failure", self.state()["failed"]["reason"])

    def test_a_run_state_from_before_the_isolation_fails_the_report(self):
        self.system.recovered["started"] = at(5).strftime(FORMAT)
        self.through("wait")
        self.assertEqual(self.step("report"), 1)
        self.assertIn("not from a run started after the isolation", self.state()["failed"]["reason"])

    def test_a_restored_head_outside_the_write_log_fails_the_report(self):
        self.system.recovered["values"]["restored_head"] = "c" * 40
        self.through("wait")
        self.assertEqual(self.step("report"), 1)
        self.assertIn("not a write acknowledged before isolation", self.state()["failed"]["reason"])

    def test_a_missed_target_stops_before_the_return(self):
        self.system.series = {location: uptime(26, 69) for location in LOCATIONS}
        self.through("wait")
        with mock.patch.object(module, "RTO_TARGET", timedelta(minutes=42)):
            self.assertEqual(self.step("report"), 1)
        self.assertIs(self.state()["result"]["met"], False)
        self.assertEqual(self.step("return"), 1)
        self.assertEqual(self.system.posts, [])
        self.step("finish")
        self.assertEqual(self.state()["outcome"], "missed")


class ReturnTests(DrillTestCase):
    def setUp(self):
        super().setUp()
        self.script_recovery()

    def test_return_dispatches_with_the_discard_input_and_waits(self):
        self.through("return")
        url, body = self.system.posts[0]
        self.assertTrue(url.endswith("/actions/workflows/return.yml/dispatches"))
        self.assertEqual(body, {"ref": "main", "inputs": {"discard_recovery_writes": True}})
        self.assertEqual(self.state()["return_run"]["id"], 901)

    def test_a_failed_return_fails_the_drill(self):
        self.system.return_plan["conclusion"] = "failure"
        self.through("report")
        self.assertEqual(self.step("return"), 1)
        self.step("finish")
        self.assertEqual(self.state()["outcome"], "failed")


class FinishTests(DrillTestCase):
    def issue(self) -> dict:
        return next(body for url, body in self.system.posts if url.endswith("/issues"))

    def test_a_passed_drill_opens_one_labelled_issue_and_stores_the_result(self):
        self.script_recovery()
        self.through("return")
        self.assertEqual(self.step("finish"), 0, self.errors)
        issue = self.issue()
        self.assertEqual(issue["labels"], ["drill"])
        self.assertEqual(issue["title"], f"Drill {self.state()['id']}: passed")
        self.assertIn("| Recovery time (target 4 h) | 0h 43m 00s |", issue["body"])
        self.assertTrue((self.work_dir / module.RESULT_FILE).exists())

    def test_a_refused_drill_says_the_primary_was_not_isolated(self):
        self.system.environ["AUTO_FAILOVER"] = "false"
        self.step("guard")
        self.assertEqual(self.step("finish"), 0, self.errors)
        self.assertEqual(self.state()["outcome"], "refused")
        self.assertIn("The primary was not isolated.", self.issue()["body"])

    def test_a_job_that_stopped_before_the_guard_is_refused(self):
        self.assertEqual(self.step("finish"), 0, self.errors)
        self.assertEqual(self.state()["outcome"], "refused")
        self.assertIn("before the guard", self.issue()["body"])

    def test_a_drill_cut_off_after_the_isolation_failed(self):
        self.through("isolate")
        self.step("finish")
        self.assertEqual(self.state()["outcome"], "failed")
        self.assertIn("The return did not finish", self.issue()["body"])

    def test_the_issue_body_masks_every_secret_value(self):
        self.system.environ["AUTO_FAILOVER"] = "false"
        self.step("guard")
        drill = module.Drill(self.system, self.work_dir)
        drill.state["failed"]["reason"] = "gs://example-state and example-project and example-backups"
        body = drill.issue_body("refused")
        for value in ("example-state", "example-project", "example-backups"):
            self.assertNotIn(value, body)

    def test_a_failed_issue_fails_the_finish(self):
        self.system.post_status["issues"] = 403
        self.assertEqual(self.step("finish"), 1)


class ResultsTests(unittest.TestCase):
    def test_the_table_has_one_row_per_drill_oldest_first(self):
        passed = {"id": "20261021T061700Z", "outcome": "passed", "event": "schedule", "run_url": "https://x/2",
                  "result": {"detection_seconds": 1500, "recovery_seconds": 2700, "data_loss_seconds": 600,
                             "writes_lost": 2, "stages": {"bootstrap": 480, "restore": 120}}}
        refused = {"id": "20261019T061700Z", "outcome": "refused", "event": "workflow_dispatch"}
        lines = module.results_table([passed, refused]).splitlines()
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[2].startswith("| 20261019T061700Z | dispatch | refused |"))
        self.assertEqual(lines[3], "| [20261021T061700Z](https://x/2) | schedule | passed | 0h 25m 00s "
                         "| 0h 45m 00s | 0h 10m 00s | 2 | 0h 08m 00s | 0h 02m 00s |")


    def test_the_page_keeps_its_text_around_the_markers(self):
        page = f"intro\n{module.RESULTS_START}\nold\n{module.RESULTS_END}\noutro\n"
        self.assertEqual(module.replace_results(page, "new"),
                         f"intro\n{module.RESULTS_START}\nnew\n{module.RESULTS_END}\noutro\n")
        with self.assertRaises(module.DrillFailed):
            module.replace_results("no markers", "new")


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.safe_load(Path(".github/workflows/drill.yml").read_text())
        self.job = self.workflow["jobs"]["drill"]

    def test_runs_on_monday_wednesday_and_friday_and_by_hand(self):
        self.assertEqual(self.workflow["on"]["schedule"], [{"cron": "17 6 * * 1,3,5"}])
        self.assertIn("workflow_dispatch", self.workflow["on"])
        self.assertNotIn("pull_request", self.workflow["on"])

    def test_the_switch_skips_the_job_and_the_guard_reads_both_switches(self):
        self.assertEqual(self.job["if"], "vars.SCHEDULED_DRILLS == 'true'")
        self.assertEqual(self.job["env"]["SCHEDULED_DRILLS"], "${{ vars.SCHEDULED_DRILLS }}")
        self.assertEqual(self.job["env"]["AUTO_FAILOVER"], "${{ vars.AUTO_FAILOVER }}")

    def test_the_job_has_its_own_concurrency_group_and_fits_a_hosted_job(self):
        # Sharing the recovery group would block the Recover run it waits for.
        self.assertEqual(self.workflow["concurrency"]["group"], "drill")
        self.assertLessEqual(self.job["timeout-minutes"], 360)
        self.assertEqual(self.workflow["permissions"], {})
        self.assertEqual(self.job["permissions"], {"contents": "read", "id-token": "write",
                                                   "actions": "write", "issues": "write"})
        self.assertEqual(self.job["environment"], "recovery")

    def test_the_steps_run_in_order_and_finish_always(self):
        commands = [step["run"].split()[4] for step in self.job["steps"]
                    if "scripts/drill.py" in step.get("run", "")]
        self.assertEqual(commands, [*module.STEPS, "finish"])
        finish = next(step for step in self.job["steps"] if "drill.py --work-dir \"$WORK_DIR\" finish" in step.get("run", ""))
        self.assertEqual(finish["if"], "always()")


if __name__ == "__main__":
    unittest.main()
