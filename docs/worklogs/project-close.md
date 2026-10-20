# Project close

Status: Complete on 2026-10-20. The environment is removed, and the watchdog, drill, and surface workflows are disabled.

## State at the close

The project closed on 2026-10-20 with its recovery contract met. The recovery runs with nobody present: the watchdog detects the outage, and one workflow builds the recovery cluster, restores the newest backup, fences the primary, and moves the public name. Two consecutive unattended drills on 2026-10-19 met both recovery targets with nobody acting after the start ([milestone 11](11-unattended-drills.md)). The code on `main` at the close is the code those drills ran. The only later commit before the close changed documentation and `tests/test_drill.py`, and the close changes documentation only. No further drill ran before the teardown, because it would have run the same code again. The [production readiness](../production-readiness.md) page lists what a production deployment would add.

## Teardown

All times are UTC on 2026-10-20. Results come from the live project and repository. Output that names the project, its buckets, or its accounts is not reproduced here.

| Step | Command | Result |
| --- | --- | --- |
| Turn off the automatic start | `gh variable set AUTO_FAILOVER --body false`, then `gh variable list` | `AUTO_FAILOVER false`, updated at 14:11:12. `SCHEDULED_DRILLS` stayed `false` from 2026-10-19 |
| Disable the scheduled workflows | `gh workflow disable` for `watchdog.yml`, `drill.yml`, and `surface.yml`, then `gh workflow list --all` | All three `disabled_manually`. The last watchdog run started at 13:56:25 from the schedule and passed. `Pins` stays enabled, because the ruleset of `main` requires its check on every pull request |
| Destroy the primary root | `terraform -chdir=infra/primary plan -destroy -out=...`, then `apply` of that plan | Plan: 16 to destroy, all in the primary root. `Apply complete! Resources: 0 added, 0 changed, 16 destroyed.` |
| Release the backup bucket | `terraform -chdir=infra/shared plan -out=...`, then `apply`, with a local edit that is not committed: retention policy removed, soft delete set to 0, `force_destroy` on, `prevent_destroy` off | Plan: 1 to change, the bucket only. `Apply complete! Resources: 0 added, 1 changed, 0 destroyed.` |
| Destroy the shared root | `terraform -chdir=infra/shared plan -destroy -out=...`, then `apply` of that plan | Plan: 46 to destroy. `Apply complete! Resources: 0 added, 0 changed, 46 destroyed.` The backup bucket held 485,341,615,479 bytes in 1,570 object versions before the destroy |
| Recovery root | `terraform -chdir=infra/recovery state list` | Empty: the last drill return had already destroyed it |
| Delete the DNS records | Cloudflare API, `DELETE` of the `git` and `git-primary` A records, then `dig +short A <name> @1.1.1.1` | Deleted at 14:55:37 and 14:55:38. No A record answers for `git`, `git-primary`, or `git-dr` |
| Delete the state bucket | `gcloud storage rm --recursive --all-versions` on the state bucket, after a guard that the primary, shared, and recovery states are empty | The guard read 0 resources in each root. The bucket was removed by 14:56 |

The `prevent_destroy` settings on the primary data disk, the backup bucket, and the state bucket, and the bucket's retention policy, stay in the code. The teardown lifted them in a local copy only, and the files match `main` again.

The drill result files under `pipeline/` were copied out of the state bucket before it was deleted. The results table on the [drill results](../drills.md) page was generated from them.

### What is left in Google Cloud

| Check | Command | Result |
| --- | --- | --- |
| Compute resources | `gcloud compute` `instances`, `disks`, `addresses`, `forwarding-rules`, `routers`, and `snapshots` lists, and `images list --no-standard-images` | None |
| Network | `gcloud compute networks list`, `gcloud compute firewall-rules list` | The `default` network with `default-allow-icmp` and `default-allow-internal`, which came with the project and are not managed here |
| Secrets and uptime checks | `gcloud secrets list`, `gcloud monitoring uptime list-configs` | None |
| Service accounts | `gcloud iam service-accounts list` | Only the two accounts that came with the project; every node and pipeline account is gone |
| Buckets | `gcloud storage buckets list` | None |

## Outside Google Cloud

| Item | State after the close |
| --- | --- |
| Workflows | `Recover` and `Return` run on dispatch only and can no longer sign in, because the workload identity provider is removed. `Ansible`, `Docs`, `Pins`, and `Backup image` still run on pushes and pull requests, and `Pins` also runs weekly. |
| `recovery` environment | Its secrets name the project, the removed buckets and accounts, and the removed workload identity provider. They hold no key, and without the provider they grant nothing. |
| Backup image | The image in the GitHub container registry stays public. |
| Cloudflare | The CAA records at the zone apex stay. The API token expires on 2026-11-01, so a rebuild needs a new token. |
| Healthchecks.io | The backup heartbeat check gets no more pings once the primary is gone. |
| Grafana Cloud | No metrics arrive after the clusters are gone. |

## Rebuilding

The code still builds every part. A rebuild follows the order in [Reproduce the project](../reproduce.md), starting with the [primary infrastructure runbook](../runbooks/primary-infrastructure.md), whose first steps create the state bucket.
