from pathlib import Path
import re
import unittest

import yaml

DEPLOY = Path("deploy")
APPS = DEPLOY / "apps"
CLUSTER = DEPLOY / "clusters/primary"
SYNC = DEPLOY / "sync/sync.yaml"


def _documents(path):
    return [document for document in yaml.safe_load_all(path.read_text()) if document]


def _by_name(path):
    return {document["metadata"]["name"]: document for document in _documents(path)}


def _sync():
    return _by_name(SYNC)


def _duration_seconds(value):
    number, unit = int(value[:-1]), value[-1]
    return number * {"s": 1, "m": 60, "h": 3600}[unit]


class ClusterSettingsTests(unittest.TestCase):
    def test_primary_uses_production_certificates_for_its_host(self):
        settings = _by_name(CLUSTER / "cluster-settings.yaml")["cluster-settings"]
        self.assertEqual(settings["metadata"]["namespace"], "flux-system")
        self.assertEqual(
            settings["data"],
            {
                "git_host": "git.sindrg.com",
                "git_cluster_host": "git-primary.sindrg.com",
                "git_issuer": "letsencrypt-production",
                "backup_cluster": "primary",
                "backup_suspend": "false",
                "cluster_name": "primary",
            },
        )

    def test_every_substituted_variable_is_defined(self):
        # Flux leaves an undefined ${var} in place, which would publish a
        # literal placeholder as the hostname.
        defined = set(_by_name(CLUSTER / "cluster-settings.yaml")["cluster-settings"]["data"])
        substituted = {
            kustomization["spec"]["path"]
            for kustomization in _sync().values()
            if "postBuild" in kustomization["spec"]
        }
        self.assertEqual(substituted, {"./deploy/certificates", "./deploy/apps/gitea", "./deploy/monitoring"})
        for directory in substituted:
            for path in Path(directory).rglob("*.yaml"):
                for variable in re.findall(r"\$\{(\w+)\}", path.read_text()):
                    with self.subTest(path=str(path), variable=variable):
                        self.assertIn(variable, defined)

    def test_certificate_takes_host_and_issuer_from_settings(self):
        certificate = _by_name(DEPLOY / "certificates/git-certificate.yaml")["git-tls"]
        self.assertEqual(certificate["metadata"]["namespace"], "gitea")
        self.assertEqual(certificate["spec"]["dnsNames"], ["${git_host}"])
        self.assertEqual(certificate["spec"]["issuerRef"]["name"], "${git_issuer}")

    def test_each_hostname_has_its_own_certificate(self):
        # Let's Encrypt limits certificates per exact set of names, so a
        # shared certificate would tie the public name to every cluster build.
        certificates = _by_name(DEPLOY / "certificates/git-certificate.yaml")
        self.assertEqual(
            {name: c["spec"]["dnsNames"] for name, c in certificates.items()},
            {"git-tls": ["${git_host}"], "git-cluster-tls": ["${git_cluster_host}"]},
        )
        for certificate in certificates.values():
            self.assertEqual(certificate["metadata"]["namespace"], "gitea")
            self.assertEqual(certificate["spec"]["secretName"], certificate["metadata"]["name"])
            self.assertEqual(certificate["spec"]["issuerRef"]["name"], "${git_issuer}")


class ServiceKustomizationTests(unittest.TestCase):
    def test_gitea_waits_for_postgresql_and_certificates(self):
        sync = _sync()
        gitea = sync["gitea"]["spec"]
        self.assertEqual(
            gitea["dependsOn"], [{"name": "postgresql"}, {"name": "certificates"}]
        )
        self.assertTrue(gitea["wait"])
        self.assertTrue(sync["postgresql"]["spec"]["wait"])

    def test_service_kustomizations_decrypt_secrets(self):
        for name in ("postgresql", "gitea"):
            with self.subTest(name=name):
                self.assertEqual(
                    _sync()[name]["spec"]["decryption"],
                    {"provider": "sops", "secretRef": {"name": "sops-age"}},
                )

    def test_root_kustomization_outlasts_the_service_chain(self):
        # flux-system waits for the Kustomizations it applies, and Ansible
        # waits for flux-system during bootstrap.
        # gitea starts after postgresql and certificates; certificates does not
        # wait, and postgresql runs alongside infrastructure.
        sync = _sync()
        timeout = {name: _duration_seconds(sync[name]["spec"]["timeout"]) for name in sync}
        chain = max(timeout["infrastructure"], timeout["postgresql"]) + timeout["gitea"]
        template = Path("ansible/roles/flux/templates/flux-sync.yml.j2").read_text()
        root = _duration_seconds(re.search(r"timeout: (\d+[smh])", template).group(1))
        self.assertGreaterEqual(root, chain)
        tasks = {
            task["name"]: task
            for task in yaml.safe_load(Path("ansible/roles/flux/tasks/main.yml").read_text())
        }
        wait = tasks["Wait for Flux to apply the cluster configuration"]
        self.assertEqual(wait["retries"] * wait["delay"], root)


class PostgresqlTests(unittest.TestCase):
    def setUp(self):
        self.statefulset = _by_name(APPS / "postgresql/statefulset.yaml")["postgresql"]
        self.pod = self.statefulset["spec"]["template"]["spec"]

    def test_image_is_postgresql_18_pinned_by_digest(self):
        for container in self.pod["initContainers"] + self.pod["containers"]:
            with self.subTest(container=container["name"]):
                self.assertRegex(container["image"], r"^postgres:18\.\d+-\w+@sha256:[0-9a-f]{64}$")

    def test_data_volume_uses_the_postgresql_18_layout_on_local_path(self):
        # PostgreSQL 18 images keep PGDATA under /var/lib/postgresql/18/.
        container = self.pod["containers"][0]
        self.assertEqual(
            container["volumeMounts"], [{"name": "data", "mountPath": "/var/lib/postgresql"}]
        )
        claim = self.statefulset["spec"]["volumeClaimTemplates"][0]
        self.assertEqual(claim["metadata"]["name"], "data")
        self.assertEqual(claim["spec"]["storageClassName"], "local-path")
        self.assertEqual(self.statefulset["spec"]["replicas"], 1)
        self.assertEqual(self.pod["nodeSelector"], {"node-role.kubernetes.io/worker": ""})

    def test_server_runs_as_the_postgres_user(self):
        context = self.pod["containers"][0]["securityContext"]
        self.assertEqual((context["runAsUser"], context["runAsGroup"]), (999, 999))
        self.assertTrue(context["runAsNonRoot"])
        self.assertFalse(context["allowPrivilegeEscalation"])

    def test_password_comes_from_the_encrypted_secret(self):
        env = {item["name"]: item for item in self.pod["containers"][0]["env"]}
        self.assertEqual(
            env["POSTGRES_PASSWORD"]["valueFrom"]["secretKeyRef"],
            {"name": "postgresql-credentials", "key": "password"},
        )
        self.assertIn("credentials.sops.yaml", _documents(APPS / "postgresql/kustomization.yaml")[0]["resources"])


class GiteaReleaseTests(unittest.TestCase):
    def setUp(self):
        self.release = _by_name(APPS / "gitea/release.yaml")["gitea"]
        self.values = self.release["spec"]["values"]

    def test_chart_is_pinned(self):
        chart = self.release["spec"]["chart"]["spec"]
        self.assertEqual((chart["chart"], chart["version"]), ("gitea", "12.7.0"))
        repository = _by_name(APPS / "gitea/repository.yaml")["gitea"]
        self.assertEqual(repository["spec"]["url"], "https://dl.gitea.com/charts/")

    def test_bitnami_dependencies_are_disabled(self):
        for dependency in ("postgresql", "postgresql-ha", "valkey", "valkey-cluster"):
            with self.subTest(dependency=dependency):
                self.assertIs(self.values[dependency]["enabled"], False)

    def test_one_replica_is_replaced_not_surged(self):
        self.assertEqual(self.values["replicaCount"], 1)
        self.assertEqual(self.values["strategy"], {"type": "Recreate"})
        self.assertEqual(self.values["persistence"]["storageClass"], "local-path")

    def test_configuration_matches_decision_0006(self):
        config = self.values["gitea"]["config"]
        self.assertEqual(config["server"]["ROOT_URL"], "https://${git_host}/")
        self.assertIs(config["server"]["DISABLE_SSH"], True)
        self.assertIs(config["service"]["DISABLE_REGISTRATION"], True)
        self.assertEqual(config["database"]["HOST"], "postgresql.postgresql.svc.cluster.local:5432")
        self.assertEqual(config["session"]["PROVIDER"], "db")
        self.assertEqual(config["cache"]["ADAPTER"], "memory")
        self.assertIs(config["mirror"]["ENABLED"], False)

    def test_credentials_come_from_encrypted_secrets(self):
        gitea = self.values["gitea"]
        self.assertEqual(gitea["admin"], {"existingSecret": "gitea-admin", "email": "admin@${git_host}"})
        self.assertNotIn("PASSWD", gitea["config"]["database"])
        self.assertEqual(
            gitea["additionalConfigFromEnvs"],
            [{
                "name": "GITEA__DATABASE__PASSWD",
                "valueFrom": {"secretKeyRef": {"name": "gitea-database", "key": "password"}},
            }],
        )
        resources = _documents(APPS / "gitea/kustomization.yaml")[0]["resources"]
        self.assertTrue({"admin.sops.yaml", "database.sops.yaml"} <= set(resources))


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.documents = {
            (document["kind"], document["metadata"]["name"]): document
            for document in _documents(APPS / "gitea/backup.yaml")
        }
        self.cronjob = self.documents["CronJob", "gitea-backup"]["spec"]
        self.pod = self.cronjob["jobTemplate"]["spec"]["template"]["spec"]
        self.kustomization = yaml.safe_load((APPS / "gitea/kustomization.yaml").read_text())
        self.script = (APPS / "gitea/backup/backup.sh").read_text()

    def test_runs_hourly_one_at_a_time_without_retries(self):
        self.assertEqual(self.cronjob["schedule"], "7 * * * *")
        self.assertEqual(self.cronjob["concurrencyPolicy"], "Forbid")
        self.assertEqual(self.cronjob["jobTemplate"]["spec"]["backoffLimit"], 0)

    def test_cluster_settings_suspend_the_cronjob(self):
        # A recovery cluster must not write a set or ping the heartbeat
        # before its data is restored. Unquoted, so Flux substitutes a boolean.
        self.assertEqual(self.cronjob["suspend"], "${backup_suspend}")
        self.assertIn("\n  suspend: ${backup_suspend}\n", (APPS / "gitea/backup.yaml").read_text())

    def test_uses_the_digest_pinned_tool_image(self):
        image = self.pod["containers"][0]["image"]
        self.assertRegex(image, r"^ghcr\.io/sindredg/k8s-dr-backup@sha256:[0-9a-f]{64}$")

    def test_mounts_the_gitea_volume_read_only(self):
        data = next(volume for volume in self.pod["volumes"] if volume["name"] == "data")
        self.assertEqual(data["persistentVolumeClaim"], {"claimName": "gitea-shared-storage", "readOnly": True})

    def test_flux_does_not_substitute_the_script(self):
        # Flux would replace the script's ${VAR} references with empty strings.
        self.assertEqual(
            self.kustomization["generatorOptions"]["annotations"],
            {"kustomize.toolkit.fluxcd.io/substitute": "disabled"},
        )
        self.assertIn("${backup_cluster}", str(self.pod["containers"][0]["env"]))

    def test_generated_configmap_names_its_namespace(self):
        # Flux rejects a namespaced object without a namespace.
        generator = self.kustomization["configMapGenerator"][0]
        self.assertEqual(generator["namespace"], "gitea")

    def test_role_can_scale_only_gitea(self):
        rules = self.documents["Role", "gitea-backup"]["rules"]
        writes = [rule for rule in rules if set(rule["verbs"]) - {"get", "list", "watch"}]
        self.assertEqual(writes, [{
            "apiGroups": ["apps"],
            "resources": ["deployments/scale"],
            "resourceNames": ["gitea"],
            "verbs": ["get", "patch", "update"],
        }])

    def test_uploads_cannot_replace_objects_and_the_manifest_is_last(self):
        self.assertIn("x-goog-if-generation-match: 0", self.script)
        self.assertIn("Content-MD5", self.script)
        uploads = re.findall(r"^\s*upload \S+ (\S+)", self.script, re.MULTILINE)
        self.assertEqual(uploads[-1], "manifest.json")

    def test_encrypts_only_to_public_keys(self):
        recipients = [
            line for line in (APPS / "gitea/backup/recipients.txt").read_text().splitlines()
            if line and not line.startswith("#")
        ]
        self.assertEqual(len(recipients), 2)
        self.assertTrue(all(re.fullmatch(r"age1[0-9a-z]{58}", line) for line in recipients))
        sops_recipient = re.search(r"age1[0-9a-z]{58}", Path(".sops.yaml").read_text()).group(0)
        self.assertNotIn(sops_recipient, recipients)


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.script = (APPS / "gitea/backup/restore.sh").read_text()
        self.playbook = yaml.safe_load(Path("ansible/playbooks/restore.yml").read_text())[0]
        self.job = Path("ansible/playbooks/templates/restore-job.yml.j2").read_text()

    def test_script_ships_in_the_configmap_under_a_fixed_name(self):
        # The playbook mounts the ConfigMap by name, outside Kustomize.
        generator = yaml.safe_load((APPS / "gitea/kustomization.yaml").read_text())["configMapGenerator"][0]
        self.assertIn("backup/restore.sh", generator["files"])
        self.assertTrue(generator["options"]["disableNameSuffixHash"])
        self.assertIn("name: gitea-backup\n", self.job)

    def test_verifies_before_the_first_destructive_step(self):
        destructive = self.script.index("DROP DATABASE")
        for check in ("sha256sum", "pg_restore --list", "tar -tzf", "--replicas=0"):
            with self.subTest(check=check):
                self.assertLess(self.script.index(check), destructive)

    def test_selects_only_sets_with_a_manifest(self):
        self.assertIn('matchGlob=${RESTORE_SOURCE}/*/manifest.json', self.script)

    def test_requires_an_explicit_target_namespace(self):
        self.assertNotIn("restore_namespace", self.playbook["vars"])
        makefile = Path("Makefile").read_text()
        self.assertRegex(makefile, r"(?m)^RESTORE_NAMESPACE \?=$")

    def test_job_uses_the_backup_identity_and_image(self):
        self.assertIn("serviceAccountName: gitea-backup", self.job)
        self.assertIn("app.kubernetes.io/name: gitea-backup", self.job)
        self.assertIn("backup.yaml", self.playbook["vars"]["restore_image"])

    def test_backups_suspended_by_the_cluster_settings_stay_suspended(self):
        # A recovery cluster enables backups with a reviewed change to its
        # settings, never as a side effect of the restore.
        names = [task["name"] for task in self.playbook["tasks"]]
        self.assertLess(
            names.index("Read whether backups are already suspended"),
            names.index("Restore with backups paused"),
        )
        block = next(task for task in self.playbook["tasks"] if "block" in task)
        resume = next(task for task in block["always"] if task["name"].startswith("Resume"))
        self.assertIn('restore_cronjob_suspended.stdout != "true"', resume["when"])

    def test_key_secret_is_deleted_on_every_exit(self):
        block = next(task for task in self.playbook["tasks"] if "block" in task)
        self.assertTrue(any(
            "delete secret/{{ restore_key_secret }}" in str(task.get("ansible.builtin.command"))
            for task in block["always"]
        ))


class RestoreTestEnvironmentTests(unittest.TestCase):
    ROOT = DEPLOY / "restore-test"

    def overlay(self, name):
        return yaml.safe_load((self.ROOT / name / "kustomization.yaml").read_text())

    def test_overlays_copy_the_service_into_restore_namespaces(self):
        for name in ("postgresql", "gitea"):
            with self.subTest(name=name):
                overlay = self.overlay(name)
                self.assertEqual(overlay["namespace"], f"{name}-restore")
                self.assertEqual(overlay["resources"], [f"../../apps/{name}"])

    def test_gitea_copy_has_no_public_route_and_writes_no_backups(self):
        deleted = {
            patch["target"].get("kind", patch["target"].get("group"))
            for patch in self.overlay("gitea")["patches"]
            if "$patch: delete" in patch["patch"]
        }
        self.assertEqual(deleted, {"Secret", "gateway.networking.k8s.io", "CronJob"})

    def test_cross_namespace_references_point_at_the_copies(self):
        text = (self.ROOT / "gitea/kustomization.yaml").read_text()
        self.assertIn("postgresql.postgresql-restore.svc.cluster.local:5432", text)
        self.assertIn("value: postgresql-restore", text)
        self.assertIn("value: gitea-restore", (self.ROOT / "postgresql/kustomization.yaml").read_text())

    def test_flux_applies_the_overlays_only_on_request(self):
        kustomizations = _by_name(self.ROOT / "flux.yaml")
        self.assertEqual(
            {name: k["spec"]["path"] for name, k in kustomizations.items()},
            {
                "postgresql-restore": "./deploy/restore-test/postgresql",
                "gitea-restore": "./deploy/restore-test/gitea",
            },
        )
        self.assertTrue(all(k["spec"]["prune"] for k in kustomizations.values()))
        self.assertNotIn("restore-test", SYNC.read_text())


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.documents = {
            (document["kind"], document["metadata"]["name"]): document
            for document in _documents(APPS / "gitea/gateway.yaml")
        }

    def test_https_listener_terminates_with_the_gitea_certificate(self):
        listeners = {
            listener["name"]: listener
            for listener in self.documents["Gateway", "gitea"]["spec"]["listeners"]
        }
        # Traefik's web and websecure entry points listen on 8000 and 8443.
        self.assertEqual((listeners["http"]["port"], listeners["https"]["port"]), (8000, 8443))
        https = listeners["https"]
        self.assertEqual(https["hostname"], "${git_host}")
        self.assertEqual(
            https["tls"], {"mode": "Terminate", "certificateRefs": [{"kind": "Secret", "name": "git-tls"}]}
        )

    def test_http_redirects_and_https_reaches_gitea(self):
        redirect = self.documents["HTTPRoute", "gitea-https-redirect"]["spec"]
        self.assertEqual(
            redirect["parentRefs"],
            [{"name": "gitea", "sectionName": "http"}, {"name": "gitea", "sectionName": "http-cluster"}],
        )
        self.assertEqual(
            redirect["rules"][0]["filters"][0]["requestRedirect"], {"scheme": "https", "statusCode": 301}
        )
        route = self.documents["HTTPRoute", "gitea"]["spec"]
        self.assertEqual(
            route["parentRefs"],
            [{"name": "gitea", "sectionName": "https"}, {"name": "gitea", "sectionName": "https-cluster"}],
        )
        self.assertEqual(route["rules"][0]["backendRefs"], [{"name": "gitea-http", "port": 3000}])
        for spec in (redirect, route):
            self.assertEqual(spec["hostnames"], ["${git_host}", "${git_cluster_host}"])

    def test_cluster_host_has_its_own_listeners_and_certificate(self):
        # The per-cluster name reaches one cluster whatever the public name
        # resolves to. See decision 0008.
        listeners = {
            listener["name"]: listener
            for listener in self.documents["Gateway", "gitea"]["spec"]["listeners"]
        }
        self.assertEqual(set(listeners), {"http", "https", "http-cluster", "https-cluster"})
        self.assertEqual(listeners["http-cluster"]["hostname"], "${git_cluster_host}")
        self.assertEqual(listeners["http-cluster"]["port"], 8000)
        https = listeners["https-cluster"]
        self.assertEqual((https["hostname"], https["port"]), ("${git_cluster_host}", 8443))
        self.assertEqual(
            https["tls"],
            {"mode": "Terminate", "certificateRefs": [{"kind": "Secret", "name": "git-cluster-tls"}]},
        )


class NetworkPolicyTests(unittest.TestCase):
    def policies(self, namespace):
        return _by_name(APPS / namespace / "network-policies.yaml")

    def test_both_namespaces_deny_by_default(self):
        for namespace in ("postgresql", "gitea"):
            with self.subTest(namespace=namespace):
                deny = self.policies(namespace)["default-deny"]
                self.assertEqual(deny["metadata"]["namespace"], namespace)
                self.assertEqual(deny["spec"], {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]})

    @staticmethod
    def _peer(peer):
        if "ipBlock" in peer:
            return peer["ipBlock"]["cidr"]
        namespace = peer["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
        labels = peer["podSelector"].get("matchLabels", {})
        return f"{namespace}/{labels.get('app.kubernetes.io/name', labels.get('k8s-app', '*'))}"

    def test_only_listed_flows_are_allowed(self):
        flows = set()
        for namespace in ("postgresql", "gitea"):
            for name, policy in self.policies(namespace).items():
                pods = policy["spec"]["podSelector"].get("matchLabels", {}).get("app.kubernetes.io/name", "*")
                for direction, peers_key in (("ingress", "from"), ("egress", "to")):
                    for rule in policy["spec"].get(direction, []):
                        # A rule without peers allows any address.
                        for peer in rule.get(peers_key, [None]):
                            other = "any" if peer is None else self._peer(peer)
                            for port in rule["ports"]:
                                flows.add((f"{namespace}/{pods}", direction, other, port["protocol"], port["port"]))
        self.assertEqual(
            flows,
            {
                ("postgresql/postgresql", "ingress", "gitea/gitea", "TCP", 5432),
                ("postgresql/postgresql", "ingress", "gitea/gitea-backup", "TCP", 5432),
                ("postgresql/*", "egress", "kube-system/kube-dns", "UDP", 53),
                ("postgresql/*", "egress", "kube-system/kube-dns", "TCP", 53),
                ("gitea/gitea", "ingress", "traefik/traefik", "TCP", 3000),
                ("gitea/gitea", "egress", "postgresql/postgresql", "TCP", 5432),
                ("gitea/*", "egress", "kube-system/kube-dns", "UDP", 53),
                ("gitea/*", "egress", "kube-system/kube-dns", "TCP", 53),
                # The backup Job: database, metadata server token, Cloud
                # Storage, Healthchecks.io, and the Kubernetes API.
                ("gitea/gitea-backup", "egress", "postgresql/postgresql", "TCP", 5432),
                ("gitea/gitea-backup", "egress", "169.254.169.254/32", "TCP", 80),
                ("gitea/gitea-backup", "egress", "any", "TCP", 443),
                ("gitea/gitea-backup", "egress", "any", "TCP", 6443),
            },
        )

    def test_every_peer_names_a_namespace_and_pods(self):
        # A namespaceSelector alone would admit every pod in that namespace.
        for namespace in ("postgresql", "gitea"):
            for name, policy in self.policies(namespace).items():
                for rule in policy["spec"].get("ingress", []) + policy["spec"].get("egress", []):
                    for peer in rule.get("from", []) + rule.get("to", []):
                        if "ipBlock" in peer:
                            continue
                        with self.subTest(namespace=namespace, policy=name):
                            self.assertIn("podSelector", peer)
                            self.assertIn("namespaceSelector", peer)

    def test_address_peers_are_single_hosts(self):
        for namespace in ("postgresql", "gitea"):
            for name, policy in self.policies(namespace).items():
                for rule in policy["spec"].get("egress", []):
                    for peer in rule.get("to", []):
                        if "ipBlock" in peer:
                            with self.subTest(namespace=namespace, policy=name):
                                self.assertTrue(peer["ipBlock"]["cidr"].endswith("/32"))
                                self.assertNotIn("except", peer["ipBlock"])


if __name__ == "__main__":
    unittest.main()
