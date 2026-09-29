# 0007: Consistent backups

Status: Accepted on 2026-09-29. Implementation in progress; the [milestone 4 gate](../../plan.md#4-consistent-backups) has not passed.

Date: 2026-09-29

## Goal

Capture PostgreSQL and the Gitea volume hourly at one consistent point, store each set outside Finland, and make sure only complete, verified sets are restorable. A leaked writer credential must not be able to delete or replace any recovery point. This settles the items [decision 0006 deferred to milestone 4](0006-service-deployment-architecture.md#deferred-to-milestone-4).

## Decisions

### Pause Gitea by scaling it to zero

An hourly CronJob in the `gitea` namespace scales the Gitea Deployment to zero, runs `pg_dump`, archives the Gitea volume, and scales Gitea back to one. Encryption and upload run after Gitea is back, as [decision 0002](0002-recovery-contract.md#dependencies-and-boundaries) requires.

| Option | Trade-off |
| --- | --- |
| Scale to zero (selected) | The database and repositories match by construction, and restore needs no repair step. Gitea is down for the capture every hour, and the external probe sees it. |
| Dump while Gitea runs, repair with `gitea doctor` | No downtime. A push during the capture can leave repositories and the database out of step, so every restore needs a repair step whose result varies. |

The capture window is measured and recorded in the worklog. The drill must not count a scheduled capture as the outage start.

### Store sets as age-encrypted objects, not restic

Each backup set is a prefix `<cluster>/<UTC timestamp>/` holding:

1. The encrypted `pg_dump` custom-format archive.
2. The encrypted archive of the Gitea volume.
3. `manifest.json`, written last, with the SHA-256 digest and size of each encrypted object and the image versions that produced them.

A set is restorable only if its manifest exists and every digest matches. A job that fails before the manifest leaves a partial set that restore ignores. The job pings the backup-age heartbeat only after the manifest upload succeeds.

| Option | Trade-off |
| --- | --- |
| tar and `pg_dump` encrypted with age (selected) | Works with a create-only writer and a retention policy. Each set is self-contained and needs only `age`, `tar`, and `pg_restore` to restore. No deduplication, so every set stores a full copy. At lab data sizes that costs little. |
| restic, as decision 0002 planned | Deduplicates and encrypts. restic creates and deletes lock files on every run and deletes data during `prune`. A create-only writer and a retention policy block both, so restic would need delete permission, which lets a leaked credential remove every recovery point. |

This amends the application backup row in decision 0002.

### Encrypt to two age recipients, neither in the cluster

Backups are encrypted to two age public keys:

- A dedicated backup key, stored in the credential store with the other recovery credentials.
- An offline key, stored off any computer, for when the credential store is unavailable.

The cluster holds only the two public keys, in a ConfigMap. No backup password or private key exists in the cluster, so a compromised cluster cannot read old backups. The restore Job receives the backup private key through a temporary Secret that the operator creates for the restore and deletes afterwards.

The SOPS age key is not a recipient. It already exists in the cluster as `flux-system/sops-age`, so encrypting backups to it would let a cluster compromise decrypt every backup.

### Write with the worker node's service account, create-only per prefix

The backup pod authenticates as the worker VM's service account through the metadata server. `infra/shared` grants each entry in `backup_clusters`:

- `roles/storage.objectCreator`, conditioned to its own `<cluster>/` prefix. Overwriting an object needs `storage.objects.delete`, so the writer can neither delete nor replace a set.
- `roles/storage.objectViewer` on the whole bucket, so a recovery cluster can restore the primary's sets. The objects are encrypted and the cluster has no decryption key.

A network policy allows only the backup pod to reach the metadata server. The previous `roles/storage.objectAdmin` grant to a user account is removed; that account is a project owner and keeps administrative access.

| Option | Trade-off |
| --- | --- |
| Node service account (selected) | No key file to store or rotate. Any pod on the worker that bypasses the network policy could obtain the token, but the token cannot delete or overwrite backups. |
| Service account key in a SOPS Secret | Portable across clusters. It is a long-lived key to rotate, and each recovery cluster needs its own. |
| Workload identity federation | Per-pod identity without keys. Needs a public OIDC issuer for the kubeadm API server, which is more setup than this lab needs. |

### Fence a returning primary by removing its writer grant

Each cluster writes only under its own prefix: `primary/` and, from milestone 5, `recovery/`. After failover, the recovery runbook removes `primary` from `backup_clusters` and applies `infra/shared`. A primary that comes back can then no longer write backups, and restore never has to choose between two clusters' sets under one prefix.

### Keep sets undeletable for 14 days, unlocked

| Setting | Value | Reason |
| --- | --- | --- |
| Retention policy | 14 days, unlocked | Covers the hourly interval, a drill window, and about two weeks to notice tampering. No one, including a project owner, can delete a set inside the period without first removing the policy. |
| Lifecycle, live objects | Delete at 14 days | Keeps about 336 hourly sets. Lifecycle deletion waits for the retention period. |
| Lifecycle, noncurrent versions | Delete 1 day after becoming noncurrent | With versioning on, an expired set becomes noncurrent. Without this rule, noncurrent versions would accumulate without limit. |
| Soft delete | 7 days, explicit | Recovers from a mistaken deletion after retention ends. It matches the Cloud Storage default, now stated in code. |

An object therefore stays billed for about 22 days in total.

A locked policy cannot be shortened or removed, even by a project owner, and the bucket cannot be deleted until every object ages out. That protects against a compromised administrator, which is what production needs. The lab keeps the policy unlocked so it stays reversible. [Production readiness](../production-readiness.md) lists the locked policy.

### Alert on backup age with a Healthchecks.io heartbeat

The job pings a Healthchecks.io check after each verified set. The check expects a ping every hour with a one-hour grace period, so it alerts when no set has completed within the two-hour RPO. The ping URL is a SOPS Secret and a recovery credential, because the recovery cluster sends the same heartbeat.

| Option | Trade-off |
| --- | --- |
| Healthchecks.io (selected) | Runs outside both regions and GCP, needs no infrastructure, and the free tier covers one check. Adds an external account. |
| Cloud Monitoring alert on the newest object's age | Stays in GCP. Needs a custom metric or log-based metric and more Terraform, and it shares the project with what it watches. |

A scheduled automated restore test is deferred. It is added before the milestone 6 drill if the manual restores show it would catch failures the heartbeat misses.

## Consequences

- Gitea is unavailable for the capture window every hour.
- Recovery credentials gain the backup private key, the offline key, and the heartbeat ping URL.
- The restore procedure is a Kubernetes Job that milestone 5 reuses unchanged. Milestone 4 runs it in temporary namespaces on the primary cluster, so the test restore shares the worker with the live service.
- The backup job owns a Role that can scale the Gitea Deployment. Flux does not revert the replica count, because the HelmRelease has no drift detection; the job restores it in every exit path.
