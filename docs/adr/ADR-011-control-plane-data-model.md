# ADR-011: Control plane data model and write path

**Status:** Accepted (Dheeru, 2026-10-14) · **Date:** 2026-10-14 · **Decider:** Dheeru

## Context

Phase 2 builds Postern's control plane in its own Supabase Postgres (`postern-control`).
Several invariants have to hold there no matter which code is calling:

- **Invariant 5:** unregistered agents can't run.
- **Invariant 8:** every consequential decision is audited in the same transaction as the
  state change.
- **TM-22:** the audit log can't be rewritten.
- **TM-24:** illegal state moves are impossible, and human-gated agents need a human.
- **TM-25:** one tenant can't read another's data.

## Decision

**A private schema.** Every table lives in `postern`, which the Data API doesn't expose. RLS
is on for every table anyway (TM-25).

**Writes go only through functions.** Component roles (`postern_api`, `postern_worker`) get
`SELECT` plus `EXECUTE` on specific `SECURITY DEFINER` functions, never `INSERT` or
`UPDATE` on tables:

| Function | Who | What it does |
|---|---|---|
| `enqueue_run` | api, worker | Checks the agent is registered and active, inserts the run, audits `run.enqueued`, sends `{run_id}` to the tier's queue, all in one transaction. Idempotent on `idempotency_key`. |
| `transition_run` | worker | Moves a run's status and audits `run.transitioned` |
| `record_step` | worker | One checkpoint per step, audited `step.recorded` |
| `record_human_decision` | api | Only while `awaiting_human`, and only by the connection's owner. Audited `human.decided`. |
| `write_audit` | api, worker | Audits any other consequential decision |
| `sync_agent`, `retire_agents_not_in` | operator only | Registry sync; every grant change is audited |
| `enqueue_nightly_runs` | pg_cron | One run per active nightly agent per active project per day |

**The database is the backstop for code.**
- Triggers enforce the run state machine and the human gate even for direct `UPDATE`s.
- A deferred constraint trigger rejects, at commit, any run status change with no matching
  audit row in the same transaction.
- Check constraints enforce:
  - "critical" requires a probe-confirmed exposure;
  - probe row counts are capped at 1000;
  - proposal branches start with `postern/`;
  - stored tokens carry Postern's envelope header (`pst1`), so plaintext can't be stored.
- Unique indexes enforce one open proposal per fingerprint and one notification per
  dedupe key.

**The audit log is append-only and hash-chained.**
- `UPDATE`, `DELETE` and `TRUNCATE` are revoked from every role, and triggers block them
  even for the table owner.
- A `BEFORE INSERT` trigger takes a transaction-scoped advisory lock and assigns
  `chain_position`, then sets `prev_hash` and
  `row_hash = sha256(prev_hash || canonical row text)`.
- `postern.verify_audit_chain()` verifies the chain in SQL, and `postern/core/audit.py`
  verifies it again independently in Python.
- `actor` is the session's login role, which a caller can't spoof.

**Queues and schedule:**
- **Queues:** pgmq `tier0`, `tier1`, `tier2` and `meta`. RLS is on, and `anon` and
  `authenticated` are revoked.
- **Nightly schedule:** the pg_cron job `postern-nightly-runs` runs at 02:17 UTC.

Both are created in a migration, because they go through extension functions that schema
diffs don't capture.

**Agents:**
- **Registry:** `agents/registry.yaml` is validated by `postern/core/registry.py` against the
  tool catalog in `postern/core/tools.py`. A tool that isn't in the catalog can't be granted,
  and no catalog entry allows a forbidden capability.
- **Manifests:** `postern/core/compiler.py` compiles each agent into a canonical manifest with
  a SHA-256. Every run records the hash of the manifest it was enqueued under.
  `agents/manifests.lock.json` makes grant changes reviewable in diffs.
- **Drill agent:** `drill_echo`, a tier 0 agent with no tools and a human gate, exists only so
  operators can exercise the control plane (game days).

**Migration workflow:**
- `supabase/schemas/*.sql` is the declarative source of truth.
- The Phase 2 migration is those files verbatim, since production had no `postern` schema
  yet.
- Later changes are diffed from the schema files and reviewed by hand before they're pushed.
- Roles, queues and cron jobs live only in migrations.

## Consequences

- **Testing roles:** the operator role (`postgres`) is granted `SET` on the component roles,
  without inheriting their privileges, so it can test exactly what each component can do.
- **The owner can still disable triggers** (residual risk R3). The hash chain detects any
  resulting edit; anchoring the chain head outside the database comes with the warehouse
  shipper.
- **Deletion conflicts with an immutable log.** Disconnect-and-delete (TM-30, Phase 8) must
  delete user rows while the audit log stays immutable. Audit payloads therefore hold only
  IDs, hashes and statuses, never personal data or row content.
- **CI cost:** CI starts a disposable Supabase Postgres (`supabase db start`) for the database
  tests, which adds about two minutes per run.
- **No login yet:** the component roles stay `NOLOGIN` until Phase 3 gives them passwords and
  connection strings in Secret Manager.
