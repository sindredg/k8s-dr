locals {
  labels = {
    environment = "primary"
    project     = "k8s-dr"
  }
}

module "primary_cluster" {
  source = "../modules/regional_cluster"

  project_id               = var.project_id
  name_prefix              = var.name_prefix
  region                   = var.primary_region
  zone                     = var.primary_zone
  subnet_cidr              = var.primary_subnet_cidr
  machine_type             = var.machine_type
  boot_disk_size_gb        = var.boot_disk_size_gb
  worker_data_disk_size_gb = var.worker_data_disk_size_gb
  boot_image               = var.boot_image
  labels                   = local.labels
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

resource "google_project_iam_member" "iap_tunnel_user" {
  project = var.project_id
  role    = "roles/iap.tunnelResourceAccessor"
  member  = var.admin_member
}

resource "google_project_iam_member" "os_admin_login" {
  project = var.project_id
  role    = "roles/compute.osAdminLogin"
  member  = var.admin_member
}

resource "google_project_iam_member" "instance_admin" {
  project = var.project_id
  role    = "roles/compute.instanceAdmin.v1"
  member  = var.admin_member
}

resource "google_service_account_iam_member" "admin_can_use_control_plane" {
  service_account_id = module.primary_cluster.service_account_ids["control-plane"]
  role               = "roles/iam.serviceAccountUser"
  member             = var.admin_member
}

resource "google_service_account_iam_member" "admin_can_use_worker" {
  service_account_id = module.primary_cluster.service_account_ids["worker"]
  role               = "roles/iam.serviceAccountUser"
  member             = var.admin_member
}
