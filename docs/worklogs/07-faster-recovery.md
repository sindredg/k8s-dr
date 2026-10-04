# Faster recovery

Status: Complete. Backups run every 15 minutes, and a third drill on 2026-10-04 measured the result. This file is the record of [milestone 7](../../plan.md#7-faster-recovery-optional).

## Scope

Choose one improvement from the [milestone 6 measurements](06-disaster-drill.md#comparison), apply it, repeat the same drill, and compare recovery time, data loss, and cost. [Decision 0012](../decisions/0012-backup-every-15-minutes.md) records the choice.

## Implemented

- The backup CronJob runs at minutes 7, 22, 37, and 52 instead of minute 7. The capture, the set format, the bucket, and the restore are unchanged.

## Validation

### The schedule is live

| Check | Command | Result |
| --- | --- | --- |
| CronJob on the primary | `kubectl get cronjob,jobs -n gitea` | Schedule `7,22,37,52 * * * *`, `SUSPEND False`; the 17:07 Job `Complete` in 31 s |
| Sets in the bucket | `gcloud storage ls gs://<bucket>/primary/*/manifest.json` | `primary/20261004T170702Z` and `primary/20261004T172201Z`, 15 minutes apart |

### Drill 3

The same procedure as [drill 2](06-disaster-drill.md#drill-2), from the same runbook, with two differences. The `suspend` check of runbook step 6 was run. The steps after the cutover that enable recovery backups and fence the primary were not repeated: both earlier drills ran them, and they follow the end of the recovery time.

| Measure | Value | Target | Met |
| --- | --- | --- | --- |
| RTO: first failed probe to all checks passing through `git.sindrg.com` | 16 min 46 s (17:32:40 to 17:49:26 UTC) | At most 4 hours | Yes |
| Observed data loss | 10 min 10 s | At most 2 hours | Yes |
| Potential loss window: backup age at isolation | 9 min 31 s | Reported, no target | |
| Acknowledged writes lost | 2 | Reported, no target | |
| Failed steps | None | | |

| Item | Value |
| --- | --- |
| Isolation time (UTC) | `gcloud compute instances stop` ran from 17:31:32 to 17:34:17; both primary VMs `TERMINATED` |
| First failed probe (UTC) | 17:32:40 |
| Restored set | `primary/20261004T172201Z` |
| Last acknowledged primary write | 17:31:29, commit `cad0302` |
| Restored HEAD | 17:21:19, commit `8f4f539` |

`make write-loop` logged 7 acknowledged writes from 17:00:58 to 17:31:29. None failed during the captures at 17:07 and 17:22. Its first failed push was at 17:36:43.

```text
$ python3 scripts/drill_report.py rpo --restored-sha 8f4f539fdf057bc9c0cb83c01032d0e3fde6ffee \
    --isolated-at 2026-10-04T17:31:32Z --backup-set 20261004T172201Z
Last acknowledged primary write: 2026-10-04T17:31:29Z cad03027b3130fe6bd3e7cde4eaec70e39f06fa9
Restored HEAD acknowledged:      2026-10-04T17:21:19Z 8f4f539fdf057bc9c0cb83c01032d0e3fde6ffee
Observed data loss:              0h 10m 10s
Acknowledged writes lost:        2
  2026-10-04T17:26:24Z 40675441a1e4b9e10d6026352aeefc07074f9889
  2026-10-04T17:31:29Z cad03027b3130fe6bd3e7cde4eaec70e39f06fa9
Potential loss window:           0h 09m 31s (backup age at isolation)
```

| Checker region | First failed check | First passing check after the cutover |
| --- | --- | --- |
| `apac-singapore` | 17:32:40 | 17:48:50 |
| `eur-belgium` | 17:33:20 | 17:49:20 |
| `usa-virginia` | 17:33:40 | 17:49:30 |

| Stage | Start (UTC) | End (UTC) | Duration | Notes |
| --- | --- | --- | --- | --- |
| detection | 2026-10-04T17:31:32Z | 2026-10-04T17:34:18Z | 0h 02m 46s | 17:31:32 isolation: gcloud compute instances stop on both primary VMs |
| provisioning | 2026-10-04T17:34:18Z | 2026-10-04T17:37:01Z | 0h 02m 43s | 17:37:01 20 resources, 2 backup grants, git-dr record |
| bootstrap | 2026-10-04T17:37:03Z | 2026-10-04T17:45:02Z | 0h 07m 59s | 17:45:02 failed=0; the playbook includes the Flux install |
| flux-reconcile | 2026-10-04T17:45:02Z | 2026-10-04T17:46:13Z | 0h 01m 11s | 17:46:13 validate-cluster and validate-services |
| restore | 2026-10-04T17:46:46Z | 2026-10-04T17:47:43Z | 0h 00m 57s | 17:47:43 primary/20261004T172201Z, restore Job 24 s |
| verification | 2026-10-04T17:47:43Z | 2026-10-04T17:48:23Z | 0h 00m 40s |  |
| dns-cutover | 2026-10-04T17:48:23Z | 2026-10-04T17:49:18Z | 0h 00m 55s | 17:49:18 record set at 17:48:31 |
| probe-recovery | 2026-10-04T17:49:18Z | 2026-10-04T17:49:27Z | 0h 00m 09s | 17:49:27 all checks through git.sindrg.com passed |

| Stage | Result |
| --- | --- |
| Provisioning | `Plan: 20 to add`, applied; `Plan: 2 to add` in `infra/shared`, applied; `git-dr` record `set at 2026-10-04T17:36:57Z` |
| Bootstrap | Control plane `ok=75 failed=0`, worker `ok=63 failed=0`; 17:37:06 to 17:44:53, 466 s |
| Cluster and services | `ok=10 failed=0`, 43 s; `ok=13 failed=0`, 29 s |
| Backups suspended before the restore | `kubectl get cronjob,jobs -n gitea` on the recovery cluster: `SUSPEND True`, `LAST SCHEDULE <none>`, no Jobs. `gcloud storage ls gs://<bucket>/recovery/` listed only the set from drill 1. |
| Restore | `restoring primary/20261004T172201Z; backup age at restore start 1505 seconds`, `digests match the manifest`, `restored primary/20261004T172201Z in 24 seconds`; `ok=15 failed=0`, 48 s |
| Checks on the recovery cluster | `/api/healthz` 200 at 17:48:14; `make check-fixtures GIT_HOST=git-dr.sindrg.com` `failed=0`, newest commit `8f4f539`; `Push accepted at 2026-10-04T17:48:22Z by https://git-dr.sindrg.com/` |
| Cutover | `set at 2026-10-04T17:48:31Z`; resolvers returned the recovery address by 17:49:18 |
| Checks through the public name | `failed=0` at 17:49:22; `Push accepted at 2026-10-04T17:49:26Z by https://git.sindrg.com/` |

Return to the primary: started at 17:49:44, `https://git-primary.sindrg.com/api/healthz` 200 at 17:52:37, `make validate-cluster` and `make validate-services` `failed=0`, newest commit `cad0302`. `git.sindrg.com` set back at 17:53:43, and `Push accepted at 2026-10-04T17:54:46Z by https://git.sindrg.com/`. The recovery grants were removed (`0 added, 0 changed, 2 destroyed`), `git-dr` deleted at 17:55:06, and the recovery root destroyed (`0 added, 0 changed, 20 destroyed`). At 17:58 only the two primary VMs ran, the recovery state was empty, and `infra/shared` planned `No changes.` The recovery environment existed for about 23 minutes.

## Comparison

| Measure | Drill 1, hourly | Drill 2, hourly | Drill 3, every 15 minutes |
| --- | --- | --- | --- |
| RTO | 19 min 17 s | 17 min 36 s | 16 min 46 s |
| Observed data loss | 25 min 28 s, 5 writes | 15 min 16 s, 3 writes | 10 min 10 s, 2 writes |
| Backup age at isolation | 21 min 02 s | 15 min 12 s | 9 min 31 s |
| Largest possible backup age | 60 min | 60 min | 15 min |
| Bootstrap stage | 7 min 46 s | 8 min 35 s | 7 min 59 s |

- **Data loss.** The benefit is the bound, not the single observation. One drill samples one point in the interval: drill 2 happened to isolate 15 minutes after an hourly backup. With the hourly schedule the backup age at isolation can reach 60 minutes; now it can reach 15. The observed loss adds the time since the last write, up to the 5-minute write interval of the drill.
- **Recovery time.** Unchanged, as expected. The three values differ by the run-to-run variation of the bootstrap and by drill 1's rerun. The change does not touch any recovery stage.

## Cost

| Cost | Before | After | Evidence |
| --- | --- | --- | --- |
| Captures that scale Gitea to zero | 1 an hour | 4 an hour | The CronJob schedule |
| Pause recorded by each capture | 4 to 5 s | 4 to 5 s | `pause_seconds` in six manifests on 2026-10-04, including the 17:07 and 17:22 sets |
| Failed uptime checks per capture | One check in two of three regions at 12:07 and 13:07 | One check in one of three regions at 17:07 and at 17:22 | The uptime check: failed points for 60 seconds per affected region |
| Sets retained for 14 days | About 336 | About 1,350, about 0.6 GB at 475 KB a set | `gcloud storage du` on one set |
| Cloud cost | | Not read from billing. Storage of 0.6 GB is the only added resource. | |

The pause is longer than `pause_seconds`: that field measures the capture, and the service is unavailable until the new Gitea pod is ready. A checker that probes during that time reports one failed check. The data shows one to two regions affected per capture, so the availability cost is now up to four short outages an hour instead of one.

## Decision

Keep the 15-minute schedule. It cuts the worst-case data loss to a quarter for a cost of three more short pauses an hour and 0.6 GB. For a service with users, the pauses are the reason to replace the scale-to-zero capture before shortening the interval further, for example with PostgreSQL continuous archiving and a repository backup that does not stop the service. This project does not build that.

## Limitations

- One drill with the new schedule. The bound follows from the schedule; the drill confirms that a 15-minute set restores and that nothing else changed.
- The Healthchecks.io check still expects a ping every hour with one hour of grace, so a stalled backup is reported within two hours, not within 15 minutes.
- The capture outage was measured with the uptime check at four captures, not over a longer period.
- The 15:07 run on the fenced primary during drill 1 is `Failed` in `kubectl get jobs`, which confirms the fence that the drill 1 record could only infer from the bucket.
