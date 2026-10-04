from pathlib import Path
import subprocess
import unittest

SCRIPT = Path("scripts/dns_record.sh")


def run(*args):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True)


class DnsRecordTests(unittest.TestCase):
    def setUp(self):
        self.text = SCRIPT.read_text()

    def test_script_parses(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_rejects_bad_arguments_before_it_reads_the_token(self):
        # Each case exits 2 from the argument checks, which run before sops.
        for args in (
            (),
            ("show",),
            ("show", "www"),
            ("set", "git-primary", "192.0.2.1"),
            ("set", "git"),
            ("set", "git", "not-an-address"),
            ("delete", "git"),
            ("purge", "git"),
        ):
            with self.subTest(args=args):
                result = run(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
        self.assertLess(self.text.index('"$action" = delete ] && [ "$name" = git ]'), self.text.index("sops --decrypt"))

    def test_records_are_dns_only_with_the_cutover_ttl(self):
        self.assertIn("TTL=60", self.text)
        self.assertIn("proxied: false", self.text)

    def test_token_never_reaches_an_argument_or_the_output(self):
        self.assertIn("curl -sS -m 30 -H @-", self.text)
        self.assertNotRegex(self.text, r"(echo|printf '%s\\n') [^\n]*\$token")

    def test_makefile_passes_the_sops_key(self):
        makefile = Path("Makefile").read_text()
        self.assertIn('FLUX_AGE_KEY_FILE="$(FLUX_AGE_KEY_FILE)" scripts/dns_record.sh', makefile)


if __name__ == "__main__":
    unittest.main()
