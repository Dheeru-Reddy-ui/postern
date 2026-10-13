# ADR-006: Fix PRs only on repos GitHub protects

**Status:** Accepted (Dheeru, 2026-10-13) · **Resolves:** threat-model decision D3

## Context

Invariant 3: no Postern credential can merge into a user's default branch. Phase 5 asks us to
stop if GitHub's rulesets can't guarantee this.

The facts, checked against GitHub's docs on 2026-10-13:

- **GitHub App permissions are per repository.** `contents: write` can push to any branch that
  isn't protected. No permission is limited to `postern/*`.
- **Rulesets are available on GitHub Free only for public repositories.** Private repos need
  Pro, Team or Enterprise. Many vibe coders have private repos on Free.
- **Postern can't add protection itself.** That needs the `administration` permission, which
  is far more power than Postern should hold.
- **Postern can read the active rules** on a branch, including inherited ones, with only
  `metadata: read` ("Get rules for a branch").
- **A PR author can't approve their own PR,** so a required approving review blocks the App
  from merging its own PR, as long as the App isn't on a bypass list.

## Options

- **A.** Open fix PRs only on repos where GitHub itself enforces the protection. Everyone else
  gets reviewed SQL and plain-language steps.
- **B.** Open PRs everywhere and rely on Postern's push gate on unprotected repos. Say so on
  the privacy page.

## Decision

**A.** Option B breaks invariant 3: on an unprotected repo, the App's token can merge.

- **Before every push** (not just at onboarding, because rules can be removed), the push gate
  confirms that the default branch's active rules include:
  - a pull-request rule requiring at least one approving review;
  - blocks on deletion and force-push;
  - and that the App can't bypass the ruleset (checking the bypass status is a Phase 5
    item).
- **Installation tokens are minted only for verified repos,** scoped with the `repositories`
  and `permissions` parameters to that one repo and to `contents: write` plus
  `pull_requests: write`. No live token ever exists for an unprotected repo.
- **Unverified repos get the no-repo path from Phase 5:** reviewed SQL plus step-by-step
  instructions. Postern never runs the SQL for them. Onboarding explains why, and how to turn
  on protection where the user's plan allows it.
- **The App never requests** `workflows`, `administration` or any merge-capable path in code
  (TM-08, TM-10).

## Consequences

- **Private repos on GitHub Free don't get fix PRs.** That is probably a large share of the
  target users, and Postern says so plainly rather than overstating its guarantee.
- **Residual risk R2:** the App's private key could still mint a token for an unprotected repo
  the user installed it on. Minting is gated in code, and the private key is readable only by
  the Tier 1 service account (TM-05).
- **Phase 5's credential-audit tests run on a test repo with a ruleset:** the App tries to
  merge and to push to `main`, and both must fail.
