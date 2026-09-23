import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

GROUP_VARS = Path("ansible/playbooks/group_vars/all.yml")


def find_ansible_inventory():
    found = shutil.which("ansible-inventory")
    if found:
        return found
    local = Path(".venv/bin/ansible-inventory")
    return str(local) if local.is_file() else None


class VariableLoadingTests(unittest.TestCase):
    def test_shared_variables_resolve_for_a_generated_inventory(self):
        binary = find_ansible_inventory()
        if binary is None:
            self.skipTest("ansible-inventory is not installed")
        expected = yaml.safe_load(GROUP_VARS.read_text())
        with tempfile.TemporaryDirectory() as directory:
            inventory = Path(directory) / "hosts.json"
            inventory.write_text(
                json.dumps(
                    {"all": {"children": {"kube_cluster": {"hosts": {"node": {}}}}}}
                )
            )
            completed = subprocess.run(
                [
                    binary,
                    "-i",
                    str(inventory),
                    "--playbook-dir",
                    "ansible/playbooks",
                    "--host",
                    "node",
                ],
                check=True,
                capture_output=True,
                text=True,
                env={**os.environ, "ANSIBLE_CONFIG": "ansible.cfg"},
            )
        resolved = json.loads(completed.stdout)
        for name, value in expected.items():
            self.assertEqual(resolved.get(name), value, name)


if __name__ == "__main__":
    unittest.main()
