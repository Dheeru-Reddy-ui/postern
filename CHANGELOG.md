# Changelog

All notable changes to Postern are recorded here, one section per phase.

## [Unreleased]

### Phase 0: threat model, ADRs and scaffolding

- **Repo layout** from `CLAUDE.md`: empty `postern` packages, plus `evals/`, `infra/terraform/`,
  `supabase/`, `web/`, `docs/` and `tests/`.
- **`docs/threat-model.md`:** 30 failure modes, each with impact, likelihood, structural
  preventions, detection and phase, and 90 named tests.
- **Threat-model tests** in `tests/invariants/` and `tests/credential_audit/`:
  - 87 are `xfail(strict=True)` until their phase lands;
  - 3 Phase 0 preventions are already real tests: locked CI installs, SHA-pinned actions,
    and no committed secrets.
- **`tests/unit/test_threat_model.py`** keeps the threat model and its tests in one-to-one
  sync.
- **ADRs:**
  - ADR-001: agent runtime (thin loop plus LiteLLM)
  - ADR-002: queue (pgmq)
  - ADR-003: warehouse ingestion (Storage Write API through an outbox)
- **Tooling:**
  - uv with a locked `uv.lock`, ruff, and mypy (strict flags on `postern.core`), plus
    pytest and pre-commit.
  - Pre-commit hooks for whitespace, YAML and TOML, private keys, `.env` files and lockfile
    sync.
  - Ruff `banned-api` rules backstop invariants 6 and 9 at lint time.
- **GitHub Actions CI** on every PR and on pushes to `main`: lint, types, tests and repo
  hygiene. Actions are pinned to commit SHAs.
- **`docs/architecture.md` v0.**
