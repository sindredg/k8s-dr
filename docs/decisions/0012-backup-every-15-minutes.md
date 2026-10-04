# 0012: Back up every 15 minutes

Status: Accepted on 2026-10-04. A third drill measured it; see the [worklog](../worklogs/07-faster-recovery.md). The schedule stays.

Date: 2026-10-04

## Goal

Choose one improvement from the [milestone 6](../../plan.md#6-disaster-drill) measurements, apply it, and repeat the drill, as [milestone 7](../../plan.md#7-faster-recovery-optional) requires.

## What the drills measured

| Measure | Drill 1 | Drill 2 | Target | Margin |
| --- | --- | --- | --- | --- |
| Recovery time | 19 min 17 s | 17 min 36 s | At most 4 hours | About 13 times |
| Observed data loss | 25 min 28 s | 15 min 16 s | At most 2 hours | See below |
| Largest stage | Bootstrap, 7 min 46 s | Bootstrap, 8 min 35 s | | |

The data loss depends on where in the backup interval the failure falls. With hourly backups it can reach one hour, half of the target. That is the smallest margin in the results, so the improvement goes there.

## Decision

Run the backup CronJob every 15 minutes, at minutes 7, 22, 37, and 52. Nothing else changes: the capture, the set format, the bucket, the restore, and the heartbeat stay as [decision 0007](0007-consistent-backups.md) defines them.

The expected data loss is then at most 15 minutes plus the time since the last write.

| Option | Benefit | Cost |
| --- | --- | --- |
| Back up every 15 minutes (selected) | Bounds data loss at a quarter of the hourly bound. One line in Git. | Four captures an hour instead of one. Each capture scales Gitea to zero, so the service is unavailable four times as often. Four times as many sets. |
| Warm standby cluster in Belgium | Removes provisioning and bootstrap, about 11 of the 18 minutes. | A second pair of VMs, a load balancer, and a NAT gateway all the time: about twice the running cost, to shorten a recovery time that already has a thirteenfold margin. It does not reduce data loss. |
| Pre-built node image | Shortens the bootstrap stage. | A new image build to maintain and a new tool. Does not reduce data loss. |
| Streaming replication to a standby database | Data loss near zero. | Needs the warm standby, and replicating the Gitea volume as well. A different design from backup and restore. |

## Cost, measured before the change

- **Availability.** The manifests of four hourly sets on 2026-10-04 record `pause_seconds` of 4 to 5. The uptime check sees more: at the 12:07 and 13:07 runs, two of the three checker regions reported failed points for up to 60 seconds. The check runs once a minute per region and repeats its last result, so this means one failed check per region and capture, not a minute of downtime. Four captures an hour make that four times an hour.
- **Storage.** A set is about 475 KB with the fixture data. At 96 sets a day and the 14-day retention, about 1,350 sets and 0.6 GB are retained, up from about 336 sets.
- **Alerting.** The Healthchecks.io check still expects a ping every hour with a one-hour grace period. It still alerts within the two-hour RPO, not within the new interval. Its period is set in the Healthchecks.io account, not in this repository.

## Consequences

- `make preflight` still accepts a newest set up to two hours old.
- The write loop of a drill fails a push when it coincides with a capture. The loop logs the failure and continues.
- The repeat drill restored a 15-minute set with a data loss of 10 minutes and showed one failed uptime check in one region at each capture. The change stays; it is one line to revert.
