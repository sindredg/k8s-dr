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
