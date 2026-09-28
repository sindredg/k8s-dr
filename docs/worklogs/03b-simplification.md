# Simplification after milestone 3

Status: Implemented. Cluster validation pending.

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
