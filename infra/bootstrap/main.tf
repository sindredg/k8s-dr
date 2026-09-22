resource "google_storage_bucket" "state" {
  name                        = var.state_bucket_name
  location                    = var.state_bucket_location
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_storage_bucket_iam_member" "state_operator" {
  bucket = google_storage_bucket.state.name
  role   = "roles/storage.objectAdmin"
  member = var.state_operator_member
}
