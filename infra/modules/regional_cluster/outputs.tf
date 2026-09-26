output "instance_names" {
  description = "Node names keyed by role."
  value       = { for role, node in google_compute_instance.node : role => node.name }
}

output "internal_ips" {
  description = "Private node addresses keyed by role."
  value       = { for role, node in google_compute_instance.node : role => node.network_interface[0].network_ip }
}

output "network_name" {
  description = "Regional cluster VPC name."
  value       = google_compute_network.cluster.name
}

output "service_account_ids" {
  description = "Node service account resource IDs keyed by role."
  value       = { for role, account in google_service_account.node : role => account.name }
}

output "public_web_address" {
  description = "Static external address of the load balancer in front of Traefik."
  value       = google_compute_address.public_web.address
}
