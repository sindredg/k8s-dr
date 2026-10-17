# Regional recovery

Status: Every step and the [return to the primary](#return-to-the-primary-after-a-drill) ran in drill 1 of [milestone 6](../../plan.md#6-disaster-drill) on 2026-10-04; drills 2 and 3 repeated them. [Decision 0008](../decisions/0008-cold-recovery.md) records the design. Since [milestone 10](../../plan.md#10-automated-recovery), `make recover` and the `Recover` workflow run steps 2 to 9.1, and `make return-to-primary` and the `Return` workflow run the return. On 2026-10-17 a full run of the `Recover` workflow, including the fence, the cutover, the checks through the public name, and the resumed backups, passed on the live system, and a run of the `Return` workflow then returned to the primary; see the [milestone 10 gate](../worklogs/10-automated-recovery.md#gate). The watchdog started nothing while its switch was off, or during a 99-second outage with the switch on; it has not started a run. Later the same day, a rerun after a cancelled `Recover` run and after a failed `Return` stage, the runner's own DNS wait, the return state's record of its recovery run, and a return after a rehearsal passed live; see [After the merge: verification](../worklogs/10-automated-recovery.md#after-the-merge-verification). The return's refusal of another recovery's state, the manual steps changed by milestone 10, and the account polling of [troubleshooting 07](../troubleshooting/07-service-account-read-lags.md) have not run live.

Every command against the recovery cluster sets `CLUSTER=recovery`. It selects `infra/recovery`, the recovery inventory, and `deploy/clusters/recovery`.

## Preconditions

- The primary service is isolated for the drill, and the external uptime check from [decision 0011](../decisions/0011-external-uptime-probe.md) is applied and passing.
- The `AUTO_FAILOVER` repository variable is unset, unless the drill tests the [watchdog](#start-automatically-with-the-watchdog). While it is `true`, isolating the primary starts a recovery.
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

`make recover` runs steps 2 to 9.1 below in order, as the stages of [decision 0013](../decisions/0013-automated-recovery.md#one-entry-point-runs-every-stage). Each stage ends with the check that its step states. The first failed command or check stops the run. Run the same command again after fixing the cause: it skips the stages that passed and starts at the failed one.

| Stage | Steps | What it checks |
| --- | --- | --- |
| `preflight` | Preconditions | `make preflight` with the `fixtures` line skipped, because a lost primary cannot pass it |
| `provisioning` | 3, 5 | The recovery plan deletes nothing and creates no service account or IAM grant, then the apply. `git-dr` resolves to the recovery address at 1.1.1.1 |
| `bootstrap` | 6 | `make inventory` and `make bootstrap` |
| `flux-reconcile` | 6 | `make validate-cluster`, `make validate-services`, the CronJob reports `suspend` as `true`, and `recovery/` holds no set named after the start of the run |
| `restore` | 2, 7 | Selects the newest set with a manifest once, then restores it |
| `verification` | 8 | `/api/healthz` on `git-dr` returns 200, `make check-fixtures` and `make write-check` on `git-dr`, and backups are still suspended |
| `fence` | 9.2 | Stops the primary VMs, and notes a failed stop without stopping the run. Disables the primary worker's account and requires `True` within two minutes. Requires that the primary's own name stops returning 200 within five minutes |
| `dns-cutover` | 9 | `git` resolves to the recovery address at 1.1.1.1 |
| `probe-recovery` | 9 | `/api/healthz`, `make check-fixtures`, and `make write-check` through `git.sindrg.com` |
| `resume-backups` | 9.1 | Creates the `run-settings` ConfigMap with `backup_suspend: "false"`, requests a reconcile of the `gitea` Kustomization, and requires that the CronJob reports `suspend` as `false` within ten minutes |

The fence runs before the cutover, so a primary that still answers stops the run before `git.sindrg.com` moves. The manual procedure fences after the cutover, as the drills ran it. The RTO ends with `probe-recovery`; `resume-backups` runs after the service is back.

```bash
make recover
```

Expected: every stage ends, and the run prints `Recovery complete` and the stage timeline. The first `recovery/` set starts at once if a scheduled slot passed in the last ten minutes, because of `startingDeadlineSeconds: 600`, and otherwise at the next slot. Compute the results as in step 10.

The run keeps its state in `DRILL_DIR`, by default `.drill/`:

- `recover.json` lists the stages that passed, the bucket, the recovery address, the selected set, and the restored HEAD. A rerun restores the same set and keeps the restored HEAD of the first check, not a later recovery write.
- `timeline.tsv` is the stage timeline of step 1. `python3 scripts/drill_report.py timeline` prints it.

Other forms:

- `make recover RECOVER_ARGS='--through verification'` stops after the checks on `git-dr`, before the fence. Use it to rehearse a recovery while the primary serves. Remove the recovery environment afterwards as in [Remove the recovery environment](#remove-the-recovery-environment).
- `python3 scripts/recover.py status` lists the stages that passed and the recorded values.
- `python3 scripts/recover.py reset` archives `recover.json` and `timeline.tsv`, so the next run starts from `preflight`.

Every run, including a rerun, first runs `terraform init` in the shared, primary, and recovery roots. A rerun after `provisioning` then requires that `infra/recovery` still reports the recorded recovery address, and regenerates the recovery inventory. A rerun on another machine therefore needs only the run state. A rerun after the recovery environment was removed stops with `the recovery environment that this run recorded no longer exists`; archive the state with `reset` and start again.

Only one run holds the lock in `DRILL_DIR` at a time. A second run exits with `another recovery run holds the lock`. The lock guards one machine only.

## Run the procedure from GitHub Actions

The `Recover` workflow runs `make recover` on a GitHub-hosted runner, as the pipeline account of [decision 0013](../decisions/0013-automated-recovery.md#amendment-pipeline-grants-and-run-state). It signs in through workload identity federation, reads both age keys from Secret Manager, and keeps the run state under `pipeline/drill/` in the state bucket, so a rerun on a new runner resumes at the failed stage.

Run either the workflow or `make recover` on an operator machine, not both: their lock files do not see each other. To continue a workflow run on an operator machine, start from an empty `.drill/`, copy the state with `STATE_BUCKET=<state bucket> python3 scripts/pipeline.py pull --drill-dir .drill`, run `make recover`, and copy the state back with `STATE_BUCKET=<state bucket> python3 scripts/pipeline.py push --drill-dir .drill` before the workflow runs again.

The workflow runs one recovery at a time. GitHub keeps one pending run, and a newer dispatch replaces it without starting it. Wait for a run to end before dispatching another.

### Set up the workflow once

Inputs: the applied shared root from the [primary infrastructure runbook](primary-infrastructure.md#apply-the-shared-root), the local `terraform.tfvars` and `backend.hcl` of `infra/shared` and `infra/recovery`, both age keys at their default paths, and `gh` signed in as a repository administrator.

1. Create the `recovery` environment, store the identifiers as its secrets, and add both age keys to Secret Manager. No value is printed.

   ```bash
   python3 scripts/pipeline.py setup
   ```

   Expected: `environment recovery: 7 secrets and 1 variable set; both age keys are in Secret Manager`. A rerun sets the same secrets again, checks an existing environment without replacing its rules, and adds no key version. It fails with `holds a different key` if the newest enabled version in Secret Manager holds another key than the local file.

2. Check that only `main` can deploy to the environment.

   ```bash
   gh api repos/sindredg/k8s-dr/environments/recovery/deployment-branch-policies --jq '.branch_policies[] | .type + " " + .name'
   gh secret list --env recovery
   ```

   Expected: `branch main` is the only policy, and the secret list names `ADMIN_MEMBER`, `BACKUP_BUCKET`, `GCP_PROJECT_ID`, `GCP_SERVICE_ACCOUNT`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, `RECOVERY_READER_MEMBER`, and `STATE_BUCKET`.

### Start a run

3. Start a run. `through` stops after a stage, as `--through` does; `verification` rehearses without the fence and the cutover. `reset=true` archives the state of an earlier run and starts at `preflight`.

   ```bash
   gh workflow run recover.yml --ref main -f through=verification -f reset=true
   gh run watch "$(gh run list --workflow recover.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
   ```

   Expected: the run ends with success, and its summary shows the stage timeline. The log shows the project ID, the project number, and the bucket names as `***`, also inside account email addresses.

4. If a stage fails, fix the cause and start the workflow again without `reset`.

   ```bash
   gh workflow run recover.yml --ref main -f through=verification
   ```

   Expected: the log shows `passed earlier, skipped` for each stage that passed, and the run continues at the failed one.

5. For a full recovery, start the workflow with `through=all`. After a rehearsal or a drill, remove the recovery environment as in [Remove the recovery environment](#remove-the-recovery-environment).

### Start automatically with the watchdog

The `Watchdog` workflow runs every five minutes and starts `Recover` with `through=all` and `reset=true` when the conditions of [decision 0013](../decisions/0013-automated-recovery.md#amendment-the-watchdog-workflow) hold: every uptime checker failed for five minutes, the runner cannot reach the public name or the primary's own name, the public name still points at the primary, no recovery runs, and the switch is on.

1. Turn the switch on, or off before planned work that stops the primary. Unset is off.

   ```bash
   gh variable set AUTO_FAILOVER --body true
   gh variable delete AUTO_FAILOVER
   ```

   Expected: `gh variable list` shows `AUTO_FAILOVER` with `true`, or does not list it.

2. Check what the last scheduled run decided.

   ```bash
   gh run view "$(gh run list --workflow watchdog.yml --limit 1 --json databaseId --jq '.[0].databaseId')" --log | grep -E 'no start|candidate=|started'
   ```

   Expected while the primary serves: `no start:` with the first condition that does not hold, and `candidate=false`. The `confirm` job is skipped.

3. If an automatic run fails, the watchdog starts nothing more until a newer `Recover` run succeeds. Fix the cause and resume the run as in step 4 of [Start a run](#start-a-run), without `reset`.

The `Drill` workflow tests this path with nobody present; see the [unattended drills runbook](unattended-drills.md).

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

   The stages are `detection`, `preflight`, `provisioning`, `bootstrap`, `flux-reconcile`, `restore`, `verification`, `fence`, `dns-cutover`, `probe-recovery`, and `resume-backups`. `make recover` records every stage after `detection` itself. Check `.drill/writes.tsv` before the isolation: the newest line must be less than `WRITE_INTERVAL` old. If the loop stopped, start it again and isolate only after a new write. Stop the write loop once its pushes fail.

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

   Expected: `true`, and the listing matches no objects or lists only sets named before the drill started. Sets from an earlier drill stay under `recovery/` for the retention period of [decision 0007](../decisions/0007-consistent-backups.md).

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

   1. Enable recovery backups: create the `run-settings` ConfigMap, which the `gitea` Kustomization reads after `cluster-settings`, and request a reconcile. Do not use `kubectl patch` on the CronJob; Flux reverts it. Git does not change.

      ```bash
      gcloud compute ssh "$RECOVERY_CP" --zone "$RECOVERY_ZONE" --tunnel-through-iap --command='sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl create configmap run-settings --namespace flux-system --from-literal=backup_suspend=false --dry-run=client --output=yaml | sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl apply --filename=-'
      gcloud compute ssh "$RECOVERY_CP" --zone "$RECOVERY_ZONE" --tunnel-through-iap -- \
        sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl annotate --overwrite --namespace flux-system \
        kustomization/gitea "reconcile.fluxcd.io/requestedAt=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      gcloud compute ssh "$RECOVERY_CP" --zone "$RECOVERY_ZONE" --tunnel-through-iap -- \
        sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl get cronjob/gitea-backup \
        --namespace gitea --output=jsonpath='{.spec.suspend}'; echo
      ```

      Expected: `false`, within the ten-minute timeout of the `gitea` Kustomization. The first `recovery/` set starts at once if a scheduled slot passed in the last ten minutes, and otherwise at the next slot.
   2. Fence the primary: disable the primary worker's service account, so a primary that returns cannot write backups. `infra/shared` ignores the account's `disabled` flag, so a later apply does not undo the fence.

      ```bash
      PRIMARY_WORKER="$(terraform -chdir=infra/shared output -json node_service_accounts | python3 -c 'import json, sys; print(json.load(sys.stdin)["primary/worker"])')"
      gcloud iam service-accounts disable "$PRIMARY_WORKER"
      gcloud iam service-accounts describe "$PRIMARY_WORKER" --format='value(disabled)'
      ```

      Expected: `True`. A read right after the change can still return the old value; read again after a minute. See [troubleshooting 07](../troubleshooting/07-service-account-read-lags.md).

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

3. Suspend recovery backups again, so the recovery cluster stops writing sets and pinging the shared heartbeat check before the primary's backups resume. Delete the `run-settings` ConfigMap and request a reconcile; `cluster-settings` then applies `"true"`. A new recovery cluster starts without the ConfigMap.

   ```bash
   gcloud compute ssh "$RECOVERY_CP" --zone "$RECOVERY_ZONE" --tunnel-through-iap -- \
     sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl delete configmap/run-settings --namespace flux-system
   gcloud compute ssh "$RECOVERY_CP" --zone "$RECOVERY_ZONE" --tunnel-through-iap -- \
     sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl annotate --overwrite --namespace flux-system \
     kustomization/gitea "reconcile.fluxcd.io/requestedAt=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
   ```

   Expected: the CronJob reports `suspend` as `true` within ten minutes, read as in step 9.1.

4. Lift the fence: enable the primary worker's service account again. The recovery grants stay; they belong to the recovery node account, not to the recovery VMs.

   ```bash
   PRIMARY_WORKER="$(terraform -chdir=infra/shared output -json node_service_accounts | python3 -c 'import json, sys; print(json.load(sys.stdin)["primary/worker"])')"
   gcloud iam service-accounts enable "$PRIMARY_WORKER"
   gcloud iam service-accounts describe "$PRIMARY_WORKER" --format='value(disabled)'
   ```

   Expected: an empty line or `False`, possibly only on a second read a minute later, as in step 9.2. Confirm that the next backup on the primary completes.

5. Delete the `git-dr` record and destroy the recovery root, as in steps 2 and 3 of the next section.

   ```bash
   make dns-delete DNS_NAME=git-dr
   ```

### Return with one command or from the workflow

`make return-to-primary` runs steps 1 to 5 as the stages of [decision 0013](../decisions/0013-automated-recovery.md#amendment-the-return-workflow), then waits for a primary backup. It reads the recovery run state in `DRILL_DIR`, resumes at a failed stage like `make recover`, and archives both run states after the last stage. `python3 scripts/recover.py reset` archives the return state too, and the return refuses to resume against another recovery run. It also removes the environment of a rehearsal that stopped before the fence: the primary then still serves, and the stages that change nothing pass.

| Stage | Steps | What it checks |
| --- | --- | --- |
| `start-primary` | 1 | `/api/healthz` on `git-primary` returns 200 within ten minutes, and `make check-fixtures` passes there. It does not run `make validate-cluster` or `make validate-services` |
| `dns-return` | 2 | `git` resolves to the primary address at 1.1.1.1 and at the runner's own resolver, then `/api/healthz`, `make check-fixtures`, and `make write-check` through `git.sindrg.com` |
| `suspend-recovery-backups` | 3 | The CronJob reports `suspend` as `true` within ten minutes. Skipped when the recovery never ran `resume-backups` |
| `lift-fence` | 4 | The primary worker account reports `disabled` as empty or `False` within two minutes |
| `remove-recovery` | 5, and steps 2 and 3 of the next section | `git-dr` has no answer at 1.1.1.1, the destroy plan only deletes and touches no account or IAM grant, and `terraform state list` in `infra/recovery` is empty after the apply |
| `primary-backup` | 4 | A set under `primary/` that started after the fence lifted has a manifest, within 20 minutes |

On an operator machine with the run state in `.drill/`:

```bash
make return-to-primary RETURN_ARGS=--discard-recovery-writes
```

Expected: every stage ends, the run prints `Return complete.` and the stage timeline, and `.drill/` holds the archived `recover-<time>.json`, `timeline-<time>.tsv`, `return-<time>.json`, and `return-<time>.tsv`. Without `--discard-recovery-writes` the command exits 1 and changes nothing. `python3 scripts/return_to_primary.py status` lists the stages that passed.

From GitHub Actions, with the run state of the `Recover` workflow:

```bash
gh workflow run return.yml --ref main -f discard_recovery_writes=true
gh run watch "$(gh run list --workflow return.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
```

Expected: the run ends with success, and its summary shows the return timeline. Without the input, the job is skipped. If a stage fails, fix the cause and start the workflow again; it resumes at that stage. The workflow shares the concurrency group of `Recover`, so it waits for a running recovery to end.

The `AUTO_FAILOVER` switch can stay as it is. While `git.sindrg.com` points at the recovery cluster, the watchdog starts nothing, and the name moves back only after the primary answers.

## Remove the recovery environment

After a gate that left the primary in service, remove the recovery environment. The recovery node accounts and their grants stay in `infra/shared`. [Return with one command or from the workflow](#return-with-one-command-or-from-the-workflow) runs these steps too.

1. Confirm that the primary worker's service account is enabled, as in step 4 of the previous section.
2. Delete the `git-dr` DNS record with `make dns-delete DNS_NAME=git-dr`, so the name does not point at a released address.
3. Destroy the recovery root.

   ```bash
   terraform -chdir=infra/recovery plan -destroy -out=recovery-destroy.tfplan
   terraform -chdir=infra/recovery apply recovery-destroy.tfplan
   ```

   Expected: `16 to destroy`. The plan lists no primary or shared resource and no service account.

4. Start the next run, from the workflow or with `make recover`, with its state archived: `reset=true` for the workflow, or `python3 scripts/recover.py reset` locally. A run that resumes the removed environment stops before the fence.

## Stop conditions and follow-up

Stop before traffic cutover if the backup is incomplete, the restore fails verification, or the recovered service cannot accept a new push. Preserve sanitized evidence for diagnosis. After a successful drill, update this runbook with tested commands, paths, timings, and rollback details; repeat the drill as required by [milestone 6](../../plan.md#6-disaster-drill).
