# Simplification after milestone 3

Status: Implemented and validated on 2026-09-29.

## Scope

Remove code that milestone 3 made redundant before milestone 4 adds backups. No behavior of the primary cluster or its services changes.

## Work completed

### Retire the disposable application

- Removed `deploy_test_app.yml`, `validate_test_app.yml`, `cleanup_test_app.yml`, `validate.yml`, the `milestone2-app.yml` manifest, their `make` targets, and their tests.
- The [bootstrap runbook](../runbooks/kubernetes-bootstrap.md) now validates with `make validate-services` and `make check-fixtures`. Gitea and the fixtures cover every check the test application made, and add public DNS, TLS, and real data. See the [ADR 0005 amendment](../decisions/0005-kubernetes-bootstrap-architecture.md#amendment-retire-the-disposable-application).
- Limitation: the service checks depend on Flux, cert-manager, and Let's Encrypt. `make validate-cluster` remains the only check without those dependencies.

### Remove repetition

- Plays that run `kubectl` or `helm` set `KUBECONFIG=/etc/kubernetes/admin.conf` once, instead of passing `--kubeconfig` on 60 commands.
- The fixture playbooks sign in to the Gitea API through play-level `module_defaults` for `ansible.builtin.uri`. The two administrator calls override the user and password. The test that requires `no_log` on every task that handles a password now also covers passwords passed through `module_defaults`.
- `scripts/prepare_ansible_inventory.py` reads the pod and service networks from `ansible/playbooks/group_vars/all.yml`, so the overlap check cannot drift from the values kubeadm uses. `make inventory` runs it with the toolchain Python, which provides PyYAML.
- `scripts/check_pins.py` checks the Traefik chart with the generic chart check.
- `scripts/run_with_iap.py` writes `known_hosts` without a redundant descriptor cleanup path.
- `infra/primary` grants the administrator `roles/iam.serviceAccountUser` on both node service accounts with one `for_each` resource. `moved` blocks keep the existing grants.

### Read facts from `ansible_facts`

- `node_prepare` and `container_runtime` read facts as `ansible_facts['...']` instead of the injected `ansible_*` variables, which ansible-core 2.24 removes. The bootstrap on 2026-09-28 printed an `INJECT_FACTS_AS_VARS` deprecation warning for each use.
- `ansible.cfg` sets `inject_facts_as_vars = False`, so any new use of an injected fact fails immediately instead of at the upgrade.

## Validation record

| Date | Command | Result |
| --- | --- | --- |
| 2026-09-28 | `make check` after retiring the disposable application | `Ran 119 tests`, `OK`. yamllint, ansible-lint, and the playbook syntax checks passed. |
| 2026-09-28 | `make check` after removing repetition | `Ran 119 tests`, `OK`. yamllint, ansible-lint, and the playbook syntax checks passed. |
| 2026-09-28 | `make check` with `no_log` removed from one fixture API task | The `no_log` test failed as expected. It passed again after the task was restored. |
| 2026-09-28 | `terraform -chdir=infra/primary plan` | Both grants reported as moved to `admin_can_use_node["control-plane"]` and `admin_can_use_node["worker"]`. `Plan: 0 to add, 0 to change, 0 to destroy.` |
| 2026-09-28 | `make pins` | All 13 checks `ok`, including `Traefik chart: traefik 41.6.0`. |
| 2026-09-28 | `make check-fixtures` | `ok=15 changed=0 unreachable=0 failed=0`. User, repository, commit `3775f53`, and issue `#1` present. |
| 2026-09-28 | `make create-fixtures` with every fixture present | The administrator and fixture-user lookups returned `ok`; every create step was skipped. The report shows all four fixtures `already present`. |
| 2026-09-28 | `make validate-cluster` | `ok=10 changed=0 unreachable=0 failed=0`. Commands ran as plain `kubectl` with `KUBECONFIG` from the play. Both nodes `Ready`; Flux last applied `main@sha1:d9e4772`. `run_with_iap: started 2026-09-28T22:00:10Z, finished 2026-09-28T22:00:55Z, elapsed 44s, exit 0`. |
| 2026-09-28 | `make bootstrap` | control-plane `ok=71 changed=14 unreachable=0 failed=0 skipped=8`; worker `ok=55 changed=0 unreachable=0 failed=0 skipped=9`. The `changed` tasks are the `kubectl apply`, `helm upgrade --install`, and annotate steps that always report a change. kubeadm did not initialize or join again, and the worker disk was not formatted. `run_with_iap: started 2026-09-28T22:03:04Z, finished 2026-09-28T22:05:33Z, elapsed 146s, exit 0`. |
| 2026-09-28 | `make validate-services` after the bootstrap | `ok=13 changed=0 unreachable=0 failed=0`. `run_with_iap: started 2026-09-28T22:27:27Z, finished 2026-09-28T22:28:15Z, elapsed 48s, exit 0`. |
| 2026-09-29 | `make check` after the fact change | `Ran 119 tests`, `OK`. |
| 2026-09-29 | Local play with `gather_facts: true` under the project `ansible.cfg` | `ansible_facts` returned `distribution`, `distribution_version`, `distribution_release`, `architecture`, and `swaptotal_mb`; `ansible_distribution is defined` was `false`. |
| 2026-09-29 | `make bootstrap` with `inject_facts_as_vars = False` | control-plane `ok=71 changed=14 unreachable=0 failed=0 skipped=8`; worker `ok=55 changed=0 unreachable=0 failed=0 skipped=9`. The platform assertion passed on both nodes, the swap task was skipped, and the output had no `INJECT_FACTS_AS_VARS` warnings. `run_with_iap: started 2026-09-28T23:00:59Z, finished 2026-09-28T23:03:44Z, elapsed 165s, exit 0`. |
