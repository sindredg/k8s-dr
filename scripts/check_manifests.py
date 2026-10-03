#!/usr/bin/env python3
"""Render every Flux Kustomization path offline and check the result.

Flux builds each path with Kustomize, substitutes ${var} from the cluster
settings, and applies the output. A mistake in that output otherwise shows
only when a cluster reconciles it. This script repeats the build and the
substitution for every cluster without credentials, requires a namespace on
every namespaced object, and writes the renders for kubeconform.

It does not decrypt SOPS files, template Helm charts, or contact a cluster.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys

import yaml

CLUSTERS = Path("deploy/clusters")
# Applied on request by ansible/playbooks/restore_test_env.yml, with the
# settings of the cluster it runs on.
EXTRA_SYNC = (Path("deploy/restore-test/flux.yaml"),)
FLUX_KUSTOMIZATION = "kustomize.toolkit.fluxcd.io/"
SUBSTITUTE_ANNOTATION = "kustomize.toolkit.fluxcd.io/substitute"
VARIABLE = re.compile(r"\$\{(\w+)\}")
# Kinds in deploy/ that take no namespace. A new cluster-scoped kind must be
# added here, or the namespace check reports it.
CLUSTER_SCOPED = frozenset({
    "ClusterIssuer",
    "ClusterRole",
    "ClusterRoleBinding",
    "CustomResourceDefinition",
    "GatewayClass",
    "Namespace",
    "PriorityClass",
    "StorageClass",
})


@dataclass(frozen=True)
class Render:
    cluster: str
    name: str
    path: str
    documents: list


def kustomize(path) -> list:
    completed = subprocess.run(
        ["kubectl", "kustomize", str(path)], check=True, capture_output=True, text=True
    )
    return [document for document in yaml.safe_load_all(completed.stdout) if document]


def substitute(document: dict, settings: dict) -> dict:
    """Substitute ${var} as Flux does: on the serialized object, so that an
    unquoted placeholder can become a boolean or a number."""
    annotations = document.get("metadata", {}).get("annotations") or {}
    if annotations.get(SUBSTITUTE_ANNOTATION) == "disabled":
        return document
    text = yaml.safe_dump(document, sort_keys=False)
    undefined = sorted(set(VARIABLE.findall(text)) - set(settings))
    if undefined:
        raise ValueError(f"undefined variables: {', '.join(undefined)}")
    return yaml.safe_load(VARIABLE.sub(lambda match: settings[match.group(1)], text))


def missing_namespaces(documents: list, target_namespace: str | None = None) -> list[str]:
    """Return the namespaced objects that Flux would reject for having no
    namespace. Kustomize builds them without complaint."""
    if target_namespace:
        return []
    return [
        f"{document['kind']}/{document['metadata']['name']}"
        for document in documents
        if document["kind"] not in CLUSTER_SCOPED and not document["metadata"].get("namespace")
    ]


def without_sops_metadata(document: dict) -> dict:
    """Drop the top-level sops key, which Flux removes when it decrypts."""
    return {key: value for key, value in document.items() if key != "sops"}


def render_cluster(cluster: str, build=kustomize) -> list[Render]:
    root_path = CLUSTERS / cluster
    root = build(root_path)
    settings = next(
        document["data"] for document in root
        if document["kind"] == "ConfigMap" and document["metadata"]["name"] == "cluster-settings"
    )
    sync = [document for document in root if document["apiVersion"].startswith(FLUX_KUSTOMIZATION)]
    for path in EXTRA_SYNC:
        sync += [document for document in yaml.safe_load_all(path.read_text()) if document]

    renders = [Render(cluster, "flux-system", str(root_path), root)]
    for kustomization in sync:
        spec = kustomization["spec"]
        documents = build(spec["path"])
        if "postBuild" in spec:
            documents = [substitute(document, settings) for document in documents]
        missing = missing_namespaces(documents, spec.get("targetNamespace"))
        if missing:
            raise ValueError(f"{spec['path']}: no namespace on {', '.join(missing)}")
        documents = [without_sops_metadata(document) for document in documents]
        renders.append(Render(cluster, kustomization["metadata"]["name"], spec["path"], documents))
    return renders


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, help="Directory for the rendered YAML files.")
    args = parser.parse_args(argv)

    failed = False
    for cluster in sorted(path.name for path in CLUSTERS.iterdir() if path.is_dir()):
        try:
            renders = render_cluster(cluster)
        except (ValueError, subprocess.CalledProcessError) as error:
            detail = getattr(error, "stderr", None) or error
            print(f"FAIL  {cluster}: {str(detail).strip()}", file=sys.stderr)
            failed = True
            continue
        for render in renders:
            print(f"ok    {cluster}/{render.name}: {len(render.documents)} objects from {render.path}")
            if args.output:
                args.output.mkdir(parents=True, exist_ok=True)
                target = args.output / f"{cluster}-{render.name}.yaml"
                target.write_text(yaml.safe_dump_all(render.documents, sort_keys=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
