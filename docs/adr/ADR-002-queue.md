# ADR-002: Work queue

**Status:** Proposed · **Date:** 2026-10-13 · **Decider:** Dheeru

## Context

Work items include nightly runs per connected project, fix-draft requests, notification
sends, warehouse shipping and re-probes after merge.

The queue must:

- **Enqueue atomically.** The enqueue must commit or roll back together with the run row and
  its audit entry (invariant 8). If the queue is a separate system, every enqueue is a dual
  write.
- **Survive crashes.** That means visibility timeouts, retries and a dead-letter path.
- **Support idempotency** (TM-18).
- **Be cheap.** It must stay inside free tiers (CLAUDE.md, Cost).
- **Work with consumers on Cloud Run Jobs** (workers) and the Cloud Run service (API).

CLAUDE.md's fixed stack already lists `pgmq` queues in Postern's Supabase Postgres. This ADR
confirms that choice against the alternatives.

## Options

**A. pgmq in Postern's Supabase Postgres (Supabase Queues).**

- `pgmq.send()` is a SQL call, so the message commits in the same transaction as the run row
  and the audit row. No dual write, no outbox.
- `pgmq.read(queue, vt, qty)` hides a message for `vt` seconds, and `set_vt` extends that for
  long steps. A message stays until `delete` or `archive`, and `archive` keeps history for
  replay and debugging. `metrics()` reports queue length and message age.
- Delivery is exactly-once within the visibility timeout and at-least-once beyond it. We pair
  it with `runs.idempotency_key` and per-step checkpoints.
- Cost: none beyond the database we already run.
- Cons:
  - Consumers poll, so nothing pushes work to a Cloud Run Job. Something has to start the job
    (see consequences).
  - Queue tables add load and storage to a free-tier database.
  - There is no built-in dead-letter queue; we use `read_ct` with a cap.

**B. GCP Pub/Sub.**

- Push delivery to Cloud Run, native dead-letter topics, and 10 GiB/month of free throughput
  per billing account.
- Cons:
  - It's a separate system from the state it describes. Committing a run row and publishing
    its message can't be atomic, so we'd need a transactional outbox anyway, which is a queue
    inside Postgres feeding a queue outside it.
  - Delivery is at-least-once, so idempotency is still needed.
  - Another IAM surface.

**C. Cloud Tasks.**

- Per-task HTTP push to the Cloud Run service, with per-queue rate limits (useful for probes)
  and scheduled delivery. The first 1 million billable operations each month are free.
- Cons:
  - The same dual-write problem as Pub/Sub.
  - It targets HTTP endpoints (the service), not Jobs, so long runs would live in request
    handlers or need a hop to Jobs.
  - Payload and dispatch limits are another contract to verify.

## Decision

**Option A: pgmq, one queue per tier** (`tier0`, `tier1`, `tier2`, `meta`; created in Phase 2).

Rules:

- **A message is a pointer.** It carries `{run_id}` only. All state lives in tables, so a
  replayed or duplicated message can't carry stale or forged state.
- **Enqueue in the same transaction** as the run row and its audit row (TM-18, TM-22).
- **Consumers follow one pattern:**
  - `read` with a visibility timeout above the expected step time;
  - `set_vt` as a heartbeat on long steps;
  - `archive` on completion;
  - once `read_ct` passes a cap, move the run to `failed`, audit-log it, and archive the
    message (our dead-letter path).
- **Re-check registration at dequeue,** not just at enqueue (TM-23).
- **Rate-limit probes** in our own code (TM-26), since pgmq has no per-queue dispatch rate.

## Consequences

- **Something has to start the worker Job.** The options (to settle in an ADR in Phase 1 or 2):
  - Cloud Scheduler triggering the Job (the free tier is a small number of jobs per billing
    account, to verify);
  - `pg_cron` plus `pg_net` calling an authenticated endpoint on `postern-api`, which starts the
    Job through the Cloud Run Admin API.
- **Archive retention is needed.** A `pg_cron` job purges archived messages older than N days,
  to stay inside the free database size.
- **Queue health is a Postgres query** (`pgmq.metrics_all()`), which feeds BigQuery and the
  Platform Auditor.
- **Revisit if** we need push semantics with sub-second latency, or if queue load starts to
  hurt the control plane's free-tier limits.
