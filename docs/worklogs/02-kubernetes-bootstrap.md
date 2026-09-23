# Milestone 2: Kubernetes bootstrap

Status: In progress. Automation is implemented and passes local checks. No live cluster validation is recorded yet.

## Scope

Configure Linux, containerd, kubelet, kubeadm, and kubectl with Ansible. Initialize the control plane, join the worker, install a CNI, and add only the ingress and storage components required for the service. Automate a rebuild from fresh VMs. See [milestone 2](../../plan.md#2-kubernetes-bootstrap).

## Work completed

Implemented on 2026-09-23 on branch `feat/milestone-2-kubernetes-bootstrap`, following the [implementation plan](../superpowers/plans/2026-09-23-kubernetes-bootstrap.md). None of it has run against the live VMs.

| Area | Files | Summary |
| --- | --- | --- |
| Toolchain and CI | `ansible/requirements.txt`, `ansible.cfg`, `ansible/.yamllint.yml`, `.github/workflows/ansible.yml` | Pinned controller packages. CI runs unit tests, yamllint, ansible-lint, playbook syntax checks, and `terraform validate` without a backend. |
| Inventory | `scripts/prepare_ansible_inventory.py`, `infra/primary/outputs.tf` | Builds an ignored JSON inventory from Terraform outputs and rejects malformed or overlapping network data. |
| IAP access | `scripts/run_with_iap.py` | Opens one IAP tunnel per node, scans host keys into a private `known_hosts`, runs Ansible, and stops every tunnel on exit. |
| Node preparation | `ansible/roles/node_prepare/`, `container_runtime/`, `kubernetes_packages/` | Configures Ubuntu, containerd with the systemd cgroup driver, and held Kubernetes 1.36.2 packages. |
| Worker storage | `ansible/roles/worker_storage/` | Refuses the boot disk and unexpected filesystems, formats only a blank data disk, and mounts it by UUID. |
| Cluster creation | `ansible/roles/control_plane/`, `worker_join/` | Initializes kubeadm once and joins the worker with a protected, unlogged join command. Partial state stops the run instead of resetting. |
| Add-ons | `ansible/roles/cluster_addons/` | Installs Calico 3.32.2 with VXLAN, Gateway API 1.6.1 Standard CRDs, Traefik chart 41.6.0 on NodePort 30080, and Local Path Provisioner 0.0.36 restricted to the worker disk. |
| Test application | `ansible/manifests/milestone2-app.yml`, `ansible/playbooks/deploy_test_app.yml`, `validate.yml`, `cleanup_test_app.yml` | Deploys a private nginx app with a PVC marker and an HTTPRoute for `milestone2.local`, collects read-only evidence, and removes the app. |
| Procedure | `docs/runbooks/kubernetes-bootstrap.md` | Lists operator commands, expected results, and evidence for each gate condition. |

Local checks: `python3 -m unittest discover -s tests`, yamllint with `ansible/.yamllint.yml`, `ansible-lint ansible`, and `--syntax-check` for all four playbooks pass. These checks are not gate evidence.

## Validation gate

Do not mark this milestone complete until evidence shows that:

1. Both nodes report `Ready` after bootstrap.
2. A disposable app schedules and can be reached through the intended path.
3. The worker rejoins after a restart.
4. A fresh rebuild follows the same automated steps.

## Validation record

| Date | Check and command | Result and sanitized evidence |
| --- | --- | --- |
| Pending | `kubectl get nodes -o wide` | Not run |
| Pending | Deploy and reach a disposable app | Not run |
| Pending | Restart worker, then check node status | Not run |
| Pending | Rebuild from fresh VMs | Not run |

## Failures and remaining work

### Undefined shared variables on the first bootstrap run

- **Symptom:** On the first live `bootstrap.yml` run, task `container_runtime : Install the pinned containerd package` failed with `'containerd_deb_version' is undefined`.
- **Confirmed cause:** Ansible loads `group_vars/` only from the inventory file's directory and the playbook's directory. The shared variables were in `ansible/group_vars/all.yml`, next to neither `ansible/inventory/generated/hosts.json` nor `ansible/playbooks/`, so every pinned version and path was undefined. Reproduced locally with `ansible-inventory --playbook-dir ansible/playbooks --host <node>`, which returned no variables. The local checks missed it because syntax checks and file-reading tests do not resolve variables.
- **Fix:** Moved the file to `ansible/playbooks/group_vars/all.yml`, which Ansible loads for every playbook in that directory. Added `tests/test_ansible_variable_loading.py`, which resolves each shared variable through `ansible-inventory` for an inventory outside `ansible/`.
- **Verification:** The new test passes locally, and a local probe playbook prints `containerd_deb_version` from a generated-style inventory. On the live rerun, `Install the pinned containerd package` reported `changed` on both nodes, and the run continued to the next failure below.

### Kubernetes package revision not published

- **Symptom:** On the second live `bootstrap.yml` run, task `kubernetes_packages : Install pinned Kubernetes packages` failed on both nodes with `no available installation candidate for kubelet=1.36.2-1.1`. Recap: `ok=17 changed=5 unreachable=0 failed=1` per node.
- **Confirmed cause:** The `pkgs.k8s.io` `core:/stable:/v1.36` repository publishes Kubernetes 1.36.2 only as package revision `1.36.2-2.1` for `kubelet`, `kubeadm`, and `kubectl`. The pin used revision `1.1`, which does not exist. Checked with `curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.36/deb/Packages`.
- **Fix:** Set `kubernetes_deb_version` to `1.36.2-2.1`. The Kubernetes version stays 1.36.2, as decided in ADR 0005.
- **Verification:** On the next live run, package installation passed on both nodes, and the run continued to the next failure below.

### Mount check misread the `mountpoint` exit code

- **Symptom:** On the third live `bootstrap.yml` run, task `worker_storage : Check whether worker data is mounted` failed on the worker. `mountpoint -q /var/lib/k8s-dr` returned `rc=32`. Recap: control plane `failed=0`, worker `ok=38 changed=5 failed=1`.
- **Confirmed cause:** util-linux `mountpoint` returns `32` when the directory is not a mount point and `1` for errors such as a missing path. The role treated `1` as "not mounted" and failed on `32`. Confirmed with `man mountpoint` for util-linux 2.39.3, the version on Ubuntu 24.04, and locally: an unmounted directory returns `32`, and a missing path returns `1`.
- **State before the fix:** Earlier tasks in the same run formatted the blank data disk as ext4 and wrote its UUID entry to `/etc/fstab`. A rerun detects `ext4`, skips `mkfs`, and continues to the mount.
- **Fix:** Accept `0` and `32`, and mount only on `32`. Added a contract test that pins both expressions.
- **Verification:** Local tests pass. The live bootstrap rerun is pending.

All milestone 2 plan steps remain open. For join failures, use the [worker join guide](../troubleshooting/01-worker-join-failure.md) and record the observed symptom, confirmed cause, fix, and verification here. Do not paste kubeconfigs, join tokens, or unsanitized command output.
