# Regional recovery

Status: Draft. This runbook describes the intended cold recovery drill. It is not executable end to end until milestones 1 through 5 provide infrastructure code, bootstrap automation, deployment configuration, verified backups, and endpoint details.

## Preconditions

- The primary service is isolated for the drill, and an external health probe is running.
- The operator can access the external source repository, remote Terraform state, recovery-region credentials, offsite backup storage, and DNS or endpoint controls without using the primary region.
- A verified backup set contains PostgreSQL data and Gitea repositories and configuration from one consistent recovery point. Its timestamp and integrity check are recorded.
- The fixture user, repository, commit, and issue identifiers and last successful write time are recorded outside the failed region.

If any prerequisite is missing, record it as a blocked drill. Do not route users to an unverified restore.

## Procedure

1. Record the outage start time from the external probe and the last successful write time. Record every manual action during recovery.
2. Select the newest backup set that passed its integrity and completeness checks. Record its recovery-point timestamp. Exclude failed or partial backup sets.
3. Provision recovery-region VMs and networking from the versioned Terraform configuration. Confirm state and credentials are reachable independently of the primary region.
4. Run the versioned Ansible and kubeadm bootstrap procedure. Join the worker, install the CNI and required ingress and storage components, and confirm both nodes are `Ready` with `kubectl get nodes -o wide`.
5. Reconnect Flux to the external GitHub repository and confirm the expected resources reconcile. Restore PostgreSQL and Gitea data from the same backup set using the validated restore procedure.
6. Test the recovery endpoint before changing public routing: log in, find the known commit and issue, and push a new commit. Investigate any failure before proceeding.
7. Change the Cloudflare DNS target for `git.sindrg.com` to the recovery endpoint. Confirm the external probe reports a healthy service and repeat the login, fixture, and authenticated push checks through `git.sindrg.com`. Record the time when all checks pass.
8. Compute RTO from the first failed external probe to the time all checks pass through `git.sindrg.com`. Compute observed RPO from the last acknowledged primary test write to the newest test write recovered. Also report the potential data-loss window from the backup recovery point to outage start. Record timestamps, calculations, failures, manual steps, and cost in the drill worklog created for milestone 6.

## Stop conditions and follow-up

Stop before traffic cutover if the backup is incomplete, the restore fails verification, or the recovered service cannot accept a new push. Preserve sanitized evidence for diagnosis. After a successful drill, update this runbook with tested commands, paths, timings, and rollback details; repeat the drill as required by [milestone 6](../../plan.md#6-disaster-drill).
