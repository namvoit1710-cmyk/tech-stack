"""Phase 4 — an oversized result gets DELIVERED, not just failed fast.

SA-1951 made an undeliverable result fail the task instead of hanging it, which
was the right first move: a node stuck RUNNING forever is worse than a node that
fails. But failing is still losing the work. Run
edda4f82-4a09-4587-beb5-8ed615ab8219 did the work — the upstream call succeeded
in 10.7 s — and then threw the answer away because it would not fit on the wire.

The executor is the one place every delivery path meets. Verified: neither the
worker-sdk pull controller nor its gRPC servicer calls the streaming converter or
the result budget, so a pull or gRPC worker gets none of Phases 2 and 3 — but its
callback still arrives HERE.

Offload is JSON, never CSV. ``data_store_service`` stores and reads verbatim JSON
(content-type or a leading brace), so the control plane resolves the ref without
``dict_to_csv`` — the 5.5-9.3x blow-up SA-1905 deliberately moved OFF a service
that runs at ``instances: 1``. And it happens only on the failure path: putting
an upload in every task's happy path would buy a round-trip for the 99% that fit.

``complete_task`` skips ``store_data`` when the output is already a file_ref
(``complete_task_use_case.py:381-383``), so the ref this builds is used as-is.
"""

from __future__ import annotations

import json

import pytest

from app.layer1_domain.errors import MessageTooLargeError, ResultUndeliverableError
from app.layer2_application.features.handle_worker_callback.use_cases.handle_worker_callback_usecase import (  # noqa: E501
    HandleWorkerCallbackInput,
    HandleWorkerCallbackUseCase,
)
from app.layer2_application.interfaces.pending_task_store_interface import PendingTaskEntry


class _Logger:
    def info(self, *a, **k): pass
    def debug(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


class _Store:
    def __init__(self, entry):
        self._entries = {entry.task_id: entry}

    def store(self, entry): self._entries[entry.task_id] = entry
    def store_if_absent(self, entry): return self._entries.setdefault(entry.task_id, entry) is entry
    def get(self, task_id): return self._entries.get(task_id)
    def remove(self, task_id): return self._entries.pop(task_id, None)
    def get_timed_out(self): return list(self._entries.values())
    def count(self): return len(self._entries)


class _Publisher:
    """Rejects any message whose JSON exceeds *cap*, like the broker does."""

    def __init__(self, cap: int = 1024):
        self.published: list[dict] = []
        self._cap = cap

    def publish_result(self, message: dict) -> None:
        size = len(json.dumps(message, default=str).encode("utf-8"))
        if size > self._cap:
            raise MessageTooLargeError("topic workflow.results", size, self._cap)
        self.published.append(message)


class _Uploader:
    def __init__(self, file_id="file-off-1", exc=None):
        self.uploads = []
        self._file_id = file_id
        self._exc = exc

    def upload(self, file_bytes, filename, content_type="application/json"):
        if self._exc:
            raise self._exc
        self.uploads.append((file_bytes, filename, content_type))
        return self._file_id


def _entry(task_id="t1"):
    return PendingTaskEntry(
        task_id=task_id, run_id="r1", node_id="n1", worker_type="http_request",
        dispatched_at="2026-08-18T00:00:00Z", timeout_at="2026-08-18T00:05:00Z",
    )


def _uc(store, publisher, uploader=None, monitor=None):
    return HandleWorkerCallbackUseCase(
        logger=_Logger(), pending_task_store=store, result_reporter=None,
        result_publisher=publisher, file_uploader=uploader, monitor=monitor,
    )


_BIG = {"resolved_body": "x" * 4000, "status_code": 200}


def _success():
    return HandleWorkerCallbackInput(
        task_id="t1", status="success", outputs=dict(_BIG), duration_ms=1.0,
    )


class TestOffloadOnOversize:
    def test_the_task_completes_instead_of_failing(self):
        pub, up = _Publisher(), _Uploader()
        _uc(_Store(_entry()), pub, up).execute(_success())

        assert len(pub.published) == 1
        assert pub.published[0]["message_type"] == "task.completed"
        assert pub.published[0]["payload"]["status"] == "COMPLETED"

    def test_the_outputs_are_replaced_by_a_file_ref(self):
        pub, up = _Publisher(), _Uploader()
        _uc(_Store(_entry()), pub, up).execute(_success())

        meta = pub.published[0]["payload"]["output_metadata"]
        assert meta["__file_ref"] is True
        assert meta["file_id"] == "file-off-1"
        assert "json" in meta["content_type"]
        # complete_task resolves the ref; the reference is also named explicitly.
        assert pub.published[0]["payload"]["output_reference"] == "file-off-1"

    def test_the_uploaded_bytes_are_the_original_outputs_as_json(self):
        pub, up = _Publisher(), _Uploader()
        _uc(_Store(_entry()), pub, up).execute(_success())

        blob, _, content_type = up.uploads[0]
        assert json.loads(blob.decode("utf-8")) == _BIG
        assert content_type == "application/json"

    def test_the_republished_message_actually_fits(self):
        """The whole point — it must be small enough for the same broker."""
        pub, up = _Publisher(), _Uploader()
        _uc(_Store(_entry()), pub, up).execute(_success())

        size = len(json.dumps(pub.published[0], default=str).encode("utf-8"))
        assert size <= 1024

    def test_the_pending_entry_is_cleared(self):
        store, pub, up = _Store(_entry()), _Publisher(), _Uploader()
        _uc(store, pub, up).execute(_success())

        assert store.get("t1") is None


class TestOffloadDegradesSafely:
    """The ladder only ever gets longer: offload, then today's behaviour."""

    def test_no_uploader_wired_is_exactly_todays_behaviour(self):
        pub = _Publisher()
        _uc(_Store(_entry()), pub, uploader=None).execute(_success())

        assert pub.published[0]["message_type"] == "task.failed"
        assert pub.published[0]["payload"]["error"]["code"] == "RESULT_TOO_LARGE"

    def test_an_upload_failure_falls_back_to_task_failed(self):
        pub, up = _Publisher(), _Uploader(exc=RuntimeError("file service down"))
        _uc(_Store(_entry()), pub, up).execute(_success())

        assert pub.published[0]["message_type"] == "task.failed"
        assert pub.published[0]["payload"]["error"]["code"] == "RESULT_TOO_LARGE"

    def test_a_transport_failure_is_not_offloaded(self):
        """A broker that is down is not a payload problem; uploading is useless.

        The call still raises ``ResultUndeliverableError`` — unchanged, and
        deliberate: the entry goes back so the sweeper can time the task out and
        the worker retries the callback.
        """
        class _Broken(_Publisher):
            def publish_result(self, message):
                raise RuntimeError("broker refused")

        pub, up = _Broken(), _Uploader()
        with pytest.raises(ResultUndeliverableError):
            _uc(_Store(_entry()), pub, up).execute(_success())

        assert up.uploads == []

    def test_an_already_failed_task_is_not_offloaded(self):
        """The worker's own error is the news; its outputs are what to drop."""
        pub, up = _Publisher(), _Uploader()
        _uc(_Store(_entry()), pub, up).execute(HandleWorkerCallbackInput(
            task_id="t1", status="error", error="ZeroDivisionError",
            outputs=dict(_BIG),
        ))

        assert up.uploads == []
        assert pub.published[0]["message_type"] == "task.failed"
        assert pub.published[0]["payload"]["error"]["code"] == "WORKER_ERROR"

    def test_a_result_that_fits_never_touches_the_uploader(self):
        pub, up = _Publisher(), _Uploader()
        _uc(_Store(_entry()), pub, up).execute(HandleWorkerCallbackInput(
            task_id="t1", status="success", outputs={"ok": True}, duration_ms=1.0,
        ))

        assert up.uploads == []
        assert pub.published[0]["message_type"] == "task.completed"


class TestOffloadIsCounted:
    def test_a_successful_offload_counts_separately_from_a_failure(self):
        class _Mon:
            def __init__(self): self.counts = []
            def increment(self, m): self.counts.append(m)

        pub, up, mon = _Publisher(), _Uploader(), _Mon()
        _uc(_Store(_entry()), pub, up, monitor=mon).execute(_success())

        assert "result_offloaded" in mon.counts
        assert "result_too_large" not in mon.counts


class TestWiring:
    def test_the_use_case_takes_a_file_uploader_by_that_exact_name(self):
        """bootstrap injects by PARAMETER NAME (``param in sig.parameters``).

        Renaming this parameter would not break a single test or raise anything
        at startup — smart-DI would simply stop passing it, the uploader would be
        None, and every oversized result would quietly go back to being thrown
        away. Pin the name.
        """
        import inspect
        params = inspect.signature(HandleWorkerCallbackUseCase).parameters
        assert "file_uploader" in params
        assert params["file_uploader"].default is None
