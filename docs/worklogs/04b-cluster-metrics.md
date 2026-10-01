# Cluster metrics before milestone 6

Status: Implemented; validation pending.

## Scope

Send node, pod, and Kubernetes object metrics from each cluster to Grafana Cloud, so the milestone 6 drill has dashboards that outlive the primary region. [Decision 0009](../decisions/0009-cluster-metrics.md) records the design. This is not a milestone; it adds no recovery step.

## Work completed

- Added `deploy/monitoring`: the `monitoring` namespace, the Grafana HelmRepository, the `k8s-monitoring` 4.5.2 HelmRelease, and the `grafana-cloud-metrics` SOPS Secret with the Prometheus instance ID and access policy token.
- Enabled only `clusterMetrics` and `hostMetrics.linuxHosts`. Self-reporting, Fleet Management, Beyla, logs, and the cost and energy services are off.
- Set memory requests and limits on every workload: requests total 336 MiB and limits 896 MiB with two nodes.
- Added the `monitoring` Flux Kustomization with `wait: false` and no dependents, and the `cluster_name` setting that labels the metrics `cluster="primary"`.
- Added `tests/test_monitoring.py`. `make pins` checks the new chart through the existing HelmRelease scan.

## Gate

Run the `kubectl` commands on the control plane over IAP SSH:

```bash
gcloud compute ssh <control-plane> --zone <zone> --tunnel-through-iap -- \
  sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl <command>
```

1. Record node memory before the merge: `kubectl describe nodes | grep -A8 'Allocated resources'`.
2. After the merge, check Flux: `kubectl -n flux-system get kustomization monitoring` and `kubectl -n monitoring get helmrelease k8s-monitoring`. Expected: both `Ready=True`.
3. Check the workloads: `kubectl -n monitoring get pods -o wide`. Expected: the operator, `alloy-metrics-0`, kube-state-metrics, and one node-exporter per node, all `Running`.
4. In Grafana Cloud, open **Kubernetes Monitoring**. Expected: cluster `primary` with two nodes and the `gitea` and `postgresql` pods.
5. In Explore, run `count({cluster="primary"})`. Expected: well under 10,000 active series.
6. Repeat step 1. Expected: each node keeps memory requests below 80% of allocatable.

## Validation

### Before the merge

On 2026-09-30, `kubectl describe nodes` reported these memory requests against about 3,808 MiB allocatable per node:

| Node | Memory requests | Memory limits |
| --- | --- | --- |
| `k8sdr-primary-control-plane` | 100Mi (2%) | 0 (0%) |
| `k8sdr-primary-worker` | 1100Mi (28%) | 9556Mi (250%) |

The cluster has no Metrics API, so `kubectl top` is unavailable. The release adds 312 MiB of requests to the worker (Alloy, the operator, kube-state-metrics, one node-exporter) and 24 MiB to the control plane, which leaves the worker near 37%.

### After the merge

Not run yet.
