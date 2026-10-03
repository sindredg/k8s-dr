# The recovery cluster in Belgium. This root reads nothing from the primary
# root or its state, so it applies while the primary region is lost. See
# decision 0008.
locals {
  labels = {
    environment = "recovery"
    project     = "k8s-dr"
  }
}

module "recovery_cluster" {
  source = "../modules/regional_cluster"

  project_id          = var.project_id
  name_prefix         = var.name_prefix
  region              = var.recovery_region
  zone                = var.recovery_zone
  subnet_cidr         = var.recovery_subnet_cidr
  machine_type        = var.machine_type
  boot_disk_size_gb   = var.boot_disk_size_gb
  worker_data_disk_id = google_compute_disk.worker_data.id
  boot_image          = var.boot_image
  labels              = local.labels
}

# No prevent_destroy: the disk holds restored data only, and terraform destroy
# must remove the whole environment after a drill.
resource "google_compute_disk" "worker_data" {
  project = var.project_id
  name    = "${var.name_prefix}-worker-data"
  zone    = var.recovery_zone
  type    = "pd-balanced"
  size    = var.worker_data_disk_size_gb
  labels  = local.labels
}

resource "google_service_account_iam_member" "admin_can_use_node" {
  for_each           = module.recovery_cluster.service_account_ids
  service_account_id = each.value
  role               = "roles/iam.serviceAccountUser"
  member             = var.admin_member
}
