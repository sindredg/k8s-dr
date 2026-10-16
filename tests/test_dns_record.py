from pathlib import Path
import re
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
            ("caa", "git"),
        ):
            with self.subTest(args=args):
                result = run(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
        self.assertLess(self.text.index('"$action" = delete ] && [ "$name" = git ]'), self.text.index("sops --decrypt"))

    def test_caa_records_match_the_surface_check(self):
        # The surface check fails on any CAA record outside CAA_EXPECTED, so
        # the records this script creates must be exactly that set.
        def array(text, name):
            body = re.search(rf"^{name}=\(\n(.*?)^\)", text, re.M | re.S).group(1)
            return re.findall(r"'([^']*)'", body)

        surface = Path("scripts/check-public-surface.sh").read_text()
        expected = sorted(array(surface, "CAA_EXPECTED"))
        created = sorted(
            '0 {} "{}"'.format(*entry.split(" ", 1)) for entry in array(self.text, "CAA_RECORDS")
        )
        self.assertTrue(expected)
        self.assertEqual(created, expected)

    def test_caa_never_deletes_a_record(self):
        start = self.text.index('if [ "$action" = caa ]; then\n  existing=')
        block = self.text[start : self.text.index("\nfi\n", start)]
        self.assertNotIn("DELETE", block)

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
