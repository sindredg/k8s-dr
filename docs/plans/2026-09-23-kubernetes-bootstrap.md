# Kubernetes Bootstrap Implementation Plan

**Goal:** Build a repeatable, private Ansible and kubeadm bootstrap for the two-node primary cluster and provide the operator procedure needed to prove the Milestone 2 gate.

**Architecture:** Terraform exposes the minimum node metadata needed by a tested inventory generator. A tested Python wrapper opens two local IAP tunnels and runs Ansible through them. Focused Ansible roles prepare Ubuntu and containerd, protect the worker data disk, create the kubeadm cluster, and reconcile pinned cluster add-ons before a private Gateway API test.

**Tech Stack:** Terraform, Python 3 standard library, ansible-core 2.21.4, ansible-lint 26.9.0, yamllint 1.38.0, Ubuntu 24.04, containerd, Kubernetes 1.36.2, Calico 3.32.2, Helm 3.22.0, Gateway API 1.6.1, Traefik chart 41.6.0, Local Path Provisioner 0.0.36, GCP IAP, and OS Login.

**Spec:** `docs/decisions/0005-kubernetes-bootstrap-architecture.md`

## Global Constraints

- Keep both VMs private. Do not add public addresses, a bastion, or public ingress in this milestone.
- Run Ansible from the external operator machine through IAP and OS Login.
- Pin Ubuntu to an exact 24.04 LTS image self-link before the rebuild gate.
- Pin Kubernetes to 1.36.2, Calico to 3.32.2, Gateway API to 1.6.1 Standard, Traefik chart to 41.6.0, and Local Path Provisioner to 0.0.36.
- Use Calico VXLAN. Do not enable BGP or IP-in-IP.
- Configure containerd and kubelet with the systemd cgroup driver.
- Never reformat a nonblank disk, reset kubeadm automatically, log a join token, or track a generated inventory or kubeconfig.
- Keep Flux, Gitea, PostgreSQL, public DNS, TLS, backups, and recovery-region infrastructure out of Milestone 2.
- The operator runs Terraform, gcloud, Ansible against live nodes, reboots, replacements, deployments, and gate validation.
- Record implementation in `docs/worklogs/02-kubernetes-bootstrap.md`. Record validation results only when sanitized output is available.

## Review Focus

- Missing, malformed, or overlapping Terraform network outputs must stop inventory generation before a file is written; Task 2 tests each case.
- A busy local tunnel port, failed SSH host-key scan, or IAP process that exits before listening must stop the run and clean up every child process; Task 3 tests these cases.
- A missing disk, boot-disk alias, or unexpected existing filesystem must fail without running `mkfs`; Task 5 tests the guards as an explicit role contract.
- Partial kubeadm state must never trigger an automatic reset or overwrite; Task 6 tests the state markers and token redaction contract.
- Unpinned add-on URLs, a public Service type, or a route that bypasses Gateway API must fail repository tests; Task 7 tests those manifest contracts.

---

## File Map

| Path | Responsibility |
| --- | --- |
| `ansible/requirements.txt` | Pin controller-side Python packages. |
| `ansible.cfg` | Keep inventory, retry files, output, and SSH behavior project-local. |
| `ansible/playbooks/group_vars/all.yml` | Hold cluster CIDRs and exact component versions. |
| `ansible/playbooks/bootstrap.yml` | Run roles in dependency order. |
| `ansible/playbooks/validate.yml` | Gather non-destructive cluster and add-on evidence. |
| `ansible/playbooks/deploy_test_app.yml` | Deploy and wait for the disposable Gateway API application. |
| `ansible/playbooks/cleanup_test_app.yml` | Remove the disposable application after validation. |
| `ansible/roles/node_prepare/` | Prepare Ubuntu kernel, swap, sysctl, packages, time, and firewall state. |
| `ansible/roles/container_runtime/` | Install and configure containerd. |
| `ansible/roles/kubernetes_packages/` | Install and hold kubelet, kubeadm, and kubectl. |
| `ansible/roles/worker_storage/` | Validate and mount the protected worker data disk. |
| `ansible/roles/control_plane/` | Render kubeadm configuration and initialize once. |
| `ansible/roles/worker_join/` | Generate a protected token and join an unjoined worker. |
| `ansible/roles/cluster_addons/` | Reconcile CNI, Gateway API, ingress, and storage provisioner. |
| `ansible/manifests/milestone2-app.yml` | Define the private disposable application and route. |
| `scripts/prepare_ansible_inventory.py` | Convert selected Terraform outputs into an ignored JSON inventory. |
| `scripts/run_with_iap.py` | Own IAP tunnel lifecycle and invoke an Ansible command. |
| `tests/` | Test dependency pins, inventory validation, tunnel cleanup, role safety contracts, and manifests. |
| `docs/runbooks/kubernetes-bootstrap.md` | Give the operator commands, expected results, and evidence requests. |

### Task 1: Add the local Ansible toolchain and test harness

**Files:**
- Create: `ansible/requirements.txt`
- Create: `ansible.cfg`
- Create: `ansible/.yamllint.yml`
- Create: `ansible/playbooks/bootstrap.yml`
- Create: `tests/test_dependency_pins.py`
- Create: `.github/workflows/ansible.yml`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: Python 3.10 or newer on the operator and GitHub Actions runner.
- Produces: `.venv/bin/ansible-playbook`, `.venv/bin/ansible-lint`, `.venv/bin/yamllint`, and the test command `python3 -m unittest discover -s tests -v` used by later tasks.

- [ ] **Step 1: Write the failing dependency-pin test**

```python
from pathlib import Path
import unittest


class DependencyPinTests(unittest.TestCase):
    def test_controller_dependencies_are_exactly_pinned(self):
        requirements = Path("ansible/requirements.txt").read_text().splitlines()
        self.assertEqual(
            requirements,
            [
                "ansible-core==2.21.4",
                "ansible-lint==26.9.0",
                "yamllint==1.38.0",
            ],
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and confirm the missing file failure**

Run: `python3 -m unittest tests.test_dependency_pins -v`

Expected: `FileNotFoundError` for `ansible/requirements.txt`.

- [ ] **Step 3: Add the exact dependency pins and local configuration**

Create `ansible/requirements.txt` with the three exact lines asserted above. Configure root `ansible.cfg` as follows so commands run from the repository root load it automatically:

```ini
[defaults]
inventory = ansible/inventory/generated/hosts.json
roles_path = ansible/roles
retry_files_enabled = False
interpreter_python = auto_silent
stdout_callback = default
host_key_checking = True

[ssh_connection]
pipelining = True
```

Create `ansible/.yamllint.yml`:

```yaml
---
extends: default
rules:
  line-length:
    max: 100
  truthy:
    allowed-values: ["true", "false"]
```

Create a minimal syntax-valid bootstrap playbook that later tasks expand:

```yaml
---
- name: Bootstrap Kubernetes nodes
  hosts: kube_cluster
  gather_facts: true
  become: true
  tasks:
    - name: Confirm supported operating system
      ansible.builtin.assert:
        that:
          - ansible_distribution == "Ubuntu"
          - ansible_distribution_version is version("24.04", ">=")
```

Append these ignored controller artifacts to `.gitignore`:

```gitignore
.venv/
ansible/.cache/
ansible/inventory/generated/
```

- [ ] **Step 4: Add the Ansible CI workflow**

Create `.github/workflows/ansible.yml` with this initial local-only job. Task 8 extends it after both playbooks and the Terraform output changes exist.

```yaml
name: Ansible

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065
        with:
          python-version: "3.13"
          cache: pip
          cache-dependency-path: ansible/requirements.txt
      - run: python -m pip install -r ansible/requirements.txt
      - run: python -m unittest discover -s tests -v
      - run: yamllint ansible .github/workflows/ansible.yml
      - run: ansible-lint ansible
      - run: ansible-playbook -i 'localhost,' --syntax-check ansible/playbooks/bootstrap.yml
```

- [ ] **Step 5: Install the local toolchain and run the green checks**

Run:

```bash
python3 -m venv .venv
.venv/bin/pip install -r ansible/requirements.txt
python3 -m unittest tests.test_dependency_pins -v
.venv/bin/yamllint ansible .github/workflows/ansible.yml
.venv/bin/ansible-lint ansible
```

Expected: one unit test passes, yamllint reports no violations, and ansible-lint reports no fatal violations.

- [ ] **Step 6: Commit the toolchain**

```bash
git add .gitignore .github/workflows/ansible.yml ansible.cfg ansible tests/test_dependency_pins.py
git commit -m "build: add pinned Ansible toolchain"
```

### Task 2: Generate a validated private inventory from Terraform outputs

**Files:**
- Modify: `infra/primary/outputs.tf`
- Create: `scripts/prepare_ansible_inventory.py`
- Create: `tests/fixtures/terraform-outputs.json`
- Create: `tests/test_prepare_ansible_inventory.py`
- Modify: `tests/test_dependency_pins.py`

**Interfaces:**
- Consumes: `terraform -chdir=infra/primary output -json`, an OS Login username, and an existing SSH private-key path.
- Produces: `build_inventory(outputs: dict, ssh_user: str, ssh_key: str) -> dict` and a mode-0600 `ansible/inventory/generated/hosts.json` with groups `kube_control_plane`, `kube_workers`, and `kube_cluster`.

- [ ] **Step 1: Write failing inventory unit tests**

Test these exact cases with `unittest` and `tempfile.TemporaryDirectory`:

```python
def test_build_inventory_maps_roles_and_private_metadata(self):
    inventory = module.build_inventory(self.outputs, "user_example", "/tmp/key")
    control_plane = inventory["all"]["children"]["kube_control_plane"]["hosts"]["control-plane"]
    self.assertEqual(control_plane["ansible_host"], "127.0.0.1")
    self.assertEqual(control_plane["ansible_port"], 2201)
    self.assertEqual(control_plane["node_internal_ip"], "10.10.0.2")
    self.assertEqual(control_plane["gcp_instance_name"], "example-control-plane")

def test_rejects_missing_output(self):
    del self.outputs["primary_zone"]
    with self.assertRaisesRegex(ValueError, "primary_zone"):
        module.build_inventory(self.outputs, "user_example", "/tmp/key")

def test_rejects_overlapping_pod_and_vpc_networks(self):
    self.outputs["primary_subnet_cidr"]["value"] = "192.168.1.0/24"
    with self.assertRaisesRegex(ValueError, "overlap"):
        module.build_inventory(self.outputs, "user_example", "/tmp/key")

def test_write_inventory_uses_private_permissions(self):
    module.write_inventory(self.output_path, module.build_inventory(self.outputs, "user_example", "/tmp/key"))
    self.assertEqual(self.output_path.stat().st_mode & 0o777, 0o600)
```

The fixture must use documentation-only values: project `example-project`, zone `europe-north1-a`, subnet `10.10.0.0/24`, control-plane address `10.10.0.2`, and worker address `10.10.0.3`.

- [ ] **Step 2: Run the inventory tests and confirm import failure**

Run: `python3 -m unittest tests.test_prepare_ansible_inventory -v`

Expected: FAIL because `scripts/prepare_ansible_inventory.py` does not exist.

- [ ] **Step 3: Expose only the additional Terraform metadata**

Add nonsensitive outputs for `project_id`, `primary_zone`, and `primary_subnet_cidr` to `infra/primary/outputs.tf`. Keep the existing instance-name and internal-address maps. Do not output credentials, backend values, IAM tokens, or local file paths.

- [ ] **Step 4: Implement the inventory builder with standard-library code**

Implement:

```python
POD_CIDR = ipaddress.ip_network("192.168.0.0/16")
SERVICE_CIDR = ipaddress.ip_network("10.96.0.0/12")
LOCAL_PORTS = {"control-plane": 2201, "worker": 2202}


def build_inventory(outputs: dict, ssh_user: str, ssh_key: str) -> dict:
    required = (
        "project_id",
        "primary_zone",
        "primary_subnet_cidr",
        "instance_names",
        "internal_ips",
    )
    missing = [name for name in required if name not in outputs]
    if missing:
        raise ValueError(f"missing Terraform outputs: {', '.join(missing)}")
    vpc = ipaddress.ip_network(outputs["primary_subnet_cidr"]["value"])
    for left, right in ((vpc, POD_CIDR), (vpc, SERVICE_CIDR), (POD_CIDR, SERVICE_CIDR)):
        if left.overlaps(right):
            raise ValueError(f"cluster and VPC networks overlap: {left} and {right}")
    # Return the documented all/children inventory structure with localhost tunnel ports.
```

The CLI runs `terraform -chdir=infra/primary output -json` by default, while honoring its explicit `--terraform-dir` argument. Use `subprocess.run(..., check=True, capture_output=True, text=True)`, check that the SSH key is a regular file, create the parent directory with mode 0700, write through a temporary file, set mode 0600, and atomically replace the destination. Error messages may name missing output keys but must not dump Terraform JSON.

- [ ] **Step 5: Expand tests for malformed CIDRs, missing roles, and unsafe keys**

Add tests that reject a non-CIDR subnet, a missing worker name or address, equal control-plane and worker addresses, an empty OS Login username, and a missing SSH key in the CLI path. Assert that failure leaves no output file.

- [ ] **Step 6: Run the inventory and Terraform checks**

Run:

```bash
python3 -m unittest tests.test_prepare_ansible_inventory -v
terraform fmt -check -recursive
terraform -chdir=infra/primary validate -no-color
```

Expected: all inventory tests pass, formatting exits 0, and Terraform reports `Success! The configuration is valid.`

- [ ] **Step 7: Commit the inventory boundary**

```bash
git add infra/primary/outputs.tf scripts/prepare_ansible_inventory.py tests
git commit -m "feat: generate private Ansible inventory"
```

### Task 3: Run Ansible through owned IAP tunnels

**Files:**
- Create: `scripts/run_with_iap.py`
- Create: `tests/test_run_with_iap.py`

**Interfaces:**
- Consumes: the generated inventory contract from Task 2 and an Ansible command after `--`.
- Produces: `load_targets(path: Path) -> list[Target]`, `ensure_ports_available(targets)`, `wait_for_tunnel(process, port, timeout)`, `write_known_hosts(targets, path)`, and `run_with_tunnels(targets, command) -> int`.

- [ ] **Step 1: Write failing tunnel lifecycle tests**

Use `unittest.mock` to cover:

```python
def test_busy_port_stops_before_starting_gcloud(self):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 2201))
        listener.listen()
        with self.assertRaisesRegex(RuntimeError, "2201.*already in use"):
            module.ensure_ports_available(self.targets)

def test_early_tunnel_exit_raises(self):
    process = Mock()
    process.poll.return_value = 1
    process.stderr.read.return_value = "permission denied"
    with self.assertRaisesRegex(RuntimeError, "IAP tunnel exited"):
        module.wait_for_tunnel(process, 2201, timeout=0.1)

def test_ansible_failure_terminates_every_tunnel(self):
    result = module.run_with_tunnels(self.targets, ["false"])
    self.assertEqual(result, 1)
    for process in self.started_processes:
        process.terminate.assert_called_once()

def test_host_key_scan_failure_terminates_every_tunnel(self):
    self.ssh_keyscan.return_value.returncode = 1
    with self.assertRaisesRegex(RuntimeError, "host key"):
        module.run_with_tunnels(self.targets, ["true"])
    for process in self.started_processes:
        process.terminate.assert_called_once()
```

Also test duplicate ports, missing target metadata, `KeyboardInterrupt`, and escalation from terminate to kill when a child does not exit.

- [ ] **Step 2: Run the tests and confirm import failure**

Run: `python3 -m unittest tests.test_run_with_iap -v`

Expected: FAIL because `scripts/run_with_iap.py` does not exist.

- [ ] **Step 3: Implement explicit gcloud process arguments**

Represent each target with a frozen dataclass:

```python
@dataclass(frozen=True)
class Target:
    role: str
    instance: str
    project: str
    zone: str
    local_port: int
```

Start each tunnel without a shell:

```python
args = [
    "gcloud",
    "compute",
    "start-iap-tunnel",
    target.instance,
    "22",
    f"--local-host-port=127.0.0.1:{target.local_port}",
    f"--project={target.project}",
    f"--zone={target.zone}",
    "--quiet",
]
subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
```

Poll the localhost socket and child status for at most 30 seconds. After both authenticated IAP tunnels listen, run `ssh-keyscan` for each localhost port, write the combined keys to `known_hosts` beside the generated inventory with mode 0600, and pass `StrictHostKeyChecking=yes` plus that file through `ANSIBLE_SSH_COMMON_ARGS`. This verifies the key used by Ansible over the same IAP endpoints without trusting a stale key after intentional VM replacement. In one `finally` block, terminate all children, wait up to five seconds, then kill remaining children. Return the Ansible command's exit code. Never print the inventory body or environment.

- [ ] **Step 4: Add CLI validation**

Require a call shaped like `--inventory ansible/inventory/generated/hosts.json -- .venv/bin/ansible-playbook -i ansible/inventory/generated/hosts.json ansible/playbooks/bootstrap.yml`. Reject an empty command, a non-JSON inventory, hosts other than the two required roles, non-loopback `ansible_host`, and ports outside 1024 through 65535.

- [ ] **Step 5: Run tunnel tests and static checks**

Run:

```bash
python3 -m unittest tests.test_run_with_iap -v
python3 -m unittest discover -s tests -v
.venv/bin/ansible-lint ansible
```

Expected: all tests pass and ansible-lint reports no fatal violations.

- [ ] **Step 6: Commit the IAP runner**

```bash
git add scripts/run_with_iap.py tests/test_run_with_iap.py
git commit -m "feat: run Ansible through managed IAP tunnels"
```

### Task 4: Prepare Ubuntu, containerd, and Kubernetes packages

**Files:**
- Create: `ansible/playbooks/group_vars/all.yml`
- Create: `ansible/roles/node_prepare/tasks/main.yml`
- Create: `ansible/roles/node_prepare/handlers/main.yml`
- Create: `ansible/roles/container_runtime/tasks/main.yml`
- Create: `ansible/roles/container_runtime/handlers/main.yml`
- Create: `ansible/roles/container_runtime/templates/config.toml.j2`
- Create: `ansible/roles/kubernetes_packages/tasks/main.yml`
- Modify: `ansible/playbooks/bootstrap.yml`
- Create: `tests/test_ansible_contract.py`

**Interfaces:**
- Consumes: inventory groups from Task 2 and root access through Task 3.
- Produces: healthy containerd and kubelet services on both nodes, with Kubernetes packages held at 1.36.2.

- [ ] **Step 1: Write failing host-role contract tests**

Parse YAML with `yaml.safe_load` and inspect the containerd template as text. Assert:

```python
def test_component_versions_are_exact(self):
    values = yaml.safe_load(Path("ansible/playbooks/group_vars/all.yml").read_text())
    self.assertEqual(values["kubernetes_version"], "1.36.2")
    self.assertEqual(values["kubernetes_deb_version"], "1.36.2-2.1")
    self.assertEqual(values["containerd_deb_version"], "2.2.1-0ubuntu1~24.04.3")
    self.assertEqual(values["helm_version"], "3.22.0")

def test_containerd_uses_systemd_cgroups_and_cri(self):
    config = Path("ansible/roles/container_runtime/templates/config.toml.j2").read_text()
    self.assertIn("SystemdCgroup = true", config)
    self.assertNotIn('disabled_plugins = ["cri"]', config)

def test_node_role_does_not_use_fixed_sleeps(self):
    role_text = Path("ansible/roles/node_prepare/tasks/main.yml").read_text()
    self.assertNotIn("pause:", role_text)
    self.assertNotIn("sleep ", role_text)
```

- [ ] **Step 2: Run the contract tests and confirm missing-file failures**

Run: `python3 -m unittest tests.test_ansible_contract -v`

Expected: FAIL for missing group variables and roles.

- [ ] **Step 3: Add exact cluster and package variables**

Use:

```yaml
---
kubernetes_version: "1.36.2"
kubernetes_minor: "v1.36"
kubernetes_deb_version: "1.36.2-2.1"
containerd_deb_version: "2.2.1-0ubuntu1~24.04.3"
helm_version: "3.22.0"
calico_version: "3.32.2"
gateway_api_version: "1.6.1"
traefik_chart_version: "41.6.0"
local_path_provisioner_version: "0.0.36"
pod_cidr: "192.168.0.0/16"
service_cidr: "10.96.0.0/12"
worker_data_device: "/dev/disk/by-id/google-worker-data"
worker_data_mount: "/var/lib/k8s-dr"
local_path_root: "/var/lib/k8s-dr/local-path"
traefik_http_node_port: 30080
```

- [ ] **Step 4: Implement the node-preparation role**

Assert Ubuntu 24.04 and `x86_64`; install `ca-certificates`, `curl`, `gpg`, `chrony`, `conntrack`, and `socat`; disable swap now and in `/etc/fstab`; load `overlay` and `br_netfilter`; set bridge iptables and IPv4 forwarding sysctls; enable chrony; and disable UFW only when installed. Use handlers for service restarts.

- [ ] **Step 5: Implement the containerd role**

Install `containerd={{ containerd_deb_version }}`, then render a complete containerd version 2 configuration with CRI enabled and `SystemdCgroup = true`. Notify one handler that validates with `containerd config dump`, restarts containerd, and waits for `/run/containerd/containerd.sock`.

- [ ] **Step 6: Implement Kubernetes package installation**

Install the `pkgs.k8s.io/core:/stable:/v1.36/deb/` signing key and repository, install exact `kubelet`, `kubeadm`, and `kubectl` Debian versions, mark them held, enable kubelet, and allow its expected pre-kubeadm restart loop without masking other failures.

- [ ] **Step 7: Expand the bootstrap playbook in dependency order**

Apply `node_prepare`, `container_runtime`, and `kubernetes_packages` to `kube_cluster`. Keep `serial` unset so both nodes can receive common configuration, but keep later cluster-creation plays separate.

- [ ] **Step 8: Run local role checks**

Run:

```bash
python3 -m unittest tests.test_ansible_contract -v
.venv/bin/yamllint ansible
.venv/bin/ansible-lint ansible
.venv/bin/ansible-playbook --syntax-check ansible/playbooks/bootstrap.yml
```

Expected: contract tests pass and all three Ansible checks exit 0.

- [ ] **Step 9: Commit host preparation**

```bash
git add ansible tests/test_ansible_contract.py
git commit -m "feat: prepare Kubernetes node software"
```

### Task 5: Protect and mount the dedicated worker disk

**Files:**
- Create: `ansible/roles/worker_storage/tasks/main.yml`
- Modify: `ansible/playbooks/bootstrap.yml`
- Modify: `tests/test_ansible_contract.py`

**Interfaces:**
- Consumes: `worker_data_device`, `worker_data_mount`, and `local_path_root` from Task 4.
- Produces: an ext4 mount at `/var/lib/k8s-dr` and a verified `/var/lib/k8s-dr/local-path` directory on the worker only.

- [ ] **Step 1: Add failing storage safety tests**

Assert the role contains all these contracts:

```python
def test_storage_role_guards_destructive_operations(self):
    text = Path("ansible/roles/worker_storage/tasks/main.yml").read_text()
    for required in (
        "readlink -f {{ worker_data_device }}",
        "findmnt -n -o SOURCE /",
        "blkid -o value -s TYPE",
        "unexpected filesystem",
        "mkfs.ext4",
        "mountpoint -q {{ worker_data_mount }}",
    ):
        self.assertIn(required, text)
    self.assertNotIn("wipefs", text)
    self.assertNotIn("force: true", text)
```

Also assert the role is applied only to `kube_workers` and runs before `worker_join`.

- [ ] **Step 2: Run the test and confirm the role is missing**

Run: `python3 -m unittest tests.test_ansible_contract.AnsibleContractTests.test_storage_role_guards_destructive_operations -v`

Expected: FAIL because the storage role does not exist.

- [ ] **Step 3: Implement device and filesystem guards**

Use `ansible.builtin.stat` for the stable device link, resolve the target and root filesystem source with `readlink` and `findmnt`, and fail when the device is missing or resolves to the boot device. Run `blkid` with `failed_when: false`; permit only an empty result or `ext4`.

- [ ] **Step 4: Format only the blank device and mount it**

Run `mkfs.ext4 -F -L k8sdr-data {{ resolved_worker_data_device }}` only when `blkid` returns no filesystem signature. Add one `/etc/fstab` entry using the filesystem UUID, create the mount point, mount it when `mountpoint -q` fails, verify `findmnt --target {{ worker_data_mount }}` resolves to the expected device, then create `local_path_root`.

- [ ] **Step 5: Run contract, lint, and syntax checks**

Run:

```bash
python3 -m unittest tests.test_ansible_contract -v
.venv/bin/yamllint ansible
.venv/bin/ansible-lint ansible
.venv/bin/ansible-playbook --syntax-check ansible/playbooks/bootstrap.yml
```

Expected: all checks pass. No command touches a live VM during these checks.

- [ ] **Step 6: Commit storage safety**

```bash
git add ansible/roles/worker_storage ansible/playbooks/bootstrap.yml tests/test_ansible_contract.py
git commit -m "feat: mount protected worker storage"
```

### Task 6: Initialize kubeadm and join the worker safely

**Files:**
- Create: `ansible/roles/control_plane/tasks/main.yml`
- Create: `ansible/roles/control_plane/templates/kubeadm-init.yml.j2`
- Create: `ansible/roles/worker_join/tasks/main.yml`
- Modify: `ansible/playbooks/bootstrap.yml`
- Modify: `tests/test_ansible_contract.py`

**Interfaces:**
- Consumes: prepared nodes from Task 4, mounted storage from Task 5, inventory host variables `node_internal_ip` and `gcp_instance_name`, and the CIDRs from `group_vars`.
- Produces: `/etc/kubernetes/admin.conf` on the control plane, `/etc/kubernetes/kubelet.conf` on the worker, and a two-node Kubernetes API ready for add-ons.

- [ ] **Step 1: Add failing kubeadm lifecycle tests**

```python
def test_kubeadm_lifecycle_is_guarded_and_tokens_are_redacted(self):
    control = Path("ansible/roles/control_plane/tasks/main.yml").read_text()
    worker = Path("ansible/roles/worker_join/tasks/main.yml").read_text()
    self.assertIn("/etc/kubernetes/admin.conf", control)
    self.assertIn("kubeadm init --config", control)
    self.assertIn("/etc/kubernetes/kubelet.conf", worker)
    self.assertIn("kubeadm token create --ttl 15m --print-join-command", worker)
    self.assertGreaterEqual(worker.count("no_log: true"), 2)
    self.assertNotIn("kubeadm reset", control + worker)

def test_kubeadm_template_uses_private_network_and_systemd(self):
    text = Path("ansible/roles/control_plane/templates/kubeadm-init.yml.j2").read_text()
    self.assertIn("apiVersion: kubeadm.k8s.io/v1beta4", text)
    self.assertIn("advertiseAddress: {{ node_internal_ip }}", text)
    self.assertIn("podSubnet: {{ pod_cidr }}", text)
    self.assertIn("serviceSubnet: {{ service_cidr }}", text)
    self.assertIn("cgroupDriver: systemd", text)
```

- [ ] **Step 2: Run lifecycle tests and confirm missing-file failures**

Run: `python3 -m unittest tests.test_ansible_contract -v`

Expected: FAIL for the missing control-plane and worker roles.

- [ ] **Step 3: Render and validate kubeadm configuration**

The template must contain `InitConfiguration`, `ClusterConfiguration`, and `KubeletConfiguration` documents. Set the CRI socket to containerd, advertise and node addresses to the private control-plane address, the control-plane endpoint to that address on 6443, exact Kubernetes version `v1.36.2`, the configured pod and service subnets, and systemd cgroups. Validate it with `kubeadm config validate --config` before init.

- [ ] **Step 4: Initialize only an uninitialized control plane**

Check `/etc/kubernetes/admin.conf`. When absent, pre-pull images and run `kubeadm init --config /etc/kubernetes/kubeadm-init.yml`. Set mode 0600 on admin.conf. Wait for `kubectl --kubeconfig /etc/kubernetes/admin.conf get --raw=/readyz` to return success. If admin.conf exists but readyz fails, stop with diagnostic commands and do not reset.

- [ ] **Step 5: Generate and consume a protected join command**

On the control plane, run `kubeadm token create --ttl 15m --print-join-command` only when the worker lacks `/etc/kubernetes/kubelet.conf`. Store it in a host fact with `no_log: true`. On the worker, append `--cri-socket unix:///run/containerd/containerd.sock --node-name {{ gcp_instance_name }}` and execute with `no_log: true`. Wait until the API lists the worker. Do not require `Ready` before Calico is installed.

- [ ] **Step 6: Run contract, lint, and syntax checks**

Run:

```bash
python3 -m unittest tests.test_ansible_contract -v
.venv/bin/yamllint ansible
.venv/bin/ansible-lint ansible
.venv/bin/ansible-playbook --syntax-check ansible/playbooks/bootstrap.yml
```

Expected: all checks pass and no token value appears in test output.

- [ ] **Step 7: Commit cluster creation**

```bash
git add ansible/roles/control_plane ansible/roles/worker_join ansible/playbooks/bootstrap.yml tests/test_ansible_contract.py
git commit -m "feat: bootstrap the kubeadm cluster"
```

### Task 7: Reconcile pinned add-ons and the disposable application

**Files:**
- Create: `ansible/roles/cluster_addons/tasks/main.yml`
- Create: `ansible/roles/cluster_addons/templates/calico-custom-resources.yml.j2`
- Create: `ansible/roles/cluster_addons/templates/traefik-values.yml.j2`
- Create: `ansible/roles/cluster_addons/templates/local-path-config.yml.j2`
- Create: `ansible/manifests/milestone2-app.yml`
- Create: `ansible/playbooks/deploy_test_app.yml`
- Create: `ansible/playbooks/cleanup_test_app.yml`
- Create: `ansible/playbooks/validate.yml`
- Modify: `ansible/playbooks/bootstrap.yml`
- Create: `tests/test_cluster_manifests.py`

**Interfaces:**
- Consumes: the control-plane kubeconfig from Task 6, the worker storage root from Task 5, and exact add-on versions from Task 4.
- Produces: healthy Calico, Gateway API CRDs, Traefik on fixed NodePort 30080, Local Path Provisioner backed only by the worker disk, and a disposable route for hostname `milestone2.local`.

- [ ] **Step 1: Write failing pinned-manifest tests**

```python
def test_addon_tasks_use_only_pinned_release_urls(self):
    text = Path("ansible/roles/cluster_addons/tasks/main.yml").read_text()
    self.assertIn("projectcalico/calico/v{{ calico_version }}", text)
    self.assertIn("gateway-api/releases/download/v{{ gateway_api_version }}", text)
    self.assertIn("--version {{ traefik_chart_version }}", text)
    self.assertIn("local-path-provisioner/v{{ local_path_provisioner_version }}", text)
    self.assertNotIn("/latest/", text)
    self.assertNotIn("/master/", text)

def test_traefik_is_private_nodeport_gateway(self):
    values = Path("ansible/roles/cluster_addons/templates/traefik-values.yml.j2").read_text()
    self.assertIn("type: NodePort", values)
    self.assertIn("nodePort: {{ traefik_http_node_port }}", values)
    self.assertIn("kubernetesGateway", values)
    self.assertNotIn("LoadBalancer", values)

def test_disposable_app_uses_worker_pvc_and_http_route(self):
    text = Path("ansible/manifests/milestone2-app.yml").read_text()
    documents = list(yaml.safe_load_all(text))
    kinds = {document["kind"] for document in documents}
    self.assertTrue({"PersistentVolumeClaim", "Deployment", "Service", "Gateway", "HTTPRoute"} <= kinds)
    self.assertIn("nginx:1.28.0-alpine3.21", text)
    self.assertIn("busybox:1.37.0", text)
    self.assertNotIn(":latest", text)
```

- [ ] **Step 2: Run manifest tests and confirm missing-file failures**

Run: `python3 -m unittest tests.test_cluster_manifests -v`

Expected: FAIL because the add-on role and manifests do not exist.

- [ ] **Step 3: Install Calico with VXLAN**

Apply the pinned 3.32.2 operator CRDs and operator manifests, then a tracked `Installation` custom resource with Kubernetes datastore, Calico IPAM, pod CIDR, `VXLAN` encapsulation, and BGP disabled. Wait for the Tigera operator, Calico node DaemonSet, and CoreDNS to become available. Do not use fixed sleeps.

- [ ] **Step 4: Install Gateway API and Traefik**

Apply the pinned 1.6.1 Standard CRDs. Install Helm 3.22.0 on the control plane from the versioned release archive and verify it with the published checksum file. Run `helm upgrade --install` for Traefik chart 41.6.0. Enable the Kubernetes Gateway provider, select the worker, set Service type NodePort and HTTP nodePort 30080, and disable dashboard exposure. Wait for the Deployment and GatewayClass acceptance.

- [ ] **Step 5: Install Local Path Provisioner safely**

Apply the pinned 0.0.36 deployment and a tracked ConfigMap whose `nodePathMap` contains the worker node name and `/var/lib/k8s-dr/local-path`, with `DEFAULT_PATH_FOR_NON_LISTED_NODES` mapped to an empty path list. Mark `local-path` as the default StorageClass and wait for the provisioner Deployment.

- [ ] **Step 6: Add the disposable application and validation playbook**

The manifest must create namespace `milestone2-test`, a PVC using `local-path`, a BusyBox 1.37.0 init container that writes a stable marker into the mounted volume, an nginx 1.28.0 Alpine Deployment pinned to the worker, a ClusterIP Service, a Gateway, and an HTTPRoute for `milestone2.local`. `deploy_test_app.yml` copies the manifest to the control plane, applies it, and waits for the Deployment, PVC, Gateway, and HTTPRoute conditions. `cleanup_test_app.yml` deletes the test namespace and copied manifest. The validation playbook runs read-only kubectl commands for nodes, system pods, deployments, Gateway conditions, route conditions, PVC, PV, and pod placement. It must not change resources.

- [ ] **Step 7: Run manifest, lint, and syntax checks**

Run:

```bash
python3 -m unittest tests.test_cluster_manifests -v
python3 -m unittest discover -s tests -v
.venv/bin/yamllint ansible
.venv/bin/ansible-lint ansible
.venv/bin/ansible-playbook --syntax-check ansible/playbooks/bootstrap.yml
.venv/bin/ansible-playbook --syntax-check ansible/playbooks/deploy_test_app.yml
.venv/bin/ansible-playbook --syntax-check ansible/playbooks/cleanup_test_app.yml
.venv/bin/ansible-playbook --syntax-check ansible/playbooks/validate.yml
```

Expected: all tests and static checks pass.

- [ ] **Step 8: Commit cluster add-ons**

```bash
git add ansible tests/test_cluster_manifests.py tests/test_ansible_contract.py
git commit -m "feat: install bootstrap cluster add-ons"
```

### Task 8: Write the operator procedure and prepare the milestone handoff

**Files:**
- Create: `docs/runbooks/kubernetes-bootstrap.md`
- Modify: `docs/README.md`
- Modify: `docs/worklogs/02-kubernetes-bootstrap.md`
- Modify: `plan.md`
- Modify: `.github/workflows/ansible.yml`

**Interfaces:**
- Consumes: all tracked automation and checks from Tasks 1 through 7.
- Produces: ordered operator commands, expected results, sanitized evidence requests, CI coverage, and an explicitly open Milestone 2 gate.

- [ ] **Step 1: Extend CI to run the complete local verification set**

Extend the Task 1 workflow with the following steps. Terraform validation runs after `terraform init -backend=false`, so CI downloads the locked provider but never reads remote state.

```yaml
      - run: ansible-playbook -i 'localhost,' --syntax-check ansible/playbooks/deploy_test_app.yml
      - run: ansible-playbook -i 'localhost,' --syntax-check ansible/playbooks/cleanup_test_app.yml
      - run: ansible-playbook -i 'localhost,' --syntax-check ansible/playbooks/validate.yml
      - uses: hashicorp/setup-terraform@dfe3c3f87815947d99a8997f908cb6525fc44e9e
        with:
          terraform_version: "1.15.8"
          terraform_wrapper: false
      - run: terraform fmt -check -recursive
      - run: terraform -chdir=infra/bootstrap init -backend=false -input=false
      - run: terraform -chdir=infra/bootstrap validate -no-color
      - run: terraform -chdir=infra/primary init -backend=false -input=false
      - run: terraform -chdir=infra/primary validate -no-color
```

- [ ] **Step 2: Write the operator prerequisites and inventory procedure**

Document these commands in order:

```bash
python3 -m venv .venv
.venv/bin/pip install -r ansible/requirements.txt
cd infra/primary
terraform plan -detailed-exitcode
export PROJECT_ID="$(terraform output -raw project_id)"
export PRIMARY_ZONE="$(terraform output -raw primary_zone)"
export CONTROL_PLANE_NAME="$(terraform output -json instance_names | python3 -c 'import json,sys; print(json.load(sys.stdin)["control-plane"])')"
export WORKER_NAME="$(terraform output -raw worker_name)"
export OS_LOGIN_USER="$(gcloud compute os-login describe-profile --format='value(posixAccounts[0].username)')"
gcloud compute ssh "$CONTROL_PLANE_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command=true
gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command=true
cd ../..
python3 scripts/prepare_ansible_inventory.py --terraform-dir infra/primary --ssh-user "$OS_LOGIN_USER" --ssh-key "$HOME/.ssh/google_compute_engine" --output ansible/inventory/generated/hosts.json
```

State the expected result after each group and warn that the generated inventory contains real identifiers and remains ignored.

- [ ] **Step 3: Document exact boot-image pinning before rebuild**

Use read-only instance and disk queries to obtain the `sourceImage` for both boot disks:

```bash
export CONTROL_PLANE_DISK="$(gcloud compute instances describe "$CONTROL_PLANE_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --format='value(disks[0].source.basename())')"
export WORKER_DISK="$(gcloud compute instances describe "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --format='value(disks[0].source.basename())')"
export CONTROL_PLANE_IMAGE="$(gcloud compute disks describe "$CONTROL_PLANE_DISK" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --format='value(sourceImage)')"
export WORKER_IMAGE="$(gcloud compute disks describe "$WORKER_DISK" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --format='value(sourceImage)')"
test "$CONTROL_PLANE_IMAGE" = "$WORKER_IMAGE"
```

Instruct the operator to add the exact matching self-link as `boot_image` in the ignored `infra/primary/terraform.tfvars`. A subsequent Terraform plan must show no replacement caused solely by the pin.

- [ ] **Step 4: Document bootstrap, rerun, and private application validation**

Use the IAP wrapper for bootstrap and validation:

```bash
python3 scripts/run_with_iap.py --inventory ansible/inventory/generated/hosts.json -- .venv/bin/ansible-playbook -i ansible/inventory/generated/hosts.json ansible/playbooks/bootstrap.yml
python3 scripts/run_with_iap.py --inventory ansible/inventory/generated/hosts.json -- .venv/bin/ansible-playbook -i ansible/inventory/generated/hosts.json ansible/playbooks/bootstrap.yml
python3 scripts/run_with_iap.py --inventory ansible/inventory/generated/hosts.json -- .venv/bin/ansible-playbook -i ansible/inventory/generated/hosts.json ansible/playbooks/deploy_test_app.yml
python3 scripts/run_with_iap.py --inventory ansible/inventory/generated/hosts.json -- .venv/bin/ansible-playbook -i ansible/inventory/generated/hosts.json ansible/playbooks/validate.yml
```

Then open an IAP-backed SSH local forward from localhost 18080 to worker localhost 30080 in one terminal:

```bash
gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap -- -N -L 18080:127.0.0.1:30080
```

From a second terminal, run:

```bash
curl --fail --header 'Host: milestone2.local' http://127.0.0.1:18080/
```

Expected: the stable marker from the mounted persistent volume. Repeat after deleting the pod and after the worker reboot. After all checks, run:

```bash
python3 scripts/run_with_iap.py --inventory ansible/inventory/generated/hosts.json -- .venv/bin/ansible-playbook -i ansible/inventory/generated/hosts.json ansible/playbooks/cleanup_test_app.yml
```

Expected: namespace `milestone2-test` no longer exists.

- [ ] **Step 5: Document worker restart and clean boot-disk replacement**

The operator reboots the worker through IAP, waits for SSH, runs the validation playbook, and repeats the HTTP and PVC checks:

```bash
gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command='sudo systemctl reboot'
gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command=true
python3 scripts/run_with_iap.py --inventory ansible/inventory/generated/hosts.json -- .venv/bin/ansible-playbook -i ansible/inventory/generated/hosts.json ansible/playbooks/validate.yml
```

For the rebuild, document a saved Terraform plan with `-replace` for exactly the control-plane and worker VM resources:

```bash
cd infra/primary
terraform plan -out=milestone2-rebuild.tfplan -replace='module.primary_cluster.google_compute_instance.node["control-plane"]' -replace='module.primary_cluster.google_compute_instance.node["worker"]'
terraform show milestone2-rebuild.tfplan
terraform apply milestone2-rebuild.tfplan
cd ../..
```

Require inspection before apply that the worker data disk, VPC, buckets, and state are not destroyed. The operator applies only that reviewed plan, regenerates inventory if addresses changed, reruns bootstrap twice, and repeats the gate checks.

- [ ] **Step 6: Update project status without closing the gate**

Set Milestone 2 status to `In progress` in `plan.md`; leave every checkbox open. In the worklog, list implemented files and state that no live validation is recorded. Add the new runbook and implementation plan to `docs/README.md`.

- [ ] **Step 7: Run the full local verification suite**

Run:

```bash
python3 -m unittest discover -s tests -v
.venv/bin/yamllint ansible .github/workflows/ansible.yml
.venv/bin/ansible-lint ansible
.venv/bin/ansible-playbook --syntax-check ansible/playbooks/bootstrap.yml
.venv/bin/ansible-playbook --syntax-check ansible/playbooks/deploy_test_app.yml
.venv/bin/ansible-playbook --syntax-check ansible/playbooks/cleanup_test_app.yml
.venv/bin/ansible-playbook --syntax-check ansible/playbooks/validate.yml
terraform fmt -check -recursive
terraform -chdir=infra/bootstrap validate -no-color
terraform -chdir=infra/primary validate -no-color
git diff --check
```

Expected: all unit and static checks pass; both Terraform roots report valid; `git diff --check` exits 0. These are local implementation checks, not the Milestone 2 gate.

- [ ] **Step 8: Commit the operator handoff**

```bash
git add .github/workflows/ansible.yml docs plan.md
git commit -m "docs: add Kubernetes bootstrap procedure"
```

- [ ] **Step 9: Request operator execution and evidence**

Hand off the runbook commands in order. Request only sanitized command, exit code, expected versus observed result, and relevant excerpts. Do not request kubeconfigs, join tokens, Terraform state, plan files, real project IDs, private addresses, SSH keys, bucket names, or complete logs. Keep the gate open until all four conditions have observed evidence.
