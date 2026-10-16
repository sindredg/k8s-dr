"""Probe Gitea densely so that a stall during a backup capture shows up.

Every INTERVAL seconds, one HTTPS request to /api/healthz (which pings the
database) and one authenticated `git ls-remote` (which reads a repository).
Each result is a tab-separated line: UTC start time, probe, outcome, seconds.
A request slower than SLOW seconds, an error, or a non-200 status counts as a
failure. Runs until interrupted or for DURATION seconds.

Usage: capture_probe.py BASE_URL REPO_PATH LOG [INTERVAL] [DURATION]
The user and password come from GIT_PROBE_USER and GIT_PROBE_PASSWORD. The
password reaches Git through scripts/git_with_password.sh, so it stays out of
command arguments.
"""
import datetime
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

SLOW = 2.0
TIMEOUT = 10
GIT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "git_with_password.sh")


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


def healthz(base):
    try:
        with urllib.request.urlopen(f"{base}/api/healthz", timeout=TIMEOUT) as r:
            return str(r.status)
    except urllib.error.HTTPError as e:
        return f"http-{e.code}"
    except Exception as e:  # noqa: BLE001 - every error is a failed probe
        return f"error-{type(e).__name__}"


def ls_remote(url, user, password):
    r = subprocess.run([GIT, "ls-remote", "--heads", url], input=password + "\n",
                       env={**os.environ, "GIT_STDIN_USERNAME": user},
                       capture_output=True, text=True, timeout=TIMEOUT, check=False)
    return "200" if r.returncode == 0 and r.stdout.strip() else f"git-exit-{r.returncode}"


def run(name, fn, log, lock):
    start, t0 = now(), time.monotonic()
    try:
        outcome = fn()
    except Exception as e:  # noqa: BLE001
        outcome = f"error-{type(e).__name__}"
    took = time.monotonic() - t0
    ok = outcome == "200" and took < SLOW
    with lock:
        log.write(f"{start}\t{name}\t{outcome if ok or outcome != '200' else 'slow'}\t{took:.3f}\n")
        log.flush()


def main():
    base, repo, path = sys.argv[1:4]
    interval = float(sys.argv[4]) if len(sys.argv) > 4 else 0.5
    duration = float(sys.argv[5]) if len(sys.argv) > 5 else float("inf")
    user = os.environ["GIT_PROBE_USER"]
    password = os.environ["GIT_PROBE_PASSWORD"]
    git_url = f"{base.rstrip('/')}/{repo}.git"
    lock = threading.Lock()
    end = time.monotonic() + duration
    threads = []
    with open(path, "a", encoding="utf-8") as log:
        while time.monotonic() < end:
            tick = time.monotonic()
            threads = [t for t in threads if t.is_alive()]
            for name, fn in (("healthz", lambda: healthz(base)), ("ls-remote", lambda: ls_remote(git_url, user, password))):
                t = threading.Thread(target=run, args=(name, fn, log, lock))
                t.start()
                threads.append(t)
            time.sleep(max(0.0, interval - (time.monotonic() - tick)))
        # Finish the probes in flight before the log closes.
        for t in threads:
            t.join()


if __name__ == "__main__":
    main()
