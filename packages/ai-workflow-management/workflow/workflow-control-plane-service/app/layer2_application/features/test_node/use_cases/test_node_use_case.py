"""Test a single node during workflow design time."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.read.run_state import child_runs_of
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer2_application.errors import ErrorCode
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer2_application.interfaces.orchestration_engine_interface import IOrchestrationEngine
from app.layer2_application.interfaces.event_log_repository_interface import (
    IEventLogRepository,
)
from app.layer2_application.interfaces.run_context_view_interface import (
    IRunContextViewStore,
)
from app.layer2_application.interfaces.snapshot_repository_interface import (
    ISnapshotRepository,
)
from app.layer2_application.repositories.workflow_snapshot_repository_interface import (
    IWorkflowSnapshotRepository,
)
from app.layer2_application.definition.workflow_snapshot_loader import capture_snapshot
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.resolution_scope import get_current_scope
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun, RunContext
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer2_application.orchestration.successor_processor import BUILTIN_NODE_TYPES, PENDING_NODE_TYPES


@dataclass
class TestNodeCommand:
    __test__ = False
    workflow_id: str = ""
    node_id: str = ""
    input_data: dict[str, Any] = field(default_factory=dict)
    mock_variables: dict[str, Any] = field(default_factory=dict)
    # Tenant + caller identity used to resolve node credential_refs against the
    # vault. Mirrors StartRunCommand/TestFullWorkflowCommand so a single-node test
    # authenticates with the SAME credential a real run would. Both default to
    # "system" to match tenant-1's FE x-tenant-id/x-user-id (a user-owned
    # credential's owner is "system"); user_id must reach the vault or its owner
    # check 403s and the credential silently never seeds → worker 401s.
    tenant_id: str = "system"
    user_id: str = "system"


@dataclass
class TestNodeResult:
    node_id: str = ""
    node_type: str = ""
    # "inline" | "dispatched" | "pending_input" | "child_run" | "loop_body"
    execution_type: str = ""
    run_id: str = ""
    # Inline (BUILTIN):
    handler_output: dict[str, Any] = field(default_factory=dict)
    skipped_branches: list[str] = field(default_factory=list)
    # Dispatched (TASK):
    task_id: str = ""
    resolved_input: dict[str, Any] = field(default_factory=dict)
    worker_type: str = ""
    # Child run (WORKFLOW node) — the started sub-workflow run id:
    child_run_id: str = ""
    # Pending (INPUT/HUMAN_ACTION):
    input_schema: list = field(default_factory=list)
    instruction: str = ""
    prefilled_data: dict[str, Any] = field(default_factory=dict)
    # Common:
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class TestNodeUseCase:
    __test__ = False

    def __init__(
        self,
        logger: ILogger,
        workflow_repo: IWorkflowRepository,
        workflow_snapshot_repo: IWorkflowSnapshotRepository,
        run_repo: IRunRepository,
        task_repo: ITaskRepository,
        handler_registry: NodeHandlerRegistry,
        orchestration_engine: IOrchestrationEngine,
        stage_recorder: StageRecorder,
        event_log_repo: IEventLogRepository | None = None,
        run_context_view_store: IRunContextViewStore | None = None,
        snapshot_repo: ISnapshotRepository | None = None,
        credential_cipher: Any = None,
        vault_credential_client: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.workflow_repo = workflow_repo
        self.workflow_snapshot_repo = workflow_snapshot_repo
        self.run_repo = run_repo
        self.task_repo = task_repo
        self._handler_registry = handler_registry
        self._engine = orchestration_engine
        self._stage_recorder = stage_recorder
        self._event_log_repo = event_log_repo
        self._view_store = run_context_view_store
        self._snapshot_repo = snapshot_repo
        # Credentials bridge — same deps StartRun/TestFull get (auto-injected by
        # name). Without these a single-node test runs unauthenticated → 401.
        self._credential_cipher = credential_cipher
        self._vault_credential_client = vault_credential_client

    async def _resolve_encrypted_credentials(
        self, workflow: Any, tenant_id: str, user_id: str | None = None,
    ) -> Any | None:
        """Resolve node ``credential_ref``s → the value for
        ``context.variables["__credentials"]`` (encrypted when a cipher is
        wired), or ``None`` when nothing resolves / the vault is dormant.
        Mirrors StartRun/TestFull so a node test authenticates like a real run.
        """
        if self._vault_credential_client is None:
            return None
        from app.layer2_application.features.start_run.use_cases.resolve_node_credential_refs import (
            collect_and_resolve,
        )
        try:
            resolved = await collect_and_resolve(
                workflow, tenant_id=tenant_id, user_id=user_id,
                client=self._vault_credential_client,
            )
        except Exception as exc:  # vault reject / not-found / transport — non-fatal
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "test-node credential resolution failed (tenant=%s) — running "
                "without seeded credentials: %s", tenant_id, exc,
            )
            return None
        if not resolved:
            return None
        if self._credential_cipher:
            return self._credential_cipher.encrypt(resolved)
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "test-node runtime_credentials stored WITHOUT encryption — "
            "GATEWAY_REGISTRY_KEK_V1 is not set. Do not use in production.",
        )
        return resolved

    async def execute(self, command: TestNodeCommand) -> TestNodeResult:
        self.logger.info("Testing node", workflow_id=command.workflow_id, node_id=command.node_id)

        # 1. Load workflow
        workflow = await self.workflow_repo.aget_by_id(command.workflow_id)
        if not workflow:
            return TestNodeResult(
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
                error_details={"workflow_id": command.workflow_id},
            )

        # 2. Find node
        node = workflow.get_node(command.node_id)
        if not node:
            return TestNodeResult(
                error=ErrorCode.NODE_NOT_FOUND.default_message,
                error_code=ErrorCode.NODE_NOT_FOUND.code,
                error_details={"node_id": command.node_id, "workflow_id": command.workflow_id},
            )

        # 3. Get-or-create test run (permanent 1:1 with workflow)
        test_run = None
        if workflow.test_run_id:
            test_run = await self.run_repo.aget_by_id(workflow.test_run_id)

        # Re-snapshot the current workflow definition for this test cycle.
        # Without this, the engine's load_workflow_for_run() raises
        # WorkflowSnapshotMissingError on any subsequent PATCH /data or
        # POST /submit because the run row would carry an empty
        # workflow_snapshot_id.
        snapshot_id = capture_snapshot(workflow, self.workflow_snapshot_repo)

        if not test_run:
            topology = GraphTopology.from_workflow(workflow)
            test_run = WorkflowRun(
                workflow_id=workflow.id,
                workflow_version=workflow.version,
                workflow_snapshot_id=snapshot_id,
                status=RunStatus.RUNNING,
                is_test=True,
                test_mode="node",
                context=RunContext(
                    variables={"artifacts": {}},
                    topology=topology,
                ),
            )
            await self.run_repo.asave(test_run)
            workflow.test_run_id = test_run.id
            await self.workflow_repo.asave(workflow)
            # Fresh run: nothing to reset. The projector seed happens once for
            # both branches after steps 4/5 below — see the comment there.
        else:
            # Refresh DAG info in case workflow changed since test run was created
            test_run.context.topology = GraphTopology.from_workflow(workflow)
            test_run.workflow_snapshot_id = snapshot_id
            # Re-stamp the version — the run survives workflow saves (each
            # save mints a new version uuid). Mirrors reset_test_run.
            test_run.workflow_version = workflow.version
            test_run.test_mode = "node"
            # Hoist TASK upstream outputs from the projector view into the
            # aggregate. Phase 3b.1 (engine-split) made the view authoritative:
            # ``apply_state_updates_as_files`` emits ``NodeOutputProduced``
            # only and no longer writes ``run.context.variables``. So a
            # completed TASK upstream's output lives only in the view, never
            # the aggregate. Without this hoist, the ``_reseed_projector``
            # below would re-emit ``VariablesSeeded`` carrying only the
            # aggregate's ``{artifacts, input}`` and any BUILTIN / PENDING
            # outputs test_node wrote inline — every TASK output would be
            # silently dropped from the view, and the next handler call's
            # input resolution would fail with
            # ``"Source '<task_node_id>' not found in context"``.
            self._hoist_view_into_aggregate(test_run, workflow)
            # Surgical reset: only the target node and everything reachable
            # downstream of it. Upstream context (variables, completed_nodes,
            # last_output_ports, etc.) is preserved so this test_node call
            # can resolve ``$.<upstream_node>.field`` against prior outputs.
            #
            # Why downstream too: stale entries from a previous run cycle
            # in ``variables[downstream]`` / ``last_output_ports[downstream]``
            # leak into iteration history and into ``_current_working_ref``
            # (`edit_node_data` reads ``view.variables[node_id]`` first),
            # pointing PATCH operations at file_ids that may have been GC'd.
            downstream = self._compute_downstream(
                command.node_id, test_run.context.topology,
            )
            node = workflow.get_node(command.node_id)
            for nid in downstream:
                test_run.context.completed_nodes.discard(nid)
                test_run.context.skipped_nodes.discard(nid)
                test_run.context.triggered_nodes.discard(nid)
                test_run.context.variables.pop(nid, None)
                test_run.context.last_output_ports.pop(nid, None)
                test_run.context.last_taken_edge_ports.pop(nid, None)
                # Also clear the name-keyed mirror (legacy resolver path)
                # for the target node when its display name diverges from id.
                if nid == command.node_id and node and node.name and node.name != nid:
                    test_run.context.variables.pop(node.name, None)
                # Cancel any stale task for downstream nodes — task ids are
                # deterministic on (run.id, node.id), so a stale COMPLETED /
                # PENDING row would otherwise be picked up by
                # ``find_latest_task_for_node`` / the submit path.
                stale_task = await self.task_repo.aget_by_id(f"{test_run.id}-{nid}")
                if stale_task is not None and stale_task.status != TaskStatus.CANCELLED:
                    from datetime import datetime, timezone
                    stale_task.status = TaskStatus.CANCELLED
                    stale_task.completed_at = datetime.now(timezone.utc).isoformat()
                    await self.task_repo.asave(stale_task)
            # Projector cannot rewind selectively (no per-event delete in
            # ``IEventLogRepository``), so wipe-and-reseed: clear the log,
            # drop snapshots, invalidate the cache, then re-emit
            # ``RunStarted`` + ``VariablesSeeded`` carrying the post-pop
            # ``context.variables``. The reseed event carries upstream
            # entries only — the downstream pops above ensure stale refs
            # don't survive into the view's variables dict.
            test_run.status = RunStatus.RUNNING

        # 4. Apply input_data
        if command.input_data:
            test_run.context.variables["input"] = command.input_data

        # 5. Merge mock_variables (user overrides take precedence)
        for key, value in command.mock_variables.items():
            test_run.context.variables[key] = value

        # 5a. NOW seed the projector — after steps 4 and 5, not before them.
        #
        # This call used to sit at the end of each branch above, which meant the
        # seed was emitted from a ``context.variables`` that did not yet contain
        # ``input`` or the caller's ``mock_variables``. That was harmless while
        # ``CONTEXT`` was persisted: the aggregate carried them to the next pass.
        # Step 5b of solace/specs/wcp-node-output-storage-redesign.md removed
        # ``CONTEXT`` from the write path, so those two assignments now write to
        # memory that the next reload discards, and the node under test resolves
        # ``{{$input.x}}`` against a projector that never heard of it —
        # "Source 'input' not found in context", with the resolver's
        # "Available sources" listing the system frames (``run``, ``workflow``,
        # ``variable``, ``variables``, ``credentials``) that always answer, which
        # makes the context look fuller than it is.
        #
        # Ordering constraints this call sits between: it must come AFTER the
        # downstream pops (which decide what the seed carries) and BEFORE step 5b
        # / step 6, because ``_reseed_projector`` CLEARS the event log and would
        # wipe anything emitted before it. Nothing between the old position and
        # this one emits — steps 4 and 5 are plain dict writes.
        self._reseed_projector(test_run, workflow)

        # 5c. Seed the encrypted __credentials map so the node under test
        # authenticates like a real run. Uniform across the fresh + reuse
        # branches (both converge here, before the handler dispatches at
        # step 6), and re-applied every call so a rotated credential — or a
        # permanent test run created before this fix — picks up the latest.
        # Excluded from the projector's VariablesSeeded exactly like StartRun:
        # it lives only on the run aggregate, decrypted by the TASK handler.
        # Phase 1 of retiring ``__credentials`` (spec §13.6): nothing is sealed
        # onto the run any more, here or in StartRun. A test node that declares
        # a ``credential_ref`` still authenticates — ``RunCredentialProvider``
        # resolves it from the vault when the node dispatches, exactly as a
        # non-test run does. What is gone is the eager whole-workflow map, which
        # was the last writer keeping ``CONTEXT`` alive.
        #
        # It was also the worse of the two writers: this path sealed the map
        # WITHOUT encryption whenever ``GATEWAY_REGISTRY_KEK_V1`` was unset, and
        # said so in a warning nobody was watching.

        # 5b. WORKFLOW-node prep. Testing a WORKFLOW node now ACTUALLY runs its
        # child sub-workflow (see the child-run branch below), so two things
        # must happen BEFORE the handler computes the deterministic child run
        # id ``(run.id, node.id, generation)``:
        #   - Bump ``generation`` so a RE-TEST gets a fresh child run id. The
        #     coordinator INSERTs the child unconditionally at version 1; a
        #     repeated id would 500 (OptimisticConcurrencyError). This mirrors
        #     ``ResetTestRunUseCase`` / ``TestFullWorkflowUseCase``.
        #   - Cancel + drop the prior child run so a paused prior child can't be
        #     resolved by the timeout sweeper onto the new tracking task.
        if node.node_type == NodeType.WORKFLOW:
            await self._cleanup_prior_child_run(test_run, node.id)
            test_run.generation += 1

        # 6. Branch by node type
        task_factory = TaskFactory()
        result = TestNodeResult(
            node_id=command.node_id,
            node_type=node.node_type.value,
            run_id=test_run.id,
        )

        try:
            handler = self._handler_registry.get(node.node_type)
            if not handler:
                return TestNodeResult(
                    node_id=command.node_id,
                    node_type=node.node_type.value,
                    run_id=test_run.id,
                    error=ErrorCode.NO_HANDLER.default_message,
                    error_code=ErrorCode.NO_HANDLER.code,
                    error_details={"node_type": node.node_type.value},
                )
            # Activate the run ResolutionScope so the handler can resolve
            # ``{{$variables.<name>}}`` (the SET_VARIABLE runtime namespace,
            # reachable ONLY via the ambient ``SystemVariablesFrame`` — the
            # plain-dict context handlers pass holds it under key ``__vars``,
            # not ``variables``, so the dict lookup misses and the resolver
            # falls back to ``get_current_scope().lookup("variables")``).
            # Unlike ``_exec_uniform`` (the queued path), ``/node/test`` calls
            # ``handler.handle()`` directly and set no scope, so a CONDITION /
            # SWITCH / COMPUTE referencing ``$variables`` silently resolved to
            # None/False. ``__vars`` is already in ``test_run.context.variables``
            # by here (hoisted from the view above; never popped on this path),
            # so the scope built from ``test_run`` captures it. Mirrors the
            # ``_exec_uniform`` scope-entry block; guarded + degrading so it is
            # inert under an already-active scope or a stub engine.
            exec_scope_tokens = None
            if get_current_scope() is None:
                try:
                    exec_scope_tokens = self._engine._enter_run_scope(
                        test_run.id, test_run,
                    )
                except Exception:
                    self.logger.debug(
                        "test-node scope activation failed; degrading to no-scope",
                    )
                    exec_scope_tokens = None
            try:
                handler_result = await handler.handle(
                    node=node,
                    run=test_run,
                    workflow=workflow,
                    topology=test_run.context.topology,
                    completed_task=None,
                    task_factory=task_factory,
                )
            finally:
                if exec_scope_tokens is not None:
                    self._engine._exit_run_scope(exec_scope_tokens)

            if node.node_type in BUILTIN_NODE_TYPES:
                # A BUILTIN node either completes INLINE (CONDITION / COMPUTE /
                # SWITCH / MERGE — the handler already did the real work) or
                # defers real ASYNC work, exactly as a real run does:
                #   (a) WORKFLOW (requires_child_run) — actually START the child
                #       sub-workflow; the node stays RUNNING and completes when
                #       the child finishes (coordinator.complete_parent), or
                #       stays RUNNING if the child pauses on its own INPUT node.
                #   (b) LOOP (completion_mode == "loop_body") — enqueue the loop
                #       body entry tasks so the iterations run via the worker
                #       queue; the node stays RUNNING until they finish.
                #   (c) inline — mark COMPLETED.
                #
                # Aggregate writes (for cross-node continuity: handlers resolve
                # ``$.PriorNode.field`` against ``run.context.variables``) and
                # skip tracking are shared. We do NOT route output through
                # ``apply_state_updates_as_files`` (it fans out to successors via
                # ``DispatcherConsumer`` — wrong for an isolated single-node
                # test); the view is synced fan-out-free per path.
                #
                # File-back each output value exactly as a real run does
                # (``apply_state_updates_as_files`` → ``_to_file_ref``) so the
                # stored value is a file_ref carrying a queryable ``file_id`` /
                # ``artifact_id`` for ``GET /data?side=output`` — but WITHOUT the
                # successor fan-out. ``_to_file_ref`` uploads dict/list values to
                # the file service and returns the pointer; it is a no-op for
                # scalars, already-stored refs, or when no data_store_service is
                # wired. The raw ``handler_result.state_updates`` is left intact
                # for ``result.handler_output`` (immediate test feedback).
                stored_updates: dict[str, Any] = {}
                if handler_result.state_updates:
                    for key, value in handler_result.state_updates.items():
                        stored = await self._engine._to_file_ref(key, value)
                        stored_updates[key] = stored
                        test_run.context.variables[key] = stored
                if handler_result.loop_state_updates:
                    test_run.context.loop_state.update(handler_result.loop_state_updates)
                for skipped_id in handler_result.skipped_node_ids:
                    test_run.context.skipped_nodes.add(skipped_id)
                result.skipped_branches = list(handler_result.skipped_node_ids)

                if handler_result.requires_child_run:
                    # (a) WORKFLOW node — run the child sub-workflow for real.
                    # Mirror ``node_execution_runner._run_child_run``: take the
                    # tracking task, mark it RUNNING + save (so the canvas shows
                    # the WORKFLOW node RUNNING via ``aggregate_node_status``),
                    # then start the child. Do NOT auto-complete — the node is
                    # completed by ``complete_parent`` when the child finishes
                    # (the coordinator is node-test aware, so it won't flip this
                    # permanent test run terminal). If the child pauses on its
                    # own INPUT node, the node correctly stays RUNNING.
                    result.execution_type = "child_run"
                    node_state = handler_result.state_updates.get(node.id, {})
                    metadata = node_state.get("metadata", {})
                    if handler_result.tasks_to_dispatch:
                        from datetime import datetime, timezone
                        tracking_task = handler_result.tasks_to_dispatch[0]
                        tracking_task.run_id = test_run.id
                        tracking_task.status = TaskStatus.RUNNING
                        tracking_task.started_at = datetime.now(timezone.utc).isoformat()
                        await self.task_repo.asave(tracking_task)
                        result.task_id = tracking_task.id
                        await self._engine._start_child_workflow(
                            node, tracking_task, test_run, workflow,
                            test_run.context.topology, handler_result,
                        )
                    # The coordinator registered the new child id on the run.
                    result.child_run_id = child_runs_of(
                        test_run.id, self._view_store, test_run,
                    ).get(node.id, "")
                    result.handler_output = {
                        "child_workflow_id": metadata.get("child_workflow_id", ""),
                        "child_input": metadata.get("child_input", {}),
                        "child_run_id": result.child_run_id,
                        "on_failure": metadata.get("on_failure", "fail"),
                    }

                elif handler_result.completion_mode == "loop_body":
                    # (b) LOOP node — enqueue the loop body entry tasks so the
                    # iterations actually run via the worker queue (mirrors
                    # ``node_execution_runner._run_loop_body``, minus the
                    # production fan-out event). The LOOP node stays RUNNING; it
                    # is not auto-completed.
                    from app.layer2_application.interfaces.orchestration_queue_interface import (
                        ExecuteNodeAction,
                    )
                    result.execution_type = "loop_body"
                    for body_task in handler_result.tasks_to_dispatch:
                        body_task.run_id = test_run.id
                        await self.task_repo.asave(body_task)
                        if self._engine._queue is not None:
                            await self._engine._queue.enqueue(
                                ExecuteNodeAction(
                                    run_id=test_run.id, task_id=body_task.id,
                                ),
                            )
                    if handler_result.tasks_to_dispatch:
                        result.task_id = handler_result.tasks_to_dispatch[0].id
                    result.handler_output = handler_result.state_updates.get(node.id, {})

                else:
                    # (c) Inline node (CONDITION / COMPUTE / SWITCH / MERGE / …).
                    # The handler did the real work; mark the node COMPLETED.
                    result.execution_type = "inline"
                    test_run.context.completed_nodes.add(node.id)
                    # Mirror the file-backed inline output into the projector
                    # view so GET /data / view-store readers see it (fan-out-
                    # free) with the queryable file_ref (file_id / artifact_id),
                    # matching a real run. Use ``stored_updates`` (file-backed),
                    # not the raw ``handler_result.state_updates``.
                    self._publish_inline_output_to_view(
                        test_run, node, stored_updates,
                    )
                    # ``aggregate_node_status`` (what the canvas reads) is
                    # task-based: a BUILTIN node with no task shows
                    # ``NOT_STARTED``, and one whose stale task the surgical
                    # reset cancelled shows ``CANCELLED`` (red). Normal execution
                    # always leaves a COMPLETED task even for inline nodes, so
                    # mirror that. Id is deterministic on (run, node), so this
                    # overwrites the just-cancelled stale task.
                    from datetime import datetime, timezone
                    builtin_task = task_factory.create_task(node, test_run)
                    builtin_task.status = TaskStatus.COMPLETED
                    builtin_task.completed_at = datetime.now(timezone.utc).isoformat()
                    await self.task_repo.asave(builtin_task)
                    # Realtime ``NodeCompleted`` (fresh uuid4 id, so a re-test
                    # isn't dropped by the FE's event_id dedupe) flips a live
                    # canvas green without a refresh. Inert for fan-out.
                    self._engine.emit_node_completed(None, test_run, node)
                    result.handler_output = handler_result.state_updates.get(node.id, {})

            elif node.node_type in PENDING_NODE_TYPES:
                # INPUT / HUMAN_ACTION / DATA_EDIT / FILE_UPLOAD
                result.execution_type = "pending_input"
                for pending_task in handler_result.tasks_to_dispatch:
                    await self.task_repo.asave(pending_task)
                    result.task_id = pending_task.id
                # Write the handler's state_updates (the freshly-provisioned
                # file_ref tree: root CSV + child tables) to
                # ``test_run.context.variables`` (the aggregate).
                #
                # We do NOT route this through ``apply_state_updates_as_files``:
                # that emits a port-bearing ``NodeOutputProduced`` which
                # ``DispatcherConsumer`` fans out to outgoing edges /
                # successors — wrong before the user has submitted the input.
                # The view is synced separately below, fan-out-free, so
                # ``GET /data?side=output`` surfaces the freshly-provisioned
                # working copy after a (re-)dispatch (the input side reads
                # ``task.resolved_input``, which is the same provisioned ref).
                if handler_result.state_updates:
                    for key, value in handler_result.state_updates.items():
                        test_run.context.variables[key] = value
                # Mirror the provisioned working ref into the projector view.
                self._publish_inline_output_to_view(
                    test_run, node, handler_result.state_updates,
                )

                # Extract schema/instruction from node config
                result.input_schema = node.config.get("input_schema", [])
                result.instruction = node.config.get("instruction", "")

                # Extract prefilled data from resolved input
                if handler_result.tasks_to_dispatch:
                    first_task = handler_result.tasks_to_dispatch[0]
                    result.prefilled_data = first_task.resolved_input or {}

            else:
                # TASK node: enqueue an ExecuteNodeAction so the worker
                # picks it up and runs the full _exec_uniform path (CSV
                # conversion, data mode, event publishing). The test run
                # carries is_test=True, so CompleteTaskUseCase will skip
                # successor processing once the worker responds.
                from app.layer2_application.interfaces.orchestration_queue_interface import ExecuteNodeAction
                result.execution_type = "dispatched"
                if handler_result.tasks_to_dispatch:
                    task = handler_result.tasks_to_dispatch[0]
                    task.run_id = test_run.id
                    # ``task.id`` is deterministic on (run.id, node.id), so
                    # repeated test_node calls on the same node produce the
                    # same id. ``apply_state_updates_as_files`` keys
                    # ``NodeOutputProduced`` event_ids on (task.id, node_id,
                    # idempotency_key) — without a fresh idempotency_key
                    # here the second test would emit an identical event_id
                    # that the event log dedupes silently, leaving the
                    # projector stuck on the first call's output.
                    from datetime import datetime, timezone
                    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
                    task.idempotency_key = f"{task.id}-test-{timestamp}"
                    await self.task_repo.asave(task)
                    # The response returns what the handler resolved — the
                    # worker-side effects (CSV store, NodeDispatched event)
                    # happen asynchronously once the queue drains.
                    result.task_id = task.id
                    result.resolved_input = task.resolved_input or {}
                    result.worker_type = task.worker_type or ""
                    if self._engine._queue is not None:
                        await self._engine._queue.enqueue(
                            ExecuteNodeAction(run_id=test_run.id, task_id=task.id),
                        )

            self._engine.publish_events(handler_result.events)

        except Exception as exc:
            self.logger.error("Test node failed", node_id=command.node_id, error=str(exc))
            result.error = str(exc)

        # 7. Save test run
        await self.run_repo.asave(test_run)

        await self._engine.drain_events()
        return result

    async def _cleanup_prior_child_run(
        self, test_run: WorkflowRun, node_id: str,
    ) -> None:
        """Cancel the child run a previous WORKFLOW-node test started and drop
        it from ``child_runs``.

        Re-testing a WORKFLOW node bumps ``generation`` so the new child gets a
        fresh id, but the prior child row persists (there is no per-entity
        delete). If it paused on its own INPUT node it is still RUNNING; the
        timeout sweeper resolves ``parent_run_id``/``parent_node_id`` to the
        (generation-free) tracking task id ``{run}-{node}`` and could FAIL the
        new node. Cancelling it in place (UPDATE — no INSERT collision) and
        popping it keeps ``/children`` showing exactly the latest child.
        """
        prior_child_id = child_runs_of(
            test_run.id, self._view_store, test_run,
        ).get(node_id)
        if not prior_child_id:
            return
        from datetime import datetime, timezone
        terminal = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
        prior_child = await self.run_repo.aget_by_id(prior_child_id)
        if prior_child is not None and prior_child.status not in terminal:
            prior_child.status = RunStatus.CANCELLED
            prior_child.completed_at = datetime.now(timezone.utc).isoformat()
            await self.run_repo.asave(prior_child)
        task_terminal = {
            TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED,
        }
        for task in await self.task_repo.alist_by_run(prior_child_id):
            if task.status not in task_terminal:
                task.status = TaskStatus.CANCELLED
                task.completed_at = datetime.now(timezone.utc).isoformat()
                await self.task_repo.asave(task)
        test_run.context.child_runs.pop(node_id, None)

    # ------------------------------------------------------------------
    # Reset helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_downstream(
        start_node_id: str, topology: GraphTopology,
    ) -> set[str]:
        """Return ``{start_node_id} ∪ every node reachable via ``adjacency``.

        BFS over the static topology. Cycles (LOOP back-edges) are handled
        by the visited-set guard. Returns just ``{start_node_id}`` when
        the node has no outgoing edges (leaf) or isn't in the topology.
        """
        visited: set[str] = {start_node_id}
        queue: list[str] = [start_node_id]
        while queue:
            current = queue.pop(0)
            for successor in topology.adjacency.get(current, []) or []:
                if successor in visited:
                    continue
                visited.add(successor)
                queue.append(successor)
        return visited

    def _publish_inline_output_to_view(
        self, test_run: WorkflowRun, node: Any, state_updates: dict[str, Any],
    ) -> None:
        """Mirror an inline (BUILTIN) / provisioned (PENDING) node output into
        the projector view.

        test_node writes ``state_updates`` straight to the aggregate, but
        ``GET /data`` and every view-store reader rebuild from the event log —
        so without an event the output is invisible (``view.variables[node.id]``
        stays empty for a node that just ran / was provisioned). Route it
        through ``emit_variables_seeded`` (a ``__bootstrap__``-sourced
        ``NodeOutputProduced`` that ``DispatcherConsumer`` skips for fan-out),
        so the view gains the write-set WITHOUT traversing edges or signalling
        successors — correct for an isolated single-node test, and required for
        PENDING nodes where the form isn't submitted yet.

        A per-node ``seed_id`` keeps the event_id distinct from the reseed's
        bootstrap ``variables_seeded`` event so the cleared-then-reseeded log
        doesn't dedupe this emission away.
        """
        if not state_updates:
            return
        self._engine.emit_variables_seeded(
            test_run, dict(state_updates), seed_id=f"node_output:{node.id}",
        )

    def _hoist_view_into_aggregate(
        self, test_run: WorkflowRun, workflow: Any,
    ) -> None:
        """Pull node outputs from the projector view back into the aggregate
        before a reseed.

        Phase 3b.1 made the projector view authoritative for node outputs —
        ``apply_state_updates_as_files`` emits ``NodeOutputProduced`` and no
        longer writes ``run.context.variables[node_id]``. ``_reseed_projector``
        re-emits ``VariablesSeeded`` from the aggregate, so without this hoist
        every view-only output is wiped on the next test_node call.

        Merge rule (the fix for the upstream "data gone" bug):

        - **Node-output keys** (node ids + name aliases) are *view*-
          authoritative — let the view win even when the aggregate already
          holds an entry. A prior ``test_node`` PENDING/INPUT call writes
          the empty dispatch-time provisioned ref straight into the
          aggregate (see the PENDING branch above); a gap-fill would keep
          that stale ref and clobber the real submitted output that lives
          only on the view, so an upstream INPUT loses its data when a
          downstream node is tested.
        - **Metadata keys** (``artifacts``, ``input``, ``run``, ...) are
          *aggregate*-authoritative (``register_artifacts`` mutates the
          aggregate dict, not an event), so gap-fill them — overwriting with
          a staler view copy would drop later registrations.
        """
        if self._view_store is None:
            return
        try:
            view = self._view_store.get(test_run.id)
        except Exception:  # pragma: no cover — defensive
            return
        if view is None:
            return
        node_output_keys = {n.id for n in workflow.nodes}
        node_output_keys |= {n.name for n in workflow.nodes if n.name}
        for key, value in (view.variables or {}).items():
            if key in node_output_keys or key not in test_run.context.variables:
                test_run.context.variables[key] = value

    def _reseed_projector(
        self, test_run: WorkflowRun, workflow: Any = None,
    ) -> None:
        """Wipe the event log + snapshot, then re-emit lifecycle seeds.

        Why both: ``RunCompleted`` / terminal events from a prior cycle
        would otherwise leave ``view.status`` stuck at ``COMPLETED`` —
        and the snapshot would resurrect that view even after the log
        is cleared (cached projector loads snapshot, tail-replays from
        ``snapshot.sequence``; cleared log → empty tail → snapshot wins).
        Same gap that ``feedback_test_run_event_log_reset`` documented
        for ``/test/full``; this brings ``/node/test`` to parity.

        ``RunStarted`` and ``VariablesSeeded`` both use deterministic
        event_ids keyed on ``run.id``. Re-emitting on the same run after
        a clear is safe — the new envelopes insert; without a clear they
        would dedupe silently.

        Re-emitting ``NodeCompleted`` / ``NodeCancelled`` for the surviving
        ``completed_nodes`` / ``skipped_nodes`` is the fix for the "upper
        node back to PENDING" bug: the projector rebuilds those sets *only*
        from those events (``RunStarted`` + ``VariablesSeeded`` carry
        neither), so clearing the log without re-emitting them drops the
        preserved upstream nodes' terminal status from the view — and the
        re-broadcast ``RunStarted`` resets the realtime canvas. The
        downstream pop already discarded reset nodes from these sets, so
        only nodes that must stay terminal remain. ``NodeCompleted`` is
        inert for dispatch — ``DispatcherConsumer`` only fans out on
        ``NodeOutputProduced`` — so this restores status without
        re-triggering any successor.
        """
        if self._event_log_repo is not None:
            self._event_log_repo.clear(test_run.id)
        if self._snapshot_repo is not None:
            # INT32 max so the value fits the HANA ``sequence`` column.
            self._snapshot_repo.delete_older_than(
                test_run.id, keep_sequence=2**31 - 1,
            )
        invalidate = getattr(self._view_store, "invalidate", None)
        if callable(invalidate):
            invalidate(test_run.id)
        # Re-publish lifecycle seeds: status → RUNNING, variables →
        # post-pop snapshot of aggregate context (upstream only, since
        # downstream entries were popped above).
        self._engine.emit_run_status(test_run, "started")
        self._engine.emit_variables_seeded(
            test_run, dict(test_run.context.variables),
        )
        # Restore terminal node status for preserved (upstream) nodes.
        for node_id in sorted(test_run.context.completed_nodes):
            node = workflow.get_node(node_id) if workflow is not None else None
            self._engine.emit_node_completed(None, test_run, node)
        for node_id in sorted(test_run.context.skipped_nodes):
            self._engine.emit_node_cancelled(
                test_run, node_id=node_id, reason="skipped",
            )
