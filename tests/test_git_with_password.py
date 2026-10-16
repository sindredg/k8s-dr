import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path("scripts/git_with_password.sh").resolve()

# Stands in for git: asks GIT_ASKPASS the two prompts Git uses, in Git's
# wording, and prints the answers.
STUB_GIT = """#!/bin/sh
printf 'username=%s\\n' "$("$GIT_ASKPASS" "Username for 'https://git.example.com': ")"
printf 'password=%s\\n' "$("$GIT_ASKPASS" "Password for 'https://user@git.example.com': ")"
printf 'args=%s\\n' "$*"
"""


class GitWithPasswordTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        bin_dir = Path(self.directory.name)
        stub = bin_dir / "git"
        stub.write_text(STUB_GIT)
        stub.chmod(0o755)
        self.env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}

    def tearDown(self):
        self.directory.cleanup()

    def run_helper(self, password, **env):
        return subprocess.run(
            [str(SCRIPT), "ls-remote", "https://git.example.com/user/repo.git"],
            input=password, env={**self.env, **env}, capture_output=True, text=True,
        )

    def test_answers_each_prompt_with_its_own_value(self):
        # A newer libcurl makes Git ask for the username as well as the
        # password, even when the URL names the user.
        result = self.run_helper("secret\n", GIT_STDIN_USERNAME="recovery-fixture")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "username=recovery-fixture",
            "password=secret",
            "args=ls-remote https://git.example.com/user/repo.git",
        ])

    def test_requires_a_username(self):
        result = self.run_helper("secret\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("GIT_STDIN_USERNAME", result.stderr)
        self.assertNotIn("password=", result.stdout)

    def test_keeps_the_password_out_of_the_arguments(self):
        result = self.run_helper("secret\n", GIT_STDIN_USERNAME="recovery-fixture")
        self.assertNotIn("secret", result.stdout.splitlines()[-1])


if __name__ == "__main__":
    unittest.main()
