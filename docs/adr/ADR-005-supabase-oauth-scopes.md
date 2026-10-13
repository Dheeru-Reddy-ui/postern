# ADR-005: Supabase OAuth scope allowlist

**Status:** Accepted (Dheeru, 2026-10-13) · **Resolves:** threat-model decision D2

## Context

Invariants 1 and 2: no Postern credential can write to a user's database, and Postern never
requests a scope that can return secret API keys.

Supabase OAuth scopes are set on the OAuth app in the dashboard, not per request. Each one
unlocks a fixed set of Management API endpoints. On 2026-10-13 we mapped every scope to its
endpoints using the `x-oauth-scope` field in the official OpenAPI spec
(`https://api.supabase.com/api/v1-json`).

## What Postern needs, and the scope each needs

| Need | Endpoint | Scope |
|---|---|---|
| List the user's projects and their status | `GET /v1/projects`, `GET /v1/projects/{ref}`, `GET /v1/projects/{ref}/health` | `projects:read` |
| Security and performance advisors | `GET /v1/projects/{ref}/advisors/{security,performance}` | `database:read` |
| splinter fallback and catalog introspection | `POST /v1/projects/{ref}/database/query/read-only` | `database:read` |
| Schema shape | `GET /v1/projects/{ref}/types/typescript` | `database:read` |
| Applied migrations, to detect drift from the repo (Phase 5) | `GET /v1/projects/{ref}/database/migrations` | `database:read` |
| Which schemas the Data API exposes, so probes target the right ones | `GET /v1/projects/{ref}/postgrest` | `rest:read` |
| Bucket list and `public` flags (Phase 4 storage findings) | `GET /v1/projects/{ref}/storage/buckets` | `storage:read` |

## Decision

**The allowlist is `projects:read`, `database:read`, `rest:read` and `storage:read`.**

Explicitly excluded:

| Scope | Why |
|---|---|
| every `*:write` | Invariant 1 |
| `secrets:read` and `secrets:write` | Invariant 2. `GET /v1/projects/{ref}/api-keys` is `secrets:read`. |
| `auth:read` | The auth config response schema includes `smtp_pass`, every OAuth provider's client secret, and auth-hook secrets |
| `organizations:read` | Not needed to list projects, and it includes organization member lists (personal data) |
| `edge_functions:read`, `analytics:read`, `domains:read`, `environment:read` | Not needed yet. Function source could contain hard-coded secrets, so it needs its own ADR first. |

`database:read` also unlocks endpoints Postern must never call: saved SQL snippets, backups,
pooler and Postgres configuration, and JIT access. Scope alone isn't enough, so **the
Supabase client implements only the endpoints in the table above** (TM-06). The read-only
query endpoint runs only allowlisted statements (TM-01).

## Consequences

- **Tests prove this:**
  - `test_oauth_scopes_are_within_read_only_allowlist` (configuration);
  - `test_supabase_client_calls_only_allowlisted_endpoints` (code);
  - the live credential-audit suite (Phase 4), which shows a real token is refused on every
    excluded endpoint.
- **Adding a scope later forces every user to re-authorize.** It also requires a new ADR and
  updates to the credential audit and to onboarding copy, because users are shown the exact
  scopes.
- **`database:read` can still `SELECT` any table** through the read-only endpoint (residual
  risk R1). The statement allowlist is the control.
