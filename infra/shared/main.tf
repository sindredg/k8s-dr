# Resources every regional root depends on. They must outlive any one region,
# so no regional root owns them.

locals {
  labels = {
    environment = "shared"
    project     = "k8s-dr"
  }

  # Project-level grants for administering nodes in any region through IAP and
  # OS Login. google_project_iam_member is additive, so exactly one root may
  # own each grant; destroying a recovery root must not revoke primary access.
  admin_project_roles = toset([
    "roles/iap.tunnelResourceAccessor",
    "roles/compute.osAdminLogin",
    "roles/compute.instanceAdmin.v1",
  ])
}

resource "google_storage_bucket" "backups" {
  project                     = var.project_id
  name                        = var.backup_bucket_name
  location                    = var.backup_bucket_location
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = local.labels

  versioning {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_storage_bucket_iam_member" "backup_operator" {
  bucket = google_storage_bucket.backups.name
  role   = "roles/storage.objectAdmin"
  member = var.backup_operator_member
}

resource "google_storage_bucket_iam_member" "recovery_reader" {
  bucket = google_storage_bucket.backups.name
  role   = "roles/storage.objectViewer"
  member = var.recovery_reader_member
}

resource "google_project_iam_member" "admin" {
  for_each = local.admin_project_roles
  project  = var.project_id
  role     = each.key
  member   = var.admin_member
}
