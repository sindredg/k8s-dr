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


if __name__ == "__main__":
    unittest.main()
