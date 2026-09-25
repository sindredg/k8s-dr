output "backup_bucket_name" {
  description = "Offsite bucket for verified application backup sets."
  value       = google_storage_bucket.backups.name
}
