"""Threat-model tests: TM-05, TM-29. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-05 · prevention lands in Phase 1"
)
def test_service_accounts_read_only_their_own_secrets() -> None:
    """Each component has its own service account, and each can read only its own secrets."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-29 · prevention lands in Phase 1"
)
def test_no_service_account_keys_exist() -> None:
    """No user-managed service-account keys exist in the project."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-29 · prevention lands in Phase 1"
)
def test_wif_provider_restricted_to_postern_repo_and_main() -> None:
    """The Workload Identity Federation provider only accepts tokens from this repository's main
    branch for deploys."""
    raise NotImplementedError
