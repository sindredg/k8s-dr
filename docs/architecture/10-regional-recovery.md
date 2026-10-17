# 10. Regional recovery

This is what the project exists to prove: after losing the primary region, the service comes back in another region from code and one backup, inside a measured time and with a measured data loss.

## Targets

| Measure | Target | Starts | Stops |
| --- | --- | --- | --- |
| Recovery time | At most 4 hours | The uptime probe's first failed check | Sign-in, the known commit and issue, and a new push all work through `git.sindrg.com` |
| Data loss | At most 2 hours | The last acknowledged write before the failure | The newest write present after the restore |

Backups run every 15 minutes, so the expected data loss is under 15 minutes plus the time since the last write.

## Cold recovery

Belgium has no VMs until they are needed. That is the cheapest design and the slowest: the whole cluster is built during the outage.

![Architecture diagram with two panels: in normal operation users reach the primary in Finland, which backs up to Belgium while no recovery VMs exist; after a failure users reach the recovery cluster in Belgium, restored from the bucket, and the primary is down and fenced.](../diagrams/recovery-before-after.svg)

## What survives the loss of Finland

| Needed for recovery | Where it is |
| --- | --- |
| Terraform, Ansible, and Flux code | GitHub |
| Terraform state | State bucket, Belgium |
| Backup sets | Backup bucket, Belgium |
| SOPS age key and backup age key | Operator credential store |
| DNS control | Cloudflare |
| Pinned packages, charts, and images | Public repositories, checked weekly by the pin check |

Nothing on the list is in Finland. Drill 1 recovered with both primary VMs stopped and used nothing else there.

## The procedure

![Swimlane diagram showing the stages of the Recover workflow in order across Google Cloud, Kubernetes, Cloudflare DNS, and the checks: preflight, build the infrastructure, bootstrap, restore, check through the direct name, fence the primary, cut over, check the public name, and resume backups.](../diagrams/recovery-procedure.svg)

| Step | Reuses |
| --- | --- |
| Build the network, VMs, and load balancer | The same Terraform module as the primary |
| Build the cluster | The same playbook, with `CLUSTER=recovery` |
| Deploy the service | The same `deploy/` manifests, with the recovery cluster settings |
| Load the data | The same restore Job the test restore uses |
| Verify | The same fixture checks used every day |

No step is written for recovery only. Each one is exercised on the primary first, so the recovery path is not untested code.

## How the recovery runs

Since [milestone 10](../../plan.md#10-automated-recovery), `make recover` runs the steps as stages: `preflight`, `provisioning`, `bootstrap`, `flux-reconcile`, `restore`, `verification`, `fence`, `dns-cutover`, `probe-recovery`, and `resume-backups`. Each stage ends with a check, the run stops at the first failed check, and a new run resumes at that stage. The `Recover` GitHub Actions workflow runs the command as a pipeline account and keeps the run state in the state bucket, so the resume works on a new runner. The scheduled watchdog starts the workflow while the `AUTO_FAILOVER` variable is `true`, every uptime checker has reported failure for five minutes, the runner cannot reach the primary either, and no recovery is running. See [decision 0013](../decisions/0013-automated-recovery.md).

The workflow fences the primary before the cutover: a primary that still answers stops the run before `git.sindrg.com` moves. The [runbook](../runbooks/regional-recovery.md#run-the-procedure-from-github-actions) lists what each stage checks.

After a drill, the `Return` workflow starts the primary, moves `git.sindrg.com` back after checks through the primary's own name, suspends recovery backups, lifts the fence, destroys the recovery environment, and waits for a primary backup. It discards every write the recovery cluster accepted.

## Why two hostnames

The recovery cluster is tested through `git-dr.sindrg.com` before any user reaches it, through its own load balancer, certificate, and routes. If the checks fail there, nothing is cut over. Because the cluster already holds a `git.sindrg.com` certificate, the cutover itself is one DNS edit with a 60-second TTL. See [traffic and certificates](06-traffic-and-certificates.md#hostnames).

## Keeping the two clusters apart

| Risk | Control |
| --- | --- |
| A returning primary writes backups that look newer than the recovery cluster's | Each cluster writes under its own prefix, and the primary worker's service account is disabled after failover |
| A command meant for recovery runs against the primary | `CLUSTER` selects a separate inventory per cluster |
| The recovery cluster serves stale or empty data to users | It is checked through `git-dr.sindrg.com` before the cutover |
| Both clusters serve `git.sindrg.com` | DNS points to one address at a time |

## Drills

A drill simulates the failure by stopping both primary VMs. Stopping keeps the disks, so the primary resumes unchanged afterwards, and it matches the real failure: nothing in the region answers.

The gates that used it:

| Milestone | What it proves |
| --- | --- |
| 5. Cold recovery | The recovered service passes the fixture checks through `git-dr.sindrg.com` while the primary is stopped. Public DNS is not changed. |
| 6 and 7. Disaster drills | The full procedure with the cutover, timed, with recovery time and data loss recorded. Run three times; see the [comparison](../worklogs/07-faster-recovery.md#comparison). |
| 10. Automated recovery | The workflow runs every stage, including the fence and the cutover, with no other action, and the `Return` workflow restores the primary. The primary served until the `fence` stage stopped it, so the run measures no detection. See the [gate](../worklogs/10-automated-recovery.md#gate). |

After a drill, the `Return` workflow removes the recovery environment and returns to the primary.

## Limits

- Recovery time is dominated by building a cluster from nothing. A faster design, such as a warm standby, costs more to keep running. Milestone 7 shortened the data loss instead ([decision 0012](../decisions/0012-backup-every-15-minutes.md)).
- The cutover is a DNS change with no approval step, and clients may cache the old address for the TTL.
- The return after a drill discards the writes that the recovery cluster accepted. A failback that keeps them is [milestone 13](../../plan.md#13-failback-with-data-optional).
- The buckets, the state, and both clusters are in one Google Cloud project. Losing the project or the account is not covered.
- Recovery depends on public package, chart, and image repositories being reachable.

See the [regional recovery runbook](../runbooks/regional-recovery.md).
