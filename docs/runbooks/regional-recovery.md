# Regional recovery

Status: Draft. Steps 2 to 8 carry the commands for the [milestone 5](../../plan.md#5-cold-recovery) gate and have not been run end to end. Steps 1, 9, and 10 belong to the [milestone 6](../../plan.md#6-disaster-drill) drill. [Decision 0008](../decisions/0008-cold-recovery.md) records the design.

Every command against the recovery cluster sets `CLUSTER=recovery`. It selects `infra/recovery`, the recovery inventory, and `deploy/clusters/recovery`.

## Preconditions

- The primary service is isolated for the drill, and the external uptime check from [decision 0011](../decisions/0011-external-uptime-probe.md) is applied and passing.
- The operator can access the external source repository, remote Terraform state, recovery-region credentials, offsite backup storage, and DNS or endpoint controls without using the primary region.
- The SOPS age key and the backup age key are on the operator machine, restored from the credential store if needed. The defaults are `~/.config/k8s-dr/age.agekey` and `~/.config/k8s-dr/backup.agekey`.
- A verified backup set contains PostgreSQL data and Gitea repositories and configuration from one consistent recovery point. Its timestamp and integrity check are recorded.
- The fixture user, repository, commit, and issue identifiers are in `recovery/fixtures.yaml`, and the last acknowledged write time from `make write-check` is recorded outside the failed region. See [recovery fixtures](service-deployment.md#recovery-fixtures).

Check the preconditions that a machine can check, before the primary is isolated:

```bash
make preflight
```

Expected: every line is `ok`, the summary reports `0 failed, 0 missing`, and the exit status is `0`. `MISSING` names a prerequisite that is absent, such as a key file or a local Terraform file. `FAIL` names a check that ran and failed. The one `manual` line asks for a dashboard sign-in that the command cannot test. The command reads state, the bucket, the service, and the Cloudflare API, and changes nothing. In a real loss of the primary, the `fixtures` line fails; the other lines still apply.

| Line | What passes |
| --- | --- |
| `git-source` | GitHub serves the branch that Flux reads |
| `key-sops`, `key-backup` | Each local age key is a recipient that the repository encrypts for |
| `fixture-secret` | The fixture password decrypts |
| `state-shared`, `state-recovery` | `terraform plan` reads each remote state |
| `backup-set` | The newest complete set matches its manifest, both archives decrypt, and it is within the RPO |
| `fixtures` | `make check-fixtures` passes on the serving cluster |
| `cloudflare-token`, `cloudflare-zone` | The certificate token is active for at least a week, and `git.sindrg.com` is DNS only with a 60-second TTL |

If any prerequisite is missing, record it as a blocked drill. Do not route users to an unverified restore.

## Procedure

1. Before isolation, start the write loop in a second terminal and leave it running. It pushes a write check every five minutes and appends each acknowledged write to `.drill/writes.tsv` on the operator machine.

   ```bash
   make write-loop
   ```

   Record the isolation time and the first failed probe time in UTC. Record each stage as it starts and ends, and every failure, retry, and manual action in the stage where it happened:

   ```bash
   python3 scripts/drill_report.py stage provisioning start
   python3 scripts/drill_report.py stage provisioning note --note "plan showed 21 resources; reran init"
   python3 scripts/drill_report.py stage provisioning end
   ```

   The stages are `detection`, `provisioning`, `bootstrap`, `flux-reconcile`, `restore`, `verification`, `dns-cutover`, and `probe-recovery`. Stop the write loop once its pushes fail.

2. Select the newest backup set that has a manifest, and record its name. The name is its recovery-point timestamp in UTC. Exclude sets without a manifest.

   ```bash
   BUCKET="$(terraform -chdir=infra/shared output -raw backup_bucket_name)"
   gcloud storage ls "gs://${BUCKET}/primary/*/manifest.json" | tail -n 3
   ```

   Expected: the newest set is at most one backup interval older than the outage start. The restore selects the same set unless `RESTORE_SET` names another.

3. Provision the recovery VMs and network. Keep existing local files; do not copy the examples over them.

   ```bash
   cd infra/recovery
   umask 077
   if [ ! -e terraform.tfvars ]; then cp terraform.tfvars.example terraform.tfvars; fi
   if [ ! -e backend.hcl ]; then cp backend.hcl.example backend.hcl; fi
   chmod 600 terraform.tfvars backend.hcl
   # Edit both files and replace every placeholder.
   terraform init -backend-config=backend.hcl
   terraform plan -out=recovery.tfplan
   terraform apply recovery.tfplan
   cd ../..
   ```

   Expected: `Plan: 20 to add, 0 to change, 0 to destroy`. The plan contains one Belgium VPC and subnet, one NAT, two private VMs, the worker data disk, the load balancer, and two service accounts. It reads and changes nothing in Finland.

4. Let the recovery worker read the backups. The grant fails before the account exists, so this follows step 3. Add the recovery line to `backup_clusters` in the local `infra/shared/terraform.tfvars`, as in `terraform.tfvars.example`, then apply.

   ```bash
   terraform -chdir=infra/shared plan -out=shared.tfplan
   terraform -chdir=infra/shared apply shared.tfplan
   ```

   Expected: `Plan: 2 to add, 0 to change, 0 to destroy`: one object creator grant limited to `recovery/` and one object viewer grant. If the uptime check is not applied yet, the plan also adds it.

5. Create the `git-dr` DNS record for the recovery address, as in the [service deployment runbook](service-deployment.md#public-endpoint). Leave `git.sindrg.com` unchanged.

   ```bash
   terraform -chdir=infra/recovery output -raw public_web_address; echo
   dig +short git-dr.sindrg.com @1.1.1.1
   ```

   Expected: both print the same address.

6. Build the cluster and let Flux deploy the service.

   ```bash
   make inventory CLUSTER=recovery
   make bootstrap CLUSTER=recovery
   make validate-cluster CLUSTER=recovery
   make validate-services CLUSTER=recovery
   ```

   Expected: each recap ends with `failed=0`. Both nodes are `Ready`, Flux applied `deploy/clusters/recovery`, and `git-tls` and `git-cluster-tls` are Ready. Gitea runs with an empty database.

   Confirm that the bootstrap did not start backups:

   ```bash
   RECOVERY_CP="$(terraform -chdir=infra/recovery output -json instance_names | python3 -c 'import json,sys; print(json.load(sys.stdin)["control-plane"])')"
   RECOVERY_ZONE="$(terraform -chdir=infra/recovery output -raw zone)"
   gcloud compute ssh "$RECOVERY_CP" --zone "$RECOVERY_ZONE" --tunnel-through-iap -- \
     sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl get cronjob/gitea-backup \
     --namespace gitea --output=jsonpath='{.spec.suspend}'; echo
   gcloud storage ls "gs://${BUCKET}/recovery/"
   ```

   Expected: `true`, and the listing matches no objects.

7. Restore PostgreSQL and Gitea data from the set selected in step 2. See the [backup and restore procedure](backup-restore.md#restore-a-set).

   ```bash
   make restore CLUSTER=recovery RESTORE_NAMESPACE=gitea
   ```

   Expected: the recap ends with `failed=0`, and the log names the set, reports matching digests, and ends with `restored <set> in <n> seconds`. The CronJob stays suspended.

8. Test the recovery endpoint before changing public routing. The recovery cluster already serves `git.sindrg.com` with its own certificate; `git-dr.sindrg.com` reaches it directly. See [decision 0008](../decisions/0008-cold-recovery.md#serve-the-public-host-and-a-per-cluster-host-from-every-cluster).

   ```bash
   make check-fixtures GIT_HOST=git-dr.sindrg.com
   ```

   Expected: the recap ends with `failed=0`. Record the newest commit that it reports before anything writes to the recovery copy: it is the restored HEAD, and the data-loss report needs it. Then push a new commit:

   ```bash
   make write-check GIT_HOST=git-dr.sindrg.com
   ```

   Expected: `failed=0`. Together these sign in, find the known commit and issue, and push a new commit. Investigate any failure before proceeding. Repeat the two commands from step 6 and expect the same results: the restore must not have enabled backups.

9. Cut over. Change the Cloudflare DNS target for `git.sindrg.com` to the recovery address. Confirm the external probe reports a healthy service and repeat `make check-fixtures` and `make write-check` without `GIT_HOST`, which targets `git.sindrg.com`. Record the time when all checks pass. Then:

   1. Enable recovery backups: merge a change that sets `backup_suspend: "false"` in `deploy/clusters/recovery/cluster-settings.yaml`. Do not use `kubectl patch`; Flux reverts it.
   2. Fence the primary: remove `primary` from `backup_clusters` in `infra/shared/terraform.tfvars` and apply `infra/shared`, so a primary that returns cannot write backups.

10. Compute the results and record them in the [drill worklog](../worklogs/06-disaster-drill.md).

    ```bash
    python3 scripts/drill_report.py rpo --restored-sha <restored HEAD> \
      --isolated-at <isolation time, such as 2026-10-10T12:00:00Z> --backup-set <restored set>
    python3 scripts/drill_report.py timeline
    ```

    - RTO runs from the first failed external probe to the time all checks pass through `git.sindrg.com`.
    - Observed data loss runs from the last write acknowledged before isolation to the acknowledgement time of the restored HEAD. The report lists every acknowledged write that the restore lost. Writes after isolation went to the recovery cluster and are ignored, so a recovery push cannot hide a lost primary write.
    - The potential loss window is the backup age at isolation. Report it separately.
    - Explain any gap between the sum of the stage durations and the externally measured RTO. Record failures, manual steps, and cost.

## Remove the recovery environment

After a gate or a drill that returns to the primary, remove the recovery environment. The order matters: a grant for a deleted service account makes later `infra/shared` plans fail.

1. Remove the `recovery` line from `backup_clusters` in `infra/shared/terraform.tfvars`, then plan and apply `infra/shared`. Expected: `2 to destroy`.
2. Delete the `git-dr` DNS record in Cloudflare, so the name does not point at a released address.
3. Destroy the recovery root.

   ```bash
   terraform -chdir=infra/recovery plan -destroy -out=recovery-destroy.tfplan
   terraform -chdir=infra/recovery apply recovery-destroy.tfplan
   ```

   Expected: `20 to destroy`. The plan lists no primary or shared resource.

## Stop conditions and follow-up

Stop before traffic cutover if the backup is incomplete, the restore fails verification, or the recovered service cannot accept a new push. Preserve sanitized evidence for diagnosis. After a successful drill, update this runbook with tested commands, paths, timings, and rollback details; repeat the drill as required by [milestone 6](../../plan.md#6-disaster-drill).
