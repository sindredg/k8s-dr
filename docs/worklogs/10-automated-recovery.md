# Automated recovery

Status: In progress. Step 1 is applied and verified on the live system on 2026-10-16; see [Migration after the merge](#migration-after-the-merge). Step 2 is built, and a rehearsal through `verification` passed on the live system on 2026-10-16; see [Rehearsal against the live system](#rehearsal-against-the-live-system). Its fence, cutover, and checks through the public name have not run live. Step 3 is applied, and two workflow runs through `verification` passed on the live system on 2026-10-17; see [Workflow runs](#workflow-runs). For steps 2 and 3, the fence, the cutover, and the checks through the public name have not run live. Step 4 is built and checked against stubs on 2026-10-17; it has not run live. See [Step 4](#step-4-fence-and-resume-backups-without-a-git-change). Step 5 is built, checked against stubs, and read against the live system on 2026-10-17; it has not started a run. See [Step 5](#step-5-the-watchdog). Step 6 is planned. This file is the record of [milestone 10](../../plan.md#10-automated-recovery).

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

## Step 3: the workflow runs the command

[Decision 0013](../decisions/0013-automated-recovery.md#github-actions-runs-it-with-no-stored-cloud-key) runs `make recover` from GitHub Actions, signed in through workload identity federation with no service account key. The [amendment of 2026-10-17](../decisions/0013-automated-recovery.md#amendment-pipeline-grants-and-run-state) lists the grants and how a rerun resumes.

### What was built

| Part | File | What it does |
| --- | --- | --- |
| Identity | `infra/shared/pipeline.tf` | A workload identity pool and provider that accept only a token for this repository's numeric ID, the `recovery` environment, and `refs/heads/main`. The pipeline account, its project roles, its use of the recovery node accounts, and a custom fence role on the primary worker account |
| Data access | `infra/shared/pipeline.tf` | Read access to the backup bucket. Read access to every Terraform state, and write access limited to the `recovery/` and `pipeline/` prefixes |
| Keys | `infra/shared/pipeline.tf` | Two Secret Manager secrets, replicated to `europe-west1` and `europe-west4`, readable by the pipeline account only. Terraform holds no secret version |
| Runner setup | `scripts/pipeline.py` | `configure` writes `backend.hcl` and `terraform.tfvars` from the environment's secrets and masks the project number. `keys` writes both age keys with mode 600. `ssh-key` registers an OS Login key that expires after six hours. `pull` and `push` copy the run state to and from the state bucket |
| One-time setup | `scripts/pipeline.py setup` | Creates the `recovery` environment with `main` as its only deployment branch, stores the identifiers as its secrets on standard input, and adds each age key to Secret Manager once |
| Workflow | `.github/workflows/recover.yml` | Started by hand with a `through` stage and a `reset` flag. One run at a time. Saves the run state whether the run passed or failed, and writes the stage timeline to the run summary |
| Resume on a new runner | `scripts/recover.py` | Every run initializes the three roots, and each stage that reaches the recovery nodes regenerates the inventory, so a rerun needs only the run state |
| Tests | `tests/test_pipeline.py`, `tests/test_recover.py` | The runner commands and the setup against a stub, the workflow inputs and secrets against the scripts, and the grants in `pipeline.tf` |
| Runbooks | [Regional recovery](../runbooks/regional-recovery.md#run-the-procedure-from-github-actions), [primary infrastructure](../runbooks/primary-infrastructure.md#apply-the-shared-root) | The setup and a run from the workflow. The shared root plan counts include the pipeline |

Tool versions on the runner, read from the official release pages on 2026-10-17: gcloud 589.0.0, sops 3.13.3, and age 1.3.2. The sops digest is the one in the release's checksum file. age publishes no checksum file, so its digest was computed from the downloaded release archive. The action pins are the commits of `actions/checkout` v7.0.1, `actions/setup-python` v7.0.0, `hashicorp/setup-terraform` v4.0.1, `google-github-actions/auth` v3.0.0, and `google-github-actions/setup-gcloud` v3.0.1.

### Local checks

Local, against stubs, 2026-10-17. Expected before the run: the runner commands write only private files, never pass a secret as an argument, treat only an empty prefix as a first run, and the grants in `pipeline.tf` include no role that can change IAM or backups.

| Check | Result | Can it fail |
| --- | --- | --- |
| `python -m unittest tests.test_pipeline` | 28 tests `OK` | Nine edits to a copy of `scripts/pipeline.py` each failed at least one test: a secret passed with `--body`, an environment open to another branch accepted, any listing error taken as a first run, the lock file pushed, a different stored key accepted, a secret without an age key accepted, the SSH key registered without a TTL, the project number left unmasked, and key files written with mode 644. Five edits to a copy of `pipeline.tf` each failed a test: `roles/iam.serviceAccountTokenCreator` added, the `ref` clause removed, the node account filter removed, `setIamPolicy` added to the fence role, and the state writer condition removed |
| `python -m unittest tests.test_recover` | 24 tests `OK` | Two edits each failed a test: no inventory on a resumed run, and the roots initialized only in the preflight stage |
| `make check` | Exit 0 | |
| `terraform validate` in `infra/shared` (Terraform 1.16.6, provider 7.46.1) | `Success! The configuration is valid.` | |

A code review of the change found ten defects, all fixed before the merge. Seven new or changed checks each failed on an edit that removed the fix: no check of the recorded environment on a resumed run, a missing tool left uncaught, no inventory on a resumed run, a push that keeps objects deleted locally, a PUT over an existing environment, an existing environment left unchecked, and a key compared against `latest` instead of the newest enabled version.

| Finding | Fix |
| --- | --- |
| A run state left after a rehearsal makes a later run skip to the fence and move `git.sindrg.com` to a released address | A run that resumes after `provisioning` requires that `infra/recovery` still reports the recorded address. The teardown starts the next run with its state archived |
| A reset is not mirrored to the bucket, so the old state returns on the next pull | `push` deletes objects that are no longer in the local directory. The state bucket keeps object versions |
| Continuing a workflow run on an operator machine has no step that copies the state back | The runbook adds the `push` |
| The bucket comment said the pipeline cannot create a backup object; it can, under `recovery/`, as the recovery worker account | The comment and the decision record say so |
| `setup` replaced the environment's protection rules on every run | It creates a missing environment and only checks an existing one |
| `setup` compared the key with `latest`, which can be a disabled version | It compares with the newest enabled version |
| The concurrency comment said a second run waits; GitHub replaces a pending run with a newer one | The comment and the runbook say so |
| A missing tool during `terraform init` raised a traceback, and a failed init left no timeline note | Both are caught and noted in the first pending stage |
| The inventory was regenerated in four stages | It is regenerated once on a resumed run, and in `bootstrap` |
| The job restored a pip cache that other workflows write | The workflow has no cache |

The first run of `tests.test_pipeline` found a defect: the placeholder pattern did not match a value such as `"user:REPLACE_WITH_..."`, so `configure` would have written placeholders into the shared variables. The pattern now matches a placeholder anywhere in the value.

### Plan against the live state

Live state, read-only plan, 2026-10-17, from this branch. Expected before the run: the plan adds the pipeline resources and changes or destroys nothing, which shows that the new file leaves the node accounts, the backup grants, and the bucket as they are.

| Root | Command | Result |
| --- | --- | --- |
| `infra/shared` | `terraform plan -lock=false -out=shared.tfplan` | `Plan: 28 to add, 0 to change, 0 to destroy.` All 28 are in `pipeline.tf`: 4 APIs, the pool and provider, the account and its federation grant, 8 project roles, 2 recovery node account grants, the fence role and its grant, 4 bucket grants, and 2 secrets with 2 accessor grants |

### Apply and setup

Live system, 2026-10-17, from `main` at `11faeed`. Expected before the run: the plan matches the reviewed one, and the setup creates the environment from nothing.

| Step | Command | Result |
| --- | --- | --- |
| Plan | `terraform -chdir=infra/shared plan -input=false -out=shared.tfplan` | `Plan: 28 to add, 0 to change, 0 to destroy.` The 28 addresses are the ones of the plan above |
| Apply | `terraform -chdir=infra/shared apply -input=false shared.tfplan` | `Apply complete! Resources: 28 added, 0 changed, 0 destroyed.` |
| Before setup | `gh api repos/sindredg/k8s-dr/environments/recovery` and `gcloud secrets versions list k8sdr-sops-age-key` | `HTTP 404`, and no secret version |
| Setup | `python3 scripts/pipeline.py setup` | Exit 0; the output was not captured. The reads in the next rows confirm it |
| Branch policy | `gh api .../environments/recovery/deployment-branch-policies` and `.../environments/recovery` | `branch main` only; `{"custom_branch_policies":true,"protected_branches":false}` |
| Secrets | `gh secret list --env recovery`, `gh variable list --env recovery` | The seven secret names of the runbook, and `RECOVERY_SUBNET_CIDR` |
| Keys | `gcloud secrets versions list` for both secrets | One `enabled` version each. Replicas in `europe-west1` and `europe-west4` |
| No stored key | `gcloud iam service-accounts keys list --managed-by=user` for the pipeline account; `gh secret list` for the repository | No key; no repository secret |

The setup's failure paths are the stub tests above: `tests.test_pipeline` ran again on 2026-10-17 with 28 tests `OK`. The key listing was not shown to fail, because creating a key would break the claim it checks.

### Workflow runs

Live system, 2026-10-17, from `main` at `11faeed`. The primary kept serving and `git.sindrg.com` did not move. Expected before the runs: the preflight passes as the pipeline account with no permission error, the second run resumes after `preflight` from the state in the bucket, every later stage ends as in the [step 2 rehearsal](#rehearsal-against-the-live-system), and the log shows no project ID, project number, bucket name, or email address.

| Run | Input | Duration | Result |
| --- | --- | --- | --- |
| 1 | `through=preflight`, `reset=true` | 08:50:06 to 08:52:16 | Success. `10 passed, 0 failed, 0 missing` |
| 2 | `through=verification` | 08:52:58 to 09:08:01 | Success. `== preflight: passed earlier, skipped` |

The stage timeline of run 2, from its summary:

| Stage | Start (UTC) | Duration | Result |
| --- | --- | --- | --- |
| `preflight` | 08:51:07 | 1 min 02 s | From run 1 |
| `provisioning` | 08:53:48 | 2 min 22 s | Plan check: `plan creates 16 and updates 0`. `Apply complete! Resources: 16 added, 0 changed, 0 destroyed.` |
| `bootstrap` | 08:56:10 | 8 min 35 s | Both hosts `failed=0` |
| `flux-reconcile` | 09:04:45 | 1 min 08 s | Both validations `failed=0`. The CronJob printed `true`, and the `recovery/` listing returned `One or more URLs matched no objects.` |
| `restore` | 09:05:53 | 1 min 41 s | Set `20261017T085201Z`, `backup age at restore start 892 seconds`, `restored primary/20261017T085201Z in 27 seconds` |
| `verification` | 09:07:34 | 21 s | `make check-fixtures` on `git-dr` `failed=0`, restored HEAD `909d15f` recorded. `make write-check` pushed at 09:07:48Z. The suspend and prefix checks repeated with the same results |

The five stages of run 2 took 14 min 07 s, from 08:53:48 to 09:07:55. The step 2 rehearsal took 14 min 42 s for the same stages. From outside, `https://git-dr.sindrg.com/api/healthz` returned `200` from the recovery address after the run.

The saved logs of both runs were searched for the project ID, the project number, the suffixes of both bucket names, `@gmail.com`, `AGE-SECRET-KEY`, and the repository ID: no match. Four lines name a node account; each shows the project as `***`. The account names are in `infra/shared/main.tf`.

Afterwards `make dns-delete DNS_NAME=git-dr` deleted the record at 09:09:38Z. The destroy plan of `infra/recovery` showed `Plan: 0 to add, 0 to change, 16 to destroy.`, all in the recovery module or its data disk, and applying it returned `Apply complete! Resources: 0 added, 0 changed, 16 destroyed.` A read afterwards listed only the primary VMs, the primary and default networks, and the primary addresses. `terraform state list` in `infra/recovery` printed nothing, and `git-dr.sindrg.com` had no answer at 1.1.1.1. The run state under `pipeline/drill/` describes the removed environment, so the next run starts with `reset=true`.

### Not verified

- The `fence`, `dns-cutover`, and `probe-recovery` stages have not run from the workflow or anywhere else live. The milestone 10 gate runs them.
- A rerun after a failed stage on a new runner. Run 2 resumed after a passed stage, which uses the same pull of the run state.
- That the roles cover the fence and the cutover. The runs above cover every call through `verification`.

## Step 4: fence and resume backups without a Git change

[Decision 0013](../decisions/0013-automated-recovery.md#amendment-run-settings-in-the-cluster) resumes recovery backups through a ConfigMap that the run creates in the cluster. The `fence` stage from step 2 already stops the primary VMs and disables the primary worker's account, so this step adds no fence code.

### What was built

| Area | Files | Change |
| --- | --- | --- |
| Flux | `deploy/sync/sync.yaml` | The `gitea` Kustomization reads the optional ConfigMap `run-settings` after `cluster-settings` |
| Runner | `scripts/recover.py`, `scripts/drill_report.py`, `.github/workflows/recover.yml` | A `resume-backups` stage after `probe-recovery`: creates `run-settings` with `backup_suspend: "false"` through `kubectl apply`, so a retry applies it again, requests a reconcile of `gitea`, and waits up to 600 seconds for `suspend` to read `false`. The workflow offers the stage as a `through` choice |
| Suspension check | `scripts/recover.py` | `recovery/` may hold sets named before the start of the run. A set named at or after the start, an object that is not a set name, or a failed listing stops the run |
| Docs | Regional recovery runbook, decisions 0008 and 0013, architecture pages 04 and 09 | Step 9.1 and return step 3 use `run-settings` instead of a merged change. Step 6 accepts sets from earlier drills |

### Local checks

Local, against stubs, 2026-10-17. Expected before the run: the stage runs after the checks through the public name, creates the ConfigMap with `false`, fails when the CronJob stays suspended, and the suspension check rejects only sets from the current run.

| Check | Result | Can it fail |
| --- | --- | --- |
| `python -m unittest tests.test_recover` | 30 tests `OK` | Five edits to a copy of `scripts/recover.py` each failed at least one test: a set from this run accepted, an object that is not a set name accepted, the stage removed, no wait for `suspend`, and `backup_suspend=true` in the ConfigMap |
| `python -m unittest tests.test_flux` | 17 tests `OK` | `optional: false` on `run-settings` failed the new test |
| `make check` | Exit 0, 294 tests `OK` | |
| `make pins` | Exit 0 | |
| `make manifests` with kubeconform 0.8.0 | `Valid: 146, Invalid: 0` | A misspelled `optionl` field failed the `gitea` Kustomization of both clusters |

### Not verified

- That Flux on the live clusters substitutes `run-settings` over `cluster-settings`. The order and the optional behavior are taken from the Flux Kustomization reference, not observed. Flux reads `sync.yaml` from `main`, so the live check runs after the merge.
- The `fence`, `dns-cutover`, `probe-recovery`, and `resume-backups` stages have not run live. They run in the full recovery of the milestone 10 gate, which also checks that the first `recovery/` set appears.
- Return step 3 of the runbook is written, not run.

### After the merge

Live system, read-only, 2026-10-17 12:33Z, on the primary through IAP. Expected: Flux applied the merge commit, the `gitea` Kustomization is Ready with the optional source, and the absent ConfigMap changes nothing.

| Read | Result |
| --- | --- |
| `kubectl get kustomization gitea --namespace flux-system --output yaml` | `lastAppliedRevision` `main@sha1:f0e4780...`, `Ready` `True` `ReconciliationSucceeded`. `substituteFrom` lists `cluster-settings`, then `run-settings` with `optional: true` |
| `kubectl get configmap run-settings --namespace flux-system` | `NotFound` |
| `kubectl get cronjob gitea-backup --namespace gitea --output yaml` | `suspend: false`, last successful run at 12:22:11Z |

This shows that a missing `run-settings` defines nothing on the primary. The override itself shows only on a recovery cluster, in the gate run.

## Step 5: the watchdog

[Decision 0013](../decisions/0013-automated-recovery.md#amendment-the-watchdog-workflow) starts the recovery from a scheduled workflow with a switch.

### What was built

| Area | Files | Change |
| --- | --- | --- |
| Script | `scripts/watchdog.py` | `local` checks the switch, the runner's requests to both names, the DNS answers for both names, and the `Recover` runs. `confirm` repeats them, reads the uptime results, and with `--start` dispatches `Recover` with `through=all` and `reset=true` |
| Workflow | `.github/workflows/watchdog.yml` | Every five minutes. The `local` job has `contents: read` and `actions: read` and no environment. The `confirm` job runs only when `local` reports `candidate=true`, in the `recovery` environment, with `id-token: write` and `actions: write` |
| Docs | Decision 0013, the regional recovery runbook, architecture pages 11 and 12 | The amendment, the switch and its check, a drill precondition, and the watchdog's access |

No Terraform change: the identity provider accepts any workflow of this repository in the `recovery` environment on `main`, and `roles/viewer` already lets the pipeline account read the uptime check and its results.

### Local checks

Local, against stubs, 2026-10-17. Expected before the run: every condition that does not hold starts nothing, the first job never signs in, and only a five-minute failure of every checker dispatches one full run.

| Check | Result | Can it fail |
| --- | --- | --- |
| `python -m unittest tests.test_watchdog` | 25 tests `OK` | Fourteen edits to a copy of `scripts/watchdog.py` each failed at least one test: the switch ignored, an answering name ignored, the cutover ignored, a running recovery ignored, a failed automatic start ignored, a passing checker ignored, a failure shorter than the window accepted, a missing checker accepted, a checker with no results accepted, two matching uptime checks accepted, `through=verification` dispatched, a failed dispatch ignored, and `confirm` without the local checks. The window edit passed the first version of the tests; a test for failures that do not reach back to the window start was added |
| `make check` | Exit 0, 319 tests `OK`, yamllint clean | |
| `make pins` | Exit 0 | |

### Reads against the live system

Live system, read-only, 2026-10-17 about 12:40Z, from the operator machine with the operator's credentials. The primary served. Expected: each condition that reads the live system reports why it starts nothing, or holds where it should.

| Read | Result |
| --- | --- |
| `python3 scripts/watchdog.py local`, switch unset | `no start: the AUTO_FAILOVER variable is not true`, `candidate=false` |
| The same with `AUTO_FAILOVER=true` in the local environment | `no start: https://git.sindrg.com/api/healthz returned 200` |
| Public name on the primary | Holds: both names resolve to the same address |
| No recovery running | Holds: the newest `Recover` runs were started by hand and succeeded |
| Every checker failed | `no start: checker usa-virginia passed within the window`. A direct read of the same API returned three series, `usa-virginia`, `eur-belgium`, and `apac-singapore`, each with about 45 results in eight minutes |

### Not verified

- The workflow has not run. Scheduled runs start after the merge. The first runs are read for `candidate=false` and a skipped `confirm` job.
- That GitHub records `github-actions[bot]` as the triggering actor of a dispatched run, and that a dispatch from `GITHUB_TOKEN` starts the run. Taken from the GitHub documentation; the gate run shows it.
- The `confirm` job as the pipeline account. Its reads ran with the operator's credentials.
- The gate's second half: no start during a probe failure shorter than the window, and none while the switch is off.
