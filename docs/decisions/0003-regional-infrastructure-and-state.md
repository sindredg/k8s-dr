# 0003: Regional infrastructure and offsite state

Status: Accepted for milestone 1 implementation; cloud validation is pending.

Date: 2026-09-23

## Context

The Finland cluster must be reproducible after a regional loss. Terraform state, application backups, and credentials must remain reachable without its VMs. The GCS backend cannot create the bucket it needs before initialization.

## Decision

- Use one regional cluster module for the VPC, subnet, firewall, NAT, two VMs, their service accounts, and the dedicated worker disk. The primary root instantiates it only in Finland.
- Use separate GCS buckets in Belgium for Terraform state and application backups. Bootstrap the state bucket with temporary local Terraform state, then migrate that state into the bucket. Manage the backup bucket in the primary root.
- Use distinct bucket IAM members for state operations, backup operations, and recovery reads. Keep their credentials in an external credential store available when Finland is unavailable. VM service accounts receive no bucket roles in milestone 1.
- Give VMs private addresses. Permit SSH ingress from the [IAP TCP forwarding range](https://docs.cloud.google.com/iap/docs/using-tcp-forwarding) and node traffic within the cluster subnet. Use Cloud NAT for outbound internet access and Private Google Access for Google APIs.
- Enable bucket versioning, uniform bucket-level access, and public-access prevention. Use `force_destroy = false` and `prevent_destroy` on both buckets and the worker data disk.
- Scope a monthly billing budget to this project's number. Set its amount and ISO currency code from the billing account. The intended alert is the billing-currency equivalent of 1,000 NOK.

## Why this boundary

The regional module is a coherent unit that a future Belgium root can instantiate with Belgium inputs. It has no reference to Finland resources or Terraform outputs. State, backups, and the budget are project-level concerns that remain available if the primary cluster is gone. A module for each VM or bucket would add interfaces without meaningful reuse.

## Trade-offs and limits

- One control plane and one worker have no local high availability. The worker disk is zonal and does not replicate to Belgium. The future backup process provides the recovery data.
- Both offsite buckets are in Belgium. They survive a Finland outage, but a simultaneous Belgium outage can interrupt recovery. A second offsite copy is outside milestone 1.
- The state bucket holds its own bootstrap state after migration. The operator must preserve the separate encrypted bootstrap copy and must not destroy the bucket casually.
- Bucket versioning protects previous object generations but increases storage cost. There is no backup retention or lifecycle policy until milestone 4 defines verified backup sets and deletion rules.
- The node-to-node firewall allows TCP, UDP, and ICMP from the cluster subnet. This supports kubeadm and the planned CNI without opening public ingress. Host firewall and Kubernetes policy remain milestone 2 work.
- Project-level IAP and OS Login grants are straightforward for this single-purpose project but broader than per-instance grants. Only the configured administrator member receives them. Node service accounts have no project or bucket roles; their `cloud-platform` OAuth scope alone does not authorize API access.
- No public service endpoint exists yet. Milestone 3 must design ingress and DNS routing before Gitea can be public.
- The boot image uses a moving Ubuntu LTS family by default. Pin `boot_image` to a tested image self-link before a reproducibility drill.

See the [milestone 1 procedure](../runbooks/primary-infrastructure.md) for bootstrap, protection, validation, and calculator inputs.
