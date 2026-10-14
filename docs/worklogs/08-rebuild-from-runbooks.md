# Rebuild from the runbooks

Status: In progress. This file is the record of [milestone 8](../../plan.md#8-rebuild-from-the-runbooks).

## Scope

The environment was removed after milestone 7. Only the Terraform state bucket remained, and the `shared`, `primary`, and `recovery` states were empty. This rebuild follows the runbooks as written and records every step that did not work as documented.

[Milestone 9](../../plan.md#9-backups-without-downtime) changes the worker data disk layout. It is built during this rebuild, before the fixtures exist, so no data has to be migrated.

## Primary infrastructure

Runbook: [primary infrastructure](../runbooks/primary-infrastructure.md). Date: 2026-10-14.

Steps 1 to 4 were skipped: they create the state bucket, which survived the teardown.

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

### Deviations

| # | Step | Documented | Observed | Fix |
| --- | --- | --- | --- | --- |
| 1 | 5 | One bucket, two bucket IAM members, three project IAM members | `Plan: 8 to add`: also a third bucket IAM member and the uptime check | Runbook lists all 8 |
| 2 | 5 | The shared root applies before the primary root | `Error 400: Service account k8sdr-primary-worker@<project>.iam.gserviceaccount.com does not exist.` for the writer and reader grants | Runbook applies the primary root first. [Milestone 10](../../plan.md#10-automated-recovery) moves the node service accounts to the shared root, which removes the dependency |
| 3 | 6 | VPC, subnet, NAT, two VMs, the data disk, and two grants | `Plan: 20 to add`: also the service accounts, router, three firewall rules, and five load balancer resources | Runbook lists all 20 by group |

## Kubernetes bootstrap

Runbook: [Kubernetes bootstrap](../runbooks/kubernetes-bootstrap.md).

| Step | Command | Result |
| --- | --- | --- |
| Toolchain | `make venv` | Requirements already satisfied |
| Inventory | `make inventory`, then `git status --short ansible/inventory` | Inventory written; `git status` printed nothing |
| Boot image | `gcloud compute disks describe` on both boot disks | Both `ubuntu-2404-noble-amd64-v20260918`, equal to the tracked default |
| Bootstrap, first run | `make bootstrap` | `failed=0` and `unreachable=0` on both hosts. `run_with_iap: started 2026-10-14T20:53:51Z, finished 2026-10-14T21:02:25Z, elapsed 514s, exit 0` |

## Remaining

- Step 9 of the primary infrastructure runbook as the recovery reader.
- The rest of the bootstrap, the service deployment, the fixtures, a backup set, and a test restore, after milestone 9 changes the data disk layout.
