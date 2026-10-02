# 0010: Harden the running service

Status: Accepted and implemented on 2026-10-01.

Date: 2026-10-01

## Goal

Close the gaps a review of the running primary service found, and keep checking the public surface after the review. The service stays up between drills, so its exposure matters even though the project measures recovery.

This record covers the changes that need no new component in the cluster. Detection inside the cluster, the scope of the in-cluster SOPS key, and the scope of the Cloudflare token are listed under [Not decided](#not-decided).

## Review findings

The review on 2026-10-01 read the repository and probed the service from outside. The [worklog](../worklogs/04c-service-hardening.md) records the commands and output.

| Finding | Decision below |
| --- | --- |
| Anonymous visitors can list users through `/api/v1/users/search` and `/explore/users` | Require sign-in |
| Traefik, the only pod the internet reaches, can open connections to the kubelets, the metadata server, and the internet | Restrict Traefik egress |
| Pods in `cert-manager` and `monitoring` can read the node service account token from the metadata server | Deny the metadata server |
| Both node service accounts hold the `cloud-platform` scope | Narrow the node scopes |
| Nothing rechecks the public surface after a change | Check the public surface daily |

## Decisions

### Require sign-in to view Gitea

Set `REQUIRE_SIGNIN_VIEW: true`. Anonymous requests then see only the sign-in page: no user list, no API, and no explore pages. `/api/healthz` stays open, so the external probe that [decision 0002](0002-recovery-contract.md) requires still works. The fixture checks sign in already.

Trade-off: the service can no longer host a public repository. The lab has none.

### Restrict Traefik egress

A NetworkPolicy in the `traefik` namespace allows egress only to cluster DNS, the Kubernetes API on port 6443, and Gitea on port 3000. Flux applies it from `deploy/infrastructure/traefik`; Ansible still owns the namespace and the release.

The policy leaves ingress open. Public traffic reaches Traefik on host ports with the client address, and the VPC firewall already limits it to ports 80 and 443.

Trade-off: Traefik's version check and anonymous usage report fail silently. A connection that is already open survives a policy change, so the gate restarts Traefik to prove it can load its configuration under the policy.

### Deny the metadata server outside the backup Job

A NetworkPolicy in `cert-manager` and in `monitoring` allows all egress except `169.254.169.254/32`. The `gitea` and `postgresql` namespaces already deny it by default, with one exception for the backup Job.

Limits:

- `flux-system` is not covered. The Flux release ships a policy that allows all egress, and NetworkPolicy rules only add. Flux also holds the SOPS key, so a token for the worker service account adds little there.
- Host-network pods, such as node-exporter and the Calico node agents, are outside NetworkPolicy.
- `kube-system`, `calico-system`, `tigera-operator`, and `local-path-storage` have no policy.

### Narrow the node OAuth scopes

The worker gets `devstorage.read_write` and the control plane gets no scope. [Decision 0003](0003-regional-infrastructure-and-state.md) accepted `cloud-platform` because the service accounts held no roles. [Decision 0007](0007-consistent-backups.md) then gave the worker bucket roles, and a later grant would widen every pod's reach without a code change in the cluster. The scope now caps a metadata-server token at Cloud Storage object access, whatever IAM grants.

Trade-off: changing scopes stops and restarts each VM, so the module sets `allow_stopping_for_update`. The same setting lets a later machine type change restart a VM; the plan shows it first.

### Check the public surface daily

`scripts/check-public-surface.sh` probes the service from outside: TLS versions, the HTTP redirect, response headers, certificate expiry, anonymous API access, registration, ports that must stay closed, CAA, and DNSSEC. A GitHub Actions workflow runs it daily. It needs no credential and runs outside both regions.

The script has three outcomes. A probe that could not run is inconclusive and never counts as a pass. Findings this record accepts are listed in the script; a listed finding that starts passing also fails the run, so the list cannot outlive the findings.

| Open finding | State |
| --- | --- |
| `hsts` | No `Strict-Transport-Security` header. A response header filter on the HTTPS route can add it. |
| `caa` | No CAA record in `sindrg.com`, so any certificate authority may issue for the zone. The fix is a DNS record for `letsencrypt.org`. |

Trade-off: the check sees only what an outside client sees. During a drill it probes whichever cluster `git.sindrg.com` points to, and reports inconclusive results while neither answers.

## Alternatives not adopted

| Option | Why not |
| --- | --- |
| Default-deny ingress for `traefik`, `cert-manager`, and `monitoring` | The API server reaches the cert-manager webhook from the control-plane node, and public traffic reaches Traefik on host ports. A wrong rule takes the service down, and the egress rules cover the findings. |
| Calico GlobalNetworkPolicy to deny the metadata server cluster-wide | One rule instead of several, and it would cover `flux-system`. It ties the manifests to the Calico API. Revisit if more namespaces need the rule. |
| A triage worker in the cluster that classifies Security Command Center findings with a model, as built for a GKE lab | It needs Pub/Sub, Vertex AI, and logging access. Without GKE Workload Identity those permissions would go on the worker node service account, which reverses the scope decision above. It also needs a reviewed corpus of controls and a threat model that this project does not have yet, adds a workload to rebuild during recovery, and stops with the region it watches. Security Command Center sees the Google Cloud configuration, not the kubeadm cluster, so it would not detect a compromise inside the cluster. |

## Not decided

Each needs its own decision record.

1. **Detection.** The cluster ships metrics only. There is no API server audit log, no Gitea or Traefik access log outside the node, and no alert, so a compromise inside the cluster would go unnoticed.
2. **The in-cluster SOPS key.** `flux-system/sops-age` decrypts every secret in the public repository, and the same key encrypts `recovery/`. A separate operator key for `recovery/` would limit what a cluster compromise reveals.
3. **The Cloudflare token.** It can edit the whole `sindrg.com` zone. A delegated subzone for ACME challenges would limit it.
4. **Project isolation.** The project also holds a Firebase service account with a project-level token creator role, and a `default` network with SSH and RDP open to the internet. No VM uses that network. Removing both is an operator action outside Terraform.
