from pathlib import Path
import re
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, str(Path("scripts")))
import fixture_commit  # noqa: E402

FIXTURES = Path("recovery/fixtures.yaml")
FIXTURE_PLAYBOOKS = [
    Path("ansible/playbooks") / name
    for name in ("create_fixtures.yml", "check_fixtures.yml", "write_check.yml")
]


def _tasks(tasks):
    for task in tasks:
        yield task
        for key in ("block", "rescue", "always"):
            yield from _tasks(task.get(key, []))


def _leaves(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from _leaves(item)
    else:
        yield value


class FixtureDefinitionTests(unittest.TestCase):
    def test_recorded_sha_matches_the_fixed_commit(self):
        # The drill checks for this SHA, so an edit to the commit fields must
        # also update it.
        fixtures = yaml.safe_load(FIXTURES.read_text())
        with tempfile.TemporaryDirectory() as directory:
            sha = fixture_commit.build(FIXTURES, directory)
        self.assertEqual(sha, fixtures["fixture_commit"]["sha"])

    def test_issue_is_the_first_in_a_new_repository(self):
        fixtures = yaml.safe_load(FIXTURES.read_text())
        self.assertEqual(fixtures["fixture_issue"]["number"], 1)
        self.assertTrue(fixtures["fixture_issue"]["title"])


class FixtureSecretTests(unittest.TestCase):
    def test_every_sops_file_matches_a_rule_and_is_encrypted(self):
        # The repository is public. This covers operator credentials outside
        # deploy/, which the Secret check in test_flux.py does not read.
        rules = yaml.safe_load(Path(".sops.yaml").read_text())["creation_rules"]
        paths = [
            path for path in Path(".").rglob("*.sops.yaml")
            if path.name != ".sops.yaml" and not {".git", ".venv", ".terraform"} & set(path.parts)
        ]
        self.assertIn(Path("recovery/fixtures.sops.yaml"), paths)
        for path in paths:
            with self.subTest(path=str(path)):
                rule = next(
                    (rule for rule in rules if re.search(rule["path_regex"], path.as_posix())), None
                )
                self.assertIsNotNone(rule, "no .sops.yaml rule matches")
                for document in yaml.safe_load_all(path.read_text()):
                    self.assertIn("sops", document)
                    encrypted = [
                        value for key, value in document.items()
                        if key != "sops" and re.search(rule.get("encrypted_regex", ""), key)
                    ]
                    self.assertTrue(encrypted)
                    for leaf in _leaves(encrypted):
                        self.assertRegex(str(leaf), r"^ENC\[")

    def test_tasks_that_use_a_password_hide_their_output(self):
        paths = FIXTURE_PLAYBOOKS + [Path("ansible/playbooks/tasks/fixture_password.yml")]
        for path in paths:
            documents = yaml.safe_load(path.read_text())
            plays = documents if "hosts" in documents[0] else [{"tasks": documents}]
            for play in plays:
                for task in _tasks(play.get("tasks", [])):
                    # Blocks are checked through their tasks; asserts and
                    # imports only name the password variable.
                    if {"block", "ansible.builtin.assert", "ansible.builtin.import_tasks"} & set(task):
                        continue
                    arguments = {key: value for key, value in task.items() if key != "name"}
                    if re.search(r"password", str(arguments)):
                        with self.subTest(path=str(path), task=task["name"]):
                            self.assertIs(task.get("no_log"), True)


if __name__ == "__main__":
    unittest.main()
