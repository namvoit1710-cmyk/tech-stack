"""The endpoint Integration Hub calls to run a rule migration against a HANA source.

    POST /api/v1/data-migration/execute          <- IH's dispatch, verbatim
    GET  /api/v1/data-migration/jobs/{id}        <- this job's progress
    GET  /api/v1/data-migration/jobs/{id}/events <- the same progress, as it happens

`execute` answers 202 straight away and runs the work in the background: a real run
against MARA takes about a minute, and the caller cannot hold a request open for that.
Progress is polled here, streamed from `/events`, or read directly from the delivery
table's catalog comment.

`/events` ends with the same message the callback POSTs, so a listener that waits for
the terminal frame needs nothing else. It does not replace the POST: a stream is a live
connection and CF will close one that goes quiet, so the POST stays as the delivery that
survives a dropped subscriber, and `GET /jobs/{id}` stays the answer to "did it finish?".
"""
import asyncio
import json
import time

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.layer2_application.features.data_migration.df_migration_job import (
    DispatchDto,
    MigrationJob,
    MigrationJobStore,
    ack_payload,
    callback_table_name,
    report_table_name,
    run_migration_job,
)

#: How often the stream looks for movement, and how long it will stay silent before
#: sending a comment line. The silence is the reason the heartbeat exists: a real run
#: spends ~12 s building the report table with nothing new to say, and an idle
#: connection is exactly what CF and the ALB close.
SSE_TICK_SEC = 0.5
SSE_HEARTBEAT_SEC = 10.0
TERMINAL = ("COMPLETED", "FAILED")

router = APIRouter(tags=["Data Migration"])

# One store per process, like IH's. A restart loses job status; the tables it wrote
# survive, which is the reason the results go to the database rather than to memory.
_store = MigrationJobStore()


@router.post("/execute", status_code=202)
def execute_migration(dispatch: DispatchDto, background: BackgroundTasks) -> dict:
    """Accept the dispatch, answer immediately, do the work in the background."""
    existing = _store.get(dispatch.job_id)
    if existing is not None:
        # Idempotent on job_id. The resolve-token is single-use, so re-running a
        # retried dispatch would strand the job on a 403 instead of repeating it.
        return existing.as_dict()

    job = MigrationJob(
        job_id=dispatch.job_id,
        report_table=report_table_name(dispatch.job_id),
        callback_table=callback_table_name(dispatch.job_id),
    )
    _store.put(job)
    background.add_task(run_migration_job, dispatch, job)
    return job.as_dict()


@router.get("/jobs/{job_id}")
def get_migration_job(job_id: str) -> dict:
    job = _store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job_id: {job_id}")
    return job.as_dict()


def sse_frame(event: str, seq: int, payload: dict) -> str:
    """One SSE frame. `separators` matters: a raw newline inside `data:` would end
    the frame early and hand the listener half a message."""
    body = json.dumps(payload, separators=(",", ":"))
    return f"id: {seq}\nevent: {event}\ndata: {body}\n\n"


def moved(snapshot: dict) -> tuple:
    """What counts as news. Everything else on the record is derived from these."""
    return (snapshot["status"], snapshot["rowsRead"],
            snapshot["rowsPassed"], snapshot["rowsWritten"])


async def migration_events(job: MigrationJob, request: Request):
    """Frames until the job reaches a terminal state, then one final frame.

    A subscriber that arrives late is not punished: the first frame is the current
    state, sent immediately, not the next change. That also means a listener attaching
    to an already-finished job gets the terminal frame at once rather than hanging.
    """
    seq, last = 0, None
    # Wall time, not a count of ticks: under load the loop wakes late, and a heartbeat
    # measured in nominal ticks would drift past the timeout it exists to beat.
    spoke_at = time.monotonic()
    while True:
        if await request.is_disconnected():
            return

        snapshot = job.as_dict()
        if moved(snapshot) != last:
            last, spoke_at = moved(snapshot), time.monotonic()
            seq += 1
            yield sse_frame("progress", seq, snapshot)

        if snapshot["status"] in TERMINAL:
            seq += 1
            if snapshot["status"] == "COMPLETED":
                # Identical to the body POSTed to callback.url - see ack_payload().
                yield sse_frame("done", seq, ack_payload(job))
            else:
                # Terminal too, but `done` would tell IH the data arrived. It did not.
                yield sse_frame("failed", seq, {
                    "jobId": job.ack_job_id or job.job_id,
                    "error": snapshot["error"],
                    "done": False,
                })
            return

        await asyncio.sleep(SSE_TICK_SEC)
        if time.monotonic() - spoke_at >= SSE_HEARTBEAT_SEC:
            spoke_at = time.monotonic()
            yield ": keepalive\n\n"


@router.get("/jobs/{job_id}/events")
async def stream_migration_job(job_id: str, request: Request) -> StreamingResponse:
    """Progress as it happens, ending with the callback message.

    Subscribe *after* dispatch: the job must exist to be streamed, and a 404 here means
    this instance has never seen that job_id - which is also what you get if the
    dispatch landed on a different instance, since the job store is per-process.
    """
    job = _store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job_id: {job_id}")
    return StreamingResponse(
        migration_events(job, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx/CF buffer streamed bodies by default, which would hold every frame
            # back until the job finished and defeat the entire point.
            "X-Accel-Buffering": "no",
        },
    )
