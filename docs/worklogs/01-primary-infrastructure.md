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

## Failures and remaining work

All milestone 1 plan steps remain open. Record failed checks and follow-up work here as they occur. Do not paste credentials, Terraform state, or raw command output.
