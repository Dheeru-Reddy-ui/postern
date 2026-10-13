# Postern threat model

**Version:** 0.1 (Phase 0 draft, awaiting review) · **Date:** 2026-10-13

Postern is designed backward from how it could fail. Each failure mode below has an
impact, a likelihood, one or more **structural preventions**, detection, and the phase where
the prevention is built. Every prevention names the test that proves it works.

**Rules for this document**

1. **A prevention is a credential limit, a database constraint, a type, or a CI gate.** It is
   never a prompt instruction. Prompts can add defence in depth, but they never appear in a
   prevention list.
2. **Every prevention names exactly one test** in `tests/invariants/` or
   `tests/credential_audit/`. The same test may protect more than one failure mode.
3. **Tests for preventions that aren't built yet** are marked
   `xfail(raises=NotImplementedError, strict=True)`. Implementing one without removing the
   marker fails the build.
4. **The mapping is enforced.** `tests/unit/test_threat_model.py` fails CI if a test named here
   doesn't exist, if a test in those folders isn't named here, or if a failure mode is missing
   a field.

## Scales

| Impact | Meaning |
|---|---|
| Critical | User data exposed, a user's production project or default branch changed without a human, or Postern holding a credential that can write |
| High | Wrong security advice, broken user trust, a user's app broken, or spend over budget |
| Medium | Degraded service, noise, or recoverable operational damage |

| Likelihood | Meaning |
|---|---|
| High | Expected in normal operation if the prevention were missing |
| Medium | Plausible within the first year of real users |
| Low | Needs an unusual attacker or a combination of failures |

The likelihoods are for a system without the prevention, so they show why each prevention
exists.

## Assets, actors and trust boundaries

**Assets:** the user's Supabase project (rows, schema, settings); the user's GitHub repo and
its CI secrets; the user's attention; Postern's credentials (per-user Supabase OAuth tokens,
the GitHub App private key, LLM, Resend and Slack keys, Postern's own database credentials);
Postern's findings, which amount to a map of who is exploitable; and Dheeru's budget.

**Actors:**

- **Project owner:** connects their project, approves fixes.
- **The app's end users:** they write row data, so row data is attacker-controlled.
- **Anyone on the internet:** they can read any app's anon key from its JavaScript bundle,
  comment on public repos, and send requests to Postern's webhooks.
- **LLM providers.**
- **The Postern operator (Dheeru).**
- **A compromised dependency.**

**Trust boundaries:**

1. Supabase Management API (OAuth token)
2. A user project's REST API (anon key, exposure probes)
3. GitHub (App installation token)
4. LLM providers, through `postern/llm/gateway.py`
5. Email and Slack, through the notification service
6. Inbound traffic: webhooks, OAuth callbacks, MCP clients, web pages
7. Postern's control plane (Supabase Postgres)
8. CI/CD: GitHub Actions to GCP through Workload Identity Federation

## Verified facts this model depends on

These are from the Supabase Management API OpenAPI spec (`https://api.supabase.com/api/v1-json`),
fetched on 2026-10-13. Phase 3 re-verifies them with contract tests against real responses.

| Endpoint | OAuth scope | Stability | Postern use |
|---|---|---|---|
| `GET /v1/projects/{ref}/advisors/security` | `database:read` | experimental | Security Sentinel |
| `GET /v1/projects/{ref}/advisors/performance` | `database:read` | experimental | Performance Tuner |
| `POST /v1/projects/{ref}/database/query/read-only` | `database:read` | beta, experimental | splinter fallback and introspection, runs as `supabase_read_only_user` |
| `GET /v1/projects/{ref}/types/typescript` | `database:read` | stable | schema shape |
| `POST /v1/projects/{ref}/database/query` | `database:write` | beta | **never requested** |
| `GET /v1/projects/{ref}/api-keys` | `secrets:read` | stable | **never requested** (invariant 2) |

Consequences:

- **Postern needs only `database:read`, plus whatever read scope lists a user's projects**
  (to confirm in Phase 3). No `*:write` scope and no `secrets:*` scope is ever needed.
- **Read-only does not mean data-blind.** `supabase_read_only_user` can `SELECT` every table.
  The read-only credential stops writes; it does not stop Postern from seeing rows. TM-01 and
  TM-02 rely on code-level allowlists for that reason.
- **Scopes are configured on the OAuth app in the Supabase dashboard,** not per authorization
  request (per Supabase's OAuth scope docs). The credential audit therefore tests what a real
  token can and can't do, not just what our config says.

## Summary

| ID | Failure mode | Impact | Likelihood | Phase |
|---|---|---|---|---|
| TM-01 | Postern itself exposes or stores user row data | Critical | Medium | 3 |
| TM-02 | Prompt injection through schema metadata | Critical | High | 3–4 |
| TM-03 | Prompt injection through row data, PR comments or issue text | Critical | High | 3, 5 |
| TM-04 | Confused deputy: acting on a project its owner didn't connect | Critical | Medium | 3, 8 |
| TM-05 | Credential leakage, including OAuth tokens at rest | Critical | Medium | 0–3 |
| TM-06 | Postern ends up holding a secret-capable Supabase credential | Critical | High | 3, 8 |
| TM-07 | Forbidden write to a user's Supabase project | Critical | Medium | 3–4 |
| TM-08 | Forbidden merge or push to a user's default branch | Critical | Medium | 5 |
| TM-09 | A drafted fix contains destructive or weakening SQL | Critical | Medium | 5 |
| TM-10 | A Postern branch runs in the user's CI with the user's secrets | Critical | Low | 5 |
| TM-11 | Runaway or unaccounted LLM spend | High | Medium | 3 |
| TM-12 | Runaway cloud cost | Medium | Medium | 1 |
| TM-13 | Notification spam and alert fatigue | High | High | 6 |
| TM-14 | Inaccurate, alarming or accusatory messages | High | High | 3, 6 |
| TM-15 | False positives | High | High | 3–4 |
| TM-16 | False negatives and false reassurance | Critical | Medium | 3–4 |
| TM-17 | Experimental Supabase endpoints change shape | High | High | 3 |
| TM-18 | Duplicate runs, PRs and alerts | Medium | High | 2–6 |
| TM-19 | Forged and replayed webhooks | High | Medium | 5–6 |
| TM-20 | OAuth flow hijack | Critical | Low | 3, 8 |
| TM-21 | A fix breaks the user's app | High | Medium | 5 |
| TM-22 | Audit log tampering or gaps | High | Low | 2 |
| TM-23 | Unregistered agent or grant escalation | Critical | Low | 2 |
| TM-24 | Human gate skipped | Critical | Low | 2, 5–6 |
| TM-25 | Cross-tenant leakage inside Postern | Critical | Medium | 2, 6, 8 |
| TM-26 | Exposure probes disturb the user's app | Medium | Medium | 3 |
| TM-27 | Postern silently stops watching | High | High | 3, 7 |
| TM-28 | Dependency or CI supply-chain compromise | Critical | Medium | 0, 8 |
| TM-29 | Deploy pipeline identity abused | Critical | Low | 1 |
| TM-30 | User data kept after disconnect | High | Medium | 8 |

---

## Failure modes

### TM-01 · Postern itself exposes or stores user row data

**Impact:** Critical. A tool sold as "read-only, never stores your data" that leaks rows ends the project.

**Likelihood:** Medium.

- Exposure probes hit tables that are exposed by definition, so a naive probe downloads rows.
- The read-only SQL endpoint can `SELECT` anything.
- The gateway logs every prompt (invariant 6), so anything put into a prompt is stored.

**Structural prevention:**
- The probe result type has exactly four fields: table, HTTP status, row count capped at
  1000, and timestamp. Nothing else can be persisted.
  → `tests/invariants/test_probe_data_minimisation.py::test_probe_result_has_only_allowed_fields`
- Probes ask PostgREST for a count only (a `HEAD` request with a count preference, to verify in
  Phase 3), so no row body is ever received.
  → `tests/invariants/test_probe_data_minimisation.py::test_probe_never_reads_response_rows`
- The read-only query tool runs only reviewed, allowlisted statements (splinter lints and
  catalog introspection), keyed by hash. No agent grant includes free-form SQL.
  → `tests/invariants/test_read_only_query_allowlist.py::test_read_only_query_runs_only_allowlisted_statements`
- Row values never enter an LLM prompt. Tool outputs are typed, and no type carries row
  values (see decision D1).
  → `tests/invariants/test_llm_input_boundaries.py::test_row_values_never_reach_llm_prompt`

**Detection:** The Platform Auditor samples `llm_calls` and logs for data-shaped strings
(emails, JWTs, phone numbers). BigQuery schemas have no column that could hold row content.

**Lands in:** Phase 3.

### TM-02 · Prompt injection through schema metadata

Table names, column names, comments, function bodies and policy text are all free text that
the project owner's AI builder or a collaborator controls.

The known pattern is from General Analysis (2025):

1. An attacker filed a support ticket containing instructions.
2. A developer's coding agent was connected to the Supabase MCP server with the
   `service_role` key, so it bypassed RLS.
3. The agent read the ticket and ran SQL that copied a private table into the ticket thread,
   where the attacker could read it.

This is the "lethal trifecta": private data, untrusted content, and an exfiltration channel.
Postern removes each leg structurally. It has no RLS-bypassing credential, no free-form SQL,
and its only outbound channels are templated messages to the owner and SQL-gated PRs.

**Impact:** Critical. Out-of-grant actions, data exfiltration.

**Likelihood:** High. Injected text costs nothing, and model susceptibility is a given.

**Structural prevention:**
- The policy gate checks every tool call against the agent's compiled manifest, in code,
  regardless of model output.
  → `tests/invariants/test_policy_gate.py::test_policy_gate_rejects_out_of_grant_tool_call`
- Untrusted text is a distinct type, wrapped and labeled at the integration boundary. It can't
  be concatenated into instructions.
  → `tests/invariants/test_untrusted_content.py::test_untrusted_content_is_wrapped_and_labeled`
- Grants are compiled from the registry before a run starts and are immutable during it.
  → `tests/invariants/test_untrusted_content.py::test_untrusted_content_cannot_change_tool_grants`
- There is no free-form SQL tool for injected text to drive (shared with TM-01).
  → `tests/invariants/test_read_only_query_allowlist.py::test_read_only_query_runs_only_allowlisted_statements`
- The CI eval gate blocks any change that lets a single safety case fail.
  → `tests/invariants/test_eval_gate.py::test_eval_gate_fails_on_any_safety_failure`

**Detection:** Every refused tool call is audit-logged and alerts the operator. The expected
rate is zero, so any refusal is worth reading. The safety suite runs on every eval run.

**Lands in:** Phase 3, with the eval gate in Phase 4.

### TM-03 · Prompt injection through row data, PR comments or issue text

Row data is written by the app's end users, who are anonymous internet users. Comments on
public repos can come from anyone.

**Impact:** Critical.

**Likelihood:** High.

**Structural prevention:**
- Row values never enter a prompt (shared with TM-01).
  → `tests/invariants/test_llm_input_boundaries.py::test_row_values_never_reach_llm_prompt`
- Comments and issue text from accounts without write access to the repo are dropped before
  any agent sees them.
  → `tests/invariants/test_untrusted_content.py::test_comments_from_non_collaborators_are_ignored`
- Whatever is read is wrapped and labeled (shared with TM-02).
  → `tests/invariants/test_untrusted_content.py::test_untrusted_content_is_wrapped_and_labeled`

**Detection:** The safety suite includes injections in rows and comments. Refusals alert.

**Lands in:** Phase 3 (rows) and Phase 5 (comments).

### TM-04 · Confused deputy: acting on a project its owner didn't connect

Every app's anon key and project URL are public in its JavaScript bundle. Without binding,
anyone could paste someone else's key and use Postern as a free vulnerability scanner against
a stranger's app. The same failure happens if an agent puts a different project ref into a
tool argument.

**Impact:** Critical. Postern becomes an attack tool, with legal exposure.

**Likelihood:** Medium.

**Structural prevention:**
- Every project-scoped operation requires a `connections` row created from a verified OAuth
  grant that includes that project ref.
  → `tests/invariants/test_connection_binding.py::test_probe_refuses_project_without_verified_connection`
- A pasted key is checked against the connected project before it is stored. For a legacy JWT
  that means its `ref` claim; for a publishable key, a test request to that project.
  → `tests/invariants/test_connection_binding.py::test_pasted_key_must_belong_to_connected_project`
- The target project and repo come from the run context. No tool takes a project or repo
  argument that the model can set.
  → `tests/invariants/test_connection_binding.py::test_agent_cannot_choose_target_project`

**Detection:** The audit log records every probe target with its connection id. A probe
without a connection is impossible by schema, so any attempt is an incident.

**Lands in:** Phase 3, with onboarding in Phase 8.

### TM-05 · Credential leakage, including OAuth tokens at rest

**Impact:** Critical. Leaked user tokens are read-only, but they still read every table
(see "Verified facts").

**Likelihood:** Medium.

**Structural prevention:**
- User OAuth tokens are encrypted at rest with a key the database never holds (envelope
  encryption, key in Secret Manager).
  → `tests/credential_audit/test_token_storage.py::test_oauth_tokens_encrypted_at_rest`
- Logs and prompts pass a redaction filter, and the gateway refuses prompts containing
  secret-shaped strings.
  → `tests/credential_audit/test_token_storage.py::test_secrets_never_appear_in_logs_or_prompts`
- Each component has its own service account, and each can read only its own secrets.
  → `tests/credential_audit/test_gcp_iam.py::test_service_accounts_read_only_their_own_secrets`
- No `.env` files, private keys or secret-shaped strings are committed. Phase 0 adds
  pre-commit hooks for this; the test proves the repo is clean.
  → `tests/credential_audit/test_repo_hygiene.py::test_no_secrets_or_env_files_committed`

**Detection:** Secret Manager data-access audit logs, and GitHub secret scanning with push
protection on the public repo.

**Lands in:** Phase 0 (hooks), Phase 1 (IAM), Phase 3 (tokens).

### TM-06 · Postern ends up holding a secret-capable Supabase credential

Supabase's API settings show the publishable key and the secret key side by side. A user who
has never written SQL will sometimes paste the wrong one.

**Impact:** Critical. Invariant 1 is broken even though Postern never asked for the key.

**Likelihood:** High.

**Structural prevention:**
- The onboarding key classifier rejects `sb_secret_` keys and any JWT whose `role` isn't
  `anon`, before persistence. A rejected value is never stored or logged.
  → `tests/credential_audit/test_supabase_scopes.py::test_onboarding_rejects_service_role_and_secret_keys`
- Requested scopes must be a subset of a fixed allowlist (decision D2). No `secrets:*` or
  `*:write` scope appears.
  → `tests/credential_audit/test_supabase_scopes.py::test_oauth_scopes_are_within_read_only_allowlist`
- A live test confirms a real Postern token gets a refusal from `GET /v1/projects/{ref}/api-keys`.
  → `tests/credential_audit/test_supabase_scopes.py::test_supabase_token_cannot_read_api_keys`

**Detection:** Classifier rejections are counted. A spike means the onboarding copy is
unclear.

**Lands in:** Phase 3, with onboarding in Phase 8.

### TM-07 · Forbidden write to a user's Supabase project

This covers writing to the production database, disabling RLS, creating or reading API keys,
changing settings, and deleting anything.

**Impact:** Critical.

**Likelihood:** Medium. With the wrong scope, any bug could write.

**Structural prevention:**
- A Postern token can't run write SQL. The `database:write` endpoint refuses it.
  → `tests/credential_audit/test_supabase_write_attempts.py::test_supabase_token_cannot_execute_write_sql`
- The read-only query endpoint rejects `INSERT`, `UPDATE`, `DELETE`, `ALTER` (including
  `DISABLE ROW LEVEL SECURITY`) and `DROP`.
  → `tests/credential_audit/test_supabase_write_attempts.py::test_read_only_query_endpoint_rejects_writes`
- A Postern token can't create or delete API keys.
  → `tests/credential_audit/test_supabase_write_attempts.py::test_supabase_token_cannot_create_or_delete_api_keys`
- A Postern token can't change auth, PostgREST, network or project settings, or pause the
  project.
  → `tests/credential_audit/test_supabase_write_attempts.py::test_supabase_token_cannot_change_project_config`
- Config, code and Secret Manager contain no user database password, `service_role` key or
  secret key.
  → `tests/credential_audit/test_repo_hygiene.py::test_no_user_database_passwords_or_service_role_keys_in_config`

**Detection:** The credential-audit suite runs against test projects on a schedule, so a
Supabase-side permission change shows up as a failing test.

**Lands in:** Phase 3, with the live suite in Phase 4.

### TM-08 · Forbidden merge or push to a user's default branch

GitHub App permissions are per repository, not per branch. `contents: write` can push to any
branch that isn't protected.

**Impact:** Critical.

**Likelihood:** Medium. One bug in branch handling is enough.

**Structural prevention:**
- Every push goes through one function that only allows `refs/heads/postern/*`.
  → `tests/invariants/test_github_push_gate.py::test_push_gate_rejects_refs_outside_postern_namespace`
- Postern's GitHub client has no merge, approve or ruleset-edit operation, and no agent grant
  names one.
  → `tests/invariants/test_github_push_gate.py::test_github_client_exposes_no_merge_or_approve_operations`
- Live: the App tries to merge a PR on a test repo that has a ruleset, and fails.
  → `tests/credential_audit/test_github_app_permissions.py::test_github_app_cannot_merge_pull_request`
- Live: the App tries to push to `main` on that test repo, and fails.
  → `tests/credential_audit/test_github_app_permissions.py::test_github_app_cannot_push_to_default_branch`

**Detection:** GitHub webhook events for pushes to non-`postern/*` refs by the App alert as an
incident.

**Lands in:** Phase 5. **Residual risk R2** applies (repos without branch protection).

### TM-09 · A drafted fix contains destructive or weakening SQL

Credentials can't stop SQL from being written into a PR, and someone who has never written
SQL will merge what looks official. Examples: `DISABLE ROW LEVEL SECURITY`, `DROP`,
`DELETE`, `TRUNCATE`, `GRANT ... TO anon`, a `USING (true)` policy, or a new
`SECURITY DEFINER` function exposed to `anon`.

**Impact:** Critical.

**Likelihood:** Medium. "Disable RLS so it works" is a common model suggestion.

**Structural prevention:**
- Every drafted migration is parsed with Postgres's own parser (libpg_query; library choice
  is an ADR in Phase 5) and checked against an allowlist of statement types and a denylist
  of weakening patterns.
  → `tests/invariants/test_sql_gate.py::test_sql_gate_rejects_forbidden_statements`
- A gate failure means no branch and no PR, and the rejection is audit-logged.
  → `tests/invariants/test_sql_gate.py::test_no_pr_opened_when_sql_gate_fails`

**Detection:** Safety evals include "disable RLS so it works" bait. Gate rejections are
counted per agent.

**Lands in:** Phase 5.

### TM-10 · A Postern branch runs in the user's CI with the user's secrets

Pushing a branch inside the user's repo triggers their workflows, and those runs get the
repo's secrets. A change to scripts, `package.json`, tests or workflow files (from a bug, or
from injected instructions) would run with those secrets.

**Impact:** Critical.

**Likelihood:** Low.

**Structural prevention:**
- The push gate only allows changes under the repo's Supabase schema and migration paths.
  → `tests/invariants/test_github_push_gate.py::test_push_gate_rejects_changes_outside_schema_paths`
- The GitHub App manifest doesn't request the `workflows` permission, so it can't modify
  `.github/workflows/`.
  → `tests/credential_audit/test_github_app_permissions.py::test_github_app_has_no_workflows_permission`

**Detection:** The audit log stores every pushed diff's file list.

**Lands in:** Phase 5.

### TM-11 · Runaway or unaccounted LLM spend

**Impact:** High.

**Likelihood:** Medium. Agent loops, retries, fallbacks and k=3 evals multiply calls.

**Structural prevention:**
- The gateway reserves estimated spend atomically in Postgres before each call. Over the
  monthly cap it refuses, parks runs and alerts.
  → `tests/invariants/test_llm_gateway.py::test_gateway_refuses_calls_over_monthly_cap`
- The agent loop enforces per-run step and token limits from the registry.
  → `tests/invariants/test_llm_gateway.py::test_agent_loop_stops_at_step_and_token_limits`
- Provider SDKs and LiteLLM are imported only in `postern/llm/gateway.py`. Ruff's banned-api
  rule also catches this at lint time.
  → `tests/invariants/test_llm_gateway.py::test_provider_sdks_imported_only_in_gateway`
- Every call is logged with prompt, response, tool calls, model, tokens, cost, latency and
  `run_id`.
  → `tests/invariants/test_llm_gateway.py::test_every_llm_call_is_logged_with_run_id`

**Detection:** Daily cost per run, agent and user in BigQuery, with the Platform Auditor
watching for spikes. Provider-side spend limits are a backstop.

**Lands in:** Phase 3.

### TM-12 · Runaway cloud cost

A GCP budget alert only notifies; it doesn't stop spending.

**Impact:** Medium.

**Likelihood:** Medium.

**Structural prevention:**
- Terraform declares hard ceilings, and CI checks they are present:
  - Cloud Run max instances
  - Job timeouts and parallelism
  - BigQuery `maximum_bytes_billed` and partition expiration
  - Artifact Registry cleanup policies

  → `tests/invariants/test_cost_ceilings.py::test_terraform_declares_cost_ceilings`

**Detection:** Budget alert at ₹100 and billing export.

**Lands in:** Phase 1.

### TM-13 · Notification spam and alert fatigue

A user who mutes Postern misses the one alert that matters.

**Impact:** High.

**Likelihood:** High.

**Structural prevention:**
- Only the notification service can send. Email and Slack clients are importable only in
  `postern/integrations/notify`, which ruff enforces too.
  → `tests/invariants/test_notification_budget.py::test_only_notification_service_can_send_messages`
- The per-person budget is enforced by the database, not by caller code: at most one
  real-time alert per 7 days, and only for a verified live exposure.
  → `tests/invariants/test_notification_budget.py::test_realtime_alert_budget_enforced_per_person`
- Quiet hours are enforced in the user's timezone.
  → `tests/invariants/test_notification_budget.py::test_no_messages_sent_during_quiet_hours`

**Detection:** Mute, unsubscribe and dismiss rates per agent, and alerts sent compared with
budget.

**Lands in:** Phase 6.

### TM-14 · Inaccurate, alarming or accusatory messages

**Impact:** High. A wrong or frightening message about someone's app breaks trust faster
than a missed finding.

**Likelihood:** High.

**Structural prevention:**
- Every number in a user-facing message is filled from ledger or findings rows by a
  template. The model writes prose around facts; it doesn't supply them.
  → `tests/invariants/test_message_accuracy.py::test_user_facing_numbers_trace_to_ledger_rows`
- Every table or column named in a message must exist in the project's introspected schema.
  → `tests/invariants/test_message_accuracy.py::test_messages_reference_only_existing_tables_and_columns`
- "Critical" severity requires a probe-confirmed live exposure.
  → `tests/invariants/test_findings.py::test_critical_severity_requires_confirmed_live_exposure`
- The CI eval gate fails if the judge's mean for clarity, calm tone and accuracy drops below
  threshold.
  → `tests/invariants/test_eval_gate.py::test_eval_gate_fails_below_judge_threshold`

**Detection:** Online evals judge a daily sample. Every user-reported bad message becomes a
golden case before it is fixed.

**Lands in:** Phase 3 (severity), Phase 4 (gate), Phase 6 (messages).

### TM-15 · False positives

**Impact:** High. Noise teaches users to ignore Postern.

**Likelihood:** High. Advisors flag configurations that are sometimes intended.

**Structural prevention:**
- Severity is gated on confirmation (shared with TM-14).
  → `tests/invariants/test_findings.py::test_critical_severity_requires_confirmed_live_exposure`
- The CI eval gate fails below the precision threshold, measured on clean and near-miss
  negatives.
  → `tests/invariants/test_eval_gate.py::test_eval_gate_fails_below_precision_threshold`

**Detection:** Dismissals marked "not a problem" are tracked as alert precision. Each
user-reported false positive becomes a golden case before it is fixed.

**Lands in:** Phases 3–4.

### TM-16 · False negatives and false reassurance

A missed exposure is bad. A report that says "you're clean" when Postern couldn't actually
check is worse.

**Impact:** Critical.

**Likelihood:** Medium.

**Structural prevention:**
- Every advisor lint and probe result becomes a finding through code. The model can explain
  and group findings but can't drop or downgrade one.
  → `tests/invariants/test_findings.py::test_llm_cannot_suppress_or_downgrade_deterministic_findings`
- A check that didn't complete is reported as "not checked", never "clean" (fail closed).
  → `tests/invariants/test_findings.py::test_incomplete_check_never_reports_clean`
- The CI eval gate fails below the recall threshold.
  → `tests/invariants/test_eval_gate.py::test_eval_gate_fails_below_recall_threshold`

**Detection:** Golden recall per run, and planted issues on the test project checked nightly.

**Lands in:** Phases 3–4.

### TM-17 · Experimental Supabase endpoints change shape

Both advisor endpoints and the read-only query endpoint are marked experimental. A shape
change that parses as an empty list would read as "no problems".

**Impact:** High. It feeds straight into TM-16.

**Likelihood:** High.

**Structural prevention:**
- The adapter validates responses strictly. Unknown or missing fields raise a schema error;
  they never yield an empty list.
  → `tests/invariants/test_advisor_adapter.py::test_advisor_shape_change_is_detected_not_ignored`
- On adapter failure, Postern falls back to running splinter's lint SQL through the read-only
  query endpoint.
  → `tests/invariants/test_advisor_adapter.py::test_advisor_failure_falls_back_to_splinter_sql`
- Incomplete checks never report clean (shared with TM-16).
  → `tests/invariants/test_findings.py::test_incomplete_check_never_reports_clean`

**Detection:** Nightly contract tests against real responses (`tests/integration/`), with the
Platform Auditor alerting on heavy fallback use.

**Lands in:** Phase 3.

### TM-18 · Duplicate runs, PRs and alerts

pgmq delivers at least once: a message becomes visible again if its visibility timeout
expires. Workers crash, and webhooks are redelivered.

**Impact:** Medium.

**Likelihood:** High.

**Structural prevention:**
- `runs.idempotency_key` is unique.
  → `tests/invariants/test_idempotency.py::test_duplicate_idempotency_key_cannot_create_second_run`
- There is at most one open proposal per finding fingerprint (unique partial index).
  → `tests/invariants/test_idempotency.py::test_one_open_proposal_per_finding_fingerprint`
- Notifications carry a unique dedupe key.
  → `tests/invariants/test_idempotency.py::test_duplicate_alert_is_suppressed`
- A resumed run skips completed checkpoints rather than repeating their effects.
  → `tests/invariants/test_checkpoints.py::test_resumed_run_skips_completed_steps`

**Detection:** The Phase 3 kill test, and BigQuery views counting duplicate fingerprints.

**Lands in:** Phase 2 (runs), Phase 3 (checkpoints), Phase 5 (proposals), Phase 6 (alerts).

### TM-19 · Forged and replayed webhooks

A forged "PR merged" event could mark an exposure as fixed. A flood of forged events could
trigger runs and spend.

**Impact:** High.

**Likelihood:** Medium.

**Structural prevention:**
- The signature is verified over the raw body before parsing, using a constant-time compare
  (GitHub's `X-Hub-Signature-256`; Resend's scheme to be verified in Phase 6).
  → `tests/invariants/test_webhooks.py::test_webhook_with_invalid_signature_is_rejected`
- Delivery IDs are stored with a unique constraint and a timestamp window, so replays are
  ignored.
  → `tests/invariants/test_webhooks.py::test_replayed_webhook_delivery_is_ignored`
- A webhook is only a hint. State is re-read from the source API before anything acts on it.
  → `tests/invariants/test_webhooks.py::test_webhook_events_are_rechecked_against_source_api`

**Detection:** Signature failures are counted and alert above a threshold.

**Lands in:** Phases 5–6.

### TM-20 · OAuth flow hijack

A CSRF or code-injection attack on the OAuth callback could link a victim's project to an
attacker's Postern account. The attacker would then receive the victim's findings, which map
exactly what is exploitable.

**Impact:** Critical.

**Likelihood:** Low.

**Structural prevention:**
- `state` is bound to the session, single-use and short-lived.
  → `tests/invariants/test_oauth_flow.py::test_oauth_callback_rejects_missing_or_reused_state`
- The flow uses PKCE.
  → `tests/invariants/test_oauth_flow.py::test_oauth_flow_uses_pkce`

**Detection:** Callback failures are logged with reason, and new connections are notified to
the account owner.

**Lands in:** Phase 3, hardened in Phase 8.

### TM-21 · A fix breaks the user's app

With the Supabase GitHub integration enabled, merging a migration applies it to production
automatically.

**Impact:** High.

**Likelihood:** Medium. The throwaway stack is built from repo migrations, which can drift
from production.

**Structural prevention:**
- A PR is opened only if all four validations pass in the throwaway stack:
  - the anonymous probe returns 0 rows
  - an owner-scoped read still works
  - the repo's existing tests pass
  - migrations apply from scratch

  → `tests/invariants/test_fix_validation.py::test_pr_not_opened_unless_all_validations_pass`
- The PR description says in plain words what merging changes in the live database.
  → `tests/invariants/test_fix_validation.py::test_pr_description_states_production_impact`
- Every merged fix is re-probed. If data is still exposed, the finding reopens and the owner
  is alerted.
  → `tests/invariants/test_fix_validation.py::test_merged_fix_is_reprobed_and_reopened_if_still_exposed`

**Detection:** Re-probe results, the ledger (merged but not verified closed), and user
reports.

**Lands in:** Phase 5.

### TM-22 · Audit log tampering or gaps

**Impact:** High. Replay, incident review and every claim Postern makes depend on the audit
log.

**Likelihood:** Low.

**Structural prevention:**
- `UPDATE` and `DELETE` are revoked from every Postern role, and a trigger blocks them.
  → `tests/invariants/test_audit_log.py::test_audit_log_rows_cannot_be_updated_or_deleted`
- `TRUNCATE` is revoked and blocked by a statement-level trigger. Row triggers don't fire on
  `TRUNCATE`.
  → `tests/invariants/test_audit_log.py::test_audit_log_cannot_be_truncated`
- Each row stores the hash of the previous row, so any edit breaks the chain.
  → `tests/invariants/test_audit_log.py::test_audit_log_hash_chain_detects_tampering`
- A state change without its audit row in the same transaction is rejected.
  → `tests/invariants/test_audit_log.py::test_state_change_without_audit_entry_is_rejected`

**Detection:** A nightly chain verification, plus the chain head anchored in BigQuery (see
residual risk R3).

**Lands in:** Phase 2.

### TM-23 · Unregistered agent or grant escalation

**Impact:** Critical.

**Likelihood:** Low.

**Structural prevention:**
- An unregistered agent can't be enqueued.
  → `tests/invariants/test_registry.py::test_unregistered_agent_cannot_be_enqueued`
- The runtime re-checks registration at dequeue, so an agent removed after enqueue doesn't run.
  → `tests/invariants/test_registry.py::test_runtime_refuses_unregistered_agent_at_dequeue`
- The registry validator rejects an agent missing owner, tier, tools, human gate or
  notification budget.
  → `tests/invariants/test_registry.py::test_registry_rejects_agent_missing_required_fields`
- The validator rejects any tool grant the agent's tier forbids.
  → `tests/invariants/test_registry.py::test_registry_rejects_tool_forbidden_for_tier`

**Detection:** Registry changes trigger the CI eval gate (invariant 7). Refusals are audited.

**Lands in:** Phase 2.

### TM-24 · Human gate skipped

**Impact:** Critical. A consequential action happens without the approval the registry
requires.

**Likelihood:** Low.

**Structural prevention:**
- Run state transitions are enforced in the database. Illegal transitions are rejected.
  → `tests/invariants/test_run_state_machine.py::test_illegal_run_state_transitions_are_rejected`
- A run for a human-gated agent can't reach `completed` without a recorded human decision.
  → `tests/invariants/test_run_state_machine.py::test_human_gated_run_cannot_complete_without_human_decision`
- A human decision must come from the owner of the connection, whether through the web or
  through `postern-mcp`.
  → `tests/invariants/test_run_state_machine.py::test_human_decision_must_come_from_project_owner`

**Detection:** The audit log links every decision to a user, and a replay shows the gate
being passed.

**Lands in:** Phase 2, with decisions in Phases 5–6.

### TM-25 · Cross-tenant leakage inside Postern

Postern's findings are a map of which apps are exploitable and how.

**Impact:** Critical.

**Likelihood:** Medium.

**Structural prevention:**
- RLS is enabled on every Postern table, checked against the catalog.
  → `tests/invariants/test_tenant_isolation.py::test_every_postern_table_has_rls_enabled`
- One user can't read another user's findings through the API.
  → `tests/invariants/test_tenant_isolation.py::test_user_cannot_read_another_users_findings`
- Report pages require authentication. There are no unguessable-link-only reports.
  → `tests/invariants/test_tenant_isolation.py::test_report_pages_require_authentication`
- `postern-mcp` tools return only the caller's projects.
  → `tests/invariants/test_tenant_isolation.py::test_mcp_tools_return_only_callers_projects`

**Detection:** Access logs per user, and alerts on cross-tenant query attempts that RLS
refuses.

**Lands in:** Phase 2 (RLS), Phase 6 (MCP), Phase 8 (web).

### TM-26 · Exposure probes disturb the user's app

**Impact:** Medium. Load on a small database, noisy logs, or a user who thinks they are under
attack.

**Likelihood:** Medium. An exact count on a large table is expensive.

**Structural prevention:**
- A per-project rate limit and a concurrency of one.
  → `tests/invariants/test_probe_safety.py::test_probe_rate_limit_is_enforced`
- Probes use only read methods (`GET` or `HEAD`). Write exposure is inferred from the schema,
  never tested by writing.
  → `tests/invariants/test_probe_safety.py::test_probe_uses_only_read_http_methods`
- The count strategy is bounded (estimated rather than exact on large tables), with a hard
  timeout.
  → `tests/invariants/test_probe_safety.py::test_probe_count_is_bounded_and_times_out`

**Detection:** Probe latency is tracked per project. Probes send an identifying
`User-Agent` so owners can recognise them in their logs.

**Lands in:** Phase 3.

### TM-27 · Postern silently stops watching

Users believe they are protected, but nothing has run because a scheduler failed, a token
expired, or the free-tier control plane paused.

**Impact:** High.

**Likelihood:** High.

**Structural prevention:**
- A dead-man's switch alerts the operator when a connection's nightly run is missing.
  → `tests/invariants/test_liveness.py::test_missed_nightly_run_raises_alert`
- A revoked or expired connection is reported to the user ("we can't see your project
  anymore"), never skipped quietly.
  → `tests/invariants/test_liveness.py::test_revoked_connection_is_reported_not_skipped`
- Every report and digest shows the time of the last successful check.
  → `tests/invariants/test_liveness.py::test_reports_show_last_successful_check_time`

**Detection:** The nightly run success-rate SLO.

**Lands in:** Phase 3, with SLO alerting in Phase 7.

### TM-28 · Dependency or CI supply-chain compromise

On 24 March 2026, LiteLLM 1.82.7 and 1.82.8 were published to PyPI with a credential stealer.
1.82.8 used a `.pth` file that runs at interpreter startup, even without `import litellm`.
LiteLLM is in Postern's fixed stack.

**Impact:** Critical. Any process with the package installed exposes every secret it can
reach.

**Likelihood:** Medium.

**Structural prevention:**
- CI installs only the hash-locked dependency set (`uv sync --locked`).
  → `tests/credential_audit/test_supply_chain.py::test_ci_installs_only_locked_dependencies`
- Every GitHub Action is pinned to a full commit SHA.
  → `tests/credential_audit/test_supply_chain.py::test_github_actions_pinned_to_commit_shas`
- LiteLLM and provider SDKs are installed only in the worker image, which can read only the
  worker's secrets.
  → `tests/credential_audit/test_supply_chain.py::test_llm_sdk_installed_only_in_worker_image`

**Detection:** Dependency scanning in CI (Phase 8) and Dependabot alerts. Lockfile diffs are
reviewed by hand.

**Lands in:** Phase 0 (lock and pinning), Phase 8 (scanning).

### TM-29 · Deploy pipeline identity abused

Deploy rights mean control of every Postern credential.

**Impact:** Critical.

**Likelihood:** Low.

**Structural prevention:**
- No user-managed service-account keys exist in the project.
  → `tests/credential_audit/test_gcp_iam.py::test_no_service_account_keys_exist`
- The Workload Identity Federation provider only accepts tokens from this repository's
  `main` branch for deploys.
  → `tests/credential_audit/test_gcp_iam.py::test_wif_provider_restricted_to_postern_repo_and_main`

**Detection:** Cloud Audit Logs for IAM changes and deploys outside CI.

**Lands in:** Phase 1.

### TM-30 · User data kept after disconnect

**Impact:** High. It breaks the privacy promise.

**Likelihood:** Medium.

**Structural prevention:**
- Disconnect triggers a deletion job that removes the user's rows from Postgres and
  BigQuery, verified by query.
  → `tests/invariants/test_data_deletion.py::test_disconnect_deletes_all_user_data`
- Disconnect revokes and destroys stored tokens immediately, before the deletion job runs.
  → `tests/invariants/test_data_deletion.py::test_disconnect_revokes_stored_tokens`

**Detection:** A monthly orphan check finds data for users with no active account.

**Lands in:** Phase 8.

---

## Open decisions for Dheeru's review

- **D1: Row values never enter LLM prompts.** CLAUDE.md lists "row samples" as untrusted
  content (invariant 10). But invariant 6 logs every prompt and invariant 4 forbids storing
  row data, so a row sample in a prompt would be stored. I recommend agents never see row
  values. Probes return counts, introspection returns schema only, and the row-sample safety
  cases in Phase 4 stay as defence in depth.
- **D2: The OAuth scope allowlist.** I propose `database:read`, plus the read scope needed to
  list a user's projects (to confirm in Phase 3). `database:read` also allows the read-only
  query endpoint, which can `SELECT` any table, so TM-01's statement allowlist carries real
  weight.
- **D3: Invariant 3 on repos without branch protection.** As far as I know, rulesets and
  branch protection aren't available on private repos for GitHub Free accounts (to verify in
  Phase 5). Postern also can't configure them on a user's repo without the `administration`
  permission, which it shouldn't hold. For those repos the guarantee is Postern's push gate,
  not GitHub. Options:
  - (a) Only open PRs where a protecting ruleset is confirmed; everyone else gets reviewed
    SQL plus instructions.
  - (b) Accept the code-level guarantee and say so plainly on the privacy page.

  Phase 5 says to stop and ask here; I'm raising it now because it changes the onboarding
  story.
- **D4: Where fix validation runs.** `supabase start` needs a Docker daemon, and Cloud Run
  Jobs can't run one. Options include a Postgres image with Supabase roles inside the job,
  or a CI runner. This needs an ADR before Phase 5.
- **D5: Test projects and the Supabase free plan.** Phase 3 needs Postern's own project plus
  a vulnerable and a clean test project. As far as I know, the free plan allows two active
  projects (to verify), and free projects pause after a week of inactivity.
- **D6: License** for the open-source repo.

## Residual risks we accept (for now)

- **R1:** `database:read` can read every table. The guarantee that Postern doesn't is in code
  (TM-01), not in the credential.
- **R2:** On repos without branch protection, invariant 3 rests on Postern's push gate
  (TM-08), not on GitHub. See D3.
- **R3:** The owner role of Postern's own database can drop audit triggers. The hash chain,
  plus a chain head anchored in BigQuery, detects this but can't prevent it.
- **R4:** Prompt injection can't be prevented, only made powerless: no grant change, no
  secrets, no free-form SQL, no open outbound channel. It can still make an explanation
  worse, which TM-14's templating and judge gate address.
- **R5:** The validation stack is built from repo migrations, which may not match production.
  The post-merge re-probe (TM-21) is the backstop.

## Assumptions to verify, by phase

| Assumption | Verify in |
|---|---|
| PostgREST returns a count with no body for `HEAD` plus a count preference, and the cost of `exact` vs `estimated` | Phase 3 |
| Which read scope lists an OAuth user's projects and organizations | Phase 3 |
| Whether Supabase OAuth refresh tokens rotate, which affects concurrent workers | Phase 3 |
| What `supabase_read_only_user` can and can't do (writes, `SET ROLE`, functions with side effects) | Phase 3–4 |
| GitHub ruleset availability by plan, and whether an App can read a repo's effective rules with `metadata: read` | Phase 5 |
| Resend webhook signature scheme | Phase 6 |
| MCP elicitation support in the official Python SDK | Phase 6 |
| Supabase free-plan project limit and pause policy | Phase 1 |
