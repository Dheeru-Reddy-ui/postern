"""The LLM gateway: the only module that may call a model (invariant 6, ADR-001).

Every call is:
  1. checked for secret-shaped text and refused if any is found (TM-05);
  2. reserved at its worst-case cost under the hard monthly cap, in Postgres, before the call
     is made (TM-11); over the cap, nothing is called and SpendCapReached is raised;
  3. sent through LiteLLM to the primary model, then the fallback model if the primary fails;
  4. recorded with prompt, response, tool calls, model, tokens, cost, latency and run_id, and
     shipped to BigQuery through the warehouse outbox.

The gateway keeps its own autocommit connection, so a call's record survives even if the
run's own transaction rolls back.
"""

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from postern.config import LLMConfig
from postern.core.redaction import find_secrets

CompletionFn = Callable[..., Any]
CostFn = Callable[[Any], float]


class GatewayError(Exception):
    """Every model failed, or the response was unusable."""


class SpendCapReached(GatewayError):
    """The hard monthly cap would be exceeded. Park the run and alert the operator."""


class PromptRejected(GatewayError):
    """The prompt contained something shaped like a secret, so it was never sent."""


@dataclass(frozen=True)
class GatewayRequest:
    run_id: str
    step_index: int
    agent: str
    purpose: str
    prompt: str


@dataclass(frozen=True)
class GatewayResponse:
    text: str
    model: str
    is_fallback: bool
    call_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int


def _litellm_completion() -> tuple[CompletionFn, CostFn]:
    # Use LiteLLM's bundled pricing table rather than fetching one over the network.
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    def cost(response: Any) -> float:
        return float(litellm.completion_cost(completion_response=response))

    completion: CompletionFn = litellm.completion
    return completion, cost


class LLMGateway:
    def __init__(
        self,
        conn: Connection[Any],
        config: LLMConfig,
        gcp_project: str,
        completion: CompletionFn | None = None,
        cost: CostFn | None = None,
    ) -> None:
        if not conn.autocommit:
            raise ValueError("the gateway needs its own autocommit connection")
        self.conn = conn
        self.config = config
        self.gcp_project = gcp_project
        if completion is None or cost is None:
            completion, cost = _litellm_completion()
        self._completion = completion
        self._cost = cost

    def _estimate(self, model: str, prompt: str) -> float:
        prices = self.config.reservation_prices_usd_per_million[model]
        input_tokens = len(prompt) / 3 + 64  # deliberately generous
        output_tokens = self.config.max_output_tokens
        return (input_tokens * prices.input + output_tokens * prices.output) / 1_000_000

    def _actual_cost(self, model: str, response: Any, usage: Any) -> float:
        """LiteLLM's cost for the response; if it can't price it, Postern's own price table.
        A pricing gap must never turn a good answer into a failed call."""
        try:
            return float(self._cost(response))
        except Exception:
            prices = self.config.reservation_prices_usd_per_million[model]
            return (
                int(usage.prompt_tokens) * prices.input
                + int(usage.completion_tokens) * prices.output
            ) / 1_000_000

    def _reserve(self, request: GatewayRequest, model: str, fallback: bool) -> str | None:
        row = self.conn.execute(
            "select postern.reserve_llm_call(%s::uuid, %s::integer, %s, %s, %s, %s, %s::boolean,"
            " %s::jsonb, %s::numeric, %s::numeric)",
            (
                request.run_id,
                request.step_index,
                request.agent,
                request.purpose,
                self.config.provider,
                model,
                fallback,
                Jsonb({"messages": [{"role": "user", "content": request.prompt}]}),
                self._estimate(model, request.prompt),
                self.config.monthly_cap_usd,
            ),
        ).fetchone()
        return None if row is None or row[0] is None else str(row[0])

    def _complete(
        self,
        call_id: str,
        status: str,
        model: str,
        response: Any,
        tool_calls: Any,
        input_tokens: int | None,
        output_tokens: int | None,
        cost: float | None,
        latency_ms: int,
        error_code: str | None,
    ) -> None:
        self.conn.execute(
            "select postern.complete_llm_call(%s::uuid, %s, %s, %s::jsonb, %s::jsonb, %s::integer,"
            " %s::integer, %s::numeric, %s::integer, %s)",
            (
                call_id,
                status,
                model,
                Jsonb(response),
                Jsonb(tool_calls),
                input_tokens,
                output_tokens,
                cost,
                latency_ms,
                error_code,
            ),
        )

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        secrets = find_secrets(request.prompt)
        if secrets:
            raise PromptRejected(f"prompt contains credential-shaped text: {sorted(secrets)}")

        attempts = [(self.config.primary_model, False), (self.config.fallback_model, True)]
        last_error = "no model attempted"
        for model, fallback in attempts:
            call_id = self._reserve(request, model, fallback)
            if call_id is None:
                raise SpendCapReached(
                    f"monthly LLM cap of ${self.config.monthly_cap_usd:.2f} reached"
                )
            started = time.monotonic()
            try:
                response = self._completion(
                    model=f"{self.config.provider}/{model}",
                    messages=[{"role": "user", "content": request.prompt}],
                    max_tokens=self.config.max_output_tokens,
                    temperature=0,
                    timeout=self.config.timeout_seconds,
                    vertex_project=self.gcp_project,
                    vertex_location=self.config.location,
                    reasoning_effort=self.config.reasoning_effort,
                )
                message = response.choices[0].message
                text = str(message.content or "")
                tool_calls = [
                    json.loads(json.dumps(call, default=lambda o: getattr(o, "__dict__", str(o))))
                    for call in (getattr(message, "tool_calls", None) or [])
                ]
                usage = response.usage
            except Exception as error:  # every provider failure falls through to the next model
                last_error = type(error).__name__
                self._complete(
                    call_id,
                    "error",
                    model,
                    None,
                    None,
                    None,
                    None,
                    None,
                    int((time.monotonic() - started) * 1000),
                    last_error,
                )
                continue
            latency_ms = int((time.monotonic() - started) * 1000)
            cost = self._actual_cost(model, response, usage)
            self._complete(
                call_id,
                "ok",
                model,
                {"text": text},
                tool_calls or None,
                int(usage.prompt_tokens),
                int(usage.completion_tokens),
                cost,
                latency_ms,
                None,
            )
            return GatewayResponse(
                text=text,
                model=model,
                is_fallback=fallback,
                call_id=call_id,
                input_tokens=int(usage.prompt_tokens),
                output_tokens=int(usage.completion_tokens),
                cost_usd=cost,
                latency_ms=latency_ms,
            )
        raise GatewayError(f"every model failed (last error: {last_error})")
