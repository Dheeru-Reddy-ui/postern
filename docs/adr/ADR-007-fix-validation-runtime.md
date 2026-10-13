# ADR-007: Where fix validation runs

**Status:** Accepted (Dheeru, 2026-10-13), pending a Phase 5 feasibility spike ·
**Resolves:** threat-model decision D4

## Context

Phase 5 validates every drafted fix in a throwaway stack built from the repo's migrations:

- (a) the anonymous probe returns 0 rows;
- (b) an owner-scoped read still works with a test user's JWT;
- (c) the repo's existing tests pass;
- (d) migrations apply cleanly from scratch.

CLAUDE.md names "Supabase CLI local stack, created and destroyed per validation". That
command (`supabase start`) needs a Docker daemon. Cloud Run, where Postern's workers run,
doesn't allow privileged containers, so it can't run one.

Validation also runs untrusted code. Migrations execute as a superuser and can run shell
commands (`COPY ... FROM PROGRAM`). See TM-31.

## Options

- **A. A self-contained validator image on its own Cloud Run Job.** The image is built on
  Supabase's Postgres image (the Supabase roles and extensions), plus PostgREST and the
  validator. Postgres and PostgREST run as processes inside the job, with no Docker. The job
  has its own service account and starts fresh every time.
- **B. `supabase start` on Postern's GitHub Actions runners.** Full fidelity, free minutes on
  a public repo. But Postern's repo is public, so workflow logs are public and would leak
  users' schemas. Users' SQL would also run next to Postern's CI credentials.
- **C. The user's own CI.** This needs the `workflows` permission, which TM-10 forbids, and
  their CI holds their secrets.
- **D. A VM with Docker** (for example a free-tier e2-micro). Always on, with patching and
  ops burden, and a free-tier VM's memory is far below what the full local stack needs.

## Decision

**A**, with these rules:

- **No access:** the validator's service account has no Secret Manager access and no
  control-plane credentials. Inputs (migrations, the drafted change, test files) and results
  pass through one storage prefix that only the validator and the Fix Drafter can use.
- **No network egress** (TM-31). The mechanism and its cost are checked in the spike.
- **SQL only.** Check (c) means the repo's **database tests** (pgTAP under `supabase/tests`,
  what `supabase test db` runs). Postern never runs the repo's application code, package
  scripts or dependency installs.
- **Check (b) is done through PostgREST,** using a JWT the validator mints with its own
  throwaway secret, for a seeded `auth.users` row with role `authenticated`.
- **Hard timeout.** Every run starts from a clean filesystem, and nothing persists between
  runs.

**Spike first (the start of Phase 5):** confirm that the Postgres image starts as a Cloud Run
Job, fits in memory (Cloud Run's filesystem is in-memory), applies a sample repo's migrations,
and serves PostgREST. If the spike fails, this ADR goes back to Proposed and we choose again
before writing Fix Drafter code.

## Consequences

- **Lower fidelity than the full local stack** (residual risk R6). There is no auth server or
  storage API, so storage-bucket fixes are validated at the SQL level only. The post-merge
  re-probe (TM-21) is the backstop.
- **This narrows Phase 5's check (c)** from "existing tests" to "existing database tests".
  That is a deliberate security choice.
- **Cost:** a few vCPU-minutes per validation, expected to fit in the Cloud Run free tier (to
  confirm in the spike).
