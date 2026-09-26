#!/usr/bin/env python3
"""Check that every pinned bootstrap artifact still resolves upstream.

A recovery bootstrap downloads these artifacts months after they were
pinned. Upstream repositories can drop a version (the Ubuntu archive keeps
only the newest build of a package), so a pin that installed yesterday can
fail during a drill. Run this in CI on a schedule to find out first.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
import urllib.error
import urllib.request

import yaml

DEFAULT_VARIABLES = Path("ansible/playbooks/group_vars/all.yml")
DOCKER_PACKAGES_URL = "https://download.docker.com/linux/ubuntu/dists/noble/stable/binary-amd64/Packages"
KUBERNETES_PACKAGES = ("kubelet", "kubeadm", "kubectl")
TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class Result:
    name: str
    ok: bool
    detail: str


def debian_package_versions(index_text: str, package: str) -> set[str]:
    """Return every version of a package listed in a Debian Packages index."""
    versions = set()
    for stanza in index_text.split("\n\n"):
        fields = {}
        for line in stanza.splitlines():
            key, separator, value = line.partition(": ")
            if separator and not line.startswith(" "):
                fields[key] = value.strip()
        if fields.get("Package") == package and "Version" in fields:
            versions.add(fields["Version"])
    return versions


def chart_versions(index_text: str, chart: str) -> set[str]:
    """Return every version of a chart listed in a Helm repository index."""
    index = yaml.safe_load(index_text) or {}
    entries = index.get("entries", {}).get(chart, [])
    return {str(entry.get("version")) for entry in entries}


def artifact_urls(pins: dict) -> dict[str, str]:
    """Map a readable name to each pinned file URL that must exist."""
    calico = f"https://raw.githubusercontent.com/projectcalico/calico/v{pins['calico_version']}"
    local_path = (
        "https://raw.githubusercontent.com/rancher/local-path-provisioner/"
        f"v{pins['local_path_provisioner_version']}"
    )
    helm = f"https://get.helm.sh/helm-v{pins['helm_version']}-linux-amd64.tar.gz"
    return {
        "Calico operator CRDs": f"{calico}/manifests/operator-crds.yaml",
        "Calico operator": f"{calico}/manifests/tigera-operator.yaml",
        "Gateway API Standard CRDs": (
            "https://github.com/kubernetes-sigs/gateway-api/releases/download/"
            f"v{pins['gateway_api_version']}/standard-install.yaml"
        ),
        "Local Path Provisioner": f"{local_path}/deploy/local-path-storage.yaml",
        "Helm archive": helm,
        "Helm checksum": f"{helm}.sha256sum",
    }


def _fetch(url: str, method: str = "GET") -> bytes:
    request = urllib.request.Request(url, method=method, headers={"User-Agent": "k8s-dr-pin-check"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read()


def check_url(name: str, url: str, fetch=_fetch) -> Result:
    try:
        fetch(url, "HEAD")
    except (urllib.error.URLError, TimeoutError) as error:
        return Result(name, False, f"{url}: {error}")
    return Result(name, True, url)


def check_kubernetes_packages(pins: dict, fetch=_fetch) -> Result:
    url = f"https://pkgs.k8s.io/core:/stable:/{pins['kubernetes_minor']}/deb/Packages"
    wanted = pins["kubernetes_deb_version"]
    try:
        index = fetch(url).decode()
    except (urllib.error.URLError, TimeoutError) as error:
        return Result("Kubernetes packages", False, f"{url}: {error}")
    missing = [
        package
        for package in KUBERNETES_PACKAGES
        if wanted not in debian_package_versions(index, package)
    ]
    if missing:
        return Result("Kubernetes packages", False, f"{wanted} not published for {', '.join(missing)}")
    return Result("Kubernetes packages", True, f"{wanted} in {url}")


def check_containerd(pins: dict, fetch=_fetch) -> Result:
    wanted = pins["containerd_deb_version"]
    try:
        index = fetch(DOCKER_PACKAGES_URL).decode()
    except (urllib.error.URLError, TimeoutError) as error:
        return Result("containerd", False, f"{DOCKER_PACKAGES_URL}: {error}")
    versions = debian_package_versions(index, "containerd.io")
    if wanted not in versions:
        available = ", ".join(sorted(versions)) or "none"
        return Result("containerd", False, f"containerd.io {wanted} is not published; available: {available}")
    return Result("containerd", True, f"containerd.io {wanted} in {DOCKER_PACKAGES_URL}")


def check_traefik_chart(pins: dict, fetch=_fetch) -> Result:
    url = "https://traefik.github.io/charts/index.yaml"
    wanted = pins["traefik_chart_version"]
    try:
        versions = chart_versions(fetch(url).decode(), "traefik")
    except (urllib.error.URLError, TimeoutError) as error:
        return Result("Traefik chart", False, f"{url}: {error}")
    if wanted not in versions:
        return Result("Traefik chart", False, f"{wanted} not in {url}")
    return Result("Traefik chart", True, f"{wanted} in {url}")


def run_checks(pins: dict, fetch=_fetch) -> list[Result]:
    results = [
        check_kubernetes_packages(pins, fetch),
        check_containerd(pins, fetch),
        check_traefik_chart(pins, fetch),
    ]
    results += [check_url(name, url, fetch) for name, url in artifact_urls(pins).items()]
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variables", type=Path, default=DEFAULT_VARIABLES)
    args = parser.parse_args(argv)
    pins = yaml.safe_load(args.variables.read_text())
    results = run_checks(pins)
    for result in results:
        print(f"{'ok  ' if result.ok else 'FAIL'} {result.name}: {result.detail}")
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
