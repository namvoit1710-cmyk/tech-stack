"""SA-1951 — a worker result must never be lost silently.

Regression cover for the bug: with a large inline output, the Event Mesh publish
413'd, the exception escaped an UNGUARDED publish, the worker ignored the 500,
and because the pending entry had already been popped the sweeper could not time
the task out either — the node stayed RUNNING forever with no error anywhere.
"""

from __future__ import annotations

import pytest

from app.layer1_domain.errors import MessageTooLargeError, ResultUndeliverableError
from app.layer2_application.features.handle_worker_callback.use_cases.handle_worker_callback_usecase import (  # noqa: E501
    HandleWorkerCallbackInput,
    HandleWorkerCallbackUseCase,
)
from app.layer2_application.features.sweep_timed_out_tasks.use_cases.sweep_timed_out_tasks_usecase import (  # noqa: E501
    SweepTimedOutTasksUseCase,
)
from app.layer2_application.interfaces.pending_task_store_interface import PendingTaskEntry


class _Logger:
    def info(self, *a, **k): pass
    def debug(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


class _Store:
    def __init__(self, entry: PendingTaskEntry | None = None):
        self._entries: dict[str, PendingTaskEntry] = {}
        if entry:
            self._entries[entry.task_id] = entry

    def store(self, entry): self._entries[entry.task_id] = entry
    def store_if_absent(self, entry): return self._entries.setdefault(entry.task_id, entry) is entry
    def get(self, task_id): return self._entries.get(task_id)
    def remove(self, task_id): return self._entries.pop(task_id, None)
    def get_timed_out(self): return list(self._entries.values())
    def count(self): return len(self._entries)


class _Concurrency:
    def __init__(self): self.releases = 0
    def release(self): self.releases += 1


class _Publisher:
    """Fails the first N publishes, records everything it was handed."""

    def __init__(self, fail_times: int = 0, exc: Exception | None = None, fail_all: bool = False):
        self.published: list[dict] = []
        self._fail_times = fail_times
        self._fail_all = fail_all
        self._exc = exc or MessageTooLargeError("topic workflow.results", 2_041_388, 1_048_576)
        self.attempts = 0

    def publish_result(self, message: dict) -> None:
        self.attempts += 1
        if self._fail_all or self.attempts <= self._fail_times:
            raise self._exc
        self.published.append(message)


def _entry(task_id="t1") -> PendingTaskEntry:
    return PendingTaskEntry(
        task_id=task_id, run_id="r1", node_id="gen", worker_type="code",
        dispatched_at="2026-07-14T00:00:00Z", timeout_at="2026-07-14T00:05:00Z",
    )


def _uc(store, publisher, concurrency=None):
    return HandleWorkerCallbackUseCase(
        logger=_Logger(),
        pending_task_store=store,
        result_reporter=None,          # event_mesh mode — the publish is the ONLY route home
        result_publisher=publisher,
        concurrency_control=concurrency,
    )


def _success_input(task_id="t1"):
    return HandleWorkerCallbackInput(
        task_id=task_id, status="success",
        outputs={"result": [{"sku": "A"}] * 3}, duration_ms=1.0,
    )


class TestHappyPath:
    def test_result_is_published_and_entry_removed(self):
        store, pub = _Store(_entry()), _Publisher()
        out = _uc(store, pub).execute(_success_input())

        assert out["accepted"] is True
        assert pub.published[0]["message_type"] == "task.completed"
        assert store.get("t1") is None


class TestOversizedResult:
    """The bug: publish raises, and the task must NOT vanish."""

    def test_publish_failure_degrades_to_a_small_task_failed(self):
        store, pub = _Store(_entry()), _Publisher(fail_times=1)
        out = _uc(store, pub).execute(_success_input())

        assert out["accepted"] is True
        assert len(pub.published) == 1
        failed = pub.published[0]
        assert failed["message_type"] == "task.failed"
        assert failed["payload"]["error"]["code"] == "RESULT_TOO_LARGE"
        # The outputs are exactly what could not be delivered — they must not be
        # smuggled into the fallback, or it is oversized too.
        assert "outputs" not in failed["payload"]

    def test_non_size_publish_failure_is_reported_as_undeliverable(self):
        store = _Store(_entry())
        pub = _Publisher(fail_times=1, exc=RuntimeError("broker refused"))
        _uc(store, pub).execute(_success_input())

        assert pub.published[0]["payload"]["error"]["code"] == "RESULT_UNDELIVERABLE"

    def test_run_fails_fast_instead_of_hanging(self):
        """The whole point: a terminal state is emitted, not silence."""
        store, pub = _Store(_entry()), _Publisher(fail_times=1)
        _uc(store, pub).execute(_success_input())

        assert pub.published[-1]["payload"]["status"] == "FAILED"


class TestAlreadyFailedTask:
    """An oversized FAILED result: the worker's error is the news — keep it."""

    def test_original_worker_error_survives_the_degrade(self):
        store, pub = _Store(_entry()), _Publisher(fail_times=1)
        _uc(store, pub).execute(HandleWorkerCallbackInput(
            task_id="t1", status="error",
            error="ZeroDivisionError: division by zero",
            outputs={"partial": ["x"] * 5},
        ))

        err = pub.published[0]["payload"]["error"]
        assert err["code"] == "WORKER_ERROR", "must not mask the worker's own error"
        assert "division by zero" in err["message"]
        # …but say WHY the full message could not be relayed, and drop the payload.
        assert err["undeliverable"] == "RESULT_TOO_LARGE"
        assert "outputs" not in pub.published[0]["payload"]


class TestBrokerCompletelyDown:
    """Even the small fallback fails — the sweeper must still be able to act."""

    def test_entry_is_restored_so_the_sweeper_can_time_it_out(self):
        store, pub, conc = _Store(_entry()), _Publisher(fail_all=True), _Concurrency()

        with pytest.raises(ResultUndeliverableError):
            _uc(store, pub, conc).execute(_success_input())

        restored = store.get("t1")
        assert restored is not None, "entry must be put back, or the task hangs forever"
        assert restored.concurrency_released is True

    def test_sweeper_does_not_double_release_a_restored_entry(self):
        store, pub, conc = _Store(_entry()), _Publisher(fail_all=True), _Concurrency()
        with pytest.raises(ResultUndeliverableError):
            _uc(store, pub, conc).execute(_success_input())
        assert conc.releases == 1  # released once, by the callback

        sweeper = SweepTimedOutTasksUseCase(
            logger=_Logger(), pending_task_store=store,
            result_reporter=None, result_publisher=_Publisher(),
            concurrency_control=conc,
        )
        assert sweeper.execute()["swept"] == 1
        assert conc.releases == 1, "the slot was already released — releasing again inflates the budget"

    def test_worker_retry_of_a_restored_entry_does_not_double_release(self):
        store, pub, conc = _Store(_entry()), _Publisher(fail_all=True), _Concurrency()
        with pytest.raises(ResultUndeliverableError):
            _uc(store, pub, conc).execute(_success_input())

        # The worker retries the callback (SA-1951 gave it raise_for_status+retry);
        # the broker is back, so this one lands.
        ok_pub = _Publisher()
        _uc(store, ok_pub, conc).execute(_success_input())

        assert ok_pub.published[0]["message_type"] == "task.completed"
        assert conc.releases == 1


class TestSweeperUnchangedForNormalEntries:
    def test_normal_timed_out_entry_still_releases(self):
        store, conc = _Store(_entry()), _Concurrency()
        sweeper = SweepTimedOutTasksUseCase(
            logger=_Logger(), pending_task_store=store,
            result_reporter=None, result_publisher=_Publisher(),
            concurrency_control=conc,
        )
        assert sweeper.execute()["swept"] == 1
        assert conc.releases == 1


class _Monitor:
    def __init__(self):
        self.counts: list[str] = []

    def increment(self, metric: str) -> None:
        self.counts.append(metric)


class TestUndeliverableResultIsCounted:
    """An undeliverable result must be COUNTABLE, not only greppable in a log.

    Run edda4f82-4a09-4587-beb5-8ed615ab8219 (tenant-1, 2026-08-18) hit
    RESULT_TOO_LARGE in production and the only way anyone found out was reading
    the run by hand. There are 0 alert rules and 0 contact points on this
    system, so a counter is the first thing that could ever page.
    """

    def test_result_too_large_increments_its_own_counter(self):
        store, pub, mon = _Store(_entry()), _Publisher(fail_times=1), _Monitor()
        HandleWorkerCallbackUseCase(
            logger=_Logger(), pending_task_store=store, result_reporter=None,
            result_publisher=pub, monitor=mon,
        ).execute(_success_input())

        assert "result_too_large" in mon.counts

    def test_a_transport_failure_counts_as_undeliverable_not_too_large(self):
        """The two are different problems; one counter for both would hide that."""
        store = _Store(_entry())
        pub = _Publisher(fail_times=1, exc=RuntimeError("broker refused"))
        mon = _Monitor()
        HandleWorkerCallbackUseCase(
            logger=_Logger(), pending_task_store=store, result_reporter=None,
            result_publisher=pub, monitor=mon,
        ).execute(_success_input())

        assert "result_undeliverable" in mon.counts
        assert "result_too_large" not in mon.counts

    def test_a_delivered_result_counts_nothing(self):
        store, pub, mon = _Store(_entry()), _Publisher(), _Monitor()
        HandleWorkerCallbackUseCase(
            logger=_Logger(), pending_task_store=store, result_reporter=None,
            result_publisher=pub, monitor=mon,
        ).execute(_success_input())

        assert mon.counts == []

    def test_no_monitor_is_not_a_crash(self):
        """Every other monitor call here is guarded; this one must be too."""
        store, pub = _Store(_entry()), _Publisher(fail_times=1)
        out = _uc(store, pub).execute(_success_input())

        assert out["accepted"] is True
