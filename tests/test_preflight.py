from pathlib import Path
import re
import subprocess
import unittest

SCRIPT = Path("scripts/preflight.sh")


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.text = SCRIPT.read_text()

    def test_script_parses(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_changes_nothing(self):
        # The preflight runs against the live service and state before a
        # drill, so it may only read.
        for verb in (
            "terraform apply", "terraform destroy", "storage rm", "kubectl",
            "instances stop", "-X POST", "-X PUT", "-X PATCH", "-X DELETE", "git push",
        ):
            with self.subTest(verb=verb):
                self.assertNotIn(verb, self.text)
        self.assertIn("plan -lock=false", self.text)
        # The only copy is a download from the bucket into the work directory.
        copies = re.findall(r"gcloud storage cp (\S+) (\S+)", self.text)
        self.assertEqual(len(copies), 1)
        self.assertTrue(copies[0][0].startswith('"gs://'))
        self.assertEqual(copies[0][1], '"$WORKDIR/"')

    def test_token_never_reaches_an_argument_or_the_output(self):
        self.assertIn("curl -sS -m 30 -H @-", self.text)
        self.assertNotRegex(self.text, r"(echo|printf '%s\\n'|report \w+ \S+) [^\n]*\$token")

    def test_a_missing_prerequisite_is_not_a_failed_check(self):
        for outcome in ("pass)", "fail)", "missing)", "manual)"):
            self.assertIn(outcome, self.text)
        self.assertIn('report missing "$id" "no readable key at $file', self.text)

    def test_reuses_the_fixture_check_and_the_restore_key(self):
        self.assertIn("make --no-print-directory check-fixtures", self.text)
        makefile = Path("Makefile").read_text()
        self.assertIn(
            'FLUX_AGE_KEY_FILE="$(FLUX_AGE_KEY_FILE)" BACKUP_AGE_KEY_FILE="$(BACKUP_AGE_KEY_FILE)" '
            "scripts/preflight.sh",
            makefile,
        )


if __name__ == "__main__":
    unittest.main()
