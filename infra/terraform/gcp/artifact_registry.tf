resource "google_artifact_registry_repository" "postern" {
  location      = var.region
  repository_id = "postern"
  format        = "DOCKER"
  description   = "Postern container images. CI pushes; Cloud Run pulls by digest."

  docker_config {
    immutable_tags = true
  }

  # Cost ceiling (TM-12): the free tier is 0.5 GB, so keep only recent images.
  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }
  cleanup_policies {
    id     = "delete-older-than-14-days"
    action = "DELETE"
    condition {
      older_than = "1209600s"
    }
  }

  depends_on = [google_project_service.enabled]
}
