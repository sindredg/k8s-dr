# Service hardening before milestone 6

Status: Implemented; validation pending.

## Scope

Close the review findings that need no new cluster component, and add a daily check of the public surface. [Decision 0010](../decisions/0010-service-hardening.md) records the design. This is not a milestone; it adds no recovery step.

## Review on 2026-10-01

Read-only checks against the running primary service.

| Check | Command | Result |
| --- | --- | --- |
| Open ports | TCP connect to 22, 6443, 10250, 2379, 3000, 5432, and 30080 on the public address | None answered; 80 and 443 answered |
| VM addresses | `gcloud compute instances list` | Neither VM has an external address |
| Firewall | `gcloud compute firewall-rules list` | The cluster network allows 80 and 443 from the internet, 22 from the IAP range, and node-to-node traffic |
| TLS | `openssl s_client` with each version | 1.0 and 1.1 refused; 1.2 and 1.3 accepted |
| Anonymous API | `curl https://git.sindrg.com/api/v1/users/search` | 200, listing `gitea-admin` and `recovery-fixture` |
| Registration | `curl https://git.sindrg.com/user/sign_up` | "Registration is disabled" |
| Service account keys | `gcloud iam service-accounts keys list --managed-by=user` for each account | None |
| Branch rules | `gh api repos/sindredg/k8s-dr/rulesets` | `main` requires a pull request and blocks force-push and deletion; 0 approvals required |

The first run of the new script, before any change was deployed:

```text
$ scripts/check-public-surface.sh
Checking the public surface of git.sindrg.com

ok        tls10-refused    server refused tls1
ok        tls11-refused    server refused tls1_1
ok        tls12-accepted   server accepted tls1_2
ok        tls13-accepted   server accepted tls1_3
ok        http-redirect    plain HTTP redirects to HTTPS
known     hsts             strict-transport-security missing on / /user/login
ok        nosniff          x-content-type-options present on every path
ok        frame-options    x-frame-options present on every path
ok        cert-expiry      85 days remaining
known     anonymous-api    the API lists users to anonymous requests
ok        registration     registration is disabled
ok        closed-ports     no answer on 22 6443 10250 30080
known     caa              no CAA record
ok        dnssec           the zone is signed

11 ok, 3 known open, 0 regressed, 0 resolved, 0 inconclusive
```

The cluster itself was not inspected; in-cluster state was read from the manifests.

## Work completed

- Set `REQUIRE_SIGNIN_VIEW: true` in the Gitea release.
- Added `deploy/infrastructure/traefik` with an egress policy for the `traefik` namespace: DNS, the Kubernetes API, and Gitea only.
- Added a `deny-metadata-server` egress policy to `cert-manager` and `monitoring`.
- Narrowed the node OAuth scopes in the `regional_cluster` module: `devstorage.read_write` on the worker, none on the control plane. `terraform plan` on the primary root shows two in-place updates and nothing else.
- Added `scripts/check-public-surface.sh`, `make surface`, and the daily `Public surface` workflow.
- Added `tests/test_hardening.py`.

## Gate

Run the `kubectl` commands on the control plane over IAP SSH.

### Cluster changes, after the merge

1. Check Flux: `kubectl -n flux-system get kustomization infrastructure gitea monitoring`. Expected: all `Ready=True` at the merged revision.
2. List the policies: `kubectl get networkpolicy -n traefik; kubectl get networkpolicy -n cert-manager; kubectl get networkpolicy -n monitoring`. Expected: `traefik-egress` and two `deny-metadata-server`.
3. Restart Traefik so that it loads its configuration under the policy: `kubectl -n traefik rollout restart deployment/traefik && kubectl -n traefik rollout status deployment/traefik`. The service is unavailable for a few seconds. Expected: `curl -s -o /dev/null -w '%{http_code}' https://git.sindrg.com/user/login` prints `200`.
4. Check that sign-in is required: `curl -s -o /dev/null -w '%{http_code}' https://git.sindrg.com/api/v1/users/search`. Expected: `401` or `403`. `https://git.sindrg.com/api/healthz` still returns `200`.
5. Run `make check-fixtures`. Expected: every check passes.
6. Check the metadata server from a policy namespace and, as a control, from one without a policy:

   ```bash
   kubectl -n monitoring run metadata-check --rm -i --restart=Never --image=busybox:1.37.0 -- \
     wget -T 5 -qO- http://169.254.169.254/
   kubectl -n default run metadata-check --rm -i --restart=Never --image=busybox:1.37.0 -- \
     wget -T 5 -qO- http://169.254.169.254/
   ```

   Expected: the first times out and the second prints the metadata directory.
7. Run `make surface`. Expected: `RESOLVED anonymous-api` and a failed exit. Remove that line from `KNOWN_OPEN`, then expect 12 ok and 2 known open.

### Node scopes

Each VM stops and restarts, so the service is unavailable for a few minutes. Avoid minute 7 of the hour, when the backup runs.

1. `terraform -chdir=infra/primary plan`. Expected: two instances updated in place, with only the scopes and `allow_stopping_for_update` changed.
2. `terraform -chdir=infra/primary apply`.
3. `make validate-cluster` and `make validate-services`. Expected: both pass.
4. `gcloud compute instances describe <instance> --zone <zone> --format='value(serviceAccounts[0].scopes)'` for each node. Expected: `devstorage.read_write` on the worker and nothing on the control plane.
5. After the next hourly backup: `kubectl -n gitea get jobs`. Expected: the newest Job is `Complete`, which proves the narrowed scope still allows the upload.

## Validation

Not run yet.
