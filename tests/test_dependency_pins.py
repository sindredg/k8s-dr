from pathlib import Path
import unittest


class DependencyPinTests(unittest.TestCase):
    def test_controller_dependencies_are_exactly_pinned(self):
        requirements = Path("ansible/requirements.txt").read_text().splitlines()
        self.assertEqual(
            requirements,
            [
                "ansible-core==2.21.5",
                "ansible-lint==26.10.1",
                "yamllint==1.38.0",
                "shellcheck-py==0.11.0.1",
            ],
        )

    def test_ansible_uses_project_local_temporary_directory(self):
        config = Path("ansible.cfg").read_text()
        self.assertIn("local_tmp = ansible/.cache/tmp", config)

    def test_ansible_uses_project_local_home(self):
        config = Path("ansible.cfg").read_text()
        self.assertIn("home = ansible/.cache", config)

    def test_ci_uses_project_local_tool_caches(self):
        workflow = Path(".github/workflows/ansible.yml").read_text()
        self.assertIn("ANSIBLE_HOME: ansible/.cache", workflow)
        self.assertIn("XDG_CACHE_HOME: ansible/.cache", workflow)


if __name__ == "__main__":
    unittest.main()
