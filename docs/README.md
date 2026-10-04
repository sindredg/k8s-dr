# Project documentation

The [project plan](../plan.md) defines each milestone and its validation gate. These documents explain the design, record the evidence, and hold the procedures.

## Start here

| Document | Purpose |
| --- | --- |
| [Architecture](architecture/README.md) | How each part of the design works and connects, with diagrams. |
| [Reproduce the project](reproduce.md) | What to prepare and change to build it in your own accounts, and the order of the runbooks. |
| [Production readiness](production-readiness.md) | What a production deployment adds to this lab. |

## Runbooks

| Document | Purpose |
| --- | --- |
| [Primary infrastructure](runbooks/primary-infrastructure.md) | Bootstrap the state bucket and apply the shared and primary roots. |
| [Kubernetes bootstrap](runbooks/kubernetes-bootstrap.md) | Build the private kubeadm cluster and validate it. |
| [Service deployment](runbooks/service-deployment.md) | Expose the cluster and deploy the service through Flux. |
| [Backup and restore](runbooks/backup-restore.md) | Check the backup sets and restore one with the restore Job. |
| [Regional recovery](runbooks/regional-recovery.md) | Recover the service in the second region, measure it, and return to the primary. |

## Worklogs

Each worklog records what was built and the commands and results that passed its gate.

| Document | Milestone |
| --- | --- |
| [Primary infrastructure](worklogs/01-primary-infrastructure.md) | 1 |
| [Kubernetes bootstrap](worklogs/02-kubernetes-bootstrap.md) | 2 |
| [Service deployment](worklogs/03-service-deployment.md) | 3, and the preparation before it |
| [Simplification](worklogs/03b-simplification.md) | The cleanup between milestones 3 and 4 |
| [Consistent backups](worklogs/04-consistent-backups.md) | 4 |
| [Cluster metrics](worklogs/04b-cluster-metrics.md) | Metrics before the drills |
| [Service hardening](worklogs/04c-service-hardening.md) | The security review and hardening before the drills |
| [Cold recovery](worklogs/05-cold-recovery.md) | 5 |
| [Disaster drill](worklogs/06-disaster-drill.md) | 6: two drills, their timelines, and their comparison |
| [Faster recovery](worklogs/07-faster-recovery.md) | 7: the 15-minute backup schedule, the third drill, and the cost |

## Decisions

| Decision | Summary |
| --- | --- |
| [0001 Use kubeadm](decisions/0001-use-kubeadm.md) | The cluster bootstrap choice and its trade-offs. |
| [0002 Recovery contract](decisions/0002-recovery-contract.md) | Recovery targets, checks, measurement, and alternatives. |
| [0003 Regional infrastructure and state](decisions/0003-regional-infrastructure-and-state.md) | The reusable regional module and offsite state. |
| [0004 Remove budget alert](decisions/0004-remove-budget-alert.md) | Why spend alerts are not in Terraform. |
| [0005 Kubernetes bootstrap architecture](decisions/0005-kubernetes-bootstrap-architecture.md) | Automation, networking, storage, access, and validation of the cluster. |
| [0006 Service deployment architecture](decisions/0006-service-deployment-architecture.md) | Flux, secrets, PostgreSQL, Gitea, and public exposure. |
| [0007 Consistent backups](decisions/0007-consistent-backups.md) | Capture, format, encryption, writer access, retention, and alerting. |
| [0008 Cold recovery](decisions/0008-cold-recovery.md) | Hostnames, cluster selection, the recovery root, isolation, and the return after a drill. |
| [0009 Cluster metrics](decisions/0009-cluster-metrics.md) | Node, pod, and object metrics in Grafana Cloud. |
| [0010 Service hardening](decisions/0010-service-hardening.md) | Sign-in, pod egress, node scopes, and the daily surface scan. |
| [0011 External uptime probe](decisions/0011-external-uptime-probe.md) | Measuring the outage with a Cloud Monitoring uptime check. |
| [0012 Back up every 15 minutes](decisions/0012-backup-every-15-minutes.md) | The shorter data-loss window and what it costs. |

## Troubleshooting

| Document | Summary |
| --- | --- |
| [Worker join failure](troubleshooting/01-worker-join-failure.md) | A guide for a worker that cannot join or become Ready. |
| [Billing budget apply errors](troubleshooting/02-billing-budget-apply-errors.md) | The budget errors that led to decision 0004. |
| [Flux rejected the age key](troubleshooting/03-flux-age-key-rejected.md) | A corrupted SOPS key, a stale Flux failure, and a dropped IAP session at the first Flux bootstrap. |
| [Flux reconciled the previous branch](troubleshooting/04-flux-reconciled-stale-revision.md) | A branch switch where Flux applied the old artifact. |

Keep credentials, kubeconfigs, Terraform state, database dumps, and unsanitized command output out of these files.
