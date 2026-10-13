"""Threat-model tests: TM-12. See docs/threat-model.md."""

from collections.abc import Callable
from typing import Any

MAX_API_INSTANCES = 3


def test_terraform_declares_cost_ceilings(terraform: Callable[[str], Any]) -> None:
    """Terraform declares hard ceilings, and CI checks they are present: Cloud Run max instances,
    job timeouts and parallelism, BigQuery partition expiration, Artifact Registry cleanup
    policies, and the monthly budget."""
    gcp = terraform("infra/terraform/gcp")

    services = gcp.resources("google_cloud_run_v2_service")
    assert services, "no Cloud Run services declared"
    for name, body in services.items():
        scaling = body["template"][0]["scaling"][0]
        assert scaling["min_instance_count"] == 0, f"{name} must scale to zero"
        assert 0 < scaling["max_instance_count"] <= MAX_API_INSTANCES, name

    jobs = gcp.resources("google_cloud_run_v2_job")
    assert jobs, "no Cloud Run jobs declared"
    for name, body in jobs.items():
        outer = body["template"][0]
        assert outer["parallelism"] == 1, name
        assert outer["task_count"] == 1, name
        task = outer["template"][0]
        assert task["timeout"].endswith("s"), f"{name} needs a task timeout"
        assert int(task["timeout"][:-1]) <= 3600, name
        assert task["max_retries"] <= 1, name

    repos = gcp.resources("google_artifact_registry_repository")
    assert repos, "no Artifact Registry repository declared"
    for name, body in repos.items():
        assert body["cleanup_policy_dry_run"] is False, name
        actions = {policy["action"] for policy in body["cleanup_policies"]}
        assert "DELETE" in actions, f"{name} never deletes old images"

    tables = gcp.locals()["warehouse_tables"]
    assert tables, "no warehouse tables declared"
    for name, spec in tables.items():
        assert isinstance(spec["expiration_days"], int), name
        assert spec["expiration_days"] > 0, name
    partitioned = gcp.resources("google_bigquery_table")["event"]
    assert "expiration_ms" in partitioned["time_partitioning"][0]

    budget = gcp.resources("google_billing_budget")["monthly"]
    assert budget["amount"][0]["specified_amount"][0]["units"]
    assert budget["threshold_rules"], "budget has no alert thresholds"
