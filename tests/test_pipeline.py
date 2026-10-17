import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import pipeline as module

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/recover.yml"
KEY = "AGE-SECRET-KEY-1EXAMPLE\n"
ENVIRON = {
    "GCP_PROJECT_ID": "example-project",
    "GCP_WORKLOAD_IDENTITY_PROVIDER": "projects/123456789/locations/global/workloadIdentityPools/github/providers/k8s-dr",
    "STATE_BUCKET": "example-state",
    "BACKUP_BUCKET": "example-backups",
    "ADMIN_MEMBER": "user:admin@example.com",
    "RECOVERY_READER_MEMBER": "user:reader@example.com",
    "RECOVERY_SUBNET_CIDR": "10.20.0.0/24",
}


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class FakeSystem(module.System):
    def __init__(self):
        self.calls: list[list[str]] = []
        self.stdins: list[str | None] = []
        self.answers: dict[str, list] = {}

    def answer(self, needle, *results):
        self.answers[needle] = list(results)

    def ran(self, needle):
        return any(needle in " ".join(argv) for argv in self.calls)

    def call(self, argv, *, stdin=None, capture=False):
        self.calls.append(list(argv))
        self.stdins.append(stdin)
        line = " ".join(argv)
        for needle, results in self.answers.items():
            if needle in line and results:
                return results.pop(0) if len(results) > 1 else results[0]
        out = next((a for a in argv if a.startswith("--out-file=")), None)
        if out:
            Path(out.split("=", 1)[1]).write_text(KEY)
        return completed()


class PipelineTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for root in module.ROOTS:
            (self.root / f"infra/{root}").mkdir(parents=True)
            shutil.copy(ROOT / f"infra/{root}/terraform.tfvars.example", self.root / f"infra/{root}")
        self.system = FakeSystem()

    def main(self, *args, environ=None):
        with mock.patch("sys.stdout") as stdout, mock.patch("sys.stderr") as stderr:
            code = module.main(["--root", str(self.root), *args], system=self.system,
                               environ=ENVIRON if environ is None else environ)
        self.output = "".join(call.args[0] for call in stdout.write.call_args_list)
        self.errors = "".join(call.args[0] for call in stderr.write.call_args_list)
        return code


class ConfigureTests(PipelineTestCase):
    def test_writes_every_backend_and_the_planned_variables(self):
        self.assertEqual(self.main("configure"), 0)
        for root in module.ROOTS:
            backend = self.root / f"infra/{root}/backend.hcl"
            self.assertEqual(backend.read_text(), f'bucket = "example-state"\nprefix = "{root}"\n')
            self.assertEqual(backend.stat().st_mode & 0o777, 0o600)
        shared = (self.root / "infra/shared/terraform.tfvars").read_text()
        self.assertIn('admin_member           = "user:admin@example.com"', shared)
        self.assertIn('state_bucket_name = "example-state"', shared)
        recovery = (self.root / "infra/recovery/terraform.tfvars").read_text()
        self.assertIn('recovery_subnet_cidr = "10.20.0.0/24"', recovery)
        self.assertIn('name_prefix   = "k8sdr-recovery"', recovery)
        for text in (shared, recovery):
            self.assertNotIn("REPLACE_WITH_", text)

    def test_every_placeholder_of_a_planned_root_is_mapped(self):
        for root in module.PLANNED_ROOTS:
            example = (ROOT / f"infra/{root}/terraform.tfvars.example").read_text()
            for line in example.splitlines():
                match = module.PLACEHOLDER.match(line)
                if match:
                    self.assertIn(match.group(2), module.VARIABLES, root)

    def test_an_unmapped_placeholder_fails(self):
        path = self.root / "infra/recovery/terraform.tfvars.example"
        path.write_text(path.read_text() + 'new_value = "REPLACE_WITH_NEW_VALUE"\n')
        self.assertEqual(self.main("configure"), 1)
        self.assertIn("no environment variable is mapped to new_value", self.errors)

    def test_a_missing_variable_fails_without_printing_values(self):
        environ = {**ENVIRON, "ADMIN_MEMBER": ""}
        self.assertEqual(self.main("configure", environ=environ), 1)
        self.assertIn("ADMIN_MEMBER is not set", self.errors)
        self.assertNotIn("example-state", self.errors + self.output)

    def test_masks_the_project_number_on_a_runner(self):
        self.main("configure", environ={**ENVIRON, "GITHUB_ACTIONS": "true"})
        self.assertIn("::add-mask::123456789", self.output)
        self.main("configure")
        self.assertNotIn("::add-mask::", self.output)


class RunnerTests(PipelineTestCase):
    def test_keys_are_private_age_keys(self):
        sops, backup = self.root / "keys/age.agekey", self.root / "keys/backup.agekey"
        self.assertEqual(self.main("keys", "--sops-key", str(sops), "--backup-key", str(backup)), 0)
        for path in (sops, backup):
            self.assertEqual(path.read_text(), KEY)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.system.ran("--secret=k8sdr-sops-age-key"))
        self.assertTrue(self.system.ran("--secret=k8sdr-backup-age-key"))

    def test_a_secret_that_is_not_an_age_key_fails(self):
        def call(argv, *, stdin=None, capture=False):
            out = next(a for a in argv if a.startswith("--out-file="))
            Path(out.split("=", 1)[1]).write_text("not a key")
            return completed()
        self.system.call = call
        args = ("--sops-key", str(self.root / "a"), "--backup-key", str(self.root / "b"))
        self.assertEqual(self.main("keys", *args), 1)
        self.assertIn("does not hold an age private key", self.errors)

    def test_the_secret_ids_match_the_shared_root(self):
        text = (ROOT / "infra/shared/pipeline.tf").read_text()
        block = re.search(r"age_key_secrets = \{(.*?)\}", text, re.S).group(1)
        declared = dict(re.findall(r'(\w+)\s*=\s*"([^"]+)"', block))
        self.assertEqual(declared, module.AGE_KEY_SECRETS)

    def test_ssh_key_is_created_once_and_registered_with_a_ttl(self):
        key = self.root / "ssh/google_compute_engine"
        module.ssh_key(self.system, key)
        self.assertTrue(self.system.ran("ssh-keygen"))
        self.assertTrue(self.system.ran(f"os-login ssh-keys add --key-file={key}.pub --ttl=6h"))
        key.write_text("existing")
        self.system.calls.clear()
        module.ssh_key(self.system, key)
        self.assertFalse(self.system.ran("ssh-keygen"))

    def test_pull_on_a_first_run_copies_nothing(self):
        self.system.answer("storage ls", completed(1, "", "ERROR: One or more URLs matched no objects.\n"))
        self.assertEqual(self.main("pull", "--drill-dir", str(self.root / ".drill")), 0)
        self.assertFalse(self.system.ran("rsync"))

    def test_pull_after_an_earlier_run_copies_its_state(self):
        self.assertEqual(self.main("pull", "--drill-dir", str(self.root / ".drill")), 0)
        self.assertIn(["gcloud", "storage", "rsync", "--recursive", "gs://example-state/pipeline/drill",
                       str(self.root / ".drill")], self.system.calls)

    def test_a_failed_listing_is_not_a_first_run(self):
        self.system.answer("storage ls", completed(1, "", "ERROR: 403 Forbidden\n"))
        self.assertEqual(self.main("pull", "--drill-dir", str(self.root / ".drill")), 1)
        self.assertFalse(self.system.ran("rsync"))

    def test_push_leaves_out_the_lock(self):
        drill = self.root / ".drill"
        drill.mkdir()
        self.assertEqual(self.main("push", "--drill-dir", str(drill)), 0)
        self.assertIn(["gcloud", "storage", "rsync", "--recursive", r"--exclude=recover\.lock$",
                       str(drill), "gs://example-state/pipeline/drill"], self.system.calls)


class WorkflowTests(unittest.TestCase):
    def test_the_workflow_passes_every_value_configure_reads(self):
        text = WORKFLOW.read_text()
        for name in module.SECRETS:
            self.assertIn(f"${{{{ secrets.{name} }}}}", text, name)
        for name in set(module.VARIABLES.values()) - set(module.SECRETS):
            self.assertIn(f"${{{{ vars.{name} }}}}", text, name)

    def test_the_job_runs_in_the_environment_with_an_identity_token(self):
        text = WORKFLOW.read_text()
        self.assertIn(f"environment: {module.ENVIRONMENT}", text)
        self.assertIn("id-token: write", text)
        self.assertNotIn("credentials_json", text)

    def test_the_stage_choices_are_the_stages_of_the_run(self):
        from scripts import recover
        text = WORKFLOW.read_text()
        options = re.search(r"options:\n((?:\s+- \S+\n)+)", text).group(1).split()
        self.assertEqual([o for o in options if o != "-"], ["all", *recover.STAGE_NAMES])


class SetupTests(PipelineTestCase):
    def setUp(self):
        super().setUp()
        (self.root / "infra/shared/terraform.tfvars").write_text(
            'project_id = "example-project"\nadmin_member = "user:admin@example.com"\n'
            'recovery_reader_member = "user:reader@example.com"\n')
        (self.root / "infra/recovery/terraform.tfvars").write_text('recovery_subnet_cidr = "10.20.0.0/24"\n')
        (self.root / "infra/shared/backend.hcl").write_text('bucket = "example-state"\nprefix = "shared"\n')
        self.keys = {name: self.root / f"{name}.agekey" for name in ("sops", "backup")}
        for path in self.keys.values():
            path.write_text(KEY)
        self.system.answer("output -raw pipeline_workload_identity_provider",
                           completed(0, ENVIRON["GCP_WORKLOAD_IDENTITY_PROVIDER"]))
        self.system.answer("output -raw pipeline_service_account",
                           completed(0, "k8sdr-pipeline@example-project.iam.gserviceaccount.com"))
        self.system.answer("output -raw backup_bucket_name", completed(0, "example-backups"))
        self.system.answer("deployment-branch-policies", completed(0, json.dumps({"branch_policies": []})))
        self.system.answer("versions list", completed(0, ""))

    def setup(self):
        return self.main("setup", "--sops-key", str(self.keys["sops"]),
                         "--backup-key", str(self.keys["backup"]))

    def test_sets_every_secret_on_stdin_and_never_in_an_argument(self):
        self.assertEqual(self.setup(), 0, self.errors)
        values = {"example-project", "example-state", "example-backups", "user:admin@example.com",
                  "user:reader@example.com", ENVIRON["GCP_WORKLOAD_IDENTITY_PROVIDER"]}
        for argv in self.system.calls:
            if argv[:3] == ["gh", "secret", "set"]:
                self.assertFalse(values & set(argv), argv)
        secrets = {argv[3] for argv in self.system.calls if argv[:3] == ["gh", "secret", "set"]}
        self.assertEqual(secrets, set(module.SECRETS))
        self.assertTrue(self.system.ran("deployment-branch-policies -f name=main -f type=branch"))
        self.assertEqual(sum("versions add" in " ".join(argv) for argv in self.system.calls), 2)
        self.assertNotIn(KEY.strip(), self.output + self.errors)

    def test_an_environment_open_to_other_branches_fails(self):
        policies = {"branch_policies": [{"type": "branch", "name": "main"}, {"type": "branch", "name": "*"}]}
        self.system.answer("deployment-branch-policies", completed(0, json.dumps(policies)))
        self.assertEqual(self.setup(), 1)
        self.assertFalse(self.system.ran("gh secret set"))

    def test_a_stored_key_that_differs_fails(self):
        self.system.answer("versions list", completed(0, "1\n"))
        self.system.answer("versions access latest", completed(0, "AGE-SECRET-KEY-1OTHER\n"))
        self.assertEqual(self.setup(), 1)
        self.assertIn("holds a different key", self.errors)
        self.assertFalse(self.system.ran("versions add"))

    def test_a_stored_key_that_matches_is_kept(self):
        self.system.answer("versions list", completed(0, "1\n"))
        self.system.answer("versions access latest", completed(0, KEY))
        self.assertEqual(self.setup(), 0, self.errors)
        self.assertFalse(self.system.ran("versions add"))

    def test_a_placeholder_left_in_a_local_file_fails(self):
        (self.root / "infra/recovery/terraform.tfvars").write_text(
            'recovery_subnet_cidr = "REPLACE_WITH_UNUSED_PRIVATE_CIDR"\n')
        self.assertEqual(self.setup(), 1)
        self.assertIn("no local value for RECOVERY_SUBNET_CIDR", self.errors)


class TerraformTests(unittest.TestCase):
    """The grants of the pipeline account in infra/shared/pipeline.tf."""

    text = (ROOT / "infra/shared/pipeline.tf").read_text()

    def block(self, pattern):
        return re.search(pattern + r"(.*?)\n\s*\]\)?\n", self.text, re.S).group(1)

    def test_no_project_role_can_change_iam_or_backups(self):
        roles = re.findall(r'"(roles/[^"]+)"', self.block(r"pipeline_project_roles = toset\(\["))
        self.assertIn("roles/compute.instanceAdmin.v1", roles)
        forbidden = re.compile(r"roles/(owner|editor|iam\.(securityAdmin|serviceAccount\w*|roleAdmin|"
                               r"workloadIdentityPoolAdmin)|resourcemanager\.\w+|storage\.\w+|"
                               r"secretmanager\.\w+)$")
        self.assertEqual([role for role in roles if forbidden.match(role)], [])

    def test_only_main_in_the_environment_of_this_repository_signs_in(self):
        condition = self.block(r"attribute_condition = join\(\" && \", \[")
        self.assertIn("assertion.repository_id == '${var.github_repository_id}'", condition)
        self.assertIn("assertion.environment == '${var.github_environment}'", condition)
        self.assertIn("assertion.ref == 'refs/heads/main'", condition)

    def test_the_pipeline_uses_only_the_recovery_node_accounts(self):
        grant = re.search(r'"pipeline_can_use_recovery_node" \{(.*?)\n\}', self.text, re.S).group(1)
        self.assertIn('if startswith(key, "recovery/")', grant)

    def test_the_fence_role_only_disables_an_account(self):
        permissions = re.findall(r'"(iam\.[^"]+)"', self.block(r"permissions = \["))
        self.assertEqual(sorted(permissions), ["iam.serviceAccounts.disable", "iam.serviceAccounts.get"])

    def test_state_writes_are_limited_to_the_pipeline_prefixes(self):
        writer = re.search(r'"pipeline_state_writer" \{(.*?)\n\}', self.text, re.S).group(1)
        self.assertIn("condition {", writer)
        self.assertIn('pipeline_state_prefixes = ["recovery/", "pipeline/"]', self.text)
        self.assertEqual(module.STATE_PREFIX.split("/")[0] + "/", "pipeline/")


if __name__ == "__main__":
    unittest.main()
