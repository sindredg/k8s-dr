#!/usr/bin/env python3
"""Start the recovery workflow when the primary is down from everywhere.

The watchdog workflow runs this every five minutes, in two jobs:

  local    the checks that need no cloud access: the switch is on, the public
           name and the primary's own name do not answer, the public name
           still points at the primary, and no recovery runs or failed after
           an automatic start. Prints candidate=true or candidate=false.
  confirm  repeats those checks, then requires that every checker of the
           uptime check failed for the whole window. With --start it then
           dispatches the recovery workflow.

The first job decides almost every run, so the pipeline account signs in only
while the service is down. See decision 0013.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

SETTINGS = "deploy/clusters/{}/cluster-settings.yaml"
RECOVERY_WORKFLOW = "recover.yml"
# A return after a drill also moves the public name and removes clusters.
RETURN_WORKFLOW = "return.yml"
# A GITHUB_TOKEN dispatch runs as this actor, which marks an automatic start.
BOT = "github-actions[bot]"
ACTIVE = ("queued", "in_progress", "waiting", "pending", "requested")
# Decision 0013: every checker failed for the last five minutes.
WINDOW_MINUTES = 5
# The API accepts no fewer regions; see infra/shared/main.tf.
MIN_CHECKERS = 3
# The period of the uptime check in infra/shared/main.tf.
PERIOD = timedelta(seconds=60)
# Uptime results reach Cloud Monitoring a minute or two after the check, so
# the query reaches further back than the window.
LAG_MINUTES = 3
DOH = "https://cloudflare-dns.com/dns-query"
METRIC = "monitoring.googleapis.com/uptime_check/check_passed"


class NoStart(Exception):
    """A condition does not hold; the watchdog starts nothing."""


@dataclass
class Response:
    status: int | None
    body: str = ""


class System:
    """The requests and clock the watchdog uses. Tests replace it."""

    def __init__(self, environ: dict):
        self.environ = environ

    def get(self, url: str, headers: dict | None = None) -> Response:
        request = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return Response(response.status, response.read().decode())
        except urllib.error.HTTPError as error:
            return Response(error.code, error.read().decode(errors="replace"))
        except (urllib.error.URLError, OSError):
            return Response(None)

    def post(self, url: str, body: dict, headers: dict) -> Response:
        request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                         headers={**headers, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return Response(response.status, response.read().decode())
        except urllib.error.HTTPError as error:
            return Response(error.code, error.read().decode(errors="replace"))

    def access_token(self) -> str:
        return subprocess.run(["gcloud", "auth", "print-access-token"], check=True,
                              capture_output=True, text=True).stdout.strip()

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


def cluster_setting(cluster: str, key: str) -> str:
    match = re.search(rf"(?m)^  {key}: (\S+)$", Path(SETTINGS.format(cluster)).read_text())
    if not match:
        raise ValueError(f"no {key} in {SETTINGS.format(cluster)}")
    return match.group(1).strip('"')


def point_time(point: dict) -> datetime:
    """The end time of a Cloud Monitoring point, which may carry fractions of a second."""
    return datetime.strptime(point["interval"]["endTime"][:19], "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=timezone.utc)


class Watchdog:
    def __init__(self, system: System):
        self.system = system
        self.environ = system.environ
        self.public_host = cluster_setting("primary", "git_host")
        self.primary_host = cluster_setting("primary", "git_cluster_host")

    def github(self, path: str) -> dict:
        api = self.environ.get("GITHUB_API_URL", "https://api.github.com")
        response = self.system.get(f"{api}/repos/{self.environ['GITHUB_REPOSITORY']}/{path}", {
            "Authorization": f"Bearer {self.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
        })
        if response.status != 200:
            raise NoStart(f"GitHub API {path} returned {response.status}")
        return json.loads(response.body)

    def resolve(self, host: str) -> list[str]:
        query = urllib.parse.urlencode({"name": host, "type": "A"})
        response = self.system.get(f"{DOH}?{query}", {"Accept": "application/dns-json"})
        if response.status != 200:
            raise NoStart(f"resolving {host} returned {response.status}")
        answers = json.loads(response.body).get("Answer", [])
        return sorted(answer["data"] for answer in answers if answer.get("type") == 1)

    # Conditions

    def require_switch(self) -> None:
        """Condition 4: the repository variable enables automatic failover."""
        if self.environ.get("AUTO_FAILOVER") != "true":
            raise NoStart("the AUTO_FAILOVER variable is not true")

    def require_unreachable(self) -> None:
        """Condition 2: the runner's own requests to both names fail."""
        for host in (self.public_host, self.primary_host):
            status = self.system.get(f"https://{host}/api/healthz").status
            if status == 200:
                raise NoStart(f"https://{host}/api/healthz returned 200")
            print(f"https://{host}/api/healthz: {status or 'no answer'}")

    def require_public_name_on_primary(self) -> None:
        """Condition 3: the public name does not point at another cluster."""
        public, primary = self.resolve(self.public_host), self.resolve(self.primary_host)
        if not public or public != primary:
            raise NoStart(f"{self.public_host} does not resolve to the address of {self.primary_host}")

    def require_no_recovery(self) -> None:
        """Condition 3: no recovery runs, and no automatic start failed since."""
        runs = self.github(f"actions/workflows/{RECOVERY_WORKFLOW}/runs?per_page=20")["workflow_runs"]
        active = [run for run in runs if run["status"] in ACTIVE]
        if active:
            raise NoStart(f"recovery run {active[0]['id']} is {active[0]['status']}")
        # The newest run decides. A failed automatic start waits for a person,
        # who resumes it or starts a run that passes.
        newest = runs[0] if runs else None
        if newest and (newest.get("triggering_actor") or {}).get("login") == BOT \
                and newest["conclusion"] != "success":
            raise NoStart(f"automatic recovery run {newest['id']} ended with {newest['conclusion']}")
        returns = self.github(f"actions/workflows/{RETURN_WORKFLOW}/runs?per_page=20")["workflow_runs"]
        active = [run for run in returns if run["status"] in ACTIVE]
        if active:
            raise NoStart(f"return run {active[0]['id']} is {active[0]['status']}")

    def uptime_series(self, start: datetime, end: datetime) -> list[dict]:
        """The results of the uptime check of the public name, one series per checker."""
        project = self.environ["GCP_PROJECT_ID"]
        headers = {"Authorization": f"Bearer {self.system.access_token()}"}
        base = f"https://monitoring.googleapis.com/v3/projects/{project}"
        response = self.system.get(f"{base}/uptimeCheckConfigs", headers)
        if response.status != 200:
            raise NoStart(f"listing the uptime checks returned {response.status}")
        configs = [config for config in json.loads(response.body).get("uptimeCheckConfigs", [])
                   if config.get("monitoredResource", {}).get("labels", {}).get("host") == self.public_host]
        if len(configs) != 1:
            raise NoStart(f"found {len(configs)} uptime checks for {self.public_host}, expected 1")
        check_id = configs[0]["name"].rsplit("/", 1)[-1]

        query = urllib.parse.urlencode({
            "filter": f'metric.type="{METRIC}" AND metric.labels.check_id="{check_id}"',
            "interval.startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "interval.endTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        response = self.system.get(f"{base}/timeSeries?{query}", headers)
        if response.status != 200:
            raise NoStart(f"reading the uptime results returned {response.status}")
        body = json.loads(response.body)
        # The default page holds far more points than these windows produce.
        if body.get("nextPageToken"):
            raise NoStart("the uptime results span more than one page")
        return body.get("timeSeries", [])

    def require_every_checker_failed(self) -> None:
        """Condition 1: every checker of the uptime check failed for the whole window."""
        end = self.system.now()
        series = self.uptime_series(end - timedelta(minutes=WINDOW_MINUTES + LAG_MINUTES), end)
        if len(series) < MIN_CHECKERS:
            raise NoStart(f"{len(series)} checkers reported, expected at least {MIN_CHECKERS}")
        since = end - timedelta(minutes=WINDOW_MINUTES)
        for one in series:
            location = one["metric"]["labels"].get("checker_location", "?")
            # Every result from one period before the window on; the
            # oldest of them must be at or before the start of the window.
            points = sorted((point_time(point), point["value"]["boolValue"]) for point in one["points"])
            points = [(time, passed) for time, passed in points if time >= since - PERIOD]
            if not points:
                raise NoStart(f"checker {location} reported nothing since {since - PERIOD:%H:%M:%S}Z")
            if any(passed for _, passed in points):
                raise NoStart(f"checker {location} passed within the window")
            if points[0][0] > since:
                raise NoStart(f"checker {location} has failed only since {points[0][0]:%H:%M:%S}Z")
            # A checker that stopped reporting has not failed for the window.
            if points[-1][0] < end - timedelta(minutes=LAG_MINUTES):
                raise NoStart(f"checker {location} has reported nothing since {points[-1][0]:%H:%M:%S}Z")
        print(f"all {len(series)} checkers failed since at least {since:%H:%M:%S}Z")

    def local(self) -> None:
        self.require_switch()
        self.require_unreachable()
        self.require_public_name_on_primary()
        self.require_no_recovery()

    def start(self) -> None:
        api = self.environ.get("GITHUB_API_URL", "https://api.github.com")
        url = f"{api}/repos/{self.environ['GITHUB_REPOSITORY']}/actions/workflows/{RECOVERY_WORKFLOW}/dispatches"
        response = self.system.post(url, {"ref": "main", "inputs": {"through": "all", "reset": True}}, {
            "Authorization": f"Bearer {self.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
        })
        if response.status != 204:
            raise RuntimeError(f"dispatching {RECOVERY_WORKFLOW} returned {response.status}")
        print(f"started {RECOVERY_WORKFLOW} with through=all and reset=true")


def write_output(environ: dict, name: str, value: str) -> None:
    if environ.get("GITHUB_OUTPUT"):
        with open(environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"{name}={value}\n")
    print(f"{name}={value}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("local", help="Run the checks that need no cloud access.")
    confirm = commands.add_parser("confirm", help="Repeat them and read the uptime results.")
    confirm.add_argument("--start", action="store_true", help="Dispatch the recovery if every condition holds.")
    return parser.parse_args(argv)


def main(argv=None, system: System | None = None) -> int:
    args = parse_args(argv)
    watchdog = Watchdog(system or System(dict(os.environ)))
    try:
        watchdog.local()
        if args.command == "confirm":
            watchdog.require_every_checker_failed()
    except NoStart as reason:
        print(f"no start: {reason}")
        if args.command == "local":
            write_output(watchdog.environ, "candidate", "false")
        return 0
    if args.command == "local":
        write_output(watchdog.environ, "candidate", "true")
        return 0
    if args.start:
        watchdog.start()
    else:
        print("every condition holds; --start was not given")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
