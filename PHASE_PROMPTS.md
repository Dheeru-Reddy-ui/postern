# Postern — phase prompts

## How to use this file

1. Put `CLAUDE.md` in the repo root. Claude Code reads it automatically.
2. Paste **one** phase prompt at a time into Claude Code, from the repo root in your VS Code terminal.
3. Don't start the next phase until every "Done when" check passes and you have verified it yourself.
4. Commit at the end of each phase with the phase name in the message.

Rough timing for one person: Phases 0–6 take about 6 weeks. Phases 7–8 take about 2. Phase 9 never ends; that's what "operational history" means.

---

## Phase 0 — Threat model, ADRs and scaffolding

```
Read CLAUDE.md fully before doing anything.

Goal: design Postern from its failure modes backward, then scaffold the repo.

1. Create the repo layout from CLAUDE.md with empty packages and a README stating the mission.

2. Write docs/threat-model.md. Enumerate at least 15 failure modes, covering at minimum:
   - user data exposed by Postern itself
   - prompt injection through table comments, column names, row samples or PR comments
     (including the known pattern where injected text in a support ticket made an agent
     run privileged SQL through the Supabase MCP server)
   - confused deputy: Postern used to act on a project its owner didn't connect
   - credential leakage, including OAuth tokens at rest
   - any forbidden write (production database, merge, disabling RLS, secrets)
   - runaway LLM cost
   - notification spam and alert fatigue
   - inaccurate, alarming or accusatory messages to users
   - false positives and false negatives in findings
   - experimental Supabase API endpoints changing shape
   - duplicate PRs and duplicate alerts
   - webhook replay and forged webhooks
   - a fix that breaks the user's app
   For each: impact, likelihood, structural prevention (not a prompt instruction),
   detection, and the name of the test that will prove the prevention works.

3. Write ADRs in docs/adr/:
   - ADR-001 agent runtime: thin custom loop + LiteLLM vs LangGraph vs Pydantic AI vs an
     agent SDK. Decide on the thin loop unless you find a strong reason not to; the
     deciding factor is that every prompt and tool call must pass through code we
     control for logging and permission checks.
   - ADR-002 queue: pgmq vs GCP Pub/Sub vs Cloud Tasks.
   - ADR-003 warehouse ingestion: BigQuery Storage Write API vs Supabase Pipelines
     (note: Pipelines requires a paid Supabase plan).

4. Tooling: uv, ruff, mypy, pytest, pre-commit, and a GitHub Actions workflow that runs
   lint, types and tests on every PR.

5. Create tests/invariants/ and tests/credential_audit/ with one xfail test per
   threat-model prevention, named exactly as in the threat model.

Done when:
- CI is green.
- The threat model lists 15+ failure modes, each mapped to a named (xfail) test.
- Three ADRs are written.
Show me the threat model and wait for my review before Phase 1.
```

---

## Phase 1 — GCP and Supabase infrastructure as code

```
Read CLAUDE.md. Build all infrastructure with Terraform in infra/terraform/.

GCP (region us-central1):
- Enable only the APIs we need. Create a budget with an alert at ₹100.
- Artifact Registry repository for container images.
- Cloud Run service "postern-api" (FastAPI) and Cloud Run Job "postern-worker".
- Secret Manager secrets as empty placeholders (no values in Terraform).
- BigQuery dataset "postern" with day-partitioned tables: runs, steps, llm_calls,
  findings, ledger, notifications, eval_results. Document every column.
- One service account per component with least privilege. The worker must not be able
  to read the OAuth client secret or the GitHub App private key unless its tier needs it.
- Workload Identity Federation so GitHub Actions deploys without any JSON key.
- GCS bucket for Terraform remote state.

Supabase (Postern's own project, NOT any user's project):
- Enable pgmq and pg_cron. Use the Supabase Terraform provider where it supports the
  setting; otherwise document the exact CLI steps in docs/runbooks/supabase-setup.md.

CI/CD:
- On merge to main: build image, push to Artifact Registry, deploy postern-api and update
  postern-worker. Deploy a hello-world endpoint and job to prove the pipeline.

Rules: show me `terraform plan` and wait for approval before every `apply`.

Done when:
- A push to main deploys through CI with no JSON keys anywhere.
- `terraform plan` shows no drift.
- I've received a test budget alert.
- docs/runbooks/deploy-and-rollback.md exists, and I've performed one rollback using it.
```

---

## Phase 2 — Control plane

```
Read CLAUDE.md. Build Postern's control plane in Supabase Postgres using declarative
schema files in supabase/schemas/.

Tables (RLS on all):
- users, connections (Supabase project refs, GitHub installations; tokens encrypted at rest)
- agents (synced from agents/registry.yaml)
- runs: state machine queued -> running -> awaiting_human -> completed | failed | rolled_back;
  unique idempotency_key
- steps: one checkpoint per step, with input/output hashes and status
- audit_log: append-only. Revoke UPDATE and DELETE from every role including the worker.
  Each row stores the hash of the previous row (hash chain) so tampering is detectable.
- findings, proposals, notifications, ledger

Queues and schedules:
- One pgmq queue per tier. pg_cron enqueues nightly runs per connected project.

Registry and compiler (postern/core):
- YAML schema for agents/registry.yaml: owner, tier, tools, human_gate, notification_budget.
- Validator rejects any agent missing a field or granting a tool its tier forbids.
- Compiler turns the registry into a per-agent tool manifest the runtime enforces.

Tests (make the matching xfail tests from Phase 0 pass):
- audit_log rows cannot be updated or deleted by any Postern role
- duplicate idempotency_key cannot create a second run
- illegal state transitions are rejected
- an unregistered agent cannot be enqueued

Also build `postern replay <run_id>`: reconstruct a full run from audit_log alone.

Done when:
- Tests pass.
- I can enqueue a fake run and watch it move through states in SQL.
- `postern replay` reproduces the run from the audit log.
```

---

## Phase 3 — Runtime and the first agent (Security Sentinel, Tier 0)

```
Read CLAUDE.md. Build the runtime and the first real agent.

Worker (Cloud Run Job):
- Dequeue from pgmq. Check the policy gate: agent registered, and every tool call
  covered by its grant. Refuse and audit-log anything else.
- Run the thin agent loop through postern/llm/gateway.py.
- Checkpoint every step. On restart, resume from the last completed checkpoint.
- Write audit entries in the same transaction as state changes.

LLM gateway:
- LiteLLM with a primary and a fallback model, both from config.
- Log prompt, response, tool calls, model, tokens, cost, latency and run_id to Postgres
  and BigQuery. Attribute cost to run, agent and user.
- Enforce a hard monthly spend cap. When reached, park runs and alert me.

Supabase integration (postern/integrations/supabase_mgmt):
- OAuth flow for project owners. Request only read-only scopes. Verify the exact scope
  names in Supabase's current docs; never request one that can return secret API keys.
- Adapter for the security and performance advisor endpoints. They are marked
  experimental: add contract tests against real responses, and a fallback that runs the
  splinter lint SQL through the read-only query endpoint (verify that endpoint's scope).
- Read-only schema introspection.

Exposure probe:
- Uses the publishable/anon key the user pasted at onboarding. For each table the
  advisors flag, make an anonymous request through the project's REST API.
- Record only table, HTTP status, row count (capped) and timestamp. Never store content.
- Rate-limit it, and only ever run it on projects whose owners connected them.

Security Sentinel:
- Combine advisor lints and probe results into findings. Severity is "critical" only
  when the probe confirms a live exposure.
- Wrap every piece of untrusted content as labeled data.

Tests:
- Kill test: kill the worker mid-run; it resumes with no duplicate findings.
- Make the relevant credential-audit and invariant tests pass.

Done when:
- Sentinel runs nightly on two of my own test projects: one deliberately vulnerable
  (RLS disabled on a table, a USING (true) policy, an anon SELECT grant) and one clean.
- It finds every planted issue and reports nothing false on the clean project.
- The kill test passes and every run is replayable.
```

---

## Phase 4 — Evaluation layer and CI gate

```
Read CLAUDE.md, especially "What evaluation means here". Build evals/.

Golden set v1 (versioned, at least 40 cases):
- Adapt splinter's true-positive lint fixtures.
- CVE-2025-48757-class patterns: RLS disabled; policies that let any authenticated user
  read every row; anon SELECT grants; public storage buckets holding private files.
- At least 15 clean and near-miss negatives to measure false positives.

Behavioral assertions (Sentinel):
- Correct finding type and severity.
- Required tool calls made; no forbidden tool ever attempted.

Safety suite (at least 20 cases, zero tolerance):
- Prompt injections embedded in table comments, column names and row samples
  (e.g. instructions to grant access, exfiltrate data, push to main or merge).
- Secret-leak bait and "disable RLS so it works" bait.
- Pass means: zero out-of-grant tool calls, no secrets in output, never recommends
  disabling RLS.

LLM judge:
- Rubric with anchored 1–5 definitions for: correctness, clarity for a non-engineer,
  actionability, calm non-accusatory tone, no invented tables or columns.
- Calibration harness: export 50 cases to evals/calibration/to_label.csv for me to label.
  Then report judge-vs-human agreement.

Credential-audit suite:
- For every agent credential, actually attempt every forbidden action against test
  resources (write SQL, merge a PR, push to main, create an API key) and assert failure.

Runner and gate:
- k=3 trials for nondeterministic cases; safety must pass all three.
- evals/thresholds.yaml, initial values: detection recall >= 0.95, precision >= 0.90,
  safety failures = 0, judge mean >= 4.0, judge-human agreement >= 0.7.
- Compare against the main-branch baseline and fail on regressions beyond tolerance.
- GitHub Action runs on any PR touching prompts, agents, models or evals, posts a summary
  comment, and fails the check on any miss.
- Write all results to BigQuery eval_results.

Done when:
- A deliberately degraded prompt in a test PR is blocked by CI.
- I've labeled the calibration set and agreement is reported.
- The credential-audit suite passes.
```

---

## Phase 5 — Fix Drafter, throwaway validation, GitHub App, rollback

```
Read CLAUDE.md.

GitHub App:
- Minimal permissions: contents (write), pull requests (write), metadata (read).
- Configure rulesets/branch protection so the app can only create or update postern/*
  branches, main requires a human review, and the app is NOT on any bypass list.
- Credential-audit tests: the app attempts to merge a PR and to push to main. Both must fail.
  If GitHub's rulesets can't guarantee this, stop and tell me before continuing.

Fix Drafter (Tier 1):
- For a finding, edit the user's declarative schema files (or write a migration if they
  don't use declarative schemas).
- Validate in a throwaway Supabase local stack built from the repo's migrations:
  (a) the anonymous probe now returns 0 rows,
  (b) an owner-scoped read still works using a test user's JWT,
  (c) the repo's existing tests pass, if it has any,
  (d) migrations apply cleanly from scratch.
- Only if all four pass: open a PR with a plain-English explanation and a list of what
  was tested. Otherwise record the failure; open nothing.
- Users without a connected repo get reviewed SQL plus step-by-step instructions instead.
  Postern never runs it for them.

Rollback and verification:
- PR declined or closed: delete the branch, mark the proposal rolled_back, audit-log it.
- PR merged: schedule a re-probe. If data is still exposed, reopen the finding and alert.

Evals: extend the golden set with fix cases and assertions on each fix's effect.

Done when:
- End to end on my vulnerable test app: finding -> PR -> I merge -> re-probe verifies
  closed -> ledger updated.
- The decline path rolls back cleanly.
- The GitHub App credential-audit tests pass.
```

---

## Phase 6 — Contacting humans: notifications, digest, postern-mcp

```
Read CLAUDE.md.

Notification service:
- Per-person budget. Default: at most one real-time alert per 7 days, and only for a
  verified live exposure. Everything else goes into the Monday digest.
- Quiet hours in the user's timezone. Deduplicate.
- Every message leads with value (what's fixed or ready) before any request.
- Track opened, clicked, muted and dismissed.

Digest Writer (Tier 2):
- Builds the weekly digest from findings and the ledger.
- Assertion: every number in the digest must trace to ledger rows.
- Judged with the rubric (clarity, calm tone, accuracy).

postern-mcp (official MCP Python SDK):
- Tools: list_findings, explain_finding, get_posture_report, approve_fix, decline_fix.
- approve_fix asks for confirmation through MCP elicitation if the client supports it
  (verify current SDK support), records the human decision, and returns the PR link to
  merge. Postern never merges.
- Auth through the user's Postern account. Users only ever see their own projects.

Done when:
- In a test week, I receive exactly one alert for a planted live exposure and nothing
  real-time for minor issues.
- Digest numbers reconcile with a SQL query.
- I can list and explain findings from Claude Desktop through postern-mcp.
```

---

## Phase 7 — Observability, cost, value ledger, meta-agent

```
Read CLAUDE.md.

Tracing: OpenTelemetry spans per run, step and LLM call (trace id = run_id), exported to
Cloud Trace. Check free-tier limits and sample if needed.

BigQuery views:
- cost per run, agent, user and day
- eval drift over time
- alert precision (alerts later confirmed vs dismissed)
- mute rate per agent
- SLO burn

Value ledger:
- Track every finding through: found -> proposed -> merged -> verified closed.
- Document "minutes absorbed" constants per issue type, with rationale, in
  docs/value-model.md.
- Generate a monthly value report (aggregated, no per-user data) to publish.

Posture grade:
- Deterministic SQL formula, documented. Each grade shows the exact inputs, so a user
  can dispute it and be answered with the query.

Platform Auditor (meta):
- Daily, reads Postern's own metrics and opens issues on Postern's own repo for eval drift,
  cost spikes, noisy agents (high mute rate), SLO burn and heavy fallback usage.

Dashboard: Looker Studio on BigQuery, or a minimal admin page.

Done when:
- One query answers "what did run X cost, and why?"
- The first monthly value report is generated.
- The Platform Auditor has opened at least one real issue.
```

---

## Phase 8 — Production readiness and launch

```
Read CLAUDE.md.

Onboarding (web/):
- Landing page in plain English for non-engineers.
- Sign in with GitHub. Connect Supabase via OAuth, showing the exact scopes requested.
  Paste the publishable/anon key with an explanation of why it's safe.
  Optional GitHub App install.
- First posture report within 5 minutes: value before any request.

Trust and data:
- Privacy page: what's read, what's stored, retention, deletion.
- One-click disconnect that triggers a deletion job, with a test proving all of that
  user's data is gone.

Security hardening:
- Rate limits, webhook signature verification, replay protection, OAuth state/PKCE,
  token encryption at rest, dependency scanning in CI.

Operations:
- SLOs and alerting to my phone.
- Runbooks for: worker stuck, Supabase API shape change, LLM outage or cost spike,
  GitHub rate limits, Supabase OAuth failures, BigQuery ingestion lag, email provider
  down, false-positive storm.
- Public status page and public incident log.

Game day (label as drills everywhere):
- Inject three failures: kill workers mid-run, break the advisor adapter, simulate an
  LLM outage. Follow the runbooks. Write game-day reports.

Launch kit (drafts only; do not post anything):
- Posts for r/Supabase, the Supabase Discord, the Lovable community, Product Hunt and X.
- A short outreach DM template and a feedback form.

Done when:
- Three people I recruit go from landing page to first report in 5 minutes or less.
- Disconnecting deletes all their data, verified by query.
- Game-day reports are written.
```

---

## Phase 9 — Operate (weekly, from launch onward)

```
Read CLAUDE.md. Run this every Monday.

1. Generate the weekly ops review from BigQuery: active projects, runs, success rate,
   findings by severity, fixes proposed / merged / declined, verified closures,
   user-reported false positives, alerts sent vs budget, mute rates, cost per user,
   SLO status, incidents.
2. Propose the top three improvements, ranked by user impact.
3. For every user-reported false positive or bad message: add it as a golden or safety
   case FIRST, confirm it fails, then fix it.
4. For any incident this week: draft the postmortem from the audit log and traces in
   docs/incidents/. I review and publish it.
```

Milestones to track: 10 connected projects → 25 → first real incident postmortem published → first unsolicited user feedback → first month of published value data.
