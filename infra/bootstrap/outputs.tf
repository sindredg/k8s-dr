output "state_bucket_name" {
  description = "Bucket to use for the bootstrap and primary GCS backends."
  value       = google_storage_bucket.state.name
}
