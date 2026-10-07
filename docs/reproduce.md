# Reproduce the project

Status: Not tested from a fork. The project was built once, in one set of accounts. This page lists what those runs depended on, so that a second build knows what to prepare and change. Expect to find gaps.

## What you need

| Requirement | Used for |
| --- | --- |
| A Google Cloud project with billing | VMs, network, load balancer, buckets, and the uptime check |
| A domain in a Cloudflare zone | The public name, the per-cluster names, and DNS-01 certificates |
| A GitHub repository that Flux can read without credentials | The source of everything Flux deploys |
| A Grafana Cloud stack | Cluster metrics; optional if you remove `deploy/monitoring` |
| A Healthchecks.io check | The backup heartbeat |
| Three age key pairs | SOPS secrets, backup encryption, and an offline second backup recipient |
| An operator machine with `terraform`, `gcloud`, `sops`, `age`, `jq`, `curl`, `dig`, Python 3, and `make` | Every command in the runbooks |

Two VMs, a load balancer, a NAT gateway, and the buckets bill for as long as they exist. A drill doubles the VMs for under an hour. See [cost planning](runbooks/primary-infrastructure.md#cost-planning).

## What to change

| Change | Where |
| --- | --- |
| The domain `sindrg.com` | `deploy/clusters/*/cluster-settings.yaml`, `Makefile`, `scripts/preflight.sh`, `scripts/dns_record.sh`, `scripts/check-public-surface.sh`, `infra/primary/outputs.tf`, `infra/recovery/outputs.tf`, `infra/shared/variables.tf`, and the tests that pin it |
| The repository URL | `flux_git_url` in `ansible/playbooks/group_vars/all.yml` |
| The age recipients | `.sops.yaml` and `deploy/apps/gitea/backup/recipients.txt` |
| Project, bucket names, and members | The ignored `terraform.tfvars` and `backend.hcl` of each root, from the tracked `.example` files |
| The backup image | `ghcr.io/sindredg/k8s-dr-backup` is public and pinned by digest. Reuse it, or build `backup-image` and update the digest in `deploy/apps/gitea/backup.yaml`. |

`make check` fails until the tests that pin the domain, the recipients, and the settings match your values.

## Secrets to create again

The seven `*.sops.yaml` files are encrypted for keys you do not hold. Replace each with your own values and encrypt it with `sops`:

| File | Content |
| --- | --- |
| `deploy/certificates/cloudflare-api-token.sops.yaml` | A Cloudflare token with Zone Read and DNS Edit on your zone |
| `deploy/apps/postgresql/credentials.sops.yaml` | The PostgreSQL user and password |
| `deploy/apps/gitea/database.sops.yaml` | The same database credentials, for Gitea |
| `deploy/apps/gitea/admin.sops.yaml` | The Gitea administrator |
| `deploy/apps/gitea/backup.sops.yaml` | The Healthchecks.io ping URL |
| `deploy/monitoring/grafana-cloud-metrics.sops.yaml` | The Grafana Cloud push URL, user, and token |
| `recovery/fixtures.sops.yaml` | The password of the fixture user |

The key names in each file are readable; only the values are encrypted. Keep the names and replace the values.

## Order

1. [Primary infrastructure](runbooks/primary-infrastructure.md): the state bucket, then the shared and primary roots.
2. [Kubernetes bootstrap](runbooks/kubernetes-bootstrap.md): `make venv`, `make inventory`, `make bootstrap`, `make validate-cluster`.
3. [Service deployment](runbooks/service-deployment.md): the public endpoint, DNS records, Flux with the SOPS key, certificates, PostgreSQL, Gitea, and `make create-fixtures`.
4. [Backup and restore](runbooks/backup-restore.md): confirm that a set appears, then restore one into the test namespaces.
5. [Regional recovery](runbooks/regional-recovery.md): `make preflight`, then the drill and the return to the primary.

Each runbook states its expected output. The [worklogs](worklogs/) show what the same steps printed here, including the failures.

## Remove it

1. Remove the recovery environment if one exists, as the [regional recovery runbook](runbooks/regional-recovery.md#remove-the-recovery-environment) describes.
2. Destroy `infra/primary`. The worker data disk has `prevent_destroy`; remove that setting first.
3. Destroy `infra/shared`. The backup bucket has `prevent_destroy` and a 14-day retention policy: remove the setting, then remove the policy and empty the bucket, or wait until the sets expire.
4. Destroy `infra/bootstrap` last: it holds the state of the other roots, and its bucket also has `prevent_destroy`.
5. Delete the DNS records, revoke the Cloudflare and Grafana Cloud tokens, and pause the Healthchecks.io check.
