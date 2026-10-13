# The bootstrap's own state lives in the bucket it created. The first apply ran with local
# state, then was migrated with:
#   terraform init -migrate-state -backend-config=../../backend-bootstrap.hcl
# (docs/runbooks/infra-setup.md, step 3).

terraform {
  backend "gcs" {}
}
