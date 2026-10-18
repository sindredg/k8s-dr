#!/usr/bin/env python3
"""Run one unattended drill, one workflow step per command.

The Drill workflow runs these commands in order (decision 0015):

  guard    refuse the drill unless every condition holds; changes nothing
  writes   push a write check every five minutes for 25 minutes
  isolate  record the time and stop both primary VMs
  wait     take no action until a Recover run that the watchdog started
           after the isolation ends, for at most four hours. If no run
           starts within an hour, start the primary again
  report   compute recovery time and data loss, and check the dataset
  return   dispatch the Return workflow and wait for it to succeed. Runs
           after any passed recovery unless the drill missed a target
  finish   store the result and open an issue labelled drill; runs always

The steps share drill.json in --work-dir. Each step copies the directory to
pipeline/drills/<id>/ in the Terraform state bucket, so a failed job keeps
its write log. `results` runs on an operator machine and prints the table
of every stored result for docs/drills.md.
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import drill_report  # noqa: E402
import pipeline  # noqa: E402
import recover  # noqa: E402
import watchdog  # noqa: E402

STATE_FILE = "drill.json"
WRITES_FILE = "writes.tsv"
RESULT_FILE = "result.json"
# Copies of the recovery run state, read before the return archives it.
RECOVERY_DIR = "recovery"
DRILLS_PREFIX = "pipeline/drills"
RUN_STATE_PREFIX = "pipeline/drill"
STEPS = ("guard", "writes", "isolate", "wait", "report", "return")
# Decision 0015: a write every five minutes for 25 minutes. A set that
# started in the first 15 minutes has finished before the isolation if its
# capture takes less than ten, so the restored commit is in the log.
WRITE_INTERVAL = 300
WRITES = 6
# deploy/apps/gitea/backup.yaml writes a set every 15 minutes.
BACKUP_INTERVAL = timedelta(minutes=15)
# Decision 0002.
RTO_TARGET = timedelta(hours=4)
RPO_TARGET = timedelta(hours=2)
# The return job's own limit in return.yml, plus time to queue.
RETURN_TIMEOUT = timedelta(minutes=100)
# Detection took up to about 25 minutes in decision 0013's estimate. A
# watchdog that has started nothing after an hour will not, and the primary
# would otherwise stay stopped until the four-hour deadline.
START_TIMEOUT = timedelta(minutes=60)
DISPATCH_ATTEMPTS = 3
RETRY_SECONDS = 30
POLL_SECONDS = 60
# A dispatched run's creation time comes from GitHub, not the runner clock.
CLOCK_SKEW = timedelta(seconds=60)
LABEL = "drill"
# Values that the workflow log masks but an issue body would not.
MASKED = ("GCP_PROJECT_ID", "GCP_WORKLOAD_IDENTITY_PROVIDER", "GCP_SERVICE_ACCOUNT", "STATE_BUCKET",
          "BACKUP_BUCKET", "ADMIN_MEMBER", "RECOVERY_READER_MEMBER")
TIME = drill_report.TIME_FORMAT


class DrillFailed(Exception):
    """A condition or step failed; the drill stops here."""


class System(watchdog.System, recover.System):
    """Requests from the watchdog and commands from the recovery. Tests replace it."""


def stamp(time: datetime) -> str:
    return time.strftime(TIME)


def parse(text: str) -> datetime:
    return drill_report.parse_time(text)


def seconds(delta: timedelta) -> int:
    return int(delta.total_seconds())


class Drill:
    def __init__(self, system: System, work_dir: Path):
        self.system = system
        self.environ = system.environ
        self.work_dir = work_dir
        self.watchdog = watchdog.Watchdog(system)
        self.public_host = self.watchdog.public_host
        self.state_path = work_dir / STATE_FILE
        self.state = {"id": None, "steps": {}, "failed": None}
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())

    def save(self) -> None:
        self.work_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, indent=2) + "\n")
        temporary.replace(self.state_path)

    # Commands

    def run(self, argv: list[str], *, capture: bool = False, env: dict | None = None) -> str:
        result = self.system.call(argv, capture=capture, echo=False, env=env)
        if result.returncode != 0:
            sys.stderr.write(result.stderr)
            raise DrillFailed(f"{shlex.join(argv[:3])} exited {result.returncode}")
        return result.stdout

    def make(self, target: str, *variables: str) -> str:
        return self.run(["make", "--no-print-directory", target, "BIN=", *variables])

    def output(self, root: str, name: str, *, raw: bool = True):
        text = self.run(["terraform", f"-chdir=infra/{root}", "output", "-raw" if raw else "-json", name],
                        capture=True)
        return text.strip() if raw else json.loads(text)

    def init_roots(self) -> None:
        for root in ("shared", "primary", "recovery"):
            self.run(["terraform", f"-chdir=infra/{root}", "init", "-input=false", "-backend-config=backend.hcl"],
                     capture=True)

    def state_url(self, prefix: str) -> str:
        return f"gs://{self.environ['STATE_BUCKET']}/{prefix}"

    def push(self) -> None:
        """Copy the work directory to the drill's folder; never deletes there."""
        self.run(["gcloud", "storage", "rsync", "--recursive", str(self.work_dir),
                  self.state_url(f"{DRILLS_PREFIX}/{self.state['id']}")])

    def try_push(self) -> None:
        """Copy as push does, but only warn on failure: the job keeps its local state.

        A failed copy must not stop a drill after the isolation, which would
        leave the primary stopped with nobody to return to it.
        """
        try:
            self.push()
        except DrillFailed as error:
            print(f"warning: copying the drill state failed: {error}", file=sys.stderr)

    def newest_primary_set(self) -> str:
        listing = self.run(["gcloud", "storage", "ls",
                            f"gs://{self.environ['BACKUP_BUCKET']}/{recover.BACKUP_SOURCE}/*/manifest.json"],
                           capture=True)
        names = sorted(line.split("/")[-2] for line in listing.splitlines() if line.endswith("/manifest.json"))
        if not names:
            raise DrillFailed(f"no complete set under {recover.BACKUP_SOURCE}/")
        return names[-1]

    def check_dataset(self) -> str:
        return self.run([sys.executable, "scripts/dataset.py", "check", "--host", self.public_host],
                        capture=True).strip()

    # GitHub

    def runs(self, workflow: str) -> list[dict]:
        return self.watchdog.github(
            f"actions/workflows/{workflow}/runs?event=workflow_dispatch&per_page=20")["workflow_runs"]

    def bot_runs_since(self, workflow: str, since: datetime) -> list[dict]:
        """Runs that a GITHUB_TOKEN dispatched at or after since, oldest first."""
        found = [run for run in self.runs(workflow)
                 if (run.get("triggering_actor") or {}).get("login") == watchdog.BOT
                 and parse(run["created_at"]) >= since]
        return sorted(found, key=lambda run: run["created_at"])

    def github_post(self, path: str, body: dict) -> watchdog.Response:
        api = self.environ.get("GITHUB_API_URL", "https://api.github.com")
        return self.system.post(f"{api}/repos/{self.environ['GITHUB_REPOSITORY']}/{path}", body, {
            "Authorization": f"Bearer {self.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
        })

    def wait_for_run(self, workflow: str, since: datetime, deadline: datetime, key: str,
                     start_deadline: datetime | None = None) -> dict:
        """Poll until the first bot run since the given time ends. Transient API errors only wait."""
        while True:
            try:
                found = self.bot_runs_since(workflow, since)
            except watchdog.NoStart as error:
                print(f"waiting: {error}", flush=True)
                found = []
            if found:
                run = found[0]
                if self.state.get(key, {}).get("id") != run["id"]:
                    print(f"{workflow} run {run['id']} started at {run['created_at']}", flush=True)
                self.state[key] = {"id": run["id"], "url": run["html_url"], "created_at": run["created_at"],
                                   "conclusion": run.get("conclusion")}
                self.save()
                if run["status"] == "completed":
                    if run["conclusion"] != "success":
                        raise DrillFailed(f"{workflow} run {run['id']} ended with {run['conclusion']}")
                    return run
            if not found and start_deadline and self.system.now() >= start_deadline:
                raise DrillFailed(f"{workflow}: no run started by {stamp(start_deadline)}")
            if self.system.now() >= deadline:
                what = f"run {found[0]['id']} did not end" if found else "no run started"
                raise DrillFailed(f"{workflow}: {what} by {stamp(deadline)}")
            self.system.sleep(POLL_SECONDS)

    # Steps

    def guard(self) -> None:
        """Every condition of decision 0015 holds; nothing has changed yet."""
        if self.environ.get("SCHEDULED_DRILLS") != "true":
            raise DrillFailed("the SCHEDULED_DRILLS variable is not true")
        # Without the watchdog, nothing would recover the isolated primary.
        self.watchdog.require_switch()
        self.watchdog.require_no_recovery()
        self.init_roots()
        left = self.run(["terraform", "-chdir=infra/recovery", "state", "list"], capture=True).split()
        if left:
            raise DrillFailed(f"infra/recovery holds {len(left)} resources from an earlier recovery")
        self.watchdog.require_public_name_on_primary()
        newest = self.newest_primary_set()
        age = self.system.now() - drill_report.parse_time(newest, drill_report.SET_FORMAT)
        if age >= 2 * BACKUP_INTERVAL:
            raise DrillFailed(f"the newest primary set {newest} is {seconds(age) // 60} minutes old")
        # Checks the keys, both states, the newest set, the fixtures through
        # the public name, and the Cloudflare token.
        self.run(["make", "--no-print-directory", "preflight", "BIN="])
        self.state["dataset"] = self.check_dataset()

    def writes(self) -> None:
        log = self.work_dir / WRITES_FILE
        for number in range(WRITES):
            if number:
                self.system.sleep(WRITE_INTERVAL)
            # One retry, so a transient failure does not cost the drill.
            try:
                self.make("write-check", f"WRITE_LOG={log.resolve()}")
            except DrillFailed as error:
                print(f"warning: {error}; retrying once", file=sys.stderr)
                self.system.sleep(RETRY_SECONDS)
                self.make("write-check", f"WRITE_LOG={log.resolve()}")
            self.try_push()

    def primary_vms(self, action: str) -> int:
        names = sorted(self.output("primary", "instance_names", raw=False).values())
        zone, project = self.output("primary", "zone"), self.output("primary", "project_id")
        if action == "stop":
            self.state["isolated_at"] = stamp(self.system.now())
            self.save()
        self.run(["gcloud", "compute", "instances", action, *names, "--zone", zone, "--project", project])
        return len(names)

    def isolate(self) -> None:
        count = self.primary_vms("stop")
        print(f"isolated at {self.state['isolated_at']}: stopped {count} primary VMs")

    def wait(self) -> None:
        isolated = parse(self.state["isolated_at"])
        try:
            self.wait_for_run(watchdog.RECOVERY_WORKFLOW, isolated, isolated + RTO_TARGET, "recovery_run",
                              start_deadline=isolated + START_TIMEOUT)
        except DrillFailed as error:
            if "recovery_run" in self.state:
                raise
            # Nothing changed but the stop: no recovery environment, and the
            # public name still points at the primary.
            self.primary_vms("start")
            self.state["primary_restarted"] = stamp(self.system.now())
            self.save()
            raise DrillFailed(f"{error}; the drill started the primary VMs again") from None

    def first_failed_probe(self, isolated: datetime, end: datetime) -> tuple[datetime, dict]:
        """The earliest failed result of any checker after the isolation, and each checker's first."""
        series = self.watchdog.uptime_series(isolated - timedelta(minutes=5), end)
        firsts = {}
        for one in series:
            location = one["metric"]["labels"].get("checker_location", "?")
            failed = sorted(watchdog.point_time(point) for point in one["points"]
                            if not point["value"]["boolValue"] and watchdog.point_time(point) >= isolated)
            if failed:
                firsts[location] = stamp(failed[0])
        if len(firsts) < watchdog.MIN_CHECKERS:
            raise DrillFailed(f"{len(firsts)} checkers reported a failure after the isolation, "
                              f"expected at least {watchdog.MIN_CHECKERS}")
        return min(parse(time) for time in firsts.values()), firsts

    def report(self) -> None:
        isolated = parse(self.state["isolated_at"])
        copies = self.work_dir / RECOVERY_DIR
        copies.mkdir(mode=0o700, exist_ok=True)
        for name in (recover.STATE_FILE, recover.TIMELINE_FILE):
            self.run(["gcloud", "storage", "cp", self.state_url(f"{RUN_STATE_PREFIX}/{name}"), str(copies / name)])
        recovered = json.loads((copies / recover.STATE_FILE).read_text())
        if recovered.get("started") is None or parse(recovered["started"]) < isolated:
            raise DrillFailed("the recovery run state is not from a run started after the isolation")
        missing = [name for name in recover.STAGE_NAMES if name not in recovered["passed"]]
        if missing:
            raise DrillFailed(f"the recovery run state has not passed {', '.join(missing)}")
        rows = drill_report.stage_rows(drill_report.read_events(copies / recover.TIMELINE_FILE))
        ended = rows["probe-recovery"]["end"]
        first_failed, checkers = self.first_failed_probe(isolated, ended)
        started = parse(self.state["recovery_run"]["created_at"])
        values = recovered["values"]
        loss = drill_report.data_loss(drill_report.read_writes(self.work_dir / WRITES_FILE),
                                      values["restored_head"], isolated)
        set_time = drill_report.parse_time(values["backup_set"], drill_report.SET_FORMAT)
        result = {
            "first_failed_probe": stamp(first_failed),
            "first_failed_by_checker": checkers,
            "recovery_started": stamp(started),
            "recovered": stamp(ended),
            "detection_seconds": seconds(started - first_failed),
            "recovery_seconds": seconds(ended - first_failed),
            "backup_set": values["backup_set"],
            "backup_age_seconds": seconds(isolated - set_time),
            "restored_head": values["restored_head"],
            "last_write": stamp(loss["last"].acknowledged),
            "data_loss_seconds": seconds(loss["loss"]),
            "writes_lost": len(loss["lost"]),
            "stages": {stage: seconds(row["end"] - row["start"]) for stage, row in rows.items()
                       if row["start"] and row["end"]},
        }
        result["met"] = (result["recovery_seconds"] <= seconds(RTO_TARGET)
                         and result["data_loss_seconds"] <= seconds(RPO_TARGET))
        self.state["result"] = result
        self.save()
        if not result["met"]:
            raise DrillFailed(f"recovery time {result['recovery_seconds']} s or data loss "
                              f"{result['data_loss_seconds']} s missed its target")
        # The public name now points at the recovery cluster.
        self.state["dataset_restored"] = self.check_dataset()

    def return_(self) -> None:
        dispatched = self.system.now()
        for attempt in range(1, DISPATCH_ATTEMPTS + 1):
            # A dispatch whose answer was lost may still have started a run.
            if attempt > 1 and self.bot_runs_since(watchdog.RETURN_WORKFLOW, dispatched - CLOCK_SKEW):
                break
            # A string, as gh workflow run -f sends it; see watchdog.start.
            response = self.github_post(f"actions/workflows/{watchdog.RETURN_WORKFLOW}/dispatches", {
                "ref": "main", "inputs": {"discard_recovery_writes": "true"}})
            if response.status in (200, 204):
                break
            print(f"warning: dispatching the return returned {response.status or 'no answer'}", file=sys.stderr)
            if attempt == DISPATCH_ATTEMPTS:
                raise DrillFailed(f"dispatching the return failed {DISPATCH_ATTEMPTS} times")
            self.system.sleep(RETRY_SECONDS)
        self.wait_for_run(watchdog.RETURN_WORKFLOW, dispatched - CLOCK_SKEW, dispatched + RETURN_TIMEOUT,
                          "return_run")

    # The result

    def outcome(self) -> str:
        failed = self.state.get("failed")
        if failed and failed["step"] in ("setup", "guard"):
            return "refused"
        if failed and failed["step"] == "report" and self.state.get("result", {}).get("met") is False:
            return "missed"
        if failed or self.state["steps"].get("return") != "passed":
            return "failed"
        return "passed"

    def masked(self, text: str) -> str:
        for name in MASKED:
            value = self.environ.get(name)
            if value:
                text = text.replace(value, "***")
        return text

    def issue_body(self, outcome: str) -> str:
        state, result = self.state, self.state.get("result") or {}
        lines = [f"Outcome: **{outcome}**", "", f"Workflow run: {self.environ.get('RUN_URL', 'unknown')}", ""]
        if state.get("failed"):
            lines += [f"Stopped in `{state['failed']['step']}`: {state['failed']['reason']}", ""]
        rows = [("Isolated at", state.get("isolated_at")),
                ("First failed probe", result.get("first_failed_probe")),
                ("Recovery started by the watchdog", (state.get("recovery_run") or {}).get("url")),
                ("Detection", duration(result.get("detection_seconds"))),
                ("Recovery time (target 4 h)", duration(result.get("recovery_seconds"))),
                ("Data loss (target 2 h)", duration(result.get("data_loss_seconds"))),
                ("Acknowledged writes lost", result.get("writes_lost")),
                ("Restored set and its age at isolation",
                 f"{result['backup_set']}, {duration(result['backup_age_seconds'])}" if result else None),
                ("Dataset after the restore", state.get("dataset_restored")),
                ("Return", (state.get("return_run") or {}).get("url"))]
        lines += ["| Measure | Value |", "| --- | --- |"]
        lines += [f"| {name} | {value} |" for name, value in rows if value not in (None, "")]
        timeline = self.work_dir / RECOVERY_DIR / recover.TIMELINE_FILE
        if timeline.exists():
            lines += ["", "Recovery stages:", "",
                      drill_report.timeline_table(drill_report.read_events(timeline))]
        if outcome != "passed" and not state.get("isolated_at"):
            lines += ["", "The primary was not isolated."]
        elif state.get("primary_restarted"):
            lines += ["", "No recovery started, so the drill started the primary VMs again at "
                      f"{state['primary_restarted']}. Read the watchdog runs since the isolation."]
        elif outcome != "passed" and state["steps"].get("return") == "passed":
            lines += ["", "The recovery and the return passed, and the primary serves again. The report "
                      "failed, so this drill's measurement is incomplete."]
        elif outcome != "passed":
            lines += ["", "The return did not finish, and the run state is kept for diagnosis. The guard "
                      "refuses further drills until the primary serves and infra/recovery is empty."]
        return self.masked("\n".join(lines)) + "\n"

    def finish(self) -> int:
        if self.state["id"] is None:
            self.state["id"] = self.system.now().strftime(drill_report.SET_FORMAT)
        if self.state.get("failed") is None and not self.state["steps"]:
            self.state["failed"] = {"step": "setup", "reason": "the job stopped before the guard"}
        outcome = self.outcome()
        self.state["outcome"] = outcome
        self.state["run_url"] = self.environ.get("RUN_URL")
        self.state["event"] = self.environ.get("GITHUB_EVENT_NAME")
        self.save()
        (self.work_dir / RESULT_FILE).write_text(json.dumps(self.state, indent=2) + "\n")
        status = 0
        try:
            self.push()
        except DrillFailed as error:
            print(f"error: storing the result failed: {error}", file=sys.stderr)
            status = 1
        title = f"Drill {self.state['id']}: {outcome}"
        response = self.github_post("issues", {"title": title, "body": self.issue_body(outcome),
                                               "labels": [LABEL]})
        if response.status != 201:
            print(f"error: opening the issue returned {response.status}", file=sys.stderr)
            status = 1
        summary = self.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as output:
                output.write(f"## {title}\n\n{self.issue_body(outcome)}")
        print(title)
        return status


def duration(value) -> str | None:
    if value is None:
        return None
    return drill_report.format_duration(timedelta(seconds=value))


# The results page

def results_table(results: list[dict]) -> str:
    """One row per drill, oldest first."""
    lines = ["| Drill | Started by | Outcome | Detection | Recovery time | Data loss | Writes lost "
             "| Bootstrap | Restore |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for state in sorted(results, key=lambda state: state["id"]):
        result = state.get("result") or {}
        stages = result.get("stages", {})
        name = f"[{state['id']}]({state['run_url']})" if state.get("run_url") else state["id"]
        cells = [name, "schedule" if state.get("event") == "schedule" else "dispatch", state["outcome"],
                 duration(result.get("detection_seconds")), duration(result.get("recovery_seconds")),
                 duration(result.get("data_loss_seconds")), result.get("writes_lost"),
                 duration(stages.get("bootstrap")), duration(stages.get("restore"))]
        lines.append("| " + " | ".join("" if cell is None else str(cell) for cell in cells) + " |")
    return "\n".join(lines)


RESULTS_START = "<!-- drill results: generated by scripts/drill.py results -->"
RESULTS_END = "<!-- end of drill results -->"


def replace_results(page: str, table: str) -> str:
    """Replace the table between the two markers of the results page."""
    if page.count(RESULTS_START) != 1 or page.count(RESULTS_END) != 1:
        raise DrillFailed("the page needs each results marker once")
    head, rest = page.split(RESULTS_START)
    _, tail = rest.split(RESULTS_END)
    return f"{head}{RESULTS_START}\n{table}\n{RESULTS_END}{tail}"


def stored_results(system: System, bucket: str) -> list[dict]:
    listing = system.call(["gcloud", "storage", "ls", f"gs://{bucket}/{DRILLS_PREFIX}/*/{RESULT_FILE}"],
                          capture=True, echo=False)
    if listing.returncode != 0:
        if "matched no objects" in listing.stderr:
            return []
        raise DrillFailed("listing the drill results failed")
    results = []
    with tempfile.TemporaryDirectory() as directory:
        for number, url in enumerate(sorted(line for line in listing.stdout.split() if line.endswith(RESULT_FILE))):
            path = Path(directory) / f"{number}.json"
            if system.call(["gcloud", "storage", "cp", url, str(path)], capture=True, echo=False).returncode:
                raise DrillFailed(f"copying {url.rsplit('/', 2)[-2]}/{RESULT_FILE} failed")
            results.append(json.loads(path.read_text()))
    return results


COMMANDS = {"guard": Drill.guard, "writes": Drill.writes, "isolate": Drill.isolate, "wait": Drill.wait,
            "report": Drill.report, "return": Drill.return_}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work-dir", type=Path, default=Path(".drill/drill"))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in (*STEPS, "finish"):
        commands.add_parser(name)
    results = commands.add_parser("results", help="Print the table of every stored result.")
    results.add_argument("--bucket", help="The Terraform state bucket; read from infra/shared/backend.hcl by default.")
    results.add_argument("--page", type=Path, help="Rewrite the table in this page instead of printing it.")
    return parser.parse_args(argv)


def main(argv=None, system: System | None = None) -> int:
    args = parse_args(argv)
    system = system or System(dict(os.environ))
    if args.command == "results":
        bucket = args.bucket or system.environ.get("STATE_BUCKET")
        if not bucket:
            bucket = pipeline.read_assignments(Path("infra/shared/backend.hcl")).get("bucket", "")
        try:
            table = results_table(stored_results(system, bucket))
            if args.page:
                args.page.write_text(replace_results(args.page.read_text(), table))
                print(f"wrote the table to {args.page}")
            else:
                print(table)
        except DrillFailed as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        return 0
    drill = Drill(system, args.work_dir)
    if args.command == "finish":
        return drill.finish()
    if args.command == "guard":
        if drill.state["steps"]:
            print("error: the work directory holds an earlier drill", file=sys.stderr)
            return 1
        drill.state["id"] = system.now().strftime(drill_report.SET_FORMAT)
        drill.save()
    elif args.command == "return":
        # A report that failed for any reason but a missed target still
        # returns: the recovery passed, and staying on the recovery cluster
        # would block every later drill.
        if any(drill.state["steps"].get(step) != "passed" for step in STEPS[:STEPS.index("report")]):
            print("error: return needs a recovery that passed", file=sys.stderr)
            return 1
        if (drill.state.get("result") or {}).get("met") is False:
            print("error: the drill missed a target; the recovery state is kept for diagnosis", file=sys.stderr)
            return 1
    elif drill.state["failed"] or any(drill.state["steps"].get(step) != "passed"
                                      for step in STEPS[:STEPS.index(args.command)]):
        print(f"error: {args.command} needs every earlier step to have passed", file=sys.stderr)
        return 1
    print(f"== {args.command}", flush=True)
    try:
        COMMANDS[args.command](drill)
    except (DrillFailed, watchdog.NoStart, OSError, ValueError, KeyError, TypeError) as error:
        drill.state["failed"] = {"step": args.command, "reason": str(error)}
        drill.state["steps"][args.command] = "failed"
        drill.save()
        print(f"error: {args.command} failed: {error}", file=sys.stderr)
        drill.try_push()
        return 1
    drill.state["steps"][args.command] = "passed"
    drill.save()
    drill.try_push()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
