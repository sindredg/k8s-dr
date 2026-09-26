# Primary infrastructure: operator procedure

Status: Implemented and validated on 2026-09-23. The milestone gate is closed. Amended on 2026-09-25 for the `infra/shared` root; an existing deployment moves to it once with the [shared-root migration](terraform-shared-root-migration.md). Run these steps to reproduce or revalidate milestone 1. Use a shell on an operator machine outside the Finland VMs. Never commit local values, plans, state, credentials, or raw output.

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

5. Fill in the shared root's local variables and backend configuration. This root owns the Belgium backup bucket, its IAM members, and the project-level administrator grants that every region uses.

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

   Expected for an initial apply: the plan contains one Belgium backup bucket, two bucket IAM members, and three project IAM members. Inspect bucket names and IAM members before apply.

## Apply the primary root

6. Fill in the local primary variables and backend configuration. If these local files already exist, keep them and remove any old `billing_account_id`, `budget_currency_code`, and `budget_amount` entries from `terraform.tfvars`. Do not copy examples over existing local values.

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

   Expected for an initial apply: the plan contains one Finland VPC and subnet, one NAT, two private VMs, one attached worker data disk, and two service account IAM grants. It contains no budget, Belgium VMs, or public VM addresses. Inspect the plan before apply, especially IAM members and disk replacement actions.

   If an earlier apply partially succeeded, make a fresh plan before applying again. After removing the budget configuration, expect no infrastructure changes if the other resources were created. If a budget was created, Terraform may propose deleting that budget. Do not apply if it proposes VM, disk, network, or bucket replacement. Review the plan before `terraform apply primary.tfplan`; skip apply if there are no changes.

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

   Expected: plan exits `0` with no changes; two running VMs appear in Finland with private addresses and an empty external address column. The worker disk reports one attached user, the worker VM. Exit `2` means drift or a pending change; exit `1` means an error. A successful apply followed by a no-change plan is the reproducibility evidence for this initial build. Do not mark a clean rebuild proven until it is actually performed.

8. Confirm IAP administration, private node traffic, and outbound access. Run from `infra/primary` with the same operator identity. A failed SSH command may indicate IAM propagation, OS Login, firewall, or VM boot issues.

   ```bash
   export PRIMARY_ZONE='REPLACE_WITH_THE_PRIMARY_ZONE'
   export WORKER_NAME="$(terraform output -raw worker_name)"
   export CONTROL_PLANE_IP="$(terraform output -raw control_plane_internal_ip)"
   gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command="ping -c 3 $CONTROL_PLANE_IP"
   gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command='curl -fsSI https://example.com'
   ```

   Expected: IAP SSH succeeds without a public VM address, ping receives responses from the control plane, and the HTTPS request returns headers through outbound access. The data disk is attached but intentionally unformatted and unmounted until milestone 2.

9. Confirm independent access to state, backup storage, and recovery credentials from this operator machine, not from a primary VM. Run the first commands as the infrastructure operator, who must also be able to access state. In a separate shell authenticated as a member of `recovery_reader_member`, run the final backup listing. Do not upload test data to the backup bucket. If you need conclusive evidence during a Finland outage, repeat these read-only checks while the primary VMs are stopped, then start them again.

   ```bash
   terraform state list
   gcloud storage buckets describe gs://REPLACE_WITH_STATE_BUCKET --project="$PROJECT_ID"
   gcloud storage buckets describe "gs://$(terraform -chdir=../shared output -raw backup_bucket_name)" --project="$PROJECT_ID"
   gcloud storage ls "gs://REPLACE_WITH_STATE_BUCKET/primary/"
   # In a separate shell: authenticate as the recovery reader identity.
   gcloud auth login
   gcloud storage ls gs://REPLACE_WITH_BACKUP_BUCKET/
   ```

   Expected: state resources list through GCS, both Belgium buckets respond, and the configured recovery reader identity can authenticate without using either primary VM. An empty backup bucket is expected. It cannot prove restore readiness; milestone 4 supplies backups and a restore test. Do not record this gate as passed until you send sanitized evidence for all three checks.

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

## Future Belgium cluster

Milestone 5 can instantiate `infra/modules/regional_cluster` from a separate recovery root and GCS state prefix. Pass a Belgium region and zone, a new VPC name prefix, and a non-overlapping CIDR. The recovery root creates its own worker data disk and can leave it unprotected so a drill can be destroyed. The module creates the VPC, NAT, service accounts, and VMs. The recovery root consumes no Finland VM, network, or state outputs, and it must expose the same outputs as `infra/primary` so the inventory script works unchanged. The project-level administrator grants and the backup bucket stay in `infra/shared`. Retrieve verified application backups from the Belgium bucket using the independently stored recovery identity. No recovery VMs are defined in milestone 1.
