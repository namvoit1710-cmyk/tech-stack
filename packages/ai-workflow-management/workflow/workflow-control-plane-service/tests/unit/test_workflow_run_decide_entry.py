"""Pure unit tests for ``WorkflowRun.decide_entry``.

The ``decide`` half of the entry-node dispatch seam (slice 3 of the
decide/evolve refactor — see ``docs/design/run-aggregate-decide-evolve-seam.md``).
Given the topology's entry node ids and the workflow definition, it classifies
each entry node as ``inline`` (PENDING-category — INPUT / HUMAN_ACTION /
DATA_EDIT / FILE_UPLOAD) or ``async`` (TASK / BUILTIN), preserving order and
dropping ids absent from the workflow — no repos, no queue, no async, no mocks.
The engine shell (`handle_entry_nodes`) performs the dispatch each item implies.

The order of ``dispatches`` is part of the contract: the original engine loop
dispatched in a single pass, so a mixed run must interleave inline/async I/O in
entry order rather than batch by mode.
"""

from __future__ import annotations

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import (
    WorkflowRun,
    RunContext,
    EntryDecision,
    EntryDispatch,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus


def _run(**ctx) -> WorkflowRun:
    return WorkflowRun(
        id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING,
        context=RunContext(**ctx),
    )


def _node(node_id: str, node_type: NodeType = NodeType.TASK) -> WorkflowNode:
    return WorkflowNode(id=node_id, node_type=node_type)


def _workflow(nodes: list[WorkflowNode]) -> Workflow:
    return Workflow(id="wf-1", workflow_id="wf-1", nodes=nodes)


# ── single-entry classification ─────────────────────────────────────────

def test_pending_input_entry_is_inline():
    wf = _workflow([_node("a", NodeType.INPUT)])
    decision = _run().decide_entry(["a"], wf)
    assert decision == EntryDecision((EntryDispatch("a", "inline"),))


def test_pending_human_action_entry_is_inline():
    wf = _workflow([_node("a", NodeType.HUMAN_ACTION)])
    decision = _run().decide_entry(["a"], wf)
    assert decision.dispatches == (EntryDispatch("a", "inline"),)


def test_task_entry_is_async():
    wf = _workflow([_node("a", NodeType.TASK)])
    decision = _run().decide_entry(["a"], wf)
    assert decision.dispatches == (EntryDispatch("a", "async"),)


def test_builtin_trigger_entry_is_async():
    wf = _workflow([_node("a", NodeType.TRIGGER)])
    assert _run().decide_entry(["a"], wf).dispatches == (EntryDispatch("a", "async"),)


def test_builtin_parallel_entry_is_async():
    wf = _workflow([_node("a", NodeType.PARALLEL)])
    assert _run().decide_entry(["a"], wf).dispatches == (EntryDispatch("a", "async"),)


# ── ordering contract (byte-identical interleaving) ─────────────────────

def test_mixed_entries_preserve_order_and_mode():
    wf = _workflow([
        _node("in", NodeType.INPUT),
        _node("task", NodeType.TASK),
        _node("ha", NodeType.HUMAN_ACTION),
    ])
    decision = _run().decide_entry(["in", "task", "ha"], wf)
    assert decision.dispatches == (
        EntryDispatch("in", "inline"),
        EntryDispatch("task", "async"),
        EntryDispatch("ha", "inline"),
    )


# ── filtering / edge cases ──────────────────────────────────────────────

def test_missing_node_is_dropped():
    wf = _workflow([_node("a", NodeType.TASK)])  # no node "ghost"
    decision = _run().decide_entry(["ghost", "a"], wf)
    assert decision.dispatches == (EntryDispatch("a", "async"),)


def test_empty_entry_ids_yields_empty_plan():
    assert _run().decide_entry([], _workflow([])).dispatches == ()


# ── every PENDING node type is inline ───────────────────────────────────

def test_all_pending_node_types_are_inline():
    pending = [
        NodeType.INPUT, NodeType.HUMAN_ACTION,
        NodeType.DATA_EDIT, NodeType.FILE_UPLOAD,
    ]
    wf = _workflow([_node(t.value, t) for t in pending])
    decision = _run().decide_entry([t.value for t in pending], wf)
    assert all(d.mode == "inline" for d in decision.dispatches)
    assert len(decision.dispatches) == len(pending)
