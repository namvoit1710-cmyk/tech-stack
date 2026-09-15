"""MERGE wait-strategy fire-timing — the 'handle the correct use case as chosen'.

Validates the readiness predicate for each strategy at the HANDLER level
(deterministic, no engine): given N of M predecessors arrived, does the merge
FIRE (completion_mode='immediate') or WAIT (completion_mode='deferred')?

This supersedes the skipped ``test_any_wait_strategy`` / ``test_n_of_m_strategy``
in test_node_handlers.py, which asserted the removed ``tasks_to_dispatch`` model.
The current uniform-execution model exposes readiness via ``completion_mode``.

Canonical keys/vocabulary only (NodeSpec Phase 3): ``merge_strategy`` in
``all/any/n_of_m`` + ``wait_count``. Legacy aliases were normalized away by
migration; unknown values behave like ``all`` (the conservative default).
"""
from types import SimpleNamespace

import pytest

from app.layer1_domain.entities.workflow import WorkflowNode
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.node_handlers.merge_handler import MergeNodeHandler


async def _completion_mode(config, predecessors, completed=(), skipped=()):
    """Run the MERGE handler once and return its completion_mode + output_port."""
    node = WorkflowNode(id="merge", node_type=NodeType.MERGE, config=config)
    topology = SimpleNamespace(predecessors={"merge": list(predecessors)})
    ctx = RunContext(topology=topology, variables={})
    for n in completed:
        ctx.completed_nodes.add(n)
    for n in skipped:
        ctx.skipped_nodes.add(n)
    run = WorkflowRun(id="r", workflow_id="wf", status=RunStatus.RUNNING, context=ctx)
    res = await MergeNodeHandler().handle(
        node=node, run=run, workflow=None, topology=topology,
        completed_task=None, task_factory=None,
    )
    return res.completion_mode, res.output_port


# ---- wait_all: fires only when EVERY predecessor has arrived ----

@pytest.mark.parametrize("strategy", ["all"])
async def test_all_waits_until_every_predecessor(strategy):
    mode, port = await _completion_mode({"merge_strategy": strategy}, ["a", "b"], completed=["a"])
    assert mode == "deferred"          # 1 of 2 → wait
    assert port == "out_default"
    mode, _ = await _completion_mode({"merge_strategy": strategy}, ["a", "b"], completed=["a", "b"])
    assert mode == "immediate"         # 2 of 2 → fire


async def test_all_counts_skipped_predecessors_toward_quorum():
    # A skipped branch (e.g. gated-off PARALLEL) must satisfy its slot, not hang.
    mode, _ = await _completion_mode(
        {"merge_strategy": "all"}, ["a", "b"], completed=["a"], skipped=["b"]
    )
    assert mode == "immediate"


# ---- wait_any: fires as soon as the FIRST predecessor arrives ----

@pytest.mark.parametrize("strategy", ["any"])
async def test_any_fires_on_first_arrival(strategy):
    mode, _ = await _completion_mode({"merge_strategy": strategy}, ["a", "b"], completed=[])
    assert mode == "deferred"          # 0 of 2 → wait
    mode, _ = await _completion_mode({"merge_strategy": strategy}, ["a", "b"], completed=["a"])
    assert mode == "immediate"         # 1 of 2 → fire (does NOT wait for b)


# ---- wait_n / n_of_m: fires when a configurable quorum arrives ----

@pytest.mark.parametrize("strategy,count_key", [("n_of_m", "wait_count")])
async def test_n_of_m_fires_at_quorum(strategy, count_key):
    cfg = {"merge_strategy": strategy, count_key: 2}
    mode, _ = await _completion_mode(cfg, ["a", "b", "c"], completed=["a"])
    assert mode == "deferred"          # 1 of 3, need 2 → wait
    mode, _ = await _completion_mode(cfg, ["a", "b", "c"], completed=["a", "b"])
    assert mode == "immediate"         # 2 of 3, need 2 → fire (does NOT wait for c)


async def test_n_of_m_zero_count_falls_back_to_all():
    # wait_count unset/0 → behaves like all (every predecessor required).
    cfg = {"merge_strategy": "n_of_m", "wait_count": 0}
    mode, _ = await _completion_mode(cfg, ["a", "b"], completed=["a"])
    assert mode == "deferred"
    mode, _ = await _completion_mode(cfg, ["a", "b"], completed=["a", "b"])
    assert mode == "immediate"


async def test_unknown_strategy_defaults_to_all():
    cfg = {"merge_strategy": "bogus"}
    mode, _ = await _completion_mode(cfg, ["a", "b"], completed=["a"])
    assert mode == "deferred"
    mode, _ = await _completion_mode(cfg, ["a", "b"], completed=["a", "b"])
    assert mode == "immediate"


# ---- at-most-once: a fired quorum absorbs stragglers (SA-846) ----


async def _two_invocations(config, predecessors, first_completed, second_completed):
    """Run the handler twice against the SAME run (shared quorum buffer) and
    return both completion modes — models a quorum fire followed by a late
    straggler arrival re-entering the merge."""
    node = WorkflowNode(id="merge", node_type=NodeType.MERGE, config=config)
    topology = SimpleNamespace(predecessors={"merge": list(predecessors)})
    ctx = RunContext(topology=topology, variables={})
    for n in first_completed:
        ctx.completed_nodes.add(n)
    run = WorkflowRun(id="r", workflow_id="wf", status=RunStatus.RUNNING, context=ctx)
    handler = MergeNodeHandler()
    first = await handler.handle(
        node=node, run=run, workflow=None, topology=topology,
        completed_task=None, task_factory=None,
    )
    for n in second_completed:
        ctx.completed_nodes.add(n)
    second = await handler.handle(
        node=node, run=run, workflow=None, topology=topology,
        completed_task=None, task_factory=None,
    )
    return first.completion_mode, second.completion_mode


async def test_n_of_m_late_arrival_after_fire_is_absorbed():
    first, second = await _two_invocations(
        {"merge_strategy": "n_of_m", "wait_count": 2},
        ["a", "b", "c"],
        first_completed=["a", "b"],   # quorum met → fire
        second_completed=["c"],       # straggler re-entry
    )
    assert first == "immediate"
    assert second == "absorbed", (
        "late arrival after a fired quorum must be absorbed, not re-fire downstream"
    )


async def test_any_late_arrival_after_fire_is_absorbed():
    first, second = await _two_invocations(
        {"merge_strategy": "any"},
        ["a", "b"],
        first_completed=["a"],
        second_completed=["b"],
    )
    assert first == "immediate"
    assert second == "absorbed"


async def test_sub_quorum_arrivals_still_defer_then_fire():
    """The fired marker must not affect the normal defer→fire progression."""
    first, second = await _two_invocations(
        {"merge_strategy": "n_of_m", "wait_count": 2},
        ["a", "b", "c"],
        first_completed=["a"],        # 1 of 2 → wait
        second_completed=["b"],       # 2 of 2 → fire
    )
    assert first == "deferred"
    assert second == "immediate"


async def test_fired_marker_is_scoped_to_the_buffer_key():
    """A fresh buffer (e.g. next loop iteration deletes its keys) re-arms."""
    node = WorkflowNode(
        id="merge", node_type=NodeType.MERGE,
        config={"merge_strategy": "any"},
    )
    topology = SimpleNamespace(predecessors={"merge": ["a"]})
    ctx = RunContext(topology=topology, variables={})
    ctx.completed_nodes.add("a")
    run = WorkflowRun(id="r", workflow_id="wf", status=RunStatus.RUNNING, context=ctx)
    handler = MergeNodeHandler()
    first = await handler.handle(
        node=node, run=run, workflow=None, topology=topology,
        completed_task=None, task_factory=None,
    )
    assert first.completion_mode == "immediate"
    # Loop-driver-style per-iteration cleanup: drop the buffer → re-armed.
    run.context.merge_quorum_buffers.clear()
    second = await handler.handle(
        node=node, run=run, workflow=None, topology=topology,
        completed_task=None, task_factory=None,
    )
    assert second.completion_mode == "immediate"
