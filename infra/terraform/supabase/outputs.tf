output "project_ref" {
  value = supabase_project.control.id
}

output "pooler_urls" {
  description = "Pooler connection strings by mode, with a password placeholder."
  value       = data.supabase_pooler.control.url
}

output "database_password" {
  description = "Operator-only, for `supabase db push`. Never put it in CI or a runtime secret."
  value       = random_password.db.result
  sensitive   = true
}
