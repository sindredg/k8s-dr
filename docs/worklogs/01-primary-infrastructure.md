# Milestone 1: Primary infrastructure

Status: Pending. No infrastructure or validation is recorded yet.

## Scope

Provision a primary control-plane VM and worker VM, their private network, restricted administration, offsite backup storage, and remote Terraform state. Recovery credentials must remain available without the primary VMs. See [milestone 1](../../plan.md#1-primary-infrastructure).

## Work completed

None recorded.

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
