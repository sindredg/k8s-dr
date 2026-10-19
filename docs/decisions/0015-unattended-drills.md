# 0015: Unattended drills

Status: Accepted on 2026-10-17. Amended on 2026-10-18 with [what a drill does when it cannot finish](#amendment-a-drill-that-cannot-finish-restores-the-service). [Milestone 12](../../plan.md#12-unattended-drills) builds and measures it. Since 2026-10-19, [decision 0016](0016-drills-on-demand.md) starts drills on demand without the certificate limit, ends every drill on the primary, and counts dispatched drills for the gate.

Date: 2026-10-17

## Context

Drills 1 to 3 on 2026-10-04 were attended. An operator stopped the primary VMs, ran the write loop on their own machine, and ran every recovery stage. Since [decision 0013](0013-automated-recovery.md), one workflow runs every stage and a scheduled watchdog can start it, but the automatic start has run against stubs only. Detection by the watchdog has not been measured, and GitHub starts scheduled runs late enough that it can take up to about 25 minutes ([decision 0013](0013-automated-recovery.md#schedule)).

The drills also restored a small data set, so the restore stage says little about a service with real data.

A drill with nobody present needs four things that a person did before: start the outage, keep the write log, end the drill, and keep the results.

## Decisions

### A drill workflow starts and ends each drill

A new `Drill` workflow runs on a schedule and on manual dispatch. The job runs only while the `SCHEDULED_DRILLS` repository variable is `true`; anything else skips it without an issue. The variable switches the drills off without a Git change, as `AUTO_FAILOVER` does for the watchdog. The job runs these steps in order:

1. **Guards.** The drill starts only when all of these hold. If one fails, the job opens an issue and stops before it changes anything.
   - `AUTO_FAILOVER` is `true`. Without it, isolating the primary causes an outage that nothing recovers.
   - No `Recover` or `Return` run is queued or in progress, the newest automatic `Recover` run did not fail, and the recovery Terraform state holds no resources. The watchdog starts nothing after a failed automatic run, so the drill would cause an outage that nothing recovers.
   - The public name points at the primary, and the fixture checks pass through it.
   - The newest primary backup set is younger than two backup intervals.
   - `make preflight` passes. It already fails when the Cloudflare token has less than a week left.
   - The public name serves the [dataset](#a-dataset-of-about-2-gb).
2. **Write log.** The job pushes a write check every five minutes for 25 minutes from the runner, and copies each acknowledged write to the drill's folder in the Terraform state bucket. A set that starts in the first 15 minutes has finished before the isolation if its capture takes less than ten minutes, so the restored commit is a write in the log.
3. **Isolation.** The job records the time and stops both primary VMs, as drills 1 to 3 did.
4. **No action.** The job waits until a `Recover` run that the watchdog started after the isolation succeeds, or until four hours after the isolation, the recovery time target of [decision 0002](0002-recovery-contract.md). It only reads run status.
5. **Report.** The job reads the first failed probe from the uptime check, the stage timeline and restored commit from the recovery run state, and the write log, and computes recovery time and data loss with `scripts/drill_report.py`. It then checks that the recovered service holds the dataset.
6. **Return.** The job dispatches the `Return` workflow with `discard_recovery_writes` and waits for it to succeed.
7. **Record.** The job writes the results to the state bucket and opens an issue labelled `drill` with the report. This step runs whatever the outcome: passed, missed a target, failed, or refused by a guard.

If the recovery fails or misses the target, the job skips the return, opens the issue, and leaves the run state for diagnosis. The guards then refuse further drills until the state is cleared, because the recovery state holds resources.

The waiting step keeps a runner busy for up to four hours. The guard, the write log, that wait, and a return fit the six-hour limit of a GitHub-hosted job: the `Return` runs on 2026-10-17 took 10 to 17 minutes. A recovery near the four-hour target leaves the return less time, and a return cut off by the limit leaves the drill reported as failed.

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

### A dataset of about 2 GB

Before the gated drills, the primary gets a generated dataset of about 2 GB, mostly Git objects that do not compress, spread over several repositories, plus issues created through the Gitea API. Repositories carry most of the size of a typical Gitea instance.

Every backup set is a full copy ([decision 0007](0007-consistent-backups.md)). At 96 sets a day and 14 days of retention, the bucket holds about 2.7 TB once the dataset is in place, and every set crosses from Finland to Belgium. Incremental backups would need a different set format and restore path; they are not part of this decision. Revisit if a capture takes longer than a third of the backup interval.

### The schedule runs three times a week

The schedule starts a drill on Monday, Wednesday, and Friday mornings, UTC, outside the top of the hour. Each recovery issues one production certificate, so three drills a week stay under the Let's Encrypt limit of five certificates per week for the same name and leave room for a rerun. The schedule runs until the milestone gate passes and enough drills exist to compare; then `SCHEDULED_DRILLS` is turned off.

### Detection stays with the GitHub schedule for now

The watchdog remains a scheduled GitHub workflow. A faster start, such as a Cloud Monitoring alert that calls a service in Belgium, would need a GitHub token stored in Google Cloud and a second trigger path to secure. The gated drills measure detection first. Revisit if detection takes longer than the rest of the recovery.

## Consequences

- Each drill is a real outage of `git.sindrg.com`, three times a week while the schedule is on, from the isolation until the recovery cluster serves. Up to about 25 minutes of detection plus the 17 min 07 s recovery of the milestone 10 run gives about 45 minutes; this is an estimate, not a measurement. Writes accepted by the recovery cluster are discarded by the return.
- Recovery time includes detection with nobody present, so it will be longer than in drills 1 to 3.
- A failed drill leaves the service down on the recovery side or stopped until someone reads the issue. This is the state a real failure would leave.
- `SCHEDULED_DRILLS` and `AUTO_FAILOVER` must both be on for drills to run. Turning off `AUTO_FAILOVER` also turns off the drills.

## Known limits

- The drill stops VMs; it does not take the region away. Calls to the Google Cloud API in Finland still succeed, so the fence's stop succeeds, where a real regional loss would note it as failed.
- GitHub Actions runs both the drill and the recovery. An outage of GitHub Actions stops both, and no drill tests the fallback of running `make recover` from an operator machine.
- The writes come from one GitHub runner every five minutes, not from real users.
- Collecting the backup container logs during a drill (issue #85) is not part of this decision; the drill job does not connect to the primary nodes.

## Amendment: a drill that cannot finish restores the service

Date: 2026-10-18

A review before the first drill found two failures that would leave the primary stopped with nobody to start it. Because the guard refuses every drill until the primary serves and the recovery state is empty, either failure also costs every later drill slot.

- **No recovery starts.** The watchdog's automatic start had not run live. If it starts nothing, the drill waited four hours with the service down and then left the primary stopped. Now, if no `Recover` run has started an hour after the isolation, the drill starts the primary VMs again and reports the drill as failed. Nothing else changed before that point: no recovery environment exists, and the public name still points at the primary. A `Recover` run that started and failed still leaves the state for diagnosis, as before.
- **The report fails after a recovery that passed.** The return ran only after a report that passed, so a transient read error, or a restored commit older than the write log, left the recovery cluster serving. Now the return runs after every recovery that passed, unless the drill missed a target. A missed target still keeps the state for diagnosis, as the decision states above. The drill is then reported as failed, with an incomplete measurement.

The same change sends the boolean inputs of both dispatches as the string `"true"`, the form that `gh workflow run -f` sends and that every live dispatch used. It also retries the return dispatch and one failed write check.

Trade-off: a drill that restarts the primary, or returns after a failed report, removes part of the evidence that a person would otherwise find in place. The issue and the stored result keep the reason, the run links, and the recovery's stage timeline.
