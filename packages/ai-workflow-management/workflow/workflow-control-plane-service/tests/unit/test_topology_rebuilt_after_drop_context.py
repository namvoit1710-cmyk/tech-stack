"""``topology`` must be rebuilt on reload — the regression `DROP CONTEXT` caused.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §13.

`topology` used to ride `AIW_WORKFLOW_RUNS.CONTEXT`. The §13 audit cleared it for
the drop by recording it as *"safe — rebuilt from AIW_WORKFLOWS"*. That is true
of the projector VIEW (`projector_view_store`, byte-identical output) and **false
of the aggregate**: `HanaRunRepository._row_to_entity` built it from the
`topology` key inside `CONTEXT`, and nothing else ever built it.

So after the column was dropped on tenant-1, every reload returned an EMPTY
topology, and the failure was silent in the worst way — a `/test/full` of a
4-iteration loop workflow went **RUNNING → COMPLETED with two
`RunStatusChanged` events and nothing else**. No node ran. No loop event. No
error, no failed task. Measured, not hypothesised: zero `%Loop%` rows in
`AIW_EVENT_LOG` for that run.

The use cases were never the problem — all seven call `GraphTopology.from_workflow`
and set it in memory. The orchestration worker is: it reloads through
`aget_by_id` and then reads `run.context.topology` at `:1873`, `:1933`, `:1987`,
`:2143` and `:2214`. Empty topology → no successors → the run "finishes".
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import (
    GraphTopology,
    RunContext,
    WorkflowRun,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.orchestration.orchestration_engine import (
    OrchestrationEngine,
)

_ensure = OrchestrationEngine._ensure_topology


def _workflow() -> Workflow:
    return Workflow(
        id="wf", name="WF",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="task", node_type=NodeType.TASK, worker_type="w"),
        ],
        edges=[WorkflowEdge(id="e1", source_node_id="trigger",
                            target_node_id="task")],
    )


def _run(topology: GraphTopology | None = None) -> WorkflowRun:
    return WorkflowRun(id="run-1", workflow_id="wf", workflow_version="1",
                       context=RunContext(topology=topology or GraphTopology()))


class TestAnEmptyTopologyIsRebuilt:
    def test_the_reloaded_run_gets_its_edges_back(self):
        """The exact post-drop shape: `_row_to_entity` has no `CONTEXT` to read,
        so it hands back a default `GraphTopology()`."""
        run = _run()
        assert run.context.topology.edge_map == {}
        _ensure(run, _workflow())
        assert run.context.topology.adjacency == {"trigger": ["task"], "task": []}
        assert [e.id for e in run.context.topology.edge_map["trigger"]] == ["e1"]

    def test_entry_nodes_come_back(self):
        """Without these, `decide_entry` dispatches nothing and the run
        completes having executed nothing — two RunStatusChanged and silence."""
        run = _run()
        _ensure(run, _workflow())
        assert run.context.topology.entry_nodes == ["trigger"]

    def test_it_matches_a_direct_from_workflow_build(self):
        """The rebuild has to be the SAME topology the use cases construct, or
        the reload path and the create path disagree about the graph."""
        wf = _workflow()
        run = _run()
        _ensure(run, wf)
        expected = GraphTopology.from_workflow(wf)
        assert run.context.topology.adjacency == expected.adjacency
        assert run.context.topology.predecessors == expected.predecessors
        assert run.context.topology.entry_nodes == expected.entry_nodes
        assert run.context.topology.leaf_nodes == expected.leaf_nodes


class TestItNeverOverwritesALiveTopology:
    def test_an_existing_topology_is_left_alone(self):
        """Every use case sets one before the first save. Rebuilding over it
        would discard whatever they had deliberately arranged."""
        wf = _workflow()
        pinned = GraphTopology(adjacency={"only": []}, predecessors={"only": []},
                               edge_map={"only": []}, entry_nodes=["only"],
                               leaf_nodes=["only"])
        run = _run(pinned)
        _ensure(run, wf)
        assert run.context.topology is pinned

    def test_an_edge_map_alone_counts_as_present(self):
        """A single-node workflow has empty adjacency lists but a real edge_map
        — treating that as absent would rebuild on every reload."""
        run = _run(GraphTopology(edge_map={"n": []}))
        _ensure(run, _workflow())
        assert run.context.topology.edge_map == {"n": []}


class TestTheRebuildIsACTUALLYWIRED:
    """Found by mutation: removing the call from ``load_workflow_for_run`` left
    all nine tests green, because they exercised ``_ensure_topology`` directly.
    An unwired fix is indistinguishable from no fix, and this one exists to stop
    a silent production outage — so the wiring gets its own test."""

    def test_load_workflow_for_run_rebuilds_the_topology(self):
        wf = _workflow()

        class _Snapshot:
            # The loader rebuilds through Workflow.from_snapshot_dict, so the
            # round-trip is exercised too — the rebuilt topology comes from the
            # PINNED definition, not from whatever the caller had in hand.
            definition = wf.to_snapshot_dict()

        class _SnapshotRepo:
            """Enough of the snapshot repo for the loader: it resolves the run's
            pinned workflow, which is the same snapshot the dropped column had
            serialised its topology from."""
            def get_by_id(self, snapshot_id):
                return _Snapshot()

            def get_by_workflow_version(self, workflow_id, version):
                return _Snapshot()

        engine = OrchestrationEngine.__new__(OrchestrationEngine)
        engine._workflow_snapshot_repo = _SnapshotRepo()

        run = _run()
        run.workflow_snapshot_id = "snap-1"
        loaded = engine.load_workflow_for_run(run)

        assert loaded is not None
        assert run.context.topology.entry_nodes == ["trigger"], (
            "load_workflow_for_run must rebuild the topology — the orchestration "
            "worker reads run.context.topology straight after this call"
        )


class TestItCannotBreakTheLoadPath:
    """``load_workflow_for_run`` runs on every orchestration step. Raising here
    would convert a missing workflow into a crashed run."""

    def test_no_workflow_is_a_no_op(self):
        run = _run()
        _ensure(run, None)
        assert run.context.topology.adjacency == {}

    def test_no_context_is_a_no_op(self):
        class Bare:
            context = None
        _ensure(Bare(), _workflow())

    def test_a_run_without_a_context_attribute_is_a_no_op(self):
        class Bare:
            pass
        _ensure(Bare(), _workflow())

    def test_a_workflow_with_no_edges_still_rebuilds(self):
        wf = Workflow(id="wf", name="WF",
                      nodes=[WorkflowNode(id="solo", node_type=NodeType.TRIGGER)],
                      edges=[])
        run = _run()
        _ensure(run, wf)
        assert run.context.topology.entry_nodes == ["solo"]
        assert run.context.topology.leaf_nodes == ["solo"]
