"""Threat-model tests: TM-18. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-18 · prevention lands in Phase 3"
)
def test_resumed_run_skips_completed_steps() -> None:
    """A resumed run skips completed checkpoints rather than repeating their effects."""
    raise NotImplementedError
