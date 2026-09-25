# Kubernetes bootstrap: operator procedure

Status: Validated. The Milestone 2 gate passed on 2026-09-25 after a VM rebuild, repeated bootstrap runs, and application validation. See the [worklog](../worklogs/02-kubernetes-bootstrap.md) for recorded evidence. Design: [ADR 0005](../decisions/0005-kubernetes-bootstrap-architecture.md).

Run every command from the repository root on the external operator machine unless a step says otherwise. Both VMs stay private. Ansible reaches them only through IAP and OS Login.

Do not commit or share the generated inventory, the generated `known_hosts` file, kubeconfigs, join tokens, Terraform plans, real project IDs, private addresses, or complete logs. When you report evidence, send the command, exit code, expected and observed results, and relevant excerpts with identifiers replaced.

## Gate checks

| Gate condition | Evidence step |
| --- | --- |
| Both nodes report `Ready` | [Validate the disposable application](#validate-the-disposable-application), `validate.yml` node output |
| A disposable app schedules and is reachable | [Validate the disposable application](#validate-the-disposable-application) |
| The worker rejoins after a restart | [Restart the worker](#restart-the-worker) |
| A fresh rebuild follows the same steps | [Rebuild from fresh VMs](#rebuild-from-fresh-vms) |

## Prepare the toolchain and inventory

1. Install the pinned controller toolchain.

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r ansible/requirements.txt
   ```

   Expected: pip installs the exact versions in `ansible/requirements.txt` without errors.

2. Export the node identifiers from Terraform state into your shell.

   ```bash
   cd infra/primary
   export PROJECT_ID="$(terraform output -raw project_id)"
   export PRIMARY_ZONE="$(terraform output -raw zone)"
   export CONTROL_PLANE_NAME="$(terraform output -json instance_names | python3 -c 'import json,sys; print(json.load(sys.stdin)["control-plane"])')"
   export WORKER_NAME="$(terraform output -raw worker_name)"
   export OS_LOGIN_USER="$(gcloud compute os-login describe-profile --format='value(posixAccounts[0].username)')"
   ```

   Expected: all five variables are nonempty.

3. Confirm IAP and OS Login access to both nodes.

   ```bash
   gcloud compute ssh "$CONTROL_PLANE_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command=true
   gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command=true
   cd ../..
   ```

   Expected: both commands exit `0`. The first connection also creates `~/.ssh/google_compute_engine` if it does not exist.

4. Generate the inventory from Terraform outputs.

   ```bash
   python3 scripts/prepare_ansible_inventory.py --terraform-dir infra/primary --ssh-user "$OS_LOGIN_USER" --ssh-key "$HOME/.ssh/google_compute_engine" --output ansible/inventory/generated/hosts.json
   git status --short ansible/inventory
   ```

   Expected: the script writes `ansible/inventory/generated/hosts.json`, and `git status` prints nothing for it. The file contains real node names and private addresses. It stays ignored by Git. Do not share it.

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

1. Run the bootstrap playbook through the IAP runner.

   ```bash
   python3 scripts/run_with_iap.py --inventory ansible/inventory/generated/hosts.json -- .venv/bin/ansible-playbook -i ansible/inventory/generated/hosts.json ansible/playbooks/bootstrap.yml
   ```

   Expected: the play recap shows `failed=0` and `unreachable=0` for both hosts. The runner closes both tunnels when Ansible exits.

2. Run the same command a second time to prove the rerun is safe.

   Expected: `failed=0` and `unreachable=0` again. kubeadm does not initialize or join again, and the worker disk is not formatted. Some add-on tasks, such as `kubectl apply` and `helm upgrade --install`, always report `changed`. That is expected and is not a failure.

3. Check the cluster without an application.

   ```bash
   python3 scripts/run_with_iap.py --inventory ansible/inventory/generated/hosts.json -- .venv/bin/ansible-playbook -i ansible/inventory/generated/hosts.json ansible/playbooks/validate_cluster.yml -v
   ```

   Expected: `failed=0`. Both nodes are `Ready`; the Calico, CoreDNS, Local Path Provisioner, and Traefik rollouts complete; and the `traefik` GatewayClass is `Accepted`. `validate.yml` runs this playbook and then `validate_test_app.yml`, which needs the disposable application.

   The IAP runner prints a final line such as `run_with_iap: started 2026-09-25T08:00:00Z, finished 2026-09-25T08:07:30Z, elapsed 450s, exit 0` for every command. Include it in evidence for bootstrap runs; it is the baseline for recovery timing.

## Validate the disposable application

1. Deploy the application and rerun validation.

   ```bash
   python3 scripts/run_with_iap.py --inventory ansible/inventory/generated/hosts.json -- .venv/bin/ansible-playbook -i ansible/inventory/generated/hosts.json ansible/playbooks/deploy_test_app.yml
   python3 scripts/run_with_iap.py --inventory ansible/inventory/generated/hosts.json -- .venv/bin/ansible-playbook -i ansible/inventory/generated/hosts.json ansible/playbooks/validate.yml -v
   ```

   Expected: deployment ends with `failed=0`, and validation ends with `failed=0`. Inspect its output: both nodes must be `Ready` on Kubernetes `v1.36.2`; the Tigera operator, `calico-node`, CoreDNS, Traefik, and `local-path-provisioner` pods must be `Running`; the PVC must be `Bound` with StorageClass `local-path`, with a matching PV; and the application pod must be `Running` on the worker. The playbook fails unless the Gateway is `Programmed`, the HTTPRoute is `Accepted`, and the PVC is `Bound`. It then requests the application from the control plane through the worker's private address and Traefik NodePort 30080:

   - `Request the application through the Gateway route` returns `200` with body `milestone2 persistent marker`.
   - `Confirm the route rejects requests without the application host` returns `404`, which confirms that the route matches the hostname.

   These requests prove reachability inside the VPC. They do not test access from the operator machine. See [ADR 0005](../decisions/0005-kubernetes-bootstrap-architecture.md#amendment-in-cluster-reachability-check).

2. Delete the application pod, wait for its replacement, and rerun validation.

   ```bash
   python3 scripts/run_with_iap.py --inventory ansible/inventory/generated/hosts.json -- .venv/bin/ansible control-plane -i ansible/inventory/generated/hosts.json --become -m ansible.builtin.shell -a 'kubectl --kubeconfig /etc/kubernetes/admin.conf -n milestone2-test delete pod -l app=milestone2-app --wait=true && kubectl --kubeconfig /etc/kubernetes/admin.conf -n milestone2-test rollout status deployment/milestone2-app --timeout=180s'
   python3 scripts/run_with_iap.py --inventory ansible/inventory/generated/hosts.json -- .venv/bin/ansible-playbook -i ansible/inventory/generated/hosts.json ansible/playbooks/validate.yml -v
   ```

   Expected: the rollout completes, the application pod has a new name in the second validation output, and validation ends with `failed=0` with the same marker. Compare pod names and the PVC-to-PV binding in the validation output before and after deletion. The replacement pod read the file that the first pod wrote to the persistent volume.

## Restart the worker

1. Reboot the worker, then wait for SSH to return.

   ```bash
   gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command='sudo systemctl reboot'
   gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command='findmnt /var/lib/k8s-dr'
   ```

   Expected: the reboot command may exit nonzero when the connection drops. Retry the second command until it succeeds. It shows an `ext4` filesystem mounted at `/var/lib/k8s-dr`, restored from the UUID entry in `/etc/fstab`. SSH and the disk may recover before Kubernetes workloads do.

2. Rerun validation.

   ```bash
   python3 scripts/run_with_iap.py --inventory ansible/inventory/generated/hosts.json -- .venv/bin/ansible-playbook -i ansible/inventory/generated/hosts.json ansible/playbooks/validate.yml -v
   ```

   Expected: validation waits up to five minutes for both nodes and the Calico, CoreDNS, Local Path Provisioner, Traefik, and test-app rollouts. The IAP runner also retries a temporary SSH host-key scan failure for up to 30 seconds. Validation then ends with `failed=0`. Inspect the node and pod output to confirm the worker is `Ready` again without a new join. Compare the PVC-to-PV binding with the output before the restart, and confirm the application request returns the same marker. If a wait times out, inspect the named workload and its events before retrying.

3. Remove the disposable application.

   ```bash
   python3 scripts/run_with_iap.py --inventory ansible/inventory/generated/hosts.json -- .venv/bin/ansible-playbook -i ansible/inventory/generated/hosts.json ansible/playbooks/cleanup_test_app.yml
   ```

   Expected: `failed=0`. The namespace `milestone2-test` no longer exists, and the provisioner deletes the PV directory because the `local-path` reclaim policy is `Delete`.

## Rebuild from fresh VMs

This step replaces both boot disks and VMs. It keeps the worker data disk, VPC, buckets, and state. The data disk has `prevent_destroy`, and the storage role reuses its existing `k8sdr-data` filesystem instead of formatting it.

1. Confirm the image from [Confirm the boot image](#confirm-the-boot-image) matches the tracked pin and that cleanup ran. Then save a replacement plan for exactly the two VMs.

   ```bash
   cd infra/primary
   terraform plan -out=milestone2-rebuild.tfplan -replace='module.primary_cluster.google_compute_instance.node["control-plane"]' -replace='module.primary_cluster.google_compute_instance.node["worker"]'
   terraform show milestone2-rebuild.tfplan
   ```

   Expected: the plan replaces the two `google_compute_instance.node` resources and the `google_compute_attached_disk.worker_data` attachment, which depends on the worker instance. It must not destroy `google_compute_disk.worker_data`, the network, subnet, firewall rules, router, NAT, service accounts, or buckets. If it does, do not apply. Report the plan summary line.

2. Apply only the reviewed plan, then delete it.

   ```bash
   terraform apply milestone2-rebuild.tfplan
   rm milestone2-rebuild.tfplan
   cd ../..
   ```

   Expected: the apply completes, and the resource counts match the reviewed plan. The plan file contains sensitive data and is ignored by Git.

3. Repeat these sections in order with no manual changes on the nodes:
   1. [Prepare the toolchain and inventory](#prepare-the-toolchain-and-inventory), steps 2 to 4. The IAP runner scans new host keys on every run, so the replaced VMs need no `known_hosts` cleanup.
   2. [Bootstrap the cluster](#bootstrap-the-cluster), all steps, including the second run.
   3. [Validate the disposable application](#validate-the-disposable-application), step 1.
   4. Cleanup from [Restart the worker](#restart-the-worker), step 3.

   Expected: every step gives the same result as the first build. Record any step that needed a manual action as a failure in the worklog.

## Troubleshooting

For join failures or a worker that stays `NotReady`, use the [worker join guide](../troubleshooting/01-worker-join-failure.md). Record the symptom, confirmed cause, fix, and verification in the [worklog](../worklogs/02-kubernetes-bootstrap.md). Label unconfirmed causes as hypotheses.
