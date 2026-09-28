locals {
  labels = {
    environment = "primary"
    project     = "k8s-dr"
  }
}

module "primary_cluster" {
  source = "../modules/regional_cluster"

  project_id          = var.project_id
  name_prefix         = var.name_prefix
  region              = var.primary_region
  zone                = var.primary_zone
  subnet_cidr         = var.primary_subnet_cidr
  machine_type        = var.machine_type
  boot_disk_size_gb   = var.boot_disk_size_gb
  worker_data_disk_id = google_compute_disk.worker_data.id
  boot_image          = var.boot_image
  labels              = local.labels
}

# The primary disk holds live service data. A recovery root creates its own
# disk and can leave it unprotected so drills can be torn down.
resource "google_compute_disk" "worker_data" {
  project = var.project_id
  name    = "${var.name_prefix}-worker-data"
  zone    = var.primary_zone
  type    = "pd-balanced"
  size    = var.worker_data_disk_size_gb
  labels  = local.labels

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_service_account_iam_member" "admin_can_use_node" {
  for_each           = module.primary_cluster.service_account_ids
  service_account_id = each.value
  role               = "roles/iam.serviceAccountUser"
  member             = var.admin_member
}

moved {
  from = google_service_account_iam_member.admin_can_use_control_plane
  to   = google_service_account_iam_member.admin_can_use_node["control-plane"]
}

moved {
  from = google_service_account_iam_member.admin_can_use_worker
  to   = google_service_account_iam_member.admin_can_use_node["worker"]
}
