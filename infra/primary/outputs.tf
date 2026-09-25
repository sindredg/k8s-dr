output "instance_names" {
  description = "Primary node names keyed by role."
  value       = module.primary_cluster.instance_names
}

output "internal_ips" {
  description = "Primary private node addresses keyed by role."
  value       = module.primary_cluster.internal_ips
}

output "project_id" {
  description = "Google Cloud project containing the primary cluster."
  value       = var.project_id
}

output "zone" {
  description = "Zone containing the cluster nodes."
  value       = var.primary_zone
}

output "subnet_cidr" {
  description = "IPv4 range assigned to the cluster subnet."
  value       = var.primary_subnet_cidr
}

output "control_plane_internal_ip" {
  description = "Private address for the node connectivity check."
  value       = module.primary_cluster.internal_ips["control-plane"]
}

output "worker_name" {
  description = "Worker VM name for IAP checks."
  value       = module.primary_cluster.instance_names["worker"]
}

output "worker_data_disk_name" {
  description = "Dedicated worker data disk."
  value       = google_compute_disk.worker_data.name
}

output "network_name" {
  description = "Primary VPC network."
  value       = module.primary_cluster.network_name
}
