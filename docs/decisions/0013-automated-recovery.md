# 0013: Automated recovery

Status: Accepted on 2026-10-16. Proposed on 2026-10-11. [Milestone 10](../../plan.md#10-automated-recovery) builds it and [milestone 12](../../plan.md#12-unattended-drills) measures it. Progress is in the milestone 10 [worklog](../worklogs/10-automated-recovery.md).

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
