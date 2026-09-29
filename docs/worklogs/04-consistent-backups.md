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

### Restore Job

- Added `backup/restore.sh` to the `gitea-backup` ConfigMap and removed the ConfigMap's name hash, so a Job created outside Kustomize can mount it by name.
- Added `ansible/playbooks/restore.yml` and `make restore`. The playbook suspends the target's backup CronJob, creates the backup age key Secret, runs the `gitea-restore` Job, prints its log, and deletes the Secret and resumes the CronJob on every exit.
- The Job selects the newest set with a manifest or a named set, checks every size and digest, and decrypts both archives before it stops Gitea, recreates the database, and replaces the volume. It logs the backup age and the restore duration.
- The Job runs as the backup ServiceAccount with the backup Pod label, so the existing Role and network policies cover it.
- `RESTORE_NAMESPACE` has no default, so a restore cannot replace the live primary service by mistake.
- Added the [backup and restore procedure](../runbooks/backup-restore.md).

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

### Hourly backups run

After the fix, the CronJob ran every hour from 11:07 UTC on 2026-09-29. On 2026-09-29 at 14:34 UTC, `kubectl -n gitea get cronjob,jobs` showed the three retained Jobs `Complete` in 30 seconds each, and the bucket held four complete sets:

```text
$ gcloud storage ls -l "gs://<bucket>/primary/**"
     23895  2026-09-29T11:07:14Z  gs://<bucket>/primary/20260929T110707Z/gitea-data.tar.gz.age
       537  2026-09-29T11:07:14Z  gs://<bucket>/primary/20260929T110707Z/manifest.json
    365731  2026-09-29T11:07:13Z  gs://<bucket>/primary/20260929T110707Z/postgresql.dump.age
...
    365732  2026-09-29T14:07:06Z  gs://<bucket>/primary/20260929T140701Z/postgresql.dump.age
TOTAL: 12 objects, 1561864 bytes (1.49MiB)
```

The 12:07 run log:

```text
2026-09-29T12:07:01Z backup primary/20260929T120701Z started
2026-09-29T12:07:06Z Gitea stopped
2026-09-29T12:07:06Z resumed Gitea
2026-09-29T12:07:06Z capture paused Gitea for 4 seconds
2026-09-29T12:07:08Z uploaded primary/20260929T120701Z
deployment "gitea" successfully rolled out
2026-09-29T12:07:27Z backup primary/20260929T120701Z complete
```

`pause_seconds` measures from the scale-down request to the scale-up request. Gitea serves again only after its init containers and readiness probe pass, so the service was unavailable for up to 26 seconds, from 12:07:01 to 12:07:27. The drill must use that window, not `pause_seconds`, when it excludes a scheduled capture.

The newest set verified locally with the [runbook checks](../runbooks/backup-restore.md#check-the-backups): both digests matched the manifest, the backup key decrypted the volume archive and the offline key decrypted the dump, the volume archive contains `git/gitea-repositories/recovery-fixture/recovery-fixture.git` and `gitea/conf/app.ini`, and the dump starts with `PGDMP`.

The milestone gate is pending.
