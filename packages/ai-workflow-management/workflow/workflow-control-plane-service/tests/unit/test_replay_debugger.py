"""SA-1372 Replay Debugger — unit tests (D6).

Exercises the read-side replay: open a session over a seeded run's event log,
step/run_to a cursor, fold the prefix through the real pure reducer, and assert
the inspector redacts secrets and never mutates the log.

Run:  py -3.13 -m pytest tests/unit/test_replay_debugger.py -v
Authority: 65537.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.event_log import EventLogEnvelope
from app.layer2_application.features.replay_debugger.use_cases.replay_debugger_use_case import (
    ReplayDebuggerUseCase,
    _REDACTED,
)
from app.layer2_application.projection.rebuild import rebuild_run_state
from app.layer4_frameworks.persistence.memory.memory_event_log_repo import (
    MemoryEventLogRepository,
)

pytestmark = pytest.mark.unit

RUN = "run-1"


def _seed() -> MemoryEventLogRepository:
    """A tiny recorded run: start -> n1 (input, output w/ secrets, completed) -> done."""
    repo = MemoryEventLogRepository()
    events = [
        ("RunStatusChanged", {"status": "started", "run_id": RUN, "workflow_id": "wf-1", "input_data": {}}),
        ("NodeInput", {"node_id": "n1", "resolved_input": {"x": 1, "password": "hunter2"}}),
        ("NodeOutputProduced", {
            "node_id": "n1",
            "state_updates": {"score": 42, "api_key": "sk-live-xyz"},
            "output_port": "success",
            "taken_edge_ports": {"e1": "success"},
            "__credentials": {"token": "secret-token"},
        }),
        ("NodeCompleted", {"node_id": "n1"}),
        ("RunStatusChanged", {"status": "completed", "output_data": {"score": 42}}),
    ]
    for i, (etype, payload) in enumerate(events, start=1):
        repo.append(EventLogEnvelope(
            event_id=f"e{i}", run_id=RUN, topic="t", event_type=etype,
            timestamp=f"2026-07-01T00:00:0{i}+00:00", payload=payload,
        ))
    return repo


def _uc(repo):
    return ReplayDebuggerUseCase(logger=None, event_log_repo=repo)


def test_open_lists_timeline():
    uc = _uc(_seed())
    res = uc.open(RUN)
    assert res.error is None
    assert res.replay_session_id
    assert res.total_steps == 5 and res.cursor == 0
    assert [t["event_type"] for t in res.timeline] == [
        "RunStatusChanged", "NodeInput", "NodeOutputProduced", "NodeCompleted", "RunStatusChanged",
    ]
    assert res.timeline[0]["node_id"] is None and res.timeline[1]["node_id"] == "n1"


def test_step_to_end_folds_projection():
    repo = _seed()
    uc = _uc(repo)
    sid = uc.open(RUN).replay_session_id
    st = uc.step(sid, op="index", index=5)
    assert st.cursor == 5 and st.replay_complete is True
    # parity with the canonical repo-driven rebuild through the same pure reducer
    assert st.completed_nodes == sorted(rebuild_run_state(RUN, repo).completed_nodes) == ["n1"]
    assert st.scope_chain["score"] == 42


def test_run_to_node_lands_on_node():
    uc = _uc(_seed())
    sid = uc.open(RUN).replay_session_id
    st = uc.step(sid, op="run_to", node_id="n1")
    assert st.node_id == "n1" and st.cursor == 2  # n1's first event (NodeInput)


def test_redaction_scope_io_and_event():
    uc = _uc(_seed())
    sid = uc.open(RUN).replay_session_id
    # at the output event, secrets in state_updates + __credentials + scope are hidden
    out = uc.step(sid, op="index", index=3)
    assert out.node_io["state_updates"]["api_key"] == _REDACTED
    assert out.event["payload"]["__credentials"] == _REDACTED
    assert out.scope_chain["api_key"] == _REDACTED
    assert "api_key" in out.redacted_keys and "__credentials" in out.redacted_keys
    # secrets in resolved_input are hidden too
    inp = uc.step(sid, op="index", index=2)
    assert inp.node_io["input"]["password"] == _REDACTED


def test_non_mutating():
    repo = _seed()
    before = len(repo.list_by_run(RUN))
    uc = _uc(repo)
    sid = uc.open(RUN).replay_session_id
    uc.step(sid, op="index", index=5)
    uc.step(sid, op="step", n=-2)
    uc.state(sid)
    uc.close(sid)
    assert len(repo.list_by_run(RUN)) == before == 5


def test_run_not_found():
    uc = _uc(MemoryEventLogRepository())
    res = uc.open("does-not-exist")
    assert res.error and res.error_code == "RUN_NOT_FOUND"


def test_session_not_found():
    uc = _uc(_seed())
    assert uc.state("bogus").error
    assert uc.step("bogus").error
    assert uc.close("bogus").error


def test_close_drops_session():
    uc = _uc(_seed())
    sid = uc.open(RUN).replay_session_id
    assert uc.close(sid).closed is True
    assert uc.state(sid).error  # gone


def _seed_failing() -> MemoryEventLogRepository:
    """A run where n1 fails (n8n-style: surface status + error at the cursor)."""
    repo = MemoryEventLogRepository()
    events = [
        ("RunStatusChanged", {"status": "started", "run_id": RUN, "workflow_id": "wf-1", "input_data": {}}),
        ("NodeInput", {"node_id": "n1", "resolved_input": {"x": 1}}),
        ("NodeFailed", {"node_id": "n1", "error_code": "PY_RUNNER_MISSING", "error_message": "Python runner unavailable: Python 3 is missing"}),
        ("RunStatusChanged", {"status": "failed", "error": "node n1 failed"}),
    ]
    for i, (etype, payload) in enumerate(events, start=1):
        repo.append(EventLogEnvelope(
            event_id=f"f{i}", run_id=RUN, topic="t", event_type=etype,
            timestamp=f"2026-07-02T00:00:0{i}+00:00", payload=payload,
        ))
    return repo


def test_node_status_and_error():
    """Canvas overlay + inline error: at the failure cursor, n1 is 'failed' and
    the error message is surfaced (like n8n's red node + toast)."""
    uc = _uc(_seed_failing())
    sid = uc.open(RUN).replay_session_id
    st = uc.step(sid, op="index", index=3)  # after NodeFailed
    assert st.node_id == "n1"
    assert st.node_status["n1"] == "failed"
    assert st.node_error and st.node_error["error_code"] == "PY_RUNNER_MISSING"
    assert "Python runner unavailable" in st.node_error["error_message"]


def test_running_status_before_terminal():
    """A dispatched-but-not-terminal node reads 'running' at an earlier cursor."""
    uc = _uc(_seed_failing())
    sid = uc.open(RUN).replay_session_id
    st = uc.step(sid, op="index", index=2)  # after NodeInput, before NodeFailed
    assert st.node_status["n1"] == "running"
    assert st.node_error is None


def test_timeline_marks_error_events():
    open_res = _uc(_seed_failing()).open(RUN)
    failed = [t for t in open_res.timeline if t["event_type"] == "NodeFailed"]
    assert failed and failed[0]["is_error"] is True
    started = [t for t in open_res.timeline if t["event_type"] == "RunStatusChanged"]
    assert all(t["is_error"] is False for t in started)


# ── Paged timeline + incremental fold (server-side paging for huge runs) ──

def _seed_long(n_nodes: int = 125) -> MemoryEventLogRepository:
    """A long run: n_nodes × (NodeInput, NodeCompleted) = 2·n_nodes events."""
    repo = MemoryEventLogRepository()
    seq = 0
    for k in range(1, n_nodes + 1):
        for etype, payload in (
            ("NodeInput", {"node_id": f"n{k}", "resolved_input": {"k": k}}),
            ("NodeCompleted", {"node_id": f"n{k}"}),
        ):
            seq += 1
            repo.append(EventLogEnvelope(
                event_id=f"L{seq}", run_id=RUN, topic="t", event_type=etype,
                timestamp="2026-07-03T00:00:00+00:00", payload=payload,
            ))
    return repo


def test_open_returns_first_page_only():
    """open() must never ship the whole log — first page + true total."""
    uc = _uc(_seed_long())  # 250 events
    res = uc.open(RUN)
    assert res.total_steps == 250
    assert len(res.timeline) == 100  # default page size
    assert res.timeline[0]["index"] == 1 and res.timeline[-1]["index"] == 100
    small = uc.open(RUN, timeline_limit=10)
    assert small.total_steps == 250 and len(small.timeline) == 10


def test_timeline_pages_with_continuing_index():
    uc = _uc(_seed_long())
    sid = uc.open(RUN).replay_session_id
    page2 = uc.timeline(sid, offset=100, limit=100)
    assert page2.error is None
    assert page2.total == 250 and len(page2.items) == 100
    assert page2.items[0]["index"] == 101 and page2.items[-1]["index"] == 200
    last = uc.timeline(sid, offset=200, limit=100)
    assert len(last.items) == 50 and last.items[-1]["index"] == 250
    beyond = uc.timeline(sid, offset=999, limit=100)
    assert beyond.items == [] and beyond.total == 250
    assert uc.timeline("bogus").error


def test_incremental_fold_matches_fresh_fold():
    """Forward delta-fold and backward refold must equal a fresh session's
    state at the same cursor (the cache is an optimisation, not a semantic)."""
    repo = _seed_long()
    uc = _uc(repo)
    sid = uc.open(RUN).replay_session_id
    uc._FOLD_BATCH = 7  # force many batches across fetches
    uc.step(sid, op="index", index=200)
    back = uc.step(sid, op="index", index=120)  # backward → refold from 0

    fresh_uc = _uc(repo)
    fresh_sid = fresh_uc.open(RUN).replay_session_id
    fresh = fresh_uc.step(fresh_sid, op="index", index=120)

    assert back.cursor == fresh.cursor == 120
    assert back.node_id == fresh.node_id
    assert back.scope_chain == fresh.scope_chain
    assert back.completed_nodes == fresh.completed_nodes
    assert back.node_status == fresh.node_status


def test_run_to_scans_across_batches():
    uc = _uc(_seed_long())
    sid = uc.open(RUN).replay_session_id
    uc._FOLD_BATCH = 7
    st = uc.step(sid, op="run_to", node_id="n100")
    assert st.node_id == "n100" and st.cursor == 199  # n100's NodeInput
    missing = uc.step(sid, op="run_to", node_id="n999")
    assert missing.cursor == 250 and missing.replay_complete is True


def test_session_memory_holds_no_envelopes():
    """The regression this feature exists for: sessions must not retain the log."""
    uc = _uc(_seed_long())
    sid = uc.open(RUN).replay_session_id
    assert "envelopes" not in uc._sessions[sid]
