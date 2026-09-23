from pathlib import Path
import unittest

import yaml


class AnsibleContractTests(unittest.TestCase):
    def test_component_versions_are_exact(self):
        values = yaml.safe_load(Path("ansible/group_vars/all.yml").read_text())
        self.assertEqual(values["kubernetes_version"], "1.36.2")
        self.assertEqual(values["kubernetes_deb_version"], "1.36.2-1.1")
        self.assertEqual(values["containerd_deb_version"], "2.2.1-0ubuntu1~24.04.3")
        self.assertEqual(values["helm_version"], "3.22.0")

    def test_network_and_storage_values_are_exact(self):
        values = yaml.safe_load(Path("ansible/group_vars/all.yml").read_text())
        self.assertEqual(values["pod_cidr"], "192.168.0.0/16")
        self.assertEqual(values["service_cidr"], "10.96.0.0/12")
        self.assertEqual(values["worker_data_device"], "/dev/disk/by-id/google-worker-data")
        self.assertEqual(values["worker_data_mount"], "/var/lib/k8s-dr")

    def test_containerd_uses_systemd_cgroups_and_cri(self):
        config = Path("ansible/roles/container_runtime/templates/config.toml.j2").read_text()
        self.assertIn("SystemdCgroup = true", config)
        self.assertNotIn('disabled_plugins = ["cri"]', config)

    def test_node_role_does_not_use_fixed_sleeps(self):
        role_text = Path("ansible/roles/node_prepare/tasks/main.yml").read_text()
        self.assertNotIn("pause:", role_text)
        self.assertNotIn("sleep ", role_text)

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


if __name__ == "__main__":
    unittest.main()
