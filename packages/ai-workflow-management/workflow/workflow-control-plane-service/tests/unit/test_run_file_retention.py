"""Unit tests for run_file_retention.delete_run_files.

The behaviour under contract is: delete EVERY file-backed output reachable in a
discarded run's variables, concurrently but bounded, fully awaited (so the
delete-before-anything-new-is-written ordering the callers rely on survives),
and never raise — retention is best-effort GC and must not fail a reset.
"""

import asyncio
import logging

import pytest

from app.layer1_domain.entities.data_reference import add_artifact, build_file_ref
from app.layer2_application import run_file_retention
from app.layer2_application.run_file_retention import (
    _DELETE_CONCURRENCY,
    delete_run_files,
)


LOGGER = logging.getLogger(__name__)


class _RecordingDeleter:
    """Tracks every delete plus the peak number in flight at once."""

    def __init__(self, delay: float = 0.0, fail_on: set[str] | None = None) -> None:
        self.deleted: list[str] = []
        self.in_flight = 0
        self.peak_in_flight = 0
        self._delay = delay
        self._fail_on = fail_on or set()

    async def delete_file(self, file_id: str) -> dict:
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            if self._delay:
                await asyncio.sleep(self._delay)
            if file_id in self._fail_on:
                raise RuntimeError(f"boom: {file_id}")
            self.deleted.append(file_id)
            return {"file_id": file_id, "status": "deleted"}
        finally:
            self.in_flight -= 1


def _variables_with(n: int) -> dict:
    """A variables tree nesting ``n`` file ids at varying depths, the way a real
    run does (``$gen.result``, ``$out.output.x``, artifacts under a main ref)."""
    main = build_file_ref(file_id="f_main", columns=["a"])
    # ``add_artifact`` mutates ``main`` in place and returns the ARTIFACT dict,
    # which is not itself a ``__file_ref`` — the tree root ``collect_file_ids``
    # has to walk is still ``main``.
    add_artifact(
        main, file_id="f_art", name="rows", artifact_id="rows",
        artifact_type="array_field",
    )
    extra = {
        f"node_{i}": {"output": build_file_ref(file_id=f"f_{i}", columns=["a"])}
        for i in range(n - 2)
    }
    return {"input": {}, "deep": {"nested": main}, **extra}


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deletes_every_reachable_file_id():
    client = _RecordingDeleter()
    variables = _variables_with(10)

    await delete_run_files(client, variables, LOGGER, "full-run")

    # 8 nested node outputs + the main ref + its artifact.
    assert sorted(client.deleted) == sorted(
        ["f_main", "f_art"] + [f"f_{i}" for i in range(8)]
    )


@pytest.mark.asyncio
async def test_all_deletes_complete_before_returning():
    """The ordering the callers depend on: nothing is still in flight when this
    returns, or a reset could wipe variables while a delete is pending."""
    client = _RecordingDeleter(delay=0.01)

    await delete_run_files(client, _variables_with(20), LOGGER, "full-run")

    assert client.in_flight == 0
    assert len(client.deleted) == 20


# ---------------------------------------------------------------------------
# the actual fix: bounded fan-out
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deletes_run_concurrently_but_bounded():
    """More than one delete is in flight (the point of the change), and never
    more than ``_DELETE_CONCURRENCY`` (the bound that protects the single
    file-service instance)."""
    client = _RecordingDeleter(delay=0.01)

    await delete_run_files(client, _variables_with(30), LOGGER, "full-run")

    assert client.peak_in_flight > 1, "deletes still serial — the fix is not live"
    assert client.peak_in_flight <= _DELETE_CONCURRENCY


@pytest.mark.asyncio
async def test_fewer_files_than_the_bound_still_all_delete():
    client = _RecordingDeleter(delay=0.01)

    await delete_run_files(client, _variables_with(3), LOGGER, "full-run")

    assert len(client.deleted) == 3
    assert client.peak_in_flight <= 3


# ---------------------------------------------------------------------------
# best-effort: nothing here may raise
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_one_failing_delete_does_not_stop_the_others():
    client = _RecordingDeleter(fail_on={"f_3"})

    await delete_run_files(client, _variables_with(10), LOGGER, "full-run")

    assert "f_3" not in client.deleted
    assert len(client.deleted) == 9


@pytest.mark.asyncio
async def test_collect_file_ids_failure_is_swallowed(monkeypatch):
    def _explode(_value):
        raise ValueError("cannot walk")

    monkeypatch.setattr(run_file_retention, "collect_file_ids", _explode)
    client = _RecordingDeleter()

    await delete_run_files(client, _variables_with(4), LOGGER, "reset")

    assert client.deleted == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client, variables",
    [
        (None, {"a": 1}),          # no file-service client wired
        (_RecordingDeleter(), None),   # nothing to clean
        (_RecordingDeleter(), {}),
    ],
)
async def test_noop_cases(client, variables):
    await delete_run_files(client, variables, LOGGER, "reset")


@pytest.mark.asyncio
async def test_variables_without_any_file_ref_is_a_noop():
    client = _RecordingDeleter()

    await delete_run_files(
        client, {"input": {"a": 1}, "artifacts": {}}, LOGGER, "reset",
    )

    assert client.deleted == []


# ---------------------------------------------------------------------------
# cancellation must still unwind (CancelledError is BaseException)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancellation_is_not_swallowed():
    class _Cancelling:
        async def delete_file(self, file_id: str) -> dict:
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await delete_run_files(_Cancelling(), _variables_with(4), LOGGER, "reset")
