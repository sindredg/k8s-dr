# Primary infrastructure: operator procedure

Status: Implemented and validated on 2026-09-23. The milestone gate is closed. Amended on 2026-09-25 for the `infra/shared` root, on 2026-10-14 after a rebuild from empty state, on 2026-10-16 for the recovery reader check in step 9, which passed as the reader the same day (see the [milestone 10 worklog](../worklogs/10-automated-recovery.md#migration-after-the-merge)), and on 2026-10-16 for milestone 10, which applies the shared root first. The new order and plan counts are written, not run from empty state. Run these steps to reproduce or revalidate milestone 1. Use a shell on an operator machine outside the Finland VMs. Never commit local values, plans, state, credentials, or raw output.

## Inputs and access

Use an existing GCP project linked to a billing account. Choose globally unique names for the state and backup buckets. Choose an unused private subnet range and a Finland zone. Keep the state operator, backup operator, and recovery reader identities available through an external identity provider or credential store, independent of the primary VMs. Separate these identities where possible.

The operator needs permissions to enable APIs, create Compute Engine and Storage resources, and manage project IAM and service accounts. The state operator needs object read and write access to the state bucket. The cluster administrator needs IAP tunnel, OS Login, instance administration, and service account user access; The shared root grants the project roles and the primary root grants service account use to `admin_member`. If an organization policy prevents any of these grants, resolve it before apply.

Read the [state security guidance](https://docs.cloud.google.com/docs/terraform/best-practices/security). A Terraform `sensitive` marker only hides selected CLI display; it does not remove values from state. This configuration creates no keys or passwords, but state still exposes resource metadata and private addresses. Treat plans and all state generations as sensitive.

## Bootstrap the state bucket

1. Set the project in your shell and authenticate your operator identity. Replace the placeholder locally. Confirm the project is linked to the expected billing account.

   ```bash
   export PROJECT_ID='REPLACE_WITH_PROJECT_ID'
   gcloud auth login
   gcloud auth application-default login
   gcloud billing projects describe "$PROJECT_ID"
   gcloud services enable serviceusage.googleapis.com compute.googleapis.com storage.googleapis.com iap.googleapis.com --project="$PROJECT_ID"
   ```

   Expected: the billing project shows the intended account, and API enablement succeeds. No Billing Budgets API is needed for this configuration.

2. Copy and edit the bootstrap example. Use a separate state operator identity that your operator credentials can access. Restrict local file permissions before Terraform creates local state.

   ```bash
   cd infra/bootstrap
   umask 077
   cp terraform.tfvars.example terraform.tfvars
   chmod 600 terraform.tfvars
   # Edit terraform.tfvars and replace every placeholder.
   terraform init
   terraform plan -out=bootstrap.tfplan
   terraform apply bootstrap.tfplan
   ```

   Expected: the plan creates one bucket and one bucket IAM member; apply returns the state bucket name. Inspect the plan before apply. Both `terraform.tfstate` and `bootstrap.tfplan` contain sensitive resource data and are ignored by Git.

3. Protect the temporary local state before migration. GnuPG adds one local dependency and a passphrase to manage; it gives an encrypted copy that remains readable without the new bucket. Store a copy of the encrypted file and its passphrase in separate protected locations outside Finland. Do not store the passphrase in a command, environment variable, or this repository.

   ```bash
   chmod 600 terraform.tfstate
   mkdir -m 700 .private
   gpg --symmetric --cipher-algo AES256 --output .private/bootstrap-before-migration.tfstate.gpg terraform.tfstate
   ```

   Expected: GnuPG asks for a passphrase and creates an encrypted file. Keep the local state and encrypted copy until remote state access is confirmed. `.private/` is ignored by Git; that alone is not backup or encryption.

4. Configure the backend and migrate state. Copying `backend.tf.example` activates the GCS backend only after the bucket exists. Both local backend files are ignored. The bucket name must match the bootstrap input.

   ```bash
   cp backend.tf.example backend.tf
   cp backend.hcl.example backend.hcl
   chmod 600 backend.tf backend.hcl
   # Edit backend.hcl and replace the bucket placeholder.
   terraform init -migrate-state -backend-config=backend.hcl
   terraform state list
   gcloud storage ls "gs://$(terraform output -raw state_bucket_name)/bootstrap/"
   ```

   Expected: Terraform offers to copy the existing state, lists the bucket and IAM resources from the GCS backend, and GCS lists the backend state object. Retain the encrypted offsite copy. Remove the unencrypted local state, any local state backups, and the saved plan only after confirming migration; check their exact paths first. Do not destroy the state bucket or its bootstrap root.

## Apply the shared root

Apply the shared root before the primary root. The shared root creates the node service accounts of both clusters, and the primary root only looks them up. From empty state, a primary plan that runs first fails with `Permission 'iam.serviceAccounts.get' denied`, which is the error Google Cloud returns for a missing account. See [decision 0013](../decisions/0013-automated-recovery.md#node-identities-and-backup-grants-move-to-the-shared-root).

5. Fill in the shared root's local variables and backend configuration. This root owns the Belgium backup bucket, its IAM members, the node service accounts of every cluster, the project-level administrator grants that every region uses, and the uptime check.

   ```bash
   cd ../shared
   umask 077
   if [ ! -e terraform.tfvars ]; then cp terraform.tfvars.example terraform.tfvars; fi
   if [ ! -e backend.hcl ]; then cp backend.hcl.example backend.hcl; fi
   chmod 600 terraform.tfvars backend.hcl
   # Edit both files and replace every placeholder.
   terraform init -backend-config=backend.hcl
   terraform plan -out=shared.tfplan
   terraform apply shared.tfplan
   ```

   Expected for an initial apply: `Plan: 18 to add, 0 to change, 0 to destroy.` The plan contains:

   | Resources | Count |
   | --- | --- |
   | Belgium backup bucket | 1 |
   | Project IAM members for the administrator | 3 |
   | Node service accounts, two for each cluster in `clusters` | 4 |
   | The administrator's use of each node account | 4 |
   | Bucket IAM members: a writer and a reader grant for each worker, and the recovery reader | 5 |
   | Uptime check | 1 |

   Inspect bucket names and IAM members before apply. The uptime check fails until the service is deployed.

## Apply the primary root

6. Fill in the local primary variables and backend configuration. Do not copy examples over existing local values.

   ```bash
   cd ../primary
   umask 077
   if [ ! -e terraform.tfvars ]; then cp terraform.tfvars.example terraform.tfvars; fi
   if [ ! -e backend.hcl ]; then cp backend.hcl.example backend.hcl; fi
   chmod 600 terraform.tfvars backend.hcl
   # Edit both files and replace every placeholder.
   terraform init -backend-config=backend.hcl
   terraform plan -out=primary.tfplan
   terraform apply primary.tfplan
   ```

   Expected for an initial apply: `Plan: 16 to add, 0 to change, 0 to destroy.` The plan contains:

   | Resources | Count |
   | --- | --- |
   | VPC, subnet, router, and NAT | 4 |
   | Firewall rules for IAP SSH, node traffic, and public web | 3 |
   | VMs, the worker data disk, and its attachment | 4 |
   | Load balancer: address, health check, instance group, backend service, and forwarding rule | 5 |

   It contains no service account, no IAM member, no Belgium VMs, and no public VM addresses; the only public address belongs to the load balancer. Inspect the plan before apply, especially disk replacement actions.

   If an earlier apply partially succeeded, make a fresh plan before applying again. Do not apply if it proposes VM, disk, network, or bucket replacement.

## Check the milestone 1 gate

7. Confirm state convergence and resource placement. Do not publish unredacted output.

   ```bash
   terraform plan -detailed-exitcode
   terraform output instance_names
   terraform output internal_ips
   terraform -chdir=../shared output backup_bucket_name
   gcloud compute instances list --project="$PROJECT_ID" --filter='labels.environment=primary' --format='table(name,zone,status,networkInterfaces[0].networkIP,networkInterfaces[0].accessConfigs[0].natIP)'
   gcloud compute disks describe "$(terraform output -raw worker_data_disk_name)" --project="$PROJECT_ID" --zone='REPLACE_WITH_THE_PRIMARY_ZONE' --format='yaml(name,zone,sizeGb,users)'
   ```

   Expected: plan exits `0` with no changes; two running VMs appear in Finland with private addresses and an empty external address column. The worker disk reports one attached user, the worker VM. Exit `2` means drift or a pending change; exit `1` means an error. A successful apply followed by a no-change plan is the reproducibility evidence for this build. The [milestone 8 rebuild](../worklogs/08-rebuild-from-runbooks.md) performed it from empty state on 2026-10-14.

8. Confirm IAP administration, private node traffic, and outbound access. Run from `infra/primary` with the same operator identity. A failed SSH command may indicate IAM propagation, OS Login, firewall, or VM boot issues.

   ```bash
   export PRIMARY_ZONE='REPLACE_WITH_THE_PRIMARY_ZONE'
   export WORKER_NAME="$(terraform output -raw worker_name)"
   export CONTROL_PLANE_IP="$(terraform output -raw control_plane_internal_ip)"
   gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command="ping -c 3 $CONTROL_PLANE_IP"
   gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command='curl -fsSI https://example.com'
   ```

   Expected: IAP SSH succeeds without a public VM address, ping receives responses from the control plane, and the HTTPS request returns headers through outbound access. The data disk is attached but intentionally unformatted and unmounted until milestone 2.

9. Confirm independent access to state, backup storage, and recovery credentials from this operator machine, not from a primary VM. Run the first commands as the infrastructure operator, who must also be able to access state. Do not upload test data to the backup bucket. If you need conclusive evidence during a Finland outage, repeat these read-only checks while the primary VMs are stopped, then start them again.

   ```bash
   terraform state list
   gcloud storage buckets describe gs://REPLACE_WITH_STATE_BUCKET --project="$PROJECT_ID"
   gcloud storage buckets describe "gs://$(terraform -chdir=../shared output -raw backup_bucket_name)" --project="$PROJECT_ID"
   gcloud storage ls "gs://REPLACE_WITH_STATE_BUCKET/primary/"
   ```

   Expected: state resources list through GCS and both Belgium buckets respond.

   Then check the recovery reader in a temporary gcloud configuration. `gcloud auth login` in another shell changes the shared configuration and signs out the operator, and it does not show which account signed in. The account check catches a sign-in with the wrong account. The permission test asks Cloud Storage which of the listed permissions the caller holds, so it writes nothing.

   ```bash
   BACKUP_BUCKET=$(terraform -chdir=../shared output -raw backup_bucket_name)
   READER=REPLACE_WITH_RECOVERY_READER_EMAIL
   export CLOUDSDK_CONFIG=$(mktemp -d)
   gcloud auth login "$READER" --no-launch-browser
   test "$(gcloud config get account)" = "$READER" && echo "account OK"
   gcloud storage ls "gs://$BACKUP_BUCKET/"
   curl -sS -H "Authorization: Bearer $(gcloud auth print-access-token)" \
     "https://storage.googleapis.com/storage/v1/b/$BACKUP_BUCKET/iam/testPermissions?permissions=storage.objects.list&permissions=storage.objects.get&permissions=storage.objects.create&permissions=storage.objects.delete"
   rm -rf "$CLOUDSDK_CONFIG"; unset CLOUDSDK_CONFIG
   ```

   Expected: `account OK`, the listing succeeds, and the permission test returns `storage.objects.get` and `storage.objects.list` only. If `account OK` is missing, the browser signed in another account: run the block again. The same test as the operator also returns `storage.objects.create` and `storage.objects.delete`. An empty backup bucket is expected on a new build. It cannot prove restore readiness; milestone 4 supplies backups and a restore test.

10. Review this project's charges and remaining credits in Cloud Billing. No Terraform-managed alert or automatic spend cap exists. Repeat this review while the lab is running.

If a check fails, send the failing command, its exit code, the relevant error text with IDs and addresses redacted, the expected and observed result, and the sanitized `terraform plan` resource summary. Do not send `terraform.tfstate`, plan files, credentials, kubeconfigs, or full IAM policy output. Validation results belong in the [milestone 1 worklog](../worklogs/01-primary-infrastructure.md) only after they are observed.

## Cost planning

Prices vary by billing currency, discounts, and traffic. Use the [Google Cloud pricing calculator](https://cloud.google.com/products/calculator) with these exact inputs before apply. The region-specific [Compute Engine](https://cloud.google.com/products/compute/pricing/general-purpose), [disk](https://cloud.google.com/compute/disks-image-pricing), [Cloud NAT](https://cloud.google.com/nat/pricing), and [Cloud Storage](https://cloud.google.com/storage/pricing) pages explain the charge categories.

| Calculator item | Input |
| --- | --- |
| Compute Engine | Finland `europe-north1`, 2 `e2-custom-2-4096` VMs, Linux/Ubuntu, 730 hours/month, on-demand, no committed-use discount |
| Persistent Disk | Finland, Balanced, 2 boot disks at 30 GiB and 1 worker data disk at 50 GiB, 730 hours/month |
| Cloud NAT | Finland, 1 public NAT gateway serving 2 VMs, 1 ephemeral NAT IP, 730 hours/month, assume 40 GiB processed/month (20 GiB outbound and 20 GiB inbound) |
| Cloud Load Balancing | Finland, 1 regional external passthrough forwarding rule and 1 in-use static external IPv4 address, 730 hours/month. Added in milestone 3 |
| Internet data transfer | Assume 20 GiB/month from Finland to the internet; replace with expected workload traffic |
| Cloud Storage state | Belgium `europe-west1`, Standard, 1 GiB including object versions |
| Cloud Storage backups | Belgium `europe-west1`, Standard, 20 GiB including object versions; no backup objects exist yet |

The calculator may show NAT and egress outside the Compute Engine section. Include them once. Exclude tax and promotional credits from the estimate, and note your billing account's actual currency. Backup volume, state versions, snapshots, storage operations, load balancer data processing, and future recovery drills can increase cost. The buckets remain billable after the VMs stop.

## Recovery root

`infra/recovery` instantiates the same `infra/modules/regional_cluster` module in Belgium with its own state prefix, name prefix, and subnet. It reads no Finland VM, network, or state output, exposes the same outputs as `infra/primary`, and owns an unprotected worker data disk so a drill can be destroyed. The project-level administrator grants and the backup bucket stay in `infra/shared`. See the [regional recovery runbook](regional-recovery.md) for the commands.

