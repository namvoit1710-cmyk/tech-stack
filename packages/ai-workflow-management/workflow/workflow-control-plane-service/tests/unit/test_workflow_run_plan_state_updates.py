"""Pure unit tests for ``WorkflowRun.plan_state_updates``.

The ``decide`` half of the state-update / node-output seam (slice 5, the final
slice of the decide/evolve refactor — see
``docs/design/run-aggregate-decide-evolve-seam.md``). Given the already-file-ref-
wrapped state updates plus the workflow, it builds the full write-set (primary
keys + node-name aliases) and constructs the ``NodeOutputProduced`` event that
carries it — no repos, no file upload, no publish, no mutation, no async, no
mocks. The engine shell (``apply_state_updates_as_files``) wraps values to file-
refs first (the only I/O the write-set needs), then publishes the returned event
and writes the read-side port mirrors.
"""

from __future__ import annotations

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext
from app.layer1_domain.events import NodeOutputProduced
from app.layer1_domain.value_objects.event_id import EventId


def _run(**kw) -> WorkflowRun:
    base = dict(
        id="run-1", workflow_id="wf-1", root_run_id="root-1",
        depth_level=2, context=RunContext(),
    )
    base.update(kw)
    return WorkflowRun(**base)


def _node(node_id: str, name: str = "") -> WorkflowNode:
    return WorkflowNode(id=node_id, name=name)


def _workflow(nodes: list[WorkflowNode]) -> Workflow:
    return Workflow(id="wf-1", workflow_id="wf-1", nodes=nodes)


# ── write-set construction ────────────────────────────────────────────────

def test_single_key_no_alias():
    wf = _workflow([_node("a")])  # name empty → no alias
    event = _run().plan_state_updates(
        {"a": {"x": 1}}, wf, source_node_id="a", output_port="success",
    )
    assert isinstance(event, NodeOutputProduced)
    assert event.state_updates == {"a": {"x": 1}}
    assert event.node_id == "a"
    assert event.output_port == "success"


def test_node_name_alias_added():
    # A node whose name differs from its id contributes a second write-set entry.
    wf = _workflow([_node("a", name="Compute")])
    event = _run().plan_state_updates({"a": {"x": 1}}, wf, source_node_id="a")
    assert event.state_updates == {"a": {"x": 1}, "Compute": {"x": 1}}


def test_name_equal_to_id_adds_no_alias():
    wf = _workflow([_node("a", name="a")])
    event = _run().plan_state_updates({"a": 1}, wf, source_node_id="a")
    assert event.state_updates == {"a": 1}


# ── primary node id ───────────────────────────────────────────────────────

def test_source_node_id_overrides_first_key():
    wf = _workflow([_node("a"), _node("b")])
    event = _run().plan_state_updates({"a": 1, "b": 2}, wf, source_node_id="b")
    assert event.node_id == "b"


def test_primary_node_id_falls_back_to_first_key():
    wf = _workflow([_node("a"), _node("b")])
    event = _run().plan_state_updates({"a": 1, "b": 2}, wf)  # no source_node_id
    assert event.node_id == "a"


# ── deterministic event id ────────────────────────────────────────────────

def test_event_id_deterministic_for_same_inputs():
    wf = _workflow([_node("a")])
    e1 = _run().plan_state_updates({"a": 1}, wf, source_node_id="a", causation_id="c1")
    e2 = _run().plan_state_updates({"a": 1}, wf, source_node_id="a", causation_id="c1")
    assert e1.event_id == e2.event_id


def test_attempt_id_changes_event_id():
    # Reruns keep the same task.id but bump attempt_id → fresh event id so the
    # event log does not dedupe the rerun against the prior attempt.
    wf = _workflow([_node("a")])
    no_attempt = _run().plan_state_updates({"a": 1}, wf, source_node_id="a", causation_id="c1")
    with_attempt = _run().plan_state_updates(
        {"a": 1}, wf, source_node_id="a", causation_id="c1", attempt_id="k2",
    )
    assert no_attempt.event_id != with_attempt.event_id


def test_causation_id_falls_back_to_run_id():
    wf = _workflow([_node("a")])
    event = _run().plan_state_updates({"a": 1}, wf, source_node_id="a")  # no causation_id
    expected = EventId.derive("run-1", "output:a")
    assert event.event_id == expected


# ── provenance + routing carried through ──────────────────────────────────

def test_provenance_and_taken_edge_ports_carried():
    wf = _workflow([_node("a")])
    taken = {"e1": "true_branch"}
    event = _run().plan_state_updates(
        {"a": 1}, wf, source_node_id="a", output_port="success", taken_edge_ports=taken,
    )
    assert event.run_id == "run-1"
    assert event.workflow_id == "wf-1"
    assert event.root_run_id == "root-1"
    assert event.depth == 2
    assert event.source == "workflow-control-plane"
    assert event.taken_edge_ports == {"e1": "true_branch"}
    # Copied, not aliased — mutating the input must not affect the event.
    taken["e2"] = "false_branch"
    assert event.taken_edge_ports == {"e1": "true_branch"}


def test_no_taken_edge_ports_yields_empty_dict():
    wf = _workflow([_node("a")])
    event = _run().plan_state_updates({"a": 1}, wf, source_node_id="a")
    assert event.taken_edge_ports == {}
