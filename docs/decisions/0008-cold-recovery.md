# 0008: Cold recovery

Status: Accepted. Implemented, and validated by the milestone 5 gate on 2026-10-03. Amended on 2026-10-04 with the [return to the primary after a drill](#amendment-return-to-the-primary-after-a-drill).

Date: 2026-09-30

## Goal

Build an independent Gitea service in Belgium (`europe-west1`) from code and one verified backup set, with no dependency on primary-region VMs or services. The [milestone 5 gate](../../plan.md#5-cold-recovery) requires the recovered service to pass the fixture checks while the primary is isolated.

Most of the design already exists: the regional Terraform module and root contract ([decision 0003](0003-regional-infrastructure-and-state.md#amendment-shared-root-and-root-owned-data-disk)), per-cluster Flux settings ([decision 0006](0006-service-deployment-architecture.md#amendment-postgresql-and-gitea-implementation)), and per-cluster backup prefixes and fencing ([decision 0007](0007-consistent-backups.md)). This record settles what remains: the hostnames each cluster serves, how operator commands select a cluster, and how the gate isolates the primary.

## Decisions

### Serve the public host and a per-cluster host from every cluster

Every cluster serves two hostnames, each with its own certificate:

| Setting | Primary | Recovery | Purpose |
| --- | --- | --- | --- |
| `git_host` | `git.sindrg.com` | `git.sindrg.com` | The public name users and the probe use. Gitea's `DOMAIN` and `ROOT_URL`. |
| `git_cluster_host` | `git-primary.sindrg.com` | `git-dr.sindrg.com` | Reaches one cluster directly, whatever `git.sindrg.com` points to. [Decision 0002](0002-recovery-contract.md) already names `git-dr.sindrg.com` for recovery tests. |

The Gitea Gateway gets a second HTTP and HTTPS listener pair for `git_cluster_host`, and the HTTPRoutes list both names. Each name has a Cloudflare DNS-only record with a 60-second TTL, created by hand like the existing record.

The recovery cluster uses `git.sindrg.com` as `git_host` because Gitea builds clone URLs, links, and redirects from `ROOT_URL`. A recovery cluster configured for another name would serve wrong URLs after the DNS cutover, and fixing that during a drill means a Git change, a Flux reconcile, and a Gitea restart inside the RTO. The issuers solve DNS-01 through Cloudflare, so the recovery cluster obtains a `git.sindrg.com` certificate before DNS points to it, and the cutover waits on nothing but DNS.

`git_cluster_host` lets `make check-fixtures GIT_HOST=git-dr.sindrg.com` test the recovery cluster through its own load balancer, TLS, and Gateway before cutover. The milestone 5 gate uses only this name and leaves `git.sindrg.com` on the primary. Cutting `git.sindrg.com` over is a milestone 6 drill step.

| Option | Trade-off |
| --- | --- |
| Both names on every cluster (selected) | Primary and recovery run identical manifests with different settings. The primary gains one DNS record, one certificate, and two listeners. |
| A second name on the recovery cluster only | Leaves the primary unchanged, but needs a recovery-only overlay of the Gitea manifests and a recovery-only Flux path. |
| `git-dr.sindrg.com` as the recovery `git_host` | Wrong URLs after cutover, as above. |
| No second name; pin `git.sindrg.com` to the recovery address for tests | No extra certificate. The Ansible `uri` module cannot override name resolution, so the fixture checks would need a separate code path that users never take. |

**Certificate limits.** Let's Encrypt allows five certificates per exact set of names every seven days. Each certificate covers one name, so each cluster build issues one `git.sindrg.com` certificate and one for its own name. Two recovery builds and one primary rebuild in a week use three of the five `git.sindrg.com` certificates. Rebuild tests that do not run the fixture checks use `letsencrypt-staging`.

### Share the Flux sync definition between clusters

Move `deploy/clusters/primary/sync.yaml` to `deploy/sync/`. Each cluster directory then holds only its `kustomization.yaml` and `cluster-settings.yaml`, and a recovery cluster cannot drift from the primary's Flux layout. The flux-system Kustomization still reads `deploy/clusters/<cluster>`.

### Select the cluster with one Makefile variable

`CLUSTER ?= primary` selects the Terraform root (`infra/$(CLUSTER)`), the generated inventory (`ansible/inventory/generated/$(CLUSTER)/hosts.json`), the Flux cluster directory, and the default fixture host. Recovery commands set `CLUSTER=recovery`.

| Option | Trade-off |
| --- | --- |
| One `CLUSTER` variable, one inventory per cluster (selected) | A stale inventory cannot send a recovery command to the primary. Every recovery command needs the variable. |
| Set `TF_DIR` and regenerate one shared inventory | No new variable. The last `make inventory` silently decides which cluster every later command reaches. |

### Add an `infra/recovery` root that mirrors the primary root

`infra/recovery` calls the regional module with Belgium inputs, its own state prefix `recovery`, the name prefix `k8sdr-recovery`, and a subnet that does not overlap the primary subnet or the cluster pod and service ranges. It owns its worker data disk without `prevent_destroy`, so `terraform destroy` removes the whole recovery environment after a drill. It exposes the same outputs as the primary root, which the inventory and the existing output-contract test read.

The recovery worker's service account needs the backup grants from `infra/shared` before it can read the primary's sets. Its email is known in advance (`k8sdr-recovery-worker@<project>`), but the grant fails until the account exists, so the order is: apply `infra/recovery`, add `recovery` to `backup_clusters`, apply `infra/shared`, then bootstrap.

### Isolate the primary by stopping its VMs for the gate

Before the gate checks, stop both primary VMs with `gcloud compute instances stop`, then run the checks against `git-dr.sindrg.com`. Start the VMs after the gate. Stopping keeps the disks, so the primary resumes unchanged, and it matches the failure the drill simulates: nothing in the primary region answers.

| Option | Trade-off |
| --- | --- |
| Stop the primary VMs (selected) | Real loss of every primary service. The Healthchecks.io heartbeat alerts while they are stopped, and the primary takes no backups. |
| A firewall rule that blocks the primary's public address | The primary keeps running, but the recovery path could still reach primary-region services that the rule misses. |

## Consequences

- The primary gains a DNS record, a certificate, and two Gateway listeners. `validate_services.yml` checks both names.
- Every operator command against the recovery cluster needs `CLUSTER=recovery`.
- Recovery credentials are unchanged: the Cloudflare token, both age keys, and the ping URL are already in the credential store.
- The recovery environment costs the same as the primary while it runs. It is destroyed after the gate.

## Known limits

- (Superseded by the amendment below.) The recovery cluster's hourly backup CronJob starts with the bootstrap, so a set taken before the restore contains an empty service. Restore reads the `primary/` prefix, so this does not affect milestone 5. Failback in a later milestone must pick a set taken after the restore.
- Gitea answers requests for `git_cluster_host` but writes `git.sindrg.com` into links and redirects. The fixture checks use the API and Git over HTTPS, which follow the requested host. The gate confirms this.

## Amendment: recovery backups stay suspended until the cutover

Date: 2026-10-03

A backup taken on a recovery cluster before its restore would publish a complete but empty `recovery/` set. It would also ping the Healthchecks.io check that both clusters share, and silence the alert for the primary's missing backups while the primary is down.

`cluster-settings` therefore carries `backup_suspend`, which sets `suspend` on the backup CronJob. The primary sets `"false"` and the recovery cluster `"true"`.

| Phase | Recovery CronJob | Heartbeat |
| --- | --- | --- |
| Bootstrap to verified restore | Suspended | Alerts for the stopped primary |
| Milestone 5 gate, no cutover | Suspended | Alerts until the primary restarts |
| After a DNS cutover in a drill or a real loss | Enabled by a reviewed change of `backup_suspend` to `"false"` in `deploy/clusters/recovery/cluster-settings.yaml` | The recovery cluster pings the same check, which then reports on the serving cluster |

The recovery cluster keeps the prefix `recovery/` and the shared ping URL in both phases. One check for the serving cluster needs no second credential. Its limit is that it cannot tell which cluster sent a ping.

`restore.yml` resumes the CronJob only if it was running before the restore, so a restore cannot enable backups as a side effect. A failed restore leaves them suspended.

| Option | Trade-off |
| --- | --- |
| A settings key that Flux substitutes (selected) | One reviewed line enables backups. Flux owns the field, so `kubectl patch` does not persist. |
| A recovery-only overlay that deletes the CronJob | No new key, but the recovery cluster would need its own Flux path, which the shared sync definition removes. |
| A second Healthchecks.io check for the recovery cluster | Separates the two heartbeats, but adds a credential that the recovery store must hold and a check that is silent most of the time. |

The milestone 5 gate checks that a bootstrap and a restore leave the CronJob suspended and write no `recovery/` object. The milestone 6 drill exercises the change that enables it.

## Amendment: return to the primary after a drill

Date: 2026-10-04

[Milestone 6](../../plan.md#6-disaster-drill) runs the drill twice. After the first drill the recovery cluster serves `git.sindrg.com`, backs up to `recovery/`, and the primary is stopped and fenced. The second drill needs the starting state again, and the project does not keep two regions running.

A drill therefore ends by returning to the primary. The stopped primary still holds its data as of the isolation. The return starts it, points `git.sindrg.com` back, restores its backup grant, suspends recovery backups again in Git, and destroys the recovery environment. The [runbook](../runbooks/regional-recovery.md#return-to-the-primary-after-a-drill) has the steps.

| Option | Trade-off |
| --- | --- |
| Return to the stopped primary and discard the recovery copy (selected) | A few commands and no data movement. Writes that the recovery cluster accepted are lost; in a drill these are only write checks. It is not a failback and proves nothing about one. |
| Fail back: restore the newest `recovery/` set into a rebuilt primary | Keeps the recovery writes and exercises the reverse path. It is a second recovery with its own runbook, checks, and cutover, which the plan does not include. |
| Keep the recovery cluster as the new primary and drill back to Finland | No return step. `infra/recovery` has no `prevent_destroy` and both roots would need to swap roles, which is a redesign. |

After a real loss of the primary there is no return: the recovery cluster stays in service, and going back to Finland is a planned failback that this project does not build.

Sets that the recovery cluster wrote stay under `recovery/` until the bucket's retention and lifecycle rules remove them. The next restore still reads `primary/`.
