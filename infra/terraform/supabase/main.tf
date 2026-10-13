# Postern's OWN Supabase project (the control plane). Never a user's project.
# Extensions (pgmq, pg_cron) aren't managed by this provider; they're enabled by a
# migration in supabase/migrations (docs/runbooks/supabase-setup.md).

terraform {
  required_version = "~> 1.16"
  required_providers {
    supabase = {
      source  = "supabase/supabase"
      version = "~> 1.11"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }
  # Bucket and prefix come from infra/terraform/backend-supabase.hcl.
  backend "gcs" {}
}

# Authenticates with the operator's personal access token in SUPABASE_ACCESS_TOKEN.
# That token is never stored in the repo, in Terraform state or in CI.
provider "supabase" {}

# The database password lives only in Terraform state (a private, versioned GCS bucket)
# and is read once by the operator for migrations. Runtime components get their own
# least-privilege Postgres roles in Phase 2, not this password.
resource "random_password" "db" {
  length  = 40
  special = false
}

resource "supabase_project" "control" {
  organization_id   = var.organization_slug
  name              = "postern-control"
  database_password = random_password.db.result
  region            = var.region

  lifecycle {
    prevent_destroy = true
    ignore_changes  = [database_password]
  }
}

resource "supabase_settings" "control" {
  project_ref     = supabase_project.control.id
  ssl_enforcement = true
}
