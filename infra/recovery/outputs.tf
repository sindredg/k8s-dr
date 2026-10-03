output "instance_names" {
  description = "Recovery node names keyed by role."
  value       = module.recovery_cluster.instance_names
}

output "internal_ips" {
  description = "Recovery private node addresses keyed by role."
  value       = module.recovery_cluster.internal_ips
}

output "project_id" {
  description = "Google Cloud project containing the recovery cluster."
  value       = var.project_id
}

output "zone" {
  description = "Zone containing the cluster nodes."
  value       = var.recovery_zone
}

output "subnet_cidr" {
  description = "IPv4 range assigned to the cluster subnet."
  value       = var.recovery_subnet_cidr
}

output "control_plane_internal_ip" {
  description = "Private address for the node connectivity check."
  value       = module.recovery_cluster.internal_ips["control-plane"]
}

output "worker_name" {
  description = "Worker VM name for IAP checks."
  value       = module.recovery_cluster.instance_names["worker"]
}

output "worker_data_disk_name" {
  description = "Dedicated worker data disk."
  value       = google_compute_disk.worker_data.name
}

output "network_name" {
  description = "Recovery VPC network."
  value       = module.recovery_cluster.network_name
}

output "public_web_address" {
  description = "Recovery public address for the git-dr.sindrg.com DNS record, and for git.sindrg.com after a cutover."
  value       = module.recovery_cluster.public_web_address
}
