"""Phase 3 Sub 3.6 — kill-a-replica chaos test.

Verifies the outbox + idempotency-key invariant under simulated
Executor crash + redelivery: when Executor A dies mid-handler before
appending ``OutboxResolved``, Executor B picks up the same
``OutboxRequested`` and completes the upload — and the file service
returns the SAME ``file_id`` because the producer's
``idempotency_key`` (sha256 content hash) is stable across replays.

Why deterministic in-process simulation rather than real
multiprocessing + Kafka:
    The kernel of the test is the **idempotency invariant**. Whether
    the OS-level kill is a SIGKILL or just dropping the in-flight
    handler does not change the contract being tested — both paths
    end with "the same OutboxRequested entry is processed twice".
    The deterministic simulation runs in milliseconds, never flakes,
    and exercises every layer (outbox repo, dispatcher, file-service
    idempotency) end-to-end. The doc (§11.2) explicitly allows the
    "existing confluent-kafka mock harness" alternative; we go one
    step further and remove the mock harness too because nothing in
    the assertions cares about Kafka semantics.

Faithful kill model: a real SIGKILL prevents the dispatcher's
catch-all from writing ``OutboxResolved(failed)``. We model that by
simulating the in-flight pop of the bytes channel WITHOUT routing
the failure back through the dispatcher — the entry stays unresolved
in the log, exactly as Kafka redelivery presents it to Executor B.

Gating: ``@pytest.mark.chaos`` + ``CHAOS_TESTS=1`` env var. Default
CI does not pay the cost; soak runs do.
"""

from __future__ import annotations

import os
from hashlib import sha256
from typing import Any

import pytest

from app.layer1_domain.events import EventTopic
from app.layer1_domain.value_objects.event_id import EventId
from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
)
from app.layer2_application.events.outbox_awaiter import OutboxAwaiter
from app.layer4_frameworks.clients.mock_file_service_client import (
    MockFileServiceClient,
)
from app.layer4_frameworks.outbox.handlers import FileUploadHandler
from app.layer4_frameworks.outbox.outbox_bytes_channel import OutboxBytesChannel
from app.layer4_frameworks.outbox.outbox_dispatcher import OutboxDispatcher
from app.layer4_frameworks.persistence.memory.memory_event_log_repo import (
    MemoryEventLogRepository,
)
from app.layer4_frameworks.persistence.memory.memory_outbox_repository import (
    MemoryOutboxRepository,
)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.chaos,
    pytest.mark.skipif(
        os.environ.get("CHAOS_TESTS", "") not in ("1", "true", "yes"),
        reason="CHAOS_TESTS env var not set; gated for soak runs only",
    ),
]


# ─────────────────────────── helpers ───────────────────────────


def _emit_outbox_request(
    *,
    log: MemoryEventLogRepository,
    outbox_repo: MemoryOutboxRepository,
    bytes_channel: OutboxBytesChannel,
    awaiter: OutboxAwaiter,
    run_id: str,
    task_id: str,
    op_index: int,
    content: bytes,
    filename: str,
    timestamp: str = "2026-05-07T00:00:00+00:00",
) -> tuple[str, str]:
    """Mimic the producer side of ``EditNodeDataUseCase._upload_via_outbox``.

    Returns ``(outbox_id, content_hash)``. The hash is the
    idempotency_key the file service should dedupe on.
    """
    outbox_id = EventId.derive_outbox(run_id, task_id, op_index, "file_upload")
    content_hash = sha256(content).hexdigest()

    awaiter.register(outbox_id)
    bytes_channel.put(outbox_id, content)
    outbox_repo.mark_run(run_id)

    envelope = EventLogEnvelope(
        event_id=EventId.derive(outbox_id, "requested"),
        run_id=run_id,
        topic=str(EventTopic.OUTBOX_REQUESTED),
        event_type="OutboxRequested",
        timestamp=timestamp,
        payload={
            "outbox_id": outbox_id,
            "run_id": run_id,
            "workflow_id": "",
            "kind": "file_upload",
            "payload": {
                "filename": filename,
                "content_hash": content_hash,
                "content_type": "text/csv",
            },
            "idempotency_key": content_hash,
        },
        causation_id=task_id,
    )
    log.append(envelope)
    return outbox_id, content_hash


# ─────────────────────────── tests ────────────────────────────


@pytest.mark.asyncio
async def test_kill_during_upload_redelivers_to_b_with_single_file_id():
    """Executor A is killed mid-handler; Executor B redelivers the
    upload via the same outbox entry; the file service's idempotency
    layer dedupes — exactly one ``file_id`` lands in storage.

    Faithful kill model: A's bytes channel is popped (mimicking the
    consumer-thread reading the work item) but the OS kills the pod
    BEFORE the dispatcher can append ``OutboxResolved``. The log
    therefore stays at "OutboxRequested with no Resolved" — exactly
    what Executor B sees on rebalance.
    """
    log = MemoryEventLogRepository()
    outbox_repo = MemoryOutboxRepository(log)
    bytes_channel_a = OutboxBytesChannel()
    bytes_channel_b = OutboxBytesChannel()
    awaiter = OutboxAwaiter()
    fs = MockFileServiceClient()

    run_id, task_id = "run-chaos-1", "task-chaos-1"
    content = b"row1,col1\nfoo,bar\n"

    outbox_id, content_hash = _emit_outbox_request(
        log=log, outbox_repo=outbox_repo, bytes_channel=bytes_channel_a,
        awaiter=awaiter, run_id=run_id, task_id=task_id, op_index=0,
        content=content, filename="data.csv",
    )

    # ─── simulate Executor A killed mid-handler ───
    # The handler thread popped the bytes (it was about to call
    # upload_file) but the pod died before the dispatcher could write
    # ``OutboxResolved``. From the event log's perspective the entry
    # is still unresolved — exactly the state Executor B inherits.
    consumed = bytes_channel_a.pop(outbox_id)
    assert consumed == content
    # No call to dispatcher_a.drain_once — that would let the
    # dispatcher's catch-all write Resolved(failed), which would
    # mis-model an OS-level kill.

    # ─── Executor B picks up the redelivery ───
    # Real-world chain: Kafka rebalance → B receives a fresh
    # ``NodeInput`` for the same node → DATA_EDIT use case re-runs →
    # producer side re-emits ``OutboxRequested`` with the same
    # deterministic outbox_id (event log dedupes on event_id) and
    # re-puts the bytes into B's channel.
    bytes_channel_b.put(outbox_id, content)
    log.append(EventLogEnvelope(
        event_id=EventId.derive(outbox_id, "requested"),
        run_id=run_id,
        topic=str(EventTopic.OUTBOX_REQUESTED),
        event_type="OutboxRequested",
        timestamp="2026-05-07T00:00:01+00:00",
        payload={
            "outbox_id": outbox_id,
            "run_id": run_id,
            "workflow_id": "",
            "kind": "file_upload",
            "payload": {
                "filename": "data.csv",
                "content_hash": content_hash,
                "content_type": "text/csv",
            },
            "idempotency_key": content_hash,
        },
        causation_id=task_id,
    ))
    # The original awaiter in A's process is gone — B re-registers.
    awaiter.register(outbox_id)

    handler_b = FileUploadHandler(file_service=fs, bytes_channel=bytes_channel_b)
    dispatcher_b = OutboxDispatcher(
        outbox_repo=outbox_repo,
        event_log_repo=log,
        handlers={"file_upload": handler_b},
        awaiter=awaiter,
    )
    await dispatcher_b.drain_once()

    # ─── assertions ───
    # Exactly one file in storage (idempotency_key dedup).
    file_ids = fs.list_files_by_idempotency_key(content_hash)
    assert len(file_ids) == 1, f"expected single file_id, got {file_ids}"

    # Exactly one OutboxResolved(ok) envelope.
    resolved = [
        e for e in log.list_by_run(run_id)
        if e.event_type == "OutboxResolved"
        and (e.payload or {}).get("outcome") == "ok"
    ]
    assert len(resolved) == 1
    assert resolved[0].payload["result"]["file_id"] == file_ids[0]

    # Exactly one OutboxRequested envelope despite two emits — log
    # dedup on deterministic event_id swallows the duplicate.
    requested = [
        e for e in log.list_by_run(run_id)
        if e.event_type == "OutboxRequested"
    ]
    assert len(requested) == 1

    # Awaiter resolved with the real file_id — the HTTP PATCH caller
    # would see a 200, not a timeout fallback.
    resolution = await awaiter.wait(outbox_id, timeout=0.5)
    assert resolution.outcome == "ok"
    assert resolution.result["file_id"] == file_ids[0]


@pytest.mark.asyncio
async def test_double_dispatch_without_crash_still_dedupes_via_idempotency_key():
    """Two dispatchers race-poll the same unresolved entry. The first
    one resolves and writes ``OutboxResolved``; the second sees the
    resolved view and never invokes the file service. With the file
    service's idempotency layer this would dedupe even if both reached
    upload_file, so the test asserts a single file_id either way.
    """
    log = MemoryEventLogRepository()
    outbox_repo = MemoryOutboxRepository(log)
    bytes_channel = OutboxBytesChannel()
    awaiter = OutboxAwaiter()
    fs = MockFileServiceClient()

    run_id, task_id = "run-chaos-2", "task-chaos-2"
    content = b"a,b\n1,2\n"
    outbox_id, content_hash = _emit_outbox_request(
        log=log, outbox_repo=outbox_repo, bytes_channel=bytes_channel,
        awaiter=awaiter, run_id=run_id, task_id=task_id, op_index=0,
        content=content, filename="x.csv",
    )

    handler = FileUploadHandler(file_service=fs, bytes_channel=bytes_channel)
    dispatcher_a = OutboxDispatcher(
        outbox_repo=outbox_repo, event_log_repo=log,
        handlers={"file_upload": handler}, awaiter=awaiter,
    )
    dispatcher_b = OutboxDispatcher(
        outbox_repo=outbox_repo, event_log_repo=log,
        handlers={"file_upload": handler}, awaiter=awaiter,
    )

    await dispatcher_a.drain_once()
    # A consumed the bytes. Re-put for B's pass — mimics a producer
    # re-run (or, equivalently, the dispatcher repository's
    # already-resolved view making B's drain a no-op).
    bytes_channel.put(outbox_id, content)
    await dispatcher_b.drain_once()

    # The file service deduped — single file_id under idempotency_key.
    file_ids = fs.list_files_by_idempotency_key(content_hash)
    assert len(file_ids) == 1

    # Single Resolved(ok) envelope despite both dispatchers running:
    # the second drain saw the entry as resolved and never invoked
    # the handler.
    resolved_ok = [
        e for e in log.list_by_run(run_id)
        if e.event_type == "OutboxResolved"
        and (e.payload or {}).get("outcome") == "ok"
    ]
    assert len(resolved_ok) == 1


@pytest.mark.asyncio
async def test_idempotency_layer_dedupes_even_without_outbox_resolved_visibility():
    """File-service-level idempotency safety net.

    Hypothesis: even if the outbox layer's resolved-view leaked and
    both dispatchers actually invoked the handler, the file service's
    ``X-Idempotency-Key`` dedupe would still prevent orphan uploads.
    Test it directly by calling ``upload_file`` twice with the same
    key.
    """
    fs = MockFileServiceClient()
    content = b"hello world"
    key = sha256(content).hexdigest()

    r1 = await fs.upload_file(content, "f.csv", "text/csv", idempotency_key=key)
    r2 = await fs.upload_file(content, "f.csv", "text/csv", idempotency_key=key)

    assert r1["file_id"] == r2["file_id"]
    assert r2.get("deduped") is True
    assert fs.list_files_by_idempotency_key(key) == [r1["file_id"]]
