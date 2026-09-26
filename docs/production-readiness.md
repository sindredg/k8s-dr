# Production readiness

This lab proves a recovery pattern that production systems use: rebuild infrastructure from code, reconcile configuration from a source outside the failed region, restore from consistent offsite backups, and measure the result in drills. The implementation is sized for a lab. This page lists what a production deployment would add.

The target setting is an organization that must run Kubernetes on VMs it controls, for example to meet regulatory requirements at the operating system level. See [decision 0001](decisions/0001-use-kubeadm.md).

| Area | This lab | Production expectation |
| --- | --- | --- |
| Availability within a region | One control plane, one worker, one zonal data disk | Three control-plane nodes behind a stable endpoint, several workers across zones, and replicated storage. Zonal failures are more common than regional ones. |
| Control-plane endpoint | The control-plane node address | A DNS name or virtual address, so control-plane nodes can be replaced or added without re-initializing |
| Node lifecycle | Pinned packages, manual upgrades | A documented upgrade cadence for the OS, containerd, and Kubernetes; certificate expiry monitoring; hardened images such as CIS benchmarks |
| Backup isolation | Same project, one Belgium bucket, operator-held credentials. Milestone 4 plans a writer that cannot delete or overwrite, an unlocked retention policy, explicit soft delete, and noncurrent-version expiry. | A separate backup project or account, a locked retention policy, and a second copy in another location |
| Terraform execution | Operator machine with personal credentials | A pipeline with reviewed plans, a service account through workload identity federation, and audited break-glass access |
| Secrets | SOPS with an offline age key (planned) | A secrets manager with rotation and audit, plus an offline recovery copy of its unseal or root material |
| Cost control | Manual review in Cloud Billing | Budget alerts and ownership labels. See [decision 0004](decisions/0004-remove-budget-alert.md). |
| Observability | Ansible output and an external probe (planned) | Metrics, logs, and alerts for backup age, certificate expiry, node health, and disk usage, routed to an on-call rotation |
| Supply chain | Images and packages pulled from public registries during recovery | Mirrored images and packages in a registry in each region, with signature or digest verification |
| Cutover and failback | Manual DNS change, no failback | Automated DNS change with approval, a documented failback, and a fencing rule so a returning primary cannot serve or write backups |
| Drills | One drill, repeated once | Scheduled drills with results tracked over time and runbook owners |
| Access | One administrator member with project-level grants | Groups, per-environment grants, just-in-time elevation, and separate recovery identities |

Each row is a deliberate scope limit, not an oversight. Items that affect recovery time or data loss are candidates for [milestone 7](../plan.md#7-faster-recovery-optional) once the drill measures them.
