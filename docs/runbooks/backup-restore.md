# Backup and restore: operator procedure

Check the hourly backups and restore a backup set with the restore Job. [Decision 0007](../decisions/0007-consistent-backups.md) records the design. Record observed results in the [milestone 4 worklog](../worklogs/04-consistent-backups.md).

## Prerequisites

- The generated inventory exists for the target cluster. See [Kubernetes bootstrap](kubernetes-bootstrap.md).
- The backup age private key is at `~/.config/k8s-dr/backup.agekey`, or `BACKUP_AGE_KEY_FILE` names its path.
- The target cluster runs the `gitea` Flux Kustomization, which ships the restore script in the `gitea-backup` ConfigMap.

## Check the backups

1. List the complete sets. A set is complete only when it has `manifest.json`.

   ```bash
   BUCKET="$(terraform -chdir=infra/shared output -raw backup_bucket_name)"
   gcloud storage ls "gs://${BUCKET}/primary/*/manifest.json"
   ```

   Expected: one set per hour, named by its UTC start time, such as `primary/20260929T140701Z/`.

2. Verify one set locally without the cluster. Run from an empty directory.

   ```bash
   SET=20260929T140701Z
   gcloud storage cp "gs://${BUCKET}/primary/${SET}/*" .
   jq -r '.files[] | "\(.sha256)  \(.name)"' < manifest.json | sha256sum -c
   age -d -i ~/.config/k8s-dr/backup.agekey gitea-data.tar.gz.age | tar -tzf - | head
   age -d -i ~/.config/k8s-dr/backup.agekey postgresql.dump.age | head -c 5; echo
   ```

   Expected: both files `OK`, a file list that includes `git/gitea-repositories/`, and `PGDMP`. Delete the downloaded files afterwards.

## Restore a set

`make restore` runs `ansible/playbooks/restore.yml`, which:

1. Suspends the target's backup CronJob, if it has one, and waits for a running backup to finish.
2. Creates the `gitea-restore-key` Secret from the backup age key and starts the `gitea-restore` Job.
3. Prints the Job log, deletes the key Secret, and resumes the CronJob, whether the Job passed or failed.

The Job selects the newest set with a manifest, or the set that `RESTORE_SET` names. It checks every size and SHA-256 digest against the manifest and decrypts both archives before it changes anything. It then stops Gitea, drops and recreates the `gitea` database, restores the dump, replaces the Gitea volume, and starts Gitea. If a step fails after the database is dropped, Gitea stays stopped; fix the cause and run the restore again.

`RESTORE_NAMESPACE` has no default, so the command cannot replace the live primary service by mistake. Use `gitea` on a recovery cluster and `gitea-restore` for a test restore.

```bash
make restore RESTORE_NAMESPACE=gitea
# Or restore a named set:
make restore RESTORE_NAMESPACE=gitea RESTORE_SET=20260929T140701Z
```

Expected: the recap ends with `failed=0`, and the log includes:

```text
restoring primary/<set>; backup age at restore start <n> seconds
digests match the manifest
both archives decrypt and read
restored <set> in <n> seconds
```

Record the set, the backup age, and the restore duration. Then run the fixture checks against the restored service before routing users to it.

## Limitations

- The restore replaces all data in the target pair. It does not merge.
- A set written by a compromised cluster can pass the digest checks, because the age public keys are public. The fixture checks are the acceptance test. See [decision 0007](../decisions/0007-consistent-backups.md#write-with-the-worker-nodes-service-account-create-only-per-prefix).
