import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import prepare_ansible_inventory as module


class PrepareAnsibleInventoryTests(unittest.TestCase):
    def setUp(self):
        fixture = Path("tests/fixtures/terraform-outputs.json")
        self.outputs = json.loads(fixture.read_text())
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.output_path = Path(self.temporary_directory.name) / "inventory" / "hosts.json"

    def test_build_inventory_maps_roles_and_private_metadata(self):
        inventory = module.build_inventory(self.outputs, "user_example", "/tmp/key")
        children = inventory["all"]["children"]
        control_plane = children["kube_control_plane"]["hosts"]["control-plane"]
        worker = children["kube_workers"]["hosts"]["worker"]

        self.assertEqual(control_plane["ansible_host"], "127.0.0.1")
        self.assertEqual(control_plane["ansible_port"], 2201)
        self.assertEqual(control_plane["node_internal_ip"], "10.10.0.2")
        self.assertEqual(control_plane["gcp_instance_name"], "example-control-plane")
        self.assertEqual(worker["ansible_port"], 2202)
        self.assertEqual(worker["gcp_project"], "example-project")
        self.assertEqual(worker["gcp_zone"], "europe-north1-a")
        self.assertEqual(children["kube_cluster"]["children"], {
            "kube_control_plane": {},
            "kube_workers": {},
        })
        self.assertEqual(inventory["all"]["vars"]["ansible_user"], "user_example")
        self.assertEqual(
            inventory["all"]["vars"]["ansible_ssh_private_key_file"],
            "/tmp/key",
        )

    def test_rejects_missing_output(self):
        del self.outputs["primary_zone"]
        with self.assertRaisesRegex(ValueError, "primary_zone"):
            module.build_inventory(self.outputs, "user_example", "/tmp/key")

    def test_rejects_overlapping_pod_and_vpc_networks(self):
        self.outputs["primary_subnet_cidr"]["value"] = "192.168.1.0/24"
        with self.assertRaisesRegex(ValueError, "overlap"):
            module.build_inventory(self.outputs, "user_example", "/tmp/key")

    def test_write_inventory_uses_private_permissions(self):
        inventory = module.build_inventory(self.outputs, "user_example", "/tmp/key")
        module.write_inventory(self.output_path, inventory)
        self.assertEqual(self.output_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.output_path.parent.stat().st_mode & 0o777, 0o700)

    def test_rejects_malformed_subnet_cidr(self):
        self.outputs["primary_subnet_cidr"]["value"] = "not-a-cidr"
        with self.assertRaisesRegex(ValueError, "primary_subnet_cidr"):
            module.build_inventory(self.outputs, "user_example", "/tmp/key")

    def test_rejects_missing_worker_name(self):
        del self.outputs["instance_names"]["value"]["worker"]
        with self.assertRaisesRegex(ValueError, "worker"):
            module.build_inventory(self.outputs, "user_example", "/tmp/key")

    def test_rejects_missing_worker_address(self):
        del self.outputs["internal_ips"]["value"]["worker"]
        with self.assertRaisesRegex(ValueError, "worker"):
            module.build_inventory(self.outputs, "user_example", "/tmp/key")

    def test_rejects_equal_node_addresses(self):
        self.outputs["internal_ips"]["value"]["worker"] = "10.10.0.2"
        with self.assertRaisesRegex(ValueError, "distinct"):
            module.build_inventory(self.outputs, "user_example", "/tmp/key")

    def test_rejects_empty_os_login_username(self):
        with self.assertRaisesRegex(ValueError, "SSH user"):
            module.build_inventory(self.outputs, "", "/tmp/key")

    def test_cli_rejects_missing_ssh_key_without_writing_output(self):
        missing_key = Path(self.temporary_directory.name) / "missing-key"
        with self.assertRaisesRegex(ValueError, "SSH private key"):
            module.main([
                "--ssh-user", "user_example",
                "--ssh-key", str(missing_key),
                "--output", str(self.output_path),
            ])
        self.assertFalse(self.output_path.exists())

    @mock.patch("scripts.prepare_ansible_inventory.subprocess.run")
    def test_cli_reads_requested_terraform_directory(self, run):
        key = Path(self.temporary_directory.name) / "id_ed25519"
        key.write_text("documentation-only-test-key")
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(self.outputs), stderr=""
        )

        result = module.main([
            "--terraform-dir", "infra/example",
            "--ssh-user", "user_example",
            "--ssh-key", str(key),
            "--output", str(self.output_path),
        ])

        self.assertEqual(result, 0)
        run.assert_called_once_with(
            ["terraform", "-chdir=infra/example", "output", "-json"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertTrue(self.output_path.exists())

    @mock.patch("scripts.prepare_ansible_inventory.os.replace", side_effect=OSError("replace failed"))
    def test_failed_atomic_replace_leaves_no_output_file(self, replace):
        inventory = module.build_inventory(self.outputs, "user_example", "/tmp/key")
        with self.assertRaisesRegex(OSError, "replace failed"):
            module.write_inventory(self.output_path, inventory)
        self.assertFalse(self.output_path.exists())


if __name__ == "__main__":
    unittest.main()
