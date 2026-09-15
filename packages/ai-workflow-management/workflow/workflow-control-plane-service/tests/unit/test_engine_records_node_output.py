"""The AIW_RUN_NODE_OUTPUT mirror, at the ONE place every node output passes.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §17.8.

Replaces ``test_complete_task_records_node_output.py``. The mirror used to sit
on ``CompleteTaskUseCase``, which only worker completions reach. Measured on
tenant-1 on 2026-08-08:

* **152 of 420** completed tasks were ``builtin:*`` — trigger, switch, output,
  set_variable, workflow, loop — with a real ``file_ref`` output and **zero**
  rows, because builtins finish in ``NodeExecutionRunner._run_immediate`` and
  never touch ``CompleteTaskUseCase``.
* **51 of 88** distinct ``schema_hash`` values on the wire had no
  ``AIW_SCHEMA_DEF`` entry, for the same reason — ``save()`` is what writes the
  dictionary.

That second number is the dangerous one: a hash with no dictionary entry cannot
be expanded back into ``columns``/``column_types``, and ``CsvConverter._unflatten``
does not fail on a missing schema — it falls back to the ``_try_parse_value``
heuristic and **guesses the types**. Silently. So the gap does not present as an
error; it presents as a value that changed shape.

The mirror now lives on ``OrchestrationEngine.apply_state_updates_as_files``,
which both paths call.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.data_reference import build_file_ref
from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import (
    GraphTopology,
    RunContext,
    WorkflowRun,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.orchestration.orchestration_engine import (
    OrchestrationEngine,
)


class _SpyRepo:
    def __init__(self, boom: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._boom = boom

    def save(self, **kw):
        self.calls.append(kw)
        if self._boom is not None:
            raise self._boom
        return 1


def _engine(repo) -> OrchestrationEngine:
    """A bare engine carrying only what the mirror and its caller touch.

    ``__new__`` on purpose — the same construction the phase-0 contract tests
    use, and the one that caught a real bug in this change: reading
    ``self._node_output_repo`` directly raised ``AttributeError`` *before* the
    try block, which would have made a mirror that must never fail a run into
    one that always could.
    """
    eng = OrchestrationEngine.__new__(OrchestrationEngine)
    eng._node_output_repo = repo
    eng._event_publisher = None      # _publish_now becomes a no-op
    eng._data_store_service = None   # _to_file_ref returns a ref unchanged
    return eng


def _workflow(*nodes: tuple[str, str]) -> Workflow:
    return Workflow(
        id="wf", name="WF",
        nodes=[WorkflowNode(id=nid, name=name, node_type=NodeType.TASK)
               for nid, name in nodes],
        edges=[],
    )


def _run() -> WorkflowRun:
    return WorkflowRun(id="r1", workflow_id="wf", workflow_version="1",
                       context=RunContext(topology=GraphTopology()))


def _ref(file_id: str = "f1") -> dict:
    return build_file_ref(file_id=file_id, row_count=2, columns=["a", "b"],
                          column_types={"a": "string", "b": "number"})


class _Task:
    def __init__(self, generation=0, iteration=0, retry=0) -> None:
        self.run_generation = generation
        self.iteration_index = iteration
        self.retry_count = retry


class TestItRecordsTheDescriptor:
    def test_a_file_ref_is_written_with_the_full_identity(self):
        repo = _SpyRepo()
        _engine(repo)._record_node_output(
            {"n1": _ref()}, _run(), _workflow(("n1", "First")), task=_Task())
        assert len(repo.calls) == 1
        call = repo.calls[0]
        assert call["run_id"] == "r1"
        assert call["node_id"] == "n1"
        assert call["node_name"] == "First"
        assert call["file_ref"]["file_id"] == "f1"

    def test_the_stamps_come_from_the_task_not_a_default(self):
        """(generation, iteration, attempt) are three fifths of the primary key.
        Defaulting them would make a re-run's first attempt overwrite the
        original's — the G-guard exists to stop exactly that."""
        repo = _SpyRepo()
        _engine(repo)._record_node_output(
            {"n1": _ref()}, _run(), _workflow(("n1", "First")),
            task=_Task(generation=2, iteration=3, retry=1))
        call = repo.calls[0]
        assert (call["generation"], call["iteration"], call["attempt"]) == (2, 3, 1)

    def test_no_task_falls_back_to_the_run_generation(self):
        """Not every caller has a task. Falling back to the RUN's generation
        keeps a re-run's rows distinct; iteration/attempt have no meaning
        without a task, so they are zero rather than guessed."""
        repo = _SpyRepo()
        run = _run()
        run.generation = 4
        _engine(repo)._record_node_output(
            {"n1": _ref()}, run, _workflow(("n1", "First")))
        call = repo.calls[0]
        assert (call["generation"], call["iteration"], call["attempt"]) == (4, 0, 0)

    def test_every_node_in_the_write_set_gets_its_own_row(self):
        """``state_updates`` is a dict, not one node — the builtin path routinely
        writes several keys in a single call."""
        repo = _SpyRepo()
        _engine(repo)._record_node_output(
            {"n1": _ref("f1"), "n2": _ref("f2")},
            _run(), _workflow(("n1", "First"), ("n2", "Second")), task=_Task())
        assert {c["node_id"] for c in repo.calls} == {"n1", "n2"}
        assert {c["file_ref"]["file_id"] for c in repo.calls} == {"f1", "f2"}


class TestWhatItDeclinesToWrite:
    def test_an_inline_output_is_skipped(self):
        """``code`` / ``approval_flow`` under the size ceiling keep their raw
        value, so there is no file for a descriptor to point at and FILE_ID is
        NOT NULL. Skipping is correct — this is the deliberate 3.7%."""
        repo = _SpyRepo()
        _engine(repo)._record_node_output(
            {"n1": {"result": [1, 2, 3]}}, _run(), _workflow(("n1", "First")),
            task=_Task())
        assert repo.calls == []

    def test_no_repo_wired_is_a_silent_no_op(self):
        _engine(None)._record_node_output(
            {"n1": _ref()}, _run(), _workflow(("n1", "First")), task=_Task())

    def test_an_engine_without_the_attribute_does_not_raise(self):
        """A ``__new__`` engine has no ``_node_output_repo`` at all. Found by
        the existing phase-0 tests when this was a bare attribute read."""
        eng = OrchestrationEngine.__new__(OrchestrationEngine)
        eng._record_node_output({"n1": _ref()}, _run(),
                                _workflow(("n1", "First")), task=_Task())

    def test_an_empty_write_set_writes_nothing(self):
        repo = _SpyRepo()
        _engine(repo)._record_node_output({}, _run(), _workflow(), task=_Task())
        assert repo.calls == []


class TestItCannotFailTheRun:
    """The authoritative write — the ``NodeOutputProduced`` event — has already
    been published when this runs. A broken mirror costs a row, never a run."""

    def test_a_repo_error_is_logged_and_swallowed(self, caplog):
        import logging as _logging
        with caplog.at_level(_logging.WARNING):
            _engine(_SpyRepo(boom=RuntimeError("hana said no")))._record_node_output(
                {"n1": _ref()}, _run(), _workflow(("n1", "First")), task=_Task())
        assert "hana said no" in caplog.text
        assert "n1" in caplog.text, "the log must name the node that lost its row"

    @pytest.mark.parametrize("boom", [
        RuntimeError("db down"), ValueError("bad row"), KeyError("missing"),
        TypeError("wrong shape"), Exception("generic"),
    ])
    def test_every_failure_kind_is_contained(self, boom):
        _engine(_SpyRepo(boom=boom))._record_node_output(
            {"n1": _ref()}, _run(), _workflow(("n1", "First")), task=_Task())

    def test_one_bad_node_does_not_stop_the_next(self):
        """A per-node try, not a try around the loop: a schema that trips HANA
        on one node must not cost every other node in the same write-set."""
        class _FlakyRepo(_SpyRepo):
            def save(self, **kw):
                self.calls.append(kw)
                if kw["node_id"] == "n1":
                    raise RuntimeError("only this one")
                return 1

        repo = _FlakyRepo()
        _engine(repo)._record_node_output(
            {"n1": _ref("f1"), "n2": _ref("f2")},
            _run(), _workflow(("n1", "First"), ("n2", "Second")), task=_Task())
        assert {c["node_id"] for c in repo.calls} == {"n1", "n2"}


@pytest.mark.asyncio
class TestTheMirrorIsACTUALLYWIRED:
    """The reason this file exists rather than a unit test of the helper.

    ``DROP CONTEXT``'s topology fix had nine green tests while the call that
    applied it was deleted, because every test invoked the helper directly. So
    the wiring gets driven through the real ``apply_state_updates_as_files`` —
    the method both the worker path and the builtin path call.
    """

    async def test_apply_state_updates_as_files_writes_the_row(self):
        repo = _SpyRepo()
        eng = _engine(repo)
        run, wf = _run(), _workflow(("n1", "First"))
        await eng.apply_state_updates_as_files(
            {"n1": _ref()}, run, wf, source_node_id="n1", task=_Task(generation=1),
        )
        assert len(repo.calls) == 1, (
            "apply_state_updates_as_files must mirror — this is the call the "
            "builtin path depends on for its rows AND its schema-dictionary "
            "entries"
        )
        assert repo.calls[0]["node_id"] == "n1"
        assert repo.calls[0]["generation"] == 1

    async def test_an_inline_write_set_still_publishes_but_stores_nothing(self):
        repo = _SpyRepo()
        eng = _engine(repo)
        await eng.apply_state_updates_as_files(
            {"n1": {"result": 1}}, _run(), _workflow(("n1", "First")),
            source_node_id="n1", task=_Task(),
        )
        assert repo.calls == []

    async def test_an_empty_update_returns_before_anything(self):
        repo = _SpyRepo()
        await _engine(repo).apply_state_updates_as_files(
            {}, _run(), _workflow(), source_node_id="n1")
        assert repo.calls == []
