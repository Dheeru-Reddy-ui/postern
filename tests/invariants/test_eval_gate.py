"""Threat-model tests: TM-02, TM-14, TM-15, TM-16. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-02 · prevention lands in Phase 4"
)
def test_eval_gate_fails_on_any_safety_failure() -> None:
    """The CI eval gate blocks any change that lets a single safety case fail."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-14 · prevention lands in Phase 4"
)
def test_eval_gate_fails_below_judge_threshold() -> None:
    """The CI eval gate fails if the judge's mean for clarity, calm tone and accuracy drops below
    threshold."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-15 · prevention lands in Phase 4"
)
def test_eval_gate_fails_below_precision_threshold() -> None:
    """The CI eval gate fails below the precision threshold, measured on clean and near-miss
    negatives."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-16 · prevention lands in Phase 4"
)
def test_eval_gate_fails_below_recall_threshold() -> None:
    """The CI eval gate fails below the recall threshold."""
    raise NotImplementedError
