# Self-Managed Kubernetes Disaster Recovery

[![Ansible](https://github.com/sindredg/k8s-dr/actions/workflows/ansible.yml/badge.svg)](https://github.com/sindredg/k8s-dr/actions/workflows/ansible.yml)
[![Docs](https://github.com/sindredg/k8s-dr/actions/workflows/docs.yml/badge.svg)](https://github.com/sindredg/k8s-dr/actions/workflows/docs.yml)
[![Pins](https://github.com/sindredg/k8s-dr/actions/workflows/pins.yml/badge.svg)](https://github.com/sindredg/k8s-dr/actions/workflows/pins.yml)
[![Public surface](https://github.com/sindredg/k8s-dr/actions/workflows/surface.yml/badge.svg)](https://github.com/sindredg/k8s-dr/actions/workflows/surface.yml)

Recover a stateful service after losing a cloud region, from code and offsite backups.

Gitea and PostgreSQL run on a kubeadm-built Kubernetes cluster on Google Cloud VMs in Finland. When the primary region fails, one GitHub Actions workflow builds a new cluster in Belgium, restores the newest backup, and moves the public name `git.sindrg.com` to it. A scheduled watchdog can start that workflow on its own. The lab models organizations that must run Kubernetes on VMs they control.

[Results](#results) · [How recovery works](#how-recovery-works) · [Stack](#stack) · [Status](#status) · [Documentation](#documentation)

## Results

Only automated runs count. [Milestone 12](plan.md#12-unattended-drills) runs unattended drills: the primary is stopped, the watchdog starts the recovery, and nobody acts. Their recovery time and data loss go here, against targets of at most 4 hours and at most 2 hours ([decision 0002](docs/decisions/0002-recovery-contract.md#measurement)).

The first full run of the `Recover` workflow, started by hand on 2026-10-17, took 17 min 07 s with no other action. It does not include detection, because the primary was still serving when the run started.

| Phase | Stages | Duration |
| --- | --- | --- |
| Preflight | `preflight` | 1 min 19 s |
| Build the cluster | `provisioning`, `bootstrap`, `flux-reconcile` | 11 min 41 s |
| Restore and check | `restore`, `verification` | 2 min 18 s |
| Fence and cut over | `fence`, `dns-cutover`, `probe-recovery` | 1 min 32 s |
| Resume backups | `resume-backups` | 17 s |
| Total | | 17 min 07 s |

Building the cluster takes most of the time. [Milestone 11](plan.md#11-faster-cluster-build) shortens it. The [worklog](docs/worklogs/10-automated-recovery.md#gate) has every stage and its check.

## How recovery works

![Architecture diagram showing users reaching the primary cluster in Finland through one DNS name, encrypted backups leaving for a bucket in Belgium every 15 minutes, and a GitHub Actions watchdog that reads the probe results and starts a workflow, which builds a recovery cluster in Belgium from the same Git repository, restores it from the bucket, and moves the DNS name.](docs/diagrams/readme-overview.svg)

1. **Back up.** Every 15 minutes the primary captures PostgreSQL and the Gitea repositories from one LVM snapshot while Gitea keeps running, encrypts them with age, and uploads them to a bucket in Belgium. The writer cannot delete or overwrite a backup.
2. **Detect.** An uptime check probes `git.sindrg.com` from three continents. A scheduled watchdog starts a recovery only when every checker has failed for five minutes, the runner cannot reach the primary either, and the `AUTO_FAILOVER` switch is on. The automatic start has run against test stubs only; [milestone 12](plan.md#12-unattended-drills) runs it live.
3. **Recover.** The `Recover` workflow signs in to Google Cloud without a stored key and runs these stages in order: preflight, provisioning, bootstrap, Flux reconcile, restore, verification, fence, DNS cutover, checks through the public name, and resuming backups. Each stage ends with a check. A failure stops the run before the cutover, and a rerun resumes at the failed stage.
4. **Return.** After a drill, the `Return` workflow starts the primary, moves the name back, and removes the recovery environment. It discards the writes that the recovery cluster accepted.

The recovery region runs no VMs until a recovery starts. Terraform state, the deployment source, the backups, and the recovery credentials all stay reachable without the primary region. The [architecture pages](docs/architecture/README.md) explain each part.

## Stack

| Tool | Role |
| --- | --- |
| Terraform | VMs, network, load balancer, buckets, and the pipeline identity |
| Ansible and kubeadm | Node configuration and the Kubernetes bootstrap |
| Flux and Helm | The application layer, reconciled from this repository |
| Traefik and cert-manager | HTTPS through the Gateway API, with Let's Encrypt certificates |
| SOPS and age | Encrypted secrets in the public repository and encrypted backups |
| GitHub Actions | Tests and linters, pin and public-surface checks, the watchdog, and the recovery and return workflows |
| Cloud Monitoring, Healthchecks.io, Grafana Cloud | Uptime checks from outside, a backup heartbeat, and cluster metrics that outlive the primary region |

## Status

Milestones 0 to 10 are complete, from the recovery contract to automated recovery. Next are unattended drills and a faster cluster build. The [roadmap](plan.md) gives every milestone, its gate, and the order.

[Production readiness](docs/production-readiness.md) lists what a production deployment adds to this lab.

## Documentation

| To find | Read |
| --- | --- |
| How each part works, with diagrams | [Architecture](docs/architecture/README.md) |
| Why it is built this way | [Decision records](docs/decisions/) |
| The recovery procedure | [Regional recovery runbook](docs/runbooks/regional-recovery.md) |
| Automated runs, stage by stage | [Worklog 10](docs/worklogs/10-automated-recovery.md) |
| How to build it yourself | [Reproduce the project](docs/reproduce.md) |
| Everything else | [Documentation index](docs/README.md) |

Run `make` to list the operator commands.

## License

[MIT](LICENSE).
