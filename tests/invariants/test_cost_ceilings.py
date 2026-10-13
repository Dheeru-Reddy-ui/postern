"""Threat-model tests: TM-12. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-12 · prevention lands in Phase 1"
)
def test_terraform_declares_cost_ceilings() -> None:
    """Terraform declares hard ceilings, and CI checks they are present: - Cloud Run max instances
    - Job timeouts and parallelism - BigQuery maximum_bytes_billed and partition expiration
    - Artifact Registry cleanup policies."""
    raise NotImplementedError
