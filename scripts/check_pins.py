#!/usr/bin/env python3
"""Check that every pinned bootstrap artifact still resolves upstream.

A recovery bootstrap downloads these artifacts months after they were
pinned. Upstream repositories can drop a version (the Ubuntu archive keeps
only the newest build of a package), so a pin that installed yesterday can
fail during a drill. Run this in CI on a schedule to find out first.
"""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

import yaml

DEFAULT_VARIABLES = Path("ansible/playbooks/group_vars/all.yml")
DEFAULT_DEPLOY = Path("deploy")
DOCKER_PACKAGES_URL = "https://download.docker.com/linux/ubuntu/dists/noble/stable/binary-amd64/Packages"
KUBERNETES_PACKAGES = ("kubelet", "kubeadm", "kubectl")
TRAEFIK_CHARTS_URL = "https://traefik.github.io/charts"
TIMEOUT_SECONDS = 60
IMAGE_DIGEST = re.compile(r"image:\s*[\"']?([^\s\"']+@sha256:[0-9a-f]{64})")
MANIFEST_TYPES = ", ".join((
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
))


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
        "Flux release manifest": (
            "https://github.com/fluxcd/flux2/releases/download/"
            f"v{pins['flux_version']}/install.yaml"
        ),
        "Helm archive": helm,
        "Helm checksum": f"{helm}.sha256sum",
    }


def _fetch(url: str, method: str = "GET", headers: dict | None = None) -> bytes:
    request = urllib.request.Request(
        url, method=method, headers={"User-Agent": "k8s-dr-pin-check", **(headers or {})}
    )
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


def flux_chart_pins(deploy_dir: Path) -> list[tuple[str, str, str, str]]:
    """Return (release, repository URL, chart, version) for each Flux HelmRelease."""
    documents = [
        document
        for path in sorted(deploy_dir.rglob("*.yaml"))
        for document in yaml.safe_load_all(path.read_text())
        if isinstance(document, dict)
    ]
    repositories = {
        (document["metadata"].get("namespace"), document["metadata"]["name"]): document["spec"]["url"]
        for document in documents
        if document.get("kind") == "HelmRepository"
    }
    pins = []
    for document in documents:
        if document.get("kind") != "HelmRelease":
            continue
        namespace = document["metadata"].get("namespace")
        spec = document["spec"]["chart"]["spec"]
        source = spec["sourceRef"]
        url = repositories[(source.get("namespace", namespace), source["name"])]
        pins.append((document["metadata"]["name"], url, spec["chart"], str(spec["version"])))
    return pins


def check_chart(name: str, url: str, chart: str, version: str, fetch=_fetch) -> Result:
    index_url = f"{url.rstrip('/')}/index.yaml"
    try:
        versions = chart_versions(fetch(index_url).decode(), chart)
    except (urllib.error.URLError, TimeoutError) as error:
        return Result(f"{name} chart", False, f"{index_url}: {error}")
    if version not in versions:
        return Result(f"{name} chart", False, f"{chart} {version} not in {index_url}")
    return Result(f"{name} chart", True, f"{chart} {version} in {index_url}")


def image_pins(deploy_dir: Path) -> list[str]:
    """Return every digest-pinned image reference in the deploy tree."""
    return sorted({
        match
        for path in deploy_dir.rglob("*.yaml")
        for match in IMAGE_DIGEST.findall(path.read_text())
    })


# Registries that serve anonymous pull tokens, by host. Docker Hub is the
# default for references without a registry host.
REGISTRIES = {
    "docker.io": (
        "https://auth.docker.io/token?service=registry.docker.io&scope=repository:{}:pull",
        "https://registry-1.docker.io/v2/{}/manifests/{}",
    ),
    "ghcr.io": (
        "https://ghcr.io/token?scope=repository:{}:pull",
        "https://ghcr.io/v2/{}/manifests/{}",
    ),
}


def check_image(reference: str, fetch=_fetch) -> Result:
    """Check that an image digest on Docker Hub or GHCR still resolves.

    Kubernetes pulls a reference with a digest by the digest alone, so the
    digest must stay available even if the tag moves.
    """
    name, _, digest = reference.partition("@")
    repository = name.rsplit(":", 1)[0] if ":" in name.rsplit("/", 1)[-1] else name
    first = repository.split("/", 1)[0]
    if "/" in repository and ("." in first or ":" in first):
        host, repository = repository.split("/", 1)
    else:
        host = "docker.io"
        if "/" not in repository:
            repository = f"library/{repository}"
    if host not in REGISTRIES:
        return Result(f"{name} image", False, f"{reference}: registry {host} is not checked")
    token_url, manifest_url = REGISTRIES[host]
    try:
        token = json.loads(fetch(token_url.format(repository)))["token"]
        fetch(
            manifest_url.format(repository, digest),
            "HEAD",
            {"Authorization": f"Bearer {token}", "Accept": MANIFEST_TYPES},
        )
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as error:
        return Result(f"{name} image", False, f"{reference}: {error}")
    return Result(f"{name} image", True, reference)


def run_checks(pins: dict, fetch=_fetch, deploy_dir: Path | None = None) -> list[Result]:
    results = [
        check_kubernetes_packages(pins, fetch),
        check_containerd(pins, fetch),
        check_chart(
            "Traefik", TRAEFIK_CHARTS_URL, "traefik", pins["traefik_chart_version"], fetch
        ),
    ]
    results += [check_url(name, url, fetch) for name, url in artifact_urls(pins).items()]
    if deploy_dir is not None:
        results += [check_chart(*pin, fetch=fetch) for pin in flux_chart_pins(deploy_dir)]
        results += [check_image(reference, fetch) for reference in image_pins(deploy_dir)]
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variables", type=Path, default=DEFAULT_VARIABLES)
    parser.add_argument("--deploy", type=Path, default=DEFAULT_DEPLOY)
    args = parser.parse_args(argv)
    pins = yaml.safe_load(args.variables.read_text())
    results = run_checks(pins, deploy_dir=args.deploy)
    for result in results:
        print(f"{'ok  ' if result.ok else 'FAIL'} {result.name}: {result.detail}")
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
