"""ProjectedRunState must fold ``LoopCompleted`` into ``completed_nodes``.

``LoopCompleted`` is the loop NODE's completion signal — no ``NodeCompleted`` is
emitted for a loop node itself (only its body nodes get one). Before the fix the
projector no-op'd it, so on the authoritative (HANA) read path the loop node
stayed un-completed forever and the run never finalized once loops finally
advanced. The aggregate-side ``completed_nodes.add(loop)`` masked this only for
the in-memory read path.
"""

from __future__ import annotations

from app.layer1_domain.run_projection import ProjectedRunState
from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
)


def _env(event_type: str, **payload) -> EventLogEnvelope:
    return EventLogEnvelope(
        event_id=f"e-{event_type}-{payload.get('node_id', '')}",
        run_id="r1",
        topic=event_type.lower(),
        event_type=event_type,
        timestamp="2026-07-11T00:00:00Z",
        payload={**payload, "run_id": "r1"},
    )


def _state() -> ProjectedRunState:
    return ProjectedRunState(run_id="r1", workflow_id="wf")


def test_loop_completed_all_done_marks_loop_node_completed():
    st = _state().apply(_env("LoopCompleted", node_id="loop", reason="all_done"))
    assert "loop" in st.completed_nodes


def test_loop_completed_other_reasons_also_complete_node():
    for reason in ("condition_false", "early_exit_error", "all_done"):
        st = _state().apply(_env("LoopCompleted", node_id="loop", reason=reason))
        assert "loop" in st.completed_nodes, reason


def test_non_terminal_loop_events_do_not_complete_the_node():
    st = _state()
    for et in (
        "LoopStarted", "LoopIterationStarted", "LoopIterationCompleted",
        "LoopExitRequested", "LoopContinueRequested",
    ):
        st = st.apply(_env(et, node_id="loop"))
    assert "loop" not in st.completed_nodes


def test_body_node_completed_still_folds_normally():
    st = _state().apply(_env("NodeCompleted", node_id="body"))
    assert "body" in st.completed_nodes
