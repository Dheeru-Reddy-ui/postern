output "project_ref" {
  value = supabase_project.control.id
}

output "database_password" {
  description = "Operator-only, for `supabase db push`. Never put it in CI or a runtime secret."
  value       = random_password.db.result
  sensitive   = true
}
