# Milestone 1: Primary infrastructure

Status: In progress. Terraform and operator documentation are written; cloud validation is pending.

## Scope

Provision a primary control-plane VM and worker VM, their private network, restricted administration, offsite backup storage, and remote Terraform state. Recovery credentials must remain available without the primary VMs. See [milestone 1](../../plan.md#1-primary-infrastructure).

## Work completed

- Added a bootstrap Terraform root for a Belgium GCS state bucket with versioning, public-access prevention, bucket IAM, and an explicit local-state migration procedure.
- Added the primary Terraform root, including a Finland regional cluster module, private VMs, IAP SSH, Cloud NAT, a dedicated worker disk, a separate Belgium backup bucket, IAM access, and a project-scoped monthly budget.
- Added placeholder configuration, local-file ignore rules, a cost calculator input table, and the [operator procedure](../runbooks/primary-infrastructure.md).
- Recorded the architecture and its trade-offs in [decision 0003](../decisions/0003-regional-infrastructure-and-state.md). No Terraform, gcloud, deployment, or validation command has been run by the implementer.

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

## Failures and remaining work

All milestone 1 plan steps remain open. Do not paste credentials, Terraform state, or raw command output.

- Symptom: Primary `terraform apply` stopped on `Call to unknown function` at `main.tf:101`.
- Confirmed cause: Terraform does not provide `round()`; the budget nanos expression called it.
- Fix written: Use supported `floor()` to turn the fractional billing-currency amount into whole nanos.
- Verification: The next apply advanced past this expression to provider validation; a successful plan and apply are still pending.

- Symptom: The next primary `terraform apply` reported missing `monitoring_notification_channels` and `pubsub_topic` in `all_updates_rule`.
- Confirmed cause: The configured `all_updates_rule` set only `enable_project_level_recipients`, while the selected Google provider requires a Monitoring channel or Pub/Sub topic when that block is present.
- Fix written: Remove the optional block and use default email delivery to Billing Account Administrators and Billing Account Users. Confirm an intended recipient has one of these roles.
- Verification: Pending operator rerun of `terraform validate` and `terraform plan`. No post-fix result has been supplied.
