"""Threat-model tests: TM-11. See docs/threat-model.md."""

import ast
import logging
import tomllib
from collections.abc import Sequence
from typing import Any

import psycopg
import pytest
from support import REPO_ROOT, FakeModel, SameTransaction, SavepointStore, llm_config_for_tests

from postern.config import AgentLimits, load_config
from postern.core.runtime import Action, Finish, ModelCall, RunContext, Runtime, ToolCall
from postern.core.store import StepRecord
from postern.core.untrusted import Prompt
from postern.llm.gateway import GatewayRequest, LLMGateway, SpendCapReached

PROVIDER_SDKS = (
    "litellm",
    "openai",
    "anthropic",
    "google.genai",
    "google.generativeai",
    "vertexai",
    "google.cloud.aiplatform",
    "mistralai",
    "cohere",
)


def _gateway(
    db: psycopg.Connection[Any], model: FakeModel, cost: Any = FakeModel.cost, **config: Any
) -> LLMGateway:
    return LLMGateway(
        SameTransaction(db),  # type: ignore[arg-type]
        llm_config_for_tests(**config),
        "test-project",
        completion=model,
        cost=cost,
    )


class Explainer:
    name = "cap_probe"

    def __init__(self, calls: int = 1) -> None:
        self.calls = calls

    def next_action(self, ctx: RunContext, history: Sequence[StepRecord]) -> Action:
        if len(history) < self.calls:
            return ModelCall("explain", Prompt().instruction("Say hello."))
        return Finish("completed")


def test_gateway_refuses_calls_over_monthly_cap(
    db: psycopg.Connection[Any], seed: Any, tier0: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The gateway reserves estimated spend atomically in Postgres before each call. Over the
    monthly cap it refuses, parks runs and alerts."""
    seed.agent("cap_probe")
    run_id = seed.run("cap_probe")
    model = FakeModel("hello")
    spent_row = db.execute("select postern.llm_spend_this_month()").fetchone()
    assert spent_row is not None
    probe = _gateway(db, model)
    one_call = probe._estimate(probe.config.primary_model, "Explain.")
    # Room for one call this month, which costs what was reserved for it.
    gateway = _gateway(
        db,
        model,
        cost=lambda response: one_call,
        monthly_cap_usd=float(spent_row[0]) + one_call * 1.5,
    )

    first = gateway.complete(GatewayRequest(run_id, 0, "cap_probe", "explain", "Explain."))
    assert first.text == "hello"
    with pytest.raises(SpendCapReached):
        gateway.complete(GatewayRequest(run_id, 1, "cap_probe", "explain", "Explain."))
    assert len(model.calls) == 1, "the refused call never reached a model"
    rows = db.execute(
        "select step_index, status, error_code from postern.llm_calls where run_id = %s"
        " order by step_index",
        (run_id,),
    ).fetchall()
    assert rows == [(0, "ok", None), (1, "refused_budget", "monthly_cap_reached")]
    refusals = db.execute(
        "select count(*) from postern.audit_log where run_id = %s"
        " and event_type = 'llm.refused_budget'",
        (run_id,),
    ).fetchone()
    assert refusals == (1,)

    # The runtime parks the run for a human and raises an alert, instead of failing it.
    seed.agent("cap_probe_two")
    parked_id = seed.run("cap_probe_two")
    store = SavepointStore(db)
    claimed = store.claim_next_run(tier0, 60, 5)
    store.commit()
    assert claimed is not None
    assert claimed.run_id in (run_id, parked_id)
    if claimed.run_id == run_id:  # the first run is still queued too; finish it out of the way
        store.finish_run(claimed, "failed", "test")
        claimed = store.claim_next_run(tier0, 60, 5)
        store.commit()
        assert claimed is not None
    capped = _gateway(db, model, monthly_cap_usd=0.000001)
    runtime = Runtime(store, capped, {}, load_config().agent_limits, visibility_seconds=60)
    with caplog.at_level(logging.CRITICAL, logger="postern.runtime"):
        outcome = runtime.execute(claimed, Explainer())
    assert (outcome.status, outcome.error_code) == ("awaiting_human", "llm_spend_cap")
    status = db.execute(
        "select status::text, error_code from postern.runs where id = %s", (claimed.run_id,)
    ).fetchone()
    assert status == ("awaiting_human", "llm_spend_cap")
    assert [r for r in caplog.records if getattr(r, "postern_alert", "") == "llm_spend_cap_reached"]


class Looper:
    name = "loop_probe"

    def __init__(self, action: Action) -> None:
        self.action = action

    def next_action(self, ctx: RunContext, history: Sequence[StepRecord]) -> Action:
        return self.action


def test_agent_loop_stops_at_step_and_token_limits(
    db: psycopg.Connection[Any], seed: Any, tier0: str
) -> None:
    """The agent loop enforces per-run limits on steps, model calls, prompt size and output
    tokens, from postern/runtime.yaml."""
    limits = AgentLimits(max_steps=4, max_llm_calls_per_run=2, max_prompt_chars=500)
    seed.agent("loop_probe", tools=["findings.record"])
    store = SavepointStore(db)
    model = FakeModel("ok")
    runtime = Runtime(
        store,
        _gateway(db, model, max_output_tokens=256),
        {"findings.record": lambda ctx, args, store: {"ok": True}},
        limits,
        visibility_seconds=60,
    )

    seed.run("loop_probe")
    claimed = store.claim_next_run(tier0, 60, 5)
    store.commit()
    assert claimed is not None
    outcome = runtime.execute(claimed, Looper(ToolCall("findings.record")))
    assert (outcome.status, outcome.error_code, outcome.steps_executed) == (
        "failed",
        "step_limit",
        4,
    )

    seed.run("loop_probe")
    claimed = store.claim_next_run(tier0, 60, 5)
    store.commit()
    assert claimed is not None
    huge = Prompt().instruction("x" * 5000)
    outcome = runtime.execute(claimed, Looper(ModelCall("explain", huge)))
    assert (outcome.status, outcome.error_code) == ("failed", "llm_call_limit")
    assert len(model.calls) == 2
    for call in model.calls:
        assert len(call["messages"][0]["content"]) <= limits.max_prompt_chars
        assert call["max_tokens"] == 256

    # The configured limits are the ones the worker uses.
    configured = load_config()
    assert configured.agent_limits.max_llm_calls_per_run <= 3
    assert configured.llm.max_output_tokens <= 2048


def test_provider_sdks_imported_only_in_gateway() -> None:
    """Provider SDKs and LiteLLM are imported only in postern/llm/gateway.py. Ruff's banned-api
    rule also catches this at lint time."""
    offenders = []
    for path in sorted((REPO_ROOT / "postern").rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if any(name == sdk or name.startswith(sdk + ".") for sdk in PROVIDER_SDKS):
                    offenders.append((relative, name))
    assert offenders == [("postern/llm/gateway.py", "litellm")]

    # The lint-time backstop bans every one of them.
    ruff = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["ruff"]
    banned = ruff["lint"]["flake8-tidy-imports"]["banned-api"]
    for sdk in PROVIDER_SDKS:
        assert sdk in banned, sdk


def test_every_llm_call_is_logged_with_run_id(db: psycopg.Connection[Any], seed: Any) -> None:
    """Every call is logged with prompt, response, tool calls, model, tokens, cost, latency and
    run_id, attributed to run, agent and user, and queued for BigQuery."""
    user = seed.user()
    connection = seed.active_connection(user)
    seed.agent("log_probe")
    run_id = seed.run("log_probe", connection=connection)
    config = llm_config_for_tests()
    model = FakeModel("the answer", failing_models=[config.primary_model])
    response = _gateway(db, model).complete(
        GatewayRequest(run_id, 3, "log_probe", "explain", "Explain this.")
    )
    assert response.is_fallback
    assert response.model == config.fallback_model

    rows = db.execute(
        "select run_id::text, step_index, agent, user_id::text, connection_id::text, model,"
        " is_fallback, status, prompt, response, input_tokens, output_tokens, cost_usd,"
        " latency_ms, error_code from postern.llm_calls where run_id = %s order by created_at,"
        " is_fallback",
        (run_id,),
    ).fetchall()
    assert len(rows) == 2, "the failed primary attempt is logged too"
    primary, fallback = rows
    assert primary[5:8] == (config.primary_model, False, "error")
    assert primary[14] == "RuntimeError"
    assert fallback[:8] == (
        run_id,
        3,
        "log_probe",
        user,
        connection,
        config.fallback_model,
        True,
        "ok",
    )
    assert fallback[8] == {"messages": [{"role": "user", "content": "Explain this."}]}
    assert fallback[9] == {"text": "the answer"}
    assert fallback[10:12] == (120, 40)
    assert float(fallback[12]) == pytest.approx(0.0001)
    assert fallback[13] is not None

    events = db.execute(
        "select row from postern.warehouse_outbox where table_name = 'llm_calls'"
        " and row ->> 'run_id' = %s",
        (run_id,),
    ).fetchall()
    assert len(events) == 2
    (shipped,) = [event[0] for event in events if event[0]["status"] == "ok"]
    for column in (
        "run_id",
        "agent",
        "user_id",
        "model",
        "prompt",
        "response",
        "cost_usd",
        "input_tokens",
        "output_tokens",
        "latency_ms",
    ):
        assert shipped[column] is not None, column
    audit = db.execute(
        "select count(*) from postern.audit_log where run_id = %s and event_type = 'llm.called'",
        (run_id,),
    ).fetchone()
    assert audit == (2,)
