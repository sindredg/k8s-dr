# Automated recovery

Status: In progress. Step 1 is applied and verified on the live system on 2026-10-16; see [Migration after the merge](#migration-after-the-merge). Step 2 is built, and a rehearsal through `verification` passed on the live system on 2026-10-16; see [Rehearsal against the live system](#rehearsal-against-the-live-system). Its fence, cutover, and checks through the public name have not run live. Steps 3 to 6 are planned. This file is the record of [milestone 10](../../plan.md#10-automated-recovery).

## Step 1: node identities and backup grants in the shared root

[Decision 0013](../decisions/0013-automated-recovery.md#node-identities-and-backup-grants-move-to-the-shared-root) moves the node service accounts and their bucket grants from the regional roots to `infra/shared`.

Before this step, `infra/shared` granted backup access to accounts that the regional roots created. A first build had to apply the primary root before the shared root, which deviation 2 of the [milestone 8 rebuild](08-rebuild-from-runbooks.md#deviations) recorded. A recovery had to add the recovery worker to `backup_clusters` and apply the shared root, and the fence removed `primary` from it.

### What was built

| Part | File | What it does |
| --- | --- | --- |
| Node accounts | `infra/shared/main.tf` | Creates a control-plane and a worker account for each entry in `clusters`, with the account IDs the regional module used before. Ignores the `disabled` flag, so an apply does not lift a fence. |
| Cluster list | `infra/shared/variables.tf` | `clusters` maps each cluster name to its `name_prefix`, with `primary` and `recovery` as the default. It replaces `backup_clusters`. |
| Grants | `infra/shared/main.tf` | Each worker account gets the object creator grant limited to its own prefix and the object viewer grant on the bucket. The administrator gets `roles/iam.serviceAccountUser` on every node account. |
| Lookup | `infra/modules/regional_cluster/main.tf` | Looks up the two accounts by ID with a data source and attaches them to the VMs. The module creates no account or IAM member. |
| Migration | `infra/primary/main.tf` | Two `removed` blocks with `destroy = false` drop the old accounts and the administrator grants from the primary state without deleting them. |
| Variables | `infra/primary`, `infra/recovery` | `admin_member` is removed from both regional roots and their examples. |
| Tests | `tests/test_node_identities.py` | Six tests: no account or IAM resource in the regional code, the module attaches the looked-up accounts, the `removed` blocks keep the objects, the shared accounts ignore `disabled`, each writer grant is limited to its own prefix, and the default `clusters` matches the `name_prefix` of each regional example. |
| Runbooks | [Primary infrastructure](../runbooks/primary-infrastructure.md), [regional recovery](../runbooks/regional-recovery.md) | The shared root applies first. The recovery no longer applies the shared root, and the fence disables the primary worker's account. |

The recovery node accounts and grants now exist whether or not a recovery runs. They cost nothing, and a worker account without a VM has no credentials to use.

### Local checks

Local, 2026-10-16.

| Check | Result | Can it fail |
| --- | --- | --- |
| `terraform validate` in `infra/shared`, `infra/primary`, and `infra/recovery` (Terraform 1.15.8, provider 7.46.1) | `Success! The configuration is valid.` in all three | |
| `python -m unittest tests.test_node_identities` | 6 tests `OK` | Eight edits to a copy of `infra/` each failed the matching test: an account resource back in the module, an IAM member in the primary root, `destroy = true`, an empty `ignore_changes`, a writer without the prefix condition, a writer grant for the control-plane account, a mismatched recovery prefix, and a VM that does not use the looked-up account |
| `make check` | Exit 0 | |

### Plans against the live state

Live state, read-only plans, 2026-10-16. Expected before the run: the primary plan forgets the four moved objects and changes nothing else, because the VMs attach accounts with the same emails. The shared plan imports the same four objects, creates the recovery accounts and grants, and leaves the primary's bucket grants unchanged, because their member strings do not change. Together this proves that the move deletes and replaces nothing on the running primary.

| Root | Command | Result |
| --- | --- | --- |
| `infra/primary` | `terraform plan -detailed-exitcode` | Exit 2. `Plan: 0 to add, 0 to change, 0 to destroy.` Four objects `will no longer be managed by Terraform, but will not be destroyed`: the two node accounts and the two administrator grants |
| `infra/shared` | `terraform plan -detailed-exitcode`, with the import file below | Exit 2. `Plan: 4 to import, 6 to add, 0 to change, 0 to destroy.` Imports the primary accounts and administrator grants. Adds the two recovery accounts, their administrator grants, and the recovery writer and reader grants. No line for `backup_writer["primary"]` or `backup_reader["primary"]` |
| `infra/recovery` | `terraform plan -detailed-exitcode`, before the shared apply | Exit 1. `Error 403: Permission 'iam.serviceAccounts.get' denied on resource (or it may not exist)` for both recovery accounts. A regional root that runs before the shared root fails at plan time, before it creates anything |

The import file stays out of Git. On a fresh build the accounts do not exist, and an `import` block for a missing object fails the plan; [decision 0003](../decisions/0003-regional-infrastructure-and-state.md) records the same limit. Its content:

```hcl
import {
  for_each = toset(["control-plane", "worker"])
  to       = google_service_account.node["primary/${each.key}"]
  id       = "projects/${var.project_id}/serviceAccounts/${var.clusters["primary"]}-${each.key == "control-plane" ? "cp" : "worker"}@${var.project_id}.iam.gserviceaccount.com"
}

import {
  for_each = toset(["control-plane", "worker"])
  to       = google_service_account_iam_member.admin_can_use_node["primary/${each.key}"]
  id       = "projects/${var.project_id}/serviceAccounts/${var.clusters["primary"]}-${each.key == "control-plane" ? "cp" : "worker"}@${var.project_id}.iam.gserviceaccount.com roles/iam.serviceAccountUser ${var.admin_member}"
}
```

### Migration after the merge

Live system, 2026-10-16, from `main` at `cbcaf03`. Each step was planned to a saved plan file, and that file was applied.

| Step | Command | Expected | Result |
| --- | --- | --- | --- |
| 1 | Shared root plan and apply, with the import file above saved as `infra/shared/m10_imports.tf` | `4 to import, 6 to add, 0 to change, 0 to destroy` | Plan as expected. `Apply complete! Resources: 4 imported, 6 added, 0 changed, 0 destroyed.` |
| 2 | Primary root plan and apply | `0 to add, 0 to change, 0 to destroy`, with the four objects forgotten | Plan as expected, with the two node accounts and the two administrator grants listed as `will no longer be managed by Terraform, but will not be destroyed`. `Apply complete! Resources: 0 added, 0 changed, 0 destroyed.` |
| 3 | Import file deleted, then `terraform plan -detailed-exitcode` in `infra/shared` and `infra/primary` | Exit 0 for each | `No changes. Your infrastructure matches the configuration.` in both |
| 4 | `terraform plan` in `infra/recovery`, not applied | `16 to add`, no service account or IAM member | `Plan: 16 to add, 0 to change, 0 to destroy.` The 16 are the network, firewall, router, NAT, load balancer, data disk, and VM resources. None is a service account or IAM member |
| 5 | Next scheduled primary backup Job | Completes, which shows the worker account still writes | `gitea-backup-29869702`, the 20:22Z run, `Complete` with `1/1` in 11 s, read with `kubectl -n gitea get jobs` over IAP. Set `20261016T202201Z` is listed under `primary/` in the bucket |
| 6 | Bucket IAM policy read as the administrator after the migration | The recovery reader keeps `roles/storage.objectViewer` with no condition | As expected. The only other members outside the legacy project roles are the two worker accounts: an object viewer grant each, and an object creator grant limited to the `primary/` or `recovery/` prefix |

The recovery reader check of [issue #90](https://github.com/sindredg/k8s-dr/issues/90) ran as the reader at 2026-10-16T19:59:59Z, before the migration, with the block in step 9 of the [primary infrastructure runbook](../runbooks/primary-infrastructure.md). Result: `account OK`, the listing succeeded, and the permission test returned `storage.objects.get storage.objects.list` only. The same test as the administrator returns `create` and `delete` as well, so it can fail. The migration did not change the reader's grant: `recovery_reader` only refreshed in both shared plans, and step 6 reads it unchanged.

### Not verified

- The first-build order and plan counts in the [primary infrastructure runbook](../runbooks/primary-infrastructure.md) are written from the code and the live plans, not run from empty state.
- The fence by disabled account and the return by enabled account are written, not run. A drill or the milestone 10 gate runs them.
- The recovery cluster has not run with the LVM layout of [milestone 9](09-backups-without-downtime.md). The milestone 10 gate runs a full recovery.

## Step 2: one command for every stage

[Decision 0013](../decisions/0013-automated-recovery.md#one-entry-point-runs-every-stage) runs the stages of the [regional recovery runbook](../runbooks/regional-recovery.md) from one command that stops at the first failed check and resumes from it. Before this step, a recovery took 18 or 19 commands, and the operator recorded each stage by hand with `scripts/drill_report.py stage`.

### What was built

| Part | File | What it does |
| --- | --- | --- |
| Stage runner | `scripts/recover.py` | Runs nine stages in runbook order and records their start, end, failures, and notes in `.drill/timeline.tsv`. Keeps the passed stages and the recorded values in `.drill/recover.json`, so a rerun starts at the failed stage. Holds a lock so that two runs cannot overlap. `--through <stage>` stops early; `status` and `reset` read and archive the state |
| Plan check | `scripts/recover.py` | Refuses to apply a recovery plan that deletes or replaces a resource, or that changes a service account or an IAM grant |
| Fence | `scripts/recover.py` | Stops the primary VMs, disables the primary worker's account, and requires the primary's own name to stop returning 200, before the DNS cutover. A failed stop is noted, because it is expected after a regional loss |
| Command | `Makefile` | `make recover`, with `RECOVER_ARGS` for `--through` |
| Stages | `scripts/drill_report.py` | Adds `preflight` and `fence` to the stage names, in run order |
| Preflight | `scripts/preflight.sh` | `PREFLIGHT_SKIP=fixtures` reports the fixture check as skipped, because a lost primary cannot pass it. Adds `dig` to the required tools |
| Tests | `tests/test_recover.py`, `tests/test_preflight.py` | Run the stages against a stub of every command, request, and the clock |
| Runbook | [Regional recovery](../runbooks/regional-recovery.md#run-the-procedure-with-one-command) | Maps each stage to its runbook step and check |

Two choices differ from the manual procedure:

- The fence runs before the cutover, as decision 0013 requires. The drills fenced after the cutover. A primary that still answers now stops the run before `git.sindrg.com` moves.
- Enabling recovery backups stays a manual step. It needs a Git change today, and [step 4](../../plan.md#10-automated-recovery) replaces it with a value in the cluster. The run ends by naming the step.

### Local checks

Local, against stubs, 2026-10-16. Expected before the run: every stage runs in runbook order, each failure stops the run before the next stage, and a rerun resumes at the failed stage with the values of the first attempt.

| Check | Result | Can it fail |
| --- | --- | --- |
| `python -m unittest tests.test_recover` | 20 tests `OK` | Fifteen edits to a copy of `scripts/recover.py` each failed at least one test: a plan that deletes allowed, an IAM change allowed, passed stages rerun, the fence after the cutover, `suspend` unchecked, a failed bucket listing taken as an empty prefix, the restored HEAD overwritten on a retry, the set selected again on a retry, no wait for the primary to stop answering, the disabled flag unchecked, a failed stop taken as fatal, the fixture skip dropped from the preflight, no lock, no wait for the `git-dr` record, and a missing restored HEAD accepted |
| `make check` | Exit 0, 255 tests `OK` | |
| `make -n recover` | Prints the key checks and `python3 scripts/recover.py --drill-dir .drill run` | |

### Rehearsal against the live system

Live system, 2026-10-16, from this branch, with `make recover DRILL_DIR=.drill/m10-step2 RECOVER_ARGS='--through verification'`. The primary kept serving and `git.sindrg.com` did not move. Expected before the run: every stage through `verification` ends, the plan check notes 16 creates, the run selects the newest primary set, records a restored HEAD that is a primary write, and exits 0.

| Stage | Start (UTC) | Duration | Result |
| --- | --- | --- | --- |
| `preflight` | 20:50:01 | 1 min 22 s | `10 passed, 0 failed, 0 missing`; `fixtures` reported as skipped |
| `provisioning` | 20:51:23 | 2 min 31 s | Plan check: `plan creates 16 and updates 0`. `Apply complete! Resources: 16 added, 0 changed, 0 destroyed.` `git-dr` resolved at 1.1.1.1 on the second query |
| `bootstrap` | 20:53:54 | 8 min 54 s | Both hosts `failed=0`; the playbook took 531 s |
| `flux-reconcile` | 21:02:48 | 1 min 33 s | Both validations `failed=0`. The CronJob printed `true`, and the `recovery/` listing returned `One or more URLs matched no objects.` |
| `restore` | 21:04:21 | 1 min 16 s | Set `20261016T205201Z`, `backup age at restore start 785 seconds`, `restored primary/20261016T205201Z in 26 seconds` |
| `verification` | 21:05:37 | 28 s | `make check-fixtures` on `git-dr` `failed=0`, restored HEAD `909d15f` recorded. `make write-check` pushed `8855c30`. The suspend and prefix checks repeated with the same results |

The six stages took 16 min 04 s, from 20:50:01 to 21:06:05. The restored HEAD is the write check of 05:14:12Z, the last write before the set; nothing wrote to the primary after it.

The live output confirmed two formats that the stubs assumed: the `matched no objects` error of an empty prefix, and the `Newest commit on main: <sha>` line. The `{.spec.suspend}` JSONPath survived the remote shell of `gcloud compute ssh`.

One fix followed: the runner printed captured output without a final newline, so `true` ran into the next command line. It now ends every captured output with one.

Afterwards `make dns-delete DNS_NAME=git-dr` deleted the record at 21:06:38Z, and the destroy plan of `infra/recovery` showed `Plan: 0 to add, 0 to change, 16 to destroy.`, all in the recovery module or its data disk. Applying that plan file returned `Apply complete! Resources: 0 added, 0 changed, 16 destroyed.` A read afterwards listed only the primary VMs, the primary and default networks, and the primary addresses in the project. `terraform state list` in `infra/recovery` printed nothing, and `git-dr.sindrg.com` had no answer at 1.1.1.1.

### Not verified

- The `fence`, `dns-cutover`, and `probe-recovery` stages have not run live. They run in a full recovery, which the milestone 10 gate runs. `True` from `gcloud iam service-accounts describe` is taken from the runbook.
- A resume after a failed stage ran against stubs only; the rehearsal had no failure.
