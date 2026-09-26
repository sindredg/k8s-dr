from pathlib import Path
import unittest

import yaml

TASKS = Path("ansible/roles/flux/tasks/main.yml")
SYNC = Path("ansible/roles/flux/templates/flux-sync.yml.j2")


def _tasks_by_name():
    return {task["name"]: task for task in yaml.safe_load(TASKS.read_text())}


def _sync_documents():
    text = (
        SYNC.read_text()
        .replace("{{ flux_git_url }}", "https://example.invalid/repo.git")
        .replace("{{ flux_git_branch }}", "main")
        .replace("{{ flux_cluster }}", "primary")
    )
    return {document["kind"]: document for document in yaml.safe_load_all(text) if document}


class FluxBootstrapTests(unittest.TestCase):
    def test_flux_installs_from_the_pinned_release(self):
        text = TASKS.read_text()
        self.assertIn("flux2/releases/download/v{{ flux_version }}/install.yaml", text)
        self.assertNotIn("/latest/", text)

    def test_flux_runs_last_in_bootstrap(self):
        plays = yaml.safe_load(Path("ansible/playbooks/bootstrap.yml").read_text())
        self.assertEqual(plays[-1]["hosts"], "kube_control_plane")
        self.assertEqual(plays[-1]["roles"], ["flux"])

    def test_age_key_reaches_the_cluster_only_on_stdin(self):
        tasks = _tasks_by_name()
        secret = tasks["Reconcile the SOPS age key Secret"]
        self.assertTrue(secret["no_log"])
        self.assertEqual(
            secret["ansible.builtin.command"]["cmd"],
            "kubectl --kubeconfig /etc/kubernetes/admin.conf apply -f -",
        )
        self.assertIn("sops_age_key_file", secret["ansible.builtin.command"]["stdin"])
        self.assertTrue(tasks["Require an age private key in the key file"]["no_log"])
        # No task may copy or template the key onto the node.
        for task in tasks.values():
            for module in ("ansible.builtin.copy", "ansible.builtin.template"):
                self.assertNotIn("sops_age_key_file", str(task.get(module, "")))

    def test_sync_reads_the_cluster_directory_and_decrypts_with_sops(self):
        documents = _sync_documents()
        self.assertEqual(documents["GitRepository"]["spec"]["ref"], {"branch": "main"})
        spec = documents["Kustomization"]["spec"]
        self.assertEqual(spec["path"], "./deploy/clusters/primary")
        self.assertTrue(spec["prune"])
        self.assertEqual(
            spec["decryption"], {"provider": "sops", "secretRef": {"name": "sops-age"}}
        )

    def test_bootstrap_target_passes_the_age_key_file(self):
        makefile = Path("Makefile").read_text()
        self.assertIn('-e sops_age_key_file="$(FLUX_AGE_KEY_FILE)"', makefile)
        self.assertIn('-e flux_git_branch="$(FLUX_GIT_BRANCH)"', makefile)
        self.assertIn("FLUX_GIT_BRANCH ?= main", makefile)
        self.assertIn("*.agekey", Path(".gitignore").read_text().splitlines())

    def test_cluster_validation_checks_flux(self):
        tasks = yaml.safe_load(Path("ansible/playbooks/validate_cluster.yml").read_text())[0]["tasks"]
        by_name = {task["name"]: task for task in tasks}
        self.assertEqual(by_name["Wait for the Flux controllers"]["loop"], "{{ flux_controllers }}")
        self.assertEqual(
            by_name["Wait for the Flux Git source and cluster Kustomization"]["loop"],
            ["gitrepository/flux-system", "kustomization/flux-system"],
        )


class DeployTreeTests(unittest.TestCase):
    def test_every_cluster_directory_builds_from_existing_files(self):
        clusters = sorted(Path("deploy/clusters").iterdir())
        self.assertTrue(clusters)
        pending = list(clusters)
        while pending:
            directory = pending.pop()
            kustomization = yaml.safe_load((directory / "kustomization.yaml").read_text())
            for resource in kustomization.get("resources", []):
                target = (directory / resource).resolve()
                with self.subTest(directory=str(directory), resource=resource):
                    self.assertTrue(target.exists())
                if target.is_dir():
                    pending.append(target)

    def test_data_namespaces_are_never_pruned(self):
        documents = yaml.safe_load_all(Path("deploy/base/namespaces.yaml").read_text())
        for namespace in (document for document in documents if document):
            with self.subTest(namespace=namespace["metadata"]["name"]):
                self.assertEqual(
                    namespace["metadata"]["annotations"]["kustomize.toolkit.fluxcd.io/prune"],
                    "disabled",
                )

    def test_secrets_in_git_are_sops_encrypted(self):
        # The repository is public, so a plaintext Secret would leak.
        for path in Path("deploy").rglob("*.yaml"):
            for document in yaml.safe_load_all(path.read_text()):
                if document and document.get("kind") == "Secret":
                    with self.subTest(path=str(path)):
                        self.assertTrue(path.name.endswith(".sops.yaml"))
                        self.assertIn("sops", document)

    def test_sops_encrypts_deploy_secrets_for_one_age_recipient(self):
        rules = yaml.safe_load(Path(".sops.yaml").read_text())["creation_rules"]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["path_regex"], r"^deploy/.*\.sops\.yaml$")
        self.assertEqual(rules[0]["encrypted_regex"], "^(data|stringData)$")
        self.assertRegex(rules[0]["age"], r"^age1[0-9a-z]{58}$")


if __name__ == "__main__":
    unittest.main()
