#!/usr/bin/env python3
"""Build the private Ansible inventory from Terraform outputs."""

import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import tempfile


POD_CIDR = ipaddress.ip_network("192.168.0.0/16")
SERVICE_CIDR = ipaddress.ip_network("10.96.0.0/12")
LOCAL_PORTS = {"control-plane": 2201, "worker": 2202}
REQUIRED_OUTPUTS = (
    "project_id",
    "zone",
    "subnet_cidr",
    "instance_names",
    "internal_ips",
)


def _output_value(outputs: dict, name: str):
    output = outputs.get(name)
    if not isinstance(output, dict) or "value" not in output:
        raise ValueError(f"missing Terraform output value: {name}")
    return output["value"]


def _role_value(values: object, output_name: str, role: str) -> str:
    if not isinstance(values, dict):
        raise ValueError(f"Terraform output {output_name} must be a role map")
    value = values.get(role)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Terraform output {output_name} is missing role: {role}")
    return value


def build_inventory(outputs: dict, ssh_user: str, ssh_key: str) -> dict:
    """Validate Terraform output JSON and map it to an Ansible inventory."""
    missing = [name for name in REQUIRED_OUTPUTS if name not in outputs]
    if missing:
        raise ValueError(f"missing Terraform outputs: {', '.join(missing)}")
    if not isinstance(ssh_user, str) or not ssh_user.strip():
        raise ValueError("SSH user must not be empty")
    if not isinstance(ssh_key, str) or not ssh_key.strip():
        raise ValueError("SSH private key path must not be empty")

    subnet_value = _output_value(outputs, "subnet_cidr")
    try:
        vpc = ipaddress.ip_network(subnet_value, strict=True)
    except (TypeError, ValueError) as error:
        raise ValueError("subnet_cidr must be a valid network CIDR") from error

    for left, right in ((vpc, POD_CIDR), (vpc, SERVICE_CIDR), (POD_CIDR, SERVICE_CIDR)):
        if left.overlaps(right):
            raise ValueError(f"cluster and VPC networks overlap: {left} and {right}")

    project = _output_value(outputs, "project_id")
    zone = _output_value(outputs, "zone")
    if not isinstance(project, str) or not project.strip():
        raise ValueError("project_id must be a nonempty string")
    if not isinstance(zone, str) or not zone.strip():
        raise ValueError("zone must be a nonempty string")

    names = _output_value(outputs, "instance_names")
    addresses = _output_value(outputs, "internal_ips")
    hosts = {}
    for role in LOCAL_PORTS:
        name = _role_value(names, "instance_names", role)
        address_text = _role_value(addresses, "internal_ips", role)
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError as error:
            raise ValueError(f"internal_ips contains an invalid {role} address") from error
        if address not in vpc:
            raise ValueError(f"{role} address is outside subnet_cidr")
        hosts[role] = {
            "ansible_host": "127.0.0.1",
            "ansible_port": LOCAL_PORTS[role],
            "node_internal_ip": str(address),
            "gcp_instance_name": name,
            "gcp_project": project,
            "gcp_zone": zone,
        }

    if hosts["control-plane"]["node_internal_ip"] == hosts["worker"]["node_internal_ip"]:
        raise ValueError("control-plane and worker addresses must be distinct")

    return {
        "all": {
            "vars": {
                "ansible_user": ssh_user.strip(),
                "ansible_ssh_private_key_file": ssh_key,
            },
            "children": {
                "kube_control_plane": {"hosts": {"control-plane": hosts["control-plane"]}},
                "kube_workers": {"hosts": {"worker": hosts["worker"]}},
                "kube_cluster": {
                    "children": {
                        "kube_control_plane": {},
                        "kube_workers": {},
                    }
                },
            },
        }
    }


def write_inventory(path: Path, inventory: dict) -> None:
    """Write inventory atomically with private directory and file permissions."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(inventory, temporary_file, indent=2, sort_keys=True)
            temporary_file.write("\n")
        temporary_path.chmod(0o600)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terraform-dir", default="infra/primary")
    parser.add_argument("--ssh-user", required=True)
    parser.add_argument("--ssh-key", required=True, type=Path)
    parser.add_argument(
        "--output",
        default=Path("ansible/inventory/generated/hosts.json"),
        type=Path,
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.ssh_key.is_file():
        raise ValueError(f"SSH private key is not a regular file: {args.ssh_key}")
    completed = subprocess.run(
        ["terraform", f"-chdir={args.terraform_dir}", "output", "-json"],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        outputs = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("Terraform output was not valid JSON") from error
    inventory = build_inventory(outputs, args.ssh_user, str(args.ssh_key))
    write_inventory(args.output, inventory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
