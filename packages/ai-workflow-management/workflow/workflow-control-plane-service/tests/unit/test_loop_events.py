"""Unit tests for the Phase 4c.4.2 loop event vocabulary.

Covers:

- The five loop event dataclasses (``LoopStarted``, ``LoopIterationStarted``,
  ``LoopIterationCompleted``, ``LoopCompleted``, ``LoopExitRequested``).
- Backward compatibility of ``LoopIterationCompleted`` — every existing
  call site at ``loop_handler.py:163`` (legacy-only kwargs) must still
  compile and produce a valid event.
- Forward compatibility — the new ``invocation_id`` / ``scope_stack`` /
  ``iter`` / ``outcome`` fields accept values without breaking the
  legacy fields.
- The projector reducer treats all five event types as no-op
  passthroughs — replaying a log of just loop events produces a state
  equivalent to the initial ``ProjectedRunState``.

Phase 4c.4.2 lands the vocabulary only. No handler emits these new
events yet. Phase 4c.4.3 wires the event-driven loop handler which
becomes the first emitter.
"""

from __future__ import annotations

from app.layer1_domain.events import (
    DomainEvent,
    LoopCompleted,
    LoopExitRequested,
    LoopIterationCompleted,
    LoopIterationStarted,
    LoopStarted,
)
from app.layer1_domain.run_projection import (
    ProjectedRunState,
)
from app.layer1_domain.value_objects.loop_scope import LoopScope
from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
)


# ──────────────────────────── LoopStarted ───────────────────────────


class TestLoopStarted:
    def test_default_construction(self):
        event = LoopStarted()
        assert event.run_id == ""
        assert event.workflow_id == ""
        assert event.node_id == ""
        assert event.invocation_id == ""
        assert event.scope_stack == []
        assert event.loop_type == ""
        assert event.mode == ""
        assert event.total == 0
        assert event.parallelism == 1
        # Inherited from DomainEvent:
        assert event.event_id  # uuid4 auto-generated
        assert event.timestamp  # iso timestamp auto-generated
        assert event.source == ""
        assert event.root_run_id == ""
        assert event.depth == 0

    def test_populated_construction(self):
        event = LoopStarted(
            run_id="run-1",
            workflow_id="wf-1",
            node_id="loop-1",
            invocation_id="abc123",
            scope_stack=[],
            loop_type="collection",
            mode="parallel",
            total=5,
            parallelism=2,
            root_run_id="run-1",
            depth=0,
            source="workflow-control-plane",
        )
        assert event.node_id == "loop-1"
        assert event.invocation_id == "abc123"
        assert event.loop_type == "collection"
        assert event.mode == "parallel"
        assert event.total == 5
        assert event.parallelism == 2

    def test_scope_stack_round_trip_via_to_dict(self):
        """scope_stack carries LoopScope.to_dict() dicts so the event
        payload serialises through JSON in the event log."""
        outer = LoopScope(
            loop_node_id="loop-outer",
            invocation_id="outer-inv",
            iter=1,
            total=3,
        )
        event = LoopStarted(
            run_id="run-1",
            node_id="loop-inner",
            invocation_id="inner-inv",
            scope_stack=[outer.to_dict()],
            loop_type="count",
            mode="sequential",
            total=10,
        )
        assert len(event.scope_stack) == 1
        assert event.scope_stack[0] == {
            "loop_node_id": "loop-outer",
            "invocation_id": "outer-inv",
            "iter": 1,
            "total": 3,
        }
        # And the round-trip reconstitutes the scope correctly.
        rebuilt = LoopScope.from_dict(event.scope_stack[0])
        assert rebuilt == outer

    def test_inherits_from_domain_event(self):
        assert isinstance(LoopStarted(), DomainEvent)


# ──────────────────── LoopIterationStarted ─────────────────────


class TestLoopIterationStarted:
    def test_default_construction(self):
        event = LoopIterationStarted()
        assert event.run_id == ""
        assert event.node_id == ""
        assert event.invocation_id == ""
        assert event.scope_stack == []
        assert event.iter == 0
        assert event.item is None

    def test_item_can_be_arbitrary_json_value(self):
        # String item (collection loop)
        s = LoopIterationStarted(iter=0, item="apple")
        assert s.item == "apple"
        # Int item (count loop)
        n = LoopIterationStarted(iter=2, item=2)
        assert n.item == 2
        # Dict item (collection of objects)
        d = LoopIterationStarted(iter=1, item={"name": "banana", "qty": 3})
        assert d.item == {"name": "banana", "qty": 3}
        # None item (falls back to iter index)
        z = LoopIterationStarted(iter=0, item=None)
        assert z.item is None

    def test_scope_stack_tail_includes_self(self):
        """The spike §3.6 invariant: LoopIterationStarted's scope_stack
        has the current loop's scope at the tail."""
        this_scope = LoopScope(
            loop_node_id="loop-1",
            invocation_id="inv-1",
            iter=2,
            total=5,
        )
        event = LoopIterationStarted(
            run_id="run-1",
            node_id="loop-1",
            invocation_id="inv-1",
            scope_stack=[this_scope.to_dict()],
            iter=2,
            item="c",
        )
        assert event.scope_stack[-1] == this_scope.to_dict()


# ─────────────────── LoopIterationCompleted ────────────────────


class TestLoopIterationCompleted:
    def test_legacy_kwargs_only_still_valid(self):
        """Regression guard: the existing emitter at loop_handler.py:163
        constructs this event with ONLY ``iteration`` + ``total`` + the
        base fields. Adding optional fields must not break it."""
        event = LoopIterationCompleted(
            run_id="run-1",
            workflow_id="wf-1",
            node_id="loop-1",
            iteration=2,
            total=5,
            root_run_id="run-1",
            depth=0,
            source="workflow-control-plane",
        )
        assert event.iteration == 2
        assert event.total == 5
        # New fields default to their zero values:
        assert event.invocation_id == ""
        assert event.scope_stack == []
        assert event.iter == 0
        assert event.outcome == "ok"

    def test_new_fields_populated_alongside_legacy(self):
        """Phase 4c.4.3 emitter dual-writes ``iteration`` and ``iter``."""
        event = LoopIterationCompleted(
            run_id="run-1",
            node_id="loop-1",
            iteration=3,
            total=5,
            invocation_id="inv-xyz",
            scope_stack=[
                LoopScope("loop-1", "inv-xyz", iter=3, total=5).to_dict()
            ],
            iter=3,
            outcome="ok",
        )
        assert event.iteration == 3
        assert event.iter == 3
        assert event.invocation_id == "inv-xyz"
        assert event.outcome == "ok"
        assert len(event.scope_stack) == 1

    def test_outcome_accepts_spike_sentinel_values(self):
        for outcome in ("ok", "failed", "skipped"):
            event = LoopIterationCompleted(outcome=outcome)
            assert event.outcome == outcome

    def test_iteration_and_iter_can_drift(self):
        """During the migration window either field can be set
        independently — the handler dual-writes but legacy emitters
        only touch ``iteration``."""
        event = LoopIterationCompleted(iteration=5)
        assert event.iteration == 5
        assert event.iter == 0  # not touched
        event2 = LoopIterationCompleted(iter=5)
        assert event2.iteration == 0  # not touched
        assert event2.iter == 5


# ───────────────────────── LoopCompleted ───────────────────────


class TestLoopCompleted:
    def test_default_construction(self):
        event = LoopCompleted()
        assert event.node_id == ""
        assert event.invocation_id == ""
        assert event.scope_stack == []
        assert event.total_iterations == 0
        assert event.reason == "all_done"

    def test_all_reason_sentinel_values(self):
        for reason in (
            "all_done", "condition_false", "break", "early_exit_error",
        ):
            event = LoopCompleted(
                node_id="loop-1",
                invocation_id="inv-1",
                total_iterations=3,
                reason=reason,
            )
            assert event.reason == reason


# ─────────────────────── LoopExitRequested ─────────────────────


class TestLoopExitRequested:
    def test_default_and_populated_construction(self):
        default = LoopExitRequested()
        assert default.loop_node_id == ""
        assert default.invocation_id == ""
        assert default.scope_stack == []

        populated = LoopExitRequested(
            run_id="run-1",
            workflow_id="wf-1",
            loop_node_id="loop-1",
            invocation_id="inv-1",
            scope_stack=[
                LoopScope("loop-1", "inv-1", iter=2, total=5).to_dict()
            ],
        )
        assert populated.loop_node_id == "loop-1"
        assert populated.invocation_id == "inv-1"
        assert len(populated.scope_stack) == 1


# ───────────────── Projector reducer passthrough ───────────────


class TestProjectorLoopEvents:
    """Phase 4c.4.2 adds explicit passthrough branches for loop events.

    The reducer has no state to rebuild from them yet — loop lifecycle
    is captured indirectly via the body NodeCompleted events which
    already contribute to ``completed_nodes``. These tests enforce
    that the branches are NO-OPs: replaying a log of just loop events
    produces the same state as applying no events at all.
    """

    @staticmethod
    def _envelope(event_type: str, **payload) -> EventLogEnvelope:
        return EventLogEnvelope(
            event_id=f"evt-{event_type}",
            run_id="run-1",
            topic="domain.events",
            event_type=event_type,
            timestamp="2026-04-11T00:00:00+00:00",
            payload=payload,
        )

    def test_loop_started_is_passthrough(self):
        initial = ProjectedRunState(run_id="run-1")
        envelope = self._envelope(
            "LoopStarted",
            run_id="run-1",
            node_id="loop-1",
            invocation_id="inv-1",
            scope_stack=[],
            loop_type="collection",
            mode="sequential",
            total=3,
        )
        result = initial.apply( envelope)
        assert result == initial

    def test_non_terminal_loop_events_passthrough_but_completed_marks_node(self):
        """The non-terminal loop lifecycle events don't change projected state
        (the projector doesn't track active loops from them). But LoopCompleted
        IS the loop NODE's completion signal — no NodeCompleted is emitted for a
        loop node itself — so it folds the loop node into completed_nodes; else
        the loop node stays un-completed and the run never finalizes."""
        initial = ProjectedRunState(run_id="run-1")
        passthrough = [
            self._envelope("LoopStarted", node_id="loop-1", invocation_id="i1"),
            self._envelope("LoopIterationStarted", node_id="loop-1", iter=0),
            self._envelope("LoopIterationCompleted", node_id="loop-1", iter=0, outcome="ok"),
            self._envelope("LoopIterationStarted", node_id="loop-1", iter=1),
            self._envelope("LoopIterationCompleted", node_id="loop-1", iter=1, outcome="ok"),
            self._envelope("LoopExitRequested", loop_node_id="loop-1"),
        ]
        state = initial
        for env in passthrough:
            state = state.apply(env)
        assert state == initial, "non-terminal loop events must be passthrough"

        state = state.apply(self._envelope(
            "LoopCompleted", node_id="loop-1", total_iterations=2, reason="all_done",
        ))
        assert "loop-1" in state.completed_nodes, (
            "LoopCompleted must mark the loop node completed"
        )
