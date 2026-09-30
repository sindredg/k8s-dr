# 0009: Cluster metrics in Grafana Cloud

Status: Proposed on 2026-09-30.

Date: 2026-09-30

## Goal

Show node, pod, and Kubernetes object metrics as dashboards, and keep them readable after the primary region is lost. The [milestone 6 drill](../../plan.md#6-disaster-drill) then has a visual record of the failure and the recovery. [Production readiness](../production-readiness.md) lists metrics as a gap.

This record covers metrics only. Logs, alerts on metrics, and the external uptime probe that milestone 6 needs are out of scope.

## Decisions

### Send metrics to Grafana Cloud with the k8s-monitoring chart

Flux installs Grafana's [`k8s-monitoring`](https://github.com/grafana/k8s-monitoring-helm) Helm chart, version 4.5.2, into a `monitoring` namespace. The chart deploys:

| Component | Kind | Purpose |
| --- | --- | --- |
| Alloy Operator | Deployment | Creates the Alloy collector from an `Alloy` custom resource |
| `alloy-metrics` | StatefulSet, 1 replica | Scrapes the kubelet, cAdvisor, node-exporter, and kube-state-metrics, then remote-writes to Grafana Cloud |
| kube-state-metrics | Deployment | Kubernetes object state |
| node-exporter | DaemonSet | Node CPU, memory, disk, and network |

The chart's `clusterMetrics` and `hostMetrics` features keep only the metrics the Grafana Cloud Kubernetes Monitoring app uses, so its dashboards work without extra configuration. Every other feature, the cost and energy services, Beyla, and the chart's self-reporting telemetry are off.

| Option | Trade-off |
| --- | --- |
| Grafana Cloud with `k8s-monitoring` (selected) | Data lives outside both regions, and dashboards work on arrival. The chart is large and adds an operator; its values change between major versions. |
| Grafana Cloud with the plain `alloy` chart | Fewer components, but we write the scrape and filter configuration and build or import dashboards whose labels match it. |
| kube-prometheus-stack in the cluster | No external account, but too heavy for 4 GiB nodes, lost with the region, and slower rebuilds. |
| GCP Managed Service for Prometheus | Stays in GCP, but fits a self-managed kubeadm cluster poorly and shares a project with what it watches. |

### Keep the collector configuration in Git

Grafana's onboarding wizard enables Fleet Management, which delivers Alloy configuration from the Grafana Cloud UI. This design disables it. The configuration is the HelmRelease values in Git, so a recovery cluster gets the same collector from the same commit, and no change reaches the cluster outside a review.

### Apply it independently of the service

A separate Flux Kustomization, `monitoring`, applies `deploy/monitoring` with `wait: false` and no dependents. A failed chart install or an unreachable Grafana Cloud cannot block Gitea, a bootstrap, or a recovery.

### Label each cluster

A new `cluster-settings` key, `cluster_name`, sets the chart's `cluster.name`. The primary reports `cluster="primary"` and the recovery cluster `cluster="recovery"`, from the same manifests. When [decision 0008](0008-cold-recovery.md#share-the-flux-sync-definition-between-clusters) moves the sync definition to `deploy/sync/`, this Kustomization moves with it.

### Use a write-only token as a recovery credential

A Grafana Cloud access policy token with only the `metrics:write` scope authenticates the remote write. The token and the stack's Prometheus instance ID are a SOPS Secret in `deploy/monitoring`. The token is also kept in the credential store, because the recovery cluster sends metrics with it. The Prometheus push URL is not secret and is set in the HelmRelease.

## Consequences

- Each cluster runs four more workloads. Explicit requests and limits cap their memory at about 1 GiB in total, with requests near 350 MiB. The gate records node memory before and after.
- The cluster scrapes nodes and kube-state-metrics, not the Gitea or PostgreSQL pods, so their network policies do not change. Alloy needs outbound HTTPS, which the nodes already have.
- The chart runs post-install and pre-delete hook Jobs that manage a finalizer on the `Alloy` resource. Removing the release must go through Helm, not by deleting the namespace.

## Known limits

- The Grafana Cloud free tier keeps metrics for 14 days and allows 10,000 active series ([pricing](https://grafana.com/pricing/)). Drill evidence older than 14 days must be exported into the worklog as screenshots or query results.
- The two clusters count against one series limit. Both running at once during milestone 5 and 6 roughly doubles the series.
- Grafana Cloud is an external dependency. If it is down, the cluster keeps running and dashboards show a gap.
