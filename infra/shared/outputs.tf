output "backup_bucket_name" {
  description = "Offsite bucket for verified application backup sets."
  value       = google_storage_bucket.backups.name
}

output "uptime_check_id" {
  description = "Uptime check ID for reading the probe results of a drill."
  value       = google_monitoring_uptime_check_config.git.uptime_check_id
}
