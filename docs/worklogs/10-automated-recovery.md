# Automated recovery

Status: In progress. Step 1 is built and planned against the live state, not applied. This file is the record of [milestone 10](../../plan.md#10-automated-recovery).

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

Not run yet. Apply after the change is on `main`, so `main` and the state never disagree.

1. Save the import file above as `infra/shared/m10_imports.tf`, then plan and apply the shared root. Expected: `4 to import, 6 to add, 0 to change, 0 to destroy`.
2. Plan and apply the primary root. Expected: `0 to add, 0 to change, 0 to destroy`, with the four objects forgotten.
3. Delete `infra/shared/m10_imports.tf`. Plan both roots with `-detailed-exitcode`. Expected: exit 0 for each.
4. Plan the recovery root. Expected: `Plan: 16 to add, 0 to change, 0 to destroy`, with no service account or IAM member. The count is reasoned from the code, not observed.
5. Confirm that the next scheduled backup on the primary completes, which shows the worker account still writes.
6. Run the recovery reader check of [issue #90](https://github.com/sindredg/k8s-dr/issues/90), because the bucket grants changed owner.

### Not verified

- No apply has run. The live accounts, grants, and backups are unchanged.
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

### Not verified

- `make recover` has not run against the live system. A rehearsal with `RECOVER_ARGS='--through verification'` builds the recovery environment and restores into it without touching the primary or `git.sindrg.com`. It has not run yet.
- The stub answers stand in for gcloud, Terraform, Ansible, and Cloudflare. Their output formats are taken from the runbook and the playbooks: the `matched no objects` error of an empty prefix, the `Newest commit on <branch>: <sha>` line of `make check-fixtures`, and `True` from `gcloud iam service-accounts describe`. The rehearsal checks the first two; only the fence checks the third.
- The fence and the cutover run only in a full recovery, which the milestone 10 gate runs.
