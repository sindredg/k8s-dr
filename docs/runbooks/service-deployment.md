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

If a check fails, send the failing command, its exit code, and the relevant error text. Do not send state, plan files, or credentials.
