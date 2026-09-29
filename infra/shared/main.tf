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

  # Longer than the hourly interval plus a drill window, and about two weeks
  # to notice tampering. See decision 0007.
  backup_retention_days = 14
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

  # Every backup set stays undeletable for the retention period, even by a
  # project owner, unless the unlocked policy is removed first. See decision
  # 0007.
  retention_policy {
    retention_period = local.backup_retention_days * 86400
    is_locked        = false
  }

  soft_delete_policy {
    retention_duration_seconds = 7 * 86400
  }

  # Expire sets after the retention period. With versioning, the deleted live
  # object becomes noncurrent; the second rule removes it into soft delete.
  lifecycle_rule {
    condition {
      age        = local.backup_retention_days
      with_state = "LIVE"
    }
    action {
      type = "Delete"
    }
  }

  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 1
      with_state                 = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

# Each cluster creates objects only under its own prefix. Object creation
# cannot overwrite, because overwriting needs storage.objects.delete. Remove a
# cluster from backup_clusters to fence it after failover.
resource "google_storage_bucket_iam_member" "backup_writer" {
  for_each = var.backup_clusters
  bucket   = google_storage_bucket.backups.name
  role     = "roles/storage.objectCreator"
  member   = each.value

  condition {
    title      = "${each.key}-prefix-only"
    expression = "resource.name.startsWith(\"projects/_/buckets/${google_storage_bucket.backups.name}/objects/${each.key}/\")"
  }
}

# Clusters read every prefix so a recovery cluster can restore the primary's
# sets. The cluster never holds the backup decryption key.
resource "google_storage_bucket_iam_member" "backup_reader" {
  for_each = var.backup_clusters
  bucket   = google_storage_bucket.backups.name
  role     = "roles/storage.objectViewer"
  member   = each.value
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
