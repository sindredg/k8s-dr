import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path("ansible/roles/worker_storage/files/k8sdr-snapshot.sh")

# Each stub records its call and exits with ${STUB_<NAME>_STATUS:-0}. lvs
# prints ${STUB_LVS_OUTPUT} first.
STUB = """#!/bin/bash
printf '%s %s\\n' "$(basename "$0")" "$*" >>"$STUB_CALLS"
name=$(basename "$0" | tr '[:lower:]' '[:upper:]')
output_var=STUB_${name}_OUTPUT
status_var=STUB_${name}_STATUS
printf '%s' "${!output_var:-}"
exit "${!status_var:-0}"
"""


class SnapshotRemoveTests(unittest.TestCase):
    """Runs `k8sdr-snapshot remove` against stubbed LVM and mount commands."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for name in ("lvs", "lvremove", "mountpoint", "umount"):
            stub = bin_dir / name
            stub.write_text(STUB)
            stub.chmod(0o755)
        # The command hardcodes host paths; the copy points them at the
        # temporary directory so the mount checks see existing paths.
        self.snapshot_mount = root / "snapshot" / "data"
        self.snapshot_mount.mkdir(parents=True)
        self.target = root / "targets" / "gitea"
        self.target.mkdir(parents=True)
        text = SCRIPT.read_text()
        for old, new in (
            ("\nSNAPSHOT_DIR=/run/k8sdr-snapshot\n", f"\nSNAPSHOT_DIR={root / 'snapshot'}\n"),
            ("\nTARGET_ROOT=/var/lib/k8s-dr-backup\n", f"\nTARGET_ROOT={root / 'targets'}\n"),
        ):
            self.assertEqual(text.count(old), 1)
            text = text.replace(old, new)
        self.script = root / "k8sdr-snapshot"
        self.script.write_text(text)
        self.calls = root / "calls"
        self.calls.touch()
        self.env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "STUB_CALLS": str(self.calls),
            # Not a mount point unless a test says otherwise.
            "STUB_MOUNTPOINT_STATUS": "32",
            "STUB_LVS_OUTPUT": "  data\n",
        }

    def tearDown(self):
        self.directory.cleanup()

    def run_remove(self, **stubs):
        result = subprocess.run(
            ["bash", str(self.script), "remove"],
            env={**self.env, **stubs}, capture_output=True, text=True,
        )
        return result, self.calls.read_text().splitlines()

    def test_fails_when_lvs_fails(self):
        result, calls = self.run_remove(STUB_LVS_STATUS="5", STUB_LVS_OUTPUT="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot list the logical volumes of k8sdr", result.stderr)
        self.assertFalse(any(call.startswith("lvremove") for call in calls))

    def test_removes_a_present_snapshot(self):
        result, calls = self.run_remove(STUB_LVS_OUTPUT="  backup\n  data\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("lvremove --yes k8sdr/backup", calls)

    def test_leaves_the_volume_group_alone_without_a_snapshot(self):
        # A name that only contains the snapshot name is not the snapshot.
        result, calls = self.run_remove(STUB_LVS_OUTPUT="  data\n  backup-old\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call.startswith("lvremove") for call in calls))

    def test_fails_when_lvremove_fails(self):
        result, _ = self.run_remove(STUB_LVS_OUTPUT="  backup\n", STUB_LVREMOVE_STATUS="5")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot remove k8sdr/backup", result.stderr)

    def test_unmounts_mounted_binds_and_the_snapshot(self):
        result, calls = self.run_remove(STUB_MOUNTPOINT_STATUS="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"umount {self.target}", calls)
        self.assertIn(f"umount {self.snapshot_mount}", calls)

    def test_fails_when_mountpoint_errors(self):
        # util-linux mountpoint returns 1 for errors and 32 for "not mounted".
        result, calls = self.run_remove(STUB_MOUNTPOINT_STATUS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot tell whether", result.stderr)
        self.assertFalse(any(call.startswith(("umount", "lvremove")) for call in calls))

    def test_fails_when_umount_fails(self):
        result, calls = self.run_remove(STUB_MOUNTPOINT_STATUS="0", STUB_UMOUNT_STATUS="32")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot unmount", result.stderr)
        self.assertFalse(any(call.startswith("lvremove") for call in calls))

    def test_skips_a_missing_snapshot_mount(self):
        # /run is empty after a reboot, and mountpoint returns 1 for a missing path.
        self.snapshot_mount.rmdir()
        result, calls = self.run_remove(STUB_MOUNTPOINT_STATUS="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(f"mountpoint -q {self.snapshot_mount}", calls)


if __name__ == "__main__":
    unittest.main()
