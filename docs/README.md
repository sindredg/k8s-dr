# Project documentation

The [project plan](../plan.md) defines milestone scope and validation gates. These documents record implementation evidence and recovery procedures. Milestones remain pending or in progress until their gates pass.

| Document | Purpose |
| --- | --- |
| [Primary infrastructure worklog](worklogs/01-primary-infrastructure.md) | Record milestone 1 changes and validation. |
| [Kubernetes bootstrap worklog](worklogs/02-kubernetes-bootstrap.md) | Record milestone 2 changes and validation. |
| [Use kubeadm](decisions/0001-use-kubeadm.md) | Explain the cluster bootstrap choice and its trade-offs. |
| [Recovery contract](decisions/0002-recovery-contract.md) | Define recovery targets, checks, architecture, and alternatives. |
| [Regional infrastructure and state](decisions/0003-regional-infrastructure-and-state.md) | Explain the reusable module and offsite state decisions. |
| [Remove budget alert](decisions/0004-remove-budget-alert.md) | Record the operator's decision to remove automated spend alerts from Terraform. |
| [Kubernetes bootstrap architecture](decisions/0005-kubernetes-bootstrap-architecture.md) | Define the Milestone 2 automation, networking, storage, access, and validation design. |
| [Kubernetes bootstrap implementation plan](plans/2026-09-23-kubernetes-bootstrap.md) | Break Milestone 2 implementation into tested, reviewable tasks. |
| [Primary infrastructure procedure](runbooks/primary-infrastructure.md) | Bootstrap state, apply the primary root, and check the milestone 1 gate. |
| [Kubernetes bootstrap procedure](runbooks/kubernetes-bootstrap.md) | Bootstrap the private kubeadm cluster and collect the milestone 2 gate evidence. |
| [Worker join failure](troubleshooting/01-worker-join-failure.md) | Diagnose a worker that cannot join or become Ready. This is a guide, not an incident report. |
| [Billing budget apply errors](troubleshooting/02-billing-budget-apply-errors.md) | Record the historical budget errors and the decision to remove the alert. |
| [Regional recovery](runbooks/regional-recovery.md) | Execute and measure a cold recovery drill after prerequisites are implemented. |

Record completed work and the commands and results that validate it in the corresponding worklog. Keep credentials, kubeconfigs, Terraform state, database dumps, and unsanitized command output out of this repository.
