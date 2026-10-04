# Disaster drill

Status: Not run. This file is the record format for [milestone 6](../../plan.md#6-disaster-drill). Fill a copy of the drill section for the first drill and for the repeat.

## Scope

Simulate the loss of the primary region, execute the [regional recovery runbook](../runbooks/regional-recovery.md), and measure recovery time and data loss as [decision 0002](../decisions/0002-recovery-contract.md#amendment-measure-data-loss-from-a-write-log) defines them.

## Preparation

- `make write-check` appends each acknowledged write to a local log when `WRITE_LOG` is set, and `make write-loop` repeats it every `WRITE_INTERVAL` seconds (default 300) into `.drill/writes.tsv`.
- `scripts/drill_report.py` records stage events in `.drill/timeline.tsv`, prints the timeline as a table, and reports observed data loss, the lost writes, and the potential loss window. It ignores writes after isolation, so a push to the recovery cluster cannot hide a lost primary write.
- `.drill/` is ignored by Git and stays on the operator machine.
- `make preflight` checks every recovery dependency from the operator machine without changing anything, and tells a missing prerequisite from a failed check. See the [regional recovery runbook](../runbooks/regional-recovery.md#preconditions).

- [Decision 0011](../decisions/0011-external-uptime-probe.md) defines the external probe: a Cloud Monitoring uptime check on `https://git.sindrg.com/api/healthz` every 60 seconds, in `infra/shared`. It was applied on 2026-10-03 and added one resource. On 2026-10-04, `terraform -chdir=infra/shared plan -lock=false` reports `No changes. Your infrastructure matches the configuration.`, and the [command that reads its results](../decisions/0011-external-uptime-probe.md#reading-the-results) returned only `true` lines from `apac-singapore`, `eur-belgium`, and `usa-virginia` for 08:31 to 08:40 UTC. The series has a point every 10 seconds per region; the decision records what that means for the measurement.

First run of the preflight, with the recovery root planned but not applied:

```text
$ make preflight
Recovery preflight at 2026-10-03T12:36:50Z

ok       tools            git terraform gcloud sops age age-keygen jq curl sha256sum
ok       git-source       main is a6b21890cd7d at https://github.com/sindredg/k8s-dr.git
ok       key-sops         matches a recipient in .sops.yaml
ok       key-backup       matches a recipient in deploy/apps/gitea/backup/recipients.txt
ok       fixture-secret   recovery/fixtures.sops.yaml decrypts
ok       state-shared     state is reachable; the plan has no changes
ok       state-recovery   state is reachable; the plan has changes
ok       backup-set       20261003T120701Z verifies and decrypts; age 30 minutes
ok       fixtures         the user, repository, commit, and issue are present on the serving cluster
ok       cloudflare-token active; expires in 28 days
ok       cloudflare-zone  git.sindrg.com is DNS only with a 60-second TTL
manual   cloudflare-login confirm that you can sign in to the Cloudflare dashboard for the cutover

11 passed, 0 failed, 0 missing
```

With both key paths pointed at missing files, the same command reports `MISSING` for `key-sops`, `key-backup`, `fixture-secret`, `backup-set`, and `cloudflare-token`, `FAIL` for `fixtures`, which needs the key, and exits `1`.

The Cloudflare token expires on 2026-11-01. Rotate it before a drill that runs within a week of that date; the preflight fails inside that week.

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

### Preflight

Output of `make preflight` before isolation:

```text
```

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
