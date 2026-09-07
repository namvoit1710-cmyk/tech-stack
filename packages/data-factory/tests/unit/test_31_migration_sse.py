"""The migration progress stream, and the promise that its last frame is the ack.

IH is meant to be able to listen to `/events` and consider the job delivered when the
terminal frame arrives. That only holds if three things are true, and each has a test
here: the stream ends, it ends with exactly the body the callback POSTs, and a failure
ends it too - with something IH cannot mistake for success.
"""

import asyncio
import json

import pytest

from app.layer2_application.features.data_migration.df_migration_job import (
    MigrationJob,
    ack_payload,
)
from app.layer3_adapters.controllers.restful.v1 import data_migration_controller as ctl


class _Request:
    """Enough of Request for the generator: it only asks whether we hung up."""

    def __init__(self, drop_after=None):
        self.polls, self.drop_after = 0, drop_after

    async def is_disconnected(self):
        self.polls += 1
        return self.drop_after is not None and self.polls > self.drop_after


def _job(**kw):
    job = MigrationJob(job_id="J1", report_table="DF_REPORT_J1",
                       callback_table="DF_CB_J1")
    job.ack_job_id = "J1"
    for k, v in kw.items():
        setattr(job, k, v)
    return job


def drain(job, request=None, tick=0.0):
    """Collect every frame the stream produces. The tick is patched to 0 so the
    tests do not spend real seconds waiting for a heartbeat."""
    request = request or _Request()
    original = ctl.SSE_TICK_SEC
    ctl.SSE_TICK_SEC = tick

    async def go():
        return [f async for f in ctl.migration_events(job, request)]

    try:
        return asyncio.run(go())
    finally:
        ctl.SSE_TICK_SEC = original


def parse(frame):
    """`id:`/`event:`/`data:` back into something assertable."""
    out = {}
    for line in frame.strip().splitlines():
        key, _, value = line.partition(": ")
        out[key] = value
    out["data"] = json.loads(out["data"])
    return out


# ------------------------------------------------------------------- it terminates

def test_a_finished_job_streams_and_ends():
    frames = drain(_job(status="COMPLETED", rows_read=10, rows_passed=4,
                        rows_written=4, columns=["A", "B"]))
    assert [parse(f)["event"] for f in frames] == ["progress", "done"]


def test_a_late_subscriber_gets_the_state_at_once_not_the_next_change():
    """A job that finished before anyone attached must not hang the listener."""
    job = _job(status="COMPLETED", columns=["A"])
    frames = drain(job)
    assert parse(frames[0])["data"]["status"] == "COMPLETED"


# ------------------------------------------------- the last frame IS the callback

def test_the_terminal_frame_is_exactly_what_the_callback_posts():
    job = _job(status="COMPLETED", rows_passed=4, rows_written=4,
               columns=["MANDT", "MATNR", "IsActive"])
    last = parse(drain(job)[-1])
    assert last["event"] == "done"
    assert last["data"] == ack_payload(job)
    assert last["data"] == {"jobId": "J1", "columns": ["MANDT", "MATNR", "IsActive"],
                            "rows": [], "done": True}


def test_the_terminal_frame_carries_the_columns_ih_sizes_the_target_from():
    """An empty `columns` would create the target without any rule-added column,
    so this is the one field the stream must never ship empty."""
    job = _job(status="COMPLETED", columns=["MANDT", "IsActive"])
    assert parse(drain(job)[-1])["data"]["columns"] == ["MANDT", "IsActive"]


def test_the_callback_job_id_wins_over_the_internal_one():
    job = _job(status="COMPLETED", columns=["A"])
    job.ack_job_id = "IH-SIDE-ID"
    assert parse(drain(job)[-1])["data"]["jobId"] == "IH-SIDE-ID"


# --------------------------------------------------------------- failure is final

def test_a_failed_job_also_ends_the_stream():
    frames = drain(_job(status="FAILED", error="ValueError: unknown column"))
    assert parse(frames[-1])["event"] == "failed"


def test_a_failure_is_never_dressed_up_as_done():
    """`done: true` is IH's signal that the rows are in the table. On a failure they
    are not, so the flag must be false and the reason must travel with it."""
    last = parse(drain(_job(status="FAILED", error="ValueError: unknown column"))[-1])
    assert last["data"]["done"] is False
    assert last["data"]["error"] == "ValueError: unknown column"


# ------------------------------------------------------------------ frame hygiene

def test_frames_are_numbered_so_a_listener_can_tell_them_apart():
    ids = [parse(f)["id"] for f in drain(_job(status="COMPLETED", columns=["A"]))]
    assert ids == ["1", "2"]


def test_a_frame_never_contains_a_bare_newline_inside_its_data():
    """A newline inside `data:` ends the frame early and hands IH half a message."""
    frame = ctl.sse_frame("progress", 1, {"note": "one\ntwo", "list": ["a\nb"]})
    assert frame.endswith("\n\n")
    assert len([l for l in frame.strip().splitlines() if l.startswith("data: ")]) == 1


def test_progress_frames_carry_the_full_job_record():
    job = _job(status="COMPLETED", rows_read=10, rows_passed=4, rows_written=4,
               columns=["A"])
    first = parse(drain(job)[0])["data"]
    for key in ("jobId", "status", "reportTable", "callbackTable", "rowsRead",
                "rowsPassed", "rowsWritten", "progressPct", "violations"):
        assert key in first


# ------------------------------------------------------------- it lets go cleanly

def test_a_disconnected_listener_stops_the_stream():
    """Nobody is reading; the generator must return rather than poll a running job
    for as long as it lives."""
    job = _job(status="WRITING", rows_read=10, rows_passed=4, rows_written=1)
    frames = drain(job, request=_Request(drop_after=1))
    assert all(parse(f)["event"] != "done" for f in frames)


def test_an_unfinished_job_emits_a_heartbeat_rather_than_going_silent():
    """CF closes a quiet connection, and a real run is quiet for ~12s while the
    report table is built."""
    job = _job(status="WRITING", rows_read=10, rows_passed=4, rows_written=1)
    request = _Request(drop_after=4)
    tick, beat = ctl.SSE_TICK_SEC, ctl.SSE_HEARTBEAT_SEC
    ctl.SSE_TICK_SEC, ctl.SSE_HEARTBEAT_SEC = 0.0, 0.0

    async def go():
        return [f async for f in ctl.migration_events(job, request)]

    try:
        frames = asyncio.run(go())
    finally:
        ctl.SSE_TICK_SEC, ctl.SSE_HEARTBEAT_SEC = tick, beat
    assert any(f.startswith(": keepalive") for f in frames)


# ------------------------------------------------------------------ the endpoint

def test_streaming_an_unknown_job_is_a_404():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        asyncio.run(ctl.stream_migration_job("NOPE", _Request()))
    assert caught.value.status_code == 404


def test_the_response_is_an_event_stream_that_proxies_will_not_buffer():
    job = _job(status="COMPLETED", columns=["A"])
    ctl._store.put(job)
    try:
        response = asyncio.run(ctl.stream_migration_job("J1", _Request()))
    finally:
        ctl._store._jobs.pop("J1", None)
    assert response.media_type == "text/event-stream"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["cache-control"] == "no-cache"
