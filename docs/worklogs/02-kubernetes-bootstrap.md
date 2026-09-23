# Milestone 2: Kubernetes bootstrap

Status: In progress. Automation is implemented and passes local checks. Gate check 1 (both nodes `Ready`) passed on 2026-09-23. Gate checks 2 to 4 are not run. See [Resume here](#resume-here).

## Scope

Configure Linux, containerd, kubelet, kubeadm, and kubectl with Ansible. Initialize the control plane, join the worker, install a CNI, and add only the ingress and storage components required for the service. Automate a rebuild from fresh VMs. See [milestone 2](../../plan.md#2-kubernetes-bootstrap).

## Work completed

Implemented on 2026-09-23 on branch `feat/milestone-2-kubernetes-bootstrap`, following the [implementation plan](../superpowers/plans/2026-09-23-kubernetes-bootstrap.md). The bootstrap needed six fixes during live runs. See [Failures and remaining work](#failures-and-remaining-work).

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
| 2026-09-23 | `bootstrap.yml` through `scripts/run_with_iap.py`, then `validate.yml -v` (runs `kubectl get nodes -o wide`), run by the operator | Passed. Bootstrap recap: control plane `ok=57 changed=9 unreachable=0 failed=0`, worker `ok=52 changed=0 unreachable=0 failed=0`. `k8sdr-primary-control-plane` (`control-plane`) and `k8sdr-primary-worker` (`worker`) are `Ready`, `v1.36.2`, Ubuntu 24.04.5 LTS, `containerd://2.2.1`. All pods in `calico-system`, `kube-system`, `tigera-operator`, `traefik`, and `local-path-storage` are `Running`; all six deployments are fully available. `validate.yml` then stopped at `Read Gateway conditions` with `namespaces "milestone2-test" not found`, as expected before the test application is deployed. |
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
- **Verification:** On the next live run, the worker play finished with `failed=0` (`ok=43`).

### Handlers lost after failed runs

- **Symptom:** On the fourth live `bootstrap.yml` run, `control_plane : Initialize the control plane` failed at kubeadm preflight with `[ERROR FileContent--proc-sys-net-ipv4-ip_forward]: /proc/sys/net/ipv4/ip_forward contents are not set to 1`. Preflight runs before kubeadm writes any state.
- **Confirmed cause:** `node_prepare` applied `/etc/sysctl.d/99-kubernetes.conf` only through a handler. Handlers run at the end of a play and are skipped when a task fails. The first run wrote the file and then failed at containerd, so the handler never ran. Later runs reported the file task as `ok`, so it never notified the handler again. The file was correct, but the kernel values were never applied.
- **Same cause, not yet observed:** `container_runtime` restarted containerd only through a handler. On the second run, `Configure containerd for Kubernetes` reported `changed`, and the play then failed at package installation. containerd therefore kept running with its package defaults instead of the systemd cgroup configuration. This is inferred from the task sequence, not observed on the node.
- **Fix:** Removed both handlers. `node_prepare` now reads the three sysctls on every run, runs `sysctl --system` when any is not `1`, and fails if they are still not `1`. `container_runtime` validates the configuration, then restarts containerd when its `ActiveEnterTimestamp` is older than the configuration file's modification time. Contract tests pin both behaviors.
- **Verification:** Local tests, yamllint, ansible-lint, and syntax checks pass. A local probe of the restart condition returns `True` for a stale or missing start time and `False` for a start after the change. On the fifth live run, the control plane initialized, the worker joined (`ok=58 failed=0`), and the run continued to the next failure below.

### Worker label used the inventory name instead of the node name

- **Symptom:** On the fifth live `bootstrap.yml` run, task `cluster_addons : Label the worker node for workload placement` failed with `Error from server (NotFound): nodes "worker" not found`. Recap: control plane `ok=36 changed=4 failed=1`, worker `ok=58 changed=4 failed=0`.
- **Confirmed cause:** The task labeled `{{ groups['kube_workers'] | first }}`, which is the Ansible inventory hostname `worker`. `kubeadm init` and `kubeadm join` register nodes by `gcp_instance_name` (`nodeRegistration.name` and `--node-name`), so the Kubernetes node is `k8sdr-primary-worker`. The Local Path Provisioner `nodePathMap` used the same wrong name. That would not fail the play, but the provisioner would treat the worker as unlisted and leave its PVCs Pending. The contract test pinned the wrong expression, so local checks passed.
- **Fix:** Both references now use `{{ hostvars[groups['kube_workers'] | first].gcp_instance_name }}`. The contract tests pin the new expression for the label task and the `nodePathMap`.
- **Verification:** Local unit tests (53), yamllint, and ansible-lint pass. Resolving the expression locally against the generated inventory returns `k8sdr-primary-worker`. On the sixth live run, the label task reported `changed`, and the run continued to the next failure below.

### Calico rollout check raced the Tigera operator

- **Symptom:** On the sixth live `bootstrap.yml` run, task `cluster_addons : Wait for Calico on every node` failed after 0.1 seconds with `Error from server (NotFound): namespaces "calico-system" not found`. Recap: control plane `ok=38 changed=5 failed=1`, worker `failed=0`.
- **Confirmed cause:** The Tigera operator creates the `calico-system` namespace and the `calico-node` DaemonSet asynchronously after it reconciles the `Installation`. `kubectl rollout status` fails immediately when the object does not exist, and the earlier operator rollout check returned before reconciliation. Immediately after the failure, `kubectl get ns` showed `calico-system` created about 16 seconds after `tigera-operator`, and `kubectl get tigerastatus` later reported `calico` as `Available=True`. Calico itself was healthy.
- **Fix:** Added `Wait for the operator to create the Calico node DaemonSet`, which retries `kubectl get daemonset/calico-node -n calico-system` every 5 seconds for up to 5 minutes before the rollout check. A contract test pins the task and its order.
- **Verification:** Local unit tests (54), yamllint, and ansible-lint pass. The seventh live run, `python3 scripts/run_with_iap.py ... bootstrap.yml`, run by Claude at the operator's request, exited `0`. Recap: control plane `ok=57 changed=15 unreachable=0 failed=0`, worker `ok=52 changed=0 unreachable=0 failed=0`. Calico already existed on that run, so the retry path passed on its first attempt; the race itself is exercised only on a fresh cluster (see [Rebuild from fresh VMs](../runbooks/kubernetes-bootstrap.md#rebuild-from-fresh-vms)).
- **Follow-up validation:** `validate.yml -v` listed `k8sdr-primary-control-plane` (`control-plane`) and `k8sdr-primary-worker` (`worker`), both `Ready` on `v1.36.2` with `containerd://2.2.1`. All pods in `calico-system`, `kube-system`, `tigera-operator`, `traefik`, and `local-path-storage` were `Running`. The run stopped at `Read Gateway conditions` (`failed=1`), which the runbook expects before the test application is deployed.
- **Observed, not blocking:** `tigerastatus/tiers` reports `Degraded` with `Waiting for Tigera API server to be ready`. The `Installation` does not request the Calico API server, and `calico`, `ippools`, and every Calico pod are healthy. Hypothesis: this status is expected without an `APIServer` resource. It is not investigated further in this milestone.

### Operator port forward dropped with a broken pipe

- **Symptom:** In [Validate the disposable application](../runbooks/kubernetes-bootstrap.md#validate-the-disposable-application), step 2 of the earlier runbook, `gcloud compute ssh <worker> --tunnel-through-iap -- -N -L 18080:127.0.0.1:30080` exited `255` with `WARNING: [0] Failed to send all data from [stdin].` and `client_loop: send disconnect: Broken pipe`. The operator did not note whether it failed immediately or later.
- **Cause (hypothesis, not confirmed):** the IAP WebSocket behind the `gcloud compute ssh` standard-input proxy closed. The same IAP and OS Login access works reliably through `scripts/run_with_iap.py`, which uses `gcloud compute start-iap-tunnel` with a local port. The failure is in the operator's SSH transport, not in the cluster, and says nothing about the application.
- **Fix:** Replaced the interactive forward with two read-only requests in `validate.yml`. They go from the control plane to the worker's private address on NodePort 30080, and expect `200` with the marker when sending `Host: milestone2.local`, and `404` without it. The pod-replacement step now runs through the IAP runner instead of `gcloud compute ssh`. [ADR 0005](../decisions/0005-kubernetes-bootstrap-architecture.md#amendment-in-cluster-reachability-check) records the change and its trade-off: the check no longer proves access from the operator machine.
- **Verification:** Local unit tests, yamllint, ansible-lint, and syntax checks pass. Rendering the request URL against the generated inventory gives the worker's private address on port `30080`. The live validation run is pending.

## Resume here

State on 2026-09-23: the primary cluster is bootstrapped and running. The operator reached the port-forward step, so the test application is probably deployed, but no deploy or validation output is recorded. Continue with the [runbook](../runbooks/kubernetes-bootstrap.md) in this order:

1. [Validate the disposable application](../runbooks/kubernetes-bootstrap.md#validate-the-disposable-application): rerun `deploy_test_app.yml` (safe to repeat), then `validate.yml -v`, which now includes the reachability requests. Then run the pod replacement check.
2. [Restart the worker](../runbooks/kubernetes-bootstrap.md#restart-the-worker).
3. [Rebuild from fresh VMs](../runbooks/kubernetes-bootstrap.md#rebuild-from-fresh-vms). This is the first run that exercises the Calico DaemonSet wait against a real race.

Known limitations to keep in mind:

- `validate.yml` fails at `Read Gateway conditions` until the test application is deployed. This is expected, but a failed recap does not by itself mean the cluster is unhealthy. Splitting cluster and application checks is a possible follow-up.
- The reachability check runs inside the VPC. HTTP access from the operator machine is not tested.
- `tigerastatus/tiers` is `Degraded` (see the Calico entry above). Not investigated.
- Ansible prints `INJECT_FACTS_AS_VARS` deprecation warnings for `ansible_*` facts in `node_prepare`. They do not affect results before ansible-core 2.24.

All milestone 2 plan steps remain open. For join failures, use the [worker join guide](../troubleshooting/01-worker-join-failure.md) and record the observed symptom, confirmed cause, fix, and verification here. Do not paste kubeconfigs, join tokens, or unsanitized command output.
