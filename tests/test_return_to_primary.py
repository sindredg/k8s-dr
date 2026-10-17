from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import drill_report
from scripts import return_to_primary as module
from tests.test_recover import INVENTORY, RECOVERY_ADDRESS, FakeSystem as RecoveryFakeSystem

PRIMARY_ADDRESS = "198.51.100.20"
WORKER = "primary-worker@example.iam.gserviceaccount.com"
DESTROY_PLAN = {"resource_changes": [
    {"address": "module.recovery_cluster.google_compute_instance.node[\"worker\"]",
     "type": "google_compute_instance", "change": {"actions": ["delete"]}},
    {"address": "module.recovery_cluster.google_compute_address.web",
     "type": "google_compute_address", "change": {"actions": ["delete"]}},
]}
# The recovery run state after a full drill.
RECOVERED = {
    "started": "2026-10-16T12:00:00Z",
    "passed": ["preflight", "provisioning", "bootstrap", "flux-reconcile", "restore", "verification",
               "fence", "dns-cutover", "probe-recovery", "resume-backups"],
    "values": {"recovery_address": RECOVERY_ADDRESS, "bucket": "example-backups"},
}


class FakeSystem(RecoveryFakeSystem):
    """Answers every command of a return that succeeds, unless told otherwise."""

    def __init__(self):
        super().__init__()
        self.dns = {"git.sindrg.com": RECOVERY_ADDRESS, "git-dr.sindrg.com": RECOVERY_ADDRESS}
        self.suspend = "false"
        self.primary_status = [200]
        self.destroy_plan = DESTROY_PLAN
        self.destroyed = False
        self.disabled = "True"
        # Set names that appear under primary/, newest last, one per listing.
        self.primary_sets = ["20261016T115201Z"]
        # Answers of this machine's resolver that lag behind 1.1.1.1, used in order.
        self.local_lag: list[str] = []

    def local_addresses(self, host):
        if self.local_lag:
            return [self.local_lag.pop(0)]
        return [self.dns[host]] if host in self.dns else []

    def answer(self, argv, line):
        ok = lambda out="": module.recover.Result(0, out, "")  # noqa: E731
        if line.startswith("terraform -chdir=infra/recovery show -json"):
            return ok(json.dumps(self.destroy_plan))
        if line.startswith("terraform -chdir=infra/recovery apply"):
            self.destroyed = True
            return ok()
        if line.startswith("terraform -chdir=infra/recovery state list"):
            return ok("" if self.destroyed else "google_compute_address.web\n")
        if line.startswith("terraform -chdir=infra/primary output -raw public_web_address"):
            return ok(PRIMARY_ADDRESS)
        if line.startswith("terraform -chdir=infra/recovery output -raw public_web_address"):
            if self.destroyed:
                return module.recover.Result(1, "", "No outputs found")
            return ok(RECOVERY_ADDRESS)
        if argv[:3] == ["make", "--no-print-directory", "dns-delete"]:
            self.dns.pop(f"{argv[3].split('=', 1)[1]}.sindrg.com", None)
            return ok()
        if "kubectl delete configmap/run-settings" in line:
            self.suspend = "true"
            return ok()
        if "service-accounts enable" in line:
            self.disabled = "False"
            return ok()
        if "service-accounts describe" in line:
            return ok(self.disabled + "\n")
        if line.startswith("gcloud storage ls gs://example-backups/primary/"):
            if len(self.primary_sets) > 1 and self.ran("service-accounts enable"):
                self.primary_sets.pop(0)
            return ok(f"gs://example-backups/primary/{self.primary_sets[0]}/manifest.json\n")
        return super().answer(argv, line)


class ReturnTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.drill = Path(directory.name) / ".drill"
        self.drill.mkdir()
        (self.drill / module.recover.STATE_FILE).write_text(json.dumps(RECOVERED))
        (self.drill / module.recover.TIMELINE_FILE).write_text("")
        self.inventory = Path(directory.name) / "hosts.json"
        self.inventory.write_text(json.dumps(INVENTORY))
        patcher = mock.patch.object(module.recover, "INVENTORY", str(self.inventory))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.system = FakeSystem()
        # The newest primary set starts at the next quarter hour after the fence lifts.
        self.system.primary_sets.append((self.system.clock + timedelta(minutes=15))
                                        .strftime(drill_report.SET_FORMAT))

    def back(self, *args):
        with mock.patch("sys.stdout"), mock.patch("sys.stderr"), mock.patch.dict("os.environ", clear=False) as env:
            env.pop("GITHUB_STEP_SUMMARY", None)
            return module.main(["--drill-dir", str(self.drill), *args], system=self.system)

    def run_back(self):
        return self.back("run", "--discard-recovery-writes")

    def state(self):
        return json.loads((self.drill / module.STATE_FILE).read_text())

    def events(self):
        return [(stage, event, note)
                for _, stage, event, note in drill_report.read_events(self.drill / module.TIMELINE_FILE)]

    def commands(self):
        return [" ".join(argv) for argv in self.system.calls]

    def index(self, needle):
        return next(i for i, line in enumerate(self.commands()) if needle in line)


class RunTests(ReturnTestCase):
    def test_runs_every_stage_in_runbook_order_and_archives_the_state(self):
        self.assertEqual(self.run_back(), 0)
        self.assertEqual(self.system.dns, {"git.sindrg.com": PRIMARY_ADDRESS})
        self.assertTrue(self.system.destroyed)
        for name in (module.STATE_FILE, module.TIMELINE_FILE,
                     module.recover.STATE_FILE, module.recover.TIMELINE_FILE):
            self.assertFalse((self.drill / name).exists(), name)
        archived = sorted(path.name.split("-")[0] for path in self.drill.iterdir() if "-" in path.name)
        self.assertEqual(archived, ["recover", "return", "return", "timeline"])
        timeline = next(self.drill.glob("return-*.tsv"))
        ends = [stage for _, stage, event, _ in drill_report.read_events(timeline) if event == "end"]
        self.assertEqual(ends, list(module.STAGE_NAMES))

    def test_the_public_name_moves_only_after_the_primary_passes_its_checks(self):
        self.run_back()
        start = self.index("instances start primary-cp primary-worker")
        fixtures = self.index(f"check-fixtures CLUSTER=primary GIT_HOST=git-primary.sindrg.com")
        cutover = self.index("DNS_NAME=git DNS_ADDRESS=" + PRIMARY_ADDRESS)
        self.assertLess(start, fixtures)
        self.assertLess(fixtures, cutover)

    def test_recovery_backups_stop_before_the_fence_lifts(self):
        self.run_back()
        self.assertLess(self.index("kubectl delete configmap/run-settings"),
                        self.index("service-accounts enable " + WORKER))

    def test_git_dr_is_deleted_before_the_recovery_root(self):
        self.run_back()
        self.assertLess(self.index("dns-delete DNS_NAME=git-dr"), self.index("plan -destroy"))
        self.assertLess(self.index("plan -destroy"), self.index("infra/recovery apply"))

    def test_the_run_needs_the_discard_flag(self):
        self.assertEqual(self.back("run"), 1)
        self.assertEqual(self.system.calls, [])

    def test_stops_at_the_first_failure_and_resumes_there(self):
        self.system.override("write-check", module.recover.Result(2, "", ""), module.recover.Result(0, "", ""))
        self.assertEqual(self.run_back(), 1)
        self.assertEqual(self.state()["passed"], ["start-primary"])
        self.assertFalse(self.system.ran("service-accounts enable"))
        self.system.calls.clear()
        self.assertEqual(self.run_back(), 0)
        self.assertFalse(self.system.ran("instances start"))

    def test_a_rerun_after_the_destroy_needs_no_recovery_environment(self):
        self.system.primary_sets = ["20261016T115201Z"]
        self.assertEqual(self.run_back(), 1)
        self.assertEqual(self.state()["passed"][-1], "remove-recovery")
        self.system.primary_sets.append("20261016T130701Z")
        self.assertEqual(self.run_back(), 0)

    def test_after_a_rehearsal_the_return_skips_the_run_settings(self):
        rehearsal = dict(RECOVERED, passed=RECOVERED["passed"][:6])
        (self.drill / module.recover.STATE_FILE).write_text(json.dumps(rehearsal))
        self.system.dns["git.sindrg.com"] = PRIMARY_ADDRESS
        self.system.suspend = "true"
        self.assertEqual(self.run_back(), 0)
        self.assertFalse(self.system.ran("kubectl delete"))

    def test_status_lists_passed_and_pending_stages(self):
        self.system.override("write-check", module.recover.Result(2, "", ""))
        self.run_back()
        with mock.patch("builtins.print") as printed:
            self.assertEqual(module.main(["--drill-dir", str(self.drill), "status"], system=self.system), 0)
        lines = [call.args[0] for call in printed.call_args_list]
        self.assertIn("passed   start-primary", lines)
        self.assertIn("pending  dns-return", lines)


class GuardTests(ReturnTestCase):
    def test_the_public_checks_wait_until_this_machine_resolves_the_primary(self):
        self.system.local_lag = [RECOVERY_ADDRESS] * 3
        self.assertEqual(self.run_back(), 0)
        self.assertEqual(self.system.local_lag, [])

    def test_a_stale_local_answer_fails_before_the_public_checks(self):
        self.system.local_lag = [RECOVERY_ADDRESS] * 1000
        self.assertEqual(self.run_back(), 1)
        self.assertFalse(self.system.ran("check-fixtures CLUSTER=primary GIT_HOST=git.sindrg.com"))
        self.assertIn("this machine does not resolve", self.events()[-1][2])

    def test_a_return_state_of_another_recovery_starts_nothing(self):
        self.system.override("write-check", module.recover.Result(2, "", ""))
        self.run_back()
        later = dict(RECOVERED, started="2026-10-18T09:00:00Z")
        (self.drill / module.recover.STATE_FILE).write_text(json.dumps(later))
        self.system.overrides.clear()
        self.system.calls.clear()
        self.assertEqual(self.run_back(), 1)
        self.assertEqual(self.system.calls, [])

    def test_no_recovery_state_starts_nothing(self):
        (self.drill / module.recover.STATE_FILE).unlink()
        self.assertEqual(self.run_back(), 1)
        self.assertFalse(self.system.ran("instances start"))
        self.assertIn("nothing to return from", self.events()[-1][2])

    def test_a_removed_recovery_environment_starts_nothing(self):
        self.system.destroyed = True
        self.assertEqual(self.run_back(), 1)
        self.assertFalse(self.system.ran("instances start"))
        self.assertIn("no longer exists", self.events()[-1][2])

    def test_a_primary_that_does_not_answer_keeps_the_public_name(self):
        self.system.primary_status = [None]
        self.assertEqual(self.run_back(), 1)
        self.assertEqual(self.system.dns["git.sindrg.com"], RECOVERY_ADDRESS)
        self.assertIn("does not return 200", self.events()[-1][2])

    def test_failed_primary_fixtures_keep_the_public_name(self):
        self.system.override("check-fixtures CLUSTER=primary GIT_HOST=git-primary", module.recover.Result(2, "", ""))
        self.assertEqual(self.run_back(), 1)
        self.assertEqual(self.system.dns["git.sindrg.com"], RECOVERY_ADDRESS)

    def test_a_cronjob_that_stays_active_keeps_the_fence(self):
        self.system.override("kubectl delete configmap/run-settings", module.recover.Result(0, "", ""))
        self.assertEqual(self.run_back(), 1)
        self.assertFalse(self.system.ran("service-accounts enable"))
        self.assertIn("is not suspended", self.events()[-1][2])

    def test_an_account_that_stays_disabled_keeps_the_recovery_environment(self):
        self.system.override("service-accounts describe", module.recover.Result(0, "True\n", ""))
        self.assertEqual(self.run_back(), 1)
        self.assertFalse(self.system.ran("plan -destroy"))

    def test_the_fence_lift_waits_for_the_account_to_read_enabled(self):
        # Return run 39056248685 read disabled=True right after a successful enable.
        self.system.override("service-accounts describe", module.recover.Result(0, "True\n", ""),
                             module.recover.Result(0, "False\n", ""))
        self.assertEqual(self.run_back(), 0)
        self.assertTrue(self.system.ran("plan -destroy"))

    def test_a_destroy_plan_that_changes_anything_else_stops_before_apply(self):
        for actions, kind in ((["create"], "google_compute_instance"),
                              (["delete", "create"], "google_compute_instance"),
                              (["delete"], "google_service_account"),
                              (["delete"], "google_project_iam_member")):
            with self.subTest(actions=actions, kind=kind):
                plan = {"resource_changes": [{"address": "x", "type": kind, "change": {"actions": actions}}]}
                with self.assertRaises(module.recover.StageFailed):
                    module.check_destroy_plan(plan)
        self.assertEqual(module.check_destroy_plan(DESTROY_PLAN), "plan destroys 2")

    def test_an_unsafe_destroy_plan_is_not_applied(self):
        self.system.destroy_plan = {"resource_changes": [
            {"address": "google_service_account.node", "type": "google_service_account",
             "change": {"actions": ["delete"]}}]}
        self.assertEqual(self.run_back(), 1)
        self.assertFalse(self.system.ran("infra/recovery apply"))

    def test_resources_left_after_the_destroy_fail_the_stage(self):
        self.system.override("infra/recovery state list", module.recover.Result(0, "google_compute_address.web\n", ""))
        self.assertEqual(self.run_back(), 1)
        self.assertEqual(self.state()["passed"][-1], "lift-fence")

    def test_no_new_primary_set_fails_the_last_stage(self):
        self.system.primary_sets = ["20261016T115201Z"]
        self.assertEqual(self.run_back(), 1)
        self.assertIn("no primary set started after", self.events()[-1][2])
        self.assertTrue((self.drill / module.STATE_FILE).exists())

    def test_a_recovery_run_holding_the_lock_blocks_the_return(self):
        import fcntl
        with (self.drill / module.recover.LOCK_FILE).open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertEqual(self.run_back(), 1)
        self.assertEqual(self.system.calls, [])


class WorkflowTests(unittest.TestCase):
    def test_the_job_runs_only_with_the_discard_input(self):
        import yaml
        workflow = yaml.safe_load(Path(".github/workflows/return.yml").read_text())
        job = workflow["jobs"]["return"]
        self.assertEqual(job["if"], "inputs.discard_recovery_writes")
        self.assertIs(workflow["on"]["workflow_dispatch"]["inputs"]["discard_recovery_writes"]["default"], False)
        self.assertEqual(job["environment"], "recovery")

    def test_return_and_recover_share_one_concurrency_group(self):
        import yaml
        groups = {yaml.safe_load(Path(f".github/workflows/{name}.yml").read_text())["concurrency"]["group"]
                  for name in ("recover", "return")}
        self.assertEqual(groups, {"recovery"})

    def test_state_is_pushed_only_after_a_successful_pull(self):
        import yaml
        for name in ("recover", "return"):
            with self.subTest(workflow=name):
                steps = yaml.safe_load(Path(f".github/workflows/{name}.yml").read_text())["jobs"]
                steps = next(iter(steps.values()))["steps"]
                push = next(step for step in steps if "pipeline.py push" in step.get("run", ""))
                self.assertEqual(push["if"], "always() && steps.pull.outcome == 'success'")
                self.assertTrue(any(step.get("id") == "pull" for step in steps))


if __name__ == "__main__":
    unittest.main()
