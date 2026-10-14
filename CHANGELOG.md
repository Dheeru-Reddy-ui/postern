# Changelog

All notable changes to Postern are recorded here, one section per phase.

## [Unreleased]

### Phase 2: control plane (all "Done when" checks met; awaiting Dheeru's sign-off)

**Applied to `postern-control` on 2026-10-14:**
- three migrations;
- registry synced (8 agents);
- game-day drill run `142e275c…` driven through every state in the SQL editor, then
  rebuilt by `postern replay` from the audit log alone;
- `postern audit verify` finds the 15-row chain intact
  (`docs/incidents/2026-10-14-game-day-control-plane-drill.md`).


- **`postern` schema** (declarative files in `supabase/schemas/`, ADR-011):
  - users, connections (tokens as envelope ciphertext only), agents, runs, steps, findings,
    proposals, notifications and ledger;
  - RLS on every table, with components reading through explicit policies and people
    reading only their own rows.
- **Append-only, hash-chained `audit_log`:**
  - `UPDATE`, `DELETE` and `TRUNCATE` are revoked from every role and blocked by triggers;
  - SHA-256 chain under an advisory lock, verified in SQL (`postern.verify_audit_chain`) and
    independently in Python.
- **Run state machine** enforced by triggers, plus a human gate, plus a commit-time check
  that every status change has an audit row in the same transaction.
- **The only write path:** `SECURITY DEFINER` functions (`enqueue_run`, `transition_run`,
  `record_step`, `record_human_decision`, `write_audit`, `sync_agent`), with idempotent
  enqueue.
- **Roles:** `postern_api` and `postern_worker` (still no login).
- **Queues and schedule:** pgmq queues `tier0`, `tier1`, `tier2`, `meta`; pg_cron job
  `postern-nightly-runs` (02:17 UTC).
- **`agents/registry.yaml`** with 7 planned agents plus the `drill_echo` drill agent.
- **Registry code:** the tool catalog (`postern/core/tools.py`), the validator, and the
  compiler with `agents/manifests.lock.json`.
- **CLI:** `postern registry validate|compile|sync`, `postern replay <run_id>`,
  `postern audit verify`.
- **15 threat-model tests now real:** TM-14, TM-18 (3), TM-22 (4), TM-23 (3), TM-24 (3),
  TM-25 (2). CI runs them against a fresh Supabase Postgres.
- **Local Supabase ports** moved to 55320–55329.

### Phase 1: GCP and Supabase infrastructure as code (complete, signed off by Dheeru 2026-10-14)

**Applied 2026-10-13/14:**
- GCP project `postern-prod-8ybj`, with state in `gs://postern-prod-8ybj-tfstate`.
- Postern's own Supabase project `postern-control` (ref `bpymxbzldxhsqrvrybyo`).
- pgmq 1.5.1 and pg_cron 1.6.4.
- Keyless CI deploys through WIF; four successful deploys so far.
- No drift in any of the three Terraform roots.
- Game-day rollback drill done by hand from the runbook
  (`docs/incidents/2026-10-13-game-day-api-rollback.md`).
- **Test budget alert received:** "1% of budget reached" email at 2026-10-13 20:06 UTC, plus
  a matching Pub/Sub message (₹0.34 cost before credits against ₹1). The test budget was then
  removed.
- Both budgets publish to Pub/Sub topic `postern-budget-alerts`.

- **Terraform roots** `infra/terraform/gcp/bootstrap`, `gcp` and `supabase` (ADR-010):
  - the project, a private versioned state bucket and the APIs Postern needs;
  - Artifact Registry with cleanup policies;
  - service accounts for api, worker and deployer;
  - eight Secret Manager placeholders, whose access comes from `secret_access.json`;
  - `postern-api` and `postern-worker` with cost ceilings;
  - BigQuery `postern` with seven documented, day-partitioned tables and dedup views;
  - GitHub Workload Identity Federation pinned to `ci.yml` on `main`;
  - the ₹100 budget, plus an optional ₹1 test budget;
  - Postern's own Supabase project with SSL enforced.
- **Supabase CLI config,** a declarative schema and a migration enabling pgmq and pg_cron.
- **Hello world:** `python -m postern` runs the API (`GET /health`) or the worker, depending on
  `POSTERN_COMPONENT`. A multi-stage, digest-pinned, non-root Dockerfile.
- **CI** builds the image on every PR. A deploy job (WIF, by digest, with a smoke test) runs on
  `main` once the `GCP_*` repository variables exist.
- **Runbooks:** `infra-setup.md`, `supabase-setup.md` and `deploy-and-rollback.md`.
- **Threat-model tests now real:**
  - TM-05: secret boundaries, and the state bucket is private
  - TM-07: no user credential names in config
  - TM-12: cost ceilings
  - TM-29: no service-account keys, and WIF is restricted
- **Tests treat warnings as errors.** Starlette's test client now uses `httpx2`.
- **ADR-005 and the threat model corrected:** the Supabase API returns auth-config secrets as
  hashes, not plaintext. `auth:read` stays excluded.

### Phase 0: threat model, ADRs and scaffolding

- **Repo layout** from `CLAUDE.md`: empty `postern` packages, plus `evals/`, `infra/terraform/`,
  `supabase/`, `web/`, `docs/` and `tests/`.
- **`docs/threat-model.md`** v0.2, reviewed: 31 failure modes, each with impact, likelihood,
  structural preventions, detection and phase, and 96 named tests.
- **Threat-model tests** in `tests/invariants/` and `tests/credential_audit/`:
  - 93 are `xfail(strict=True)` until their phase lands;
  - 3 Phase 0 preventions are already real tests: locked CI installs, SHA-pinned actions,
    and no committed secrets.
- **`tests/unit/test_threat_model.py`** keeps the threat model and its tests in one-to-one
  sync.
- **ADRs:**
  - ADR-001: agent runtime (thin loop plus LiteLLM)
  - ADR-002: queue (pgmq)
  - ADR-003: warehouse ingestion (Storage Write API through an outbox)
- **Decisions D1–D6 resolved:**
  - ADR-004: row values never reach a prompt
  - ADR-005: Supabase OAuth scopes `projects:read`, `database:read`, `rest:read`,
    `storage:read`
  - ADR-006: fix PRs only on repos GitHub protects
  - ADR-007: an isolated validator Job for fix validation
  - ADR-008: two free Supabase projects in separate orgs
  - ADR-009: Apache-2.0
- **New failure mode TM-31:** untrusted repository code runs inside Postern during fix
  validation.
- **Tooling:**
  - uv with a locked `uv.lock`, ruff, and mypy (strict flags on `postern.core`), plus
    pytest and pre-commit.
  - Pre-commit hooks for whitespace, YAML and TOML, private keys, `.env` files and lockfile
    sync.
  - Ruff `banned-api` rules backstop invariants 6 and 9 at lint time.
- **GitHub Actions CI** on every PR and on pushes to `main`: lint, types, tests and repo
  hygiene. Actions are pinned to commit SHAs, and the runner is pinned to `ubuntu-24.04`.
- **`LICENSE`** (Apache-2.0) and **`docs/architecture.md` v0.**
