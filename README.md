# Postern

*Governed agents that guard your Supabase app. Read-only by construction.*

A postern is the small, guarded side gate in a fortress wall. Apps built with Lovable, Bolt,
Cursor and similar tools often leave a side door open: a public key plus a table without
row-level security. Postern watches for that door, and for health and performance problems,
and turns what it finds into tested fixes that a human approves. Postern holds no credential
that could open the door itself.

> **Status: Phase 0 complete (threat model, ADRs, scaffolding).** Nothing runs yet. See
> [PHASE_PROMPTS.md](PHASE_PROMPTS.md) for the build plan.

## What "read-only by construction" means

Safety is enforced by credentials, database constraints and code. It does not depend on
prompts asking a model to behave.

- **Supabase:** OAuth with four read-only scopes (`projects:read`, `database:read`,
  `rest:read`, `storage:read`). Postern never requests, accepts or stores a `service_role`
  key, a secret API key or a database password.
- **Exposure checks:** use only the public (publishable/anon) key you paste in, which your app
  already ships to every browser. Postern records table name, HTTP status, a capped row count
  and a timestamp. It never stores your rows.
- **GitHub:** Postern only pushes `postern/*` branches and opens pull requests on repos where
  GitHub itself requires a human-approved review before merging. Everywhere else you get
  reviewed SQL and step-by-step instructions instead.
- **Every agent is registered** with an owner, a tier, explicit tool grants, a human gate and a
  notification budget. Anything outside a grant is refused and written to an append-only
  audit log.

The full rules are in [CLAUDE.md](CLAUDE.md). How each one can fail, and the test that proves
it doesn't, is in [docs/threat-model.md](docs/threat-model.md).

## Repo layout

```
postern/          the Python package
  core/           registry, compiler, policy gate, runtime loop, checkpoints, audit
  agents/         one module per agent
  integrations/   supabase_mgmt, github_app, notify, bigquery
  llm/            gateway (the only place an LLM provider is called)
  mcp/            postern-mcp server
  api/            FastAPI app
agents/           registry.yaml (Phase 2)
evals/            golden/, safety/, judge/, calibration/
infra/terraform/  gcp/, supabase/, modules/
supabase/         Postern's own database: schemas/, migrations/
web/              Next.js landing, onboarding and report pages
docs/             adr/, runbooks/, incidents/, threat-model.md, architecture.md, value-model.md
tests/            unit/, integration/, invariants/, credential_audit/
```

## Development

Requires [uv](https://docs.astral.sh/uv/). Run every command from the repo root.

```bash
uv sync
uv run pre-commit install
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```

`tests/invariants/` and `tests/credential_audit/` hold one test per threat-model prevention.
Tests for preventions that aren't built yet are marked `xfail(strict=True)`, so they turn into
failures the moment someone implements one without removing the marker.
`tests/unit/test_threat_model.py` checks that the threat model and those tests stay in sync.

## Docs

- [Threat model](docs/threat-model.md)
- [Architecture](docs/architecture.md)
- [ADRs](docs/adr/)
- [Changelog](CHANGELOG.md)

## License

[Apache-2.0](LICENSE). See [ADR-009](docs/adr/ADR-009-license.md).
