"""``RunStatusChanged(started)`` carries a REFERENCE, not the input payload.

Measured on tenant-1: `AIW_EVENT_LOG` holds **86.30 MB** of `input_data` across
5,939 `started` events — p50 125,161 B, max 341,296 B, and **100 of the 100
largest sampled were raw values**, not references. The stated reason
(`start_run_use_case.py`) is that the projector's `input_data` field must be
rebuildable from the log. A reference rebuilds it just as well at ~200 bytes.

The change costs no new I/O: `store_data("input", …)` had already written those
exact bytes to the file service for `variables["input"]` a few statements
earlier, and the event was re-serialising them from `req.input_data`.

Spec: `solace/specs/wcp-node-output-storage-redesign.md` §16.

Two things these tests pin, because each fails silently:

* the event must NOT contain the payload — a regression here is invisible except
  as event-log growth, which nobody watches;
* `run.input_data` must STAY raw — it is the fallback `$input` reads when a
  file_ref's `extracted` cache is empty (`scope_providers.py:372`), and turning
  it into a reference empties the variable-suggestion dropdown with no error.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.layer1_domain.entities.data_reference import build_file_ref, is_file_ref


_BIG_INPUT = {"payload": [{"i": i, "blob": "x" * 200} for i in range(400)]}


class _Store:
    """Stands in for DataStoreService: hands back a file_ref, as production does."""

    def __init__(self):
        self.stored = []

    async def store_data(self, key, value):
        self.stored.append((key, value))
        return build_file_ref(
            file_id="file-input-1", row_count=len(value) if isinstance(value, list) else 1,
            columns=["payload"], column_types={"payload": "json"}, value_kind="object",
        )


@pytest.fixture
def start_run_harness(logger, workflow_repo, run_repo, task_repo):
    """Drive the real ``StartRunUseCase`` with a MagicMock engine.

    The engine is mocked on purpose: what is under test is the ARGUMENT handed
    to ``emit_run_status``, and a real engine would run the whole orchestration
    to observe one kwarg. Repos are the real in-memory ones so ``run`` is a real
    ``WorkflowRun`` and ``run.input_data`` is whatever production would store.
    """
    from unittest.mock import AsyncMock
    from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
    from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
        StartRunCommand, StartRunUseCase,
    )
    from app.layer2_application.events.stage_recorder import StageRecorder
    from app.layer4_frameworks.persistence.memory.memory_stage_history_repo import (
        MemoryStageHistoryRepository,
    )

    async def _run(input_data, store=_Store):
        wf = Workflow(
            id="wf-1", name="wf", version="1",
            nodes=[WorkflowNode(id="n1", name="n1", node_type="INPUT")],
            edges=[],
        )
        await workflow_repo.asave(wf)

        # AsyncMock base so every awaited engine call (``drain_events``,
        # ``orchestrate_start``) works without enumerating them; the emit_*
        # methods are synchronous, so they are overridden back to MagicMock.
        engine = AsyncMock()
        for sync_method in ("emit_run_status", "emit_variables_seeded",
                            "emit_node_output"):
            setattr(engine, sync_method, MagicMock())
        the_store = store() if isinstance(store, type) else store

        uc = StartRunUseCase(
            logger=logger, workflow_repo=workflow_repo, run_repo=run_repo,
            task_repo=task_repo, orchestration_engine=engine,
            stage_recorder=StageRecorder(repository=MemoryStageHistoryRepository()),
            data_store_service=the_store,
        )
        await uc.execute(StartRunCommand(
            workflow_id="wf-1", input_data=input_data, tenant_id="t1"))
        # The run is the first positional arg of the emit itself — no need to
        # go back through the repo and guess at the result's shape.
        started = [c for c in engine.emit_run_status.call_args_list
                   if len(c.args) > 1 and c.args[1] == "started"]
        assert started, "start_run never emitted a 'started' status"
        return started[0].args[0], engine, the_store

    return _run


def _emitted_started(engine: MagicMock):
    """The kwargs of the single emit_run_status(..., 'started', ...) call."""
    calls = [c for c in engine.emit_run_status.call_args_list
             if len(c.args) > 1 and c.args[1] == "started"]
    assert len(calls) == 1, f"expected exactly one started emit, got {len(calls)}"
    return calls[0].kwargs


@pytest.mark.asyncio
class TestStartedEventCarriesARef:

    async def test_event_carries_a_file_ref_not_the_payload(self, start_run_harness):
        run, engine, _store = await start_run_harness(_BIG_INPUT)
        sent = _emitted_started(engine)["input_data"]

        assert is_file_ref(sent), (
            f"the started event still carries {type(sent).__name__} — the payload "
            "is riding the event log again (86.30 MB of it, measured)"
        )
        blob = json.dumps(sent, default=str)
        assert "xxxxxxxxxx" not in blob, "payload bytes leaked into the event"
        assert len(blob.encode("utf-8")) < 4096, (
            f"the reference is {len(blob)} B — a descriptor, not a payload, "
            "is the whole point"
        )

    async def test_it_reuses_the_ref_already_stored_no_second_write(
            self, start_run_harness):
        """``variables['input']`` and the event must be the SAME ref. A second
        ``store_data`` would double the writes this change exists to remove."""
        run, engine, store = await start_run_harness(_BIG_INPUT)
        sent = _emitted_started(engine)["input_data"]

        assert len(store.stored) == 1, (
            f"store_data called {len(store.stored)}x — the ref already existed"
        )
        assert sent == run.context.variables["input"]

    async def test_the_aggregate_input_data_stays_RAW(self, start_run_harness):
        """The field with actual readers. ``$input`` falls back to it when a
        file_ref's ``extracted`` cache is empty; a ref here empties the
        suggestion dropdown and raises nothing."""
        run, engine, _store = await start_run_harness(_BIG_INPUT)

        assert not is_file_ref(run.input_data)
        assert run.input_data == _BIG_INPUT

    async def test_without_a_store_it_degrades_to_the_old_behaviour(
            self, start_run_harness):
        """No store wired (tests, degraded deploys) ⇒ byte-identical to before,
        never an empty dict."""
        run, engine, _store = await start_run_harness(_BIG_INPUT, store=None)
        sent = _emitted_started(engine)["input_data"]

        assert sent == _BIG_INPUT

    async def test_empty_input_still_emits_a_dict(self, start_run_harness):
        """The projector folds ``dict(payload.get('input_data') or {})`` — the
        shape must stay a dict for every input, including none at all."""
        run, engine, _store = await start_run_harness({})
        sent = _emitted_started(engine)["input_data"]
        assert isinstance(sent, dict)
