"""SA-1371 S1 — durable-timer projection fold + Timer entity.

The projector tracks ``pending_timers`` (read-side view): TimerScheduled adds
one; TimerFired / TimerCancelled retire it. Folds are idempotent under replay.
"""

from __future__ import annotations

from app.layer1_domain.entities.timer import Timer
from app.layer1_domain.run_projection import ProjectedRunState
from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
)
from app.layer4_frameworks.persistence.memory.memory_event_log_repo import (
    MemoryEventLogRepository,
)
from app.layer2_application.projection import rebuild_run_state


_seq = 0


def _env(event_type: str, run_id: str = "r1", **payload) -> EventLogEnvelope:
    global _seq
    _seq += 1
    return EventLogEnvelope(
        event_id=f"{event_type}-{run_id}-{_seq}",
        run_id=run_id,
        topic=event_type.lower(),
        event_type=event_type,
        timestamp="2026-07-01T00:00:00Z",
        payload={**payload, "run_id": run_id},
    )


class TestTimerEntity:
    def test_is_due_string_compare(self):
        t = Timer(timer_id="t1", run_id="r1", fire_at="2026-07-01T00:00:00Z")
        assert t.is_due("2026-07-01T00:00:00Z") is True
        assert t.is_due("2026-07-01T00:00:01Z") is True
        assert t.is_due("2026-06-30T23:59:59Z") is False

    def test_no_fire_at_never_due(self):
        assert Timer(timer_id="t1", run_id="r1").is_due("2026-07-01T00:00:00Z") is False


class TestTimerProjection:
    def test_scheduled_adds_pending_timer(self):
        state = ProjectedRunState(run_id="r1").apply(
            _env("TimerScheduled", timer_id="t1", node_id="n1", kind="delay",
                 fire_at="2026-07-01T00:05:00Z"))
        assert state.pending_timers == {
            "t1": {"node_id": "n1", "kind": "delay", "fire_at": "2026-07-01T00:05:00Z"}
        }

    def test_fired_retires_timer(self):
        state = ProjectedRunState(run_id="r1")
        state = state.apply(_env("TimerScheduled", timer_id="t1", node_id="n1",
                                 fire_at="2026-07-01T00:05:00Z"))
        state = state.apply(_env("TimerFired", timer_id="t1", node_id="n1"))
        assert state.pending_timers == {}

    def test_cancelled_retires_timer(self):
        state = ProjectedRunState(run_id="r1")
        state = state.apply(_env("TimerScheduled", timer_id="t1", node_id="n1"))
        state = state.apply(_env("TimerCancelled", timer_id="t1", reason="node_skipped"))
        assert state.pending_timers == {}

    def test_fire_without_id_or_unknown_is_noop(self):
        state = ProjectedRunState(run_id="r1").apply(
            _env("TimerScheduled", timer_id="t1", node_id="n1"))
        # Unknown timer fire → no-op (idempotent retire).
        after = state.apply(_env("TimerFired", timer_id="ghost"))
        assert after.pending_timers == {"t1": {"node_id": "n1", "kind": "delay", "fire_at": ""}}

    def test_replay_idempotent(self):
        e = _env("TimerScheduled", timer_id="t1", node_id="n1",
                 fire_at="2026-07-01T00:05:00Z")
        s1 = ProjectedRunState(run_id="r1").apply(e)
        s2 = s1.apply(e)
        assert s1.pending_timers == s2.pending_timers

    def test_multiple_timers_tracked_independently(self):
        repo = MemoryEventLogRepository()
        repo.append(_env("TimerScheduled", timer_id="t1", node_id="n1"))
        repo.append(_env("TimerScheduled", timer_id="t2", node_id="n2"))
        repo.append(_env("TimerFired", timer_id="t1", node_id="n1"))
        state = rebuild_run_state("r1", repo)
        assert set(state.pending_timers) == {"t2"}

    def test_codec_round_trip(self):
        state = ProjectedRunState(
            run_id="r1",
            pending_timers={"t1": {"node_id": "n1", "kind": "sla", "fire_at": "x"}},
        )
        decoded = ProjectedRunState.from_json(state.to_json())
        assert decoded.pending_timers == state.pending_timers

    def test_pre_sa1371_snapshot_defaults_empty(self):
        legacy = (
            '{"run_id":"r1","workflow_id":"wf","status":"RUNNING","started_at":"",'
            '"completed_at":null,"error":null,"output_data":null,"completed_nodes":[],'
            '"skipped_nodes":[],"child_runs":{},"variables":{},"input_data":{},'
            '"pending_outbox":{},"failed_outbox":{}}'
        )
        assert ProjectedRunState.from_json(legacy).pending_timers == {}

    def test_schema_version_bumped_to_3(self):
        assert ProjectedRunState.SCHEMA_VERSION == 3
