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

### Per-cluster hostname

- `cluster-settings` gains `git_cluster_host`; the primary sets `git-primary.sindrg.com`.
- A second Certificate, `git-cluster-tls`, covers that name. Each name has its own certificate, so a cluster build uses one `git.sindrg.com` certificate of the five that Let's Encrypt allows per week.
- The `gitea` Gateway gains the listeners `http-cluster` and `https-cluster` for that name. Both HTTPRoutes attach to the listeners of both names.
- `validate_services.yml` waits for both certificates, requires each route to be accepted by both listeners, and checks the HTTP redirect for both names.

Checked before the merge: `kubectl kustomize deploy/apps/gitea`, substituted with the primary settings, renders four listeners (`git.sindrg.com` and `git-primary.sindrg.com` on 8000 and 8443) and both names on each route.

Not yet shown on a cluster: that the existing `git.sindrg.com` listeners keep serving while `git-cluster-tls` is being issued. A first install has the same order (the Gateway exists before `git-tls` does), so this is expected to hold.

### Cluster selection and recovery settings

- `CLUSTER ?= primary` in the `Makefile` selects the Terraform root `infra/$(CLUSTER)`, the inventory `ansible/inventory/generated/$(CLUSTER)/hosts.json`, the Flux directory through `flux_cluster`, and the settings file that gives the fixture targets their default host.
- The inventory moved from `ansible/inventory/generated/hosts.json` to a directory per cluster. The IAP runner keeps `known_hosts` beside the inventory, so host keys are per cluster too. Run `make inventory` once after this change to write the primary inventory at its new path.
- `deploy/clusters/recovery/` holds the recovery settings: `git_host` `git.sindrg.com`, `git_cluster_host` `git-dr.sindrg.com`, backup prefix `recovery`, and `backup_suspend` `"true"`.

Checked before the merge: `make -n bootstrap CLUSTER=recovery` prints the recovery inventory and `-e flux_cluster=recovery`; `kubectl kustomize deploy/clusters/recovery` renders the same five Flux Kustomizations as the primary.

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

2. Create the `git-primary` DNS record as in the [service deployment runbook](../runbooks/service-deployment.md#public-endpoint), then check the primary through its own name:

   ```bash
   dig +short git-primary.sindrg.com @1.1.1.1
   make validate-services
   make check-fixtures GIT_HOST=git-primary.sindrg.com
   make check-fixtures
   ```

   Expected: the primary's public address; validation shows `git-tls` and `git-cluster-tls` Ready from `letsencrypt-production`; the fixture checks pass through both names.
