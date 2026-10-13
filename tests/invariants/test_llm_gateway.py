"""Threat-model tests: TM-11. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-11 · prevention lands in Phase 3"
)
def test_gateway_refuses_calls_over_monthly_cap() -> None:
    """The gateway reserves estimated spend atomically in Postgres before each call. Over the
    monthly cap it refuses, parks runs and alerts."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-11 · prevention lands in Phase 3"
)
def test_agent_loop_stops_at_step_and_token_limits() -> None:
    """The agent loop enforces per-run step and token limits from the registry."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-11 · prevention lands in Phase 3"
)
def test_provider_sdks_imported_only_in_gateway() -> None:
    """Provider SDKs and LiteLLM are imported only in postern/llm/gateway.py. Ruff's banned-api
    rule also catches this at lint time."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-11 · prevention lands in Phase 3"
)
def test_every_llm_call_is_logged_with_run_id() -> None:
    """Every call is logged with prompt, response, tool calls, model, tokens, cost, latency and
    run_id."""
    raise NotImplementedError
