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
| Split validation | `ansible/playbooks/validate_cluster.yml`, `validate_test_app.yml`, `validate.yml` | Cluster checks run without an application, so a recovery drill can use them right after bootstrap. `validate.yml` imports both, so existing commands still work. The app checks now fail unless the Gateway is `Programmed`, the HTTPRoute is `Accepted`, and the PVC is `Bound`; milestone 2 only listed them. |
| Run timing | `scripts/run_with_iap.py` | Prints UTC start and finish times, elapsed seconds, and the exit code after each command. No new dependency. Per-task timing through the `ansible.posix.profile_tasks` callback would add a Galaxy collection download to the recovery path, so it is deferred until milestone 7 needs task-level bottlenecks. |
| Pin check | `scripts/check_pins.py`, `.github/workflows/pins.yml` | Confirms every pinned artifact in `group_vars/all.yml` still resolves: the Kubernetes package revision, the containerd build, the Traefik chart, and the Calico, Gateway API, Local Path, and Helm files. Runs on pull requests, on `main`, and weekly. A unit test fails when a new version pin is not checked. |
| Service architecture | `docs/decisions/0006-service-deployment-architecture.md`, `docs/production-readiness.md`, `docs/decisions/0001-use-kubeadm.md` | Proposed decisions for Flux, the Ansible and Flux ownership boundary, SOPS secrets, PostgreSQL, Gitea, public exposure, and network policies. Records the regulatory VM rationale and the production gap. Awaits operator decisions. |

Local checks: `terraform fmt -check -recursive` and `terraform validate` pass for the bootstrap, shared, and primary roots. `python3 -m unittest discover -s tests`, yamllint, ansible-lint, and syntax checks for all six playbooks pass. These checks are not gate evidence.

### Findings

**The containerd pin will stop resolving.** Ubuntu's archive index lists only the newest build of a package in each pocket. `curl -fsSL "https://api.launchpad.net/1.0/ubuntu/+archive/primary?ws.op=getPublishedBinaries&binary_name=containerd&exact_match=true&distro_arch_series=https://api.launchpad.net/1.0/ubuntu/noble/amd64"` on 2026-09-25 showed `2.2.1-0ubuntu1~24.04.3` as `Published` and `2.2.1-0ubuntu1~24.04.2` as `Superseded`. When Ubuntu publishes the next build, `containerd={{ containerd_deb_version }}` fails on a fresh node, including in a recovery drill.

- `scripts/check_pins.py` detects this. Run against a copy of the variables with `~24.04.2`, it exits `1` with `2.2.1-0ubuntu1~24.04.2 is no longer published`. With the Kubernetes revision `1.36.2-1.1` from the milestone 2 failure, it exits `1` with `1.36.2-1.1 not published for kubelet, kubeadm, kubectl`.
- Options, not yet decided: install containerd from a source that keeps old versions, such as Docker's `containerd.io` repository or the upstream release archive with a checksum; or accept the risk and update the pin whenever the check fails. Either change needs the milestone 2 bootstrap validation again.

### Validation record

No results recorded yet. Run the [shared-root migration](../runbooks/terraform-shared-root-migration.md).
