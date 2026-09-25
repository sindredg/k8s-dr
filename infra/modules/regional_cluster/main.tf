locals {
  node_roles = toset(["control-plane", "worker"])
  node_tag   = "${var.name_prefix}-node"
  iap_tag    = "${var.name_prefix}-iap-ssh"
}

resource "google_compute_network" "cluster" {
  project                 = var.project_id
  name                    = "${var.name_prefix}-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "cluster" {
  project                  = var.project_id
  name                     = "${var.name_prefix}-subnet"
  region                   = var.region
  network                  = google_compute_network.cluster.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true
}

resource "google_compute_router" "outbound" {
  project = var.project_id
  name    = "${var.name_prefix}-router"
  region  = var.region
  network = google_compute_network.cluster.id
}

resource "google_compute_router_nat" "outbound" {
  project                            = var.project_id
  name                               = "${var.name_prefix}-nat"
  region                             = var.region
  router                             = google_compute_router.outbound.name
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  subnetwork {
    name                    = google_compute_subnetwork.cluster.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }
}

resource "google_compute_firewall" "iap_ssh" {
  project       = var.project_id
  name          = "${var.name_prefix}-iap-ssh"
  network       = google_compute_network.cluster.name
  direction     = "INGRESS"
  source_ranges = ["35.235.240.0/20"]
  target_tags   = [local.iap_tag]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_firewall" "node_internal" {
  project       = var.project_id
  name          = "${var.name_prefix}-node-internal"
  network       = google_compute_network.cluster.name
  direction     = "INGRESS"
  source_ranges = [var.subnet_cidr]
  target_tags   = [local.node_tag]

  allow {
    protocol = "tcp"
  }

  allow {
    protocol = "udp"
  }

  allow {
    protocol = "icmp"
  }
}

resource "google_service_account" "node" {
  for_each     = local.node_roles
  project      = var.project_id
  account_id   = "${var.name_prefix}-${each.key == "control-plane" ? "cp" : "worker"}"
  display_name = "${var.name_prefix} ${each.key} node"
}

resource "google_compute_instance" "node" {
  for_each     = local.node_roles
  project      = var.project_id
  name         = "${var.name_prefix}-${each.key}"
  zone         = var.zone
  machine_type = var.machine_type
  tags         = [local.node_tag, local.iap_tag]
  labels       = merge(var.labels, { role = each.key == "control-plane" ? "control-plane" : "worker" })

  boot_disk {
    auto_delete = true

    initialize_params {
      image = var.boot_image
      size  = var.boot_disk_size_gb
      type  = "pd-balanced"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.cluster.id
  }

  metadata = {
    enable-oslogin         = "TRUE"
    block-project-ssh-keys = "TRUE"
  }

  service_account {
    email  = google_service_account.node[each.key].email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  # The standalone attachment owns this field on the worker.
  lifecycle {
    ignore_changes = [attached_disk]
  }

  depends_on = [google_compute_router_nat.outbound]
}

# The calling root owns the data disk so it can decide whether the disk is
# protected from destroy. A lifecycle setting cannot come from a variable.
resource "google_compute_attached_disk" "worker_data" {
  project     = var.project_id
  disk        = var.worker_data_disk_id
  instance    = google_compute_instance.node["worker"].id
  device_name = "worker-data"
}
