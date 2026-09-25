# One-time imports from the 2026-09 shared-root refactor. See
# docs/runbooks/terraform-shared-root-migration.md. Delete this file after the
# migration is recorded: an import block fails on a fresh deployment where the
# resource does not exist yet.

import {
  to = google_storage_bucket.backups
  id = var.backup_bucket_name
}

import {
  to = google_storage_bucket_iam_member.backup_operator
  id = "b/${var.backup_bucket_name} roles/storage.objectAdmin ${var.backup_operator_member}"
}

import {
  to = google_storage_bucket_iam_member.recovery_reader
  id = "b/${var.backup_bucket_name} roles/storage.objectViewer ${var.recovery_reader_member}"
}

import {
  for_each = local.admin_project_roles
  to       = google_project_iam_member.admin[each.key]
  id       = "${var.project_id} ${each.key} ${var.admin_member}"
}
