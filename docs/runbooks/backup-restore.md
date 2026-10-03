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
3. Prints the Job log and deletes the key Secret, whether the Job passed or failed. It resumes the CronJob only if the CronJob was running before the restore. A recovery cluster keeps backups suspended through `backup_suspend` in its `cluster-settings` until the restore is verified.

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

## Test a restore

The test environment copies the service into the `gitea-restore` and `postgresql-restore` namespaces on the same cluster. It has no Gateway and no backup CronJob, so it serves no public traffic and writes no sets. It shares the worker with the live service.

`make restore-test-env` creates the namespaces, copies the `postgresql-credentials`, `gitea-admin`, `gitea-database`, and `gitea-backup` Secrets from the live namespaces, and applies the Flux Kustomizations in `deploy/restore-test/flux.yaml`. SOPS Secrets cannot be applied in another namespace, because their MAC covers the namespace. Flux builds the overlays from the branch it tracks, so merge overlay changes before you run the test.

1. Create the environment. The first Gitea install runs its migrations.

   ```bash
   make restore-test-env
   ```

   Expected: `failed=0`, and `deployment "gitea" successfully rolled out` for `gitea-restore`.

2. Restore the newest set into it.

   ```bash
   make restore RESTORE_NAMESPACE=gitea-restore
   ```

   Expected: as in [Restore a set](#restore-a-set). Record the set, backup age, restore duration, and the `run_with_iap` elapsed time.

3. In a second terminal, forward `localhost:3000` to the restored Gitea. The command holds the terminal.

   ```bash
   make restore-test-forward
   ```

4. Check the fixtures and push a new commit through the forward.

   ```bash
   make check-fixtures GIT_URL=http://localhost:3000
   make write-check GIT_URL=http://localhost:3000
   ```

   Expected: both recaps end with `failed=0`. `check-fixtures` finds the fixture commit and issue; `write-check` prints the accepted push. The push reaches only the test copy.

5. Stop the forward with Ctrl+C and delete the environment and its data.

   ```bash
   make restore-test-env-delete
   ```

   Expected: `failed=0`. `kubectl get namespace gitea-restore postgresql-restore` reports `NotFound`.

## Limitations

- The restore replaces all data in the target pair. It does not merge.
- A set written by a compromised cluster can pass the digest checks, because the age public keys are public. The fixture checks are the acceptance test. See [decision 0007](../decisions/0007-consistent-backups.md#write-with-the-worker-nodes-service-account-create-only-per-prefix).
