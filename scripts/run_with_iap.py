#!/usr/bin/env python3
"""Run a command while owning verified Google Cloud IAP SSH tunnels."""

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time


DEFAULT_INVENTORY = Path("ansible/inventory/generated/hosts.json")
REQUIRED_HOSTS = {
    "kube_control_plane": "control-plane",
    "kube_workers": "worker",
}


@dataclass(frozen=True)
class Target:
    role: str
    instance: str
    project: str
    zone: str
    local_port: int


def load_targets(path: Path) -> list[Target]:
    """Load and validate the IAP metadata boundary from generated inventory."""
    try:
        inventory = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"inventory must be a readable JSON file: {path}") from error

    try:
        children = inventory["all"]["children"]
    except (KeyError, TypeError) as error:
        raise ValueError("inventory is missing all.children") from error

    targets = []
    for group, role in REQUIRED_HOSTS.items():
        try:
            hosts = children[group]["hosts"]
        except (KeyError, TypeError) as error:
            raise ValueError(f"inventory is missing host group: {group}") from error
        if not isinstance(hosts, dict) or set(hosts) != {role}:
            raise ValueError(f"inventory group {group} must contain exactly host {role}")
        metadata = hosts[role]
        if not isinstance(metadata, dict):
            raise ValueError(f"inventory metadata for {role} must be an object")

        required = (
            "ansible_host",
            "ansible_port",
            "gcp_instance_name",
            "gcp_project",
            "gcp_zone",
        )
        missing = [name for name in required if name not in metadata]
        if missing:
            raise ValueError(f"inventory host {role} is missing: {', '.join(missing)}")
        if metadata["ansible_host"] != "127.0.0.1":
            raise ValueError(f"inventory host {role} must use the IPv4 loopback address")
        port = metadata["ansible_port"]
        if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
            raise ValueError(f"inventory host {role} port must be between 1024 and 65535")

        text_fields = {
            "gcp_instance_name": "instance",
            "gcp_project": "project",
            "gcp_zone": "zone",
        }
        values = {}
        for inventory_name, target_name in text_fields.items():
            value = metadata[inventory_name]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"inventory host {role} has invalid {inventory_name}")
            values[target_name] = value
        targets.append(Target(role=role, local_port=port, **values))

    return targets


def ensure_ports_available(targets: list[Target]) -> None:
    """Fail before starting any child when a requested local port is unavailable."""
    ports = [target.local_port for target in targets]
    for port in ports:
        if ports.count(port) > 1:
            raise ValueError(f"duplicate local port: {port}")
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as error:
                raise RuntimeError(f"local port {port} is already in use") from error


def wait_for_tunnel(process, port: int, timeout: float = 30.0) -> None:
    """Wait until a tunnel accepts local TCP connections or exits."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            detail = process.stderr.read().strip() if process.stderr is not None else ""
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(f"IAP tunnel exited with status {return_code}{suffix}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"IAP tunnel on local port {port} did not become ready")


def write_known_hosts(targets: list[Target], path: Path) -> None:
    """Scan the live tunnel endpoints and create a private known_hosts file."""
    keys = []
    for target in targets:
        completed = subprocess.run(
            ["ssh-keyscan", "-p", str(target.local_port), "127.0.0.1"],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            raise RuntimeError(f"host key scan failed for {target.role}")
        keys.append(completed.stdout.rstrip())

    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as known_hosts:
            known_hosts.write("\n".join(keys) + "\n")
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    path.chmod(0o600)


def _start_tunnel(target: Target):
    args = [
        "gcloud",
        "compute",
        "start-iap-tunnel",
        target.instance,
        "22",
        f"--local-host-port=127.0.0.1:{target.local_port}",
        f"--project={target.project}",
        f"--zone={target.zone}",
        "--quiet",
    ]
    return subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def _stop_tunnels(processes) -> None:
    active = []
    for process in processes:
        if process.poll() is None:
            process.terminate()
            active.append(process)
    for process in active:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def run_with_tunnels(
    targets: list[Target],
    command: list[str],
    known_hosts_path: Path = DEFAULT_INVENTORY.with_name("known_hosts"),
) -> int:
    """Run a command with live IAP tunnels and always reap the children."""
    if not command:
        raise ValueError("Ansible command must not be empty")
    ensure_ports_available(targets)
    processes = []
    try:
        for target in targets:
            processes.append(_start_tunnel(target))
        for process, target in zip(processes, targets, strict=True):
            wait_for_tunnel(process, target.local_port)
        write_known_hosts(targets, known_hosts_path)
        environment = os.environ.copy()
        known_hosts_option = shlex.quote(str(Path(known_hosts_path).resolve()))
        environment["ANSIBLE_SSH_COMMON_ARGS"] = (
            "-o StrictHostKeyChecking=yes "
            f"-o UserKnownHostsFile={known_hosts_option}"
        )
        completed = subprocess.run(command, check=False, env=environment)
        return completed.returncode
    finally:
        _stop_tunnels(processes)


def parse_cli(argv: list[str]):
    if "--" not in argv:
        raise ValueError("separate the Ansible command with --")
    separator = argv.index("--")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    args = parser.parse_args(argv[:separator])
    command = argv[separator + 1 :]
    if not command:
        raise ValueError("Ansible command must not be empty")
    return args, command


def main(argv=None) -> int:
    args, command = parse_cli(list(sys.argv[1:] if argv is None else argv))
    targets = load_targets(args.inventory)
    return run_with_tunnels(
        targets,
        command,
        known_hosts_path=args.inventory.with_name("known_hosts"),
    )


if __name__ == "__main__":
    raise SystemExit(main())
