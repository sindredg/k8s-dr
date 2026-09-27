"""Create the recovery fixture commit in a new local repository.

The commit has fixed content, identity, and dates, so its SHA is known before
the push. The create playbook pushes the result; tests/test_fixtures.py checks
that the SHA in recovery/fixtures.yaml still matches.
"""

import argparse
import os
from pathlib import Path
import subprocess
import sys

import yaml


def build(fixtures_file, directory):
    fixtures = yaml.safe_load(Path(fixtures_file).read_text())
    commit = fixtures["fixture_commit"]
    name, email = commit["author"].removesuffix(">").split(" <")
    env = {
        **os.environ,
        # Ignore the operator's Git configuration, such as signing or hooks.
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_AUTHOR_DATE": commit["date"],
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
        "GIT_COMMITTER_DATE": commit["date"],
    }

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=directory, env=env, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "--quiet", "--initial-branch", fixtures["fixture_branch"])
    Path(directory, commit["path"]).write_text(commit["content"])
    git("add", commit["path"])
    git("commit", "--quiet", "--message", commit["message"])
    return git("rev-parse", "HEAD")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", required=True, help="Path to recovery/fixtures.yaml")
    parser.add_argument("--directory", required=True, help="Empty directory for the new repository")
    args = parser.parse_args()
    print(build(args.fixtures, args.directory))


if __name__ == "__main__":
    sys.exit(main())
