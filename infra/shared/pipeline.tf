# The recovery pipeline: a GitHub Actions workflow signs in through workload
# identity federation as one service account, with no key. See decision 0013.
# The pipeline cannot apply this root: it holds no permission to change the
# IAM policy of the project, an account, a bucket, or a secret.

locals {
  # APIs the federation and the key secrets need. Disabling them on destroy
  # would break the other roots, so destroy leaves them enabled.
  pipeline_services = toset([
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "secretmanager.googleapis.com",
    "sts.googleapis.com",
  ])

  # Project-level roles of the pipeline. The three compute roles build and
  # stop the regional resources; they also hold setIamPolicy on compute
  # objects such as disks and subnetworks, which predefined roles do not
  # separate. The read roles let the preflight plan this root. None of them
  # can change project, account, bucket, or secret IAM.
  pipeline_project_roles = toset([
    "roles/compute.instanceAdmin.v1",
    "roles/compute.networkAdmin",
    "roles/compute.securityAdmin",
    "roles/compute.osAdminLogin",
    "roles/iap.tunnelResourceAccessor",
    "roles/viewer",
    "roles/iam.securityReviewer",
    "roles/iam.workloadIdentityPoolViewer",
  ])

  # The pipeline reads these with gcloud secrets versions access. Terraform
  # creates the secrets only; the key material is added outside Terraform so
  # that it never reaches the state.
  age_key_secrets = {
    sops   = "k8sdr-sops-age-key"
    backup = "k8sdr-backup-age-key"
  }

  # Terraform state the pipeline writes: the recovery root, and the recovery
  # run state that lets a later workflow run resume.
  pipeline_state_prefixes = ["recovery/", "pipeline/"]
}

resource "google_project_service" "pipeline" {
  for_each           = local.pipeline_services
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
  description               = "Workflows of the k8s-dr repository."

  depends_on = [google_project_service.pipeline]
}

# Only a job of this repository that runs in the protected environment on
# main can sign in. The environment allows main only; the ref condition
# repeats that here, so a change to the environment alone opens nothing.
resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "k8s-dr"
  display_name                       = "k8s-dr recovery"

  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.repository_id" = "assertion.repository_id"
    "attribute.environment"   = "assertion.environment"
    "attribute.ref"           = "assertion.ref"
  }

  attribute_condition = join(" && ", [
    "assertion.repository_id == '${var.github_repository_id}'",
    "assertion.environment == '${var.github_environment}'",
    "assertion.ref == 'refs/heads/main'",
  ])

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "pipeline" {
  project      = var.project_id
  account_id   = "k8sdr-pipeline"
  display_name = "k8s-dr recovery pipeline"

  depends_on = [google_project_service.pipeline]
}

resource "google_service_account_iam_member" "pipeline_federation" {
  service_account_id = google_service_account.pipeline.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/${var.github_repository_id}"
}

resource "google_project_iam_member" "pipeline" {
  for_each = local.pipeline_project_roles
  project  = var.project_id
  role     = each.key
  member   = google_service_account.pipeline.member
}

# Creating a VM with a node account attached, and signing in to it through
# OS Login, both need this grant on that account. The pipeline gets it on the
# recovery accounts only.
resource "google_service_account_iam_member" "pipeline_can_use_recovery_node" {
  for_each           = { for key, account in google_service_account.node : key => account if startswith(key, "recovery/") }
  service_account_id = each.value.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.pipeline.member
}

# The fence disables the primary worker account, and the return after a
# drill enables it again. No predefined role holds those permissions without
# the right to change the account's IAM policy.
resource "google_project_iam_custom_role" "fence" {
  project     = var.project_id
  role_id     = "k8sdrFence"
  title       = "k8s-dr fence"
  description = "Disable a node service account to fence a cluster out of the backups, and enable it again."
  permissions = [
    "iam.serviceAccounts.disable",
    "iam.serviceAccounts.enable",
    "iam.serviceAccounts.get",
  ]
}

resource "google_service_account_iam_member" "pipeline_can_fence_primary" {
  service_account_id = google_service_account.node["primary/worker"].name
  role               = google_project_iam_custom_role.fence.id
  member             = google_service_account.pipeline.member
}

# The preflight lists and decrypts the newest set, and the restore selects
# it. Reading the bucket itself lets the preflight plan this root. The
# pipeline cannot delete or overwrite a backup object. Through a VM that
# runs as the recovery worker account, it can create objects under
# recovery/.
resource "google_storage_bucket_iam_member" "pipeline_backup_reader" {
  for_each = toset(["roles/storage.objectViewer", "roles/storage.legacyBucketReader"])
  bucket   = google_storage_bucket.backups.name
  role     = each.key
  member   = google_service_account.pipeline.member
}

# The pipeline reads every state, for the outputs of the shared and primary
# roots, and writes only under its own prefixes.
resource "google_storage_bucket_iam_member" "pipeline_state_reader" {
  bucket = var.state_bucket_name
  role   = "roles/storage.objectViewer"
  member = google_service_account.pipeline.member
}

resource "google_storage_bucket_iam_member" "pipeline_state_writer" {
  bucket = var.state_bucket_name
  role   = "roles/storage.objectUser"
  member = google_service_account.pipeline.member

  condition {
    title = "pipeline-prefixes-only"
    expression = join(" || ", [
      for prefix in local.pipeline_state_prefixes :
      "resource.name.startsWith(\"projects/_/buckets/${var.state_bucket_name}/objects/${prefix}\")"
    ])
  }
}

resource "google_secret_manager_secret" "age_key" {
  for_each  = local.age_key_secrets
  project   = var.project_id
  secret_id = each.value
  labels    = local.labels

  replication {
    user_managed {
      dynamic "replicas" {
        for_each = var.secret_locations
        content {
          location = replicas.value
        }
      }
    }
  }

  depends_on = [google_project_service.pipeline]
}

resource "google_secret_manager_secret_iam_member" "pipeline_age_key" {
  for_each  = google_secret_manager_secret.age_key
  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.pipeline.member
}
