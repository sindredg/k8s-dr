# Disaster drill

Status: Not run. This file is the record format for [milestone 6](../../plan.md#6-disaster-drill). Fill a copy of the drill section for the first drill and for the repeat.

## Scope

Simulate the loss of the primary region, execute the [regional recovery runbook](../runbooks/regional-recovery.md), and measure recovery time and data loss as [decision 0002](../decisions/0002-recovery-contract.md#amendment-measure-data-loss-from-a-write-log) defines them.

## Preparation

- `make write-check` appends each acknowledged write to a local log when `WRITE_LOG` is set, and `make write-loop` repeats it every `WRITE_INTERVAL` seconds (default 300) into `.drill/writes.tsv`.
- `scripts/drill_report.py` records stage events in `.drill/timeline.tsv`, prints the timeline as a table, and reports observed data loss, the lost writes, and the potential loss window. It ignores writes after isolation, so a push to the recovery cluster cannot hide a lost primary write.
- `.drill/` is ignored by Git and stays on the operator machine.

Checked with sample logs, not against the service: a log with writes at 11:05 and 11:10, a restored HEAD of the 11:05 write, and a later recovery write reports `Observed data loss: 0h 05m 00s` and one lost write. Naming the recovery write as the restored HEAD fails with a message to record the HEAD before any recovery write.

## Drill 1

Date: not run.

### Result

| Measure | Value | Target | Met |
| --- | --- | --- | --- |
| RTO: first failed probe to all checks passing through `git.sindrg.com` | | At most 4 hours | |
| Observed data loss: last acknowledged write to the restored HEAD | | At most 2 hours | |
| Potential loss window: backup age at isolation | | Reported, no target | |
| Acknowledged writes lost | | Reported, no target | |
| Manual actions | | Reported, no target | |
| Incremental cloud cost | | Reported, no target | |

### Recovery point

| Item | Value |
| --- | --- |
| Isolation time (UTC) | |
| First failed probe (UTC) | |
| Restored set | |
| Last acknowledged primary write | |
| Restored HEAD | |

Output of `python3 scripts/drill_report.py rpo`:

```text
```

### Stage timeline

Output of `python3 scripts/drill_report.py timeline`:

| Stage | Start (UTC) | End (UTC) | Duration | Notes |
| --- | --- | --- | --- | --- |

Gap between the stage durations and the measured RTO, and its cause:

### Failures and manual actions

For each failure: symptom, confirmed cause, fix, and verification. Label an unconfirmed cause as a hypothesis.

### Runbook changes

Changes made to the runbook after this drill, before the repeat.

## Drill 2

Not run. Use the same sections, then compare the two drills stage by stage.
