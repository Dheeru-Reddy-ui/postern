"""The thin agent loop (ADR-001).

An agent decides its next action from its history alone, so a run can be resumed after a
crash by reloading the checkpointed steps and asking again: completed steps are never redone
(TM-18). Each step is:

  1. checked by the policy gate against the agent's compiled grant (invariant 5);
  2. executed (a tool), or sent to the LLM gateway (a model call, invariant 6);
  3. checkpointed with its audit entry and warehouse event, in one transaction (invariant 8).

Anything the gate refuses is recorded, audited and alerted, and ends the run.
"""

import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from postern.config import AgentLimits
from postern.core.policy import Grant, PolicyGate, PolicyRefusal
from postern.core.store import ClaimedRun, ConnectionRecord, StepRecord, Store
from postern.core.untrusted import Prompt
from postern.llm.gateway import (
    GatewayError,
    GatewayRequest,
    LLMGateway,
    PromptRejected,
    SpendCapReached,
)

log = logging.getLogger("postern.runtime")


class RunAbort(Exception):
    """Raised by a tool to end the run with a specific, recorded reason (e.g. a revoked
    connection), instead of a generic tool failure."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelCall:
    purpose: str
    prompt: Prompt


@dataclass(frozen=True)
class Finish:
    status: Literal["completed", "failed", "awaiting_human"]
    error_code: str | None = None


Action = ToolCall | ModelCall | Finish


@dataclass(frozen=True)
class RunContext:
    """What a run may act on. Tools read the target from here, never from arguments, so
    nothing a run reads can redirect it to another project (TM-04)."""

    run_id: str
    agent: str
    user_id: str | None
    connection: ConnectionRecord | None


class Agent(Protocol):
    name: str

    def next_action(self, ctx: RunContext, history: Sequence[StepRecord]) -> Action: ...


Tool = Callable[[RunContext, Mapping[str, Any], Store], Any]


@dataclass(frozen=True)
class Outcome:
    status: str
    error_code: str | None
    steps_executed: int
    steps_resumed: int


def content_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class Runtime:
    def __init__(
        self,
        store: Store,
        gateway: LLMGateway | None,
        tools: Mapping[str, Tool],
        limits: AgentLimits,
        visibility_seconds: int,
        gate: PolicyGate | None = None,
    ) -> None:
        self.store = store
        self.gateway = gateway
        self.tools = tools
        self.limits = limits
        self.visibility_seconds = visibility_seconds
        self.gate = gate or PolicyGate()

    def execute(self, claimed: ClaimedRun, agent: Agent) -> Outcome:
        run = self.store.load_run(claimed.run_id)
        agent_row = self.store.load_agent(run.agent)
        grant = Grant.from_manifest(agent_row.manifest, agent_row.manifest_hash)
        if agent_row.manifest_hash != run.manifest_hash:
            return self._finish(claimed, "failed", "manifest_changed", 0, 0)

        connection = self.store.load_connection(run.connection_id) if run.connection_id else None
        ctx = RunContext(run.run_id, run.agent, run.user_id, connection)
        history = self.store.completed_steps(run.run_id)
        resumed = len(history)
        executed = 0
        if resumed:
            log.info(
                "resuming run after %d checkpointed steps",
                resumed,
                extra={"run_id": run.run_id, "agent": run.agent},
            )

        while True:
            if len(history) >= self.limits.max_steps:
                return self._finish(claimed, "failed", "step_limit", executed, resumed)
            action = agent.next_action(ctx, history)
            if isinstance(action, Finish):
                if action.status == "completed" and connection is not None:
                    self.store.mark_connection_checked(run.run_id)
                return self._finish(claimed, action.status, action.error_code, executed, resumed)

            index = len(history)
            self.store.extend_claim(claimed, self.visibility_seconds)
            if isinstance(action, ToolCall):
                step = self._run_tool(ctx, grant, action, index)
            else:
                llm_steps = sum(1 for step in history if step.kind == "llm_call")
                if llm_steps >= self.limits.max_llm_calls_per_run:
                    return self._finish(claimed, "failed", "llm_call_limit", executed, resumed)
                try:
                    step = self._run_model(ctx, action, index)
                except SpendCapReached:
                    self.store.rollback()
                    log.critical(
                        "LLM monthly spend cap reached; parking run",
                        extra={
                            "run_id": run.run_id,
                            "agent": run.agent,
                            "postern_alert": "llm_spend_cap_reached",
                        },
                    )
                    return self._finish(
                        claimed, "awaiting_human", "llm_spend_cap", executed, resumed
                    )
            executed += 1
            history.append(step)
            if step.status == "refused":
                return self._finish(claimed, "failed", "policy_refusal", executed, resumed)
            if step.status == "failed" and step.kind == "tool_call":
                # Fail closed: a check that didn't complete never reports clean (TM-16).
                code = (step.output or {}).get("error_code") or f"tool_failed:{step.tool_name}"
                return self._finish(claimed, "failed", str(code), executed, resumed)

    def _run_tool(self, ctx: RunContext, grant: Grant, call: ToolCall, index: int) -> StepRecord:
        input_hash = content_hash({"tool": call.name, "args": call.args})
        try:
            self.gate.check(grant, call.name)
        except PolicyRefusal as refusal:
            self.store.record_step(
                ctx.run_id,
                index,
                "tool_call",
                call.name,
                "refused",
                input_hash,
                None,
                None,
                refusal_reason=refusal.reason,
            )
            self.store.write_audit(
                "policy.refused",
                ctx.run_id,
                "tool",
                call.name,
                {"agent": ctx.agent, "reason": refusal.reason},
            )
            self.store.commit()
            log.critical(
                "policy gate refused a tool call",
                extra={"run_id": ctx.run_id, "agent": ctx.agent, "postern_alert": "policy_refusal"},
            )
            return StepRecord(index, "tool_call", call.name, "refused", None)

        started = time.monotonic()
        try:
            output = self.tools[call.name](ctx, call.args, self.store)
        except RunAbort as abort:
            self.store.rollback()
            output = {"error": "aborted", "error_code": abort.error_code}
            self.store.record_step(
                ctx.run_id,
                index,
                "tool_call",
                call.name,
                "failed",
                input_hash,
                content_hash(output),
                output,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            self.store.commit()
            log.critical(
                "run aborted: %s",
                abort.error_code,
                extra={"run_id": ctx.run_id, "agent": ctx.agent, "postern_alert": abort.error_code},
            )
            return StepRecord(index, "tool_call", call.name, "failed", output)
        except Exception as error:
            self.store.rollback()
            output = {"error": type(error).__name__}
            self.store.record_step(
                ctx.run_id,
                index,
                "tool_call",
                call.name,
                "failed",
                input_hash,
                content_hash(output),
                output,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            self.store.commit()
            log.warning(
                "tool %s failed: %s",
                call.name,
                type(error).__name__,
                extra={"run_id": ctx.run_id, "agent": ctx.agent},
            )
            return StepRecord(index, "tool_call", call.name, "failed", output)

        # The tool's own writes (e.g. findings) commit atomically with its checkpoint.
        self.store.record_step(
            ctx.run_id,
            index,
            "tool_call",
            call.name,
            "completed",
            input_hash,
            content_hash(output),
            output,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        self.store.commit()
        return StepRecord(index, "tool_call", call.name, "completed", output)

    def _run_model(self, ctx: RunContext, call: ModelCall, index: int) -> StepRecord:
        prompt = call.prompt.render()[: self.limits.max_prompt_chars]
        input_hash = content_hash({"purpose": call.purpose, "prompt": prompt})
        if self.gateway is None:
            raise GatewayError("no LLM gateway configured")
        started = time.monotonic()
        try:
            response = self.gateway.complete(
                GatewayRequest(ctx.run_id, index, ctx.agent, call.purpose, prompt)
            )
            output: dict[str, Any] = {
                "text": response.text,
                "model": response.model,
                "call_id": response.call_id,
            }
            status = "completed"
        except SpendCapReached:
            raise
        except GatewayError as error:
            # The run carries on without the model (its findings are already recorded), but
            # the operator hears about it.
            output = {"error": type(error).__name__}
            status = "failed"
            log.error(
                "model call failed: %s",
                type(error).__name__,
                extra={
                    "run_id": ctx.run_id,
                    "agent": ctx.agent,
                    "postern_alert": "prompt_rejected"
                    if isinstance(error, PromptRejected)
                    else "llm_unavailable",
                },
            )
        self.store.record_step(
            ctx.run_id,
            index,
            "llm_call",
            None,
            status,
            input_hash,
            content_hash(output),
            output,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        self.store.commit()
        return StepRecord(index, "llm_call", None, status, output)

    def _finish(
        self, claimed: ClaimedRun, status: str, error_code: str | None, executed: int, resumed: int
    ) -> Outcome:
        self.store.finish_run(claimed, status, error_code)
        self.store.commit()
        return Outcome(status, error_code, executed, resumed)
