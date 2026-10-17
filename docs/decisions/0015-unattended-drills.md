# 0015: Unattended drills

Status: Proposed on 2026-10-17. [Milestone 12](../../plan.md#12-unattended-drills) builds and measures it.

Date: 2026-10-17

## Context

Drills 1 to 3 on 2026-10-04 were attended. An operator stopped the primary VMs, ran the write loop on their own machine, and ran every recovery stage. Since [decision 0013](0013-automated-recovery.md), one workflow runs every stage and a scheduled watchdog can start it, but the automatic start has run against stubs only. Detection by the watchdog has not been measured, and GitHub starts scheduled runs late enough that it can take up to about 25 minutes ([decision 0013](0013-automated-recovery.md#schedule)).

The drills also restored a small data set, so the restore stage says little about a service with real data, and their cost was estimated from run times, not read from billing.

A drill with nobody present needs four things that a person did before: start the outage, keep the write log, end the drill, and keep the results.

## Decisions

### A drill workflow starts and ends each drill

A new `Drill` workflow runs on a schedule and on manual dispatch. One job runs these steps in order:

1. **Guards.** The drill starts only when all of these hold. If one fails, the job opens an issue and stops before it changes anything.
   - The `SCHEDULED_DRILLS` repository variable is `true`. It switches the schedule off without a Git change, as `AUTO_FAILOVER` does for the watchdog.
   - `AUTO_FAILOVER` is `true`. Without it, isolating the primary causes an outage that nothing recovers.
   - No `Recover` or `Return` run is queued or in progress, and the recovery Terraform state holds no resources.
   - The public name points at the primary, and the fixture checks pass through it.
   - The newest primary backup set is younger than two backup intervals.
   - `make preflight` passes. It already fails when the Cloudflare token has less than a week left.
2. **Write log.** The job pushes a write check every five minutes for 20 minutes from the runner, and copies each acknowledged write to the drill's folder in the Terraform state bucket.
3. **Isolation.** The job records the time and stops both primary VMs, as drills 1 to 3 did.
4. **No action.** The job waits until a `Recover` run that the watchdog started after the isolation succeeds, or until four hours after the isolation, the recovery time target of [decision 0002](0002-recovery-contract.md). It only reads run status.
5. **Report.** The job reads the first failed probe from the uptime check, the stage timeline and restored commit from the recovery run state, and the write log, and computes recovery time and data loss with `scripts/drill_report.py`.
6. **Return.** The job dispatches the `Return` workflow with `discard_recovery_writes` and waits for it to succeed.
7. **Record.** The job writes the results to the state bucket and opens an issue labelled `drill` with the report.

If the recovery fails or misses the target, the job skips the return, opens the issue, and leaves the run state for diagnosis. The guards then refuse further drills until the state is cleared, because the recovery state holds resources.

The waiting step keeps a runner busy for up to four hours. The whole job stays under the six-hour limit of a GitHub-hosted job.

### Isolation stops the primary VMs

| Option | Effect | Problem |
| --- | --- | --- |
| Stop both primary VMs | The service and its backups stop, as in a regional loss. The return starts the same VMs with their data | The Google Cloud control plane in Finland still answers, which a real regional loss would not guarantee |
| Firewall rule that blocks inbound traffic | The service stops answering | The VMs keep running and keep writing backups, so the newest set changes during the drill. A regional loss stops both |
| Delete the VMs | Closest to losing the region | The return needs the disks. Rebuilding the primary from the workflow is issue #101 |

Stopping the VMs is chosen. It matches drills 1 to 3, so the results compare, and the [fence](0013-automated-recovery.md#fence-the-primary-before-the-cutover) already expects a stopped primary.

### The write log runs on the runner

The write loop moves from the operator machine to the drill job. The log goes to the state bucket in Belgium, not to the runner's disk, so a failed job does not lose it. The fixture password comes from the SOPS file, decrypted with the age key from Secret Manager, as in the recovery run.

### Results go to the state bucket and an issue

| Option | Problem |
| --- | --- |
| The workflow commits each result to `main` | The job would need write access to `main` and would bypass review |
| Workflow artifacts | Artifacts expire after at most 90 days |
| State bucket, plus one issue per drill | Two places to read |

The state bucket holds one JSON file per drill and is the source of the results table. The issue is the notification: a failed or refused drill appears in the repository's issue list with nobody watching. A results page in `docs/`, linked from the README, is generated from the JSON files by a script and updated through a pull request.

### Cost comes from the billing export

Cloud Billing exports to a BigQuery dataset. The dataset is created by Terraform in the shared root, in the `EU` multi-region. The export itself can only be turned on in the Cloud Billing console, so it is a one-time step for the billing account owner. Billing data arrives hours after the usage, and the export fills in earlier data only for multi-region datasets.

The cost of a drill is the sum of the exported cost of resources labelled `environment=recovery` during the drill's usage window. The `Drill` workflow reads it at the start of the next drill for every earlier drill older than two days, and adds it to that drill's JSON file and issue.

### A dataset of about 2 GB

Before the gated drills, the primary gets a generated dataset of about 2 GB, mostly Git objects that do not compress, spread over several repositories, plus issues created through the Gitea API. Repositories carry most of the size of a typical Gitea instance.

Every backup set is a full copy ([decision 0007](0007-consistent-backups.md)). At 96 sets a day and 14 days of retention, the bucket holds about 2.7 TB once the dataset is in place, and every set crosses from Finland to Belgium. The billing export measures that cost. Incremental backups would need a different set format and restore path; they are not part of this decision. Revisit if a capture takes longer than a third of the backup interval.

### The schedule runs twice a week

The schedule starts a drill on Tuesday and Friday mornings, UTC, outside the top of the hour. Each recovery issues one production certificate, which stays within the Let's Encrypt limit of five per week for the same name. The schedule stays on after the milestone gate, so results accumulate.

### Detection stays with the GitHub schedule for now

The watchdog remains a scheduled GitHub workflow. A faster start, such as a Cloud Monitoring alert that calls a service in Belgium, would need a GitHub token stored in Google Cloud and a second trigger path to secure. The gated drills measure detection first. Revisit if detection takes longer than the rest of the recovery.

## Consequences

- Each drill is a real outage of `git.sindrg.com`, twice a week, from the isolation until the recovery cluster serves. Up to about 25 minutes of detection plus the 17 min 07 s recovery of the milestone 10 run gives about 45 minutes; this is an estimate, not a measurement. Writes accepted by the recovery cluster are discarded by the return.
- Recovery time includes detection with nobody present, so it will be longer than in drills 1 to 3.
- A failed drill leaves the service down on the recovery side or stopped until someone reads the issue. This is the state a real failure would leave.
- `SCHEDULED_DRILLS` and `AUTO_FAILOVER` must both be on for drills to run. Turning off `AUTO_FAILOVER` also turns off the drills.

## Known limits

- The drill stops VMs; it does not take the region away. Calls to the Google Cloud API in Finland still succeed, so the fence's stop succeeds, where a real regional loss would note it as failed.
- GitHub Actions runs both the drill and the recovery. An outage of GitHub Actions stops both, and no drill tests the fallback of running `make recover` from an operator machine.
- The writes come from one GitHub runner every five minutes, not from real users.
- Collecting the backup container logs during a drill (issue #85) is not part of this decision; the drill job does not connect to the primary nodes.
