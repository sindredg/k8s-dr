# Service deployment: operator procedure

Deploy the milestone 3 service in the order of [decision 0006](../decisions/0006-service-deployment-architecture.md). Each section ends with the checks that must pass before the next section starts. Record observed results in the [milestone 3 worklog](../worklogs/03-service-deployment.md).

## Prerequisites

- The primary cluster passes `make validate-cluster`.
- The SOPS age private key and the Cloudflare DNS token are in the external credential store, and each stored copy was tested.
- The `sindrg.com` zone is active in Cloudflare.

## Public endpoint

Terraform adds a static external address and a regional external passthrough load balancer that forwards TCP 80 and 443 to the worker. Traefik binds host ports 80 and 443 on the worker, so the load balancer needs no port mapping. Its NodePort 30080 stays for the private validation.

The load balancer bills a forwarding rule and a static address every hour while the primary runs. See [cost planning](primary-infrastructure.md#cost-planning).

1. Plan the change from `infra/primary`. The worker gains a network tag in place.

   ```bash
   cd infra/primary
   terraform plan -out=public-web.tfplan
   ```

   Expected: `Plan: 6 to add, 1 to change, 0 to destroy.` The additions are the address, instance group, health check, backend service, forwarding rule, and firewall rule. The change is `module.primary_cluster.google_compute_instance.node["worker"]` updated in place for `tags`. Stop if any resource is replaced or destroyed.

2. Apply the saved plan and read the address.

   ```bash
   terraform apply public-web.tfplan
   rm public-web.tfplan
   PUBLIC_WEB="$(terraform output -raw public_web_address)"
   echo "$PUBLIC_WEB"
   ```

   Expected: `Apply complete! Resources: 6 added, 1 changed, 0 destroyed.`

3. Reconcile Traefik from the repository root. This run also replaces Ubuntu's `containerd` with the pinned `containerd.io` package on nodes built before that change, which restarts running containers.

   ```bash
   cd ../..
   make bootstrap
   make validate-cluster
   ```

   Expected: both recaps end with `failed=0`. The Traefik rollout completes with one pod on the worker.

4. Check the load balancer health. Replace `<name_prefix>` with the primary `name_prefix`. Probes can take a minute to pass after Traefik starts.

   ```bash
   gcloud compute backend-services get-health <name_prefix>-public-web \
     --region=europe-north1 --format='value(status.healthStatus[].healthState)'
   ```

   Expected: `HEALTHY`.

5. Request Traefik through the public address from the operator machine. No route exists yet, so Traefik answers `404`.

   ```bash
   curl -sS -o /dev/null -w '%{http_code}\n' "http://$PUBLIC_WEB/"
   curl -sSk -o /dev/null -w '%{http_code}\n' "https://$PUBLIC_WEB/"
   ```

   Expected: `404` for both. The HTTPS request uses Traefik's default self-signed certificate, so `-k` is required until cert-manager issues one.

6. Optional: prove a Gateway route works end to end through the public path with the disposable application, then remove it.

   ```bash
   make deploy-test-app
   curl -sS -H 'Host: milestone2.local' "http://$PUBLIC_WEB/"
   make cleanup-test-app
   ```

   Expected: the response contains the application's persistent marker.

7. Create the DNS record in the Cloudflare dashboard for `sindrg.com`:

   | Field | Value |
   | --- | --- |
   | Type | `A` |
   | Name | `git` |
   | IPv4 address | The value of `$PUBLIC_WEB` |
   | Proxy status | DNS only |
   | TTL | 1 min |

   DNS only keeps TLS termination in the cluster. The 60-second TTL bounds the cutover delay that [decision 0002](../decisions/0002-recovery-contract.md) measures.

8. Confirm the record resolves and reaches Traefik.

   ```bash
   dig +short git.sindrg.com @1.1.1.1
   curl -sS -o /dev/null -w '%{http_code}\n' http://git.sindrg.com/
   ```

   Expected: the public address, then `404`.

## Flux and SOPS

`make bootstrap` ends by installing the pinned Flux release, creating the `flux-system/sops-age` Secret from the operator's age private key, and applying one `GitRepository` and one `Kustomization`. Flux then reads this public repository anonymously and applies `deploy/clusters/primary`. Secrets in `deploy/` are committed encrypted with SOPS for the age public key in `.sops.yaml`.

The age private key is a recovery credential. Without it, a recovered cluster cannot decrypt any secret in Git.

1. Create the age key once. Skip this step if the key file already exists: a new key cannot decrypt secrets encrypted for the old one.

   ```bash
   mkdir -p ~/.config/k8s-dr
   age-keygen -o ~/.config/k8s-dr/age.agekey
   chmod 600 ~/.config/k8s-dr/age.agekey
   age-keygen -y ~/.config/k8s-dr/age.agekey
   ```

   Expected: the last command prints one public key that starts with `age1`. The public key is not secret; `.sops.yaml` lists it.

2. Store the private key file in the external credential store. Retrieve the stored copy to a temporary path and compare its public key.

   ```bash
   age-keygen -y <retrieved_copy>
   ```

   Expected: the same `age1` public key as step 1. Delete the retrieved copy.

3. Optional: test a pushed branch before merging. Flux reads the branch from GitHub, not the local checkout.

   ```bash
   make bootstrap FLUX_GIT_BRANCH=<branch>
   ```

   After the merge, run step 4 so Flux tracks `main` again.

4. Bootstrap from `main` and validate.

   ```bash
   make bootstrap
   make validate-cluster
   ```

   Expected: both recaps end with `failed=0`. The four Flux controller rollouts complete, `gitrepository/flux-system` and `kustomization/flux-system` are `Ready`, and the task `Show the Git revision Flux last applied` prints `main@sha1:<commit>`. Compare it with `git rev-parse origin/main`.

5. Confirm Flux created the service namespaces.

   ```bash
   gcloud compute ssh "$CONTROL_PLANE_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap \
     --command='sudo kubectl --kubeconfig /etc/kubernetes/admin.conf get namespaces gitea postgresql -L kustomize.toolkit.fluxcd.io/name'
   ```

   Expected: both namespaces are `Active` with the label value `flux-system`.

To encrypt a new secret, write it as `<name>.sops.yaml` under `deploy/` and run `sops --encrypt --in-place <file>` before `git add`. SOPS reads the recipient from `.sops.yaml`. To rotate the key, add the new public key to `.sops.yaml`, run `sops updatekeys` on every encrypted file, rerun `make bootstrap` with the new key file, and then remove the old public key.

## cert-manager and certificates

Flux installs cert-manager from its Helm chart through the `infrastructure` Kustomization. The `certificates` Kustomization runs after it and applies the SOPS-encrypted Cloudflare token, the `letsencrypt-staging` and `letsencrypt-production` ClusterIssuers, and the `git-tls` Certificate for `git.sindrg.com` in the `gitea` namespace. Both issuers solve DNS-01 through Cloudflare, so the cluster needs no inbound port for issuance.

Each cluster names its hostname and issuer in `deploy/clusters/<cluster>/cluster-settings.yaml`, and Flux substitutes them into `deploy/certificates/git-certificate.yaml`. The primary first used `letsencrypt-staging` to prove issuance, then switched to `letsencrypt-production` in the [PostgreSQL and Gitea](#postgresql-and-gitea) section. Staging certificates are not trusted by browsers. Let's Encrypt allows five production certificates for the same names each week, so set `git_issuer: letsencrypt-staging` on a test branch before a rebuild test.

The `certificates` Kustomization does not wait for its objects to become ready. A slow Let's Encrypt order therefore cannot fail `make bootstrap`; `make validate-services` checks the Certificate instead.

1. Encrypt the Cloudflare token into the repository. The token is read without echo and piped to SOPS, so the plaintext is never written to a file.

   ```bash
   read -rsp 'Cloudflare API token: ' CF_API_TOKEN; echo
   printf 'apiVersion: v1\nkind: Secret\nmetadata:\n  name: cloudflare-api-token\n  namespace: cert-manager\ntype: Opaque\nstringData:\n  api-token: %s\n' "$CF_API_TOKEN" \
     | sops --encrypt --filename-override deploy/certificates/cloudflare-api-token.sops.yaml \
         --input-type yaml --output-type yaml /dev/stdin \
     > deploy/certificates/cloudflare-api-token.sops.yaml
   unset CF_API_TOKEN
   ```

2. Check the file without printing the token.

   ```bash
   grep -c 'api-token: ENC\[' deploy/certificates/cloudflare-api-token.sops.yaml
   SOPS_AGE_KEY_FILE=~/.config/k8s-dr/age.agekey sops --decrypt deploy/certificates/cloudflare-api-token.sops.yaml >/dev/null && echo decrypts
   make check
   ```

   Expected: `1`, then `decrypts`, then passing checks. The test suite fails if any Secret under `deploy/` is not SOPS-encrypted.

3. Commit and push the branch, then point Flux at it and validate. Flux reads the branch from GitHub.

   ```bash
   make bootstrap FLUX_GIT_BRANCH=<branch>
   make validate-cluster
   make validate-services
   ```

   Expected: all three recaps end with `failed=0`. `validate-services` waits for the `infrastructure` and `certificates` Kustomizations, the `cert-manager` HelmRelease, both ClusterIssuers, and `certificate/git-tls`, then prints its issuer, the name `git.sindrg.com`, and an expiry about 90 days ahead. From the next section on, `validate-services` also checks PostgreSQL and Gitea.

4. After the merge, run `make bootstrap` so Flux tracks `main` again, then repeat both validations.

To rotate the Cloudflare token, repeat steps 1 to 3 with the new token, then revoke the old one in Cloudflare.

## PostgreSQL and Gitea

Flux applies two more Kustomizations. `postgresql` runs PostgreSQL 18 as a one-replica StatefulSet with a `local-path` volume on the worker disk. `gitea` depends on `postgresql` and `certificates`. It installs Gitea chart 12.7.0 with its Bitnami subcharts disabled, a `gitea` Gateway with an HTTP listener that redirects to HTTPS and an HTTPS listener that uses `git-tls`, and the network policies from [decision 0006](../decisions/0006-service-deployment-architecture.md#restrict-traffic-with-network-policies). The same change switches `git-tls` to `letsencrypt-production`, which issues one production certificate.

The database and administrator passwords are generated random values, committed encrypted in `deploy/apps/postgresql/credentials.sops.yaml`, `deploy/apps/gitea/database.sops.yaml`, and `deploy/apps/gitea/admin.sops.yaml`. The age key decrypts them, so they need no separate entry in the credential store.

The first Gitea install runs database migrations and can take several minutes. `make bootstrap` waits up to 15 minutes for the `flux-system` Kustomization, which waits for the others.

1. Confirm the encrypted files decrypt with the operator key, without printing them.

   ```bash
   for file in deploy/apps/postgresql/credentials.sops.yaml deploy/apps/gitea/database.sops.yaml deploy/apps/gitea/admin.sops.yaml; do
     SOPS_AGE_KEY_FILE=~/.config/k8s-dr/age.agekey sops --decrypt "$file" >/dev/null && echo "decrypts: $file"
   done
   ```

   Expected: three `decrypts:` lines.

2. Point Flux at the pushed branch and validate.

   ```bash
   make bootstrap FLUX_GIT_BRANCH=<branch>
   make validate-cluster
   make validate-services
   ```

   Expected: all three recaps end with `failed=0`. `validate-services` also waits for the `postgresql` and `gitea` Kustomizations and the `gitea` HelmRelease, the StatefulSet and Deployment rollouts, both volume claims `Bound`, the Gateway `Programmed`, both HTTPRoutes `Accepted`, and a `default-deny` policy in each namespace. It then requests Traefik's private NodePort with the Gitea host and requires a `301` to `https://git.sindrg.com/`. The certificate task prints the issuer `letsencrypt-production` and a new expiry.

3. From the operator machine, check the public endpoint. No `-k`: the production certificate must be trusted.

   ```bash
   curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' http://git.sindrg.com/
   curl -fsS https://git.sindrg.com/api/healthz
   echo | openssl s_client -connect git.sindrg.com:443 -servername git.sindrg.com 2>/dev/null \
     | openssl x509 -noout -issuer -enddate
   ```

   Expected: `301 https://git.sindrg.com/`; a JSON body with `"status": "pass"`, including the database check; an issuer with `O = Let's Encrypt` and the same expiry as step 2.

4. Sign in through the API as the administrator. The password is read into a variable and not printed.

   ```bash
   GITEA_ADMIN_PASSWORD="$(SOPS_AGE_KEY_FILE=~/.config/k8s-dr/age.agekey sops --decrypt \
     --extract '["stringData"]["password"]' deploy/apps/gitea/admin.sops.yaml)"
   curl -fsS -u "gitea-admin:$GITEA_ADMIN_PASSWORD" https://git.sindrg.com/api/v1/user \
     | python3 -c 'import json, sys; user = json.load(sys.stdin); print(user["login"], user["is_admin"])'
   unset GITEA_ADMIN_PASSWORD
   ```

   Expected: `gitea-admin True`.

5. Check the network policies from the control plane. Each command should fail.

   ```bash
   gcloud compute ssh "$CONTROL_PLANE_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap \
     --command='sudo kubectl --kubeconfig /etc/kubernetes/admin.conf run np-probe --namespace default --rm -i --restart=Never --image=busybox:1.37.0 -- nc -z -w 3 postgresql.postgresql.svc.cluster.local 5432; echo "exit=$?"'
   gcloud compute ssh "$CONTROL_PLANE_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap \
     --command='sudo kubectl --kubeconfig /etc/kubernetes/admin.conf exec --namespace gitea deploy/gitea -c gitea -- wget -q -T 5 -O /dev/null https://dl.gitea.com/; echo "exit=$?"'
   ```

   Expected: a non-zero `exit=` for both. The first shows that a pod outside `gitea` cannot reach PostgreSQL. The second shows that Gitea has no internet egress. Step 3 already showed that Traefik reaches Gitea and Gitea reaches PostgreSQL.

6. Restart both pods and check that the service returns.

   ```bash
   gcloud compute ssh "$CONTROL_PLANE_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap \
     --command='K="sudo kubectl --kubeconfig /etc/kubernetes/admin.conf"; $K delete pod --namespace postgresql postgresql-0 && $K delete pod --namespace gitea -l app.kubernetes.io/name=gitea && $K rollout status statefulset/postgresql --namespace postgresql --timeout=300s && $K rollout status deployment/gitea --namespace gitea --timeout=600s'
   curl -fsS https://git.sindrg.com/api/healthz
   ```

   Then repeat step 4.

   Expected: both rollouts complete, `healthz` passes, and the administrator signs in. The volume claims keep their data. The milestone gate repeats this check after the fixtures exist.

7. After the merge, run `make bootstrap` so Flux tracks `main` again, then repeat `make validate-cluster` and `make validate-services`.

To rotate the administrator password, replace `password` in `admin.sops.yaml` with `sops edit` and let Flux apply it; Gitea resets the password when its pod starts. To rotate the database password, run `ALTER ROLE gitea PASSWORD '<new>'` in PostgreSQL first, then update both `credentials.sops.yaml` and `database.sops.yaml`, and restart Gitea. The image reads `POSTGRES_PASSWORD` only when it creates an empty data directory.

## Recovery fixtures

The recovery checks in [decision 0002](../decisions/0002-recovery-contract.md) need known data: one user, one repository with a known commit on its default branch, and one issue. `recovery/fixtures.yaml` records their identifiers. It is tracked and not secret, so it stays available on GitHub and in every clone when the primary region is lost. The fixture user's password is in `recovery/fixtures.sops.yaml`, encrypted for the same age key as `deploy/`. See the [decision 0006 amendment](../decisions/0006-service-deployment-architecture.md#amendment-recovery-fixtures).

Three make targets run on the operator machine against the public HTTPS endpoint, not through IAP, so they check what users see. Each decrypts passwords locally with `FLUX_AGE_KEY_FILE` and hides them with `no_log`. Git runs over HTTPS and reads the password from stdin through `scripts/git_with_password.sh`, so the password never appears in command arguments or on disk.

| Target | Changes data | What it does |
| --- | --- | --- |
| `make create-fixtures` | Yes | Creates each missing fixture through the Gitea API and a real `git push`, then runs the read-only check. Skips fixtures that already exist, so it is safe to rerun. |
| `make check-fixtures` | No | Fails unless the fixture user signs in, the repository exists, the recorded commit is on the default branch, and the recorded issue has the expected title. Prints the newest commit on the default branch. |
| `make write-check` | Yes | Pushes one new commit as the fixture user and prints the UTC time the push was accepted. That time is the last acknowledged write for the RPO measurement. |

The targets use `git_host` from `deploy/clusters/primary/cluster-settings.yaml`. To check another endpoint, such as the recovery host, set `GIT_HOST`:

```bash
make check-fixtures GIT_HOST=git-dr.sindrg.com
```

The fixture commit has fixed content, identity, and dates, so its SHA is recorded before the push. `scripts/fixture_commit.py` builds it, the create playbook fails if the SHA differs from the record, and a unit test fails if someone edits the commit fields without updating the SHA. The issue is `#1` because the repository starts empty.

1. Create the fixtures. Requires `git`, `sops`, and the controller toolchain from `make venv`.

   ```bash
   make create-fixtures
   ```

   Expected: `Report what this run created` prints a creation time for the user, repository, and issue, and a push time for commit `3775f53a1042434d76ec940d3d49360e1b3a0847`. The check play then reports all four fixtures, and the recap ends with `failed=0`. A rerun reports each fixture as `already present`.

2. Record the four lines from `Report what this run created` in the milestone worklog. Gitea reports creation times in its server time zone; the push time is UTC.

3. Run the read-only check.

   ```bash
   make check-fixtures
   ```

   Expected: `failed=0` and `changed=0`. `Report the fixture state` lists the user, repository, commit, issue, and the newest commit on `main`.

4. Run the write check.

   ```bash
   make write-check
   ```

   Expected: `failed=0` and a line `Push accepted at <UTC time> by https://git.sindrg.com/`, followed by the new commit SHA. A later `make check-fixtures` shows that SHA as the newest commit.

## Milestone gate

The [milestone 3 gate](../../plan.md#3-service-deployment) requires that Git changes reconcile, Gitea works after its pods restart, and all fixture data remains. Run the steps in order and record each result.

1. Bootstrap from `main` and validate.

   ```bash
   make bootstrap
   make validate-cluster
   make validate-services
   ```

   Expected: all three recaps end with `failed=0`. `validate-cluster` prints `main@sha1:<commit>` equal to `git rev-parse origin/main`.

2. Create the fixtures as described in [Recovery fixtures](#recovery-fixtures), steps 1 and 2.

3. Restart both pods with the command from [PostgreSQL and Gitea](#postgresql-and-gitea), step 6.

   Expected: both rollouts complete.

4. Run the fixture checks.

   ```bash
   make check-fixtures
   make write-check
   ```

   Expected: both end with `failed=0`. The write check prints the time the push was accepted.

5. Restart the worker VM. This is stronger than the plan requires: it checks that the Local Path volumes on the dedicated disk come back with the node.

   ```bash
   gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command='sudo systemctl reboot'
   gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command='findmnt /var/lib/k8s-dr'
   make validate-cluster
   make validate-services
   make check-fixtures
   ```

   Expected: the reboot command may exit nonzero when the connection drops. Retry `findmnt` until it shows the `ext4` data disk. Both validations and the fixture check end with `failed=0`, and the newest commit is the one from step 4.

6. Show that a Git change reconciles without a bootstrap. Merge a harmless change to `main`, such as a label on the service namespaces in `deploy/base/namespaces.yaml`. The `GitRepository` polls every minute. Then read what Flux applied:

   ```bash
   gcloud compute ssh "$CONTROL_PLANE_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap \
     --command='K="sudo kubectl --kubeconfig /etc/kubernetes/admin.conf"; $K get kustomization/flux-system --namespace flux-system -o jsonpath="{.status.lastAppliedRevision}{\"\n\"}"; $K get namespaces gitea postgresql -L app.kubernetes.io/part-of'
   git rev-parse origin/main
   ```

   Expected: the applied revision is `main@sha1:` followed by the merge commit, and both namespaces show the new label.

If a check fails, send the failing command, its exit code, and the relevant error text. Do not send state, plan files, or credentials.
