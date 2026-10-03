# Project documentation

The [project plan](../plan.md) defines milestone scope and validation gates. These documents record implementation evidence and recovery procedures. Milestones remain pending or in progress until their gates pass.

| Document | Purpose |
| --- | --- |
| [Architecture](architecture/README.md) | Explain how each part of the final design works and connects, with diagrams: infrastructure, Ansible, the cluster, Flux, Helm, traffic, Gitea, PostgreSQL, backups, regional recovery, monitoring, and access. |
| [Primary infrastructure worklog](worklogs/01-primary-infrastructure.md) | Record milestone 1 changes and validation. |
| [Kubernetes bootstrap worklog](worklogs/02-kubernetes-bootstrap.md) | Record milestone 2 changes and validation. |
| [Service deployment worklog](worklogs/03-service-deployment.md) | Record milestone 3 preparation, changes, and validation. |
| [Simplification worklog](worklogs/03b-simplification.md) | Record the cleanup between milestones 3 and 4. |
| [Consistent backups worklog](worklogs/04-consistent-backups.md) | Record milestone 4 changes and validation. |
| [Cluster metrics worklog](worklogs/04b-cluster-metrics.md) | Record the metrics setup before milestone 6 and its validation. |
| [Service hardening worklog](worklogs/04c-service-hardening.md) | Record the security review, the hardening before milestone 6, and its validation. |
| [Cold recovery worklog](worklogs/05-cold-recovery.md) | Record milestone 5 changes and validation. |
| [Use kubeadm](decisions/0001-use-kubeadm.md) | Explain the cluster bootstrap choice and its trade-offs. |
| [Recovery contract](decisions/0002-recovery-contract.md) | Define recovery targets, checks, architecture, and alternatives. |
| [Regional infrastructure and state](decisions/0003-regional-infrastructure-and-state.md) | Explain the reusable module and offsite state decisions. |
| [Remove budget alert](decisions/0004-remove-budget-alert.md) | Record the operator's decision to remove automated spend alerts from Terraform. |
| [Kubernetes bootstrap architecture](decisions/0005-kubernetes-bootstrap-architecture.md) | Define the Milestone 2 automation, networking, storage, access, and validation design. |
| [Service deployment architecture](decisions/0006-service-deployment-architecture.md) | Define Flux, secrets, PostgreSQL, Gitea, and public exposure for milestone 3. |
| [Consistent backups](decisions/0007-consistent-backups.md) | Define the backup capture, format, encryption, writer access, retention, and alerting for milestone 4. |
| [Cold recovery](decisions/0008-cold-recovery.md) | Define the hostnames, cluster selection, recovery root, and primary isolation for milestone 5. |
| [Cluster metrics](decisions/0009-cluster-metrics.md) | Send node, pod, and Kubernetes object metrics to Grafana Cloud from each cluster. |
| [Service hardening](decisions/0010-service-hardening.md) | Require sign-in, restrict pod egress and node scopes, and check the public surface daily. |
| [Production readiness](production-readiness.md) | List what a production deployment adds to this lab. |
| [Kubernetes bootstrap implementation plan](plans/2026-09-23-kubernetes-bootstrap.md) | Break Milestone 2 implementation into tested, reviewable tasks. |
| [Cluster metrics implementation plan](plans/2026-09-30-cluster-metrics.md) | Break the decision 0009 implementation into tasks. |
| [Primary infrastructure procedure](runbooks/primary-infrastructure.md) | Bootstrap state, apply the primary root, and check the milestone 1 gate. |
| [Terraform shared-root migration](runbooks/terraform-shared-root-migration.md) | Move the backup bucket and project IAM into `infra/shared` once, without recreating resources. |
| [Kubernetes bootstrap procedure](runbooks/kubernetes-bootstrap.md) | Bootstrap the private kubeadm cluster and collect the milestone 2 gate evidence. |
| [Service deployment procedure](runbooks/service-deployment.md) | Expose the cluster publicly and deploy the milestone 3 service in order. |
| [Worker join failure](troubleshooting/01-worker-join-failure.md) | Diagnose a worker that cannot join or become Ready. This is a guide, not an incident report. |
| [Billing budget apply errors](troubleshooting/02-billing-budget-apply-errors.md) | Record the historical budget errors and the decision to remove the alert. |
| [Flux rejected the age key](troubleshooting/03-flux-age-key-rejected.md) | A corrupted SOPS key, a stale Flux failure, and a dropped IAP session during the first Flux bootstrap. |
| [Flux reconciled the previous branch](troubleshooting/04-flux-reconciled-stale-revision.md) | A branch switch where Flux applied the old artifact and waited on a child that had read the new one. |
| [Backup and restore procedure](runbooks/backup-restore.md) | Check the hourly backup sets and restore one with the restore Job. |
| [Regional recovery](runbooks/regional-recovery.md) | Execute and measure a cold recovery drill after prerequisites are implemented. |

Record completed work and the commands and results that validate it in the corresponding worklog. Keep credentials, kubeconfigs, Terraform state, database dumps, and unsanitized command output out of this repository.
