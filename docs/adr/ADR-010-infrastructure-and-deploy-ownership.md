# ADR-010: Infrastructure layout and deploy ownership

**Status:** Proposed · **Date:** 2026-10-13 · **Decider:** Dheeru

## Context

Phase 1 builds every GCP and Supabase resource with Terraform. CI must deploy on every push
to `main`, and Terraform must show no drift. CLAUDE.md requires a plan plus Dheeru's
approval before every `apply`, so CI can't run `terraform apply`.

## Decision

**Three Terraform root modules,** each with its own state in one GCS bucket:

| Root | State | Owns |
|---|---|---|
| `infra/terraform/gcp/bootstrap` | local first, then migrated to `gs://<project>-tfstate/bootstrap` | the project, its billing link, the state bucket |
| `infra/terraform/gcp` | `gs://<project>-tfstate/gcp` | APIs, Artifact Registry, service accounts, secrets, Cloud Run, BigQuery, WIF, budget |
| `infra/terraform/supabase` | `gs://<project>-tfstate/supabase` | Postern's own Supabase project and its settings |

**Terraform owns the shape; CI owns which image runs.** Terraform declares each service and
job: identity, scaling, limits, environment and IAM. CI builds the image and rolls it out
with `gcloud run deploy` and `gcloud run jobs update`, by digest. Terraform ignores
`image`, `client`, `client_version` and the service's `traffic`, so deploys and rollbacks
never show up as drift.

**One image for every component.** `python -m postern` reads `POSTERN_COMPONENT` (`api` or
`worker`), which Terraform sets per service or job. Phase 3 splits out a worker-only image so
LiteLLM is never installed in the API (TM-28).

**The deploy job runs inside `ci.yml`.** It runs after the checks pass, only on pushes to
`main`, and only once the repository variables from `terraform output` exist. The WIF
provider accepts only that workflow file, on `main`, on `push`, from this repo's numeric ID.

**Extensions are applied by a migration.** The Supabase provider (v1.11) has no extension
resource, so pgmq and pg_cron are enabled by
`supabase/migrations/20261013000000_enable_pgmq_and_pg_cron.sql` through the Supabase CLI.
The steps are in docs/runbooks/supabase-setup.md.

## Consequences

- **The deployer has project-level `roles/run.developer`.** Rolling out a revision polls
  project-scoped operations, so a service-level grant isn't enough. The deployer can change
  any Cloud Run resource in this project, which holds only Postern.
- **Rollbacks are sticky.** Rolling back pins traffic to a named revision. Later deploys
  create revisions that get no traffic, so CI's smoke test fails until someone rolls forward
  on purpose (docs/runbooks/deploy-and-rollback.md).
- **The control-plane database password lives in Terraform state.** The state bucket is
  private, versioned and uniform-access. Runtime components never use this password; they
  get their own Postgres roles in Phase 2.
- **Cost:**
  - Secret placeholders cost nothing until they hold values.
  - Once all eight hold a version, two are over the free six, about $0.12/month (roughly ₹10).
  - Everything else is inside the free tier at Phase 1 volumes.
