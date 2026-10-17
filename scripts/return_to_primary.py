#!/usr/bin/env python3
"""Return to the primary after a drill and remove the recovery environment.

Each stage runs the commands of "Return to the primary after a drill" and
"Remove the recovery environment" in docs/runbooks/regional-recovery.md, and
ends with the check that the runbook states. The first failed command or
check stops the run, and a rerun resumes at the failed stage, as in
scripts/recover.py.

The primary still holds its data as of the isolation. Every write that the
recovery cluster accepted is discarded, so the run requires
--discard-recovery-writes. It is not a failback after a real loss.

The run reads the recovery run state from .drill/ and keeps its own beside it:

  return.json  the stages that passed and the values recorded so far
  return.tsv   the stage timeline

After the last stage, the run archives both files and the recovery run state,
so the next recovery starts at preflight. See decision 0013.
"""

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sys
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import drill_report  # noqa: E402
import recover  # noqa: E402
from recover import StageFailed, System  # noqa: E402

STATE_FILE = recover.RETURN_STATE_FILE
TIMELINE_FILE = recover.RETURN_TIMELINE_FILE
DESTROY_PLAN = "recovery-destroy.tfplan"
# Drill 1 saw the primary answer about three minutes after the start.
START_TIMEOUT = 600
# The primary backup schedule in deploy/apps/gitea/backup.yaml runs every 15
# minutes; a set takes about a minute to upload.
BACKUP_TIMEOUT = 1200


def check_destroy_plan(plan: dict) -> str:
    """Accept a plan that only deletes, and never an account or IAM grant."""
    problems, deleted = [], 0
    for change in plan.get("resource_changes", []):
        actions = change["change"]["actions"]
        if actions == ["no-op"]:
            continue
        if actions != ["delete"]:
            problems.append(f"{change['address']} would be {'/'.join(actions)}")
        elif recover.FORBIDDEN_TYPES.search(change["type"]):
            problems.append(f"{change['address']} is an account or IAM grant")
        else:
            deleted += 1
    if problems:
        raise StageFailed("the destroy plan is not safe to apply: " + "; ".join(problems))
    return f"plan destroys {deleted}"


class Return(recover.Recovery):
    def __init__(self, system: System, drill_dir: Path):
        super().__init__(system, drill_dir)
        # The parent loaded recover.json; the return reads it and keeps its own.
        self.recovered = self.state
        self.state_path = drill_dir / STATE_FILE
        self.state = {"started": None, "passed": [], "values": {}}
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())

    def event(self, stage: str, event: str, note: str = "") -> None:
        drill_report.append_event(
            self.drill_dir / TIMELINE_FILE, stage, event, note, self.system.now()
        )

    def require_recorded_environment(self) -> None:
        """The recovery root must still report the address that the recovery recorded."""
        recorded = self.recovered["values"].get("recovery_address")
        if "provisioning" not in self.recovered["passed"] or not recorded:
            raise StageFailed("no recovery run state with a recovery environment; nothing to return from")
        try:
            address = self.output("recovery", "public_web_address")
        except StageFailed:
            address = ""
        if address != recorded:
            raise StageFailed("the recovery environment that the recovery recorded no longer exists")

    def newest_primary_set(self) -> str:
        listing = self.run(["gcloud", "storage", "ls",
                            f"gs://{self.bucket()}/{recover.BACKUP_SOURCE}/*/manifest.json"],
                           capture=True, echo=False)
        names = sorted(line.split("/")[-2] for line in listing.splitlines()
                       if line.endswith("/manifest.json"))
        return names[-1] if names else ""

    # Stages

    def start_primary(self) -> None:
        """Runbook step 1: start the primary and check it through its own name."""
        names = sorted(self.output_json("primary", "instance_names").values())
        self.run(["gcloud", "compute", "instances", "start", *names,
                  "--zone", self.output("primary", "zone"),
                  "--project", self.output("primary", "project_id")])
        self.wait(f"https://{self.primary_host}/api/healthz does not return 200",
                  lambda: self.system.http_status(f"https://{self.primary_host}/api/healthz") == 200,
                  START_TIMEOUT)
        self.make("check-fixtures", "CLUSTER=primary", f"GIT_HOST={self.primary_host}")

    def dns_return(self) -> None:
        """Runbook step 2: point the public name back at the primary."""
        address = self.output("primary", "public_web_address")
        self.make("dns-set", f"DNS_NAME={self.public_host.split('.')[0]}", f"DNS_ADDRESS={address}")
        self.wait(f"{self.public_host} does not resolve to the primary address at {recover.RESOLVER}",
                  lambda: self.resolve(self.public_host) == address, recover.DNS_TIMEOUT)
        # The recovery cluster still serves. The checks below use this
        # machine's resolver, which may still return the recovery address.
        self.wait(f"this machine does not resolve {self.public_host} to the primary address",
                  lambda: self.system.local_addresses(self.public_host) == [address], recover.DNS_TIMEOUT)
        self.wait_for_health(self.public_host)
        self.make("check-fixtures", "CLUSTER=primary", f"GIT_HOST={self.public_host}")
        self.make("write-check", "CLUSTER=primary", f"GIT_HOST={self.public_host}")

    def suspend_recovery_backups(self) -> None:
        """Runbook step 3: delete run-settings, so cluster-settings suspends the CronJob again."""
        if "resume-backups" not in self.recovered["passed"]:
            self.event("suspend-recovery-backups", "note", "the recovery never resumed its backups")
            return
        self.kubectl(["delete", f"configmap/{recover.RUN_SETTINGS}", "--namespace", "flux-system",
                      "--ignore-not-found"])
        requested = self.system.now().strftime(drill_report.TIME_FORMAT)
        self.kubectl(["annotate", "--overwrite", "--namespace", "flux-system", "kustomization/gitea",
                      f"reconcile.fluxcd.io/requestedAt={requested}"])
        self.wait("the recovery backup CronJob is not suspended",
                  lambda: self.backup_suspend() == "true", recover.RESUME_TIMEOUT)

    def lift_fence(self) -> None:
        """Runbook step 4: enable the primary worker account again."""
        worker = self.output_json("shared", "node_service_accounts")["primary/worker"]
        self.run(["gcloud", "iam", "service-accounts", "enable", worker])
        self.wait("the primary worker account still reports disabled=True",
                  lambda: self.account_disabled(worker) in ("", "False"), recover.ACCOUNT_TIMEOUT)
        # The next primary set must start after this point.
        self.values["fence_lifted"] = self.system.now().strftime(drill_report.SET_FORMAT)
        self.save()

    def remove_recovery(self) -> None:
        """Runbook steps 5 and "Remove the recovery environment": git-dr, then the recovery root."""
        self.make("dns-delete", f"DNS_NAME={self.recovery_host.split('.')[0]}")
        self.wait(f"{self.recovery_host} still resolves at {recover.RESOLVER}",
                  lambda: self.resolve(self.recovery_host) == "", recover.DNS_TIMEOUT)
        self.run(["terraform", "-chdir=infra/recovery", "plan", "-destroy", "-input=false",
                  f"-out={DESTROY_PLAN}"])
        plan = json.loads(self.run(["terraform", "-chdir=infra/recovery", "show", "-json", DESTROY_PLAN],
                                   capture=True, echo=False))
        self.event("remove-recovery", "note", check_destroy_plan(plan))
        self.run(["terraform", "-chdir=infra/recovery", "apply", "-input=false", DESTROY_PLAN])
        left = self.run(["terraform", "-chdir=infra/recovery", "state", "list"],
                        capture=True, echo=False).split()
        if left:
            raise StageFailed(f"infra/recovery still holds {len(left)} resources")

    def primary_backup(self) -> None:
        """The primary writes a set again, so the fence is lifted end to end."""
        lifted = self.values["fence_lifted"]
        self.wait(f"no primary set started after {lifted}",
                  lambda: self.newest_primary_set() >= lifted, BACKUP_TIMEOUT)
        self.event("primary-backup", "note", f"set {self.newest_primary_set()}")


# The order of the runbook.
STAGES: tuple[tuple[str, Callable[[Return], None]], ...] = (
    ("start-primary", Return.start_primary),
    ("dns-return", Return.dns_return),
    ("suspend-recovery-backups", Return.suspend_recovery_backups),
    ("lift-fence", Return.lift_fence),
    ("remove-recovery", Return.remove_recovery),
    ("primary-backup", Return.primary_backup),
)
STAGE_NAMES = tuple(name for name, _ in STAGES)
# Stages that reach the recovery environment, which a rerun must still find.
NEEDS_RECOVERY = ("start-primary", "dns-return", "suspend-recovery-backups", "lift-fence")


def run(returning: Return) -> int:
    if returning.state["started"] is None:
        returning.state["started"] = returning.system.now().strftime(drill_report.TIME_FORMAT)
        returning.save()
    pending = [name for name in STAGE_NAMES if name not in returning.state["passed"]]
    # A return resumes only against the recovery run it started with.
    if returning.recovered["started"] is not None:
        returning.values.setdefault("recovery_started", returning.recovered["started"])
        returning.save()
    recovery_started = returning.values.get("recovery_started")
    if recovery_started is not None and recovery_started != returning.recovered["started"]:
        print(f"error: the return state belongs to the recovery started at {recovery_started}, "
              f"not {returning.recovered['started']}; archive it with scripts/recover.py reset",
              file=sys.stderr)
        return 1
    try:
        returning.init_roots()
        if any(name in NEEDS_RECOVERY for name in pending):
            returning.require_recorded_environment()
            returning.inventory()
    except (StageFailed, OSError, KeyError) as error:
        if pending:
            returning.event(pending[0], "note", f"failed before the stage: {error}")
        print(f"error: {error}", file=sys.stderr)
        return 1
    for name, stage in STAGES:
        if name in returning.state["passed"]:
            print(f"== {name}: passed earlier, skipped", flush=True)
            continue
        print(f"== {name}", flush=True)
        returning.event(name, "start")
        try:
            stage(returning)
        except (StageFailed, OSError, ValueError, KeyError) as error:
            returning.save()
            returning.event(name, "note", f"failed: {error}")
            print(f"error: {name} failed: {error}", file=sys.stderr)
            print("Fix the cause and run the command again; it resumes at this stage.", file=sys.stderr)
            return 1
        returning.state["passed"].append(name)
        returning.save()
        returning.event(name, "end")
    table = drill_report.timeline_table(drill_report.read_events(returning.drill_dir / TIMELINE_FILE))
    print("Return complete. The primary serves and backs up, and the recovery environment is removed.")
    print(table)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as output:
            output.write(table + "\n")
    stamp = returning.system.now().strftime(drill_report.SET_FORMAT)
    recover.archive(returning.drill_dir,
                    (recover.STATE_FILE, recover.TIMELINE_FILE, STATE_FILE, TIMELINE_FILE), stamp)
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--drill-dir", type=Path, default=recover.DRILL_DIR)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("run", help="Run the stages that have not passed, in order.")
    start.add_argument("--discard-recovery-writes", action="store_true",
                       help="Required: every write that the recovery cluster accepted is lost.")
    commands.add_parser("status", help="List each stage and the values recorded so far.")
    return parser.parse_args(argv)


def main(argv=None, system: System | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run" and not args.discard_recovery_writes:
        print("error: the return discards every write that the recovery cluster accepted; "
              "pass --discard-recovery-writes after a drill", file=sys.stderr)
        return 1
    args.drill_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    # The recovery's lock: a return and a recovery never run at once on one machine.
    with (args.drill_dir / recover.LOCK_FILE).open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("error: another recovery or return run holds the lock", file=sys.stderr)
            return 1
        returning = Return(system or System(), args.drill_dir)
        if args.command == "status":
            for name in STAGE_NAMES:
                print(f"{'passed ' if name in returning.state['passed'] else 'pending'}  {name}")
            for key, value in returning.values.items():
                print(f"{key}: {value}")
            return 0
        return run(returning)


if __name__ == "__main__":
    raise SystemExit(main())
