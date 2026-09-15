"""Phase 3 Sub 3.3 — DATA_EDIT outbox migration.

End-to-end tests for the outbox-routed ``_upload`` and
``_gc_orphans`` paths on ``EditNodeDataUseCase``. Exercises:

- Flag-off (default): inline ``_fs.upload_file`` path returns the
  file_id immediately, no events appended.
- Flag-on (``OUTBOX_DATA_EDIT=on``-equivalent — outbox deps wired):
  ``_upload`` appends ``OutboxRequested``, the dispatcher's
  ``file_upload`` handler runs against a stub file service, the
  awaiter resolves, and the use case returns the new file_id.
- Replay safety: re-running ``_upload`` with the same seed lands on
  the same ``outbox_id`` and the event log dedupes.
- ``_gc_orphans`` with a wired outbox emits one
  ``OutboxRequested(kind="file_delete")`` per file_id and skips the
  fire-and-forget delete loop.
- Timeout fallback: a slow dispatcher triggers
  ``OutboxTimeoutError`` and the use case falls back to direct
  upload so the PATCH 200 contract is preserved.
"""

from __future__ import annotations

import asyncio

import pytest

from app.layer1_domain.value_objects.event_id import EventId
from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
    EditNodeDataUseCase,
)
from app.layer2_application.events.outbox_awaiter import OutboxAwaiter
from app.layer4_frameworks.outbox.handlers import (
    FileDeleteHandler,
    FileUploadHandler,
)
from app.layer4_frameworks.outbox.outbox_bytes_channel import (
    OutboxBytesChannel,
)
from app.layer4_frameworks.outbox.outbox_dispatcher import OutboxDispatcher
from app.layer4_frameworks.persistence.memory.memory_event_log_repo import (
    MemoryEventLogRepository,
)
from app.layer4_frameworks.persistence.memory.memory_outbox_repository import (
    MemoryOutboxRepository,
)


# ──────────────────────────── stubs ────────────────────────────


class _StubFileService:
    """Tracks every upload/delete call, returns deterministic file_ids."""

    def __init__(self, fail_upload: bool = False) -> None:
        self.uploads: list[tuple[bytes, str, str]] = []
        self.deletes: list[str] = []
        self._fail_upload = fail_upload

    async def upload_file(
        self,
        content: bytes,
        filename: str,
        content_type: str = "text/csv",
        *,
        idempotency_key: str | None = None,
        **_kwargs,
    ) -> dict:
        self.uploads.append((content, filename, content_type))
        if self._fail_upload:
            raise RuntimeError("upload boom")
        return {"file_id": f"f_{len(self.uploads)}"}

    async def delete_file(self, file_id: str) -> dict:
        self.deletes.append(file_id)
        return {}

    async def download_file(self, file_id: str) -> bytes:  # pragma: no cover
        return b""


def _build_use_case(
    file_service,
    *,
    outbox_enabled: bool,
    awaiter: OutboxAwaiter | None = None,
    bytes_channel: OutboxBytesChannel | None = None,
    event_log: MemoryEventLogRepository | None = None,
    outbox_repo: MemoryOutboxRepository | None = None,
) -> EditNodeDataUseCase:
    from app.layer4_frameworks.config.app_config import settings
    return EditNodeDataUseCase(
        logger=None,
        run_repo=None,
        run_context_view_store=None,
        task_repo=None,
        workflow_repo=None,
        file_service_client=file_service,
        node_input_etag_calculator=None,
        outbox_awaiter=awaiter if outbox_enabled else None,
        outbox_bytes_channel=bytes_channel if outbox_enabled else None,
        outbox_repo=outbox_repo if outbox_enabled else None,
        event_log_repo=event_log if outbox_enabled else None,
        outbox_http_wait_timeout_s=0.5,
        app_config=settings,
    )


# ─────────────────────────── tests ────────────────────────────


@pytest.mark.asyncio
class TestUploadFlagOff:
    async def test_inline_upload_when_outbox_deps_missing(self):
        fs = _StubFileService()
        uc = _build_use_case(fs, outbox_enabled=False)
        file_id = await uc._upload(b"abc", filename="x.csv")
        assert file_id == "f_1"
        assert fs.uploads == [(b"abc", "x.csv", "text/csv")]

    async def test_inline_upload_when_run_id_missing(self):
        # Outbox wired but no run_id — fall back to inline.
        fs = _StubFileService()
        log = MemoryEventLogRepository()
        repo = MemoryOutboxRepository(log)
        ch = OutboxBytesChannel()
        a = OutboxAwaiter()
        uc = _build_use_case(
            fs, outbox_enabled=True,
            awaiter=a, bytes_channel=ch, event_log=log, outbox_repo=repo,
        )
        await uc._upload(b"abc", filename="x.csv")
        assert fs.uploads == [(b"abc", "x.csv", "text/csv")]
        assert log.list_by_run("") == []


@pytest.mark.asyncio
class TestUploadFlagOn:
    async def _build(self, fs):
        log = MemoryEventLogRepository()
        repo = MemoryOutboxRepository(log)
        ch = OutboxBytesChannel()
        a = OutboxAwaiter()
        uc = _build_use_case(
            fs, outbox_enabled=True,
            awaiter=a, bytes_channel=ch, event_log=log, outbox_repo=repo,
        )
        dispatcher = OutboxDispatcher(
            outbox_repo=repo,
            event_log_repo=log,
            handlers={"file_upload": FileUploadHandler(fs, ch)},
            awaiter=a,
        )
        return uc, dispatcher, log, repo, ch

    async def test_outbox_round_trip_returns_file_id(self):
        fs = _StubFileService()
        uc, dispatcher, log, repo, _ch = await self._build(fs)

        # Schedule the dispatcher to drain after the use case starts
        # waiting; the awaiter delivers in-process.
        async def upload_and_wait():
            return await uc._upload(
                b"hello", filename="root.csv",
                run_id="r_1", task_id="t_1", op_index=0,
            )

        async def drain_after_delay():
            await asyncio.sleep(0.02)
            await dispatcher.drain_once()

        upload_task = asyncio.create_task(upload_and_wait())
        await drain_after_delay()
        file_id = await asyncio.wait_for(upload_task, timeout=1.0)

        assert file_id == "f_1"
        assert fs.uploads == [(b"hello", "root.csv", "text/csv")]
        # Two log entries: requested + resolved.
        envelopes = log.list_by_run("r_1")
        kinds = [(e.event_type, e.payload.get("outbox_id")) for e in envelopes]
        assert ("OutboxRequested", EventId.derive_outbox("r_1", "t_1", 0, "file_upload")) in kinds
        assert any(e == "OutboxResolved" for e, _ in kinds)

    async def test_replay_dedupes_to_same_outbox_id(self):
        fs = _StubFileService()
        uc, dispatcher, log, _repo, _ch = await self._build(fs)

        async def first_run():
            t = asyncio.create_task(uc._upload(
                b"hello", filename="root.csv",
                run_id="r_1", task_id="t_1", op_index=0,
            ))
            await asyncio.sleep(0.02)
            await dispatcher.drain_once()
            return await t

        await first_run()

        # Re-call with identical (run_id, task_id, op_index) — the
        # event log already contains an OutboxResolved for this id, so
        # the dispatcher won't see a fresh request. The producer
        # registers an awaiter that times out — caller falls back to
        # direct upload.
        async def replay():
            return await uc._upload(
                b"hello", filename="root.csv",
                run_id="r_1", task_id="t_1", op_index=0,
            )

        file_id = await asyncio.wait_for(replay(), timeout=1.5)

        # Replay produces the same deterministic id; the event-log
        # append on replay is a no-op (dedupe by event_id), so only one
        # OutboxRequested envelope exists.
        requested = [
            e for e in log.list_by_run("r_1") if e.event_type == "OutboxRequested"
        ]
        assert len(requested) == 1
        # Timeout-fallback path uploaded directly — second upload call.
        assert len(fs.uploads) == 2
        assert file_id == "f_2"

    async def test_permanent_handler_failure_falls_back(self):
        fs = _StubFileService()
        uc, dispatcher, _log, _repo, _ch = await self._build(fs)

        # Replace the handler with one that raises PermanentError.
        from app.layer4_frameworks.outbox.outbox_errors import (
            PermanentError,
        )

        class _Boom:
            async def perform(self, entry):
                raise PermanentError("boom")

        dispatcher._handlers = {"file_upload": _Boom()}

        async def upload():
            return await uc._upload(
                b"hello", filename="root.csv",
                run_id="r_2", task_id="t_2", op_index=0,
            )

        async def drain():
            await asyncio.sleep(0.02)
            await dispatcher.drain_once()

        upload_task = asyncio.create_task(upload())
        await drain()
        file_id = await asyncio.wait_for(upload_task, timeout=1.0)

        # Outbox resolved failed → use case fallback → direct upload.
        assert file_id == "f_1"
        assert len(fs.uploads) == 1

    async def test_timeout_falls_back_to_direct_upload(self):
        fs = _StubFileService()
        uc, _dispatcher, _log, _repo, _ch = await self._build(fs)

        # No drain happens, awaiter times out at 0.5s.
        file_id = await uc._upload(
            b"hello", filename="root.csv",
            run_id="r_3", task_id="t_3", op_index=0,
        )

        # Outbox stalled; fallback uploaded directly.
        assert file_id == "f_1"
        assert len(fs.uploads) == 1


@pytest.mark.asyncio
class TestGCOrphans:
    async def test_inline_delete_when_outbox_disabled(self):
        fs = _StubFileService()
        uc = _build_use_case(fs, outbox_enabled=False)
        await uc._gc_orphans(["f_a", "f_b"], run_id="r_1")
        # Background task — wait briefly for delete loop to finish.
        await asyncio.sleep(0.05)
        assert sorted(fs.deletes) == ["f_a", "f_b"]

    async def test_outbox_emits_one_request_per_file_id(self):
        fs = _StubFileService()
        log = MemoryEventLogRepository()
        repo = MemoryOutboxRepository(log)
        uc = _build_use_case(
            fs, outbox_enabled=True,
            awaiter=OutboxAwaiter(),
            bytes_channel=OutboxBytesChannel(),
            event_log=log, outbox_repo=repo,
        )

        await uc._gc_orphans(["f_a", "f_b"], run_id="r_1")

        envelopes = [
            e for e in log.list_by_run("r_1") if e.event_type == "OutboxRequested"
        ]
        kinds = [e.payload.get("kind") for e in envelopes]
        assert kinds == ["file_delete", "file_delete"]
        # No direct delete calls — those happen via the dispatcher.
        await asyncio.sleep(0.05)
        assert fs.deletes == []

    async def test_outbox_skipped_without_run_id(self):
        fs = _StubFileService()
        log = MemoryEventLogRepository()
        repo = MemoryOutboxRepository(log)
        uc = _build_use_case(
            fs, outbox_enabled=True,
            awaiter=OutboxAwaiter(),
            bytes_channel=OutboxBytesChannel(),
            event_log=log, outbox_repo=repo,
        )
        # No run_id — outbox path requires it; falls back to inline.
        await uc._gc_orphans(["f_a"])
        await asyncio.sleep(0.05)
        assert fs.deletes == ["f_a"]
        assert log.list_by_run("r_1") == []

    async def test_dispatcher_resolves_file_delete(self):
        fs = _StubFileService()
        log = MemoryEventLogRepository()
        repo = MemoryOutboxRepository(log)
        ch = OutboxBytesChannel()
        a = OutboxAwaiter()
        uc = _build_use_case(
            fs, outbox_enabled=True,
            awaiter=a, bytes_channel=ch, event_log=log, outbox_repo=repo,
        )
        dispatcher = OutboxDispatcher(
            outbox_repo=repo,
            event_log_repo=log,
            handlers={"file_delete": FileDeleteHandler(fs)},
            awaiter=a,
        )

        await uc._gc_orphans(["f_a", "f_b"], run_id="r_4")
        await dispatcher.drain_once()

        assert sorted(fs.deletes) == ["f_a", "f_b"]
        resolved = [
            e for e in log.list_by_run("r_4") if e.event_type == "OutboxResolved"
        ]
        assert len(resolved) == 2
        assert all(e.payload.get("outcome") == "ok" for e in resolved)

    async def test_orphan_gc_disabled_setting_skips_outbox(self, monkeypatch):
        from app.layer4_frameworks.config import app_config

        monkeypatch.setattr(app_config.settings, "ORPHAN_GC_ENABLED", False)
        fs = _StubFileService()
        log = MemoryEventLogRepository()
        repo = MemoryOutboxRepository(log)
        uc = _build_use_case(
            fs, outbox_enabled=True,
            awaiter=OutboxAwaiter(),
            bytes_channel=OutboxBytesChannel(),
            event_log=log, outbox_repo=repo,
        )
        await uc._gc_orphans(["f_a"], run_id="r_1")
        # Kill switch wins — neither outbox nor inline path runs.
        assert log.list_by_run("r_1") == []
        await asyncio.sleep(0.05)
        assert fs.deletes == []
