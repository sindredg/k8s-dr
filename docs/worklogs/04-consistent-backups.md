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

## Validation

Pending.
