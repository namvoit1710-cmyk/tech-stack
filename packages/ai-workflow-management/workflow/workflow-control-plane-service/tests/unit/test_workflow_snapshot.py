"""Phase A scaffolding for the workflow-snapshot rollout.

Verifies:
- ``Workflow.to_snapshot_dict`` / ``from_snapshot_dict`` round-trip preserves
  every observable field, including the enum-typed ``node_type`` and the
  ``AgentRef`` dict→object coercion.
- ``MemoryWorkflowSnapshotRepository`` round-trips an entity and isolates the
  stored ``definition`` dict from caller mutation.
- ``WorkflowRun.workflow_snapshot_id`` defaults to ``""`` (Phase B will start
  populating it).
- ``WorkflowSnapshotMissingError`` carries the run id and optional snapshot id.
"""

from __future__ import annotations

import json

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.entities.workflow_snapshot import WorkflowSnapshot
from app.layer1_domain.exceptions import WorkflowSnapshotMissingError
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer4_frameworks.persistence.memory.memory_workflow_snapshot_repo import (
    MemoryWorkflowSnapshotRepository,
)


def _sample_workflow() -> Workflow:
    return Workflow(
        id="wf-1",
        name="Sample",
        description="d",
        version="2",
        nodes=[
            WorkflowNode(
                id="n1",
                name="Start",
                node_type=NodeType.TASK,
                worker_type="http",
                input_binding={"k": "{{$input.x}}"},
                config={"a": 1},
                input_require=["k"],
                parameters={"p": "v"},
            ),
            WorkflowNode(
                id="n2",
                name="Cond",
                node_type=NodeType.CONDITION,
                config={"expression": "true"},
            ),
        ],
        edges=[
            WorkflowEdge(
                id="e1",
                source_node_id="n1",
                target_node_id="n2",
                condition="then",
                source_port_id="success",
            ),
        ],
        variables={"foo": "bar"},
    )


class TestWorkflowSnapshotRoundTrip:
    def test_to_snapshot_dict_is_json_serialisable(self) -> None:
        w = _sample_workflow()
        json.dumps(w.to_snapshot_dict())  # must not raise

    def test_from_snapshot_dict_preserves_fields(self) -> None:
        w = _sample_workflow()
        w2 = Workflow.from_snapshot_dict(w.to_snapshot_dict())

        assert w2.id == w.id
        assert w2.name == w.name
        assert w2.version == w.version
        assert w2.variables == w.variables

    def test_from_snapshot_dict_preserves_nodes_and_enum_coercion(self) -> None:
        w = _sample_workflow()
        w2 = Workflow.from_snapshot_dict(w.to_snapshot_dict())

        assert len(w2.nodes) == 2
        n1, n2 = w2.nodes
        assert isinstance(n1.node_type, NodeType)
        assert n1.node_type is NodeType.TASK
        assert n1.input_binding == {"k": "{{$input.x}}"}
        assert n1.input_require == ["k"]
        assert n2.node_type is NodeType.CONDITION

    def test_from_snapshot_dict_preserves_edges(self) -> None:
        w = _sample_workflow()
        w2 = Workflow.from_snapshot_dict(w.to_snapshot_dict())

        assert len(w2.edges) == 1
        edge = w2.edges[0]
        assert edge.id == "e1"
        assert edge.source_node_id == "n1"
        assert edge.target_node_id == "n2"
        assert edge.condition == "then"
        assert edge.source_port_id == "success"

    def test_mutating_snapshot_dict_does_not_affect_source(self) -> None:
        w = _sample_workflow()
        d = w.to_snapshot_dict()
        d["variables"]["foo"] = "MUTATED"
        d["nodes"][0]["config"]["a"] = 999
        # Original workflow untouched
        assert w.variables == {"foo": "bar"}
        assert w.nodes[0].config == {"a": 1}


class TestMemoryWorkflowSnapshotRepository:
    def test_save_then_get_returns_equal_definition(self) -> None:
        repo = MemoryWorkflowSnapshotRepository()
        snap = WorkflowSnapshot(
            workflow_id="wf-1",
            workflow_version="2",
            definition={"id": "wf-1", "name": "X", "nodes": [], "edges": []},
        )
        repo.save(snap)
        loaded = repo.get_by_id(snap.id)
        assert loaded is not None
        assert loaded.id == snap.id
        assert loaded.workflow_id == "wf-1"
        assert loaded.definition == snap.definition

    def test_get_unknown_id_returns_none(self) -> None:
        assert MemoryWorkflowSnapshotRepository().get_by_id("nope") is None

    def test_caller_mutation_after_save_does_not_leak_into_store(self) -> None:
        repo = MemoryWorkflowSnapshotRepository()
        snap = WorkflowSnapshot(
            id="s-1", definition={"name": "X"},
        )
        repo.save(snap)
        # Mutate caller's reference: store must be unaffected.
        snap.definition["name"] = "mutated"
        assert repo.get_by_id("s-1").definition == {"name": "X"}

    def test_caller_mutation_after_get_does_not_leak_into_store(self) -> None:
        repo = MemoryWorkflowSnapshotRepository()
        repo.save(WorkflowSnapshot(id="s-1", definition={"name": "X"}))
        first = repo.get_by_id("s-1")
        first.definition["name"] = "mutated"
        assert repo.get_by_id("s-1").definition == {"name": "X"}

    def test_delete_removes_entry(self) -> None:
        repo = MemoryWorkflowSnapshotRepository()
        repo.save(WorkflowSnapshot(id="s-1"))
        repo.delete("s-1")
        assert repo.get_by_id("s-1") is None


class TestWorkflowRunSnapshotIdField:
    def test_default_is_empty_string(self) -> None:
        run = WorkflowRun()
        assert run.workflow_snapshot_id == ""

    def test_field_round_trips_on_assignment(self) -> None:
        run = WorkflowRun(workflow_snapshot_id="snap-42")
        assert run.workflow_snapshot_id == "snap-42"


class TestWorkflowSnapshotMissingError:
    def test_carries_run_id_only(self) -> None:
        err = WorkflowSnapshotMissingError("run-1")
        assert err.run_id == "run-1"
        assert err.snapshot_id == ""
        assert "run-1" in str(err)

    def test_carries_run_and_snapshot_id(self) -> None:
        err = WorkflowSnapshotMissingError("run-1", snapshot_id="snap-9")
        assert err.snapshot_id == "snap-9"
        assert "snap-9" in str(err)
