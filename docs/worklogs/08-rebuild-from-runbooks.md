# Rebuild from the runbooks

Status: Complete. The [gate](#gate) passed on 2026-10-16. This file is the record of milestone 8.

## Scope

The environment was removed after milestone 7. Only the Terraform state bucket remained, and the `shared`, `primary`, and `recovery` states were empty. This rebuild follows the runbooks as written and records every step that did not work as documented.

[Milestone 9](09-backups-without-downtime.md) changes the worker data disk layout. It is built during this rebuild, before the fixtures exist, so no data has to be migrated.

## Primary infrastructure

Runbook: [primary infrastructure](../runbooks/primary-infrastructure.md). Date: 2026-10-14.

Steps 1 to 4 were skipped: they create the state bucket, which survived the teardown. Step numbers below refer to the runbook as run on 2026-10-14. The fix for deviation 2 swapped steps 5 and 6, so the primary root is now step 5 and the shared root step 6.

| Step | Command | Result |
| --- | --- | --- |
| 5, shared root, as written | `terraform plan`, then `terraform apply` in `infra/shared` | Plan: 8 to add. Apply created 6 and failed on 2; see deviation 2 |
| 6, primary root | `terraform plan`, then `terraform apply` in `infra/primary` | Plan: 20 to add. `Apply complete! Resources: 20 added, 0 changed, 0 destroyed.` |
| 5 again, shared root | `terraform plan`, then `terraform apply` in `infra/shared` | Plan: 2 to add. `Apply complete! Resources: 2 added, 0 changed, 0 destroyed.` |
| 7, convergence | `terraform plan -detailed-exitcode` in both roots | Exit `0` in `shared` and `primary` |
| 7, placement | `gcloud compute instances list --filter='labels.environment=primary'` | Both VMs `RUNNING` in `europe-north1-a`, internal addresses only, empty external address column |
| 7, data disk | `gcloud compute disks describe <worker data disk>` | 50 GB, one user: the worker VM |
| 8, IAP and private traffic | `gcloud compute ssh <worker> --tunnel-through-iap --command="ping -c 3 <control plane>; curl -fsSI https://example.com \| head -1"` | `3 packets transmitted, 3 received, 0% packet loss`, then `HTTP/2 200` |
| 9, recovery reader | `gcloud storage ls gs://<backup bucket>/` as the recovery reader | Not yet valid: the first attempt signed in as the administrator, not the recovery reader |
| 9, recovery reader, attempts 2 and 3 | Same, after `gcloud auth login` in a separate shell | Not valid: both signed in as the administrator. The administrator's upload in attempt 2 created `reader-write-test` at 2026-10-15T18:03:21Z. The live object was removed; the bucket retention policy holds the noncurrent version for 14 days. See deviation 4 |
| 9, recovery reader, attempt 4, 2026-10-16 | Temporary `CLOUDSDK_CONFIG`, `gcloud auth login <reader>`, account check, `gcloud storage ls gs://<backup bucket>/primary/`, upload attempt | `account OK (reader)`. The listing succeeded; its last lines were sets `20261016T040701Z` and `20261016T042201Z`. The upload output was not captured; a listing of all versions as the administrator afterwards showed no new `reader-write-test` version |
| 9, rewritten check | The block from the fix for deviation 4 | Not run as the reader; see [#90](https://github.com/sindredg/k8s-dr/issues/90). As the administrator on 2026-10-16, the permission test returned `storage.objects.create`, `delete`, `get`, and `list`, so it reports a write grant when one exists |
| 9, reader grants, 2026-10-16 | Bucket and project IAM policy read as the administrator | The reader holds `roles/storage.objectViewer` on the backup bucket, with no condition, and no project role. The bucket has no group, domain, or public members and uses uniform bucket-level access |

### Deviations

| # | Step | Documented | Observed | Fix |
| --- | --- | --- | --- | --- |
| 1 | 5 | One bucket, two bucket IAM members, three project IAM members | `Plan: 8 to add`: also a third bucket IAM member and the uptime check | Runbook lists all 8 |
| 2 | 5 | The shared root applies before the primary root | `Error 400: Service account k8sdr-primary-worker@<project>.iam.gserviceaccount.com does not exist.` for the writer and reader grants | Runbook applies the primary root first. [Milestone 10](10-automated-recovery.md) moves the node service accounts to the shared root, which removes the dependency |
| 3 | 6 | VPC, subnet, NAT, two VMs, the data disk, and two grants | `Plan: 20 to add`: also the service accounts, router, three firewall rules, and five load balancer resources | Runbook lists all 20 by group |
| 4 | 9 | `gcloud auth login` in a separate shell, then list as the recovery reader | The separate shell shares the gcloud configuration, so the login replaced the administrator's, and nothing showed which account signed in. Attempts 2 and 3 ran as the administrator | Runbook uses a temporary `CLOUDSDK_CONFIG`, checks the signed-in account, and replaces the write attempt with a permission test that writes nothing |

## Kubernetes bootstrap

Runbook: [Kubernetes bootstrap](../runbooks/kubernetes-bootstrap.md).

| Step | Command | Result |
| --- | --- | --- |
| Toolchain | `make venv` | Requirements already satisfied |
| Inventory | `make inventory`, then `git status --short ansible/inventory` | Inventory written; `git status` printed nothing |
| Boot image | `gcloud compute disks describe` on both boot disks | Both `ubuntu-2404-noble-amd64-v20260918`, equal to the tracked default |
| Bootstrap, first run | `make bootstrap` | `failed=0` and `unreachable=0` on both hosts. `run_with_iap: started 2026-10-14T20:53:51Z, finished 2026-10-14T21:02:25Z, elapsed 514s, exit 0` |

## Service deployment, fixtures, and restore

Runbook: [service deployment](../runbooks/service-deployment.md). The data disk conversion of milestone 9, the rest of the bootstrap, the `git` DNS record, the fixtures, and the restore of set `20261014T232201Z` into the test namespaces ran on 2026-10-14 and are recorded in the milestone 9 worklog: [live conversion](09-backups-without-downtime.md#live-conversion-of-the-primary-data-disk) and [restore of a set taken under load](09-backups-without-downtime.md#restore-of-a-set-taken-under-load). Its deviations are listed there. That restore ran before the fix in [troubleshooting 05](../troubleshooting/05-git-clone-rejected-fixture-user.md); its fixture checks passed with the earlier helper, and no restore has run with the fixed one.

The four `Report what this run created` lines of the 2026-10-14 fixture run were not recorded. The fixture commit is built deterministically and its hash is pinned in `recovery/fixtures.yaml`, so the check below identifies the same fixture.

| Step | Command | Result |
| --- | --- | --- |
| 6, cluster record, 2026-10-16 | `git-primary` A record in Cloudflare, DNS only | `dig +short git-primary.sindrg.com @1.1.1.1` returned the public address |
| 7, both names, 2026-10-16 | `curl -sS -o /dev/null -w '%{http_code}' http://<name>/` for `git.sindrg.com` and `git-primary.sindrg.com` | `301` to `https://<name>/` for both; see deviation 5. `https://<name>/api/healthz` returned `200` for both, with a trusted certificate |
| Fixtures, 2026-10-16 | `make check-fixtures` | `failed=0`, after the fix in [troubleshooting 05](../troubleshooting/05-git-clone-rejected-fixture-user.md) |
| Fixtures, 2026-10-16T05:04Z | `make check-fixtures` | `ok=15 changed=0 failed=0`: user signed in, commit `3775f53a1042434d76ec940d3d49360e1b3a0847` on `main`, issue #1 |
| New push, 2026-10-16 | `make write-check` | `Push accepted at 2026-10-16T04:44:42Z`; `failed=0` |

| # | Step | Documented | Observed | Fix |
| --- | --- | --- | --- | --- |
| 5 | 7 | `404` from `http://git.sindrg.com/` | `301` to `https://git.sindrg.com/` once Gitea is deployed: the `gitea-https-redirect` route in `deploy/apps/gitea/gateway.yaml` redirects HTTP on both names | Runbook expects `404` before Gitea exists and `301` after, and prints the redirect target |
| 6 | 6 | Create both records in the Cloudflare dashboard | `git` was set with `make dns-set`; `git-primary` was created in the dashboard, because `scripts/dns_record.sh` manages only `git` and `git-dr` | Runbook names `make dns-set` for `git` and `git-dr` and the dashboard for `git-primary` |
| 7 | 1, 2 | Plan shows `6 to add, 1 to change` for the load balancer | The primary root built from empty state already includes the load balancer; see deviation 3 | Runbook skips steps 1 and 2 on a build from empty state and reads the address from the output |
| 8 | cert-manager 1 | Encrypt the Cloudflare token | The encrypted token file was not changed: `git log` shows its last commit on 2026-09-27, and the token is valid until 2026-11-01 | Runbook says when to skip it |

## Gate

The milestone 8 gate passed on 2026-10-16:

- The primary serves the fixtures: `make check-fixtures` passed on 2026-10-16T05:04Z against `https://git.sindrg.com/`, and `make write-check` pushed a commit at 2026-10-16T04:44:42Z.
- A backup set restores in the test namespaces: set `20261014T232201Z`, taken while the write loop ran, restored into `gitea-restore` on 2026-10-14 with `failed=0`, and the fixture and push checks passed against it.
- Deviations 1 to 8 above and the deviations in the milestone 9 worklog list every step that did not work as documented, each with its fix.

An independent review on 2026-10-16 checked these claims against the runbooks, the code, and the live system. Its findings are fixed in this worklog and the runbooks, except the rewritten recovery reader check, which has not run as the reader ([#90](https://github.com/sindredg/k8s-dr/issues/90)).
