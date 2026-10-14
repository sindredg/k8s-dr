from pathlib import Path
import re
import unittest


class MakefileTests(unittest.TestCase):
    def test_check_target_syntax_checks_every_playbook(self):
        # A new playbook must be added to PLAYBOOKS, or `make check` skips it.
        makefile = Path("Makefile").read_text()
        listed = set(re.search(r"^PLAYBOOKS := (.+)$", makefile, re.MULTILINE).group(1).split())
        playbooks = {path.stem for path in Path("ansible/playbooks").glob("*.yml")}
        self.assertEqual(listed, playbooks)

    def test_bootstrap_passes_extra_arguments_and_defaults_to_none(self):
        # The data disk conversion runs the worker plays alone with
        # BOOTSTRAP_ARGS='--limit worker'. See docs/runbooks/snapshot-backups.md.
        makefile = Path("Makefile").read_text()
        self.assertRegex(makefile, r"(?m)^BOOTSTRAP_ARGS \?=$")
        recipe = re.search(r"(?ms)^bootstrap:.*?(?=^\S|\Z)", makefile).group(0)
        self.assertIn("$(BOOTSTRAP_ARGS)", recipe)

    def test_cluster_selects_the_root_inventory_and_flux_directory(self):
        # One variable, so a recovery command cannot mix a recovery root with
        # the primary inventory. See decision 0008.
        makefile = Path("Makefile").read_text()
        self.assertRegex(makefile, r"(?m)^CLUSTER \?= primary$")
        self.assertRegex(makefile, r"(?m)^TF_DIR \?= infra/\$\(CLUSTER\)$")
        self.assertRegex(
            makefile, r"(?m)^INVENTORY := ansible/inventory/generated/\$\(CLUSTER\)/hosts\.json$"
        )
        self.assertIn("-e flux_cluster=$(CLUSTER)", makefile)
        self.assertIn("-e fixture_cluster=$(CLUSTER)", makefile)

    def test_defaults_outside_make_point_at_the_primary(self):
        expected = "ansible/inventory/generated/primary/hosts.json"
        for path in ("ansible.cfg", "scripts/run_with_iap.py", "scripts/prepare_ansible_inventory.py"):
            with self.subTest(path=path):
                self.assertIn(expected, Path(path).read_text())


if __name__ == "__main__":
    unittest.main()
