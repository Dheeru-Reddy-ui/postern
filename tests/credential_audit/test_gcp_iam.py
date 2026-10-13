"""Threat-model tests: TM-05, TM-29, TM-31. See docs/threat-model.md."""

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SECRET_ACCESS = REPO_ROOT / "infra" / "terraform" / "gcp" / "secret_access.json"
WORKFLOWS = sorted((REPO_ROOT / ".github" / "workflows").glob("*.y*ml"))
TERRAFORM_ROOTS = (
    "infra/terraform/gcp/bootstrap",
    "infra/terraform/gcp",
    "infra/terraform/supabase",
)

RUNTIME_READERS = {"api", "worker"}
# Secrets the worker must never read unless its tier needs them (Phase 1 prompt, TM-05).
NEVER_FOR_WORKER = {"supabase-oauth-client-secret", "github-app-private-key"}


def test_service_accounts_read_only_their_own_secrets(terraform: Callable[[str], Any]) -> None:
    """Each component has its own service account, and each can read only its own secrets.
    secret_access.json is the single source; Terraform derives every binding from it."""
    gcp = terraform("infra/terraform/gcp")
    secrets = json.loads(SECRET_ACCESS.read_text(encoding="utf-8"))["secrets"]

    for secret, spec in secrets.items():
        readers = set(spec["readers"])
        assert readers <= RUNTIME_READERS, f"{secret}: unknown reader {readers - RUNTIME_READERS}"
        if secret in NEVER_FOR_WORKER:
            assert "worker" not in readers, f"the worker must not read {secret}"

    # Bindings exist only as the one for_each over secret_access.json, at secret level.
    assert set(gcp.resources("google_secret_manager_secret_iam_member")) == {"reader"}
    assert "for_each" in gcp.resources("google_secret_manager_secret_iam_member")["reader"]
    for kind in (
        "google_secret_manager_secret_iam_binding",
        "google_secret_manager_secret_iam_policy",
    ):
        assert not gcp.resources(kind), f"{kind} bypasses secret_access.json"
    assert set(gcp.locals()["runtime_members"]) == RUNTIME_READERS

    # No account gets a project-wide Secret Manager role, which would cover every secret.
    for kind in ("google_project_iam_member", "google_project_iam_binding"):
        for name, body in gcp.resources(kind).items():
            assert "secretmanager" not in body["role"], f"{kind}.{name} grants {body['role']}"


def test_no_service_account_keys_exist(terraform: Callable[[str], Any]) -> None:
    """No user-managed service-account keys exist in the project. Statically: no root module
    declares a key and every workflow authenticates through WIF. The live check (`gcloud iam
    service-accounts keys list --managed-by=user`) is in docs/runbooks/infra-setup.md."""
    for root in TERRAFORM_ROOTS:
        assert "google_service_account_key" not in terraform(root).resource_types(), root

    auth_steps = 0
    for workflow in WORKFLOWS:
        text = workflow.read_text(encoding="utf-8")
        assert "credentials_json" not in text, workflow.name
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in text, workflow.name
        for step in re.split(r"\n\s*- ", text):
            if "google-github-actions/auth@" in step:
                auth_steps += 1
                assert "workload_identity_provider:" in step, workflow.name
    assert auth_steps, "no GCP authentication step found"


def test_wif_provider_restricted_to_postern_repo_and_main(terraform: Callable[[str], Any]) -> None:
    """The Workload Identity Federation provider only accepts tokens from this repository's main
    branch for deploys."""
    gcp = terraform("infra/terraform/gcp")
    variables = gcp.variables()
    assert variables["github_repository"]["default"] == "Dheeru-Reddy-ui/postern"
    assert variables["github_repository_id"]["default"] == "1416674974"
    assert variables["github_owner_id"]["default"] == "172974763"
    assert gcp.locals()["deploy_workflow_ref"] == (
        "${var.github_repository}/.github/workflows/ci.yml@refs/heads/main"
    )

    providers = gcp.resources("google_iam_workload_identity_pool_provider")
    assert set(providers) == {"postern_repo"}
    condition = providers["postern_repo"]["attribute_condition"]
    for clause in (
        "assertion.repository_id == '${var.github_repository_id}'",
        "assertion.repository_owner_id == '${var.github_owner_id}'",
        "assertion.ref == 'refs/heads/main'",
        "assertion.event_name == 'push'",
        "assertion.job_workflow_ref == '${local.deploy_workflow_ref}'",
    ):
        assert clause in condition, f"attribute_condition is missing {clause}"

    bindings = {
        name: body
        for name, body in gcp.resources("google_service_account_iam_member").items()
        if body["role"] == "roles/iam.workloadIdentityUser"
    }
    assert set(bindings) == {"deployer_via_wif"}, "only the deployer may be impersonated via WIF"
    assert bindings["deployer_via_wif"]["member"].endswith(
        "/attribute.repository_id/${var.github_repository_id}"
    )

    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in ci


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-31 · prevention lands in Phase 5"
)
def test_validator_has_no_secret_or_control_plane_access() -> None:
    """The validator runs as its own service account, which has no Secret Manager access and no
    control-plane credentials."""
    raise NotImplementedError
