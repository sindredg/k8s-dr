# Self-Managed Kubernetes Disaster Recovery

[![Ansible](https://github.com/sindredg/k8s-dr/actions/workflows/ansible.yml/badge.svg)](https://github.com/sindredg/k8s-dr/actions/workflows/ansible.yml)
[![Docs](https://github.com/sindredg/k8s-dr/actions/workflows/docs.yml/badge.svg)](https://github.com/sindredg/k8s-dr/actions/workflows/docs.yml)
[![Pins](https://github.com/sindredg/k8s-dr/actions/workflows/pins.yml/badge.svg)](https://github.com/sindredg/k8s-dr/actions/workflows/pins.yml)

Recover a stateful service after losing a cloud region, from code and offsite backups.

Gitea and PostgreSQL run on a kubeadm-built Kubernetes cluster on Google Cloud VMs in Finland. When the primary region fails, one GitHub Actions workflow builds a new cluster in Belgium, restores the newest backup, and moves the public name `git.sindrg.com` to it. A scheduled watchdog can start that workflow on its own. The lab models organizations that must run Kubernetes on VMs they control. The project is complete and its environment removed; see [Status](#status).

[Results](#results) · [How recovery works](#how-recovery-works) · [Stack](#stack) · [Status](#status) · [Documentation](#documentation)

## Results

Only automated runs count. In the unattended drills of [milestone 11](docs/worklogs/11-unattended-drills.md), a workflow stops the primary, the watchdog starts the recovery, and nobody acts. The targets are a recovery time of at most 4 hours and a data loss of at most 2 hours ([decision 0002](docs/decisions/0002-recovery-contract.md#measurement)). Two consecutive drills on 2026-10-19 met both, with a 2 GiB dataset on the primary:

| Drill | Detection | Recovery time | Data loss |
| --- | --- | --- | --- |
| [20261019T170003Z](https://github.com/sindredg/k8s-dr/actions/runs/39290237165) | 13 min 15 s | 34 min 21 s | 10 min 18 s |
| [20261019T185625Z](https://github.com/sindredg/k8s-dr/actions/runs/39306669248) | 18 min 15 s | 36 min 00 s | 5 min 09 s |

The recovery time runs from the first failed probe to the checks through the public name, so it includes detection. For drill 20261019T185625Z:

| Phase | Stages | Duration |
| --- | --- | --- |
| Detection | From the first failed probe to the watchdog's `Recover` run | 18 min 15 s |
| Job setup | Before the first stage | 54 s |
| Preflight | `preflight` | 1 min 40 s |
| Build the cluster | `provisioning`, `bootstrap`, `flux-reconcile` | 11 min 01 s |
| Restore and check | `restore`, `verification` | 3 min 38 s |
| Fence and cut over | `fence`, `dns-cutover`, `probe-recovery` | 32 s |
| Total | | 36 min 00 s |

Detection and building the cluster take most of the time. Detection needs five minutes of failed checks and then a watchdog run, which GitHub schedules every five minutes but started 12 and 13 minutes apart in these drills. The [drill results](docs/drills.md) list every drill, and the [worklog](docs/worklogs/11-unattended-drills.md#comparison-with-drills-1-to-3) compares them with the attended drills.

## How recovery works

![Architecture diagram showing users reaching the primary cluster in Finland through one DNS name, encrypted backups leaving for a bucket in Belgium every 15 minutes, and a GitHub Actions watchdog that reads the probe results and starts a workflow, which builds a recovery cluster in Belgium from the same Git repository, restores it from the bucket, and moves the DNS name. A step strip names the sequence: back up, the primary fails, detect, and recover; where motion is allowed, the figure plays it in a loop.](docs/diagrams/readme-overview.svg)

1. **Back up.** Every 15 minutes the primary captures PostgreSQL and the Gitea repositories from one LVM snapshot while Gitea keeps running, encrypts them with age, and uploads them to a bucket in Belgium. The writer cannot delete or overwrite a backup.
2. **Detect.** An uptime check probes `git.sindrg.com` from three continents. A scheduled watchdog starts a recovery only when every checker has failed for five minutes, the runner cannot reach the primary either, and the `AUTO_FAILOVER` switch is on. It started the recovery in every unattended drill of [milestone 11](docs/worklogs/11-unattended-drills.md).
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
| GitHub Actions | Tests and linters, pin and public-surface checks, the watchdog, and the recovery, return, and drill workflows |
| Cloud Monitoring, Healthchecks.io, Grafana Cloud | Uptime checks from outside, a backup heartbeat, and cluster metrics that outlive the primary region |

## Status

The project is complete: milestones 0 to 11, from the recovery contract to unattended drills, all passed their gates. The environment was removed after the close, so `git.sindrg.com` no longer answers; the results above link to the workflow runs. The [worklogs](docs/README.md#worklogs) record every milestone and its gate, and the [close](docs/worklogs/project-close.md) records the teardown.

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
