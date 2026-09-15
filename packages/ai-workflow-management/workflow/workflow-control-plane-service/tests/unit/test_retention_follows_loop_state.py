"""Retention has to walk ``loop_state``, not just ``variables``.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §14 — *"when data
leaves ``variables``, the walk that finds it has to follow"*.

A loop writes two kinds of file that ``variables`` never references:

* ``_claim_check_items`` stores an oversized collection as
  ``loop-items-{invocation_id}`` and keeps only the pointer, on
  ``loop_state[node]["items"]``;
* ``_spill_window`` flushes each results window to
  ``loop-results-{invocation_id}-{n}`` and records the chunk refs on
  ``loop_state[node]["iteration_result_chunks"]``.

Both are reachable through ``loop_state`` alone. Walking only ``variables``
deletes neither, so every re-run of a looping workflow leaks them — and leaks
nothing loudly, because retention is best-effort by design: a file nobody asked
for produces no error, no failed task and no log line.
"""
from __future__ import annotations

import pytest

from app.layer2_application.run_file_retention import delete_run_files


class FakeDeleter:
    def __init__(self, explode_on: set[str] | None = None) -> None:
        self.deleted: list[str] = []
        self._explode_on = explode_on or set()

    async def delete_file(self, file_id: str) -> dict:
        if file_id in self._explode_on:
            raise RuntimeError("file service said no")
        self.deleted.append(file_id)
        return {"ok": True}


class _NullLogger:
    def debug(self, *a, **k): pass
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass


def _ref(file_id: str) -> dict:
    return {"__file_ref": True, "file_id": file_id, "row_count": 3,
            "columns": ["a"], "artifacts": []}


def _loop_state() -> dict:
    return {
        "loop-1": {
            "total": 500,
            "items": _ref("loop-items-inv1"),
            "iteration_result_chunks": [
                _ref("loop-results-inv1-0"), _ref("loop-results-inv1-1"),
            ],
        }
    }


async def _run(variables=None, loop_state=None, deleter=None):
    deleter = deleter or FakeDeleter()
    await delete_run_files(deleter, variables, _NullLogger(), "test",
                           loop_state=loop_state)
    return deleter


class TestTheLoopsFilesAreDeleted:
    @pytest.mark.asyncio
    async def test_a_claim_checked_items_collection(self):
        d = await _run(variables={}, loop_state=_loop_state())
        assert "loop-items-inv1" in d.deleted

    @pytest.mark.asyncio
    async def test_every_spilled_results_chunk(self):
        d = await _run(variables={}, loop_state=_loop_state())
        assert {"loop-results-inv1-0", "loop-results-inv1-1"} <= set(d.deleted)

    @pytest.mark.asyncio
    async def test_loop_state_alone_is_enough_to_run(self):
        """The regression shape: ``variables`` empty because the node outputs
        were inline, ``loop_state`` holding the only files there are. The old
        signature returned early on ``not variables`` and deleted nothing."""
        d = await _run(variables=None, loop_state=_loop_state())
        assert len(d.deleted) == 3

    @pytest.mark.asyncio
    async def test_both_roots_are_collected(self):
        d = await _run(variables={"gen": {"result": _ref("node-output-1")}},
                       loop_state=_loop_state())
        assert set(d.deleted) == {
            "node-output-1", "loop-items-inv1",
            "loop-results-inv1-0", "loop-results-inv1-1",
        }

    @pytest.mark.asyncio
    async def test_a_file_in_both_roots_is_deleted_once(self):
        """A loop's final output can appear as the loop node's variable AND on
        its ``loop_state``. Two deletes would be harmless but the second is a
        wasted ~0.5 s round-trip on a path measured at 12.38 s of 13.48 s."""
        shared = _ref("shared-1")
        d = await _run(variables={"out": shared},
                       loop_state={"l": {"items": shared}})
        assert d.deleted == ["shared-1"]


class TestTheOldBehaviourIsUnchanged:
    @pytest.mark.asyncio
    async def test_variables_only_still_works(self):
        d = await _run(variables={"gen": {"result": _ref("f1")}})
        assert d.deleted == ["f1"]

    @pytest.mark.asyncio
    async def test_nothing_to_do_is_a_no_op(self):
        d = await _run(variables={}, loop_state={})
        assert d.deleted == []

    @pytest.mark.asyncio
    async def test_no_client_is_a_no_op(self):
        await delete_run_files(None, {"a": _ref("f1")}, _NullLogger(), "test",
                               loop_state=_loop_state())   # must not raise

    @pytest.mark.asyncio
    async def test_a_delete_failure_never_propagates(self):
        """Retention is awaited by both callers with no try/except of their
        own — it must not be able to fail a reset."""
        d = FakeDeleter(explode_on={"loop-items-inv1"})
        await _run(variables={}, loop_state=_loop_state(), deleter=d)
        assert set(d.deleted) == {"loop-results-inv1-0", "loop-results-inv1-1"}


class TestOneBadRootDoesNotDiscardTheOther:
    @pytest.mark.asyncio
    async def test_a_cyclic_loop_state_still_deletes_the_variables(self):
        """``collect_file_ids`` guards cycles, but the two roots are collected
        in separate calls precisely so a failure in one cannot swallow the
        other's ids."""
        cyclic: dict = {"l": {}}
        cyclic["l"]["self"] = cyclic
        cyclic["l"]["items"] = _ref("loop-items-inv1")
        d = await _run(variables={"gen": _ref("f1")}, loop_state=cyclic)
        assert "f1" in d.deleted and "loop-items-inv1" in d.deleted
