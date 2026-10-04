import shutil
import unittest

from scripts import check_manifests as module


def _configmap(namespace=None, annotations=None, data=None):
    metadata = {"name": "example"}
    if namespace:
        metadata["namespace"] = namespace
    if annotations:
        metadata["annotations"] = annotations
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": metadata, "data": data or {}}


class NamespaceCheckTests(unittest.TestCase):
    def test_rejects_a_namespaced_object_without_a_namespace(self):
        # Regression: kubectl kustomize built a generated ConfigMap without a
        # namespace, and Flux rejected it only on the cluster.
        self.assertEqual(module.missing_namespaces([_configmap()]), ["ConfigMap/example"])

    def test_accepts_a_namespace_or_a_target_namespace(self):
        self.assertEqual(module.missing_namespaces([_configmap("gitea")]), [])
        self.assertEqual(module.missing_namespaces([_configmap()], target_namespace="gitea"), [])

    def test_cluster_scoped_kinds_need_no_namespace(self):
        namespace = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "gitea"}}
        self.assertEqual(module.missing_namespaces([namespace]), [])

    def test_a_render_without_a_namespace_fails_the_cluster(self):
        settings = _configmap("flux-system", data={"git_host": "example.invalid"})
        settings["metadata"]["name"] = "cluster-settings"
        sync = {
            "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
            "kind": "Kustomization",
            "metadata": {"name": "broken", "namespace": "flux-system"},
            "spec": {"path": "./broken"},
        }

        def build(path):
            return [_configmap()] if str(path) == "./broken" else [settings, sync]

        with self.assertRaisesRegex(ValueError, "./broken: no namespace on ConfigMap/example"):
            module.render_cluster("primary", build=build)


class SubstitutionTests(unittest.TestCase):
    def test_unquoted_placeholder_becomes_a_boolean(self):
        document = {"metadata": {"name": "job"}, "spec": {"suspend": "${backup_suspend}"}}
        self.assertIs(module.substitute(document, {"backup_suspend": "true"})["spec"]["suspend"], True)

    def test_placeholder_inside_a_string_stays_a_string(self):
        document = {"metadata": {"name": "release"}, "spec": {"url": "https://${git_host}/"}}
        self.assertEqual(
            module.substitute(document, {"git_host": "example.invalid"})["spec"]["url"],
            "https://example.invalid/",
        )

    def test_undefined_variable_fails(self):
        document = {"metadata": {"name": "route"}, "spec": {"hostname": "${missing}"}}
        with self.assertRaisesRegex(ValueError, "undefined variables: missing"):
            module.substitute(document, {})

    def test_annotated_objects_are_left_alone(self):
        # The backup script uses shell variables that Flux must not touch.
        document = _configmap(
            "gitea",
            annotations={"kustomize.toolkit.fluxcd.io/substitute": "disabled"},
            data={"backup.sh": "echo ${BACKUP_CLUSTER}"},
        )
        self.assertEqual(module.substitute(document, {}), document)

    def test_sops_metadata_is_dropped(self):
        secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "s"}, "sops": {"mac": "x"}}
        self.assertNotIn("sops", module.without_sops_metadata(secret))


@unittest.skipUnless(shutil.which("kubectl"), "kubectl is not installed")
class RepositoryRenderTests(unittest.TestCase):
    def test_every_cluster_renders_every_flux_path(self):
        for cluster in ("primary", "recovery"):
            with self.subTest(cluster=cluster):
                renders = {render.name: render for render in module.render_cluster(cluster)}
                self.assertEqual(
                    set(renders),
                    {
                        "flux-system", "infrastructure", "certificates", "postgresql",
                        "gitea", "monitoring", "postgresql-restore", "gitea-restore",
                    },
                )
                cronjob = next(d for d in renders["gitea"].documents if d["kind"] == "CronJob")
                # The settings test pins each cluster's value; this checks that it renders.
                settings = Path("deploy/clusters", cluster, "cluster-settings.yaml").read_text()
                self.assertIs(cronjob["spec"]["suspend"], 'backup_suspend: "true"' in settings)
                self.assertNotIn("${", str(renders["certificates"].documents))


if __name__ == "__main__":
    unittest.main()
