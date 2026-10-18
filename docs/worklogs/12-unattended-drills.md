# Unattended drills

Status: In progress. The drill workflow and the dataset are built, and the dataset is loaded on the primary; no drill has run. This file is the record of [milestone 12](../../plan.md#12-unattended-drills).

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

### Plan only

- The job's worst case exceeds its timeout: [issue #122](https://github.com/sindredg/k8s-dr/issues/122).
- The tests stub out the runner wiring and the real API responses: [issue #123](https://github.com/sindredg/k8s-dr/issues/123).
- The workload identity credentials have not been used by a job that runs longer than an hour. The first drill uses them one to two hours after the setup step.

### Local checks

Source: local machine, against stubs.

Expected before running: the new tests pass, and each new check fails when the code it guards is broken.

- `tests/test_drill.py` and `tests/test_watchdog.py`: 78 tests, `OK`.
- 12 single-line mutations of the new code, each applied alone: all 12 failed their tests. They removed the start deadline, the primary restart, the check for a started run, both return gates, the string inputs, the lost-answer check, the last-attempt failure, the write retry, the network-error handling in `post`, and the issue text after a failed report. The 25 mutations of step 1 still fail their tests.
