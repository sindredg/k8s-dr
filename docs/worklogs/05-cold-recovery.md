# Cold recovery

Status: In progress. The gate has not run.

## Scope

Build an independent Gitea service in the recovery region from code and one verified backup set, for [milestone 5](../../plan.md#5-cold-recovery). [Decision 0008](../decisions/0008-cold-recovery.md) records the design.

## Work completed

### Shared Flux sync and suspended recovery backups

- `deploy/clusters/primary/sync.yaml` moved to `deploy/sync/`, with a `kustomization.yaml` so that each cluster directory can include it. The primary's `kustomization.yaml` now lists `../../sync`. The objects Flux applies are unchanged.
- `cluster-settings` gains `backup_suspend`, and the backup CronJob sets `suspend: ${backup_suspend}`. The primary sets `"false"`. A recovery cluster starts with `"true"`, so a bootstrap alone cannot write a `recovery/` set or ping the heartbeat while the primary is stopped. Enabling recovery backups is a reviewed change to the recovery `cluster-settings` after the restore passes its checks.
- `ansible/playbooks/restore.yml` reads the CronJob's `suspend` value before it pauses backups and resumes them only if they were running. Before this change it always resumed them, which would have enabled backups on a recovery cluster as a side effect of the restore.

Limitation: Flux now owns the `suspend` field. On a cluster whose settings enable backups, a Flux reconcile during a restore into `gitea` resets the pause that the playbook set. Restores into `gitea` are for a recovery cluster, where the settings keep backups suspended, and the test restore namespace has no CronJob.

Checked before the merge, without a cluster:

| Check | Command | Result |
| --- | --- | --- |
| The primary renders the same objects | `kubectl kustomize deploy/clusters/primary` on `main` and on the branch, then `diff` | One added line: `backup_suspend: "false"` in `cluster-settings` |
| The CronJob gains only the new field | `kubectl kustomize deploy/apps/gitea` on `main` and on the branch, then `diff` | One added line: `suspend: ${backup_suspend}` |
| The substituted value is a boolean | `envsubst` with `backup_suspend=false` on the render, then parse the CronJob | `suspend` is the boolean `false` |

`envsubst` stands in for Flux's substitution here. The cluster check after the merge is in the gate below.

## Gate

Not run. The steps are added with the changes they check.

1. After the merge, confirm that Flux applied the revision and that the primary CronJob is not suspended:

   ```bash
   make validate-services
   gcloud compute ssh <control-plane> --tunnel-through-iap -- \
     sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl get cronjob/gitea-backup \
     --namespace gitea --output=jsonpath='{.spec.suspend}'
   ```

   Expected: validation passes and the command prints `false`. The next hourly run writes a `primary/` set.
