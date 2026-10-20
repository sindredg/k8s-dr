# Unattended drills

Status: Complete. The drill workflow and the dataset were built, and the dataset was loaded on the primary. Drill 1 ran from the schedule on 2026-10-19 and failed in the restore. Drills 2 and 3 ran on demand the same day and passed, the [gate](#gate) is met, and the [independent review](#independent-review) found no claim that overturns it. This file is the record of milestone 11.

## Step 1: the drill workflow and the dataset

[Decision 0015](../decisions/0015-unattended-drills.md) runs each drill from a scheduled workflow that isolates the primary, takes no action until the watchdog's recovery has passed, and returns.

### What was built

| Part | File | What it does |
| --- | --- | --- |
| Drill steps | `scripts/drill.py`, `tests/test_drill.py` | One command per workflow step: `guard`, `writes`, `isolate`, `wait`, `report`, `return`, and `finish`. The steps share `drill.json` in a work directory and copy it to `pipeline/drills/<id>/` in the state bucket. A step refuses to run unless every earlier step passed. `results` writes the table of every stored result into `docs/drills.md`. |
| Drill workflow | `.github/workflows/drill.yml` | Runs the steps at 06:17 UTC on Monday, Wednesday, and Friday, and on dispatch, while `SCHEDULED_DRILLS` is `true`. It runs in the `recovery` environment with its own concurrency group, so it does not block the `Recover` run it waits for. `finish` runs whatever the outcome. |
| Dataset | `scripts/dataset.py`, `tests/test_dataset.py` | Loads or checks eight private repositories of the fixture user, each with 16 commits that add 16 MiB of seeded random bytes, and 200 issues. Every commit SHA is computed from the seeds without the data, so `check` needs no clone and `load` resumes after the last pushed part. |
| Operator commands | `Makefile` | `make load-dataset`, `make check-dataset`, and `make drill-results`. |
| Results page | `docs/drills.md` | The table of every drill, between two markers that `make drill-results` rewrites, and how each column is measured. |
| Shared code | `scripts/watchdog.py`, `scripts/drill_report.py` | The uptime query moved into `Watchdog.uptime_series`, and the stage rows into `drill_report.stage_rows`, so the watchdog, the timeline table, and the drill report use one copy. |
| Runner image | `.github/workflows/recover.yml`, `return.yml`, `watchdog.yml`, `.github/actions/pipeline/action.yml` | The recovery workflows move to `ubuntu-26.04` ([issue #118](https://github.com/sindredg/k8s-dr/issues/118)), and the pipeline setup installs `jq` with `dig`, so the preflight does not depend on the image's preinstalled tools. |
| Runbook | `docs/runbooks/unattended-drills.md` | Load the dataset, switch the drills on and off, read a result, and act on a drill that did not pass. |

Choices made while building, recorded in decision 0015 before it was accepted:

- `SCHEDULED_DRILLS` skips the job instead of opening an issue. A refused drill opens an issue; a switched-off schedule is the normal state after the milestone and should not open one three times a week.
- The write log runs for 25 minutes instead of 20. With a 2 GiB set, a capture takes minutes. In a 20-minute log, a set that started late in the first 15 minutes might not finish before the isolation, and the restored commit would then be older than every logged write, which the data-loss report rejects. 25 minutes allows a capture of up to ten minutes.
- The guard also refuses a drill after a failed automatic `Recover` run, because the watchdog starts nothing after one, and checks that the public name serves the dataset.
- Copies to the state bucket during the drill only warn on failure. A failed copy after the isolation must not end the job, which would leave the primary stopped and nobody to start the return. `finish` still fails if it cannot store the result.

### Local checks

Source: local machine, against stubs.

Expected before running: every test passes, including the new drill and dataset tests, and each new check fails when the code it guards is broken.

| Check | Result |
| --- | --- |
| `make check` | Passed on 2026-10-17: `Ran 402 tests`, `OK`; yamllint, ansible-lint (`0 failure(s), 0 warning(s)`), ShellCheck, and the playbook syntax checks passed. |
| Mutations | 25 single-line breaks of the new checks in `scripts/drill.py` and `scripts/dataset.py`, each applied alone to a copy and run against its test module: all 25 failed their tests, and none survived. They covered each guard condition, the bot and time filters on runs, the isolation filter on probe results, the checker count, the run state's start time, the target check, the masking of the issue body, the discard input of the return, the step order, the write interval, the issue status, the best-effort copy, and each dataset check. |
| `tests/test_dataset.py` | The SHAs that `expected_commits` computes equal the SHAs that `git commit` writes for the same parts, identity, and date. |

### Dataset load

Source: live system, from the operator machine through `git.sindrg.com`.

Expected before running: `make check-dataset` fails before the load, because no dataset repository exists, and passes after it. The first set captured after the load holds about 2 GiB.

1. Before the load, `scripts/dataset.py check` exited `1` with `git ls-remote exited 128: remote: Repository not found`. The check can fail.
2. `scripts/dataset.py load` exited `0` after 128 `pushed part` lines and `dataset-01: 200 issues`. The Gitea API reports `dataset-01` created at 23:01:27Z and `dataset-08` last updated at 23:12:37Z, each 262260 KiB and private. An anonymous request for either repository returns `403`.
3. `make check-dataset` after the load: `dataset: 8 repositories at the expected head, 200 issues in dataset-01`, exit status `0`, in 16 seconds.
4. Backup sets, from their manifests: `gitea-data.tar.gz.age` was 0.8 MiB in `20261017T223701Z`, before the load, and 1020.5 MiB in `20261017T230701Z`, which started during it. `20261017T232201Z`, the first set after the load, holds `gitea-data.tar.gz.age` of 2150125095 bytes and totals 2.00 GiB (`gcloud storage ls -l`). Its manifest was written at 23:24:47Z, 2 min 46 s after the set's start, so the capture fits the ten minutes that the 25-minute write log allows.

### Not verified

- No drill has run. The drill steps ran against stubs only, including the GitHub API, Cloud Monitoring, and every command.
- The `Recover`, `Return`, and `Watchdog` workflows have not run on `ubuntu-26.04`. The first drill runs all three.
- The first drill is also the first restore of a 2 GiB set. The preflight's download and decryption of a set that size has not run on a runner.

## Step 2: review before the first drill

An independent review read the drill code against decision 0015 and against the grants, tokens, and APIs it uses, before the first scheduled run on 2026-10-19. It ran the local tests and read-only API calls only. It found no defect that stops a drill that goes well. The findings below are about what happens when a step fails.

### Fixed

| Finding | Evidence | Fix |
| --- | --- | --- |
| A failed `report` skipped the return, even after a recovery that passed | The `Return` step had no condition, and `return` required every earlier step to pass. A transient read, or a restored commit older than the write log, would leave the recovery cluster serving and the guard refusing every later drill | `return` runs after every recovery that passed, unless the drill missed a target. The workflow step runs on `always() && steps.wait.outcome == 'success'` |
| If the watchdog started no recovery, nothing started the primary again | `wait` failed after four hours and left the VMs stopped. The `Return` workflow needs a recovery environment | If no `Recover` run starts within an hour of the isolation, `wait` starts the primary VMs and fails the drill |
| Both dispatches sent JSON booleans | `watchdog.start` sent `"reset": True` and `return` sent `"discard_recovery_writes": True`. Every live dispatch was `gh workflow run -f`, which sends strings. No authoritative source on booleans was found | Both send `"true"` |
| A dispatch that got no answer was not retried | `watchdog.System.post` caught only HTTP errors | `post` returns no status on a network error. `return` retries up to three times, and does not dispatch again if a run already started |
| One failed write check ended the drill before the isolation | `writes` stopped at the first failure | One retry after 30 seconds |
| The runbook sent a failed report to the `Recover` resume | The table in the runbook | One row per case, including a failed report and a failed return |

[Decision 0015](../decisions/0015-unattended-drills.md#amendment-a-drill-that-cannot-finish-restores-the-service) records the first two as an amendment.

### Not fixed

- The job's worst case exceeds its timeout: [issue #122](https://github.com/sindredg/k8s-dr/issues/122).
- The tests stub out the runner wiring and the real API responses: [issue #123](https://github.com/sindredg/k8s-dr/issues/123).
- The workload identity credentials have not been used by a job that runs longer than an hour. The first drill uses them one to two hours after the setup step.

### Local checks

Source: local machine, against stubs.

Expected before running: the new tests pass, and each new check fails when the code it guards is broken.

- `tests/test_drill.py` and `tests/test_watchdog.py`: 78 tests, `OK`.
- 12 single-line mutations of the new code, each applied alone: all 12 failed their tests. They removed the start deadline, the primary restart, the check for a started run, both return gates, the string inputs, the lost-answer check, the last-attempt failure, the write retry, the network-error handling in `post`, and the issue text after a failed report. The 25 mutations of step 1 still fail their tests.

## Drill 1: the first scheduled drill, 2026-10-19

Source: live system. The `Drill` run 39213159208, the `Watchdog` runs, the `Recover` run 39218739238, and issue #126, read through the GitHub API after the drill had failed. No expected values were written down before the result was read; decision 0015 expects a drill to pass.

| Step | Time (UTC) | Result |
| --- | --- | --- |
| Scheduled start | 06:59:44Z | The cron is `17 6 * * 1,3,5`. GitHub created the run 42 min 44 s late |
| `guard` | 07:00:24Z | Passed, including `make preflight` and the dataset check |
| `writes` | 07:04:46Z to 07:30:58Z | Six write checks passed |
| `isolate` | 07:31:05Z | Stopped both primary VMs |
| Watchdog | 07:50:47Z | Run 39218628119 on `ubuntu-26.04`: `local` and `confirm` passed, and `confirm` dispatched the recovery |
| `Recover` 39218739238 | 07:51:48Z | Created by `github-actions[bot]`, on `ubuntu-26.04`. `preflight` 07:53:12Z, `provisioning` 07:55:18Z, `bootstrap` 07:57:43Z, `flux-reconcile` 08:06:57Z, `restore` 08:08:16Z |
| `restore` | 08:10:35Z | Failed: `restore failed with status 141 before any change`. Cause and fix in [troubleshooting 08](../troubleshooting/08-restore-dump-check-sigpipe.md) |
| `wait` | 08:11:47Z | Failed: `recover.yml run 39218739238 ended with failure` |
| `report`, `return` | | Skipped, as decision 0015 specified after a failed recovery |
| `finish` | 08:11:56Z | Stored `result.json` and opened issue #126, `Drill 20261019T070024Z: failed` |

What the drill showed:

- The automatic start ran end to end for the first time: the watchdog's `confirm` job dispatched the `Recover` run with nobody present. Detection, from the isolation to the run's creation, took 20 min 43 s. GitHub ran the five-minute watchdog schedule at 06:50:57Z, 07:17:32Z, 07:50:47Z, and 08:04:52Z, so the first run after the isolation came 19 min 42 s later.
- The `Recover`, `Watchdog`, and `Drill` workflows ran on `ubuntu-26.04`.
- The workload identity credentials outlived an hour. `google-github-actions/auth` created them at 07:00:04Z, and the `finish` step copied the result to the state bucket at 08:11:54Z.
- The real GitHub API answered the run polling and the issue creation, and `gcloud` stopped the VMs from the runner: paths that [issue #123](https://github.com/sindredg/k8s-dr/issues/123) lists as tested against stubs only.
- The failed recovery left the primary stopped, and nothing restored the service, as designed. The watchdog then logged `no start: automatic recovery run 39218739238 ended with failure` on every run. The `Return` run 39264176899, dispatched by hand at 13:54:25Z, started the primary at 13:58:23Z and removed the recovery environment by 14:01:58Z. The public name did not serve for about 6 h 27 min. Issue #127 records the gap.
- The recovery issued production certificates for `git.sindrg.com` and `git-dr.sindrg.com` before it failed. With the recoveries of 2026-10-17, that left about one certificate for each name in the Let's Encrypt limit of five a week, so the next drill could not follow the fix the same day.

## Step 3: drills on demand

[Decision 0016](../decisions/0016-drills-on-demand.md) addresses the last three findings of drill 1.

### What was built

| Part | Files | What it does |
| --- | --- | --- |
| Run name on the recovery's certificates | `scripts/recover.py`, `ansible/roles/flux/tasks/main.yml`, `deploy/clusters/recovery/kustomization.yaml`, `deploy/clusters/recovery/certificates/`, `deploy/clusters/recovery/cluster-settings.yaml` | The `bootstrap` stage records `run-<start>.sindrg.com` in the run state and passes it to `make bootstrap`. The Flux role writes it to `certificate-settings`. On the recovery cluster only, the `certificates` Kustomization adds it to both certificates; the default is `recovery.sindrg.com` |
| A drill ends on the primary | `scripts/drill.py`, `.github/workflows/drill.yml` | The return step runs after every step. After an isolation it dispatches the `Return` if the recovery recorded an environment, and otherwise starts the primary VMs. A missed target returns too. The issue keeps the first failure and copies the recovery's stage timeline |
| A passed return clears a failed automatic start | `scripts/watchdog.py` | A `Return` run that passed and was created after the failed `Recover` run lifts the block, in the watchdog and in the drill guard |
| Documents | Decision 0016 and pointers in decisions 0008, 0013, and 0015, the plan, architecture pages 06 and 10, the runbooks, the results page, the readiness page, and the `flux-kustomizations` diagram | The gate counts dispatched drills. The runbook's table for a drill that did not pass follows the new ends |

### Local checks

Source: local machine, against stubs, and offline renders of the manifests.

Expected before running: every new test fails against the code before the change and passes after it, and each new condition is caught when it is broken alone.

- New tests, each run against the code before the change and failing there: `test_a_return_that_passed_after_a_failed_automatic_start_clears_it`, `test_bootstrap_passes_one_certificate_name_per_run`, `test_a_recovery_run_sets_its_certificate_name_before_flux_applies`, `test_only_the_recovery_certificates_carry_the_run_name`, and in `tests/test_drill.py` the tests for a failed recovery with and without an environment, a run state older than the isolation, a missing run state, a return without an isolation or after a restart, a failed return after a failed recovery, and a missed target that returns.
- `make check`: exit `0`, 423 tests `OK`, and the linters pass.
- `make manifests`: `recovery/certificates: 5 objects from ./deploy/clusters/recovery/certificates`, and kubeconform reports 146 resources valid. The recovery's `git-tls` names `git.sindrg.com` and `recovery.sindrg.com`, its `git-cluster-tls` names `git-dr.sindrg.com` and `recovery.sindrg.com`, and the primary's certificates are unchanged.
- 17 single-line mutations of the new code, each applied alone and run against its test module. 15 failed their tests at first. A recovery state with `provisioning` passed and no address survived, so a test case was added, and it now fails. A check that the drill had seen a `Recover` run survived because the age check on the run state already covers it, so the check was removed. They covered both conditions of the cleared start, each condition of the recovery environment, both early exits of `return`, the first failure kept in the issue, the copied timeline, the issue text after a missed target, the bootstrap argument, the role's condition, the settings source and the patch on the recovery cluster, and the workflow condition.

### Not verified

- Nothing of step 3 has run live. The first recovery after the merge is the first order of a certificate with a run name, the first `certificate-settings` ConfigMap, and the first render of `deploy/clusters/recovery/certificates` by Flux.
- The return after a failed recovery and the cleared failed start have run against stubs only. The `Return` run 39264176899 took the path that the drill now takes after a failed recovery, by hand, and passed.
- Detection took longer than the rest of a passing recovery in drill 1, which is the revisit condition of [decision 0015](../decisions/0015-unattended-drills.md#detection-stays-with-the-github-schedule-for-now). One drill is not enough to decide; the next drills measure it again.

## Rehearsal after the restore fix, 2026-10-19

Source: live system. The `Recover` run 39276135506, dispatched by hand with `through=verification` and `reset=true` from `main` at 8292e0a, after the restore fix of [troubleshooting 08](../troubleshooting/08-restore-dump-check-sigpipe.md) merged.

Expected before running: every stage through `verification` passes, and the restore reports `both archives decrypt and read` for the newest primary set with the dataset. The primary keeps serving.

Result: the run failed in `flux-reconcile` at 15:46:08Z, before the restore. `validate-services` timed out waiting for both Gitea certificates: `error: timed out waiting for the condition on certificates/git-tls`. On the recovery cluster, `kubectl get orders.acme.cert-manager.io --namespace gitea -o wide` showed both orders `errored` with `429 urn:ietf:params:acme:error:rateLimited: too many certificates (5) already issued for this exact set of identifiers in the last 168h0m0s, retry after 2026-10-19 16:15:49 UTC`. The estimate of one certificate left in the limit was wrong: none were left. The primary served throughout. The `Return` run 39283531515 removed the recovery environment and passed at 16:47:08Z.

This is the limit that [decision 0016](../decisions/0016-drills-on-demand.md#each-recovery-run-adds-one-name-to-its-certificates) removes; step 3 merged at 16:17:59Z.

## Drill 2: the first drill on demand, 2026-10-19

Source: live system. The `Drill` run 39290237165, dispatched with `gh workflow run drill.yml --ref main` at 16:59:20Z from `main` at 0579094, after GitHub reported Actions as operational again following an incident from 15:49Z. Issue #130.

Expected before running: the guard passes; the watchdog starts the recovery; the recovery's certificates carry a run name and are issued without a `429`; the restore reports `both archives decrypt and read` for a set with the dataset; both targets are met; the drill returns; the issue reports `passed`.

| Measure | Value |
| --- | --- |
| Isolated at | 17:30:49Z |
| First failed probe | 17:31:40Z |
| Watchdog run that started the recovery | 39296601465 at 17:43:46Z; the run before it was at 17:31:34Z |
| `Recover` run | 39296767806, created by `github-actions[bot]` at 17:44:55Z |
| Detection | 13 min 15 s |
| Recovery time | 34 min 21 s, to the end of `probe-recovery` at 18:06:01Z |
| Data loss | 10 min 18 s, 2 acknowledged writes |
| Restored set and its age at isolation | `20261019T172201Z`, 8 min 48 s |
| Dataset on the recovery cluster | `dataset: 8 repositories at the expected head, 200 issues in dataset-01` |
| Return | `Return` run 39299859812, passed; the drill run ended at 18:28:35Z |

| Stage | Duration |
| --- | --- |
| preflight | 1 min 58 s |
| provisioning | 2 min 18 s |
| bootstrap | 8 min 50 s |
| flux-reconcile | 1 min 51 s |
| restore | 4 min 11 s; the restore Job logged `restored primary/20261019T172201Z in 130 seconds` |
| verification | 20 s |
| fence | 17 s |
| dns-cutover | 6 s |
| probe-recovery | 12 s |
| resume-backups | 18 s |

What it verified live:

- The run name. The bootstrap received `run_certificate_host=run-20261019t174546z.sindrg.com`. `validate-services` listed `git-tls` with `[git.sindrg.com run-20261019t174546z.sindrg.com]` and `git-cluster-tls` with `[git-dr.sindrg.com run-20261019t174546z.sindrg.com]`, both Ready.
- The restore fix. The restore Job logged `digests match the manifest` at 18:03:11Z and `both archives decrypt and read` at 18:03:46Z, then restored the database and the 2 GiB Gitea volume.
- The drill on demand. Nobody acted between the dispatch and the issue.
- Paths that [issue #123](https://github.com/sindredg/k8s-dr/issues/123) lists as tested against stubs only, besides those that drill 1 ran: the report's read of the Cloud Monitoring `timeSeries`, the dispatch of the `Return` run with its string input, and the wait for that run.

After the return, `git.sindrg.com` resolved to the primary address at Cloudflare's DNS-over-HTTPS resolver and returned `200`, both primary VMs were `RUNNING`, and `infra/recovery` held no resources.

## Drill 3: the second drill on demand, 2026-10-19

Source: live system. The `Drill` run 39306669248, dispatched with `gh workflow run drill.yml --ref main` at 18:55:53Z from `main` at 0579094, the commit of drill 2. Issue #131. The expectation written for drill 2 applied unchanged and was not written down again.

| Measure | Value |
| --- | --- |
| Isolated at | 19:26:43Z |
| First failed probe | 19:27:50Z |
| Watchdog run that started the recovery | 39313549619 at 19:44:56Z. The run before it, at 19:31:33Z, logged `no start: checker usa-virginia passed within the window` |
| `Recover` run | 39313715218, created by `github-actions[bot]` at 19:46:05Z |
| Detection | 18 min 15 s |
| Recovery time | 36 min 00 s, to the end of `probe-recovery` at 20:03:50Z |
| Data loss | 5 min 09 s, 1 acknowledged write |
| Restored set and its age at isolation | `20261019T192201Z`, 4 min 42 s |
| Dataset on the recovery cluster | `dataset: 8 repositories at the expected head, 200 issues in dataset-01` |
| Return | `Return` run 39316323118, passed at 20:26:17Z; it logged `Apply complete! Resources: 0 added, 0 changed, 16 destroyed.` for the recovery root. The drill run ended at 20:27:30Z |

| Stage | Duration |
| --- | --- |
| preflight | 1 min 40 s |
| provisioning | 2 min 03 s |
| bootstrap | 7 min 10 s |
| flux-reconcile | 1 min 48 s |
| restore | 3 min 18 s; the restore Job logged `restored primary/20261019T192201Z in 107 seconds` |
| verification | 20 s |
| fence | 14 s |
| dns-cutover | 6 s |
| probe-recovery | 12 s |
| resume-backups | 13 s |

What it verified live:

- A second run name within two hours. `validate-services` listed `git-tls` with `[git.sindrg.com run-20261019t194648z.sindrg.com]` and `git-cluster-tls` with `[git-dr.sindrg.com run-20261019t194648z.sindrg.com]`, both issued by `letsencrypt-production`. With the old names, this recovery would have been refused: the rehearsal's `429` gave one certificate per set back at 16:15:49Z, drill 2 would have used it, and the limit gives the next one back 34 hours later.
- The restore fix with a second set. The restore Job logged `both archives decrypt and read` at 20:01:56Z.
- The drill on demand. Nobody acted between the dispatch and the issue.

The first watchdog run after the first failed probe came 3 min 43 s later, before the five-minute window had filled. GitHub ran the next one 13 min 23 s after it.

After the return, `git.sindrg.com` and `git-primary.sindrg.com` resolved to the primary address at Cloudflare's DNS-over-HTTPS resolver and returned `200` at 20:28Z, and `gcloud compute instances list` listed only the two primary VMs, both `RUNNING`.

## Comparison with drills 1 to 3

Source: [worklog 06](06-disaster-drill.md) and [worklog 07](07-faster-recovery.md#drill-3) for the attended drills of 2026-10-04, and the sections above for drills 2 and 3 of this milestone. Drill 1 of this milestone failed before it had a recovery time and is left out. The durations that the worklogs do not state directly were computed from the times they record.

| Measure | Attended drill 1 | Attended drill 2 | Attended drill 3 | Drill 2 | Drill 3 |
| --- | --- | --- | --- | --- | --- |
| Recovery started by | A person | A person | A person | The watchdog | The watchdog |
| Backup interval | 1 hour | 1 hour | 15 minutes | 15 minutes | 15 minutes |
| Data on the primary | Fixtures | Fixtures | Fixtures | Fixtures and the 2 GiB dataset | Fixtures and the 2 GiB dataset |
| First failed probe to the start of the recovery | 2 min 07 s | 1 min 28 s | 1 min 38 s | 13 min 15 s | 18 min 15 s |
| Start of the recovery to the checks through the public name | 17 min 10 s | 16 min 08 s | 15 min 08 s | 21 min 06 s | 17 min 45 s |
| Recovery time | 19 min 17 s | 17 min 36 s | 16 min 46 s | 34 min 21 s | 36 min 00 s |
| Bootstrap stage | 7 min 46 s | 8 min 35 s | 7 min 59 s | 8 min 50 s | 7 min 10 s |
| Restore stage | 1 min 08 s | 1 min 10 s | 57 s | 4 min 11 s | 3 min 18 s |
| Observed data loss | 25 min 28 s, 5 writes | 15 min 16 s, 3 writes | 10 min 10 s, 2 writes | 10 min 18 s, 2 writes | 5 min 09 s, 1 write |
| Backup age at isolation | 21 min 02 s | 15 min 12 s | 9 min 31 s | 8 min 48 s | 4 min 42 s |

The start of the recovery is the end of the `detection` stage in the attended drills, when the operator ran the first command, and the creation of the `Recover` run in the unattended drills. After that start, an unattended recovery also spends about a minute on job setup before its first stage, and runs a `preflight` stage that the attended drills did not have.

What the comparison shows:

- **Detection explains most of the longer recovery time.** After its start, the recovery took 17 min 45 s and 21 min 06 s, against 15 min 08 s to 17 min 10 s when attended, while it also restored the 2 GiB dataset and ran the preflight. Detection took 13 min 15 s and 18 min 15 s instead of at most 2 min 07 s. The watchdog needs five minutes of failures from every checker, and on its five-minute schedule the run that started each recovery came 12 min 12 s and 13 min 23 s after the run before it.
- **The restore grows with the data.** With the dataset, the restore stage took 3 min 18 s and 4 min 11 s, and the restore Job 107 and 130 seconds, against 24 to 28 seconds for the fixtures.
- **The bootstrap is unchanged.** Its mean in drills 2 and 3 is 8 min 00 s. Drill 1's bootstrap passed in 9 min 14 s; the mean uses the two gate drills, which ran from one commit.
- **Data loss.** Each value is one sample. Both stay within the bound that [worklog 07](07-faster-recovery.md#comparison) gives for the 15-minute schedule: up to 15 minutes of backup age plus up to 5 minutes since the last write.
- **Detection against decision 0015.** [Decision 0015](../decisions/0015-unattended-drills.md#detection-stays-with-the-github-schedule-for-now) revisits the GitHub schedule if detection takes longer than the rest of the recovery. In drill 3 it did: 18 min 15 s against 17 min 45 s. In drill 2 it did not: 13 min 15 s against 21 min 06 s. In drill 1, detection from the isolation took 20 min 43 s before the recovery failed. The condition is met. The watchdog stays on the GitHub schedule: both drills met the recovery time target with more than three hours to spare, and a faster start would need a GitHub token stored in Google Cloud and a second trigger path to secure.

## Gate

Source: live system, the sections above and the run history of the repository.

Expected before checking the run history: between each dispatch and its issue, only the `Drill` run was started by a person; the `Watchdog` runs came from the schedule, and the `Recover` and `Return` runs from `github-actions[bot]`. A run that a person started in that window would show the person as its triggering actor.

| Condition | Drill 2 | Drill 3 |
| --- | --- | --- |
| Started by the schedule or a dispatch | Dispatch, run 39290237165 | Dispatch, run 39306669248 |
| Recovery time, at most 4 hours | 34 min 21 s | 36 min 00 s |
| Data loss, at most 2 hours | 10 min 18 s | 5 min 09 s |
| The watchdog started the recovery | Run 39296601465 dispatched `Recover` run 39296767806, created by `github-actions[bot]` | Run 39313549619 dispatched `Recover` run 39313715218, created by `github-actions[bot]` |

- **Consecutive.** The `Drill` runs are 39213159208 (schedule, failed), 39290237165, and 39306669248, with none between the last two.
- **No person acted after the start.** Besides the `Watchdog` runs from the schedule, the runs created from 16:59Z to 20:28Z are the two dispatched `Drill` runs and two `Recover` and two `Return` runs by `github-actions[bot]`, as expected.
- **Compared with drills 1 to 3.** See the [comparison](#comparison-with-drills-1-to-3).

Result: Verified on 2026-10-19. The run history covers GitHub runs only; the other commands during both drills were read-only checks.

Accepted: the return after a failed recovery or a missed target, the restart of the primary VMs, and the failed start that a passed return clears have run against stubs only. Both drills passed, so they took the `Return` path. Forcing a failure on the live system would need a fault input that the workflows do not have, and one more outage. Each of these branches has a test that fails when the branch is broken. Trigger to check again: the first drill that fails or misses a target, whose issue names the end it took.

[Issue #122](https://github.com/sindredg/k8s-dr/issues/122) applies only if a drill's recovery takes longer than two hours. The recoveries took 34 min 21 s and 36 min 00 s, so it did not apply.

## Independent review

Source: a review of the milestone's claims against the code, the run history, the stored results, and the live endpoints, on 2026-10-19, before the milestone closed. It recomputed every figure of drills 2 and 3 from the `result.json` files, the issues, and the job logs, and found them correct. It ran 27 single-line mutations of the milestone's checks, of which 25 failed their tests. Of the two survivors, one gives the same result as the original code, and the other is fixed below.

Fixed in the same change:

- Stale text: the plan's introduction to this milestone and its header, the runbook's time to the `Recover` run, the misdated sentence in the status of the regional recovery runbook, and the limits and gates of architecture page 10.
- The runbook keyed a failed return on `The return failed`, which the issue prints only after an earlier failure. It now keys on `The return did not finish`, which the issue prints in both cases.
- In the comparison, the row to the checks through the public name, the job setup that the README called a runner start, and "most of" for what detection explains. The time between the rehearsal's `429` and drill 2's certificates was wrong and is removed; drill 3's certificates show the limit no longer applies.
- The plan step named several gigabytes for a 2 GiB dataset.
- The mutation that ended the recovery time at the last stage instead of `probe-recovery` survived, because the test's recovery had no stage after `probe-recovery`. The test now has a `resume-backups` stage, and the mutation fails two tests.
- The paths of issue #123 that drills 2 and 3 ran, and the state of issue #122, are recorded above.

Not changed:

- Whether the watchdog keeps the GitHub schedule after the detection of drill 3. It stays; see the [comparison](#comparison-with-drills-1-to-3).
- After a failed `wait`, the return can read the run state of an earlier recovery while a late `Recover` run still runs, start the primary VMs, and leave that run to cut over with no return. This is reasoned from the code, not observed; [issue #133](https://github.com/sindredg/k8s-dr/issues/133) records it.
