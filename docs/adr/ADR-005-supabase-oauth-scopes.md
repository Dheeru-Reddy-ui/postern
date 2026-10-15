# ADR-005: Supabase OAuth scope allowlist

**Status:** Accepted (Dheeru, 2026-10-13) · **Amended 2026-10-14 (Phase 3): `rest:read`
removed; temporary-access guard added; scopes also requested explicitly; catalog checks
replace splinter. Amendment accepted (Dheeru, 2026-10-15).** ·
**Resolves:** threat-model decision D2

## Context

Invariants 1 and 2: no Postern credential can write to a user's database, and Postern never
requests a scope that can return secret API keys.

Supabase OAuth scopes are set on the OAuth app in the dashboard. The authorize endpoint also
takes an optional, space-separated `scope` parameter (in the spec since at least 2026-10-14),
and Postern always sends exactly its three scopes there too, so a misconfigured app can't
silently widen a grant. Each scope unlocks a fixed set of Management API endpoints. We mapped every scope to its endpoints using
the `x-oauth-scope` field in the official OpenAPI spec
(`https://api.supabase.com/api/v1-json`): first on 2026-10-13, and again on 2026-10-14 with a
scan of every response schema for secret-like fields.

## What Postern needs, and the scope each needs

| Need | Endpoint | Scope |
|---|---|---|
| List the user's projects and their status | `GET /v1/projects`, `GET /v1/projects/{ref}`, `GET /v1/projects/{ref}/health` | `projects:read` |
| Security and performance advisors | `GET /v1/projects/{ref}/advisors/{security,performance}` | `database:read` |
| Postern's own catalog checks (ADR-012) | `POST /v1/projects/{ref}/database/query/read-only` | `database:read` |
| Whether temporary database access is on (see below) | `GET /v1/projects/{ref}/jit-access` | `database:read` |
| Schema shape | `GET /v1/projects/{ref}/types/typescript` | `database:read` |
| Applied migrations, to detect drift from the repo (Phase 5) | `GET /v1/projects/{ref}/database/migrations` | `database:read` |
| Bucket list and `public` flags (Phase 4 storage findings) | `GET /v1/projects/{ref}/storage/buckets` | `storage:read` |

Postern reads which schemas the Data API exposes through the read-only query endpoint
(`pgrst.db_schemas`, defaulting to `public`). Whether that setting is visible to
`supabase_read_only_user` is checked on the live testbed in Phase 3; if it isn't, the
default of `public` holds and the advisor's lints still cover other exposed schemas.

## Decision

**The allowlist is `projects:read`, `database:read` and `storage:read`.**

Explicitly excluded:

| Scope | Why |
|---|---|
| every `*:write` | Invariant 1 |
| `secrets:read` and `secrets:write` | Invariant 2. `GET /v1/projects/{ref}/api-keys` is `secrets:read`. |
| `rest:read` | **Removed 2026-10-14.** Its only endpoint, `GET /v1/projects/{ref}/postgrest`, returns `PostgrestConfigWithJWTSecretResponse`, which has a `jwt_secret` field. A project's JWT secret can mint `service_role` tokens, so this is worse than any API key. |
| `auth:read` | The auth config has fields for `smtp_pass`, every OAuth provider's client secret, and auth-hook secrets. The API returns them as hashes, not plaintext (per the Supabase Terraform provider docs). Postern has no need for auth config, so it stays excluded until an ADR shows one. |
| `organizations:read` | Not needed to list projects, and it includes organization member lists (personal data) |
| `edge_functions:read`, `analytics:read`, `domains:read`, `environment:read` | Not needed yet. Function source could contain hard-coded secrets, so it needs its own ADR first. |

**The client only calls endpoints in the table above, and only once a feature uses them.** `database:read` also unlocks
endpoints Postern must never call: saved SQL snippets, backups, pooler and Postgres
configuration, and `POST /v1/projects/{ref}/database/jit`. Scope alone isn't enough, so the
Supabase client is an endpoint allowlist (TM-06), and the read-only query endpoint runs only
allowlisted statements (TM-01).

**Temporary access guard.** `database:read` includes the JIT endpoints behind Supabase's
"temporary access" feature. That feature lets a project's users log into Postgres with a
personal access token as the password, as a role an admin mapped them to. It's off by
default, and Supabase doesn't say whether an OAuth token is accepted there. Until that's
proven either way:

- Before storing any token, and again before every run, Postern reads `GET /jit-access`. If
  temporary access is enabled, Postern refuses the connection, destroys the stored tokens and
  tells the owner why
  (`test_connection_refused_when_temporary_access_enabled`).
- Phase 4's credential audit enables temporary access on the testbed, maps the testbed owner
  to a writable role, and tries to log into Postgres with Postern's OAuth token. It must fail.
  If it ever succeeds, invariant 1 is broken: stop and tell Dheeru.

## Consequences

- **Tests prove this:**
  - `test_oauth_scopes_are_within_read_only_allowlist` (configuration);
  - `test_supabase_client_calls_only_allowlisted_endpoints` (code);
  - `test_connection_refused_when_temporary_access_enabled` (guard);
  - the live credential-audit suite (Phase 4), which shows a real token is refused on every
    excluded endpoint and can't be used as a database login.
- **Adding a scope later forces every user to re-authorize.** It also requires a new ADR and
  updates to the credential audit and to onboarding copy, because users are shown the exact
  scopes.
- **`database:read` can still `SELECT` any table** through the read-only endpoint (residual
  risk R1). The statement allowlist is the control.
- **Owners who rely on temporary access can't use Postern** until the Phase 4 audit shows
  OAuth tokens aren't accepted as database logins.
