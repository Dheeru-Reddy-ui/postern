# Changelog

All notable changes to Postern are recorded here, one section per phase.

## [Unreleased]

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
