# Milestone 1: Primary infrastructure

Status: In progress. Operator resource checks are recorded; the milestone gate remains open.

## Scope

Provision a primary control-plane VM and worker VM, their private network, restricted administration, offsite backup storage, and remote Terraform state. Recovery credentials must remain available without the primary VMs. See [milestone 1](../../plan.md#1-primary-infrastructure).

## Work completed

- Added a bootstrap Terraform root for a Belgium GCS state bucket with versioning, public-access prevention, bucket IAM, and an explicit local-state migration procedure.
- Added the primary Terraform root, including a Finland regional cluster module, private VMs, IAP SSH, Cloud NAT, a dedicated worker disk, a separate Belgium backup bucket, and IAM access.
- Added placeholder configuration, local-file ignore rules, a cost calculator input table, and the [operator procedure](../runbooks/primary-infrastructure.md).
- Recorded the architecture and its trade-offs in [decision 0003](../decisions/0003-regional-infrastructure-and-state.md). No Terraform, gcloud, deployment, or validation command has been run by the implementer.
- Recorded the observed budget errors in [troubleshooting](../troubleshooting/02-billing-budget-apply-errors.md) and clarified the tracked variable examples. Local values and provider lock files were not changed.
- Removed the budget resource, required budget and billing inputs, and project-number lookup at the operator's request. Updated the example, operator procedure, plan, and [decision 0004](../decisions/0004-remove-budget-alert.md). No Terraform or cloud validation was run by the implementer; local `terraform.tfvars` was not edited.

## Validation gate

Do not mark this milestone complete until evidence shows that:

1. Terraform can reproduce the primary VMs from code.
2. The control plane and worker can communicate over the intended private network.
3. Terraform state, backup storage, and recovery credentials remain accessible without the primary VMs.

## Validation record

| Date | Check and command | Result and sanitized evidence |
| --- | --- | --- |
| Pending | Reprovision primary VMs | Not run |
| Pending | Verify node connectivity | Not run |
| Pending | Verify independent access to state, storage, and credentials | Not run |
| 2026-09-23 | `terraform apply` in `infra/primary` | Failed during configuration evaluation: `Call to unknown function` for `round()` at `main.tf:101`. Output supplied by operator; post-fix verification pending. |
| 2026-09-23 | `terraform apply` in `infra/primary` | Failed provider validation: `all_updates_rule` required a Monitoring notification channel or Pub/Sub topic. Output supplied by operator; post-fix verification pending. |
| 2026-09-23 | `terraform apply` in `infra/primary` | Partial apply: operator output showed the Finland VMs, network and NAT, worker data disk, backup bucket, and IAM resources created. Budget creation failed with HTTP 403 because local ADC had no quota project. Post-fix verification pending. |
| 2026-09-23 | `terraform output instance_names`, `terraform output internal_ips`, `terraform output backup_bucket_name`, and `gcloud compute instances list` | Operator output showed two running VMs in Finland with internal addresses and no external NAT addresses; the backup bucket output returned a name. This does not establish node connectivity or independent storage access. |
| 2026-09-23 | `gcloud compute disks describe "$(terraform output -raw worker_data_disk_name)" --project="$PROJECT_ID" --zone=europe-north1-a` | Operator output showed a 50 GiB balanced disk in READY state, attached to the worker VM. A separate `--format=...` shell line failed with `command not found`; the disk describe itself succeeded. |

## Failures and remaining work

All milestone 1 plan steps remain open. An earlier apply output showed the infrastructure created and the budget failing. The operator later reported that Terraform still prompted for a number after deleting a local value, but did not provide new command output. The budget has now been removed from configuration. The VM and disk checks above are partial evidence; a fresh no-change plan, node connectivity, and independent state, backup, and credential access remain unverified. See [billing budget apply errors](../troubleshooting/02-billing-budget-apply-errors.md) for the historical symptoms and current disposition. Do not paste credentials, Terraform state, or raw command output.
