# Unattended drills

Status: Written on 2026-10-17 for [milestone 12](../../plan.md#12-unattended-drills). No drill has run yet. [Decision 0015](../decisions/0015-unattended-drills.md) records the design, and [worklog 12](../worklogs/12-unattended-drills.md) records each validation.

The `Drill` workflow runs one drill with nobody present. It refuses to start unless a drill is safe, pushes a write check every five minutes for 25 minutes, stops both primary VMs, and then only reads run status until the `Recover` run that the watchdog starts has ended. It then measures recovery time and data loss, runs the `Return` workflow, stores the result under `pipeline/drills/<id>/` in the Terraform state bucket, and opens an issue labelled `drill`.

Each drill is a real outage of `git.sindrg.com`, and the return discards every write that the recovery cluster accepted.

## Inputs and access

- The [recovery workflow setup](regional-recovery.md#set-up-the-workflow-once) is done, and the `Recover`, `Return`, and `Watchdog` workflows are on `main`.
- `gh` signed in as a repository administrator, for the variables and the label.
- For the dataset and the results page: the SOPS age key at `~/.config/k8s-dr/age.agekey`, the local `infra/shared/backend.hcl`, and `gcloud` signed in with read access to the state bucket.

## Prepare once

1. Load the dataset onto the primary through the public name. It creates eight private repositories of the fixture user, pushes 16 parts of 16 MiB to each, and creates 200 issues. A rerun pushes only what is missing.

   ```bash
   make load-dataset
   ```

   Expected: one `pushed part <n> of 16` line per part, then `dataset-01: 200 issues`, and exit status `0`. On a rerun, each repository reports `complete at <sha>`.

2. Check the dataset.

   ```bash
   make check-dataset
   ```

   Expected: `dataset: 8 repositories at the expected head, 200 issues in dataset-01`. The guard of every drill and its report run the same check.

3. Create the issue label.

   ```bash
   gh label create drill --description "Result of an unattended drill" --color 5319E7
   ```

   Expected: `Label "drill" created`. An existing label fails with `already exists`, which is fine.

4. Wait for a backup set taken after the load, and check its size.

   ```bash
   gcloud storage du --summarize --readable-sizes "gs://<backup bucket>/primary/$(gcloud storage ls 'gs://<backup bucket>/primary/*/manifest.json' | sort | tail -n 1 | cut -d/ -f5)/"
   ```

   Expected: about 2 GiB.

## Turn the drills on

5. Turn on the watchdog and the drills. A drill requires both: without the watchdog, nothing would recover the stopped primary.

   ```bash
   gh variable set AUTO_FAILOVER --body true
   gh variable set SCHEDULED_DRILLS --body true
   ```

   Expected: `gh variable list` shows both with `true`. The schedule starts a drill at 06:17 UTC on Monday, Wednesday, and Friday, and GitHub may start it later.

6. Optionally start a drill now instead of waiting for the schedule.

   ```bash
   gh workflow run drill.yml --ref main
   ```

   Expected: a `Drill` run starts. About 30 minutes later its `Isolate the primary` step stops the primary, a `Recover` run started by `github-actions[bot]` follows within about 25 minutes, and a `Return` run follows after the recovery.

## Read a result

7. List the drill issues.

   ```bash
   gh issue list --label drill --state all
   ```

   Expected: one issue per drill, titled `Drill <id>: <outcome>`. The body gives the first failed probe, detection, recovery time, data loss, the restored set, the dataset check after the restore, and the recovery's stage timeline.

8. Update the results page from the stored results, and open a pull request with the change.

   ```bash
   make drill-results
   git diff docs/drills.md
   ```

   Expected: `wrote the table to docs/drills.md`, and the diff adds one row per new drill.

## After a drill that did not pass

The issue names the step that stopped the drill. The guard refuses every later drill until the primary serves again and `infra/recovery` is empty. Two cases restore the service on their own: if no `Recover` run starts within an hour of the isolation, the drill starts the primary VMs again, and after a recovery that passed, the drill returns even if its report failed.

| Outcome | State left | What to do |
| --- | --- | --- |
| `refused` | Nothing changed | Fix the condition the issue names. The next scheduled drill runs the guard again |
| `failed` in `writes` | Nothing changed; the primary serves | Read the write-check error in the workflow log |
| `failed` in `wait`, no recovery run | The drill started the primary VMs again. Nothing else changed | Read the watchdog runs after the isolation to see why none started. Check the service with `make check-fixtures` |
| `failed` in `wait`, with a recovery run | The primary is stopped. The recovery environment may exist, and it may serve the public name | Follow the [regional recovery runbook](regional-recovery.md#start-automatically-with-the-watchdog) to resume the failed `Recover` run, then return as in step 9 |
| `failed` in `report`, return passed | The primary serves again; the measurement is incomplete | Read the report error in the issue. No action is needed for the service |
| `failed` in `return` | The recovery cluster serves the public name | Rerun the failed `Return` run, or return as in step 9 |
| `missed` | The recovery cluster serves the public name | Return as in step 9 |

9. Return to the primary after a recovery that passed, as the drill would have.

   ```bash
   gh workflow run return.yml --ref main -f discard_recovery_writes=true
   ```

   Expected: the `Return` run ends with success, and `make check-fixtures` passes through `git.sindrg.com`. If no recovery environment exists because no recovery started, start the primary VMs instead as in step 1 of [Return to the primary after a drill](regional-recovery.md#return-to-the-primary-after-a-drill).

## Turn the drills off

10. Delete the drill switch after the milestone gate. The watchdog switch stays as it is.

    ```bash
    gh variable delete SCHEDULED_DRILLS
    ```

    Expected: `gh variable list` does not list `SCHEDULED_DRILLS`, and the next scheduled `Drill` run shows the job as skipped.
