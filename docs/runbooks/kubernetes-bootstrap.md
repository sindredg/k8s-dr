# Kubernetes bootstrap: operator procedure

Status: Validated. The Milestone 2 gate passed on 2026-09-25 after a VM rebuild, repeated bootstrap runs, and application validation. See the [worklog](../worklogs/02-kubernetes-bootstrap.md) for recorded evidence. Design: [ADR 0005](../decisions/0005-kubernetes-bootstrap-architecture.md).

The milestone 2 gate used a disposable test application. Gitea and the recovery fixtures now cover the same checks, so the test application was removed; this procedure validates with the service and fixture checks instead. See the [ADR 0005 amendment](../decisions/0005-kubernetes-bootstrap-architecture.md#amendment-retire-the-disposable-application).

Run every command from the repository root on the external operator machine unless a step says otherwise. Both VMs stay private. Ansible reaches them only through IAP and OS Login.

The `make` targets wrap the commands in the repository `Makefile`. Run `make` to list them, and `make -n <target>` to print a command without running it. `make inventory` reads the OS Login user from `gcloud` and the node values from `TF_DIR`, which defaults to `infra/primary`. The Makefile stores no real identifiers.

Do not commit or share the generated inventory, the generated `known_hosts` file, kubeconfigs, join tokens, Terraform plans, real project IDs, private addresses, or complete logs. When you report evidence, send the command, exit code, expected and observed results, and relevant excerpts with identifiers replaced.

## Gate checks

| Gate condition | Evidence step |
| --- | --- |
| Both nodes report `Ready` | [Bootstrap the cluster](#bootstrap-the-cluster), step 3 |
| An application schedules and is reachable | [Validate the services](#validate-the-services) |
| The worker rejoins after a restart | [Restart the worker](#restart-the-worker) |
| A fresh rebuild follows the same steps | [Rebuild from fresh VMs](#rebuild-from-fresh-vms) |

## Prepare the toolchain and inventory

1. Install the pinned controller toolchain.

   ```bash
   make venv
   ```

   Expected: pip installs the exact versions in `ansible/requirements.txt` without errors.

2. Export the node identifiers from Terraform state into your shell.

   ```bash
   cd infra/primary
   export PROJECT_ID="$(terraform output -raw project_id)"
   export PRIMARY_ZONE="$(terraform output -raw zone)"
   export CONTROL_PLANE_NAME="$(terraform output -json instance_names | python3 -c 'import json,sys; print(json.load(sys.stdin)["control-plane"])')"
   export WORKER_NAME="$(terraform output -raw worker_name)"
   ```

   Expected: all four variables are nonempty.

3. Confirm IAP and OS Login access to both nodes.

   ```bash
   gcloud compute ssh "$CONTROL_PLANE_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command=true
   gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command=true
   cd ../..
   ```

   Expected: both commands exit `0`. The first connection also creates `~/.ssh/google_compute_engine` if it does not exist.

4. Generate the inventory from Terraform outputs.

   ```bash
   make inventory
   git status --short ansible/inventory
   ```

   Expected: the script writes `ansible/inventory/generated/primary/hosts.json`, and `git status` prints nothing for it. Each cluster has its own inventory: `make inventory CLUSTER=recovery` reads `infra/recovery` and writes `ansible/inventory/generated/recovery/hosts.json`, and every later command against that cluster needs `CLUSTER=recovery`. The file contains real node names and private addresses. It stays ignored by Git. Do not share it.

## Confirm the boot image

The tested Ubuntu image is pinned as the `boot_image` default in `infra/modules/regional_cluster/variables.tf`. Images are global, so the primary and recovery roots use the same image. Confirm the running VMs match the pin before any rebuild.

1. Read the source image of both boot disks. These commands are read-only.

   ```bash
   export CONTROL_PLANE_DISK="$(gcloud compute instances describe "$CONTROL_PLANE_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --format='value(disks[0].source.basename())')"
   export WORKER_DISK="$(gcloud compute instances describe "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --format='value(disks[0].source.basename())')"
   export CONTROL_PLANE_IMAGE="$(gcloud compute disks describe "$CONTROL_PLANE_DISK" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --format='value(sourceImage)')"
   export WORKER_IMAGE="$(gcloud compute disks describe "$WORKER_DISK" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --format='value(sourceImage)')"
   test "$CONTROL_PLANE_IMAGE" = "$WORKER_IMAGE" && echo "$CONTROL_PLANE_IMAGE"
   grep -A 4 'variable "boot_image"' infra/modules/regional_cluster/variables.tf
   ```

   Expected: the test passes and prints one image self-link that equals the tracked default. If the images differ from each other or from the default, stop and report the image names.

2. Plan the primary root.

   ```bash
   cd infra/primary
   terraform plan -detailed-exitcode
   cd ../..
   ```

   Expected: the plan exits `0`. Leave `boot_image` unset in the local `terraform.tfvars`. To adopt a newer image, change the tracked default in a reviewed PR and repeat the milestone 2 rebuild validation.

## Bootstrap the cluster

1. Run the bootstrap playbook through the IAP runner. The last play installs Flux, so the SOPS age private key must be at `FLUX_AGE_KEY_FILE` (default `~/.config/k8s-dr/age.agekey`). See [Flux and SOPS](service-deployment.md#flux-and-sops).

   ```bash
   make bootstrap
   ```

   Expected: the play recap shows `failed=0` and `unreachable=0` for both hosts. The runner closes both tunnels when Ansible exits.

2. Run the same command a second time to prove the rerun is safe.

   Expected: `failed=0` and `unreachable=0` again. kubeadm does not initialize or join again, and the worker disk is not formatted. Some add-on tasks, such as `kubectl apply` and `helm upgrade --install`, always report `changed`. That is expected and is not a failure.

3. Check the cluster without an application.

   ```bash
   make validate-cluster
   ```

   Expected: `failed=0`. Both nodes are `Ready`; the Calico, CoreDNS, Local Path Provisioner, Traefik, and Flux rollouts complete; the `traefik` GatewayClass is `Accepted`; and the Flux Git source and cluster Kustomization are `Ready`.

   The IAP runner prints a final line such as `run_with_iap: started 2026-09-25T08:00:00Z, finished 2026-09-25T08:07:30Z, elapsed 450s, exit 0` for every command. Include it in evidence for bootstrap runs; it is the baseline for recovery timing.

## Validate the services

Flux deploys PostgreSQL and Gitea during bootstrap. Their checks prove scheduling, cross-node networking, persistent volumes, Gateway API routing, and public HTTPS.

1. Check the Flux-owned services.

   ```bash
   make validate-services
   ```

   Expected: `failed=0`. The service Kustomizations and Helm releases are `Ready`, the certificate is `Ready`, both data PVCs are `Bound`, the Gateway is `Programmed`, both HTTPRoutes are `Accepted`, and a request from the control plane to the worker's Traefik NodePort is redirected to HTTPS. See [service deployment](service-deployment.md) for details.

2. Check the recovery fixtures from the operator machine.

   ```bash
   make check-fixtures
   ```

   Expected: `failed=0`. The fixture user signs in, and the repository, issue, and commit exist. The fixtures live on the worker data disk, so this also proves the data survived. If the fixtures do not exist yet, create them first as described in [Recovery fixtures](service-deployment.md#recovery-fixtures).

## Restart the worker

1. Reboot the worker, then wait for SSH to return.

   ```bash
   gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command='sudo systemctl reboot'
   gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command='findmnt /var/lib/k8s-dr'
   ```

   Expected: the reboot command may exit nonzero when the connection drops. Retry the second command until it succeeds. It shows an `ext4` filesystem mounted at `/var/lib/k8s-dr`, restored from the UUID entry in `/etc/fstab`. SSH and the disk may recover before Kubernetes workloads do.

2. Rerun validation.

   ```bash
   make validate-cluster
   make validate-services
   make check-fixtures
   ```

   Expected: validation waits up to five minutes for both nodes and the Calico, CoreDNS, Local Path Provisioner, Traefik, and Flux rollouts. The IAP runner also retries a temporary SSH host-key scan failure for up to 30 seconds. All three commands end with `failed=0`. Inspect the node output to confirm the worker is `Ready` again without a new join. The fixture check confirms the Gitea data survived the restart. If a wait times out, inspect the named workload and its events before retrying.

## Rebuild from fresh VMs

This step replaces both boot disks and VMs. It keeps the worker data disk, VPC, buckets, and state. The data disk has `prevent_destroy`, and the storage role reuses its existing `k8sdr-data` filesystem instead of formatting it.

1. Confirm the image from [Confirm the boot image](#confirm-the-boot-image) matches the tracked pin. Then save a replacement plan for exactly the two VMs.

   ```bash
   cd infra/primary
   terraform plan -out=vm-rebuild.tfplan -replace='module.primary_cluster.google_compute_instance.node["control-plane"]' -replace='module.primary_cluster.google_compute_instance.node["worker"]'
   terraform show vm-rebuild.tfplan
   ```

   Expected: the plan replaces the two `google_compute_instance.node` resources and the `google_compute_attached_disk.worker_data` attachment, which depends on the worker instance. It must not destroy `google_compute_disk.worker_data`, the network, subnet, firewall rules, router, NAT, service accounts, or buckets. If it does, do not apply. Report the plan summary line.

2. Apply only the reviewed plan, then delete it.

   ```bash
   terraform apply vm-rebuild.tfplan
   rm vm-rebuild.tfplan
   cd ../..
   ```

   Expected: the apply completes, and the resource counts match the reviewed plan. The plan file contains sensitive data and is ignored by Git.

3. Repeat these sections in order with no manual changes on the nodes:
   1. [Prepare the toolchain and inventory](#prepare-the-toolchain-and-inventory), steps 2 to 4. The IAP runner scans new host keys on every run, so the replaced VMs need no `known_hosts` cleanup.
   2. [Bootstrap the cluster](#bootstrap-the-cluster), all steps, including the second run.
   3. [Validate the services](#validate-the-services), all steps.

   Expected: every step gives the same result as the first build. The fixture check passes without recreating the fixtures, because the worker data disk is kept. Record any step that needed a manual action as a failure in the worklog.

## Troubleshooting

For join failures or a worker that stays `NotReady`, use the [worker join guide](../troubleshooting/01-worker-join-failure.md). Record the symptom, confirmed cause, fix, and verification in the [worklog](../worklogs/02-kubernetes-bootstrap.md). Label unconfirmed causes as hypotheses.
