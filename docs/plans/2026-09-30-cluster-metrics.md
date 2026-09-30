# Cluster metrics implementation plan

**Goal:** Send node, pod, and Kubernetes object metrics from each cluster to Grafana Cloud through Flux.

**Architecture:** A `monitoring` Flux Kustomization applies `deploy/monitoring`: a namespace, the Grafana HelmRepository, a `k8s-monitoring` 4.5.2 HelmRelease, and a SOPS Secret with the remote write credentials. `cluster-settings` supplies the cluster label.

**Spec:** [Decision 0009](../decisions/0009-cluster-metrics.md)

## Global constraints

- Chart `k8s-monitoring` 4.5.2 from `https://grafana.github.io/helm-charts`.
- Only `clusterMetrics` and `hostMetrics.linuxHosts`; `selfReporting` off; no `remoteConfig`.
- Every workload sets memory requests and limits; limits total about 1 GiB with two nodes.
- The Kustomization has `wait: false` and nothing depends on it.
- No plaintext credential in Git; the Secret is `deploy/monitoring/grafana-cloud-metrics.sops.yaml`.

## Review focus

1. Flux substitutes `${...}` in `deploy/monitoring`: every variable must exist in `cluster-settings`.
2. A missing or wrong Secret must not affect Gitea: the Kustomization is independent.
3. The Secret keys must match the chart: `username` and `password`.
4. The pin check must see the new chart, so `make pins` covers it.
5. Series count: the gate checks active series against the 10,000 limit.

---

### Task 1: Flux wiring and manifests

**Files:**
- Modify: `deploy/clusters/primary/cluster-settings.yaml`, `deploy/clusters/primary/sync.yaml`
- Create: `deploy/monitoring/{kustomization,namespace,repository,release}.yaml`
- Create: `tests/test_monitoring.py`
- Modify: `tests/test_services.py` (settings keys and substituted paths)

- [ ] Write `tests/test_monitoring.py`: the Kustomization is independent (`wait: false`, no `dependsOn`, decrypts with SOPS, substitutes `cluster-settings`), no other Kustomization depends on it, the chart is pinned to 4.5.2, only the two features are enabled, `selfReporting` is off, no `remoteConfig`, the destination reads Secret `grafana-cloud-metrics` without creating it, `cluster.name` is `${cluster_name}`, and every workload has memory requests and limits.
- [ ] Run `.venv/bin/python -m unittest tests.test_monitoring`; expect failures.
- [ ] Add the manifests and the `cluster_name: primary` setting; update `tests/test_services.py`.
- [ ] Render the release values with `helm template` and confirm the kinds: Alloy, the operator, kube-state-metrics, node-exporter, and no Beyla or remote config.
- [ ] Run `make check`; expect it to pass.
- [ ] Commit.

### Task 2: Credentials Secret

**Files:**
- Create: `deploy/monitoring/grafana-cloud-metrics.sops.yaml`

- [ ] The operator encrypts the Secret from the local token file with `sops`; the file never holds plaintext in the repository.
- [ ] Run `make check`; `test_secrets_in_git_are_sops_encrypted` covers it.
- [ ] Commit.

### Task 3: Documentation

**Files:**
- Create: `docs/worklogs/04b-cluster-metrics.md`
- Modify: `plan.md`, `docs/README.md`, `docs/production-readiness.md`

- [ ] Worklog: work completed, the gate commands with expected results, and an empty validation section.
- [ ] `plan.md`: a "Before milestone 6: cluster metrics" section linking decision 0009.
- [ ] Commit, push, and open the PR.
