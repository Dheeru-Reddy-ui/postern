"""Threat-model tests: TM-18. See docs/threat-model.md."""

from collections.abc import Callable
from typing import Any

from support import PLANTED, SentinelWorld

from postern.core.replay import replay_run


class WorkerKilled(BaseException):
    """The worker process dying mid-step. Nothing in the runtime may catch it."""


def test_resumed_run_skips_completed_steps(sentinel_world: Callable[..., SentinelWorld]) -> None:
    """A resumed run skips completed checkpoints rather than repeating their effects.

    The Phase 3 kill test: the worker dies while recording the second finding, its open
    transaction is lost, the queue redelivers the run, and a new worker resumes it.
    """
    world = sentinel_world()
    record = world.tools["findings.record"]
    attempts = 0

    def killed_on_second_finding(*args: Any) -> Any:
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise WorkerKilled
        return record(*args)

    world.runtime.tools = {**world.tools, "findings.record": killed_on_second_finding}
    run_id = world.enqueue()
    claimed = world.claim()
    try:
        world.runtime.execute(claimed, world.agent)
        raise AssertionError("the worker should have died")
    except WorkerKilled:
        world.store.rollback()  # the dead worker's open transaction is gone

    checkpointed = world.store.completed_steps(run_id)
    calls_before_kill = list(world.tool_calls)
    assert len(world.findings()) == 1, "the first finding was committed with its checkpoint"
    assert world.run_status(run_id) == ("running", None)

    # Once the message's visibility timeout passes, the queue hands the run out again.
    world.db.execute("select pgmq.set_vt('tier0', %s, 0)", (claimed.msg_id,))
    world.runtime.tools = world.tools
    resumed = world.claim()
    assert (resumed.run_id, resumed.msg_id, resumed.resumed) == (run_id, claimed.msg_id, True)
    outcome = world.runtime.execute(resumed, world.agent)

    assert outcome.status == "completed", outcome
    assert outcome.steps_resumed == len(checkpointed)

    # Nothing that completed before the kill ran again.
    after_resume = world.tool_calls[len(calls_before_kill) :]
    for tool in ("supabase.project.read", "supabase.advisors.security", "probe.exposure"):
        assert tool not in after_resume, f"{tool} was repeated after resuming"
    assert after_resume.count("findings.record") == len(PLANTED) - 1

    # No duplicate findings: each planted issue was found exactly once.
    assert {(f["finding_type"], f["object_name"]) for f in world.findings()} == PLANTED
    events = world.audit_events(run_id)
    assert events.count("finding.found") == len(PLANTED)
    assert events.count("run.resumed") == 1

    # Steps form one unbroken sequence, and the run replays from the audit log alone.
    steps = world.store.completed_steps(run_id)
    assert [step.index for step in steps] == list(range(len(steps)))
    replayed = replay_run(world.db, run_id)
    assert replayed.integrity_problems == []
    assert replayed.status == "completed"
    assert [step.step_index for step in replayed.steps] == [step.index for step in steps]
