from pathlib import Path
import unittest

import yaml


class AnsibleContractTests(unittest.TestCase):
    def test_component_versions_are_exact(self):
        values = yaml.safe_load(Path("ansible/playbooks/group_vars/all.yml").read_text())
        self.assertEqual(values["kubernetes_version"], "1.36.2")
        self.assertEqual(values["kubernetes_deb_version"], "1.36.2-2.1")
        self.assertEqual(values["containerd_deb_version"], "2.2.1-0ubuntu1~24.04.3")
        self.assertEqual(values["helm_version"], "4.3.0")

    def test_network_and_storage_values_are_exact(self):
        values = yaml.safe_load(Path("ansible/playbooks/group_vars/all.yml").read_text())
        self.assertEqual(values["pod_cidr"], "192.168.0.0/16")
        self.assertEqual(values["service_cidr"], "10.96.0.0/12")
        self.assertEqual(values["worker_data_device"], "/dev/disk/by-id/google-worker-data")
        self.assertEqual(values["worker_data_mount"], "/var/lib/k8s-dr")

    def test_containerd_uses_systemd_cgroups_and_cri(self):
        config = Path("ansible/roles/container_runtime/templates/config.toml.j2").read_text()
        self.assertIn("SystemdCgroup = true", config)
        self.assertNotIn('disabled_plugins = ["cri"]', config)

    def test_containerd_restarts_when_running_config_is_stale(self):
        # A handler is lost when a later task fails, so compare the service start
        # time with the config file on every run instead.
        tasks = yaml.safe_load(
            Path("ansible/roles/container_runtime/tasks/main.yml").read_text()
        )
        by_name = {task["name"]: task for task in tasks}
        self.assertNotIn("notify", by_name["Configure containerd for Kubernetes"])
        self.assertIn("config dump", by_name["Validate containerd configuration"]["ansible.builtin.command"])
        restart = by_name["Restart containerd with the current configuration"]
        self.assertEqual(restart["ansible.builtin.service"]["state"], "restarted")
        self.assertIn("container_runtime_config.stat.mtime", restart["when"])
        self.assertFalse(Path("ansible/roles/container_runtime/handlers/main.yml").exists())

    def test_node_role_does_not_use_fixed_sleeps(self):
        role_text = Path("ansible/roles/node_prepare/tasks/main.yml").read_text()
        self.assertNotIn("pause:", role_text)
        self.assertNotIn("sleep ", role_text)

    def test_node_role_applies_and_verifies_runtime_sysctls(self):
        # A handler is skipped when a later task fails, and never fires again once
        # the file exists, so the role must check the live kernel values each run.
        tasks = yaml.safe_load(Path("ansible/roles/node_prepare/tasks/main.yml").read_text())
        by_name = {task["name"]: task for task in tasks}
        self.assertNotIn("notify", by_name["Configure Kubernetes networking sysctls"])
        apply = by_name["Apply Kubernetes networking sysctls"]
        self.assertEqual(apply["ansible.builtin.command"], "sysctl --system")
        verify = by_name["Verify Kubernetes networking sysctls"]
        self.assertIn("net.ipv4.ip_forward", verify["ansible.builtin.command"])
        self.assertFalse(Path("ansible/roles/node_prepare/handlers/main.yml").exists())

    def test_bootstrap_applies_common_roles_in_dependency_order(self):
        plays = yaml.safe_load(Path("ansible/playbooks/bootstrap.yml").read_text())
        self.assertEqual(plays[0]["hosts"], "kube_cluster")
        self.assertNotIn("serial", plays[0])
        self.assertEqual(
            plays[0]["roles"],
            ["node_prepare", "container_runtime", "kubernetes_packages"],
        )

    def test_kubernetes_package_role_does_not_mask_failures(self):
        role_text = Path("ansible/roles/kubernetes_packages/tasks/main.yml").read_text()
        self.assertNotIn("ignore_errors:", role_text)
        self.assertIn("selection: hold", role_text)

    def test_storage_role_guards_destructive_operations(self):
        text = Path("ansible/roles/worker_storage/tasks/main.yml").read_text()
        for required in (
            "readlink -f {{ worker_data_device }}",
            "findmnt -n -o SOURCE /",
            "lsblk -n -o PKNAME",
            "blkid -o value -s TYPE",
            "unexpected filesystem",
            "mkfs.ext4",
            "mountpoint -q {{ worker_data_mount }}",
        ):
            self.assertIn(required, text)
        self.assertNotIn("wipefs", text)
        self.assertNotIn("force: true", text)

    def test_storage_role_uses_util_linux_mountpoint_exit_codes(self):
        # util-linux mountpoint returns 32 for "not a mountpoint" and 1 for errors.
        tasks = yaml.safe_load(
            Path("ansible/roles/worker_storage/tasks/main.yml").read_text()
        )
        by_name = {task["name"]: task for task in tasks}
        check = by_name["Check whether worker data is mounted"]
        mount = by_name["Mount worker data"]
        self.assertEqual(
            check["failed_when"], "worker_storage_mountpoint.rc not in [0, 32]"
        )
        self.assertEqual(mount["when"], "worker_storage_mountpoint.rc == 32")

    def test_storage_role_runs_only_on_workers_and_before_join(self):
        plays = yaml.safe_load(Path("ansible/playbooks/bootstrap.yml").read_text())
        role_plays = [
            (play["hosts"], role)
            for play in plays
            for role in play.get("roles", [])
        ]
        self.assertIn(("kube_workers", "worker_storage"), role_plays)
        self.assertNotIn(("kube_cluster", "worker_storage"), role_plays)
        roles = [role for _, role in role_plays]
        if "worker_join" in roles:
            self.assertLess(roles.index("worker_storage"), roles.index("worker_join"))

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


if __name__ == "__main__":
    unittest.main()
