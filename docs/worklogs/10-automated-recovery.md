# Automated recovery

Status: In progress. Step 1 is applied and verified on the live system on 2026-10-16; see [Migration after the merge](#migration-after-the-merge). Steps 2 to 6 are planned. This file is the record of [milestone 10](../../plan.md#10-automated-recovery).

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
