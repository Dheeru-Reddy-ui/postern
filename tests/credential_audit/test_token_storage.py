"""Threat-model tests: TM-05. See docs/threat-model.md."""

from collections.abc import Callable
from typing import Any

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-05 · prevention lands in Phase 3"
)
def test_oauth_tokens_encrypted_at_rest() -> None:
    """User OAuth tokens are encrypted at rest with a key the database never holds (envelope
    encryption, key in Secret Manager)."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-05 · prevention lands in Phase 3"
)
def test_secrets_never_appear_in_logs_or_prompts() -> None:
    """Logs and prompts pass a redaction filter, and the gateway refuses prompts containing
    secret-shaped strings."""
    raise NotImplementedError


def test_terraform_state_bucket_is_private_and_versioned(terraform: Callable[[str], Any]) -> None:
    """Terraform state holds Postern's own control-plane database password, so the state bucket
    is private, versioned and protected from deletion."""
    buckets = terraform("infra/terraform/gcp/bootstrap").resources("google_storage_bucket")
    assert set(buckets) == {"tfstate"}
    bucket = buckets["tfstate"]
    assert bucket["uniform_bucket_level_access"] is True
    assert bucket["public_access_prevention"] == "enforced"
    assert bucket["force_destroy"] is False
    assert bucket["versioning"][0]["enabled"] is True
    assert bucket["lifecycle"][0]["prevent_destroy"] is True
    # No IAM on the bucket beyond the project owner's: nothing grants access to it.
    for kind in ("google_storage_bucket_iam_member", "google_storage_bucket_iam_binding"):
        for root in ("infra/terraform/gcp/bootstrap", "infra/terraform/gcp"):
            assert not terraform(root).resources(kind), f"{root} grants access via {kind}"
