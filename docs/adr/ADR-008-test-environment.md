# ADR-008: Test environment within the Supabase free plan

**Status:** Accepted (Dheeru, 2026-10-13) · **Resolves:** threat-model decision D5

## Context

Phase 3's "Done when" runs Sentinel nightly on two test projects, one deliberately vulnerable
and one clean, alongside Postern's own control-plane project. That's three projects.

Supabase's billing docs (checked 2026-10-13) say:

- the free plan grants **two free projects**, and the limit **applies across all
  organizations where you are an Owner or Administrator**;
- paused projects don't count toward the limit;
- free projects pause after a week of inactivity.

## Options

- **A.** Upgrade to Pro for a third project. That's a recurring monthly cost far above the ₹100
  budget.
- **B.** Create a second Supabase account to own the test projects. This sidesteps a
  per-person limit, and we haven't verified it's allowed under Supabase's terms, so we won't.
- **C.** Use Postern's own control plane as the "clean" project. That would give Postern a
  `database:read` token over every user's findings and encrypted tokens, which widens TM-05 and
  TM-25.
- **D.** Two free projects in separate organizations: the control plane, and one testbed that
  holds both the planted issues and clean "near-miss" tables.

## Decision

**D.**

| Organization | Project | Purpose |
|---|---|---|
| `postern` | `postern-control` | Postern's own control plane (production) |
| `postern-testbed` | `testbed` | The user stand-in that Sentinel watches nightly |

- **Separate organizations matter.** A Supabase OAuth grant covers an organization, so the
  grant Postern holds for testing never covers its own control plane.
- **The testbed contains:**
  - planted issues: a table with RLS disabled, a `USING (true)` policy and an anon `SELECT`
    grant, plus a public bucket holding private files from Phase 4;
  - clean near-misses: owner-scoped RLS, an intentionally public reference table with an
    explicit read-only policy, and grants that look risky but aren't;
  - project-level settings configured cleanly.
- **Phase 3's Done-when becomes:** finds every planted issue, and reports nothing on the
  clean tables or project settings. This is a stronger false-positive test than an empty
  clean project, because the near-misses are exactly where false positives come from.

## Consequences

- **Both free slots are used by Postern.** Any other free Supabase projects you own or
  administer must be paused (paused projects don't count) before `postern-control` and
  `testbed` exist.
- **Inactivity pauses:** nightly runs should keep both projects active. If one pauses, the
  dead-man's switch (TM-27) catches it.
- **A project-level advisor lint affects the whole testbed,** so project settings stay clean
  and project-level lints are asserted separately from table-level ones.
- **Revisit when** Postern has recurring revenue or a Supabase plan that removes the limit.
