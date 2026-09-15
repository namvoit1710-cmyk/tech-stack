"""Retention has to keep finding files once ``CONTEXT`` is gone.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §14 — *"Step 5 must
re-point it in the same commit."*

Both retention callers walk ``run.context.variables``, which is deserialized
from the ``CONTEXT`` NCLOB. ``DROP CONTEXT`` makes that ``{}``, at which point
``delete_run_files`` returns early having deleted nothing — and **every
``/test/full`` re-run and every reset leaks every file**, silently, because
retention is best-effort by design: an undeleted file produces no error, no
failed task and no log line.

This is the second half of the same trap #1837 closed for ``loop_state``. That
one was about a tree the walk never visited; this one is about the walk's ROOT
disappearing.

``retention_variables`` prefers the aggregate and falls back to the projector
view, which has been authoritative for ``variables`` since Phase 1b. Aggregate
first on purpose: behaviour today stays byte-identical, and the fallback only
engages once there is nothing in ``CONTEXT`` to find. Flipping the precedence
now would change what gets deleted during the shadow period, and a wrong delete
is unrecoverable.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer2_application.run_file_retention import (
    delete_run_files,
    retention_variables,
)


def _ref(file_id: str) -> dict:
    return {"__file_ref": True, "file_id": file_id, "row_count": 1,
            "columns": ["a"], "artifacts": []}


class _View:
    def __init__(self, variables: dict) -> None:
        self.variables = variables


class _ViewStore:
    def __init__(self, view=None, explode: bool = False) -> None:
        self._view = view
        self._explode = explode
        self.gets: list[str] = []

    def get(self, run_id: str):
        self.gets.append(run_id)
        if self._explode:
            raise RuntimeError("projector unavailable")
        return self._view


def _run(variables: dict | None = None) -> WorkflowRun:
    return WorkflowRun(id="run-1", workflow_id="wf",
                       context=RunContext(variables=variables or {}))


class TestTheAggregateStillWinsWhileContextExists:
    def test_it_returns_the_aggregates_variables(self):
        run = _run({"gen": _ref("from-context")})
        store = _ViewStore(_View({"gen": _ref("from-view")}))
        assert retention_variables(run, store) == {"gen": _ref("from-context")}

    def test_the_view_is_not_even_consulted(self):
        """Today's behaviour has to stay byte-identical — a wrong delete is
        unrecoverable, so the fallback must not change what happens now."""
        store = _ViewStore(_View({"gen": _ref("from-view")}))
        retention_variables(_run({"gen": _ref("x")}), store)
        assert store.gets == []


class TestOnceContextIsGoneTheProjectorAnswers:
    def test_it_falls_back_to_the_view(self):
        """The post-DROP-CONTEXT shape: the aggregate's variables are empty
        because there is no NCLOB left to deserialize them from."""
        store = _ViewStore(_View({"gen": _ref("from-view")}))
        assert retention_variables(_run(), store) == {"gen": _ref("from-view")}

    @pytest.mark.asyncio
    async def test_the_files_are_actually_deleted(self):
        """End to end: without the fallback this deletes nothing at all."""
        deleted: list[str] = []

        class Client:
            async def delete_file(self, file_id: str) -> dict:
                deleted.append(file_id)
                return {}

        class Log:
            def debug(self, *a, **k): pass

        store = _ViewStore(_View({"gen": {"result": _ref("f1")},
                                  "out": _ref("f2")}))
        await delete_run_files(Client(), retention_variables(_run(), store),
                               Log(), "reset")
        assert set(deleted) == {"f1", "f2"}

    def test_an_empty_view_is_empty_not_a_crash(self):
        assert retention_variables(_run(), _ViewStore(_View({}))) == {}

    def test_a_missing_view_is_empty(self):
        assert retention_variables(_run(), _ViewStore(None)) == {}


class TestItCannotBeTheThingThatBreaksAReset:
    """``delete_run_files`` is awaited by both callers with no ``try``/``except``
    of their own, and this helper runs immediately before it."""

    def test_a_projector_failure_degrades_to_empty(self):
        assert retention_variables(_run(), _ViewStore(explode=True)) == {}

    def test_no_view_store_configured_is_fine(self):
        assert retention_variables(_run(), None) == {}

    @pytest.mark.parametrize("junk", [None, "x", 0])
    def test_junk_in_place_of_a_run(self, junk):
        assert retention_variables(junk, _ViewStore(_View({"a": 1}))) in ({}, {"a": 1})

    def test_a_run_with_no_context_at_all(self):
        class Bare:
            id = "run-1"
        assert retention_variables(Bare(), _ViewStore(_View({"a": 1}))) == {"a": 1}


class TestBothCallersUseIt:
    """The helper is worthless if a call site still reads
    ``run.context.variables`` directly — that is the whole defect."""

    def test_reset_test_run_passes_the_resolved_variables(self):
        import inspect
        from app.layer2_application.features.reset_test_run.use_cases import (
            reset_test_run_use_case as mod,
        )
        src = inspect.getsource(mod.ResetTestRunUseCase.execute)
        assert "retention_variables(test_run, self._view_store)" in src
        assert "_cleanup_run_files(\n                test_run.context.variables" not in src

    def test_test_full_workflow_passes_the_resolved_variables(self):
        import inspect
        from app.layer2_application.features.test_full_workflow.use_cases import (
            test_full_workflow_use_case as mod,
        )
        src = inspect.getsource(mod.TestFullWorkflowUseCase.execute)
        assert "retention_variables(test_run, self._view_store)" in src
