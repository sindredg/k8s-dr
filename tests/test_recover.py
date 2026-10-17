from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import drill_report
from scripts import recover as module

RECOVERY_ADDRESS = "203.0.113.10"
SET = "20261016T202201Z"
HEAD = "a" * 40
INVENTORY = {"all": {"children": {"kube_control_plane": {"hosts": {"control-plane": {
    "gcp_instance_name": "recovery-cp", "gcp_zone": "europe-west1-b", "gcp_project": "example",
}}}}}}
PLAN = {"resource_changes": [
    {"address": "module.recovery_cluster.google_compute_instance.node[\"worker\"]",
     "type": "google_compute_instance", "change": {"actions": ["create"]}},
]}


class FakeSystem(module.System):
    """Answers every command of a recovery that succeeds, unless told otherwise."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.envs: list[dict | None] = []
        self.clock = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
        # Substring of the joined argv -> list of results, used in order.
        self.overrides: dict[str, list[module.Result]] = {}
        self.plan = PLAN
        self.suspend = "true"
        self.primary_status: list[int | None] = [None]
        self.public_status = 200
        self.dns: dict[str, str] = {}

    def override(self, needle: str, *results: module.Result) -> None:
        self.overrides[needle] = list(results)

    def ran(self, needle: str) -> bool:
        return any(needle in " ".join(argv) for argv in self.calls)

    def call(self, argv, *, capture=False, echo=True, env=None):
        self.calls.append(list(argv))
        self.envs.append(env)
        line = " ".join(argv)
        for needle, results in self.overrides.items():
            if needle in line and results:
                return results.pop(0) if len(results) > 1 else results[0]
        return self.answer(argv, line)

    def answer(self, argv, line):
        ok = lambda out="": module.Result(0, out, "")  # noqa: E731
        if line.startswith("terraform -chdir=infra/recovery show -json"):
            return ok(json.dumps(self.plan))
        if "output -raw public_web_address" in line:
            return ok(RECOVERY_ADDRESS)
        if "output -raw backup_bucket_name" in line:
            return ok("example-backups")
        if "output -raw zone" in line:
            return ok("europe-north1-a")
        if "output -raw project_id" in line:
            return ok("example")
        if "output -json instance_names" in line:
            return ok(json.dumps({"control-plane": "primary-cp", "worker": "primary-worker"}))
        if "output -json node_service_accounts" in line:
            return ok(json.dumps({"primary/worker": "primary-worker@example.iam.gserviceaccount.com"}))
        if argv[:2] == ["make", "--no-print-directory"] and argv[2] == "dns-set":
            name = argv[3].split("=", 1)[1]
            self.dns[f"{name}.sindrg.com"] = argv[4].split("=", 1)[1]
            return ok()
        if argv[0] == "dig":
            return ok(self.dns.get(argv[2], "") + "\n")
        if "kubectl get cronjob/gitea-backup" in line:
            return ok(self.suspend + "\n")
        if line.startswith("gcloud storage ls gs://example-backups/recovery/"):
            return module.Result(1, "", "ERROR: (gcloud.storage.ls) One or more URLs matched no objects.\n")
        if line.startswith("gcloud storage ls gs://example-backups/primary/"):
            return ok("".join(f"gs://example-backups/primary/{name}/manifest.json\n"
                              for name in ("20261016T193701Z", SET, "20261016T200701Z")))
        if "service-accounts describe" in line:
            return ok("True\n")
        if "check-fixtures" in line:
            return ok(f'ok: [localhost] => {{"msg": ["Newest commit on main: {HEAD} write check"]}}\n')
        return ok()

    def http_status(self, url):
        if "git-primary" in url:
            return self.primary_status.pop(0) if len(self.primary_status) > 1 else self.primary_status[0]
        return self.public_status

    def sleep(self, seconds):
        self.clock += timedelta(seconds=seconds)

    def now(self):
        self.clock += timedelta(seconds=1)
        return self.clock


class RecoverTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.drill = Path(directory.name) / ".drill"
        self.inventory = Path(directory.name) / "hosts.json"
        self.inventory.write_text(json.dumps(INVENTORY))
        patcher = mock.patch.object(module, "INVENTORY", str(self.inventory))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.system = FakeSystem()

    def recover(self, *args):
        with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            return module.main(["--drill-dir", str(self.drill), *args], system=self.system)

    def state(self):
        return json.loads((self.drill / module.STATE_FILE).read_text())

    def events(self):
        return [(stage, event, note)
                for _, stage, event, note in drill_report.read_events(self.drill / module.TIMELINE_FILE)]


class RunTests(RecoverTestCase):
    def test_runs_every_stage_in_runbook_order(self):
        self.assertEqual(self.recover("run"), 0)
        self.assertEqual(self.state()["passed"], list(module.STAGE_NAMES))
        ends = [stage for stage, event, _ in self.events() if event == "end"]
        self.assertEqual(ends, list(module.STAGE_NAMES))
        self.assertEqual(self.state()["values"]["backup_set"], SET)
        self.assertEqual(self.state()["values"]["restored_head"], HEAD)
        # The fence comes before the public record moves.
        commands = [" ".join(argv) for argv in self.system.calls]
        disable = next(i for i, line in enumerate(commands) if "service-accounts disable" in line)
        cutover = next(i for i, line in enumerate(commands) if "DNS_NAME=git " in line + " ")
        self.assertLess(disable, cutover)
        self.assertIn("make --no-print-directory restore CLUSTER=recovery RESTORE_NAMESPACE=gitea "
                      f"RESTORE_SET={SET}", commands)

    def test_every_stage_is_a_stage_of_the_drill_report(self):
        self.assertLessEqual(set(module.STAGE_NAMES), set(drill_report.STAGES))
        order = [drill_report.STAGES.index(name) for name in module.STAGE_NAMES]
        self.assertEqual(order, sorted(order))

    def test_preflight_skips_only_the_fixture_check(self):
        self.recover("run", "--through", "preflight")
        index = self.system.calls.index(["make", "--no-print-directory", "preflight"])
        self.assertEqual(self.system.envs[index], {"PREFLIGHT_SKIP": "fixtures"})

    def test_stops_at_the_first_failure_and_resumes_there(self):
        failed = module.Result(2, "", "")
        self.system.override("make --no-print-directory restore", failed, module.Result(0, "", ""))
        self.assertEqual(self.recover("run"), 1)
        self.assertEqual(self.state()["passed"], ["preflight", "provisioning", "bootstrap", "flux-reconcile"])
        self.assertFalse(self.system.ran("check-fixtures"))
        self.assertIn(("restore", "note", "failed: make --no-print-directory restore exited 2"), self.events())

        self.system.calls.clear()
        self.assertEqual(self.recover("run"), 0)
        # A rerun initializes the roots and rebuilds the inventory, then
        # starts at the failed stage.
        commands = [" ".join(argv) for argv in self.system.calls]
        self.assertEqual(commands[:6], [
            *(f"terraform -chdir=infra/{root} init -input=false -backend-config=backend.hcl"
              for root in ("shared", "primary", "recovery")),
            "terraform -chdir=infra/recovery output -raw public_web_address",
            "make --no-print-directory inventory CLUSTER=recovery",
            "make --no-print-directory restore CLUSTER=recovery RESTORE_NAMESPACE=gitea "
            f"RESTORE_SET={SET}",
        ])
        self.assertFalse(self.system.ran("terraform -chdir=infra/recovery apply"))
        self.assertFalse(self.system.ran("make --no-print-directory preflight"))
        self.assertEqual(self.state()["passed"], list(module.STAGE_NAMES))

    def test_a_resumed_run_stops_when_its_environment_is_gone(self):
        self.recover("run", "--through", "verification")
        # The rehearsal environment is removed; its outputs are gone.
        self.system.override("output -raw public_web_address", module.Result(1, "", "No outputs found"))
        self.system.calls.clear()
        self.assertEqual(self.recover("run"), 1)
        self.assertFalse(self.system.ran("instances stop"))
        self.assertFalse(self.system.ran("service-accounts disable"))
        self.assertNotIn("git.sindrg.com", self.system.dns)
        self.assertEqual(self.events()[-1][:2], ("fence", "note"))
        self.assertIn("no longer exists", self.events()[-1][2])

    def test_a_resumed_run_stops_when_the_address_changed(self):
        self.recover("run", "--through", "verification")
        self.system.override("output -raw public_web_address", module.Result(0, "198.51.100.7", ""))
        self.assertEqual(self.recover("run"), 1)
        self.assertFalse(self.system.ran("service-accounts disable"))

    def test_a_missing_tool_is_a_recorded_failure(self):
        real = self.system.call
        def call(argv, **kwargs):
            if argv[0] == "terraform":
                raise FileNotFoundError("terraform")
            return real(argv, **kwargs)
        self.system.call = call
        self.assertEqual(self.recover("run"), 1)
        self.assertEqual(self.events()[-1][:2], ("preflight", "note"))

    def test_a_failed_init_stops_before_any_stage(self):
        self.system.override("infra/primary init", module.Result(1, "", ""))
        self.assertEqual(self.recover("run"), 1)
        self.assertFalse(self.system.ran("make --no-print-directory preflight"))
        self.assertEqual(self.state()["passed"], [])

    def test_a_retried_restore_reads_the_same_set(self):
        self.system.override("make --no-print-directory restore", module.Result(2, "", ""))
        self.recover("run")
        self.system.overrides.clear()
        newer = "gs://example-backups/primary/20261016T210701Z/manifest.json\n"
        self.system.override("gcloud storage ls gs://example-backups/primary/", module.Result(0, newer, ""))
        self.recover("run")
        self.assertEqual(self.state()["values"]["backup_set"], SET)

    def test_a_retried_verification_keeps_the_restored_head(self):
        self.system.override("write-check", module.Result(2, "", ""))
        self.assertEqual(self.recover("run"), 1)
        self.system.overrides.clear()
        later = f'"Newest commit on main: {"b" * 40} recovery write"'
        self.system.override("check-fixtures", module.Result(0, later, ""))
        self.assertEqual(self.recover("run"), 0)
        self.assertEqual(self.state()["values"]["restored_head"], HEAD)

    def test_through_stops_before_the_fence(self):
        self.assertEqual(self.recover("run", "--through", "verification"), 0)
        self.assertFalse(self.system.ran("instances stop"))
        self.assertFalse(self.system.ran("service-accounts disable"))
        self.assertNotIn("git.sindrg.com", self.system.dns)
        self.assertEqual(self.state()["passed"][-1], "verification")

    def test_reset_archives_the_state_and_timeline(self):
        self.recover("run", "--through", "preflight")
        self.assertEqual(self.recover("reset"), 0)
        self.assertFalse((self.drill / module.STATE_FILE).exists())
        self.assertFalse((self.drill / module.TIMELINE_FILE).exists())
        self.assertEqual(len(list(self.drill.glob("recover-*.json"))), 1)
        self.assertEqual(len(list(self.drill.glob("timeline-*.tsv"))), 1)

    def test_a_second_run_waits_for_no_lock(self):
        import fcntl
        self.drill.mkdir()
        with (self.drill / module.LOCK_FILE).open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertEqual(self.recover("run"), 1)
        self.assertEqual(self.system.calls, [])


class CheckTests(RecoverTestCase):
    def test_a_plan_that_deletes_stops_before_apply(self):
        self.system.plan = {"resource_changes": [
            {"address": "google_compute_network.recovery", "type": "google_compute_network",
             "change": {"actions": ["delete", "create"]}},
        ]}
        self.assertEqual(self.recover("run"), 1)
        self.assertFalse(self.system.ran("apply"))
        self.assertNotIn("provisioning", self.state()["passed"])

    def test_a_plan_that_grants_stops_before_apply(self):
        self.system.plan = {"resource_changes": [
            {"address": "google_storage_bucket_iam_member.x", "type": "google_storage_bucket_iam_member",
             "change": {"actions": ["create"]}},
        ]}
        self.assertEqual(self.recover("run"), 1)
        self.assertFalse(self.system.ran("apply"))

    def test_an_active_backup_schedule_stops_the_run(self):
        self.system.suspend = "false"
        self.assertEqual(self.recover("run"), 1)
        self.assertNotIn("flux-reconcile", self.state()["passed"])
        self.assertFalse(self.system.ran("make --no-print-directory restore"))

    def test_a_failed_bucket_listing_is_not_an_empty_prefix(self):
        denied = module.Result(1, "", "ERROR: (gcloud.storage.ls) [user] does not have permission\n")
        self.system.override("gcloud storage ls gs://example-backups/recovery/", denied)
        self.assertEqual(self.recover("run"), 1)
        self.assertNotIn("flux-reconcile", self.state()["passed"])

    def test_a_missing_restored_head_stops_the_run(self):
        self.system.override("check-fixtures", module.Result(0, "no commit line", ""))
        self.assertEqual(self.recover("run"), 1)
        self.assertNotIn("verification", self.state()["passed"])
        self.assertFalse(self.system.ran("write-check"))

    def test_an_unanswered_record_times_out(self):
        self.system.override("dig +short git-dr.sindrg.com", module.Result(0, "198.51.100.1\n", ""))
        self.assertEqual(self.recover("run"), 1)
        self.assertNotIn("provisioning", self.state()["passed"])
        self.assertFalse(self.system.ran("make --no-print-directory bootstrap"))


class FenceTests(RecoverTestCase):
    def test_a_failed_stop_is_noted_and_the_run_continues(self):
        self.system.override("instances stop", module.Result(1, "", ""))
        self.assertEqual(self.recover("run"), 0)
        self.assertIn(("fence", "note", "stopping the primary VMs exited 1"), self.events())

    def test_a_primary_that_still_answers_blocks_the_cutover(self):
        self.system.primary_status = [200]
        self.assertEqual(self.recover("run"), 1)
        self.assertNotIn("fence", self.state()["passed"])
        self.assertNotIn("git.sindrg.com", self.system.dns)

    def test_the_fence_waits_for_the_primary_to_stop_answering(self):
        self.system.primary_status = [200, 200, None]
        self.assertEqual(self.recover("run"), 0)

    def test_an_account_that_stays_enabled_blocks_the_cutover(self):
        self.system.override("service-accounts describe", module.Result(0, "\n", ""))
        self.assertEqual(self.recover("run"), 1)
        self.assertNotIn("git.sindrg.com", self.system.dns)


class StatusTests(RecoverTestCase):
    def test_status_lists_passed_and_pending_stages(self):
        self.recover("run", "--through", "bootstrap")
        with mock.patch("builtins.print") as printed:
            module.main(["--drill-dir", str(self.drill), "status"], system=self.system)
        lines = [call.args[0] for call in printed.call_args_list]
        self.assertIn("passed   bootstrap", lines)
        self.assertIn("pending  flux-reconcile", lines)


if __name__ == "__main__":
    unittest.main()
