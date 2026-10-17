output "backup_bucket_name" {
  description = "Offsite bucket for verified application backup sets."
  value       = google_storage_bucket.backups.name
}

output "node_service_accounts" {
  description = "Node service account emails keyed by <cluster>/<role>."
  value       = { for key, account in google_service_account.node : key => account.email }
}

output "uptime_check_id" {
  description = "Uptime check ID for reading the probe results of a drill."
  value       = google_monitoring_uptime_check_config.git.uptime_check_id
}

output "pipeline_workload_identity_provider" {
  description = "Provider name the recovery workflow passes to google-github-actions/auth."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "pipeline_service_account" {
  description = "Service account the recovery workflow signs in as."
  value       = google_service_account.pipeline.email
}

output "age_key_secrets" {
  description = "Secret Manager secret IDs of the SOPS and backup age keys, keyed sops and backup."
  value       = { for key, secret in google_secret_manager_secret.age_key : key => secret.secret_id }
}
