# 0013: Automated recovery

Status: Accepted on 2026-10-16. Proposed on 2026-10-11. Amended on 2026-10-17 with the [pipeline grants and run state](#amendment-pipeline-grants-and-run-state) with [run settings in the cluster](#amendment-run-settings-in-the-cluster), and with [the watchdog workflow](#amendment-the-watchdog-workflow). [Milestone 10](../../plan.md#10-automated-recovery) builds it and [milestone 12](../../plan.md#12-unattended-drills) measures it. Progress is in the milestone 10 [worklog](../worklogs/10-automated-recovery.md).

Date: 2026-10-11

## Goal

Recover the service in the second region with no person involved: nobody notices the outage, decides, or types a command.

The three drills on 2026-10-04 recovered in 17 to 19 minutes, but an operator ran 18 or 19 commands, edited `backup_clusters` by hand, and merged one change. The clock also started with that operator already signed in, with the keys in place. Time to notice the outage and to decide was not measured. See the [limits of the drills](../worklogs/06-disaster-drill.md#limits-of-these-drills).

## Decisions

### One entry point runs every stage

A single command, `make recover`, runs the stages of the [regional recovery runbook](../runbooks/regional-recovery.md) in order: preflight, provisioning, bootstrap, validation, restore, checks on the recovery name, fencing, DNS cutover, checks through the public name, and enabling recovery backups. Each stage ends with the check the runbook states today. A failed check stops the run before the cutover, and a rerun continues from the failed stage.

The command records the stage timeline that `scripts/drill_report.py` already reads, so a drill needs no manual notes.

The logic lives in a script with unit tests, not in workflow YAML. The same command runs on an operator machine when GitHub is unavailable.

### GitHub Actions runs it, with no stored cloud key

A workflow calls `make recover` on a GitHub-hosted runner. The runner signs in to Google Cloud through workload identity federation as a dedicated pipeline service account. No service account key exists.

| Need | Source |
| --- | --- |
| Google Cloud access | Workload identity federation, limited to this repository and one GitHub environment that only `main` can use |
| SOPS age key and backup age key | Secret Manager, replicated to two regions outside Finland, readable only by the pipeline account |
| Cloudflare token | The SOPS file in the repository, as today |
| Project, bucket, and prefix names | Secrets of the GitHub environment, so the public logs mask them |

The same workflow builds a cluster without a restore. The primary is built that way too, so Terraform no longer runs from a personal machine with personal credentials.

### A watchdog starts the recovery

A scheduled workflow runs every five minutes. It starts a recovery only when all of these hold:

1. Every checker region of the [uptime check](0011-external-uptime-probe.md) reported failure for the last five minutes.
2. The runner's own requests to the public name and to the primary's own name fail.
3. No recovery is running, and the public name does not already point at the recovery cluster.
4. The repository variable that enables automatic failover is set.

Condition 1 ignores a failure seen from only some regions. Backups no longer pause the service ([decision 0014](0014-snapshot-backups.md)). Condition 4 is the switch for planned work on the primary.

| Option | Trade-off |
| --- | --- |
| Scheduled watchdog in GitHub Actions (selected) | No new component and no token. GitHub starts scheduled runs late under load, so detection takes 5 to 15 minutes, and GitHub disables schedules in a public repository after 60 days without activity. |
| Cloud Monitoring alert that calls GitHub | Detection within a minute or two. Needs a relay function, and a GitHub token that only a person can create and that expires. |
| A person starts the workflow | No false start. Recovery then waits for someone to notice, which is the delay this decision removes. |

### Fence the primary before the cutover

Before DNS changes, the run stops the primary VMs and disables the primary worker's service account. In a real regional loss the stop call fails, and the run continues. The run then requires that the primary's own name does not answer. If it still answers, the run stops and changes nothing.

A stopped primary cannot serve, and a primary that returns cannot write backups, because its account is disabled. This replaces the manual removal of `primary` from `backup_clusters`.

### Node identities and backup grants move to the shared root

Today `infra/shared` grants bucket access to service accounts that the regional roots create. A recovery therefore edits `backup_clusters` and applies the shared root, and a first build fails if the shared root is applied before the regional one.

The node service accounts and their bucket grants move to `infra/shared`. Each cluster's worker account can always create objects under its own prefix and read every prefix. A regional root only attaches the accounts to its VMs.

The pipeline account then needs no permission to change any IAM policy. It cannot grant itself access to the backups.

The shared root ignores the `disabled` flag of the node accounts, so an apply of the shared root never lifts a fence. Until the run fences automatically, the [regional recovery runbook](../runbooks/regional-recovery.md) disables the account by hand.

### Recovery backups start without a Git change

A new cluster must not write a backup set before the restore, so the recovery CronJob starts suspended. Today a merged change resumes it after the cutover.

The run sets a value in the cluster instead, and Flux reads it when it substitutes the cluster settings. The default stays suspended. The pipeline never pushes to `main`.

### The drill return is a second workflow

Returning to the primary after a drill, and removing the recovery environment, becomes one workflow with the steps of the [runbook](../runbooks/regional-recovery.md#return-to-the-primary-after-a-drill). It still discards the writes that the recovery cluster accepted. A failback that keeps them is [milestone 13](../../plan.md#13-failback-with-data-optional).

## What a false start costs

If the watchdog starts a recovery while the primary is healthy but unreachable from every checker, the run stops the primary and restores the newest backup set. The service is unavailable for about 20 minutes and loses up to 15 minutes of writes, the same as a real recovery. The four conditions above make that unlikely. The design accepts the remaining risk in exchange for a recovery that does not wait for a person.

## Consequences

- Recovery time is measured from the first failed probe with nobody present. It now includes detection, so it will be longer than the 17 to 19 minutes of the attended drills.
- GitHub becomes part of the recovery path: an outage of GitHub Actions delays an automatic recovery. `make recover` on an operator machine is the fallback.
- Whoever controls the repository's `main` branch controls the pipeline account. That account can create and delete VMs and networks in the project and write Terraform state. It cannot delete or overwrite backups, change IAM policies, or read secrets other than the two keys.
- The workflow logs are public. Identifiers are masked, and the run publishes only the stage timeline.
- The 18 or 19 commands, the manual edit, and the merged change of an attended recovery are no longer needed. The runbook stays as the description of what the pipeline does and as the manual fallback.

## Known limits

- The watchdog, the pipeline account, the uptime check, and both clusters share one Google Cloud project. Losing the project stops all of them.
- The pipeline account can delete compute resources in both regions. Production would give recovery its own project and identity; see [production readiness](../production-readiness.md).
- The Cloudflare token expires and can only be replaced in the Cloudflare dashboard. The preflight stage fails when less than a week remains.
- The Healthchecks.io period is set in that account, not in this repository.

## Amendment: pipeline grants and run state

Date: 2026-10-17. [Step 3](../../plan.md#10-automated-recovery) of milestone 10 builds the workflow. `infra/shared/pipeline.tf` holds everything below.

### Grants

| Grant | On | Why |
| --- | --- | --- |
| `roles/compute.instanceAdmin.v1`, `roles/compute.networkAdmin`, `roles/compute.securityAdmin` | Project | Create and delete the recovery resources, and stop the primary VMs |
| `roles/compute.osAdminLogin`, `roles/iap.tunnelResourceAccessor` | Project | Run the playbooks as root through IAP |
| `roles/viewer`, `roles/iam.securityReviewer`, `roles/iam.workloadIdentityPoolViewer` | Project | Plan `infra/shared` in the preflight, which reads every IAM policy it manages |
| `roles/iam.serviceAccountUser` | The two recovery node accounts | Attach them to the recovery VMs and sign in to those VMs |
| A custom role with `iam.serviceAccounts.disable` and `iam.serviceAccounts.get` | The primary worker account | The fence. No predefined role can disable an account without also changing its IAM policy |
| `roles/storage.objectViewer`, `roles/storage.legacyBucketReader` | Backup bucket | List and verify the newest set |
| `roles/storage.objectViewer` | State bucket | Read the outputs of the shared and primary roots |
| `roles/storage.objectUser`, limited to the `recovery/` and `pipeline/` prefixes | State bucket | Write the recovery state and the run state |
| `roles/secretmanager.secretAccessor` | The two age key secrets | Decrypt the SOPS files and the backup sets |

The three compute roles also hold `setIamPolicy` on compute objects such as disks, images, and subnetworks. Predefined roles do not separate it, and a custom role would have to list every permission that a plan and an apply use. The pipeline still cannot change the IAM policy of the project, a service account, a bucket, or a secret, so it cannot give itself access to delete or overwrite backups. It can already read every backup with the backup key, so the compute object grants add no access to the data. It can create objects under `recovery/` through a VM that runs as the recovery worker account, which the recovery cluster needs; a set planted there is not restored, because a recovery reads `primary/`, but it fails the check that the `recovery/` prefix is empty and so stops a run.

A job signs in only when its token names this repository by numeric ID, the `recovery` environment, and `refs/heads/main`. The environment allows `main` as its only deployment branch, so either check alone blocks a workflow from another branch.

The key secrets hold no version in Terraform. `scripts/pipeline.py setup` adds each key once from the operator machine, and fails if a stored version differs from the local key, so the key material never reaches the Terraform state.

### Run state

A rerun of the workflow starts on a new runner. The runner copies `.drill/` from `pipeline/drill/` in the state bucket before the run, and mirrors it back after the run, whether the run passed or failed. `make recover` initializes the Terraform roots on every run. A run that resumes after `provisioning` first requires that the recovery root still reports the address the run recorded, then regenerates the recovery inventory. A run whose environment was removed after a rehearsal therefore stops before the fence, instead of moving `git.sindrg.com` to a released address.

The workflow's concurrency group allows one run at a time. GitHub keeps one pending run per group, and a newer dispatch replaces it. The lock file in `.drill/` only guards one machine, so it does not stop an operator from running `make recover` on a laptop during a workflow run. The two runs would use different run state. The runbook says to run one or the other.

### Not changed

The workflow builds only the recovery cluster. Building the primary from the same workflow, which the decision above names, is [issue #101](https://github.com/sindredg/k8s-dr/issues/101). Until then the primary is built from an operator machine.


## Amendment: run settings in the cluster

Date: 2026-10-17. [Step 4](../../plan.md#10-automated-recovery) of milestone 10 builds it. It specifies [Recovery backups start without a Git change](#recovery-backups-start-without-a-git-change) and replaces two sentences above, as listed under [Replaced text](#replaced-text).

### Resume backups through an optional ConfigMap

The `gitea` Kustomization reads a second source after `cluster-settings`: the ConfigMap `run-settings` in `flux-system`, marked optional. Flux lets later sources override earlier ones and treats a missing optional source as empty. Git never defines `run-settings`, so a cluster that the run has not touched keeps `backup_suspend` from `cluster-settings`.

A new `resume-backups` stage runs after `probe-recovery`. It creates `run-settings` with `backup_suspend: "false"`, requests a reconcile of the `gitea` Kustomization, and requires that the CronJob reports `suspend` as `false` within ten minutes, the timeout of that Kustomization. The stage runs after the checks through the public name, so the recovery time ends before it.

| Option | Trade-off |
| --- | --- |
| Optional ConfigMap that the run creates (selected) | No Git push and no new credential. The value lives only in the cluster, so a rebuilt recovery cluster starts suspended again. A person who reads `cluster-settings` alone sees `"true"` on a cluster that backs up; the CronJob and the runbook show the actual state. |
| Push a commit to `main` from the workflow | The value stays in Git. The pipeline would need write access to `main`, which also controls the pipeline account. |
| `kubectl patch` on the CronJob | Flux reverts it at the next reconcile. |
| A Flux `Kustomization` patch held only in the cluster | Flux owns the `gitea` Kustomization through `flux-system`, which reverts the patch. |

Only the `gitea` Kustomization reads `run-settings`. Any principal that can create a ConfigMap in `flux-system` can change `backup_suspend` on that cluster; on the primary that is the cluster administrator, who could already change the CronJob.

The return after a drill deletes `run-settings` before the primary's backups resume, so the two clusters do not ping the shared heartbeat check at once. Destroying the recovery environment removes it with the cluster.

### Sets from an earlier drill

Sets that the recovery cluster wrote stay under `recovery/` for the 14-day retention period of [decision 0007](0007-consistent-backups.md). The check that `recovery/` is empty would then stop every run for 14 days after a full drill, which the repeated drills of [milestone 12](../../plan.md#12-unattended-drills) cannot accept.

The check therefore fails only on a set named after the start of the run, or on an object whose name is not a set name. A set named in the future, such as one planted with a forged name, also fails. A set planted with an older name passes, and nothing restores it, because a recovery reads `primary/`.

### Replaced text

- "Until the run fences automatically, the regional recovery runbook disables the account by hand" no longer holds: the `fence` stage disables it.
- "it fails the check that the `recovery/` prefix is empty and so stops a run" holds only for a planted set named after the start of the run.

## Amendment: the watchdog workflow

Date: 2026-10-17. [Step 5](../../plan.md#10-automated-recovery) of milestone 10 builds [A watchdog starts the recovery](#a-watchdog-starts-the-recovery) as `.github/workflows/watchdog.yml` and `scripts/watchdog.py`.

### Two jobs

The workflow runs every five minutes. A first job needs no cloud access and checks conditions 2, 3, and 4. Only when they hold does a second job run in the `recovery` environment, sign in as the pipeline account, repeat them, and check condition 1. A healthy primary therefore causes no sign-in and no deployment record, and the public log shows only the reason that the run started nothing.

| Condition | How the script checks it |
| --- | --- |
| 1. Every checker failed for five minutes | Reads `uptime_check/check_passed` of the one uptime check whose host is the public name. At least three checker regions must report, no result in the window may pass, and each region's oldest result in the window must be at or before its start. A missing or failed read starts nothing |
| 2. The runner's own requests fail | `https://<name>/api/healthz` on the public name and on the primary's own name returns anything but 200, or nothing |
| 3. No recovery runs, and the public name points at the primary | No run of the `Recover` workflow is queued, waiting, or in progress. The public name and the primary's own name resolve to the same addresses at Cloudflare's DNS over HTTPS. After a cutover they differ |
| 4. The switch is on | The repository variable `AUTO_FAILOVER` is exactly `true`. Unset is off |

Every check fails closed: an error, a missing answer, or an unexpected count starts nothing.

### What it starts

The second job dispatches `Recover` on `main` with `through=all` and `reset=true`, through the job's `GITHUB_TOKEN` with `actions: write`. GitHub starts a workflow from a `GITHUB_TOKEN` dispatch, and records `github-actions[bot]` as the run's triggering actor.

`reset=true` archives the run state of an earlier rehearsal or drill, which would otherwise stop the run before the fence. A run of the current incident is never archived by a later start, because of the next rule.

If the newest `Recover` run was started by the watchdog and did not succeed, the watchdog starts nothing more. A retry every five minutes of a run that fails the same way helps nobody, and a run that fails before the cutover leaves the primary fenced but the public name unchanged. A person fixes the cause and resumes the run without `reset`. Once a newer run succeeds, the rule no longer applies, and after a cutover condition 3 holds the watchdog back anyway.

| Option | Trade-off |
| --- | --- |
| Two jobs, cloud sign-in only when needed (selected) | One more job definition. A healthy primary costs one short job with no credential |
| One job in the `recovery` environment | Simpler, but signs in as the pipeline account and records a deployment every five minutes |
| Retry a failed automatic start | Recovers from a transient failure without a person. A failure that repeats would rerun stages with side effects every five minutes |

### Limits

- Detection takes at least five minutes of failures, plus up to five minutes until the next scheduled run, plus GitHub's schedule delay, plus a minute or two before uptime results reach Cloud Monitoring.
- The `GITHUB_TOKEN` of the second job can start and cancel any workflow of the repository during that job. The job runs only code from `main`.
- A planned isolation of the primary starts a recovery while the switch is on. Turn it off first, as the runbook says.
- The repository variable is the only switch. Anyone who can change repository variables can turn automatic recovery on or off.
