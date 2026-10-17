#!/usr/bin/env python3
"""Load the drill dataset into Gitea, or check that an endpoint holds it.

The dataset gives the restore a realistic size (decision 0015). It belongs to
the fixture user: REPOSITORIES private repositories, each with COMMITS
commits that add one file of PART_SIZE random bytes, and ISSUES issues in
the first repository. Random bytes do not compress, so the backup set grows
by about the size of the files.

The bytes come from a generator seeded with the repository and part names,
and every commit has a fixed identity and date. Every SHA is therefore known
without the data or a clone:

  load   create what is missing and push each commit the endpoint lacks
  check  compare each repository's head with its expected SHA, and count the
         issues

The password reaches Git on stdin through scripts/git_with_password.sh and
the API in a header, never in an argument.
"""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

import yaml

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "recovery/fixtures.yaml"
FIXTURE_SECRETS = ROOT / "recovery/fixtures.sops.yaml"
SETTINGS = ROOT / "deploy/clusters/primary/cluster-settings.yaml"
GIT_WITH_PASSWORD = ROOT / "scripts/git_with_password.sh"
# 8 x 16 x 16 MiB = 2 GiB. One push sends one part: Traefik closes a request
# that takes longer than its default read timeout of 60 seconds.
REPOSITORIES = 8
COMMITS = 16
PART_SIZE = 16 * 1024 * 1024
ISSUES = 200
PREFIX = "dataset"
# randbytes accepts at most 2**31 bits per call.
CHUNK = 4 * 1024 * 1024
# A commit date outside every write-check and fixture date.
DATE = 1760745600  # 2025-10-18T00:00:00Z
MODE = "100644"


class Failed(Exception):
    """A request or check failed."""


def repository_names(count: int = REPOSITORIES) -> list[str]:
    return [f"{PREFIX}-{number:02d}" for number in range(1, count + 1)]


def part_name(number: int) -> str:
    return f"part-{number:02d}.bin"


def part_bytes(repository: str, number: int, size: int = PART_SIZE):
    """The content of one part, in chunks."""
    generator = random.Random(f"{repository}/{part_name(number)}")
    left = size
    while left:
        chunk = min(CHUNK, left)
        yield generator.randbytes(chunk)
        left -= chunk


def git_object(kind: str, body: bytes) -> str:
    return hashlib.sha1(f"{kind} {len(body)}\0".encode() + body).hexdigest()


def blob_sha(repository: str, number: int, size: int) -> str:
    digest = hashlib.sha1(f"blob {size}\0".encode())
    for chunk in part_bytes(repository, number, size):
        digest.update(chunk)
    return digest.hexdigest()


def commit_message(number: int, commits: int) -> str:
    return f"Add dataset part {number} of {commits}"


def expected_commits(repository: str, identity: str, commits: int = COMMITS,
                     size: int = PART_SIZE) -> list[str]:
    """The SHA of each commit, oldest first, as Git computes them."""
    entries, shas, parent = [], [], None
    for number in range(1, commits + 1):
        entries.append(f"{MODE} {part_name(number)}".encode() + b"\0"
                       + bytes.fromhex(blob_sha(repository, number, size)))
        # Tree entries sort by name; the zero-padded names sort in order.
        tree = git_object("tree", b"".join(entries))
        lines = [f"tree {tree}"]
        if parent:
            lines.append(f"parent {parent}")
        lines += [f"author {identity} {DATE} +0000", f"committer {identity} {DATE} +0000", "",
                  commit_message(number, commits), ""]
        parent = git_object("commit", "\n".join(lines).encode())
        shas.append(parent)
    return shas


class Gitea:
    """The fixture user's view of one endpoint. Tests replace it."""

    def __init__(self, url: str, user: str, password: str):
        self.url, self.user, self.password = url.rstrip("/"), user, password
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}

    def api(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict, object]:
        request = urllib.request.Request(
            f"{self.url}/api/v1{path}", method=method, headers={**self.headers, "Content-Type": "application/json"},
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                text = response.read().decode()
                return response.status, dict(response.headers), json.loads(text) if text else None
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), None
        except (urllib.error.URLError, OSError) as error:
            raise Failed(f"{method} {path}: {error}") from None

    def clone_url(self, repository: str) -> str:
        return f"{self.url}/{self.user}/{repository}.git"

    def git(self, *args: str, cwd: Path | None = None) -> str:
        completed = subprocess.run(
            [str(GIT_WITH_PASSWORD), *args], cwd=cwd, input=self.password + "\n", text=True,
            capture_output=True, env={**os.environ, "GIT_STDIN_USERNAME": self.user})
        if completed.returncode != 0:
            raise Failed(f"git {args[0]} exited {completed.returncode}: {completed.stderr.strip()}")
        return completed.stdout

    def head(self, repository: str, branch: str) -> str:
        """The branch head, or empty when the repository has no such branch."""
        listing = self.git("ls-remote", self.clone_url(repository), f"refs/heads/{branch}")
        return listing.split()[0] if listing.strip() else ""

    def issue_count(self, repository: str) -> int:
        status, headers, _ = self.api("GET", f"/repos/{self.user}/{repository}/issues?state=all&type=issues&limit=1")
        if status != 200:
            raise Failed(f"listing the issues of {repository} returned {status}")
        count = {key.lower(): value for key, value in headers.items()}.get("x-total-count")
        if count is None:
            raise Failed(f"the issue listing of {repository} has no X-Total-Count header")
        return int(count)


def fixture_password(age_key_file: str) -> str:
    completed = subprocess.run(
        ["sops", "--decrypt", "--extract", '["fixture_password"]', str(FIXTURE_SECRETS)],
        capture_output=True, text=True, env={**os.environ, "SOPS_AGE_KEY_FILE": age_key_file})
    if completed.returncode != 0 or not completed.stdout.strip():
        raise Failed("the fixture password does not decrypt with the SOPS age key")
    return completed.stdout.strip()


def build_repository(directory: Path, repository: str, identity: str, branch: str,
                     commits: int, size: int) -> list[str]:
    """Commit every part in a new local repository and return the commit SHAs."""
    name, email = identity.removesuffix(">").split(" <")
    date = f"{DATE} +0000"
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": date,
           "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email, "GIT_COMMITTER_DATE": date}

    def git(*args):
        return subprocess.run(["git", *args], cwd=directory, env=env, check=True,
                              capture_output=True, text=True).stdout.strip()

    git("init", "--quiet", "--initial-branch", branch)
    shas = []
    for number in range(1, commits + 1):
        with (directory / part_name(number)).open("wb") as part:
            for chunk in part_bytes(repository, number, size):
                part.write(chunk)
        git("add", part_name(number))
        git("commit", "--quiet", "--message", commit_message(number, commits))
        shas.append(git("rev-parse", "HEAD"))
    return shas


def issue_body(number: int) -> str:
    generator = random.Random(f"{PREFIX}-issue/{number}")
    words = ["backup", "restore", "region", "drill", "commit", "volume", "snapshot", "cluster"]
    return " ".join(generator.choice(words) for _ in range(150))


def load(gitea: Gitea, fixtures: dict, repositories: list[str], commits: int, size: int, issues: int) -> None:
    identity = fixtures["fixture_commit"]["author"]
    branch = fixtures["fixture_branch"]
    for repository in repositories:
        expected = expected_commits(repository, identity, commits, size)
        status, _, _ = gitea.api("GET", f"/repos/{gitea.user}/{repository}")
        if status == 404:
            status, _, _ = gitea.api("POST", "/user/repos", {
                "name": repository, "private": True, "default_branch": branch,
                "description": "Drill dataset; see decision 0015."})
            if status != 201:
                raise Failed(f"creating {repository} returned {status}")
            print(f"{repository}: created")
        elif status != 200:
            raise Failed(f"reading {repository} returned {status}")
        head = gitea.head(repository, branch)
        if head == expected[-1]:
            print(f"{repository}: complete at {head[:12]}")
            continue
        if head and head not in expected:
            raise Failed(f"{repository} has {branch} at {head[:12]}, which is not a dataset commit")
        pushed = expected.index(head) + 1 if head else 0
        with tempfile.TemporaryDirectory(prefix="dataset-") as directory:
            built = build_repository(Path(directory), repository, identity, branch, commits, size)
            if built != expected:
                raise Failed(f"{repository}: the local commits do not match the computed SHAs")
            # Each push sends one part, oldest first.
            for number in range(pushed, commits):
                gitea.git("push", "--quiet", gitea.clone_url(repository),
                          f"{built[number]}:refs/heads/{branch}", cwd=Path(directory))
                print(f"{repository}: pushed part {number + 1} of {commits}", flush=True)
    first = repositories[0]
    count = gitea.issue_count(first)
    for number in range(count + 1, issues + 1):
        status, _, _ = gitea.api("POST", f"/repos/{gitea.user}/{first}/issues", {
            "title": f"Dataset issue {number}", "body": issue_body(number)})
        if status != 201:
            raise Failed(f"creating issue {number} in {first} returned {status}")
    print(f"{first}: {max(count, issues)} issues")


def check(gitea: Gitea, fixtures: dict, repositories: list[str], commits: int, size: int, issues: int) -> None:
    identity = fixtures["fixture_commit"]["author"]
    branch = fixtures["fixture_branch"]
    problems = []
    for repository in repositories:
        expected = expected_commits(repository, identity, commits, size)[-1]
        head = gitea.head(repository, branch)
        if head != expected:
            problems.append(f"{repository} has {branch} at {head[:12] or 'nothing'}, expected {expected[:12]}")
    count = gitea.issue_count(repositories[0])
    if count != issues:
        problems.append(f"{repositories[0]} has {count} issues, expected {issues}")
    if problems:
        raise Failed("; ".join(problems))
    print(f"dataset: {len(repositories)} repositories at the expected head, {count} issues in {repositories[0]}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("load", "check"))
    parser.add_argument("--host", help="Gitea host; the primary's public name by default.")
    parser.add_argument("--url", help="Full Gitea URL, which overrides --host.")
    parser.add_argument("--age-key-file", default=os.environ.get(
        "FLUX_AGE_KEY_FILE", str(Path.home() / ".config/k8s-dr/age.agekey")))
    return parser.parse_args(argv)


def main(argv=None, gitea: Gitea | None = None, sizes: tuple[int, int, int, int] | None = None) -> int:
    args = parse_args(argv)
    fixtures = yaml.safe_load(FIXTURES.read_text())
    count, commits, size, issues = sizes or (REPOSITORIES, COMMITS, PART_SIZE, ISSUES)
    try:
        if gitea is None:
            host = args.host or yaml.safe_load(SETTINGS.read_text())["data"]["git_host"]
            gitea = Gitea(args.url or f"https://{host}", fixtures["fixture_user"],
                          fixture_password(args.age_key_file))
        command = load if args.command == "load" else check
        command(gitea, fixtures, repository_names(count), commits, size, issues)
    except Failed as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
