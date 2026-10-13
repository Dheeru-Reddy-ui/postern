# ADR-003: Warehouse ingestion

**Status:** Proposed · **Date:** 2026-10-13 · **Decider:** Dheeru

## Context

BigQuery holds Postern's analytical history, in day-partitioned tables built in Phase 1:
`runs`, `steps`, `llm_calls`, `findings`, `ledger`, `notifications` and `eval_results`. It
feeds cost views, online evals, SLO burn, the value ledger and the Platform Auditor.

Postgres stays the system of record. Volumes are small: nightly runs per project, a handful
of LLM calls each, and eval runs in CI.

Constraints:

- stay inside free tiers;
- no JSON service-account keys anywhere (CLAUDE.md, CI/CD);
- data in BigQuery must agree with Postgres, because digest numbers must reconcile with SQL
  (Phase 6);
- ingestion lag should be minutes, not days (online evals, SLO alerts).

CLAUDE.md's fixed stack names the BigQuery Storage Write API. Phase 0 asks for it to be
compared with Supabase Pipelines.

## Options

**A. BigQuery Storage Write API, fed from a Postgres outbox.**

- The first 2 TiB per month of ingestion is free. It requires a billing-enabled project;
  the BigQuery sandbox doesn't allow streaming.
- The default stream is at-least-once. Committed-type streams with offsets give exactly-once
  delivery, at the cost of more client code.
- Rows are serialized as protocol buffers, so the Python client needs a descriptor per table.
  That is the main complexity (to verify against the current client docs in Phase 1).
- Cons: we write and operate the shipper.

**B. Supabase Pipelines (Postgres change-data-capture replication to BigQuery).**

- Replicates tables without any code from us.
- Cons:
  - **Paid plans only.** Supabase's public-alpha announcement says it is "available on all
    paid plans". Postern's control plane is on the free plan, so this adds at least a Pro
    plan as a recurring cost.
  - **Billed hourly per pipeline,** including while replication is paused, plus per-GB
    charges.
  - **Alpha status,** with partial schema-change support (broader type changes aren't covered
    yet).
  - **Needs a GCP service-account key.** Its BigQuery setup asks for "the downloaded service
    account JSON" (Supabase docs, checked 2026-10-13), and WIF isn't mentioned. That
    directly conflicts with "never create or use JSON service-account keys".
  - CDC copies raw table changes, not the curated, append-only event rows the views want.

**C. Legacy streaming inserts (`tabledata.insertAll`).**

- Simple API.
- Cons: billed per 200 MB with no free allowance, and superseded by the Storage Write API.

**D. Batch load jobs (files to BigQuery).**

- Load jobs are free on the shared slot pool and simple to code.
- Cons: minutes-to-hours latency, and per-table daily load quotas. It's a reasonable fallback
  if the Storage Write API client proves too heavy, but it is a change to the fixed stack and
  would need its own ADR.

## Decision

**Option A: the Storage Write API, fed by a transactional outbox in Postgres.**

1. Every state change that matters to analytics writes an event row to `warehouse_outbox`, in
   the same transaction as the state change and its audit row (invariant 8).
2. A shipper step in the worker Job reads outbox batches and appends them to BigQuery through
   the default stream. It marks rows shipped only after BigQuery confirms the append.
3. Each event carries a UUID `event_id`. BigQuery views deduplicate on it, which makes
   at-least-once delivery safe without stream-offset bookkeeping.
4. Tables are partitioned by event day, with partition expiration set in Terraform (TM-12).
5. The shipper authenticates as the worker's own service account, through Cloud Run's
   attached identity. There are no keys.

## Consequences

- **We build and test the shipper** (protobuf descriptors per table, batching, retries) in
  Phase 1 or 3, after verifying the current Python client against Google's docs.
- **Analysts query the dedup views,** not the raw tables. View definitions live in Terraform.
- **Ingestion lag becomes a metric,** covered by a runbook (Phase 8: "BigQuery ingestion lag")
  and an SLO input.
- **Postgres can be rebuilt as the source of truth,** and BigQuery can be backfilled from it.
- **Revisit if** Postern's control plane moves to a paid Supabase plan for other reasons and
  Pipelines leaves alpha with key-less (Workload Identity) authentication.
