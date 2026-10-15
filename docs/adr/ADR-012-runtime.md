# ADR-012: Runtime, token broker, LLM gateway and Security Sentinel

**Status:** Accepted (Dheeru, 2026-10-15) · **Date:** 2026-10-14 · **Decider:** Dheeru

## Context

Phase 3 turns the control plane (ADR-011) into a running system. The worker dequeues runs,
checks the policy gate, runs a thin agent loop through `postern/llm/gateway.py`, checkpoints
every step and resumes after a crash. The LLM gateway enforces a hard monthly cap. Owners
connect projects with read-only OAuth, and the first agent, Security Sentinel, finds exposed
data and confirms it with a count-only probe.

The constraints that shaped the choices:

- **Invariants 1–10**, especially read-only access, no row data, and audit in the same
  transaction.
- **TM-28.** LiteLLM was compromised on PyPI in March 2026. Any process that has it installed
  can leak every secret that process can reach.
- **Free tiers.** The budget is ₹100 a month.

## Decisions

### 1. Trigger: pg_cron enqueues, Cloud Scheduler starts the worker

pg_cron already enqueues every nightly run at 02:17 UTC inside the control plane (Phase 2).
A Cloud Scheduler job then starts the `postern-worker` Cloud Run Job at 02:30 UTC through the
documented `jobs:run` API. It uses its own service account, `postern-scheduler`, whose only
grant is `roles/run.invoker` on that one job.

*Rejected:* letting pg_cron call Cloud Run through `pg_net`, which would put a GCP credential
in the control plane; and an always-on worker, which costs money.

### 2. The agent loop

An agent is a function of its history: `next_action(ctx, history)` returns one tool call, one
model call, or "finish". The runtime runs one action per step and commits each step with its
audit row and warehouse event in one transaction. Resuming a run means reloading its
checkpointed steps and asking again, so completed steps are never redone (the kill test).

- **Policy gate.** Every tool call is checked against the grant compiled from the agent's
  manifest, which comes from `postern.agents`, never from the model.
- **Failure handling.** A refused call is recorded, audited, alerted, and ends the run. A tool
  that fails ends the run as failed: a check that didn't finish never reports clean.
- **Limits** (`postern/runtime.yaml`): 80 steps, 3 model calls, 40,000 prompt characters and
  1,024 output tokens per run.

### 3. Queue semantics

`claim_next_run` reads one message, re-checks that the agent is still active (TM-23), and
either starts the run or resumes one whose worker died, which it audits as `run.resumed`. It
fails a run after 5 deliveries. The 600-second visibility timeout is extended on every step.
`finish_run` changes the run's status and archives the message in one transaction.

### 4. Token broker: the worker never holds a long-lived credential

postern-api holds the OAuth client secret, the encrypted tokens' key and the refresh logic.
When the worker needs a Supabase access token, it calls `POST /internal/supabase-token` on the
API with a Google-signed identity token. The API checks that the identity is the worker's
service account and returns only a short-lived access token.

Before issuing each token, the API also re-checks the temporary-access guard (ADR-005). If
temporary database access has been turned on, the API marks the connection and destroys its
tokens.

So the process that has LiteLLM installed can reach only the worker's database URL and
short-lived access tokens.

*Rejected:* the worker decrypting tokens itself, which would mean LiteLLM shares a process
with the key (TM-28); and Cloud KMS, which adds cost and a dependency for no gain at this
scale.

### 5. Two images

One Dockerfile builds `--target api` and `--target worker`. Only the worker installs the
`worker` extra (LiteLLM, BigQuery Storage), and CI checks that the API image contains no
LiteLLM. Dependencies sit in a layer that changes only when `uv.lock` does, so each commit
adds only the source tree and Artifact Registry stays inside its 0.5 GB free tier.

### 6. LLM: Gemini on Vertex AI, keyless, with a hard cap

- **Models.** The provider is `vertex_ai` with location `global`, through LiteLLM 1.103.1.
  The primary model is `gemini-3.5-flash-lite` and the fallback is `gemini-3.1-flash-lite`.
- **Credentials.** There is no API key. The worker authenticates as its own service account,
  which holds a custom role with the single permission `aiplatform.endpoints.predict`.
  (`roles/aiplatform.user` would also allow creating endpoints and jobs.) The `llm-api-key`
  secret has no readers and stays empty.
- **Reasoning.** `reasoning_effort: disable` maps to Gemini 3's lowest thinking level, so
  explanations don't pay for reasoning tokens.
- **Spend cap.** Before each call, the gateway reserves the call's worst-case cost in
  Postgres, under an advisory lock. Over the cap of $1.13 (about ₹100), it refuses: the run
  is parked as `awaiting_human` with `llm_spend_cap`, and an alert fires. Parking is better
  than failing because the findings are already recorded and nothing is lost.
- **Actual cost.** After the call, the gateway records the actual cost from LiteLLM's pricing
  table, falling back to Postern's own price table. A pricing gap never turns a good answer
  into a failed call.
- **Logging.** The gateway writes through its own autocommit connection, so a call's record
  survives even if the run's transaction rolls back. Prompts containing anything
  credential-shaped are refused before they are sent.

### 7. The model explains; it never decides

Everything that decides what was found is deterministic code: lints, probes, severity and
fingerprints. The model is called once per run, after every finding is recorded, to write
plain-language explanations.

Its output reaches the database through only one function, `set_finding_explanation`, which
can't change a finding. Names read from the project reach the model only as labeled
`Untrusted` data.

### 8. Security lints: the advisor merged with Postern's own catalog checks

Postern's own catalog checks run on every check, through the read-only query endpoint,
merged with the advisor's lints by `(lint, schema, object)`. They read only `pg_catalog` and
`information_schema`. The checks are:

- `rls_disabled_in_public`
- `policy_exists_rls_disabled`
- `security_definer_view`
- `rls_policy_always_true`

They run every time, not only as a fallback, for three reasons:

- splinter's SQL can't be vendored, because its repository has no license;
- the advisor's lint list (spec, 2026-10-14) has nothing for a `USING (true)` policy, which is
  one of the planted Phase 3 issues;
- the advisor endpoint is experimental (TM-17).

An always-true policy is flagged only when it lets anyone write (`FOR ALL`, `INSERT`,
`UPDATE` or `DELETE`): that's the "Enable all access" mistake. A read-only `USING (true)`
policy is how Supabase publishes a table on purpose, and ADR-008 lists one as a near-miss
that must stay quiet, so Phase 3 doesn't report it (Dheeru, 2026-10-15). The known gap is a
private table published read-only; a check on column names (emails, phone numbers) could
close it later.

A check counts as complete only if Postern's catalog checks ran:

- **Catalog checks fail:** what the advisor found is still recorded, but the run fails as
  `incomplete_check`.
- **Advisor fails, or reports a "couldn't check" lint** (`advisor_check_unavailable`,
  `db_not_reachable`, and others): its error is recorded on the run.
- **Both fail:** the run fails.

### 9. Exposure probe

For each table or view that an exposure lint flags, the probe sends `HEAD
/rest/v1/<table>?select=*` with the owner's public key and `Prefer: count=estimated`.

- **Counting.** This is PostgREST's documented counting mode: an exact count up to
  `db-max-rows`, and the planner's estimate above it. The count is capped at 1000.
- **Pacing.** At least 0.5 seconds between requests to the same project, a 10-second timeout,
  and at most 40 tables per run.
- **Preconditions.** It runs only for an active, verified connection with a stored public key.
- **Severity.** A finding is critical only if the probe got a 200 or 206 with a count above
  zero. A database constraint enforces this as well.

### 10. Connecting a project

Until Phase 8's onboarding, the operator issues a single-use link (`postern connect link`).
The OAuth flow:

- PKCE with S256, plus an explicit `scope` parameter.
- The state is stored only as a SHA-256 hash, can be used once, expires after 10 minutes, and
  is bound to the browser by an HttpOnly cookie.
- The project must be in the signed-in owner's own project list.

Tokens are encrypted with AES-256-GCM (header `pst1`). The key lives in Secret Manager and
only postern-api reads it; the database refuses anything without the header.

The owner pastes the public key at a hidden prompt (`postern connect key`). Postern checks
what kind of key it is and that it belongs to the connected project before storing it.

### 11. Component database logins

`postern_api` and `postern_worker` log in with passwords generated by `postern db-login
rotate`. The password reaches Postgres only as a SCRAM-SHA-256 verifier, so it can't appear
in a server log. The connection string goes straight to Secret Manager through gcloud's
standard input. Each role has a connection limit and statement and idle timeouts. Both
connect through the session pooler.

### 12. Warehouse shipping

State changes write warehouse events to `postern.warehouse_outbox` in the same transaction
(ADR-003). At the end of each execution, the worker ships them through the Storage Write
API's default stream. That stream is at-least-once; the deduplicating views take care of
repeats.

### 13. Operator alerts

Every alert is a structured log line with a `postern_alert` field. One Cloud Monitoring
log-match policy emails the operator for:

- any `postern_alert`;
- any worker error;
- any scheduler error.

This includes failed runs, policy refusals, a reached spend cap, a model outage, revoked
connections and missed nightly checks.

## Consequences

**Cost:**

- Vertex AI usage stays under the ₹100 cap.
- Cloud Scheduler uses 1 of its 3 free jobs.
- Secret Manager will hold 4 active versions, within the 6 free.
- The alert policy is free until 2027-09-01, then $0.35 a month (about ₹30). That is new
  recurring cost from September 2027; flagged.
- Artifact Registry stays near its free 0.5 GB. After a lockfile change, old dependency
  layers can briefly exceed it, costing cents.

**Verified only on paper until the live run:**

- `aiplatform.endpoints.predict` is enough for publisher models (Google's own service agents
  rely on it).
- The Supabase pooler accepts the component roles.
- Supabase honors the explicit `scope` parameter.
- `pgrst.db_schemas` is visible to the read-only user.

**Open until Phase 7:** a scheduler that silently never fires raises no alert. The
per-connection dead-man's switch only fires when the worker runs.

**Tests:** `tests/invariants/` and `tests/credential_audit/`, mapped one-to-one in
`docs/threat-model.md`, plus the end-to-end Sentinel tests in `tests/integration/`.
