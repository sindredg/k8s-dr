# Simplification after milestone 3

Status: Implemented. Cluster validation pending.

## Scope

Remove code that milestone 3 made redundant before milestone 4 adds backups. No behavior of the primary cluster or its services changes.

## Work completed

### Retire the disposable application

- Removed `deploy_test_app.yml`, `validate_test_app.yml`, `cleanup_test_app.yml`, `validate.yml`, the `milestone2-app.yml` manifest, their `make` targets, and their tests.
- The [bootstrap runbook](../runbooks/kubernetes-bootstrap.md) now validates with `make validate-services` and `make check-fixtures`. Gitea and the fixtures cover every check the test application made, and add public DNS, TLS, and real data. See the [ADR 0005 amendment](../decisions/0005-kubernetes-bootstrap-architecture.md#amendment-retire-the-disposable-application).
- Limitation: the service checks depend on Flux, cert-manager, and Let's Encrypt. `make validate-cluster` remains the only check without those dependencies.

## Validation record

| Date | Command | Result |
| --- | --- | --- |
| 2026-09-28 | `make check` | `Ran 119 tests`, `OK`. yamllint, ansible-lint, and the playbook syntax checks passed. |
