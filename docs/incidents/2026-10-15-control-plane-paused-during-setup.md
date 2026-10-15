# 2026-10-15 · Control plane paused during testbed setup

**Status:** draft · **Severity:** low (no users, no data loss) · **Author:** Claude, for review
by Dheeru

## Summary

While making room for the Phase 3 testbed on Supabase's Free plan, Postern's own control-plane
project (`postern-control`) was paused instead of a different, unrelated project. Postern had
no users and nothing scheduled ran during the pause. The project was restored, and no data was
lost.

## Timeline (UTC)

Exact times weren't recorded; the order of events is certain.

| Time | Event |
|---|---|
| 2026-10-15 | The setup guide's step 4a says to pause "your other active free project". `postern-control` was paused. |
| 2026-10-15 | The testbed (`lsngduwpmudaoernxqut`) was created and `testbed.sql` applied. |
| 2026-10-15 | The production migration push failed: the pooler reported tenant `postern.bpymxbzldxhsqrvrybyo` not found. |
| 2026-10-15 | Diagnosis: the control plane's hostname no longer resolved, while the testbed answered. Postern's token-encryption key (which needs no database) was created; database steps were held. |
| 2026-10-15 | The other free project was paused and `postern-control` restored. A dry run then listed the same two migrations, and the rollout continued. |

## Impact

- No users exist yet, so none were affected.
- No nightly window passed during the pause, so no scheduled check was missed.
- Pausing keeps a Supabase project's data. After the restore, `postern audit verify` found the
  audit chain intact (17 rows).

## Root cause

The Free plan allows two active projects across every organization the owner administers. The
guide named the project to keep paused only as "the other" one, without naming
`postern-control` as the one never to pause. Both appear in the same project list.

## Detection

Caught by the first production command after the pause: the migration push failed to connect,
within minutes. Nothing would have caught it unprompted: the control plane has no external
uptime check yet, and its own pg_cron can't report that the database itself is down.

## What went well

- The rollout steps were ordered so that nothing was half-applied. The failing step changed
  nothing, and the step that didn't need the database went ahead safely.
- The diagnosis needed only public signals (DNS and the REST endpoint), and no credentials.

## Action items

| Action | Owner | Tracking issue | Regression case added? |
|---|---|---|---|
| Runbooks say explicitly: never pause `postern-control` | Claude | this change | n/a (docs) |
| External uptime check on the control plane, alerting the operator | Phase 7 (SLOs) | to open with Phase 7 | Phase 7 |
