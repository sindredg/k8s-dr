#!/usr/bin/env python3
"""Run every stage of the regional recovery and resume from a failed one.

Each stage runs the commands of docs/runbooks/regional-recovery.md and ends
with the check that the runbook states. The first failed command or check
stops the run. A rerun skips the stages that passed and starts again at the
failed one, with the backup set, the recovery address, and the restored HEAD
that the first attempt recorded.

The run keeps two files under .drill/ on the operator machine, outside Git:

  recover.json  the stages that passed and the values recorded so far
  timeline.tsv  the stage timeline that scripts/drill_report.py reads

See decision 0013.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import time
from typing import Callable
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import drill_report  # noqa: E402

DRILL_DIR = Path(".drill")
STATE_FILE = "recover.json"
LOCK_FILE = "recover.lock"
TIMELINE_FILE = "timeline.tsv"
# Written by scripts/return_to_primary.py. A reset archives them too, so a
# later return cannot resume against another recovery environment.
RETURN_STATE_FILE = "return.json"
RETURN_TIMELINE_FILE = "return.tsv"
SETTINGS = "deploy/clusters/{}/cluster-settings.yaml"
INVENTORY = "ansible/inventory/generated/recovery/hosts.json"
PLAN_FILE = "recovery.tfplan"
# The prefix the restore reads. See decision 0007.
BACKUP_SOURCE = "primary"
RESOLVER = "1.1.1.1"
# The records are written with a 60-second TTL, so a public resolver answers
# with the new address within about a minute.
DNS_TIMEOUT = 180
# Drill 1 saw the endpoint answer within a minute of the restore.
HEALTH_TIMEOUT = 300
# A stopped primary stops answering within the stop call; a primary that
# cannot be reached never answers.
FENCE_TIMEOUT = 300
# IAM reads can return the old value of an account for a while after a
# change: Return run 39056248685 read disabled=True right after an enable.
ACCOUNT_TIMEOUT = 120
# The gitea Kustomization timeout in deploy/sync/sync.yaml.
RESUME_TIMEOUT = 600
# Created by the resume-backups stage, read by the gitea Kustomization after
# cluster-settings. See deploy/sync/sync.yaml.
RUN_SETTINGS = "run-settings"
POLL_INTERVAL = 5
# A recovery plan creates resources only. It never deletes one, and the node
# accounts and their grants live in infra/shared.
FORBIDDEN_TYPES = re.compile(r"^google_service_account|_iam_(member|binding|policy)$")


class StageFailed(Exception):
    """A command or check failed; the run stops before the next stage."""


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str


class System:
    """The commands, requests, and clock a recovery uses. Tests replace it."""

    def call(self, argv: list[str], *, capture: bool = False, echo: bool = True,
             env: dict | None = None) -> Result:
        print(f"$ {shlex.join(argv)}", flush=True)
        completed = subprocess.run(
            argv, text=True, env={**os.environ, **(env or {})},
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
        if capture and echo:
            # kubectl jsonpath output has no final newline.
            if completed.stdout:
                print(completed.stdout.rstrip("\n"))
            sys.stderr.write(completed.stderr)
        return Result(completed.returncode, completed.stdout or "", completed.stderr or "")

    def http_status(self, url: str) -> int | None:
        """The HTTP status of a GET, or None when nothing answers."""
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code
        except (urllib.error.URLError, OSError):
            return None

    def local_addresses(self, host: str) -> list[str]:
        """The IPv4 addresses this machine's own resolver returns, which the checks use."""
        try:
            infos = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
        except OSError:
            return []
        return sorted({info[4][0] for info in infos})

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


def cluster_setting(cluster: str, key: str) -> str:
    match = re.search(rf"(?m)^  {key}: (\S+)$", Path(SETTINGS.format(cluster)).read_text())
    if not match:
        raise StageFailed(f"no {key} in {SETTINGS.format(cluster)}")
    return match.group(1).strip('"')


def check_plan(plan: dict) -> str:
    """Refuse a recovery plan that deletes, replaces, or grants anything."""
    problems, counts = [], {"create": 0, "update": 0}
    for change in plan.get("resource_changes", []):
        actions = change["change"]["actions"]
        if "delete" in actions:
            problems.append(f"{change['address']} would be deleted")
        elif FORBIDDEN_TYPES.search(change["type"]) and actions != ["no-op"]:
            problems.append(f"{change['address']} is an account or IAM change")
        for action in counts:
            counts[action] += action in actions
    if problems:
        raise StageFailed("the recovery plan is not safe to apply: " + "; ".join(problems))
    return f"plan creates {counts['create']} and updates {counts['update']}"


class Recovery:
    def __init__(self, system: System, drill_dir: Path):
        self.system = system
        self.drill_dir = drill_dir
        self.state_path = drill_dir / STATE_FILE
        self.state = {"started": None, "passed": [], "values": {}}
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
        self.recovery_host = cluster_setting("recovery", "git_cluster_host")
        self.public_host = cluster_setting("recovery", "git_host")
        self.primary_host = cluster_setting("primary", "git_cluster_host")

    @property
    def values(self) -> dict:
        return self.state["values"]

    def save(self) -> None:
        self.drill_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, indent=2) + "\n")
        temporary.replace(self.state_path)

    def event(self, stage: str, event: str, note: str = "") -> None:
        drill_report.append_event(
            self.drill_dir / TIMELINE_FILE, stage, event, note, self.system.now()
        )

    # Commands

    def run(self, argv: list[str], *, capture: bool = False, echo: bool = True,
            env: dict | None = None) -> str:
        result = self.system.call(argv, capture=capture, echo=echo, env=env)
        if result.returncode != 0:
            raise StageFailed(f"{shlex.join(argv[:3])} exited {result.returncode}")
        return result.stdout

    def make(self, target: str, *variables: str, capture: bool = False) -> str:
        return self.run(["make", "--no-print-directory", target, *variables], capture=capture)

    def output(self, root: str, name: str) -> str:
        value = self.run(["terraform", f"-chdir=infra/{root}", "output", "-raw", name],
                         capture=True, echo=False).strip()
        if not value:
            raise StageFailed(f"infra/{root} has an empty output {name}")
        return value

    def output_json(self, root: str, name: str):
        return json.loads(self.run(["terraform", f"-chdir=infra/{root}", "output", "-json", name],
                                   capture=True, echo=False))

    def wait(self, what: str, condition: Callable[[], bool], timeout: int) -> None:
        deadline = self.system.now().timestamp() + timeout
        while not condition():
            if self.system.now().timestamp() >= deadline:
                raise StageFailed(f"{what} after {timeout} seconds")
            self.system.sleep(POLL_INTERVAL)

    def resolve(self, host: str) -> str:
        answer = self.run(["dig", "+short", host, f"@{RESOLVER}"], capture=True, echo=False)
        lines = answer.split()
        return lines[-1] if lines else ""

    def account_disabled(self, account: str) -> str:
        return self.run(["gcloud", "iam", "service-accounts", "describe", account,
                         "--format=value(disabled)"], capture=True, echo=False).strip()

    def wait_for_record(self, host: str, address: str) -> None:
        self.wait(f"{host} does not resolve to the recovery address at {RESOLVER}",
                  lambda: self.resolve(host) == address, DNS_TIMEOUT)

    def wait_for_health(self, host: str) -> None:
        self.wait(f"https://{host}/api/healthz does not return 200",
                  lambda: self.system.http_status(f"https://{host}/api/healthz") == 200,
                  HEALTH_TIMEOUT)

    def bucket(self) -> str:
        if "bucket" not in self.values:
            self.values["bucket"] = self.output("shared", "backup_bucket_name")
        return self.values["bucket"]

    def kubectl(self, *commands: list[str], capture: bool = True) -> str:
        """Run kubectl on the recovery control plane; several commands form a pipeline."""
        hosts = json.loads(Path(INVENTORY).read_text())
        node = hosts["all"]["children"]["kube_control_plane"]["hosts"]["control-plane"]
        remote = " | ".join(
            shlex.join(["sudo", "KUBECONFIG=/etc/kubernetes/admin.conf", "kubectl", *command])
            for command in commands
        )
        return self.run([
            "gcloud", "compute", "ssh", node["gcp_instance_name"], "--zone", node["gcp_zone"],
            "--project", node["gcp_project"], "--tunnel-through-iap", f"--command={remote}",
        ], capture=capture)

    def backup_suspend(self) -> str:
        return self.kubectl(["get", "cronjob/gitea-backup", "--namespace", "gitea",
                             "--output=jsonpath={.spec.suspend}"]).strip()

    def recovery_sets(self) -> list[str]:
        """The set names under recovery/. An empty prefix is an error from gcloud; only that error passes."""
        listing = self.system.call(["gcloud", "storage", "ls", f"gs://{self.bucket()}/recovery/"],
                                   capture=True)
        if listing.returncode != 0:
            if "matched no objects" in listing.stderr:
                return []
            raise StageFailed("the listing of the recovery prefix failed")
        return [line.rstrip("/").split("/")[-1] for line in listing.stdout.splitlines() if line.strip()]

    def require_backups_suspended(self) -> None:
        """Runbook step 6: the recovery CronJob is suspended and wrote no set during this run.

        Sets from an earlier drill stay under recovery/ for the retention
        period, so only a set named after the start of this run fails.
        """
        suspend = self.backup_suspend()
        if suspend != "true":
            raise StageFailed(f"the recovery backup CronJob has suspend={suspend or '<empty>'}")
        started = datetime.strptime(self.state["started"], drill_report.TIME_FORMAT)
        for name in self.recovery_sets():
            try:
                written = datetime.strptime(name, drill_report.SET_FORMAT)
            except ValueError:
                raise StageFailed(f"recovery/{name} is not a set name") from None
            if written >= started:
                raise StageFailed(f"recovery/{name} was written during this run, before the cutover")

    def init_roots(self) -> None:
        """Every run initializes the roots: a resumed run can start on a new runner."""
        for root in ("shared", "primary", "recovery"):
            self.run(["terraform", f"-chdir=infra/{root}", "init", "-input=false",
                      "-backend-config=backend.hcl"])

    def inventory(self) -> None:
        """Generate the recovery inventory, which a new runner does not have."""
        self.make("inventory", "CLUSTER=recovery")

    def require_recorded_environment(self) -> None:
        """A resumed run must find the recovery environment it recorded."""
        try:
            address = self.output("recovery", "public_web_address")
        except StageFailed:
            address = ""
        if address != self.values.get("recovery_address"):
            raise StageFailed("the recovery environment that this run recorded no longer exists; "
                              "archive the run state with reset and start again")

    # Stages

    def preflight(self) -> None:
        # The fixtures are checked on the recovery cluster after the restore.
        # In a real loss the primary cannot pass them.
        self.run(["make", "--no-print-directory", "preflight"], env={"PREFLIGHT_SKIP": "fixtures"})

    def provisioning(self) -> None:
        self.run(["terraform", "-chdir=infra/recovery", "plan", "-input=false", f"-out={PLAN_FILE}"])
        plan = json.loads(self.run(["terraform", "-chdir=infra/recovery", "show", "-json", PLAN_FILE],
                                   capture=True, echo=False))
        self.event("provisioning", "note", check_plan(plan))
        self.run(["terraform", "-chdir=infra/recovery", "apply", "-input=false", PLAN_FILE])
        address = self.output("recovery", "public_web_address")
        self.values["recovery_address"] = address
        self.save()
        self.make("dns-set", f"DNS_NAME={self.recovery_host.split('.')[0]}", f"DNS_ADDRESS={address}")
        self.wait_for_record(self.recovery_host, address)

    def bootstrap(self) -> None:
        self.inventory()
        self.make("bootstrap", "CLUSTER=recovery")

    def flux_reconcile(self) -> None:
        self.make("validate-cluster", "CLUSTER=recovery")
        self.make("validate-services", "CLUSTER=recovery")
        self.require_backups_suspended()

    def restore(self) -> None:
        # The set is chosen once, so a retried restore reads the same set.
        if "backup_set" not in self.values:
            listing = self.run(["gcloud", "storage", "ls",
                                f"gs://{self.bucket()}/{BACKUP_SOURCE}/*/manifest.json"],
                               capture=True, echo=False)
            manifests = sorted(line for line in listing.splitlines() if line.endswith("/manifest.json"))
            if not manifests:
                raise StageFailed(f"no complete set under {BACKUP_SOURCE}/")
            self.values["backup_set"] = manifests[-1].split("/")[-2]
            self.save()
            self.event("restore", "note", f"set {self.values['backup_set']}")
        self.make("restore", "CLUSTER=recovery", "RESTORE_NAMESPACE=gitea",
                  f"RESTORE_SET={self.values['backup_set']}")

    def verification(self) -> None:
        self.wait_for_health(self.recovery_host)
        report = self.make("check-fixtures", "CLUSTER=recovery", f"GIT_HOST={self.recovery_host}",
                           capture=True)
        # The first check after the restore reads the restored HEAD. A retry
        # after the write check would read the recovery write instead.
        if "restored_head" not in self.values:
            match = re.search(r"Newest commit on \S+: ([0-9a-f]{40})\b", report)
            if not match:
                raise StageFailed("check-fixtures did not report the newest commit")
            self.values["restored_head"] = match.group(1)
            self.save()
            self.event("verification", "note", f"restored HEAD {match.group(1)}")
        self.make("write-check", "CLUSTER=recovery", f"GIT_HOST={self.recovery_host}")
        self.require_backups_suspended()

    def fence(self) -> None:
        """Stop the primary and disable its worker account before the cutover."""
        names = sorted(self.output_json("primary", "instance_names").values())
        zone, project = self.output("primary", "zone"), self.output("primary", "project_id")
        stop = self.system.call(["gcloud", "compute", "instances", "stop", *names,
                                 "--zone", zone, "--project", project])
        if stop.returncode != 0:
            # Expected when the primary region is lost; the checks below decide.
            self.event("fence", "note", f"stopping the primary VMs exited {stop.returncode}")
        worker = self.output_json("shared", "node_service_accounts")["primary/worker"]
        self.run(["gcloud", "iam", "service-accounts", "disable", worker])
        self.wait("the primary worker account does not report disabled=True",
                  lambda: self.account_disabled(worker) == "True", ACCOUNT_TIMEOUT)
        self.wait(f"https://{self.primary_host}/api/healthz still returns 200",
                  lambda: self.system.http_status(f"https://{self.primary_host}/api/healthz") != 200,
                  FENCE_TIMEOUT)

    def dns_cutover(self) -> None:
        address = self.values["recovery_address"]
        self.make("dns-set", f"DNS_NAME={self.public_host.split('.')[0]}", f"DNS_ADDRESS={address}")
        self.wait_for_record(self.public_host, address)

    def probe_recovery(self) -> None:
        self.wait_for_health(self.public_host)
        self.make("check-fixtures", "CLUSTER=recovery", f"GIT_HOST={self.public_host}")
        self.make("write-check", "CLUSTER=recovery", f"GIT_HOST={self.public_host}")

    def resume_backups(self) -> None:
        """Set backup_suspend to false in the cluster, not in Git, and wait for the CronJob."""
        self.kubectl(
            ["create", "configmap", RUN_SETTINGS, "--namespace", "flux-system",
             "--from-literal=backup_suspend=false", "--dry-run=client", "--output=yaml"],
            ["apply", "--filename=-"],
        )
        # Flux does not watch the ConfigMap; request the reconcile.
        requested = self.system.now().strftime(drill_report.TIME_FORMAT)
        self.kubectl(["annotate", "--overwrite", "--namespace", "flux-system", "kustomization/gitea",
                      f"reconcile.fluxcd.io/requestedAt={requested}"])
        self.wait("the recovery backup CronJob is still suspended",
                  lambda: self.backup_suspend() == "false", RESUME_TIMEOUT)


# The order of the runbook. Each name is a stage of scripts/drill_report.py.
STAGES: tuple[tuple[str, Callable[[Recovery], None]], ...] = (
    ("preflight", Recovery.preflight),
    ("provisioning", Recovery.provisioning),
    ("bootstrap", Recovery.bootstrap),
    ("flux-reconcile", Recovery.flux_reconcile),
    ("restore", Recovery.restore),
    ("verification", Recovery.verification),
    ("fence", Recovery.fence),
    ("dns-cutover", Recovery.dns_cutover),
    ("probe-recovery", Recovery.probe_recovery),
    ("resume-backups", Recovery.resume_backups),
)
STAGE_NAMES = tuple(name for name, _ in STAGES)


def run(recovery: Recovery, through: str | None = None) -> int:
    last = STAGE_NAMES.index(through) if through else len(STAGES) - 1
    if recovery.state["started"] is None:
        recovery.state["started"] = recovery.system.now().strftime(drill_report.TIME_FORMAT)
        recovery.save()
    pending = [name for name in STAGE_NAMES[: last + 1] if name not in recovery.state["passed"]]
    try:
        recovery.init_roots()
        # A resumed run may start on a new runner, and the environment it
        # recorded may have been removed since.
        if pending and "provisioning" in recovery.state["passed"]:
            recovery.require_recorded_environment()
            recovery.inventory()
    except (StageFailed, OSError) as error:
        if pending:
            recovery.event(pending[0], "note", f"failed before the stage: {error}")
        print(f"error: {error}", file=sys.stderr)
        return 1
    for name, stage in STAGES[: last + 1]:
        if name in recovery.state["passed"]:
            print(f"== {name}: passed earlier, skipped", flush=True)
            continue
        print(f"== {name}", flush=True)
        recovery.event(name, "start")
        try:
            stage(recovery)
        except (StageFailed, OSError, ValueError, KeyError) as error:
            recovery.save()
            recovery.event(name, "note", f"failed: {error}")
            print(f"error: {name} failed: {error}", file=sys.stderr)
            print("Fix the cause and run the command again; it resumes at this stage.", file=sys.stderr)
            return 1
        recovery.state["passed"].append(name)
        recovery.save()
        recovery.event(name, "end")
    if last < len(STAGES) - 1:
        print(f"Stopped after {through}, as requested. Run again without --through to continue.")
    else:
        # startingDeadlineSeconds in deploy/apps/gitea/backup.yaml runs a slot
        # missed in the last ten minutes as soon as the CronJob resumes.
        print("Recovery complete. The recovery cluster writes its first set under recovery/ "
              "at once if a scheduled slot passed in the last ten minutes, else at the next slot.")
    print(drill_report.timeline_table(drill_report.read_events(recovery.drill_dir / TIMELINE_FILE)))
    return 0


def archive(drill_dir: Path, names: tuple[str, ...], stamp: str) -> None:
    """Rename each file with the stamp, so the next run starts over and the record stays."""
    for name in names:
        path = drill_dir / name
        if path.exists():
            path.replace(drill_dir / f"{path.stem}-{stamp}{path.suffix}")
            print(f"archived {path.name} as {path.stem}-{stamp}{path.suffix}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--drill-dir", type=Path, default=DRILL_DIR)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("run", help="Run the stages that have not passed, in order.")
    start.add_argument("--through", choices=STAGE_NAMES,
                       help="Stop after this stage, such as verification for a rehearsal without a cutover.")
    commands.add_parser("status", help="List each stage and the values recorded so far.")
    commands.add_parser("reset", help="Archive the state and timeline so the next run starts over.")
    return parser.parse_args(argv)


def main(argv=None, system: System | None = None) -> int:
    args = parse_args(argv)
    args.drill_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    # One run at a time: a second run would repeat the stages of the first.
    with (args.drill_dir / LOCK_FILE).open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("error: another recovery run holds the lock", file=sys.stderr)
            return 1
        if args.command == "reset":
            archive(args.drill_dir, (STATE_FILE, TIMELINE_FILE, RETURN_STATE_FILE, RETURN_TIMELINE_FILE),
                    datetime.now(timezone.utc).strftime(drill_report.SET_FORMAT))
            return 0
        recovery = Recovery(system or System(), args.drill_dir)
        if args.command == "status":
            for name in STAGE_NAMES:
                print(f"{'passed ' if name in recovery.state['passed'] else 'pending'}  {name}")
            for key, value in recovery.values.items():
                print(f"{key}: {value}")
            return 0
        return run(recovery, args.through)


if __name__ == "__main__":
    raise SystemExit(main())
