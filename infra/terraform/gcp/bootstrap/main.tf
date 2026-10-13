# Bootstrap: the GCP project, its billing link, and the bucket that holds every other
# root module's Terraform state. Applied once with local state, then migrated into the
# bucket it creates (docs/runbooks/infra-setup.md).

terraform {
  required_version = "~> 1.16"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.6"
    }
  }
}

# Creating the project needs no quota project, so the default provider sends none.
provider "google" {}

# Everything inside the project bills API quota to the project itself.
provider "google" {
  alias                 = "in_project"
  project               = var.project_id
  billing_project       = var.project_id
  user_project_override = true
}

resource "google_project" "postern" {
  name            = "Postern"
  project_id      = var.project_id
  billing_account = var.billing_account
  deletion_policy = "PREVENT"
  labels = {
    app        = "postern"
    managed_by = "terraform"
  }
}

# Enabled through the default provider: the new project can't be its own quota project
# until serviceusage is on, which is what this resource turns on.
resource "google_project_service" "bootstrap" {
  project = google_project.postern.project_id
  for_each = toset([
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

resource "google_storage_bucket" "tfstate" {
  provider = google.in_project
  name     = "${var.project_id}-tfstate"
  # us-central1 regional Standard storage is inside the always-free tier; multi-region is not.
  location                    = "US-CENTRAL1"
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 20
      with_state         = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }

  labels = {
    app        = "postern"
    managed_by = "terraform"
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.bootstrap]
}
