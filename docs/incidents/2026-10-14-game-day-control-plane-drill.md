# 2026-10-14 · GAME DAY: control-plane run drill

> **This was a game day (a planned drill), not an incident.** It exercised the run state
> machine, the human gate and the audit log on the production control plane (Phase 2
> "Done when"). The drill agent `drill_echo` has no tools and can't touch any project.

**Status:** reviewed · **Severity:** none (drill) · **Operator:** Dheeru · **Recorded by:** Claude

## Summary

Dheeru drove a `drill_echo` run through every state in the Supabase SQL editor. Both
deliberate violations were refused, and `postern replay` rebuilt the run from the audit log
alone with the hash chain intact.

## Timeline (UTC, from `postern replay 142e275c-a3f3-45ce-891a-b6af729db978`)

| Audit row | Time | Event |
|---|---|---|
| #10 | 05:48:10 | Enqueued (`trigger = drill`) |
| #11 | 05:48:57 | queued → running |
| #12 | 05:49:16 | Step 0 checkpointed |
| #13 | 05:49:30 | running → awaiting_human |
| none | ~05:50 | Completing without approval refused: *needs an approved human decision* |
| #14 | 05:50:10 | Human approved (operator) |
| #15 | 05:50:27 | awaiting_human → completed |
| none | after | completed → running refused: *illegal run transition* |

Refused attempts leave no audit rows, because nothing changed.

## What we verified

- The run state machine and the human gate hold on production, enforced by the database.
- The audit log alone is enough to rebuild the run (`postern replay`).
- `postern audit verify`: all 15 production audit rows are intact, checked in Python and in
  the database.

## Observations

- An earlier drill run, `c88fac5e-ceac-460a-85e8-42b9bf6e49df` (audit row #9, 05:21), was
  enqueued and never moved on, so its message stayed in `tier0`. It was most likely step 1
  run once before the simplified instructions. It needs to be closed before the Phase 3
  worker consumes the queue.

## Action items

| Action | Owner | Status |
|---|---|---|
| Close the stray drill run `c88fac5e…` (queued → failed, `error_code = drill_abandoned`) and archive its message | Claude, with Dheeru's OK | open |
| Ship a `postern drill` command that runs this whole drill in one step, for future game days | Phase 8 game day | open |
