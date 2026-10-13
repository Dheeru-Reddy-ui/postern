output "project_id" {
  value = google_project.postern.project_id
}

output "project_number" {
  value = google_project.postern.number
}

output "state_bucket" {
  description = "Use as `bucket` in infra/terraform/backend-*.hcl."
  value       = google_storage_bucket.tfstate.name
}
