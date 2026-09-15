"""A test run's ``$input`` has to reach the PROJECTOR, not just the aggregate.

Step 5b of the node-output redesign removed ``CONTEXT`` from
``HanaRunRepository._DATA_COLUMNS`` (commit 89f55519a, 2026-08-08), and the
column is now physically gone from ``AIW_WORKFLOW_RUNS`` on tenant-1. From that
moment ``run.context.variables`` is not persisted at all: every orchestration
pass rebuilds it from the event log and then fills the gaps from the projector
view (``orchestration_engine.py`` step 1b, ``setdefault``).

``StartRunUseCase`` was given the compensating write — it emits
``emit_variables_seeded`` right after ``run.started`` (``start_run_use_case.py``).
The two TEST paths were not. They still do only this:

    test_run.context.variables = {"input": command.input_data, "artifacts": {}}
    await self.run_repo.asave(test_run)          # CONTEXT is gone → dropped
    self._engine.emit_run_status(test_run, "started", input_data=...)
    await self._engine.orchestrate_start_locked(test_run.id)   # reloads by ID

so ``input`` lives for exactly the three statements between the assignment and
the reload, and every node that resolves ``{{$input.x}}`` afterwards fails with
``Source 'input' not found in context``.

Measured on tenant-1 before the fix, counted by parsing every
``NodeOutputProduced`` payload in ``AIW_EVENT_LOG``:

    run kind                             runs   seed carrying 'input'
    ────────────────────────────────────────────────────────────────
    normal (StartRunUseCase)               76        76
    test run, TEST_MODE=full               85         0
    test run, TEST_MODE=node                5         0

100% against 0%, split exactly along the code boundary. Two live workflows
(``4e1c7382``, ``1124b9af``) were failing on it; the other 88 test runs were
green only because nothing in them read ``$input`` after the first pass — green
by luck, not by contract.

``emit_run_status(..., input_data=...)`` does NOT cover this: the reducer folds
that argument into ``ProjectedRunState.input_data``, a different field from
``ProjectedRunState.variables``, and ``$input`` reads ``variables`` via
``RunLocalsFrame``. So these tests replay the emitted events through the REAL
reducer rather than asserting on the call — asserting the call alone would pass
for an event that lands in the wrong field.

``__credentials`` must never ride along (LAI-19: the sealed map may not enter a
replayable store), so that exclusion is pinned here too — it is the one thing
that must NOT be fixed by "seed everything".
"""
from __future__ import annotations

import contextlib

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.features.test_full_workflow.use_cases.test_full_workflow_use_case import (
    TestFullWorkflowCommand,
    TestFullWorkflowUseCase,
)
from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
)
from app.layer2_application.projection import ProjectedRunState
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_stage_history_repo import (
    MemoryStageHistoryRepository,
)
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import (
    MemoryWorkflowRepository,
)
from app.layer4_frameworks.persistence.memory.memory_workflow_snapshot_repo import (
    MemoryWorkflowSnapshotRepository,
)
from tests.conftest import DummyLogger, DummyOrchestrationEngine


# ─────────────────────────── harness ───────────────────────────


class _ForgetsContext:
    """Run repo that models step 5b: a reload comes back with no ``variables``.

    In-memory repos hand back the SAME object, so ``context.variables`` appears
    to survive a save/load cycle — the one thing production no longer does.
    ``HanaRunRepository._row_to_entity`` reads ``ctx_data = self._from_json(
    row.get("context")) or {}``, and with the column gone that is ``{}``, so
    ``variables=ctx_data.get("variables", {})`` is empty on every reload.

    Without this wrapper a test-run re-run passes for the wrong reason: the
    aggregate still carries the PREVIOUS call's ``input``, and the assertion
    reads an alias rather than anything that was persisted.
    """

    def __init__(self, inner):
        self._inner = inner

    def _forget(self, run):
        if run is not None and run.context is not None:
            run.context.variables = {}
        return run

    def get_by_id(self, id_):
        return self._forget(self._inner.get_by_id(id_))

    async def aget_by_id(self, id_):
        return self._forget(await self._inner.aget_by_id(id_))

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _RecordingEngine(DummyOrchestrationEngine):
    """Records the seed write-sets; ``locked_run`` so the reset branch runs.

    Deliberately NOT a MagicMock: the point is to capture the exact dict handed
    to ``emit_variables_seeded`` so it can be folded through the real reducer.
    """

    def __init__(self):
        super().__init__()
        self.seeds: list[dict] = []
        self.status_events: list[tuple[str, dict | None]] = []

    def locked_run(self, run_id):
        return contextlib.nullcontext()

    def emit_variables_seeded(self, run, state_updates, *, seed_id="variables_seeded"):
        self.seeds.append(dict(state_updates))

    def emit_run_status(self, run, status, *, input_data=None, **kwargs):
        self.status_events.append((status, input_data))


def _replay(engine: _RecordingEngine, run_id: str = "run-1") -> dict:
    """Fold what the engine emitted through the REAL projector reducer.

    This is the assertion that matters. It reproduces, in-process, what the
    orchestration worker does on the next pass: rebuild ``variables`` from the
    log. Anything not in here is not resolvable by ``{{$input.…}}``.
    """
    state = ProjectedRunState(run_id=run_id, workflow_id="wf")
    for status, input_data in engine.status_events:
        state = state.apply(EventLogEnvelope(
            event_id=f"status-{status}", run_id=run_id, topic="run_status_changed",
            event_type="RunStatusChanged", timestamp="2026-08-11T07:19:42Z",
            payload={"run_id": run_id, "workflow_id": "wf", "status": status,
                     "input_data": input_data or {}},
        ))
    for i, write_set in enumerate(engine.seeds):
        state = state.apply(EventLogEnvelope(
            event_id=f"seed-{i}", run_id=run_id, topic="node_output_produced",
            event_type="NodeOutputProduced", timestamp="2026-08-11T07:19:42Z",
            payload={"run_id": run_id, "workflow_id": "wf",
                     "node_id": "__bootstrap__", "state_updates": write_set},
        ))
    return dict(state.variables)


def _workflow(repo: MemoryWorkflowRepository) -> Workflow:
    """Trigger → task, the shape of the two workflows that broke live."""
    wf = Workflow(
        id="wf", name="wf",
        nodes=[
            WorkflowNode(id="trigger-1", name="1. Start", node_type=NodeType.TRIGGER),
            WorkflowNode(
                id="comment_start", name="1b. Jira: Comment (started)",
                node_type=NodeType.TASK, worker_type="jira_add_comment",
                input_binding={"issue_key": "{{$input.ticket_key}}"},
            ),
        ],
        edges=[WorkflowEdge(source_node_id="trigger-1", target_node_id="comment_start")],
    )
    repo.save(wf)
    return wf


def _use_case(workflow_repo, run_repo, engine, *, cipher=None, vault=None):
    return TestFullWorkflowUseCase(
        logger=DummyLogger(),
        workflow_repo=workflow_repo,
        workflow_snapshot_repo=MemoryWorkflowSnapshotRepository(),
        run_repo=run_repo,
        task_repo=MemoryTaskRepository(),
        orchestration_engine=engine,
        stage_recorder=StageRecorder(repository=MemoryStageHistoryRepository()),
        credential_cipher=cipher,
        vault_credential_client=vault,
    )


async def _run_test_full(input_data, *, times=1, cipher=None, vault=None):
    """Drive the real use case ``times`` times; return (engine, run_repo, run_id).

    ``times=1`` exercises the fresh branch, ``times=2`` the reuse/reset branch —
    the same two-branch split ``test_test_full_credentials.py`` covers, and the
    branch a permanent test run is on for every re-run after its first.
    """
    workflow_repo = MemoryWorkflowRepository()
    run_repo = MemoryRunRepository()
    engine = _RecordingEngine()
    _workflow(workflow_repo)
    # The use case reads through the forgetful wrapper (production); assertions
    # read the inner repo, which still holds whatever the call left in memory.
    uc = _use_case(workflow_repo, _ForgetsContext(run_repo), engine,
                   cipher=cipher, vault=vault)
    res = None
    for _ in range(times):
        engine.seeds.clear()
        engine.status_events.clear()
        res = await uc.execute(
            TestFullWorkflowCommand(workflow_id="wf", input_data=input_data)
        )
        assert res.error == "", res.error
    return engine, run_repo, res.run_id


# ───────────────────── /test/full — both branches ─────────────────────


class TestTheTestFullPath:
    """``POST /workflows/{id}/test/full`` — the button business users press."""

    @pytest.mark.asyncio
    async def test_a_fresh_test_run_puts_input_in_the_projector(self):
        engine, _, run_id = await _run_test_full({"ticket_key": "MCKES-360"})

        variables = _replay(engine, run_id)
        assert variables.get("input") == {"ticket_key": "MCKES-360"}, (
            "the fresh branch writes context.variables['input'] and saves — but "
            "CONTEXT is gone, so without a seed event the very next reload "
            "(orchestrate_start by id) loses it"
        )

    @pytest.mark.asyncio
    async def test_the_reset_branch_puts_input_in_the_projector(self):
        # A permanent test run is on this branch for every re-run after the
        # first — i.e. essentially always, for a workflow anyone is iterating on.
        engine, _, run_id = await _run_test_full(
            {"ticket_key": "MCKES-360"}, times=2,
        )

        variables = _replay(engine, run_id)
        assert variables.get("input") == {"ticket_key": "MCKES-360"}

    @pytest.mark.asyncio
    async def test_artifacts_reaches_the_projector_too(self):
        # ``$artifacts`` was lost by exactly the same mechanism and is just as
        # silent — an empty dict resolves to "nothing here" rather than an error.
        for times in (1, 2):
            engine, _, run_id = await _run_test_full({"a": 1}, times=times)
            assert "artifacts" in _replay(engine, run_id), f"times={times}"

    @pytest.mark.asyncio
    async def test_the_seed_carries_everything_the_aggregate_holds(self):
        # The invariant, stated once: after step 5b the projector is the only
        # carrier, so anything the use case leaves on the aggregate and does not
        # seed is simply lost. Pinning the SET rather than the two known keys is
        # what makes a third key added later fail here instead of in production.
        engine, run_repo, run_id = await _run_test_full({"ticket_key": "X"}, times=2)

        on_aggregate = {
            k for k in run_repo.get_by_id(run_id).context.variables
            if k != "__credentials"
        }
        in_projector = set(_replay(engine, run_id))
        assert on_aggregate <= in_projector, (
            f"dropped on reload: {sorted(on_aggregate - in_projector)}"
        )

    @pytest.mark.asyncio
    async def test_an_empty_input_is_still_a_seeded_input(self):
        # Running with no input at all is legal. ``{}`` must arrive as ``{}`` —
        # ``$input`` resolving to an empty dict is a different, debuggable
        # outcome from ``$input`` not existing.
        engine, _, run_id = await _run_test_full({})

        variables = _replay(engine, run_id)
        assert "input" in variables
        assert variables["input"] == {}

    @pytest.mark.asyncio
    async def test_run_input_data_stays_raw_on_the_aggregate(self):
        # ``run.input_data`` (column INPUT_DATA) is a separate carrier and must
        # keep the raw payload — the variable-suggestion dropdown reads it. The
        # live failing run proved the two are independent: INPUT_DATA held
        # {"ticket_key": "MCKES-360"} while $input was unresolvable.
        _, run_repo, run_id = await _run_test_full({"ticket_key": "MCKES-360"}, times=2)

        assert run_repo.get_by_id(run_id).input_data == {"ticket_key": "MCKES-360"}


# ───────────────────────── size — small to large ─────────────────────────


class TestItHoldsAtSize:
    """The seed is one event; a large input must not be truncated or reshaped.

    ``run.started`` was deliberately changed to carry a REFERENCE rather than
    the payload (86.30 MB of AIW_EVENT_LOG on tenant-1). The seed is a second
    carrier of the same bytes, so its size behaviour is worth pinning rather
    than assuming — and a test-run input is author-typed, so it is small in
    practice and large only when someone pastes a real payload.
    """

    @pytest.mark.parametrize("n_keys", [1, 10, 400, 2000])
    @pytest.mark.asyncio
    async def test_every_key_survives(self, n_keys):
        payload = {f"k{i}": f"v{i}" for i in range(n_keys)}

        engine, _, run_id = await _run_test_full(payload, times=2)

        assert _replay(engine, run_id).get("input") == payload

    @pytest.mark.asyncio
    async def test_a_nested_payload_survives_unflattened(self):
        # A webhook payload — the real shape tenant-1 feeds this workflow — is
        # deeply nested. The seed must carry it verbatim; flattening it would
        # break ``{{$input.issue.fields.summary}}`` while looking successful.
        payload = {
            "ticket_key": "MCKES-360",
            "issue": {"fields": {"summary": "x" * 4096,
                                 "labels": [f"l{i}" for i in range(200)]}},
            "changelog": {"items": [{"field": "status", "to": str(i)}
                                    for i in range(500)]},
        }

        engine, _, run_id = await _run_test_full(payload, times=2)

        assert _replay(engine, run_id).get("input") == payload

    @pytest.mark.asyncio
    async def test_the_seed_is_not_shared_mutable_state(self):
        # ``emit_variables_seeded`` hands a dict the caller still owns. If the
        # use case passed ``context.variables`` itself, a later mutation (the
        # credential seeding two statements down) would retroactively change an
        # already-"published" event — the kind of aliasing that only shows up
        # under a real publisher.
        payload = {"ticket_key": "MCKES-360"}
        engine, run_repo, run_id = await _run_test_full(payload, times=2)
        before = _replay(engine, run_id)
        assert engine.seeds, "nothing was seeded — this assertion proves nothing"

        run_repo.get_by_id(run_id).context.variables["injected"] = "later"

        assert _replay(engine, run_id) == before


# ─────────────────────────── /node/test ───────────────────────────


class _Recorded:
    """Adapter so a MagicMock engine's calls can go through ``_replay``."""

    def __init__(self, seeds, status_events=()):
        self.seeds = list(seeds)
        self.status_events = list(status_events)


def _seeds_of(engine_mock) -> _Recorded:
    return _Recorded([
        dict(call.args[1] if len(call.args) > 1 else call.kwargs["state_updates"])
        for call in engine_mock.emit_variables_seeded.call_args_list
    ])


class TestTheTestNodePath:
    """``POST /workflows/{id}/nodes/{nid}/test`` — same gap, different shape.

    Here the seed is not missing, it is EARLY: ``_reseed_projector`` emits the
    write-set, and only afterwards does ``execute`` apply ``command.input_data``
    and ``command.mock_variables`` to ``context.variables``. Harmless while
    CONTEXT persisted the aggregate; after step 5b those two assignments write
    to memory that the next pass throws away.
    """

    @staticmethod
    def _deps(handler_result=None):
        from tests.conftest import DummyEventPublisher, DummyStageRecorder
        from tests.unit.test_test_node import MemRepo, _make_engine, _make_handler_registry

        return {
            "logger": DummyLogger(),
            "workflow_repo": MemRepo(),
            "workflow_snapshot_repo": MemoryWorkflowSnapshotRepository(),
            "run_repo": MemRepo(),
            "task_repo": MemRepo(),
            "stage_recorder": DummyStageRecorder(),
            "event_publisher": DummyEventPublisher(),
        }, _make_engine(), _make_handler_registry(handler_result)

    @classmethod
    async def _run_test_node(cls, *, input_data=None, mock_variables=None,
                             times=1, handler_result=None):
        from app.layer2_application.features.test_node.use_cases.test_node_use_case import (
            TestNodeCommand, TestNodeUseCase,
        )
        from tests.unit.test_test_node import _make_workflow

        deps, engine, registry = cls._deps(handler_result)
        deps["workflow_repo"].save(_make_workflow())
        inner_runs = deps["run_repo"]
        uc = TestNodeUseCase(
            handler_registry=registry, orchestration_engine=engine,
            **{**deps, "run_repo": _ForgetsContext(inner_runs)},
        )
        res = None
        for _ in range(times):
            engine.emit_variables_seeded.reset_mock()
            res = await uc.execute(TestNodeCommand(
                workflow_id="wf-1", node_id="compute-1",
                input_data=input_data or {}, mock_variables=mock_variables or {},
            ))
        return engine, inner_runs, res

    @pytest.mark.asyncio
    @pytest.mark.parametrize("times", [1, 2])
    async def test_command_input_data_reaches_the_projector(self, times):
        engine, _, res = await self._run_test_node(
            input_data={"ticket_key": "MCKES-360"}, times=times,
        )

        variables = _replay(_seeds_of(engine), res.run_id)
        assert variables.get("input") == {"ticket_key": "MCKES-360"}, (
            "input_data is applied AFTER _reseed_projector has already emitted"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("times", [1, 2])
    async def test_mock_variables_reach_the_projector(self, times):
        # Mock variables are the whole point of single-node testing — they stand
        # in for upstream nodes that have not run. Losing them on reload turns
        # "test this node in isolation" into "test this node against nothing".
        engine, _, res = await self._run_test_node(
            mock_variables={"upstream": {"value": 42}}, times=times,
        )

        variables = _replay(_seeds_of(engine), res.run_id)
        assert variables.get("upstream") == {"value": 42}

    @pytest.mark.asyncio
    async def test_the_node_under_test_still_gets_its_own_seed(self):
        # ``_publish_inline_output_to_view`` seeds the tested node's output with
        # its own seed_id, and it fires only when the handler actually produced
        # state_updates — so the handler must return some, or this asserts
        # nothing. Reordering the reseed must not swallow that second seed: it
        # is what the FE reads to show the node's result, and both seeds have to
        # survive together (distinct seed_ids → distinct deterministic
        # event_ids; a collision would silently drop one).
        from app.layer2_application.node_handlers.base import NodeHandlerResult

        engine, _, res = await self._run_test_node(
            input_data={"a": 1},
            handler_result=NodeHandlerResult(
                state_updates={"compute-1": {"result": 50}},
            ),
        )

        seed_ids = [
            call.kwargs.get("seed_id", "variables_seeded")
            for call in engine.emit_variables_seeded.call_args_list
        ]
        assert "node_output:compute-1" in seed_ids
        assert "variables_seeded" in seed_ids


# ──────────────────── the chokepoint that keeps it safe ────────────────────


class TestCredentialsCannotRideTheSeed:
    """LAI-19: the sealed credential map must never enter a replayable store.

    "Seed the whole ``variables`` dict" is the obvious shape of this fix, and
    ``__credentials`` is the one key it must not sweep up. Filtered inside
    ``emit_variables_seeded`` rather than at each caller: there are five
    publishers, and a sixth that forgets would be a silent leak into
    ``AIW_EVENT_LOG`` — permanent, and invisible until someone reads the log.

    Driven against the real method with a duck-typed ``self`` so the assertion
    is about the published EVENT, not about a caller's argument.
    """

    @staticmethod
    def _emit(state_updates):
        from app.layer1_domain.entities.workflow_run import WorkflowRun
        from app.layer2_application.orchestration.orchestration_engine import (
            OrchestrationEngine,
        )

        class _Spy:
            def __init__(self):
                self.published = []

            def _publish_now(self, topic, event):
                self.published.append(event)

        spy = _Spy()
        run = WorkflowRun(id="run-1", workflow_id="wf", workflow_version="1")
        OrchestrationEngine.emit_variables_seeded(spy, run, state_updates)
        return spy.published

    def test_the_credential_map_is_stripped_from_the_event(self):
        published = self._emit({
            "input": {"ticket_key": "MCKES-360"},
            "__credentials": {"c1": {"type": "bearer", "config": {"token": "s"}}},
        })

        assert len(published) == 1
        assert published[0].state_updates == {"input": {"ticket_key": "MCKES-360"}}

    def test_a_write_set_of_only_credentials_publishes_nothing(self):
        # Not "publish an empty event" — an event whose write-set is empty still
        # costs a row in AIW_EVENT_LOG and contributes nothing to the view.
        assert self._emit({"__credentials": {"c1": {}}}) == []

    def test_the_callers_dict_is_not_mutated(self):
        # The caller keeps using ``context.variables`` after this returns; a
        # filter that popped in place would delete the run's own credentials.
        write_set = {"input": {"a": 1}, "__credentials": {"c1": {}}}

        self._emit(write_set)

        assert "__credentials" in write_set
