"""Unit tests for the Phase 4b.3 Projector reducer.

Verifies:
- Each supported event type updates the projected state correctly.
- Node-set updates (``completed_nodes``, ``skipped_nodes``) are
  idempotent under replay (set-union semantics).
- Unknown event types pass through unchanged — the reducer is
  forward-compatible.
- ``rebuild_run_state`` folds a log into a single state, matching
  the per-event unit tests.
"""

from __future__ import annotations

from dataclasses import replace

from app.layer2_application.projection import (
    ProjectedRunState,
    rebuild_run_state,
)
from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
)
from app.layer4_frameworks.persistence.memory.memory_event_log_repo import (
    MemoryEventLogRepository,
)


_env_seq = 0


def _env(event_type: str, run_id: str = "run-1", **payload) -> EventLogEnvelope:
    """Build a test envelope with a unique event_id per call.

    The monotonic counter means two envelopes of the same event type
    aren't collapsed by the log's dedupe set — they test the reducer's
    behavior on real distinct events, not dedupe behavior.
    """
    global _env_seq
    _env_seq += 1
    return EventLogEnvelope(
        event_id=payload.pop("_event_id", None) or f"{event_type}-{run_id}-{_env_seq}",
        run_id=run_id,
        topic=event_type.lower(),
        event_type=event_type,
        timestamp="2026-04-11T00:00:00Z",
        payload={**payload, "run_id": run_id},
    )


# ──────────────────────── lifecycle events ───────────────────────


class TestRunLifecycle:
    def test_run_started_sets_running_and_metadata(self):
        state = ProjectedRunState().apply(
            _env("RunStatusChanged", status="started", workflow_id="wf-1"),
        )
        assert state.run_id == "run-1"
        assert state.workflow_id == "wf-1"
        assert state.status == "RUNNING"
        assert state.started_at == "2026-04-11T00:00:00Z"

    def test_run_completed_sets_terminal_state(self):
        state = ProjectedRunState(run_id="run-1", status="RUNNING")
        state = state.apply(
            _env("RunStatusChanged", status="completed", output_data={"result": 42}),
        )
        assert state.status == "COMPLETED"
        assert state.output_data == {"result": 42}
        assert state.completed_at == "2026-04-11T00:00:00Z"

    def test_run_failed_captures_error(self):
        state = ProjectedRunState(run_id="run-1", status="RUNNING").apply(
            _env("RunStatusChanged", status="failed", error="upstream dependency unavailable"),
        )
        assert state.status == "FAILED"
        assert state.error == "upstream dependency unavailable"
        assert state.completed_at == "2026-04-11T00:00:00Z"

    def test_run_cancelled(self):
        state = ProjectedRunState(run_id="run-1", status="RUNNING").apply(
            _env("RunStatusChanged", status="cancelled"),
        )
        assert state.status == "CANCELLED"

    def test_run_paused_and_resumed(self):
        state = ProjectedRunState(run_id="run-1", status="RUNNING").apply(
            _env("RunStatusChanged", status="paused"),
        )
        assert state.status == "PAUSED"
        state = state.apply( _env("RunStatusChanged", status="resumed"))
        assert state.status == "RUNNING"


# ─────────────────────── task / node sets ───────────────────────


class TestTaskEvents:
    def test_task_completed_adds_to_completed_nodes(self):
        state = ProjectedRunState(run_id="run-1").apply(
            _env("NodeCompleted", node_id="n1"),
        )
        assert state.completed_nodes == frozenset({"n1"})

    def test_task_completed_is_idempotent(self):
        """Replaying the same NodeCompleted must not double-count."""
        state = ProjectedRunState(run_id="run-1")
        env = _env("NodeCompleted", node_id="n1")
        state = state.apply( env)
        first = state
        state = state.apply( env)
        assert state == first  # set-union is idempotent

    def test_task_failed_marks_node_completed(self):
        """A failed task ends the node's lifecycle from a projection POV."""
        state = ProjectedRunState(run_id="run-1").apply(
            _env("NodeFailed", node_id="n2"),
        )
        assert "n2" in state.completed_nodes

    def test_task_skipped_adds_to_skipped_nodes(self):
        state = ProjectedRunState(run_id="run-1").apply(
            _env("NodeCancelled", node_id="n3", reason="branch_not_taken"),
        )
        assert state.skipped_nodes == frozenset({"n3"})

    def test_multiple_task_events_accumulate(self):
        state = ProjectedRunState(run_id="run-1")
        state = state.apply( _env("NodeCompleted", node_id="n1"))
        state = state.apply( _env("NodeCompleted", node_id="n2"))
        state = state.apply( _env("NodeCancelled", node_id="n3", reason="branch_not_taken"))
        assert state.completed_nodes == frozenset({"n1", "n2"})
        assert state.skipped_nodes == frozenset({"n3"})

    def test_empty_node_id_is_noop(self):
        state = ProjectedRunState(run_id="run-1").apply(
            _env("NodeCompleted", node_id=""),
        )
        assert state.completed_nodes == frozenset()

    def test_task_cancelled_marks_node_completed(self):
        """Phase 1a engine-split: cancellation is terminal, same as
        failed/completed from the projection's POV."""
        state = ProjectedRunState(run_id="run-1").apply(
            _env(
                "NodeCancelled", node_id="n4",
                cancelled_by="user", reason="run_cancelled",
            ),
        )
        assert "n4" in state.completed_nodes

    def test_task_cancelled_is_idempotent(self):
        state = ProjectedRunState(run_id="run-1")
        env = _env("NodeCancelled", node_id="n4", reason="run_cancelled")
        state = state.apply( env)
        first = state
        state = state.apply( env)
        assert state == first

    def test_edge_traversed_unskips_target(self):
        """SA-846 cycle execution: a taken edge into a skipped node
        un-skips it — all skips are flow-derived, so a later taken edge
        overrides them. Mirrors the aggregate-side discard in the
        dispatch gates."""
        state = ProjectedRunState(run_id="run-1")
        state = state.apply( _env("NodeCancelled", node_id="n3", reason="branch_not_taken"),
        )
        assert state.skipped_nodes == frozenset({"n3"})
        state = state.apply(
            _env("EdgeTraversed", source_node_id="n1", target_node_id="n3",
                 edge_id="e1"),
        )
        assert state.skipped_nodes == frozenset()

    def test_edge_traversed_to_unskipped_target_is_noop(self):
        state = ProjectedRunState(run_id="run-1")
        state = state.apply( _env("NodeCompleted", node_id="n1"))
        before = state
        state = state.apply(
            _env("EdgeTraversed", source_node_id="n1", target_node_id="n2",
                 edge_id="e1"),
        )
        assert state == before

    def test_reskip_after_unskip_round_trips(self):
        """A cycle can skip the same node again on a later visit: the
        re-skip arrives as a fresh (visit-keyed) NodeCancelled and must
        re-add the node after the EdgeTraversed un-skip removed it."""
        state = ProjectedRunState(run_id="run-1")
        state = state.apply( _env("NodeCancelled", node_id="n3", reason="branch_not_taken"),
        )
        state = state.apply(
            _env("EdgeTraversed", source_node_id="n1", target_node_id="n3",
                 edge_id="e1"),
        )
        state = state.apply( _env("NodeCancelled", node_id="n3", reason="branch_not_taken"),
        )
        assert state.skipped_nodes == frozenset({"n3"})

    def test_node_data_edited_updates_variables(self):
        """Phase 1a engine-split: NodeDataEdited folds into the
        ``variables`` dict the same way as NodeOutputProduced."""
        state = ProjectedRunState(run_id="run-1")
        edit_payload = {"state_updates": {"n1": {"value": 7}, "input_a": {"value": 7}}}
        state = state.apply( _env("NodeDataEdited", **edit_payload))
        assert state.variables == {"n1": {"value": 7}, "input_a": {"value": 7}}

    def test_node_data_edited_is_last_write_wins_per_key(self):
        state = ProjectedRunState(run_id="run-1")
        state = state.apply(
            _env("NodeOutputProduced", state_updates={"n1": {"value": 1}}),
        )
        state = state.apply(
            _env("NodeDataEdited", state_updates={"n1": {"value": 9}}),
        )
        assert state.variables["n1"] == {"value": 9}

    def test_run_input_received_populates_input_data(self):
        state = ProjectedRunState(run_id="run-1")
        state = state.apply(
            _env("RunStatusChanged", status="started", input_data={"amount": 42, "customer": "acme"}),
        )
        assert state.input_data == {"amount": 42, "customer": "acme"}

    def test_run_input_received_empty_payload_is_noop(self):
        state = ProjectedRunState(run_id="run-1")
        state = state.apply(
            _env("RunStatusChanged", status="started", input_data={}),
        )
        assert state.input_data == {}


# ─────────────────────── node output (Phase 5.1) ────────────────


class TestNodeOutputProduced:
    def test_variables_populated_from_event(self):
        state = ProjectedRunState(run_id="run-1").apply(
            _env(
                "NodeOutputProduced",
                node_id="compute-1",
                state_updates={
                    "compute-1": {"value": 42},
                    "double_seven": {"value": 42},  # alias
                },
            ),
        )
        assert state.variables == {
            "compute-1": {"value": 42},
            "double_seven": {"value": 42},
        }

    def test_multiple_events_merge_variables(self):
        """Each node's output is additive — the reducer merges rather than
        replaces, matching the live aggregate's per-key semantics."""
        state = ProjectedRunState(run_id="run-1")
        state = state.apply(
            _env(
                "NodeOutputProduced",
                node_id="n1",
                state_updates={"n1": {"a": 1}},
            ),
        )
        state = state.apply(
            _env(
                "NodeOutputProduced",
                node_id="n2",
                state_updates={"n2": {"b": 2}},
            ),
        )
        assert state.variables == {"n1": {"a": 1}, "n2": {"b": 2}}

    def test_last_write_wins_per_key(self):
        """If a node emits two NodeOutputProduced events for the same key,
        the latest one wins (matches dict.update semantics)."""
        state = ProjectedRunState(run_id="run-1")
        state = state.apply(
            _env(
                "NodeOutputProduced",
                node_id="n1",
                state_updates={"n1": {"v": "first"}},
            ),
        )
        state = state.apply(
            _env(
                "NodeOutputProduced",
                node_id="n1",
                state_updates={"n1": {"v": "second"}},
            ),
        )
        assert state.variables == {"n1": {"v": "second"}}

    def test_empty_state_updates_is_noop(self):
        state_before = ProjectedRunState(run_id="run-1", variables={"n1": 1})
        state_after = state_before.apply(
            _env("NodeOutputProduced", node_id="n1", state_updates={}),
        )
        assert state_after.variables == {"n1": 1}

    def test_output_port_is_tracked_per_node(self):
        """Reducer mirrors ``output_port`` onto ``last_output_ports[node_id]``."""
        state = ProjectedRunState(run_id="run-1").apply(
            _env(
                "NodeOutputProduced",
                node_id="task-1",
                state_updates={"task-1": {"result": "ok"}},
                output_port="success",
            ),
        )
        assert state.last_output_ports == {"task-1": "success"}

    def test_taken_edge_ports_are_tracked_per_node(self):
        """CONDITION/SWITCH — ``taken_edge_ports`` mirror per node."""
        state = ProjectedRunState(run_id="run-1").apply(
            _env(
                "NodeOutputProduced",
                node_id="cond-1",
                state_updates={"cond-1": {"output": {}}},
                taken_edge_ports={"e1": "true_branch", "e2": "false_branch"},
            ),
        )
        assert state.last_taken_edge_ports == {
            "cond-1": {"e1": "true_branch", "e2": "false_branch"},
        }

    def test_later_emission_overwrites_port_for_same_node(self):
        """Loop iterations can emit twice for the same node — latest wins."""
        state = ProjectedRunState(run_id="run-1")
        state = state.apply(
            _env(
                "NodeOutputProduced",
                node_id="task-1",
                state_updates={"task-1": {"v": 1}},
                output_port="success",
            ),
        )
        state = state.apply(
            _env(
                "NodeOutputProduced",
                node_id="task-1",
                state_updates={"task-1": {"v": 2}},
                output_port="failure",
            ),
        )
        assert state.last_output_ports == {"task-1": "failure"}

    def test_empty_port_and_edges_leaves_state_untouched(self):
        """A NodeOutputProduced with no port/edges and no state_updates is a noop."""
        state_before = ProjectedRunState(
            run_id="run-1",
            last_output_ports={"task-1": "success"},
        )
        state_after = state_before.apply(
            _env("NodeOutputProduced", node_id="task-1", state_updates={}),
        )
        assert state_after.last_output_ports == {"task-1": "success"}


# ─────────────────────── child workflow ─────────────────────────


class TestChildRunStarted:
    def test_child_run_started_populates_child_runs(self):
        state = ProjectedRunState(run_id="parent-1").apply(
            _env(
                "ChildRunStarted",
                run_id="parent-1",
                parent_node_id="wf-node",
                child_run_id="child-1",
                child_workflow_id="wf-child",
            ),
        )
        assert state.child_runs == {"wf-node": "child-1"}

    def test_missing_parent_node_id_is_noop(self):
        state = ProjectedRunState(run_id="parent-1").apply(
            _env("ChildRunStarted", run_id="parent-1", child_run_id="child-1"),
        )
        assert state.child_runs == {}


# ─────────────────────── unknown events ─────────────────────────


class TestUnknownEvents:
    def test_unknown_event_type_is_passthrough(self):
        """Forward-compat: new event types added in Phase 5 must not
        break replaying existing logs."""
        state_before = ProjectedRunState(
            run_id="run-1", status="RUNNING",
            completed_nodes=frozenset({"n1"}),
        )
        state_after = state_before.apply(
            _env("SomeFutureEventType", payload_field="whatever"),
        )
        assert state_after == state_before

    def test_informational_events_are_noops(self):
        """EdgeTraversed, NodeDispatched, ContextUpdated carry no
        rebuildable state today. They must NOT mutate the state."""
        state_before = ProjectedRunState(run_id="run-1", status="RUNNING")
        for event_type in [
            "EdgeTraversed", "NodeDispatched", "ContextUpdated",
            "InputRequested",
            "ArtifactAdded", "LoopIterationCompleted",
        ]:
            state_after = state_before.apply( _env(event_type))
            assert state_after == state_before, (
                f"{event_type} should be a no-op until Phase 5 gives it semantics"
            )


# ─────────────────────── rebuild_run_state ──────────────────────


class TestRebuildRunState:
    def test_rebuild_empty_log_returns_initial_state(self):
        repo = MemoryEventLogRepository()
        state = rebuild_run_state("missing-run", repo)
        assert state == ProjectedRunState(run_id="missing-run")

    def test_rebuild_folds_events_in_log_order(self):
        repo = MemoryEventLogRepository()
        repo.append(_env("RunStatusChanged", status="started", workflow_id="wf-1"))
        repo.append(_env("NodeCompleted", node_id="n1"))
        repo.append(_env("NodeCompleted", node_id="n2"))
        repo.append(_env("NodeCancelled", node_id="n3", reason="branch_not_taken"))
        repo.append(_env("RunStatusChanged", status="completed"))

        state = rebuild_run_state("run-1", repo)
        assert state.run_id == "run-1"
        assert state.workflow_id == "wf-1"
        assert state.status == "COMPLETED"
        assert state.completed_nodes == frozenset({"n1", "n2"})
        assert state.skipped_nodes == frozenset({"n3"})

    def test_rebuild_is_deterministic_under_replay(self):
        """Calling rebuild twice on the same log yields identical state.
        This is the property the engine's redelivery + event log dedupe
        + reducer idempotency together guarantee."""
        repo = MemoryEventLogRepository()
        for event in [
            _env("RunStatusChanged", status="started", workflow_id="wf-1"),
            _env("NodeCompleted", node_id="n1"),
            _env("NodeCompleted", node_id="n2"),
            _env("RunStatusChanged", status="completed"),
        ]:
            repo.append(event)

        first = rebuild_run_state("run-1", repo)
        second = rebuild_run_state("run-1", repo)
        assert first == second

    def test_run_failed_precedence_over_earlier_running(self):
        """A RunFailed at the end overrides earlier RUNNING state."""
        repo = MemoryEventLogRepository()
        repo.append(_env("RunStatusChanged", status="started", workflow_id="wf-1"))
        repo.append(_env("NodeCompleted", node_id="n1"))
        repo.append(_env("RunStatusChanged", status="failed", error="boom"))

        state = rebuild_run_state("run-1", repo)
        assert state.status == "FAILED"
        assert state.error == "boom"
        assert state.completed_nodes == frozenset({"n1"})
