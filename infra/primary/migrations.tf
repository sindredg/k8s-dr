# One-time state moves from the 2026-09 shared-root refactor. See
# docs/runbooks/terraform-shared-root-migration.md. These blocks are no-ops
# after the migration and on a fresh deployment. Delete them after the
# migration is recorded.

moved {
  from = module.primary_cluster.google_compute_disk.worker_data
  to   = google_compute_disk.worker_data
}

# infra/shared now manages these resources. Forget them here without
# destroying them.
removed {
  from = google_storage_bucket.backups

  lifecycle {
    destroy = false
  }
}

removed {
  from = google_storage_bucket_iam_member.backup_operator

  lifecycle {
    destroy = false
  }
}

removed {
  from = google_storage_bucket_iam_member.recovery_reader

  lifecycle {
    destroy = false
  }
}

removed {
  from = google_project_iam_member.iap_tunnel_user

  lifecycle {
    destroy = false
  }
}

removed {
  from = google_project_iam_member.os_admin_login

  lifecycle {
    destroy = false
  }
}

removed {
  from = google_project_iam_member.instance_admin

  lifecycle {
    destroy = false
  }
}
