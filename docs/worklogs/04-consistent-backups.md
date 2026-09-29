# Consistent backups

Status: In progress. The [milestone 4 gate](../../plan.md#4-consistent-backups) has not passed.

## Scope

Hourly consistent backups of PostgreSQL and the Gitea volume, stored outside Finland in a hardened bucket, and a verified restore into a separate environment. [Decision 0007](../decisions/0007-consistent-backups.md) records the design.

## Work completed

### Decision record

- Added [decision 0007](../decisions/0007-consistent-backups.md). It settles the items decision 0006 deferred to milestone 4 and replaces restic with age-encrypted objects, because restic needs delete permission that the create-only writer and the retention policy remove.
- Amended decision 0002's application backup row and linked decision 0006's deferred items and the production readiness row to decision 0007.

### Harden the backup bucket

`infra/shared` changes:

- A 14-day unlocked retention policy, an explicit 7-day soft delete policy, a lifecycle rule that deletes live objects at 14 days, and one that deletes noncurrent versions 1 day after they become noncurrent.
- A new `backup_clusters` map from cluster name to its worker node service account. Each entry gets `roles/storage.objectCreator` conditioned to its `<cluster>/` prefix and `roles/storage.objectViewer` on the bucket.
- Removed the `backup_operator_member` variable and its `roles/storage.objectAdmin` grant. That account is a project owner.

`terraform plan` for `infra/shared` on 2026-09-29, summarized:

```text
~ google_storage_bucket.backups: add 2 lifecycle_rule blocks and retention_policy (1209600 s, unlocked)
- google_storage_bucket_iam_member.backup_operator (roles/storage.objectAdmin)
+ google_storage_bucket_iam_member.backup_reader["primary"] (roles/storage.objectViewer)
+ google_storage_bucket_iam_member.backup_writer["primary"] (roles/storage.objectCreator, primary/ prefix condition)
Plan: 2 to add, 1 to change, 1 to destroy.
```

The soft delete policy shows no change because the explicit value matches the existing default.

### Backup-age heartbeat

Created the Healthchecks.io check `k8s-dr-backup` with a 1-hour period and a 1-hour grace time, so it alerts when no verified set completes within the 2-hour RPO. The ping URL is held with the recovery credentials and is not in the repository.

![Healthchecks.io check with a 1-hour period and grace time](../images/healthchecks-backup-check.png)

### Backup tool image

- Added `images/backup/Dockerfile`: the pinned PostgreSQL 18.6 image plus `age`, `curl`, `jq`, and `kubectl` 1.36.2 verified by SHA-256.
- Added the `Backup image` workflow. It builds on pull requests and publishes `ghcr.io/sindredg/k8s-dr-backup` from `main`.
- `scripts/check_pins.py` now checks GHCR digests as well as Docker Hub digests.
- Corrected decision 0007: other pods on the worker can reach the metadata server, and the decision records why that exposure is accepted.

### Backup CronJob

- Added the `gitea-backup` CronJob in `deploy/apps/gitea/backup.yaml`. It runs at minute 7 of every hour, one run at a time, with no retries and a 30-minute deadline.
- `backup/backup.sh` scales Gitea to zero, streams `pg_dump` and the Gitea volume through `age` to both public keys, scales Gitea back to one, uploads each object with `Content-MD5` and `x-goog-if-generation-match: 0`, and uploads `manifest.json` last. It pings the heartbeat at start, on success with the pause duration, and on failure.
- The script and the age public keys ship in a generated ConfigMap with Flux substitution disabled. `cluster-settings` gains `backup_cluster: primary` for the object prefix.
- `backup.sops.yaml` holds the bucket name and the ping URL, encrypted for the SOPS key.
- The Role can read the Gitea Deployment and Pods and patch only the `gitea` scale subresource.
- Network policies allow the backup Pod to reach PostgreSQL, the metadata server on port 80, and any address on ports 443 and 6443. PostgreSQL admits it on port 5432. Tests list every allowed flow.

## Validation

### Bucket hardening applied

`terraform apply` in `infra/shared` on 2026-09-29 applied the plan above:

```text
Apply complete! Resources: 2 added, 1 changed, 1 destroyed.
```

![Apply of the backup bucket hardening](../images/backup-bucket-apply.png)

A following `terraform plan` reported `No changes. Your infrastructure matches the configuration.`

### First reconciliation of the CronJob failed

- **Symptom:** after the CronJob change merged, the `gitea` Kustomization reported `ConfigMap/gitea-backup-9gf79t552m namespace not specified` and applied nothing. The running service was unaffected.
- **Cause:** the `gitea` Kustomization sets no default namespace, and a `configMapGenerator` entry does not inherit one from the other resources. `kubectl kustomize` renders the object without complaint; only the API server rejects it.
- **Fix:** set `namespace: gitea` on the generator. A test now requires it.

The milestone gate is pending.
