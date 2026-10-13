# ADR-009: License

**Status:** Accepted (Dheeru, 2026-10-13) · **Resolves:** threat-model decision D6

## Context

Postern is open source. Its users are solo builders and small teams, and its credibility
depends on people being able to read and run exactly what touches their projects. Its
dependencies (LiteLLM, FastAPI, the MCP SDK, the Google Cloud clients) are under permissive
licenses.

## Options

- **MIT.** Short and permissive, but it has no explicit patent grant.
- **Apache-2.0.** Permissive, with an explicit patent grant and patent-retaliation clause,
  clear terms for contributions (section 5), and a NOTICE mechanism. Common for security and
  infrastructure tools.
- **AGPL-3.0.** Stops closed hosted forks, but discourages adoption and contributions from
  companies, and complicates reuse of the eval and threat-model material.

## Decision

**Apache-2.0.**

## Consequences

- `LICENSE` holds the canonical Apache-2.0 text, and `pyproject.toml` declares
  `license = "Apache-2.0"`.
- Contributions are licensed under section 5 by default, so no CLA is needed.
- Anyone may run a hosted fork. Postern's moat is its operational history, evals and
  trustworthiness, not the code.
