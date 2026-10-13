terraform {
  required_version = "~> 1.16"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.6"
    }
  }
  # Bucket and prefix come from infra/terraform/backend-gcp.hcl:
  #   terraform init -backend-config=../backend-gcp.hcl
  backend "gcs" {}
}

provider "google" {
  project               = var.project_id
  region                = var.region
  billing_project       = var.project_id
  user_project_override = true
  default_labels = {
    app        = "postern"
    managed_by = "terraform"
  }
}

data "google_project" "this" {}
