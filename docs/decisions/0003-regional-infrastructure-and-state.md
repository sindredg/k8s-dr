# 0003: Regional infrastructure and offsite state

Status: Accepted. Milestone 1 cloud validation passed on 2026-09-23. Amended on 2026-09-25 for a shared root and a root-owned data disk; see the [amendment](#amendment-shared-root-and-root-owned-data-disk).

Date: 2026-09-23

## Context

The Finland cluster must be reproducible after a regional loss. Terraform state, application backups, and credentials must remain reachable without its VMs. The GCS backend cannot create the bucket it needs before initialization.

## Decision

- Use one regional cluster module for the VPC, subnet, firewall, NAT, two VMs, their service accounts, and the dedicated worker disk. The primary root instantiates it only in Finland.
- Use separate GCS buckets in Belgium for Terraform state and application backups. Bootstrap the state bucket with temporary local Terraform state, then migrate that state into the bucket. Manage the backup bucket in the primary root.
- Use distinct bucket IAM members for state operations, backup operations, and recovery reads. Keep their credentials in an external credential store available when Finland is unavailable. VM service accounts receive no bucket roles in milestone 1.
- Give VMs private addresses. Permit SSH ingress from the [IAP TCP forwarding range](https://docs.cloud.google.com/iap/docs/using-tcp-forwarding) and node traffic within the cluster subnet. Use Cloud NAT for outbound internet access and Private Google Access for Google APIs.
- Enable bucket versioning, uniform bucket-level access, and public-access prevention. Use `force_destroy = false` and `prevent_destroy` on both buckets and the worker data disk.
- Do not manage a billing budget in this Terraform root. The original alert decision was superseded by [decision 0004](0004-remove-budget-alert.md).

## Why this boundary

The regional module is a coherent unit that a future Belgium root can instantiate with Belgium inputs. It has no reference to Finland resources or Terraform outputs. State and backups remain available if the primary cluster is gone. A module for each VM or bucket would add interfaces without meaningful reuse.

## Trade-offs and limits

- One control plane and one worker have no local high availability. The worker disk is zonal and does not replicate to Belgium. The future backup process provides the recovery data.
- Both offsite buckets are in Belgium. They survive a Finland outage, but a simultaneous Belgium outage can interrupt recovery. A second offsite copy is outside milestone 1.
- The state bucket holds its own bootstrap state after migration. The operator must preserve the separate encrypted bootstrap copy and must not destroy the bucket casually.
- Bucket versioning protects previous object generations but increases storage cost. There is no backup retention or lifecycle policy until milestone 4 defines verified backup sets and deletion rules.
- The node-to-node firewall allows TCP, UDP, and ICMP from the cluster subnet. This supports kubeadm and the planned CNI without opening public ingress. Host firewall and Kubernetes policy remain milestone 2 work.
- Project-level IAP and OS Login grants are straightforward for this single-purpose project but broader than per-instance grants. Only the configured administrator member receives them. Node service accounts have no project or bucket roles; their `cloud-platform` OAuth scope alone does not authorize API access.
- No public service endpoint exists yet. Milestone 3 must design ingress and DNS routing before Gitea can be public.
- The boot image was a moving Ubuntu LTS family by default. The amendment below tracks the tested image in the module.

## Amendment: shared root and root-owned data disk

Date: 2026-09-25.

**Problem:** the original layout does not hold a second region.

| Issue | Consequence |
| --- | --- |
| `infra/primary` owns the project IAM grants for IAP, OS Login, and instance administration. | A recovery root needs the same grants. `google_project_iam_member` is additive, so two roots would own one grant, and destroying the recovery root after a drill would revoke primary access. |
| `infra/primary` owns the backup bucket. | The bucket exists to outlive the primary region, but its lifecycle is tied to the primary root. |
| The module owns the worker data disk with `prevent_destroy`. | A lifecycle setting cannot come from a variable, so every recovery drill disk would block `terraform destroy`. |
| The inventory reads `primary_zone` and `primary_subnet_cidr`. | A recovery root would need outputs named after the wrong region. |
| The tested image lived only in an ignored `terraform.tfvars`. | A recovery from another operator machine would fall back to the moving image family. |

**Decision:**

- Add an `infra/shared` root with its own state prefix. It owns the backup bucket, its IAM members, and the project-level administrator grants. Regional roots own only regional resources and the service account grants for their own nodes.
- Move the worker data disk from the module to the calling root. The module attaches a disk ID it receives. The primary root keeps `prevent_destroy`; a recovery root can omit it.
- Rename the primary outputs to `zone` and `subnet_cidr`. Every regional root exposes the same output contract, and a unit test checks it.
- Track the tested image self-link as the module's `boot_image` default. Images are global, so both regions use the same one.
- Move existing resources with `moved`, `removed`, and `import` blocks instead of recreating them. See the [migration procedure](../runbooks/terraform-shared-root-migration.md).

**Trade-offs:**

- One more root to initialize and apply, in a fixed order: bootstrap, shared, then regional roots.
- `admin_member` is set in both the shared and primary roots. The two values must match.
- The import blocks require Terraform 1.7 or later and must be deleted after the migration, because they fail on a fresh deployment.
- The shared root is a single point of configuration for access. A mistaken apply there affects every region, so review its plans with the same care as the state bucket.

See the [milestone 1 procedure](../runbooks/primary-infrastructure.md) for bootstrap, protection, validation, and calculator inputs.
