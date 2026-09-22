# Recovery contract and target design

Status: Proposed for review  
Date: 2026-09-22

## Goal

Build a self-managed Kubernetes service on VMs and prove it can be restored in a second region after the primary region is unavailable. The first target is a cold recovery within a few hours. This is a lab and portfolio project, with a public demonstration service and recorded drill evidence.

## Decisions

| Area | Decision |
| --- | --- |
| Regions | Finland (`europe-north1`) primary; Belgium (`europe-west1`) recovery |
| Spend | Alert at 1,000 NOK per month for this GCP project; no automatic spend cap |
| Recovery targets | RTO at most 4 hours; RPO at most 2 hours |
| Backup cadence | Every hour; alert if no completed backup remains within the RPO |
| Primary cluster | One kubeadm control-plane VM and one worker VM, each starting at 2 vCPU and 4 GB RAM, on Ubuntu LTS |
| Recovery cluster | Cold VMs created only for restore tests and drills |
| Administration | Private VM addresses and restricted SSH through IAP; no public SSH |
| Cluster components | containerd, Calico, Traefik with Gateway API, and Local Path Provisioner on a dedicated worker data disk |
| Deployment | Flux reads manifests and Helm releases from external GitHub |
| Workload | Gitea and PostgreSQL with persistent storage; HTTPS Git pushes |
| Public access | `git.sindrg.com` shows Gitea and a public demo repository; registration disabled and writes authenticated |
| Recovery access | `git-dr.sindrg.com` is available during restore tests and drills |
| Cutover | Change Cloudflare DNS for `git.sindrg.com` to the recovery endpoint using a runbook |

## Dependencies and boundaries

- Keep Terraform state, encrypted backups, and recovery credentials accessible when Finland is unavailable. Store state and backups outside Finland.
- Run hourly backup sets containing a native PostgreSQL dump plus Gitea repositories and configuration. Briefly pause Gitea writes while capturing the matching set, then resume writes before the remote upload.
- Give each backup set a timestamp and integrity check. A failed or incomplete set is ineligible for restore.
- Keep a separate, protected copy of the credentials needed for infrastructure, backup decryption, application restore, and DNS cutover. Do not depend on the primary VMs for these credentials.
- Local Path storage retains data through pod restarts and ordinary VM boot disk replacement when the dedicated worker disk is reattached. It does not provide cross-region replication; the offsite backup is the recovery source.
- Keep the service URL `git.sindrg.com` stable after cutover. Validate the recovered backend through the recovery endpoint before changing the canonical DNS record.

## Measurement

| Measure | Start | Stop or comparison | Target |
| --- | --- | --- | --- |
| RTO | First failed check from an external probe after primary isolation | Login, known commit and issue, and a new authenticated push all succeed through `git.sindrg.com` | At most 4 hours |
| RPO | Last acknowledged test write on the primary before isolation | Timestamp of the newest test write present after restore | Difference at most 2 hours |

The external probe checks every minute. During a drill, create identifiable test writes every five minutes and record their UTC timestamps. Record the isolation time separately from the first failed probe so detection delay remains visible.

## Drill sequence

1. Verify a completed backup and record its age.
2. Isolate the primary service and its VMs so recovery cannot read from them.
3. Provision recovery VMs in Belgium, bootstrap kubeadm with Ansible, and reconnect Flux to GitHub.
4. Restore one verified backup set into Gitea and PostgreSQL.
5. Validate the recovered service through `git-dr.sindrg.com`.
6. Change the canonical DNS target and verify login, the known commit and issue, and a new push through `git.sindrg.com`.
7. Record RTO, RPO, backup age, manual actions, failures, and cost. Repair the runbook and repeat the drill once.

## Validation gates

- Before infrastructure: the recovery checks, timers, data-loss calculation, regions, and spend alert are documented.
- Before declaring backups ready: restore into a separate test environment and make a new push.
- Before declaring DR ready: recover while primary access is blocked and pass the same checks through the canonical hostname.

Implementation details such as exact VM machine type, certificate issuer, and public endpoint resource belong to their respective milestones and must be validated before provisioning.
