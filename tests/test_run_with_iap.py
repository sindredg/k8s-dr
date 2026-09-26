from datetime import datetime, timezone
import io
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import run_with_iap as module


class RunWithIapTests(unittest.TestCase):
    def setUp(self):
        self.targets = [
            module.Target(
                role="control-plane",
                instance="example-control-plane",
                project="example-project",
                zone="europe-north1-a",
                local_port=2201,
            ),
            module.Target(
                role="worker",
                instance="example-worker",
                project="example-project",
                zone="europe-north1-a",
                local_port=2202,
            ),
        ]

    def test_busy_port_stops_before_starting_gcloud(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            target = module.Target("worker", "vm", "project", "zone", listener.getsockname()[1])
            with self.assertRaisesRegex(RuntimeError, f"{target.local_port}.*already in use"):
                module.ensure_ports_available([target])

    def test_duplicate_ports_are_rejected(self):
        duplicate = [self.targets[0], module.Target("worker", "vm", "p", "z", 2201)]
        with self.assertRaisesRegex(ValueError, "duplicate.*2201"):
            module.ensure_ports_available(duplicate)

    def test_early_tunnel_exit_raises(self):
        process = mock.Mock()
        process.poll.return_value = 1
        process.stderr.read.return_value = "permission denied"
        with self.assertRaisesRegex(RuntimeError, "IAP tunnel exited.*permission denied"):
            module.wait_for_tunnel(process, 2201, timeout=0.1)

    @mock.patch("scripts.run_with_iap.subprocess.run")
    @mock.patch("scripts.run_with_iap.wait_for_tunnel")
    @mock.patch("scripts.run_with_iap.ensure_ports_available")
    @mock.patch("scripts.run_with_iap.subprocess.Popen")
    def test_ansible_failure_terminates_every_tunnel(self, popen, ensure, wait, run):
        processes = [self._process(), self._process()]
        popen.side_effect = processes
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "key-one\n", ""),
            subprocess.CompletedProcess([], 0, "key-two\n", ""),
            subprocess.CompletedProcess([], 1, "", ""),
        ]
        with tempfile.TemporaryDirectory() as directory:
            result = module.run_with_tunnels(
                self.targets, ["false"], known_hosts_path=Path(directory) / "known_hosts"
            )
        self.assertEqual(result, 1)
        environment = run.call_args_list[2].kwargs["env"]
        self.assertIn("StrictHostKeyChecking=yes", environment["ANSIBLE_SSH_COMMON_ARGS"])
        self.assertIn("UserKnownHostsFile=", environment["ANSIBLE_SSH_COMMON_ARGS"])
        self.assertIn("ServerAliveInterval=30", environment["ANSIBLE_SSH_COMMON_ARGS"])
        for process in processes:
            process.terminate.assert_called_once()

    @mock.patch("scripts.run_with_iap.subprocess.run")
    @mock.patch("scripts.run_with_iap.wait_for_tunnel")
    @mock.patch("scripts.run_with_iap.ensure_ports_available")
    @mock.patch("scripts.run_with_iap.subprocess.Popen")
    def test_host_key_scan_failure_terminates_every_tunnel(self, popen, ensure, wait, run):
        processes = [self._process(), self._process()]
        popen.side_effect = processes
        run.return_value = subprocess.CompletedProcess([], 1, "", "scan failed")
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch("scripts.run_with_iap.time.monotonic", side_effect=[0, 31]):
                with self.assertRaisesRegex(RuntimeError, "host key"):
                    module.run_with_tunnels(
                        self.targets, ["true"], known_hosts_path=Path(directory) / "known_hosts"
                    )
        for process in processes:
            process.terminate.assert_called_once()

    @mock.patch("scripts.run_with_iap.ensure_ports_available")
    @mock.patch("scripts.run_with_iap.subprocess.Popen")
    def test_partial_start_failure_terminates_started_tunnel(self, popen, ensure):
        process = self._process()
        popen.side_effect = [process, OSError("gcloud failed")]
        with self.assertRaisesRegex(OSError, "gcloud failed"):
            module.run_with_tunnels(self.targets, ["true"])
        process.terminate.assert_called_once()

    @mock.patch("scripts.run_with_iap.subprocess.Popen")
    def test_start_tunnel_uses_explicit_gcloud_arguments(self, popen):
        module._start_tunnel(self.targets[0])
        popen.assert_called_once_with(
            [
                "gcloud",
                "compute",
                "start-iap-tunnel",
                "example-control-plane",
                "22",
                "--local-host-port=127.0.0.1:2201",
                "--project=example-project",
                "--zone=europe-north1-a",
                "--quiet",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

    @mock.patch("scripts.run_with_iap.write_known_hosts")
    @mock.patch("scripts.run_with_iap.subprocess.run", side_effect=KeyboardInterrupt)
    @mock.patch("scripts.run_with_iap.wait_for_tunnel")
    @mock.patch("scripts.run_with_iap.ensure_ports_available")
    @mock.patch("scripts.run_with_iap.subprocess.Popen")
    def test_keyboard_interrupt_terminates_every_tunnel(
        self, popen, ensure, wait, run, write_known_hosts
    ):
        processes = [self._process(), self._process()]
        popen.side_effect = processes
        with self.assertRaises(KeyboardInterrupt):
            module.run_with_tunnels(self.targets, ["command"])
        for process in processes:
            process.terminate.assert_called_once()

    @mock.patch("scripts.run_with_iap.write_known_hosts")
    @mock.patch("scripts.run_with_iap.subprocess.run")
    @mock.patch("scripts.run_with_iap.wait_for_tunnel")
    @mock.patch("scripts.run_with_iap.ensure_ports_available")
    @mock.patch("scripts.run_with_iap.subprocess.Popen")
    def test_cleanup_escalates_from_terminate_to_kill(
        self, popen, ensure, wait, run, write_known_hosts
    ):
        process = self._process()
        process.wait.side_effect = [subprocess.TimeoutExpired("gcloud", 5), 0]
        popen.side_effect = [process, self._process()]
        run.return_value = subprocess.CompletedProcess([], 0, "", "")

        self.assertEqual(module.run_with_tunnels(self.targets, ["true"]), 0)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()

    def test_write_known_hosts_uses_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "known_hosts"
            completed = subprocess.CompletedProcess([], 0, "[127.0.0.1]:2201 ssh-ed25519 AAAA\n", "")
            with mock.patch("scripts.run_with_iap.subprocess.run", return_value=completed):
                module.write_known_hosts([self.targets[0]], path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    @mock.patch("scripts.run_with_iap.time.sleep")
    @mock.patch("scripts.run_with_iap.subprocess.run")
    def test_host_key_scan_retries_after_temporary_ssh_failure(self, run, sleep):
        run.side_effect = [
            subprocess.CompletedProcess([], 1, "", "connection closed"),
            subprocess.CompletedProcess([], 0, "[127.0.0.1]:2201 ssh-ed25519 AAAA\n", ""),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "known_hosts"
            try:
                module.write_known_hosts([self.targets[0]], path)
            except RuntimeError as error:
                self.fail(f"temporary SSH failure was not retried: {error}")
            self.assertIn("ssh-ed25519", path.read_text())
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once()

    def test_load_targets_rejects_non_json_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hosts.json"
            path.write_text("not json")
            with self.assertRaisesRegex(ValueError, "JSON"):
                module.load_targets(path)

    def test_load_targets_maps_both_required_roles(self):
        targets = self._load(self._inventory())
        self.assertEqual(targets, self.targets)

    def test_load_targets_rejects_missing_metadata(self):
        inventory = self._inventory()
        del inventory["all"]["children"]["kube_workers"]["hosts"]["worker"]["gcp_zone"]
        with self.assertRaisesRegex(ValueError, "worker.*gcp_zone"):
            self._load(inventory)

    def test_load_targets_rejects_unexpected_host(self):
        inventory = self._inventory()
        hosts = inventory["all"]["children"]["kube_workers"]["hosts"]
        hosts["extra"] = dict(hosts["worker"])
        with self.assertRaisesRegex(ValueError, "exactly.*worker"):
            self._load(inventory)

    def test_load_targets_rejects_non_loopback_host(self):
        inventory = self._inventory()
        host = inventory["all"]["children"]["kube_workers"]["hosts"]["worker"]
        host["ansible_host"] = "10.10.0.3"
        with self.assertRaisesRegex(ValueError, "loopback"):
            self._load(inventory)

    def test_load_targets_rejects_privileged_port(self):
        inventory = self._inventory()
        host = inventory["all"]["children"]["kube_workers"]["hosts"]["worker"]
        host["ansible_port"] = 22
        with self.assertRaisesRegex(ValueError, "1024"):
            self._load(inventory)

    def test_cli_rejects_empty_command(self):
        with self.assertRaisesRegex(ValueError, "command"):
            module.main(["--inventory", "hosts.json", "--"])

    def test_run_summary_uses_utc_timestamps_and_whole_seconds(self):
        summary = module.format_run_summary(
            datetime(2026, 9, 25, 8, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 25, 8, 7, 30, tzinfo=timezone.utc),
            450.4,
            0,
        )
        self.assertEqual(
            summary,
            "run_with_iap: started 2026-09-25T08:00:00Z, "
            "finished 2026-09-25T08:07:30Z, elapsed 450s, exit 0",
        )

    @mock.patch("scripts.run_with_iap.run_with_tunnels", return_value=2)
    @mock.patch("scripts.run_with_iap.load_targets")
    def test_main_reports_timing_and_returns_the_command_exit_code(self, load, run):
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            result = module.main(["--inventory", "hosts.json", "--", "ansible-playbook"])
        self.assertEqual(result, 2)
        self.assertRegex(stderr.getvalue(), r"^run_with_iap: started \S+Z, finished \S+Z, elapsed \d+s, exit 2\n$")

    @staticmethod
    def _process():
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        return process

    @staticmethod
    def _inventory():
        def host(role, port):
            return {
                "ansible_host": "127.0.0.1",
                "ansible_port": port,
                "gcp_instance_name": f"example-{role}",
                "gcp_project": "example-project",
                "gcp_zone": "europe-north1-a",
            }

        return {
            "all": {
                "children": {
                    "kube_control_plane": {
                        "hosts": {"control-plane": host("control-plane", 2201)}
                    },
                    "kube_workers": {"hosts": {"worker": host("worker", 2202)}},
                    "kube_cluster": {"children": {}},
                }
            }
        }

    def _load(self, inventory):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hosts.json"
            path.write_text(json.dumps(inventory))
            return module.load_targets(path)


if __name__ == "__main__":
    unittest.main()
