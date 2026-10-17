#!/usr/bin/env python3
"""Prepare a GitHub Actions runner for make recover, and set up the pipeline.

The recovery workflow runs these commands on a fresh runner:

  configure  write backend.hcl and terraform.tfvars from the environment
  keys       read the SOPS and backup age keys from Secret Manager
  ssh-key    register an SSH key with OS Login for the IAP connections
  pull       copy the run state of an earlier workflow run from the bucket
  push       copy the run state back, so a later run resumes

`setup` runs once on an operator machine after infra/shared is applied. It
creates the GitHub environment, stores the identifiers as its secrets, and
adds the age keys to Secret Manager. No value is printed. See decision 0013.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

ROOTS = ("shared", "primary", "recovery")
# Roots that a recovery plans. The primary root is only read for outputs,
# which needs its backend but no variables.
PLANNED_ROOTS = ("shared", "recovery")
# Each placeholder of a terraform.tfvars.example, and the environment
# variable that replaces it on the runner. A new placeholder without an
# entry fails configure.
VARIABLES = {
    "project_id": "GCP_PROJECT_ID",
    "backup_bucket_name": "BACKUP_BUCKET",
    "state_bucket_name": "STATE_BUCKET",
    "admin_member": "ADMIN_MEMBER",
    "recovery_reader_member": "RECOVERY_READER_MEMBER",
    "recovery_subnet_cidr": "RECOVERY_SUBNET_CIDR",
}
# Values the workflow logs must not show. RECOVERY_SUBNET_CIDR is a private
# range and is stored as a plain environment variable.
SECRETS = (
    "GCP_PROJECT_ID", "GCP_WORKLOAD_IDENTITY_PROVIDER", "GCP_SERVICE_ACCOUNT",
    "STATE_BUCKET", "BACKUP_BUCKET", "ADMIN_MEMBER", "RECOVERY_READER_MEMBER",
)
ENVIRONMENT = "recovery"
# Same IDs as local.age_key_secrets in infra/shared/pipeline.tf.
AGE_KEY_SECRETS = {"sops": "k8sdr-sops-age-key", "backup": "k8sdr-backup-age-key"}
STATE_PREFIX = "pipeline/drill"
# The lock only guards one machine; the workflow's concurrency group guards
# the runs.
NOT_PUSHED = ("recover.lock",)
SSH_KEY = Path.home() / ".ssh/google_compute_engine"
# Longer than a recovery, so the key expires on its own afterwards.
SSH_KEY_TTL = "6h"
PLACEHOLDER = re.compile(r'^(\s*)([a-z_]+)(\s*=\s*)"[^"]*REPLACE_WITH_[^"]*"(.*)$')
ASSIGNMENT = re.compile(r'^\s*([a-z_]+)\s*=\s*"([^"]*)"')


class Failed(Exception):
    """A step failed; the message names it without any value."""


class System:
    """The commands the pipeline runs. Tests replace it."""

    def call(self, argv: list[str], *, stdin: str | None = None, capture: bool = False):
        # Arguments carry identifiers, never keys, and the workflow masks them.
        print(f"$ {shlex.join(argv)}", flush=True)
        return subprocess.run(argv, input=stdin, text=True, capture_output=capture)


def run(system: System, argv: list[str], *, stdin: str | None = None, capture: bool = False) -> str:
    completed = system.call(argv, stdin=stdin, capture=capture)
    if completed.returncode != 0:
        raise Failed(f"{shlex.join(argv[:4])} exited {completed.returncode}")
    return completed.stdout or ""


def require(environ: dict, name: str) -> str:
    value = environ.get(name, "")
    if not value:
        raise Failed(f"{name} is not set")
    return value


def private_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(text)
    path.chmod(0o600)


def render_example(example: str, environ: dict) -> str:
    """Replace each placeholder of a tfvars example with its variable."""
    lines = []
    for line in example.splitlines():
        match = PLACEHOLDER.match(line)
        if match:
            indent, key, equals, rest = match.groups()
            if key not in VARIABLES:
                raise Failed(f"no environment variable is mapped to {key}")
            value = require(environ, VARIABLES[key])
            line = f'{indent}{key}{equals}{json.dumps(value)}{rest}'
        lines.append(line)
    return "\n".join(lines) + "\n"


def configure(root_dir: Path, environ: dict) -> None:
    bucket = require(environ, "STATE_BUCKET")
    for root in ROOTS:
        private_file(root_dir / f"infra/{root}/backend.hcl",
                     f'bucket = {json.dumps(bucket)}\nprefix = "{root}"\n')
    for root in PLANNED_ROOTS:
        example = (root_dir / f"infra/{root}/terraform.tfvars.example").read_text()
        private_file(root_dir / f"infra/{root}/terraform.tfvars", render_example(example, environ))
    # The provider name holds the project number, which no secret masks.
    provider = environ.get("GCP_WORKLOAD_IDENTITY_PROVIDER", "")
    number = re.match(r"projects/(\d+)/", provider)
    if number and environ.get("GITHUB_ACTIONS") == "true":
        print(f"::add-mask::{number.group(1)}")
    print("wrote backend.hcl for " + ", ".join(ROOTS) + " and terraform.tfvars for " + ", ".join(PLANNED_ROOTS))


def keys(system: System, environ: dict, sops_file: Path, backup_file: Path) -> None:
    project = require(environ, "GCP_PROJECT_ID")
    for name, path in (("sops", sops_file), ("backup", backup_file)):
        private_file(path, "")
        run(system, ["gcloud", "secrets", "versions", "access", "latest",
                     f"--secret={AGE_KEY_SECRETS[name]}", f"--project={project}", f"--out-file={path}"])
        if "AGE-SECRET-KEY-" not in path.read_text():
            raise Failed(f"secret {AGE_KEY_SECRETS[name]} does not hold an age private key")
        path.chmod(0o600)


def ssh_key(system: System, key: Path = SSH_KEY) -> None:
    if not key.exists():
        key.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        run(system, ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)])
    run(system, ["gcloud", "compute", "os-login", "ssh-keys", "add",
                 f"--key-file={key}.pub", f"--ttl={SSH_KEY_TTL}"])


def state_url(environ: dict) -> str:
    return f"gs://{require(environ, 'STATE_BUCKET')}/{STATE_PREFIX}"


def pull(system: System, environ: dict, drill_dir: Path) -> None:
    drill_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    listing = system.call(["gcloud", "storage", "ls", f"{state_url(environ)}/"], capture=True)
    if listing.returncode != 0:
        # Only an empty prefix is a first run; any other error stops it.
        if "matched no objects" in (listing.stderr or ""):
            print("no earlier run state; the run starts at preflight")
            return
        raise Failed(f"cannot list {STATE_PREFIX} in the state bucket")
    run(system, ["gcloud", "storage", "rsync", "--recursive", state_url(environ), str(drill_dir)])


def push(system: System, environ: dict, drill_dir: Path) -> None:
    if not drill_dir.is_dir():
        print("no run state to save")
        return
    exclude = "|".join(re.escape(name) + "$" for name in NOT_PUSHED)
    run(system, ["gcloud", "storage", "rsync", "--recursive", f"--exclude={exclude}",
                 str(drill_dir), state_url(environ)])


# Setup, on an operator machine.

def read_assignments(path: Path) -> dict:
    values = {}
    for line in path.read_text().splitlines():
        match = ASSIGNMENT.match(line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def terraform_output(system: System, root: str, name: str) -> str:
    value = run(system, ["terraform", f"-chdir=infra/{root}", "output", "-raw", name], capture=True).strip()
    if not value:
        raise Failed(f"infra/{root} has an empty output {name}")
    return value


def setup_values(system: System, root_dir: Path) -> tuple[dict, dict]:
    shared = read_assignments(root_dir / "infra/shared/terraform.tfvars")
    recovery = read_assignments(root_dir / "infra/recovery/terraform.tfvars")
    backend = read_assignments(root_dir / "infra/shared/backend.hcl")
    secrets = {
        "GCP_PROJECT_ID": shared.get("project_id", ""),
        "GCP_WORKLOAD_IDENTITY_PROVIDER": terraform_output(system, "shared", "pipeline_workload_identity_provider"),
        "GCP_SERVICE_ACCOUNT": terraform_output(system, "shared", "pipeline_service_account"),
        "STATE_BUCKET": backend.get("bucket", ""),
        "BACKUP_BUCKET": terraform_output(system, "shared", "backup_bucket_name"),
        "ADMIN_MEMBER": shared.get("admin_member", ""),
        "RECOVERY_READER_MEMBER": shared.get("recovery_reader_member", ""),
    }
    variables = {"RECOVERY_SUBNET_CIDR": recovery.get("recovery_subnet_cidr", "")}
    for name, value in {**secrets, **variables}.items():
        if not value or value.startswith("REPLACE_WITH_"):
            raise Failed(f"no local value for {name}")
    return secrets, variables


def setup_environment(system: System, repository: str) -> None:
    """Create the environment, deployable from main only."""
    policy = {"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}}
    run(system, ["gh", "api", "--method", "PUT", f"repos/{repository}/environments/{ENVIRONMENT}",
                 "--input", "-"], stdin=json.dumps(policy), capture=True)
    existing = json.loads(run(system, [
        "gh", "api", f"repos/{repository}/environments/{ENVIRONMENT}/deployment-branch-policies",
    ], capture=True))
    names = {(entry.get("type"), entry.get("name")) for entry in existing.get("branch_policies", [])}
    extra = names - {("branch", "main")}
    if extra:
        raise Failed(f"the {ENVIRONMENT} environment allows more than main: {sorted(extra)}")
    if ("branch", "main") not in names:
        run(system, ["gh", "api", "--method", "POST",
                     f"repos/{repository}/environments/{ENVIRONMENT}/deployment-branch-policies",
                     "-f", "name=main", "-f", "type=branch"], capture=True)


def setup_keys(system: System, project: str, files: dict) -> None:
    """Add each key once. An existing version must hold the same key."""
    for name, secret in AGE_KEY_SECRETS.items():
        local = files[name].read_text()
        if "AGE-SECRET-KEY-" not in local:
            raise Failed(f"{files[name]} is not an age private key")
        versions = run(system, ["gcloud", "secrets", "versions", "list", secret, f"--project={project}",
                                "--filter=state:enabled", "--format=value(name)"], capture=True)
        if not versions.strip():
            run(system, ["gcloud", "secrets", "versions", "add", secret, f"--project={project}",
                         f"--data-file={files[name]}"], capture=True)
            continue
        stored = run(system, ["gcloud", "secrets", "versions", "access", "latest", f"--secret={secret}",
                              f"--project={project}"], capture=True)
        if stored.strip() != local.strip():
            raise Failed(f"secret {secret} holds a different key than {files[name]}; "
                         "add a new version by hand if the key was rotated")


def setup(system: System, root_dir: Path, repository: str, files: dict) -> None:
    secrets, variables = setup_values(system, root_dir)
    setup_environment(system, repository)
    for name, value in secrets.items():
        # The value goes on stdin, never in an argument.
        run(system, ["gh", "secret", "set", name, "--env", ENVIRONMENT, "--repo", repository],
            stdin=value, capture=True)
    for name, value in variables.items():
        run(system, ["gh", "variable", "set", name, "--env", ENVIRONMENT, "--repo", repository],
            stdin=value, capture=True)
    setup_keys(system, secrets["GCP_PROJECT_ID"], files)
    print(f"environment {ENVIRONMENT}: {len(secrets)} secrets and {len(variables)} variable set; "
          "both age keys are in Secret Manager")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("."))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("configure", help="Write the Terraform backend and variable files.")
    fetch = commands.add_parser("keys", help="Write both age keys from Secret Manager.")
    fetch.add_argument("--sops-key", type=Path, required=True)
    fetch.add_argument("--backup-key", type=Path, required=True)
    commands.add_parser("ssh-key", help="Register an SSH key with OS Login.")
    for name in ("pull", "push"):
        sync = commands.add_parser(name, help=f"{name.capitalize()} the run state.")
        sync.add_argument("--drill-dir", type=Path, required=True)
    first = commands.add_parser("setup", help="Create the GitHub environment and store the keys, once.")
    first.add_argument("--repository", default="sindredg/k8s-dr")
    first.add_argument("--sops-key", type=Path, default=Path.home() / ".config/k8s-dr/age.agekey")
    first.add_argument("--backup-key", type=Path, default=Path.home() / ".config/k8s-dr/backup.agekey")
    return parser.parse_args(argv)


def main(argv=None, system: System | None = None, environ: dict | None = None) -> int:
    args = parse_args(argv)
    system = system or System()
    environ = os.environ if environ is None else environ
    try:
        if args.command == "configure":
            configure(args.root, environ)
        elif args.command == "keys":
            keys(system, environ, args.sops_key, args.backup_key)
        elif args.command == "ssh-key":
            ssh_key(system)
        elif args.command == "pull":
            pull(system, environ, args.drill_dir)
        elif args.command == "push":
            push(system, environ, args.drill_dir)
        else:
            setup(system, args.root, args.repository, {"sops": args.sops_key, "backup": args.backup_key})
    except (Failed, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
