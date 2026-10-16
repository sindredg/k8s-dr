# Regional recovery

Status: Every step and the [return to the primary](#return-to-the-primary-after-a-drill) ran in drill 1 of [milestone 6](../../plan.md#6-disaster-drill) on 2026-10-04, except the `suspend` check in step 6; see the [drill worklog](../worklogs/06-disaster-drill.md#drill-1). Drill 2 repeated it with the changes from drill 1 and no failed step. [Drill 3](../worklogs/07-faster-recovery.md#drill-3) ran the `suspend` check. [Decision 0008](../decisions/0008-cold-recovery.md) records the design. Amended on 2026-10-16 for [milestone 10](../../plan.md#10-automated-recovery): the node accounts and backup grants live in `infra/shared`, so step 4 is gone, and the fence disables the primary worker's account. The amended steps are written, not run. `make recover` runs the procedure in one command; see [Run the procedure with one command](#run-the-procedure-with-one-command). A rehearsal through `verification` passed against the live system on 2026-10-16; the fence, the cutover, and the checks through the public name have run against stubs only. See the [milestone 10 worklog](../worklogs/10-automated-recovery.md#step-2-one-command-for-every-stage).

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

## Run the procedure with one command

`make recover` runs steps 2 to 9 below in order, as the stages of [decision 0013](../decisions/0013-automated-recovery.md#one-entry-point-runs-every-stage). Each stage ends with the check that its step states. The first failed command or check stops the run. Run the same command again after fixing the cause: it skips the stages that passed and starts at the failed one.

| Stage | Steps | What it checks |
| --- | --- | --- |
| `preflight` | Preconditions | `terraform init` of the shared, primary, and recovery roots, then `make preflight` with the `fixtures` line skipped, because a lost primary cannot pass it |
| `provisioning` | 3, 5 | The recovery plan deletes nothing and creates no service account or IAM grant, then the apply. `git-dr` resolves to the recovery address at 1.1.1.1 |
| `bootstrap` | 6 | `make inventory` and `make bootstrap` |
| `flux-reconcile` | 6 | `make validate-cluster`, `make validate-services`, the CronJob reports `suspend` as `true`, and the `recovery/` prefix is empty |
| `restore` | 2, 7 | Selects the newest set with a manifest once, then restores it |
| `verification` | 8 | `/api/healthz` on `git-dr` returns 200, `make check-fixtures` and `make write-check` on `git-dr`, and backups are still suspended |
| `fence` | 9.2 | Stops the primary VMs, and notes a failed stop without stopping the run. Disables the primary worker's account and requires `True`. Requires that the primary's own name stops returning 200 within five minutes |
| `dns-cutover` | 9 | `git` resolves to the recovery address at 1.1.1.1 |
| `probe-recovery` | 9 | `/api/healthz`, `make check-fixtures`, and `make write-check` through `git.sindrg.com` |

The fence runs before the cutover, so a primary that still answers stops the run before `git.sindrg.com` moves. The manual procedure fences after the cutover, as the drills ran it.

```bash
make recover
```

Expected: every stage ends, and the run prints `Recovery complete` and the stage timeline. Then enable recovery backups as in step 9.1, and compute the results as in step 10.

The run keeps its state in `DRILL_DIR`, by default `.drill/`:

- `recover.json` lists the stages that passed, the bucket, the recovery address, the selected set, and the restored HEAD. A rerun restores the same set and keeps the restored HEAD of the first check, not a later recovery write.
- `timeline.tsv` is the stage timeline of step 1. `python3 scripts/drill_report.py timeline` prints it.

Other forms:

- `make recover RECOVER_ARGS='--through verification'` stops after the checks on `git-dr`, before the fence. Use it to rehearse a recovery while the primary serves. Remove the recovery environment afterwards as in [Remove the recovery environment](#remove-the-recovery-environment).
- `python3 scripts/recover.py status` lists the stages that passed and the recorded values.
- `python3 scripts/recover.py reset` archives `recover.json` and `timeline.tsv`, so the next run starts from `preflight`.

Only one run holds the lock in `DRILL_DIR` at a time. A second run exits with `another recovery run holds the lock`.

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

   The stages are `detection`, `preflight`, `provisioning`, `bootstrap`, `flux-reconcile`, `restore`, `verification`, `fence`, `dns-cutover`, and `probe-recovery`. `make recover` records every stage after `detection` itself. Check `.drill/writes.tsv` before the isolation: the newest line must be less than `WRITE_INTERVAL` old. If the loop stopped, start it again and isolate only after a new write. Stop the write loop once its pushes fail.

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

   Expected: `Plan: 16 to add, 0 to change, 0 to destroy`. The plan contains one Belgium VPC and subnet, one NAT, two private VMs, the worker data disk, and the load balancer. It creates no service account or IAM member: the VMs attach the recovery node accounts that `infra/shared` keeps, with their backup grants, whether or not a recovery runs. It reads and changes nothing in Finland.

4. This step is no longer needed. Since [milestone 10](../../plan.md#10-automated-recovery), the recovery worker holds its backup grants before the recovery starts. The step number stays so the drill worklogs still match.

5. Create the `git-dr` DNS record for the recovery address. Leave `git.sindrg.com` unchanged. The command writes a DNS-only record with a 60-second TTL through the Cloudflare API; the dashboard is the fallback. See [decision 0002](../decisions/0002-recovery-contract.md#amendment-change-dns-records-through-the-cloudflare-api).

   ```bash
   RECOVERY_ADDRESS="$(terraform -chdir=infra/recovery output -raw public_web_address)"
   make dns-set DNS_NAME=git-dr DNS_ADDRESS="$RECOVERY_ADDRESS"
   dig +short git-dr.sindrg.com @1.1.1.1
   ```

   Expected: the command prints the new record, and `dig` prints the recovery address.

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
   until [ "$(curl -s -m 5 -o /dev/null -w '%{http_code}' https://git-dr.sindrg.com/api/healthz)" = 200 ]; do sleep 5; done
   make check-fixtures GIT_HOST=git-dr.sindrg.com
   ```

   Expected: the wait ends within a minute of the restore, and the recap ends with `failed=0`. In drill 1 the check failed when it ran ten seconds after the restore, before the endpoint answered. Record the newest commit that it reports before anything writes to the recovery copy: it is the restored HEAD, and the data-loss report needs it. Then push a new commit:

   ```bash
   make write-check GIT_HOST=git-dr.sindrg.com
   ```

   Expected: `failed=0`. Together these sign in, find the known commit and issue, and push a new commit. Investigate any failure before proceeding. Repeat the two commands from step 6 and expect the same results: the restore must not have enabled backups.

9. Cut over. Point `git.sindrg.com` at the recovery address.

   ```bash
   make dns-set DNS_NAME=git DNS_ADDRESS="$RECOVERY_ADDRESS"
   until [ "$(dig +short git.sindrg.com @1.1.1.1)" = "$RECOVERY_ADDRESS" ]; do sleep 5; done
   ```

   Expected: the wait ends within the 60-second TTL. Confirm the external probe reports a healthy service and repeat `make check-fixtures` and `make write-check` without `GIT_HOST`, which targets `git.sindrg.com`. Record the time when all checks pass. Then:

   1. Enable recovery backups: merge a change that sets `backup_suspend: "false"` in `deploy/clusters/recovery/cluster-settings.yaml` and in the expected settings in `tests/test_services.py`, which pins the value. Do not use `kubectl patch`; Flux reverts it. The next scheduled run writes the first `recovery/` set.
   2. Fence the primary: disable the primary worker's service account, so a primary that returns cannot write backups. `infra/shared` ignores the account's `disabled` flag, so a later apply does not undo the fence.

      ```bash
      PRIMARY_WORKER="$(terraform -chdir=infra/shared output -json node_service_accounts | python3 -c 'import json, sys; print(json.load(sys.stdin)["primary/worker"])')"
      gcloud iam service-accounts disable "$PRIMARY_WORKER"
      gcloud iam service-accounts describe "$PRIMARY_WORKER" --format='value(disabled)'
      ```

      Expected: `True`.

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

## Return to the primary after a drill

A drill ends by returning to the stopped primary, which still holds its data as of the isolation. Writes that the recovery cluster accepted are discarded. This is not a failback, and it does not apply after a real loss. See [decision 0008](../decisions/0008-cold-recovery.md#amendment-return-to-the-primary-after-a-drill).

1. Start the primary VMs and wait for the service. Validate it through its own name while `git.sindrg.com` still points at the recovery cluster.

   ```bash
   gcloud compute instances start <primary control plane> <primary worker> --zone <primary zone>
   curl -s -o /dev/null -w '%{http_code}\n' https://git-primary.sindrg.com/api/healthz
   make validate-cluster
   make validate-services
   make check-fixtures GIT_HOST=git-primary.sindrg.com
   ```

   Expected: `200`, about three minutes after the start, then `failed=0` three times. `make validate-cluster` waits for the API server and took 147 seconds in drill 1. The newest commit is the last write before the isolation.

2. Point `git.sindrg.com` back at the primary address and check through the public name.

   ```bash
   make dns-set DNS_NAME=git DNS_ADDRESS="$(terraform -chdir=infra/primary output -raw public_web_address)"
   make check-fixtures
   make write-check
   ```

3. Suspend recovery backups again: merge a change that sets `backup_suspend: "true"` in `deploy/clusters/recovery/cluster-settings.yaml` and in `tests/test_services.py`. The next recovery bootstrap must not write a set.

4. Lift the fence: enable the primary worker's service account again. The recovery grants stay; they belong to the recovery node account, not to the recovery VMs.

   ```bash
   PRIMARY_WORKER="$(terraform -chdir=infra/shared output -json node_service_accounts | python3 -c 'import json, sys; print(json.load(sys.stdin)["primary/worker"])')"
   gcloud iam service-accounts enable "$PRIMARY_WORKER"
   gcloud iam service-accounts describe "$PRIMARY_WORKER" --format='value(disabled)'
   ```

   Expected: an empty line or `False`. Confirm that the next backup on the primary completes.

5. Delete the `git-dr` record and destroy the recovery root, as in steps 2 and 3 of the next section.

   ```bash
   make dns-delete DNS_NAME=git-dr
   ```

## Remove the recovery environment

After a gate that left the primary in service, remove the recovery environment. The recovery node accounts and their grants stay in `infra/shared`.

1. Confirm that the primary worker's service account is enabled, as in step 4 of the previous section.
2. Delete the `git-dr` DNS record with `make dns-delete DNS_NAME=git-dr`, so the name does not point at a released address.
3. Destroy the recovery root.

   ```bash
   terraform -chdir=infra/recovery plan -destroy -out=recovery-destroy.tfplan
   terraform -chdir=infra/recovery apply recovery-destroy.tfplan
   ```

   Expected: `16 to destroy`. The plan lists no primary or shared resource and no service account.

## Stop conditions and follow-up

Stop before traffic cutover if the backup is incomplete, the restore fails verification, or the recovered service cannot accept a new push. Preserve sanitized evidence for diagnosis. After a successful drill, update this runbook with tested commands, paths, timings, and rollback details; repeat the drill as required by [milestone 6](../../plan.md#6-disaster-drill).
