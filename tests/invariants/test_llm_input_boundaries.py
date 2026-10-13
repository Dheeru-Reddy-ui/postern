"""Threat-model tests: TM-01, TM-03. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-01, TM-03 · prevention lands in Phase 3"
)
def test_row_values_never_reach_llm_prompt() -> None:
    """Row values never enter an LLM prompt. Tool outputs are typed, and no type carries row
    values (see decision D1)."""
    raise NotImplementedError
