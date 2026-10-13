# ADR-001: Agent runtime

**Status:** Accepted (Dheeru, 2026-10-13) · **Date:** 2026-10-13 · **Decider:** Dheeru

## Context

CLAUDE.md fixes the LLM layer as "LiteLLM behind `postern/llm/gateway.py`, with a thin custom
agent loop. No agent framework that hides steps we must log." This ADR records why, and what
the loop must do.

The runtime has to satisfy these invariants on every step:

- **Invariant 5:** refuse unregistered agents, and any tool call the grant doesn't cover.
- **Invariant 6:** every LLM call goes through the gateway, which logs prompt, response, tool
  calls, model, tokens, cost, latency and `run_id`.
- **Invariant 8:** consequential decisions are audit-logged in the same transaction as the
  state change.
- **Invariant 10:** untrusted content is wrapped and labeled, and can't change grants.
- **Phase 3:** checkpoint every step, and resume from the last completed checkpoint after a
  crash.
- **Phase 2:** `postern replay <run_id>` rebuilds a run from the audit log alone.

Postern's agents are small. Tier 0 agents are mostly deterministic (advisor lints, probes,
introspection), and the model explains and groups the results. The hard parts are control,
logging and recovery, not orchestration.

**The deciding factor: every prompt and every tool call must pass through code we control,
for logging and for permission checks.**

## Options

**A. Thin custom loop plus LiteLLM.** We own the loop: build messages, call the gateway, read
tool calls, check each against the compiled manifest, execute, then checkpoint and audit in
one transaction. LiteLLM provides one OpenAI-style interface across providers (including
tool calling), per-call cost calculation, and fallbacks.

- Pros:
  - Every step is our code.
  - The policy gate sits between the model and every tool.
  - Checkpoints and audit rows use our control-plane schema.
  - Deterministic tests can swap in a fake gateway.
  - Few dependencies.
- Cons: we write retries, tool-schema conversion, checkpoint and resume, and loop limits
  ourselves (estimated at a few hundred lines).

**B. LangGraph.** Graph-based orchestration with built-in checkpointers (including Postgres)
and interrupts for human-in-the-loop.

- Pros: mature checkpoint and resume; interrupts map onto `awaiting_human`.
- Cons:
  - Its checkpoints use LangGraph's own state format, not our audit log.
  - Model calls go through LangChain chat-model classes that import provider SDKs, so
    invariant 6 would need a custom model wrapper that fights the framework.
  - Tool execution happens inside framework nodes, so the policy gate becomes a callback
    rather than the only path.
  - Large dependency surface.

**C. Pydantic AI.** Typed agents, typed tool schemas, model-agnostic, with an iterable run
API that exposes each node.

- Pros: excellent typing; validated tool arguments; clean step iteration.
- Cons:
  - Model calls go through its own provider classes, which import provider SDKs. Satisfying
    invariant 6 means a custom model class that calls our gateway, which removes most of the
    library's provider value.
  - Its instrumentation is OpenTelemetry/Logfire-shaped, not our audit log.

**D. A vendor agent SDK** (OpenAI Agents SDK, Claude Agent SDK, Google ADK).

- Pros: fast to start; good tool support for that vendor.
- Cons:
  - Tied to one vendor, which makes the gateway's primary/fallback model requirement
    (Phase 3) awkward.
  - The loop runs inside the SDK.
  - Tracing defaults to the vendor's dashboards.
  - Some tools are hosted on the vendor's side, outside our policy gate.

## Decision

**Option A: a thin custom loop, with LiteLLM used only inside `postern/llm/gateway.py`.**

Loop outline (built in Phase 3):

```
load compiled manifest for agent   (refuse if unregistered)
resume from the last completed checkpoint, if any
for step in range(manifest.max_steps):
    response = gateway.complete(run_id, messages, tools=manifest.tool_schemas)
        # gateway: spend reservation, logging, fallback model, cost attribution
    for call in response.tool_calls:
        policy_gate.check(manifest, call)          # refuse and audit anything out of grant
        result = tools.execute(call, ctx)          # ctx fixes project/repo; the model can't
        wrap result as untrusted data (typed)
    in one transaction: write checkpoint (input/output hashes) + audit row + state change
    stop on final answer, or on the step or token limit
```

Rules that come with the decision:

- **Use LiteLLM as a library only.** We don't run LiteLLM's proxy server, and we don't use its
  logging callbacks as the system of record. The gateway writes its own rows.
- **Lock LiteLLM tightly.** Pin an exact version, hash-locked in `uv.lock`, and review every
  upgrade diff. On 24 March 2026, LiteLLM 1.82.7 and 1.82.8 were published to PyPI with a
  credential stealer that ran at interpreter startup (TM-28).
- **Install LiteLLM only where it's needed.** It goes in an optional dependency group used
  only by the worker image, so the API service never has it installed (TM-28).
- **Enforce the import boundary twice.** Ruff's banned-api rule blocks provider SDK imports
  outside the gateway today. The AST test
  `test_provider_sdks_imported_only_in_gateway` becomes the proof in Phase 3.

## Consequences

- We own and test the loop, retries, tool-schema conversion, checkpoint and resume. The Phase 3
  kill test covers recovery.
- Human-in-the-loop is a run state (`awaiting_human`) in the control plane, not a framework
  interrupt. The run state machine is enforced in the database (TM-24).
- Replay works because the audit log, not a framework's state blob, is the record of what
  happened.
- Revisit this ADR if the loop grows past roughly 1,000 lines, or if multi-agent coordination
  appears that the queue and run tables can't express.
