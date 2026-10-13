# 2026-10-13 · GAME DAY: postern-api rollback drill

> **This was a game day (a planned drill), not an incident.** Nothing was broken. The goal
> was to prove that [deploy-and-rollback.md](../runbooks/deploy-and-rollback.md) works when
> someone follows it by hand (Phase 1 "Done when").

**Status:** reviewed · **Severity:** none (drill) · **Operator:** Dheeru · **Recorded by:** Claude

## Summary

Dheeru rolled `postern-api` back from the latest deploy to the previous one, confirmed the
older code was serving, and rolled forward again, using only the runbook. It worked on the
first attempt, with no downtime, and was repeated once.

## Timeline (UTC)

| Time | Event |
|---|---|
| 12:17 | CI deploys `postern-api-00002-4h2` (commit `b8ed56f`) |
| 14:11 | CI deploys `postern-api-00003-7tk` (commit `8fb00e0`); 100% traffic to latest |
| 16:52:29 | **Drill 1:** `update-traffic --to-revisions postern-api-00002-4h2=100` |
| 16:52 | `GET /health` returns `git_sha` `b8ed56f…`, so the rollback is confirmed |
| 16:52:49 | `update-traffic --to-latest`; traffic back on `00003` |
| 16:59:37 | **Drill 2:** same rollback, repeated |
| 16:59:52 | Roll forward to latest; `GET /health` returns `git_sha` `8fb00e0…` |

The times come from the Cloud Run admin-activity audit log (`ReplaceService` by
dheerureddy2004@gmail.com) and the terminal output.

## Impact

None. Both revisions were healthy builds of the same hello-world API, and Postern has no
users yet.

## What we verified

- Traffic switches to a named older revision in seconds, with no rebuild.
- `/health` exposes the serving commit, which makes rollbacks easy to verify.
- `--to-latest` un-pins traffic for the roll forward.
- `terraform plan` shows no drift afterwards, because Terraform ignores `traffic` (ADR-010).

## What went well

- The runbook's commands worked unchanged on Windows PowerShell 5.1.
- The audit log recorded exactly who changed traffic, and when.

## Action items

| Action | Owner | Status |
|---|---|---|
| Rehearse the worker (job) rollback path too, at the Phase 8 game day | Dheeru | open |
| Make rollback verification automatic: compare `/health` `git_sha` with the chosen revision's image | Phase 7 (observability) | open |
