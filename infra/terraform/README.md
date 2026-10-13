# Terraform

Three root modules, each with its own state in `gs://<project_id>-tfstate`
([ADR-010](../../docs/adr/ADR-010-infrastructure-and-deploy-ownership.md)):

| Root | Creates | Backend config |
|---|---|---|
| `gcp/bootstrap/` | GCP project, billing link, state bucket | `backend-bootstrap.hcl` (after the first apply) |
| `gcp/` | APIs, Artifact Registry, service accounts, secret placeholders, Cloud Run, BigQuery, WIF, budget | `backend-gcp.hcl` |
| `supabase/` | Postern's own Supabase project (`postern-control`) | `backend-supabase.hcl` |

The first-time setup order is in [docs/runbooks/infra-setup.md](../../docs/runbooks/infra-setup.md).

**Rules (CLAUDE.md):**

- Show `terraform plan` and wait for Dheeru's approval before every `apply`.
- Never run `destroy` without an explicit yes.
- Never commit `*.tfvars` (only `*.tfvars.example`) or state.

`.terraform.lock.hcl` files are committed, with provider hashes for linux_amd64,
windows_amd64 and darwin_arm64. Update them with:

```bash
terraform providers lock -platform=linux_amd64 -platform=windows_amd64 -platform=darwin_arm64
```

`tests/invariants/test_cost_ceilings.py` and `tests/credential_audit/test_gcp_iam.py` parse
these files in CI and fail if a cost ceiling, a secret boundary or the WIF restriction is
weakened.
