"""Credentials resolve at EGRESS, and a sub-workflow is not a special case.

SA-2287 (B3) moved resolution out of StartRun. This file is the proof, and it
is organised around the properties that could regress rather than around the
classes that implement them:

* the original defect stays fixed — a node INSIDE a sub-workflow authenticates;
* nothing reaches the vault before a node needs it, which is the whole point;
* the worker contract is byte-identical, because a worker change was the one
  thing B3 promised not to require;
* a refusal lands on the node that needed the secret, not on the run;
* a run that started BEFORE this change keeps working.

The predecessor of this file tested the opposite design (resolve every ref at
child-run start) and every one of its assertions was correct for that design.
It is replaced, not deleted, because the BEHAVIOUR it pinned — "a credential
node inside a sub-workflow reaches its worker with a secret" — is still the
headline requirement and is re-asserted below through the new path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import (
    GraphTopology,
    RunContext,
    WorkflowRun,
)
from app.layer1_domain.exceptions import (
    CredentialNotFound,
    CredentialResolveAuthError,
    ExpressionError,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.resolution_scope import ResolutionScopeBuilder
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.credentials import (
    RunCredentialProvider,
    RunCredentialSeeder,
)
from app.layer2_application.node_handlers.task_handler import TaskNodeHandler
from app.layer2_application.node_handlers.workflow_handler import WorkflowNodeHandler
from app.layer2_application.orchestration.child_workflow_coordinator import (
    ChildWorkflowCoordinator,
)
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
)
from app.layer4_frameworks.persistence.memory.memory_run_repo import (
    MemoryRunRepository,
)

CRED_REF = "2823fa09-d348-4c7c-9a04-6397e4782ced"
OTHER_REF = "9f1d0c77-1111-2222-3333-444455556666"
VAULT_ENTRY = {"type": "bearer", "config": {"token": "s3cr3t"}}


class FakeVault:
    """Records every resolve so the tests can count network calls."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self._error = error

    async def resolve_by_id(self, tenant_id, credential_id, user_id=None):
        self.calls.append((tenant_id, credential_id, user_id))
        if self._error is not None:
            raise self._error
        # expires_at is returned by the real client and dropped on the way in —
        # asserted below, because the map shape IS the worker contract.
        return {"type": "bearer", "config": {"token": "s3cr3t"},
                "expires_at": "2027-01-01T00:00:00+00:00"}


def _resolver() -> VariableResolver:
    return VariableResolver(
        strategies=[MetadataResolverStrategy(), TraversalResolverStrategy()]
    )


def _task_node(node_id: str = "call_api", ref: str | None = CRED_REF) -> WorkflowNode:
    return WorkflowNode(
        id=node_id, name=node_id, node_type=NodeType.TASK,
        worker_type="http-request-worker",
        config={"credential_ref": ref} if ref else {},
    )


def _child_workflow(*, with_credential: bool = True) -> Workflow:
    return Workflow(
        id="child-wf", name="child",
        nodes=[_task_node(ref=CRED_REF if with_credential else None)],
        edges=[],
    )


def _parent_workflow() -> Workflow:
    return Workflow(
        id="parent-wf", name="parent",
        nodes=[
            WorkflowNode(
                id="sub", name="sub", node_type=NodeType.WORKFLOW,
                config={"workflow_id": "child-wf", "input_mapping": {},
                        "on_failure": "fail"},
            )
        ],
        edges=[],
    )


def _run(
    workflow: Workflow,
    *,
    metadata: dict | None = None,
    sealed: dict | None = None,
) -> WorkflowRun:
    """A run started the way an event trigger starts one: no user behind it.

    ``sealed`` writes the reserved key directly — it models BOTH the caller's
    ``runtime_credentials`` and a pre-B3 run's fully-resolved map, which after
    this change are the same thing on the wire.
    """
    variables: dict = {"input": {}, "artifacts": {}}
    if sealed is not None:
        variables["__credentials"] = dict(sealed)
    run = WorkflowRun(
        id="parent-run", workflow_id=workflow.id, status=RunStatus.RUNNING,
        input_data={},
        context=RunContext(
            variables=variables, topology=GraphTopology.from_workflow(workflow),
        ),
        tenant_id="system", metadata=dict(metadata or {}),
    )
    run.root_run_id = run.id
    return run


def _handler(vault=None, run_repo=None, cipher=None) -> TaskNodeHandler:
    return TaskNodeHandler(
        _resolver(),
        credential_provider=RunCredentialProvider(
            vault_client=vault, cipher=cipher, run_repo=run_repo,
        ),
    )


async def _dispatch(node, run, workflow, *, vault=None, run_repo=None, cipher=None):
    """Run the REAL TaskNodeHandler and return the task it would dispatch."""
    result = await _handler(vault, run_repo, cipher).handle(
        node, run, workflow, GraphTopology.from_workflow(workflow), None,
        TaskFactory(),
    )
    return result.tasks_to_dispatch[0]


def _scope_builder(provider, refs=(CRED_REF,)) -> ResolutionScopeBuilder:
    return ResolutionScopeBuilder(
        resolve_credentials=lambda r, w: provider.for_refs(list(refs), r),
    )


def _engine_stub():
    engine = MagicMock()
    engine._is_executor_mode = MagicMock(return_value=True)
    engine._enter_run_scope = MagicMock(return_value=("t1", "t2"))
    engine._exit_run_scope = MagicMock()
    engine.publish_event = MagicMock()
    engine.emit_run_status = MagicMock()
    engine.emit_variables_seeded = MagicMock()
    engine.check_run_completion = AsyncMock()
    engine.drain_events = AsyncMock()
    return engine


async def _start_child(*, run_repo=None, parent_run=None, child_workflow=None):
    """Drive the REAL WorkflowNodeHandler + ChildWorkflowCoordinator."""
    parent_workflow = _parent_workflow()
    child_workflow = child_workflow or _child_workflow()
    run = parent_run or _run(parent_workflow)
    run_repo = run_repo or MemoryRunRepository()
    if run_repo.get_by_id(run.id) is None:
        run_repo.save(run)

    workflow_repo = MagicMock()
    workflow_repo.aget_by_latest_publish_version = AsyncMock(
        return_value=child_workflow
    )
    node = parent_workflow.nodes[0]
    topology = GraphTopology.from_workflow(parent_workflow)
    handler_result = await WorkflowNodeHandler(_resolver()).handle(
        node, run, parent_workflow, topology, None, TaskFactory()
    )
    coordinator = ChildWorkflowCoordinator(
        engine=_engine_stub(), run_repo=run_repo, task_repo=AsyncMock(),
        workflow_repo=workflow_repo, workflow_snapshot_repo=MagicMock(),
    )
    await coordinator.start(
        node, handler_result.tasks_to_dispatch[0], run, parent_workflow,
        topology, handler_result,
    )
    child_run = run_repo.get_by_id(run.context.child_runs[node.id])
    assert child_run is not None, "the coordinator did not persist a child run"
    return child_run, child_workflow


class TestTheNodeInsideASubWorkflowStillAuthenticates:
    """The headline requirement, carried over from the eager design.

    Why it was ever broken: ``TaskNodeHandler`` reads the credential for the
    run it is executing and never walks up the parent chain. Under the eager
    design the child run had to be seeded separately, and was not. Under B3
    there is nothing to seed — the node asks at dispatch, so a child and a root
    ask in exactly the same way.
    """

    async def test_the_worker_task_inside_the_child_receives_the_credential(self):
        vault = FakeVault()
        run_repo = MemoryRunRepository()
        child_run, child_workflow = await _start_child(run_repo=run_repo)

        task = await _dispatch(
            child_workflow.nodes[0], child_run, child_workflow,
            vault=vault, run_repo=run_repo,
        )

        assert task.resolved_input["__credentials"] == {CRED_REF: VAULT_ENTRY}
        assert vault.calls == [("system", CRED_REF, None)]

    async def test_the_child_run_itself_carries_no_secret_at_rest(self):
        """The child run row holds nothing. Under the eager design it held the
        whole map — the child half of the at-rest exposure B5 (SA-2289) is
        about, gone as a side effect of resolving late."""
        child_run, _ = await _start_child()
        assert "__credentials" not in child_run.context.variables

    async def test_the_control_the_same_handler_on_the_root_also_gets_it(self):
        """Control. Without it, "the child works" is indistinguishable from
        "this harness hands credentials to anything"."""
        workflow = _child_workflow()
        task = await _dispatch(
            workflow.nodes[0], _run(workflow), workflow, vault=FakeVault(),
        )
        assert task.resolved_input["__credentials"] == {CRED_REF: VAULT_ENTRY}


class TestNothingIsResolvedBeforeItIsNeeded:
    """The property B3 exists for — these would all have FAILED against the
    eager design, which is what makes this a change rather than a refactor."""

    async def test_seeding_a_run_does_not_touch_the_vault(self):
        """StartRun used to walk every node and resolve every ref before the
        first node could run, and saved the run only afterwards — so a vault
        refusal meant no run row at all (SA-2294)."""
        workflow = _child_workflow()
        run = _run(workflow)

        await RunCredentialSeeder().seed(
            run, workflow, tenant_id="system", user_id=None, overrides={},
        )

        assert "__credentials" not in run.context.variables
        assert not hasattr(RunCredentialSeeder(), "_vault"), (
            "the seeder still holds a vault client — resolution leaked back "
            "into start time"
        )

    async def test_a_node_only_ever_fetches_its_own_ref(self):
        """Two credentials in one workflow cost ONE call per dispatch."""
        vault = FakeVault()
        workflow = Workflow(
            id="two", name="two",
            nodes=[_task_node("a", CRED_REF), _task_node("b", OTHER_REF)],
            edges=[],
        )

        task = await _dispatch(
            workflow.nodes[0], _run(workflow), workflow, vault=vault,
        )

        assert vault.calls == [("system", CRED_REF, None)]
        assert OTHER_REF not in task.resolved_input["__credentials"], (
            "a node was handed a credential belonging to a different node"
        )

    async def test_a_credential_on_a_branch_that_never_runs_is_never_fetched(self):
        """A broken credential on dead code used to fail the whole run at
        start. Dispatching node 'a' must not touch node 'b''s credential."""
        vault = FakeVault(error=CredentialNotFound(OTHER_REF))
        workflow = Workflow(
            id="two", name="two",
            nodes=[_task_node("a", None), _task_node("b", OTHER_REF)],
            edges=[],
        )

        task = await _dispatch(
            workflow.nodes[0], _run(workflow), workflow, vault=vault,
        )

        assert vault.calls == []
        assert "__credentials" not in task.resolved_input

    async def test_a_node_with_no_ref_never_calls_the_vault(self):
        vault = FakeVault()
        workflow = _child_workflow(with_credential=False)

        task = await _dispatch(
            workflow.nodes[0], _run(workflow), workflow, vault=vault,
        )

        assert vault.calls == []
        assert "__credentials" not in task.resolved_input

    async def test_a_dormant_vault_changes_nothing(self):
        """``CREDENTIALS_VAULT_URL`` unset — provider wired, inactive."""
        workflow = _child_workflow()
        task = await _dispatch(
            workflow.nodes[0], _run(workflow), workflow, vault=None,
        )
        assert "__credentials" not in task.resolved_input


class TestTheWorkerContractIsUnchanged:
    """B3's hard promise: no worker changes. The SDK's own coalesce is the
    authority on what "unchanged" means, so it is imported and run."""

    async def test_the_sdk_coalesce_borrows_what_the_control_plane_sends(self):
        """Runs the WORKER's real coalesce against what this service produces.

        Loaded straight from the file rather than imported as a package: the
        module has no imports beyond ``typing``, and importing ``worker_sdk``
        properly would drag in its pydantic settings (which reject this repo's
        .env) and permanently pollute ``sys.path`` for the rest of the suite.
        If this ever fails to load, the contract moved — that is a finding, not
        a reason to weaken the test to a hand-copied dict.
        """
        import importlib.util
        import pathlib

        src = (
            pathlib.Path(__file__).resolve().parents[3]
            / "worker" / "worker-sdk" / "worker_sdk" / "layer2_application"
            / "services" / "effective_auth.py"
        )
        assert src.is_file(), f"the worker SDK coalesce moved: {src}"
        spec = importlib.util.spec_from_file_location("_sdk_effective_auth", src)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        resolve_effective_auth = module.resolve_effective_auth

        workflow = _child_workflow()
        task = await _dispatch(
            workflow.nodes[0], _run(workflow), workflow, vault=FakeVault(),
        )

        inputs = dict(task.resolved_input)
        inputs["credential_ref"] = CRED_REF   # what the node ships to the worker
        assert resolve_effective_auth(inputs, require=True) == VAULT_ENTRY

    async def test_transport_extras_are_dropped_from_the_map(self):
        """``expires_at`` comes back from the vault and must not reach the
        worker — an entry is exactly ``{type, config}``. Honouring it is B6."""
        workflow = _child_workflow()
        task = await _dispatch(
            workflow.nodes[0], _run(workflow), workflow, vault=FakeVault(),
        )
        assert set(task.resolved_input["__credentials"][CRED_REF]) == {
            "type", "config",
        }


class TestARefusalLandsOnTheNode:
    @pytest.mark.parametrize(
        "error",
        [CredentialNotFound(CRED_REF), CredentialResolveAuthError("403")],
        ids=["not_found", "refused"],
    )
    async def test_a_verdict_becomes_an_expression_error(self, error):
        """``_exec_uniform`` already routes ExpressionError to
        NodeDispatched(dispatch_to_worker=False) + NodeFailed, so the node goes
        red with this message and the empty task never reaches a worker that
        would overwrite it with its own 401."""
        workflow = _child_workflow()
        with pytest.raises(ExpressionError) as excinfo:
            await _dispatch(
                workflow.nodes[0], _run(workflow), workflow,
                vault=FakeVault(error=error),
            )

        message = str(excinfo.value)
        assert "call_api" in message, "names the node"
        assert CRED_REF in message, "names the credential"
        assert "Automation" in message, "points at the SA-2279 grant"

    async def test_a_transient_fault_is_left_alone(self):
        """Only VERDICTS are converted. Calling a blip terminal would fail a
        node a retry would have completed."""
        workflow = _child_workflow()
        with pytest.raises(TimeoutError):
            await _dispatch(
                workflow.nodes[0], _run(workflow), workflow,
                vault=FakeVault(error=TimeoutError("vault timeout")),
            )

    async def test_a_refused_child_credential_no_longer_kills_the_child_start(self):
        """Under the eager design this raised out of ``coordinator.start`` and
        the child run was never created. Now the child exists, and the failure
        waits for the node that actually needs the secret."""
        run_repo = MemoryRunRepository()
        child_run, child_workflow = await _start_child(run_repo=run_repo)

        assert child_run is not None, "the child run must exist regardless"
        with pytest.raises(ExpressionError):
            await _dispatch(
                child_workflow.nodes[0], child_run, child_workflow,
                vault=FakeVault(error=CredentialResolveAuthError("403")),
                run_repo=run_repo,
            )


class TestTheIdentityMatchesTheRoot:
    """A child must resolve as the identity the ROOT ran as, or a sub-workflow
    of a hand-started run silently becomes a user-less caller and needs an
    automation grant its parent never needed.

    Moving to system authority is B4 (SA-2288) and is NOT done here — these
    tests pin today's rule so B4 has something explicit to change.
    """

    async def test_the_root_user_is_used_for_a_top_level_run(self):
        vault = FakeVault()
        workflow = _child_workflow()
        run = _run(workflow, metadata={"user_id": "u-42"})

        await _dispatch(workflow.nodes[0], run, workflow, vault=vault)

        assert vault.calls == [("system", CRED_REF, "u-42")]

    async def test_a_child_resolves_as_the_root_user(self):
        """The child run carries no metadata of its own — by design, since
        metadata also holds agent routing keys that must not follow a child —
        so the identity is read from the root."""
        run_repo = MemoryRunRepository()
        root = _run(_parent_workflow(), metadata={"user_id": "u-42"})
        run_repo.save(root)
        child_run, child_workflow = await _start_child(
            run_repo=run_repo, parent_run=root,
        )
        vault = FakeVault()

        await _dispatch(
            child_workflow.nodes[0], child_run, child_workflow,
            vault=vault, run_repo=run_repo,
        )

        assert vault.calls == [("system", CRED_REF, "u-42")]

    async def test_the_identity_lookup_is_skipped_when_the_node_needs_nothing(self):
        """The root read is a repo call. A node with no credential_ref must not
        pay for it."""
        run_repo = MemoryRunRepository()
        reads: list[str] = []
        original = run_repo.aget_by_id

        async def _counting(run_id):
            reads.append(run_id)
            return await original(run_id)

        run_repo.aget_by_id = _counting
        child = _run(_child_workflow(with_credential=False))
        child.id, child.root_run_id = "child-run", "root-run"
        run_repo.save(child)
        workflow = _child_workflow(with_credential=False)

        await _dispatch(
            workflow.nodes[0], child, workflow,
            vault=FakeVault(), run_repo=run_repo,
        )

        assert reads == []


class TestAuthorSuppliedEntriesAndInFlightRuns:
    """``__credentials`` on the run changed MEANING (everything the workflow
    might need → the caller's overrides). Both readings must keep working."""

    async def test_author_supplied_entries_win_over_the_vault(self):
        mine = {CRED_REF: {"type": "api_key", "config": {"value": "mine"}}}
        vault = FakeVault()
        workflow = _child_workflow()

        task = await _dispatch(
            workflow.nodes[0], _run(workflow, sealed=mine), workflow, vault=vault,
        )

        assert task.resolved_input["__credentials"] == mine
        assert vault.calls == [], (
            "the vault was asked for a ref the caller had already supplied"
        )

    async def test_a_run_started_before_this_change_still_dispatches(self):
        """An in-flight run carries its fully-resolved map under the same key.
        It is read as overrides, so it keeps working and does not re-fetch —
        deploying B3 must not strand a run paused on an INPUT node."""
        pre_b3 = {CRED_REF: VAULT_ENTRY, OTHER_REF: VAULT_ENTRY}
        vault = FakeVault(error=CredentialNotFound("vault would refuse now"))
        workflow = _child_workflow()

        task = await _dispatch(
            workflow.nodes[0], _run(workflow, sealed=pre_b3), workflow, vault=vault,
        )

        assert task.resolved_input["__credentials"] == pre_b3
        assert vault.calls == []

    async def test_the_default_key_convention_survives(self):
        """``borrow_credential`` falls back to the literal ``"default"`` when a
        node ships no ``credential_ref``. That entry can only come from the
        caller, which is why overrides pass through whole rather than being
        filtered down to the node's own ref."""
        default_cred = {"default": {"type": "bearer", "config": {"token": "d"}}}
        workflow = _child_workflow(with_credential=False)

        task = await _dispatch(
            workflow.nodes[0], _run(workflow, sealed=default_cred), workflow,
            vault=FakeVault(),
        )

        assert task.resolved_input["__credentials"] == default_cred


class TestTheExpressionFrameAgreesWithTheWorker:
    """B3 required both consumers to move together. If ``$credentials.<ref>``
    and the node's own worker disagreed about what a ref resolves to, an author
    would have no way to tell which one was lying."""

    async def test_building_a_scope_costs_nothing(self):
        vault = FakeVault()
        builder = _scope_builder(RunCredentialProvider(vault_client=vault))
        workflow = _child_workflow()

        scope = builder.build(_run(workflow), workflow=workflow)
        await scope.lookup("run")          # an unrelated source

        assert vault.calls == [], "the scope reached the vault without being asked"

    async def test_the_frame_resolves_the_same_entry_the_worker_gets(self):
        vault = FakeVault()
        workflow = _child_workflow()
        run = _run(workflow)
        scope = _scope_builder(RunCredentialProvider(vault_client=vault)).build(
            run, workflow=workflow,
        )

        from_expression = await scope.lookup("credentials")
        task = await _dispatch(
            workflow.nodes[0], run, workflow, vault=FakeVault(),
        )

        assert from_expression[CRED_REF] == (
            task.resolved_input["__credentials"][CRED_REF]
        )

    async def test_a_vault_failure_leaves_the_frame_empty_rather_than_raising(self):
        """An expression must not be the thing that reports a credential
        problem — the node that needs it reports it, with the real reason."""
        provider = RunCredentialProvider(
            vault_client=FakeVault(error=CredentialResolveAuthError("403")),
        )
        workflow = _child_workflow()
        scope = _scope_builder(provider).build(_run(workflow), workflow=workflow)

        assert await scope.lookup("credentials") == {}

    async def test_the_frame_is_memoised_within_a_pass(self):
        vault = FakeVault()
        workflow = _child_workflow()
        scope = _scope_builder(RunCredentialProvider(vault_client=vault)).build(
            _run(workflow), workflow=workflow,
        )

        await scope.lookup("credentials")
        await scope.lookup("credentials")

        assert len(vault.calls) == 1
