"""Phase 3 Sub 3.2 — OutboxDispatcher loop semantics.

Drives the dispatcher with stub handlers and a memory event log /
outbox repo, asserting:
  - Successful handler perform → ``OutboxResolved(outcome="ok")``
    appended with the handler's result and idempotency clean-up.
  - ``RetryableError`` → attempt counter increments; eventually
    converts to ``failed`` after ``max_attempts``.
  - ``PermanentError`` → immediate ``failed`` resolution.
  - Unknown kind → immediate ``failed`` resolution.
  - Empty handler perform → ``ok`` with empty result.
  - Replays: a redelivered ``OutboxRequested`` does not double-resolve
    (resolved ids are filtered by the repo's anti-join).
"""

from __future__ import annotations

import pytest

from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
)
from app.layer2_application.interfaces.outbox_repository_interface import (
    OutboxEntry,
)
from app.layer4_frameworks.outbox.outbox_dispatcher import OutboxDispatcher
from app.layer4_frameworks.outbox.outbox_errors import (
    PermanentError,
    RetryableError,
)
from app.layer4_frameworks.persistence.memory.memory_event_log_repo import (
    MemoryEventLogRepository,
)
from app.layer4_frameworks.persistence.memory.memory_outbox_repository import (
    MemoryOutboxRepository,
)


# ────────────────────────── stub handlers ──────────────────────────


class _OkHandler:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def perform(self, entry):
        self.calls += 1
        return self.result


class _RaisingHandler:
    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    async def perform(self, entry):
        self.calls += 1
        raise self.exc


# ─────────────────────────── helpers ───────────────────────────────


def _seed_request(
    log: MemoryEventLogRepository,
    *,
    run_id: str,
    outbox_id: str,
    kind: str,
    payload: dict | None = None,
    event_id: str | None = None,
) -> None:
    log.append(EventLogEnvelope(
        event_id=event_id or f"req-{outbox_id}",
        run_id=run_id,
        topic="outbox.requested",
        event_type="OutboxRequested",
        timestamp="2026-04-25T00:00:00Z",
        payload={
            "outbox_id": outbox_id,
            "run_id": run_id,
            "workflow_id": "wf_1",
            "kind": kind,
            "payload": payload or {},
            "idempotency_key": "k_" + outbox_id,
        },
    ))


def _build():
    log = MemoryEventLogRepository()
    repo = MemoryOutboxRepository(log)
    return log, repo


def _resolved_envelopes(log: MemoryEventLogRepository, run_id: str):
    return [
        e for e in log.list_by_run(run_id)
        if e.event_type == "OutboxResolved"
    ]


# ────────────────────────────── tests ───────────────────────────────


@pytest.mark.asyncio
class TestDrainOnce:
    async def test_no_entries_returns_zero(self):
        log, repo = _build()
        d = OutboxDispatcher(repo, log, handlers={"file_upload": _OkHandler({})})
        assert await d.drain_once() == 0

    async def test_ok_handler_emits_resolved_ok(self):
        log, repo = _build()
        repo.mark_run("r_1")
        _seed_request(log, run_id="r_1", outbox_id="ob_1", kind="file_upload")
        handler = _OkHandler({"file_id": "f_xyz"})
        d = OutboxDispatcher(repo, log, handlers={"file_upload": handler})

        retired = await d.drain_once()

        assert retired == 1
        assert handler.calls == 1
        resolved = _resolved_envelopes(log, "r_1")
        assert len(resolved) == 1
        payload = resolved[0].payload
        assert payload["outbox_id"] == "ob_1"
        assert payload["outcome"] == "ok"
        assert payload["result"] == {"file_id": "f_xyz"}

    async def test_unknown_kind_resolves_failed(self):
        log, repo = _build()
        repo.mark_run("r_1")
        _seed_request(log, run_id="r_1", outbox_id="ob_1", kind="unknown")
        d = OutboxDispatcher(repo, log, handlers={})

        await d.drain_once()

        resolved = _resolved_envelopes(log, "r_1")
        assert resolved[0].payload["outcome"] == "failed"
        assert "no handler" in resolved[0].payload["error"]

    async def test_permanent_error_resolves_failed_immediately(self):
        log, repo = _build()
        repo.mark_run("r_1")
        _seed_request(log, run_id="r_1", outbox_id="ob_1", kind="file_upload")
        handler = _RaisingHandler(PermanentError("bad payload"))
        d = OutboxDispatcher(repo, log, handlers={"file_upload": handler})

        await d.drain_once()

        assert handler.calls == 1
        resolved = _resolved_envelopes(log, "r_1")
        assert resolved[0].payload["outcome"] == "failed"
        assert "bad payload" in resolved[0].payload["error"]

    async def test_retryable_error_records_attempt_and_does_not_resolve(self):
        log, repo = _build()
        repo.mark_run("r_1")
        _seed_request(log, run_id="r_1", outbox_id="ob_1", kind="file_upload")
        handler = _RaisingHandler(RetryableError("transient"))
        d = OutboxDispatcher(
            repo, log, handlers={"file_upload": handler}, max_attempts=3,
        )

        retired = await d.drain_once()

        assert retired == 0
        assert _resolved_envelopes(log, "r_1") == []
        assert repo.get_attempts("ob_1") == 1

    async def test_retryable_exhausted_resolves_failed(self):
        log, repo = _build()
        repo.mark_run("r_1")
        _seed_request(log, run_id="r_1", outbox_id="ob_1", kind="file_upload")
        handler = _RaisingHandler(RetryableError("transient"))
        d = OutboxDispatcher(
            repo, log, handlers={"file_upload": handler}, max_attempts=2,
        )
        # First attempt → counter=1, no resolve.
        await d.drain_once()
        assert _resolved_envelopes(log, "r_1") == []
        # Second attempt → counter hits max_attempts → resolve failed.
        await d.drain_once()
        resolved = _resolved_envelopes(log, "r_1")
        assert len(resolved) == 1
        assert resolved[0].payload["outcome"] == "failed"
        assert "max attempts" in resolved[0].payload["error"]

    async def test_unclassified_exception_resolves_failed(self):
        log, repo = _build()
        repo.mark_run("r_1")
        _seed_request(log, run_id="r_1", outbox_id="ob_1", kind="file_upload")
        handler = _RaisingHandler(RuntimeError("oops"))
        d = OutboxDispatcher(repo, log, handlers={"file_upload": handler})

        await d.drain_once()

        resolved = _resolved_envelopes(log, "r_1")
        assert resolved[0].payload["outcome"] == "failed"
        assert "oops" in resolved[0].payload["error"]

    async def test_handler_returns_empty_ok(self):
        log, repo = _build()
        repo.mark_run("r_1")
        _seed_request(log, run_id="r_1", outbox_id="ob_1", kind="file_delete")
        d = OutboxDispatcher(
            repo, log, handlers={"file_delete": _OkHandler({})},
        )

        await d.drain_once()

        resolved = _resolved_envelopes(log, "r_1")
        assert resolved[0].payload["outcome"] == "ok"
        assert resolved[0].payload["result"] == {}

    async def test_resolves_multiple_in_one_drain(self):
        log, repo = _build()
        repo.mark_run("r_1")
        _seed_request(log, run_id="r_1", outbox_id="ob_1", kind="file_upload")
        _seed_request(log, run_id="r_1", outbox_id="ob_2", kind="file_upload")
        handler = _OkHandler({})
        d = OutboxDispatcher(repo, log, handlers={"file_upload": handler})

        retired = await d.drain_once()

        assert retired == 2
        assert handler.calls == 2
        assert len(_resolved_envelopes(log, "r_1")) == 2

    async def test_resolved_entry_filtered_on_next_drain(self):
        log, repo = _build()
        repo.mark_run("r_1")
        _seed_request(log, run_id="r_1", outbox_id="ob_1", kind="file_upload")
        d = OutboxDispatcher(
            repo, log, handlers={"file_upload": _OkHandler({"file_id": "f"})},
        )

        await d.drain_once()
        # Second drain: the entry has been resolved, repo skips it.
        assert await d.drain_once() == 0

    async def test_attempts_reset_on_ok(self):
        log, repo = _build()
        repo.mark_run("r_1")
        _seed_request(log, run_id="r_1", outbox_id="ob_1", kind="file_upload")
        repo.record_attempt("ob_1")  # simulate prior failed attempt
        d = OutboxDispatcher(
            repo, log, handlers={"file_upload": _OkHandler({})},
        )

        await d.drain_once()

        assert repo.get_attempts("ob_1") == 0

    async def test_attempts_reset_on_failed(self):
        log, repo = _build()
        repo.mark_run("r_1")
        _seed_request(log, run_id="r_1", outbox_id="ob_1", kind="file_upload")
        repo.record_attempt("ob_1")
        d = OutboxDispatcher(
            repo, log,
            handlers={"file_upload": _RaisingHandler(PermanentError("x"))},
        )

        await d.drain_once()

        assert repo.get_attempts("ob_1") == 0

    async def test_resolved_event_id_is_deterministic(self):
        log, repo = _build()
        repo.mark_run("r_1")
        _seed_request(log, run_id="r_1", outbox_id="ob_1", kind="file_upload")
        d = OutboxDispatcher(
            repo, log, handlers={"file_upload": _OkHandler({"file_id": "f"})},
        )

        await d.drain_once()
        first_event_id = _resolved_envelopes(log, "r_1")[0].event_id

        # Re-seed the same outbox_id from scratch (simulates replay where the
        # repo is queried before the resolved row was committed).
        log2 = MemoryEventLogRepository()
        repo2 = MemoryOutboxRepository(log2)
        repo2.mark_run("r_1")
        _seed_request(log2, run_id="r_1", outbox_id="ob_1", kind="file_upload")
        d2 = OutboxDispatcher(
            repo2, log2, handlers={"file_upload": _OkHandler({"file_id": "f"})},
        )
        await d2.drain_once()
        second_event_id = _resolved_envelopes(log2, "r_1")[0].event_id

        assert first_event_id == second_event_id


@pytest.mark.asyncio
class TestStop:
    async def test_stop_breaks_run_forever(self):
        import asyncio

        log, repo = _build()
        d = OutboxDispatcher(
            repo, log, handlers={}, poll_interval_s=0.05,
        )
        task = asyncio.create_task(d.run_forever())
        await asyncio.sleep(0.1)
        d.stop()
        await asyncio.wait_for(task, timeout=1.0)
        assert task.done()
