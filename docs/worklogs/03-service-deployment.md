# Milestone 3: Service deployment

Status: Pending. Preparation work is implemented and awaits validation.

## Scope

Bootstrap Flux from the external GitHub repository, deploy Gitea and PostgreSQL with persistent volumes, and create the recovery fixtures. See [milestone 3](../../plan.md#3-service-deployment).

## Preparation

The [pre-milestone 3 steps](../../plan.md#before-milestone-3-recovery-readiness) change the layout and tooling before the worker disk holds service data.

### Work completed

| Area | Files | Summary |
| --- | --- | --- |
| Shared Terraform root | `infra/shared/`, `infra/primary/`, `infra/modules/regional_cluster/` | New root for the backup bucket, its IAM members, and project administrator grants. The primary root owns the worker data disk; the module attaches a disk ID. One-time `moved`, `removed`, and `import` blocks migrate existing resources. See the [decision 0003 amendment](../decisions/0003-regional-infrastructure-and-state.md#amendment-shared-root-and-root-owned-data-disk). |
| Output contract | `infra/primary/outputs.tf`, `scripts/prepare_ansible_inventory.py`, `tests/` | Region-neutral `zone` and `subnet_cidr` outputs. A test checks that each regional root declares every output the inventory needs. |
| Tracked boot image | `infra/modules/regional_cluster/variables.tf` | The tested `ubuntu-2404-noble-amd64-v20260918` image is the module default instead of an ignored local value. |
| Procedures | `docs/runbooks/terraform-shared-root-migration.md`, `primary-infrastructure.md`, `kubernetes-bootstrap.md` | One-time migration steps, the fresh-deployment order, and the image confirmation step. |

Local checks: `terraform fmt -check -recursive` and `terraform validate` pass for the bootstrap, shared, and primary roots. `python3 -m unittest discover -s tests` passes. These checks are not gate evidence.

### Validation record

No results recorded yet. Run the [shared-root migration](../runbooks/terraform-shared-root-migration.md).
