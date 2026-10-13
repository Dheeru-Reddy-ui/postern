# ADR-004: Row values never reach an LLM prompt

**Status:** Accepted (Dheeru, 2026-10-13) · **Resolves:** threat-model decision D1

## Context

Three invariants pull against each other:

- **Invariant 10** lists "row samples" among the untrusted content agents might handle.
- **Invariant 6** logs every prompt and response, and Phase 3 writes them to Postgres and
  BigQuery.
- **Invariant 4** says Postern never stores user row data.

If a row sample ever enters a prompt, invariant 6 stores it, and invariant 4 is broken.
Row data is also the most dangerous injection source (TM-03), because the app's anonymous end
users write it.

## Options

- **A.** Allow row samples in prompts and redact them in the gateway logs. Redaction is
  heuristic, and a missed field is a stored row.
- **B.** Allow row samples but skip logging those prompts. That breaks invariant 6 and
  replay.
- **C.** Agents never see row values.

## Decision

**C.** No tool returns row values, so no prompt can contain them.

- Exposure probes return only table, HTTP status, capped row count and timestamp (TM-01).
- Introspection returns schema only: tables, columns, types, policies, grants and comments.
  Comments and identifiers are still untrusted and are wrapped as such (TM-02).
- Tool outputs are typed. No output type has a field that can carry row content, and the
  gateway rejects inputs that don't match a declared type.
- Proven by `tests/invariants/test_llm_input_boundaries.py::test_row_values_never_reach_llm_prompt`.

## Consequences

- **Explanations describe data by column name and type, never by example.** For instance:
  "the `email` column in `profiles` can be read by anyone", not a sample value.
- **The Phase 4 safety suite keeps its row-sample injection cases as defence in depth.** If a
  future change ever lets a row value through, those cases must still pass.
- **Invariant 10 in CLAUDE.md stays as written.** Row samples are still untrusted content;
  this ADR just makes sure agents never receive any.
