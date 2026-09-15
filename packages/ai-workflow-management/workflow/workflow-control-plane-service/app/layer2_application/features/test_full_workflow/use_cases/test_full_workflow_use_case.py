"""Test a full workflow execution using the design-time test_run_id."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer2_application.interfaces.orchestration_engine_interface import IOrchestrationEngine
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer2_application.read.schema_validator import SchemaValidator
from app.layer2_application.run_file_retention import (
    delete_run_files,
    retention_variables,
)
from app.layer2_application.interfaces.file_service_client_interface import (
    IFileServiceClient,
)
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.interfaces.orchestration_queue_interface import (
    IOrchestrationQueue,
    StartRunAction,
)
from app.layer2_application.interfaces.event_log_repository_interface import (
    IEventLogRepository,
)
from app.layer2_application.interfaces.run_context_view_interface import (
    IRunContextViewStore,
)
from app.layer2_application.interfaces.snapshot_repository_interface import (
    ISnapshotRepository,
)
from app.layer2_application.errors import ErrorCode
from app.layer1_domain.value_objects.instance_selection import (
    normalize_selection,
    to_run_selection,
    SELECTION_METADATA_KEY,
)


def _apply_instance_selection(run: WorkflowRun, selection: dict) -> None:
    """Stash (or clear) the SA-2424 instance pin on the run metadata. The
    permanent test run is REUSED across /test/full calls and its metadata is not
    cleared on reset, so an absent selection must actively remove any prior pin —
    otherwise last run's debug pin would silently stick to this run."""
    if selection:
        run.metadata[SELECTION_METADATA_KEY] = selection
    else:
        run.metadata.pop(SELECTION_METADATA_KEY, None)


@dataclass
class TestFullWorkflowCommand:
    workflow_id: str = ""
    workflow_version: str = ""
    input_data: dict[str, Any] = field(default_factory=dict)
    skip_validation: bool = True
    # Tenant + caller identity used to resolve node credential_refs against the
    # vault. Mirrors StartRunCommand so a test run resolves the SAME credential a
    # real run would. Both default to "system" because that is what tenant-1's FE
    # sends as x-tenant-id/x-user-id when storing credentials (see the workflow-
    # management deployment PUBLIC_DEV_TENANT_ID/PUBLIC_DEV_USER_ID=system), so a
    # user-owned credential's owner is "system" and the vault's owner check
    # (ResolveCredentialUseCase) requires the resolve call to carry user_id=owner.
    # Passing user_id=None here was the 403 → the credential silently never seeded
    # → the worker went out with only credential_ref and no __credentials → 401.
    tenant_id: str = "system"
    user_id: str = "system"
    # SA-2365: the environment selected for this test run; its non-secret
    # variables are injected as the ``$env`` source (same as a real run).
    environment_id: str = ""
    # SA-2424: optional per-node worker-instance pin (debug routing). Same shape
    # + semantics as StartRunCommand.instance_selection — stashed on the (reused)
    # test run's metadata so the engine resolves it per node at dispatch.
    instance_selection: dict[str, Any] = field(default_factory=dict)
    # SA-2558: display label of the server-verified caller who launched this test
    # run (from the BFF session / Bearer JWT). Separate from ``user_id`` (which
    # drives credential resolution) — this is purely the attribution shown on the
    # Execution-History chip. "system" for automated / M2M runs.
    triggered_by: str = "system"


@dataclass
class TestFullWorkflowResult:
    run_id: str = ""
    workflow_id: str = ""
    status: str = ""
    output_data: dict[str, Any] | None = None
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class TestFullWorkflowUseCase:
    def __init__(
        self,
        logger: ILogger,
        workflow_repo: IWorkflowRepository,
        workflow_snapshot_repo: Any,
        run_repo: IRunRepository,
        task_repo: ITaskRepository,
        orchestration_engine: IOrchestrationEngine,
        stage_recorder: StageRecorder,
        orchestration_queue: Optional[IOrchestrationQueue] = None,
        event_log_repo: Optional[IEventLogRepository] = None,
        run_context_view_store: Optional[IRunContextViewStore] = None,
        snapshot_repo: Optional[ISnapshotRepository] = None,
        credential_cipher: Any = None,
        vault_credential_client: Any = None,
        file_service_client: Optional[IFileServiceClient] = None,
        loop_state_repo: Any = None,
        environment_repo: Any = None,
        workflow_environment_repo: Any = None,
        worker_config_repo: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self._environment_repo = environment_repo
        self._workflow_environment_repo = workflow_environment_repo  # SA-2450 run guard
        from app.layer2_application.features.start_run.use_cases.env_pool import (
            PoolCursorRegistry,
        )
        self._pool_cursors = PoolCursorRegistry()
        self._file_service_client = file_service_client
        self._loop_state_repo = loop_state_repo
        self.workflow_repo = workflow_repo
        self.workflow_snapshot_repo = workflow_snapshot_repo
        self.run_repo = run_repo
        self.task_repo = task_repo
        self._engine = orchestration_engine
        self._stage_recorder = stage_recorder
        self._event_log_repo = event_log_repo
        self._view_store = run_context_view_store
        self._snapshot_repo = snapshot_repo
        # Credentials bridge (same deps StartRunUseCase gets, auto-injected by
        # name). Without these a test run never populates the encrypted
        # ``__credentials`` map, so every credential-using node runs
        # unauthenticated → 401. Mirrors the StartRun producer step.
        self._credential_cipher = credential_cipher
        self._vault_credential_client = vault_credential_client
        self._worker_config_repo = worker_config_repo  # SA-2437
        self._queue = orchestration_queue if orchestration_queue is not None else getattr(getattr(self, "_engine", None) or getattr(self, "_orchestration_engine", None), "_queue", None)

    def _bind_env(self, test_run: Any, command: "TestFullWorkflowCommand") -> None:
        """SA-2365: inject the selected environment's ``$env`` source into the
        test run context (same helper StartRun uses)."""
        from app.layer2_application.features.start_run.use_cases.env_pool import (
            load_and_bind,
        )
        load_and_bind(
            test_run,
            tenant_id=command.tenant_id,
            environment_id=command.environment_id,
            repo=self._environment_repo,
            cursors=self._pool_cursors,
            logger=self.logger,
        )

    async def _resolve_encrypted_credentials(
        self, workflow: Any, tenant_id: str, user_id: str | None = None,
    ) -> Any | None:
        """Resolve node ``credential_ref``s → the value to store under
        ``context.variables["__credentials"]`` (encrypted when a cipher is
        configured), or ``None`` when nothing resolves / the vault is dormant.

        Mirrors ``StartRunUseCase``'s producer step so a test run authenticates
        like a real run. See ``resolve_node_credential_refs``.

        **Non-fatal on resolve failure.** A test run must not 500 just because a
        node's credential can't be resolved (e.g. the vault rejects the tenant, or
        the credential was deleted). On any resolve error we log and return
        ``None`` — the run proceeds without that credential and the worker
        fail-closes with a clear auth error, exactly as it did before test-run
        credential seeding existed. (StartRun stays strict: its tenant comes from
        the authenticated request, so a failure there is a real caller error.)
        """
        if self._vault_credential_client is None:
            return None
        from app.layer2_application.features.start_run.use_cases.resolve_node_credential_refs import (
            collect_and_resolve,
        )
        try:
            resolved_creds = await collect_and_resolve(
                workflow,
                tenant_id=tenant_id,
                user_id=user_id,
                client=self._vault_credential_client,
            )
        except Exception as exc:  # vault reject / not-found / transport
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "test-run credential resolution failed (tenant=%s) — running "
                "without seeded credentials; credential-using nodes will "
                "fail-closed: %s", tenant_id, exc,
            )
            return None
        if not resolved_creds:
            return None
        if self._credential_cipher:
            return self._credential_cipher.encrypt(resolved_creds)
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "test-run runtime_credentials stored WITHOUT encryption — "
            "GATEWAY_REGISTRY_KEK_V1 is not set. Do not use in production.",
        )
        return resolved_creds

    async def _cleanup_run_files(
        self,
        variables: dict[str, Any] | None,
        loop_state: dict[str, Any] | None = None,
    ) -> None:
        """Delete every file-backed output reachable in ``variables`` — and in
        ``loop_state`` — from the file service before a full-run reset wipes
        them. Fully best-effort: a missing client is a no-op and any
        collect/delete failure is swallowed so retention never blocks the reset.
        Mirrors ResetTestRunUseCase.

        ``loop_state`` is a second root because a loop's claim-checked ``items``
        and spilled results chunks are reachable through nothing else — see
        ``run_file_retention``.

        Deletes are fanned out with bounded concurrency (see
        ``run_file_retention``) but still fully awaited here, so the
        delete-before-anything-new-is-written ordering is unchanged — that
        ordering is load-bearing, because content-idempotent uploads mean a
        byte-identical re-run reuses the same ``file_id``."""
        await delete_run_files(
            self._file_service_client, variables, self.logger, "full-run",
            loop_state=loop_state,
        )

    def _clear_loop_state_rows(self, run_id: str) -> None:
        """Drop the run's ``AIW_RUN_LOOP_STATE`` rows alongside the in-memory
        clear.

        The mirror in ``HanaRunRepository.save`` only fires when ``loop_state``
        is non-empty — 6,105 of 6,116 runs have none, and an unconditional
        DELETE would put 43,199 extra statements on the save path this redesign
        exists to shorten. A CLEAR therefore cannot propagate through it, and a
        row left behind reads as a loop that is still running. This is one of
        exactly two callers that clear it.

        Best-effort: a stale row must not block a re-run.
        """
        if self._loop_state_repo is None:
            return
        try:
            self._loop_state_repo.delete_run(run_id)
        except Exception:
            self.logger.warning(
                f"loop_state rows not cleared on re-run for {run_id}")

    async def execute(self, command: TestFullWorkflowCommand) -> TestFullWorkflowResult:
        self.logger.info(f"Testing full workflow {command.workflow_id} with workflow version {command.workflow_version}")

        # 1. Load workflow
        if command.workflow_version:
            workflow = await self.workflow_repo.aget_by_id_version(command.workflow_id, command.workflow_version)
        else:
            workflow = await self.workflow_repo.aget_by_id(command.workflow_id)
        if not workflow:
            return TestFullWorkflowResult(
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
                error_details={"workflow_id": command.workflow_id},
            )

        # SA-2450: reject a run whose environment_id is not assigned to this
        # workflow (unassigned → any concrete env rejected; empty/inherit ok).
        from app.layer2_application.features.start_run.use_cases.env_assignment_guard import (
            check_environment_assignment,
        )
        env_reject = check_environment_assignment(
            repo=self._workflow_environment_repo,
            tenant_id=command.tenant_id,
            workflow_id=command.workflow_id,
            environment_id=command.environment_id,
        )
        if env_reject:
            return TestFullWorkflowResult(
                error=env_reject,
                error_code=ErrorCode.ENVIRONMENT_NOT_ASSIGNED.code,
                error_details={"workflow_id": command.workflow_id, "environment_id": command.environment_id},
            )

        # SA-2424: validate the optional inline instance pin up front (bad mode -> 400).
        try:
            instance_selection = normalize_selection(command.instance_selection)
        except ValueError as exc:
            return TestFullWorkflowResult(
                error=str(exc),
                error_code=ErrorCode.INPUT_VALIDATION_FAILED.code,
                error_details={"workflow_id": command.workflow_id, "field": "instance_selection"},
            )
        # SA-2437: no inline pin → fall back to the persisted per-workflow config.
        if not instance_selection and self._worker_config_repo is not None:
            try:
                saved = self._worker_config_repo.get(command.workflow_id)
                if saved:
                    instance_selection = to_run_selection(saved.mode, saved.selection)
            except Exception as exc:
                self.logger.warning(f"worker-config load failed for {command.workflow_id}: {exc}")
        # SA-2437: tag with the flow id so the pin applies ONLY to this flow's
        # nodes, never to a sub-flow it calls (see _pin_selection_for).
        if instance_selection:
            instance_selection["workflow_id"] = command.workflow_id

        # 2. Validate input schema (unless skipped). Structural DAG
        # validation removed — execution is fan-out driven.
        if not command.skip_validation and workflow.input_schema:
            schema_errors = SchemaValidator.validate_input(command.input_data, workflow.input_schema)
            if schema_errors:
                return TestFullWorkflowResult(
                    error=ErrorCode.INPUT_VALIDATION_FAILED.default_message,
                    error_code=ErrorCode.INPUT_VALIDATION_FAILED.code,
                    error_details={"workflow_id": command.workflow_id, "errors": schema_errors},
                )

        # 3b. Resolve node credential_refs against the vault BEFORE the run is
        # built/reset, so both the new-run and reuse branches seed the same
        # encrypted ``__credentials`` a real run gets. Without this every
        # credential-using node in a test run goes out unauthenticated → 401.
        encrypted_credentials = await self._resolve_encrypted_credentials(
            workflow, command.tenant_id, command.user_id,
        )

        # 4. Prepare DAG
        topology = GraphTopology.from_workflow(workflow)

        # Snapshot the LATEST workflow definition for this trigger so the
        # test execution runs against today's edits, not whatever the
        # previous /test/full saw.
        from app.layer2_application.definition.workflow_snapshot_loader import capture_snapshot
        snapshot_id = capture_snapshot(workflow, self.workflow_snapshot_repo)

        # 5. Get or create test run (permanent 1:1 with workflow)
        #
        # Lookup is by id only — NOT version-scoped. Saving the workflow
        # mints a new version uuid while the run row keeps the version it
        # was created under, so an (id, version) lookup misses after every
        # save and silently rotates test_run_id. The frontend subscribes
        # to realtime events by the run_id it read BEFORE the save, so the
        # rotated run's events are never captured. The reuse branch below
        # re-snapshots the latest definition anyway; the version filter
        # added nothing but the rotation. (test_node / reset_test_run
        # already look up by id only.)
        test_run = None
        if workflow.test_run_id:
            test_run = await self.run_repo.aget_by_id(workflow.test_run_id)

        if not test_run:
            test_run = WorkflowRun(
                workflow_id=workflow.id,
                workflow_version=workflow.version,
                workflow_snapshot_id=snapshot_id,
                status=RunStatus.PENDING,
                is_test=True,
                test_mode="full",
                triggered_by=(command.triggered_by or "system"),  # SA-2558
                input_data=command.input_data,
                context=RunContext(
                    variables={
                        "input": command.input_data,
                        "artifacts": {},
                        # __credentials no longer sealed — spec §13.6 phase 1.
                    },
                    topology=topology,
                ),
            )
            self._bind_env(test_run, command)
            _apply_instance_selection(test_run, instance_selection)
            await self.run_repo.asave(test_run)
            workflow.test_run_id = test_run.id
            await self.workflow_repo.asave(workflow)

            # Brand-new run — no prior cycle to race with, so a single
            # orchestrate_start (which acquires its own lock) is fine.
            test_run.status = RunStatus.RUNNING
            await self.run_repo.asave(test_run)
            self._stage_recorder.record(
                run_id=test_run.id,
                event_type="run.started",
                from_status=RunStatus.PENDING.value,
                to_status=RunStatus.RUNNING.value,
                metadata={"is_test": True},
            )
            self._engine.emit_run_status(test_run, "started", input_data=command.input_data)
            # Seed the projector with what we just wrote onto the aggregate.
            #
            # ``CONTEXT`` left the write path in step 5b of
            # solace/specs/wcp-node-output-storage-redesign.md, so the ``asave``
            # above does NOT persist ``context.variables``: the next reload
            # rebuilds it from the event log. ``orchestrate_start`` on the very
            # next line reloads BY ID, so without this event ``input`` and
            # ``artifacts`` live for exactly three statements and every
            # ``{{$input.x}}`` downstream fails with "Source 'input' not found
            # in context" — measured on tenant-1 as 0 of 85 test runs carrying
            # ``input``, against 76 of 76 real runs.
            #
            # ``emit_run_status(..., input_data=...)`` does not cover it: the
            # reducer folds that into ``ProjectedRunState.input_data``, and
            # ``$input`` reads ``variables``. Different field, different reader.
            #
            # ``StartRunUseCase`` has had this since the same step; the two test
            # paths were the half that was never wired.
            self._engine.emit_variables_seeded(
                test_run, dict(test_run.context.variables),
            )
            await self._engine.orchestrate_start(test_run.id)
        else:
            # Reuse existing test run — the prior cycle's worker (e.g.
            # ResumeTaskAction processing the last submit) may still be
            # in flight when /test/full lands. Hold the engine's per-run
            # lock across the entire reset + restart so any pending
            # callback drains FIRST, and no late RunCompleted from the
            # prior cycle can land in the freshly-cleared event log.
            #
            self.logger.info(
                "test_full RESET-PATH entering lock | run_id=%s", test_run.id,
            )
            async with self._engine.locked_run(test_run.id):
                # Re-read the run INSIDE the lock. ``test_run`` above was loaded
                # OUTSIDE the lock; while we waited to acquire it, the prior
                # cycle's still-in-flight orchestration (often an async queue
                # worker) advances the row's VERSION. A CAS save against the
                # stale version then 409s with OPTIMISTIC_CONCURRENCY_CONFLICT
                # and the WHOLE reset — including the new ``input_data`` — is
                # dropped, leaving the run on the previous test's values. The
                # engine lock serializes all orchestration writes for this run,
                # so once held this fresh read is a stable CAS base.
                fresh = await self.run_repo.aget_by_id(test_run.id)
                if fresh is not None:
                    test_run = fresh
                # Bump ``generation`` on the fresh row, inside the lock, so the
                # persisted increment makes the complete/fail guard reject any
                # late callback from the prior generation. (The former pre-lock
                # in-memory bump was never persisted before the lock, so it had
                # no cross-request effect.)
                test_run.generation += 1
                self.logger.info(
                    "test_full RESET-PATH lock acquired | run_id=%s generation=%d",
                    test_run.id, test_run.generation,
                )
                test_run.context.topology = topology
                # SA-2558: re-attribute the reused test run to the caller who
                # launched THIS re-run (the verified actor), not whoever first
                # created the row.
                test_run.triggered_by = command.triggered_by or "system"
                # Re-snapshot so the reset cycle runs against the just-
                # captured definition. Old snapshot row is intentionally
                # left in place (immutable; no GC in the initial cut).
                test_run.workflow_snapshot_id = snapshot_id
                # Re-stamp the version: the run survives workflow saves
                # (each save mints a new version uuid), so without this
                # the row keeps pointing at the version it was created
                # under. Mirrors reset_test_run.
                test_run.workflow_version = workflow.version
                test_run.test_mode = "full"
                test_run.context.completed_nodes.clear()
                test_run.context.skipped_nodes.clear()
                # Retention (SA-1905 E): delete the prior cycle's file-backed
                # outputs before the reassignment below wipes them from
                # variables — else every full re-run leaks the streamed CSV
                # main + artifact files. Best-effort; mirrors reset_test_run.
                await self._cleanup_run_files(
                    retention_variables(test_run, self._view_store),
                    test_run.context.loop_state,
                )
                # Re-seed __credentials on reset too — this dict assignment wipes
                # the prior cycle's map, so without re-adding it the reused test
                # run would run unauthenticated (the original 401 cause).
                test_run.context.variables = {
                    "input": command.input_data,
                    "artifacts": {},
                    # __credentials no longer sealed — spec §13.6 phase 1.
                }
                # SA-2365: re-bind $env on reset too (the assignment above wiped it).
                self._bind_env(test_run, command)
                test_run.context.loop_state.clear()
                self._clear_loop_state_rows(test_run.id)
                test_run.context.child_runs.clear()
                # Clear per-node port / edge mirrors, merge-quorum buffers, and
                # the scratch ``current_node`` pointer. Without this, the next
                # ``GET /data?side=output`` surfaces stale "took success port"
                # info from the prior run, and MERGE nodes inside loops would
                # reuse quorum state left over from the previous execution.
                test_run.context.last_output_ports.clear()
                test_run.context.last_taken_edge_ports.clear()
                test_run.context.merge_quorum_buffers.clear()
                test_run.context.pending_revisits.clear()
                test_run.context.current_node = ""
                test_run.input_data = command.input_data
                test_run.status = RunStatus.PENDING
                test_run.error = None
                test_run.output_data = None
                test_run.completed_at = None
                # Delete stale tasks from previous test executions so
                # check_run_completion does not see orphaned RUNNING tasks.
                for stale_task in await self.task_repo.alist_by_run(test_run.id):
                    stale_task.status = TaskStatus.CANCELLED
                    stale_task.completed_at = datetime.now(timezone.utc).isoformat()
                    await self.task_repo.asave(stale_task)
                # Wipe the prior generation's event log + projector cache.
                # Lifecycle events (RunStarted, etc.) use deterministic event_ids
                # keyed on run.id, so re-emitting them on the same run dedupes
                # silently in the log — leaving the projector stuck on the
                # previous terminal state (CANCELLED / COMPLETED) while the
                # aggregate moves to RUNNING. Clearing the log lets the new
                # generation's events project from a clean slate.
                if self._event_log_repo is not None:
                    self._event_log_repo.clear(test_run.id)
                # Wipe projector snapshots too, otherwise on cold cache the
                # CachedProjectorRunContextViewStore (when SNAPSHOT_MODE=
                # authoritative) loads the prior cycle's snapshot — which
                # was saved at the COMPLETED terminal — and tail-replays
                # ``list_by_run_since(snap.sequence)``. Since clear() reset
                # the per-run sequence counter, the new cycle's events all
                # have ``sequence < snap.sequence`` so the tail is empty
                # and the loaded state ``status="COMPLETED"`` wins. The
                # next ``submit`` then 409s with INVALID_RUN_STATUS.
                if self._snapshot_repo is not None:
                    # ``delete_older_than`` removes snapshots with
                    # ``sequence < keep_sequence``. Use INT32 max so the
                    # value fits the HANA ``sequence`` column (INT, not
                    # BIGINT) — ``2**31`` overflows with HANA error -10427.
                    self._snapshot_repo.delete_older_than(
                        test_run.id, keep_sequence=2**31 - 1,
                    )
                invalidate = getattr(self._view_store, "invalidate", None)
                if callable(invalidate):
                    invalidate(test_run.id)

                # SA-2424: (re)apply this call's instance pin — overwrites or
                # clears any pin left on the reused test run from a prior /test/full.
                _apply_instance_selection(test_run, instance_selection)

                test_run.status = RunStatus.RUNNING
                await self.run_repo.asave(test_run)

                self._stage_recorder.record(
                    run_id=test_run.id,
                    event_type="run.started",
                    from_status=RunStatus.PENDING.value,
                    to_status=RunStatus.RUNNING.value,
                    metadata={"is_test": True},
                )

                self._engine.emit_run_status(test_run, "started", input_data=command.input_data)
                # Same seed as the fresh branch above, and this is the branch
                # that matters more: a permanent test run is on it for every
                # re-run after its first. Emitted AFTER the ``clear()`` so it
                # survives, and after the ``variables`` reassignment so it
                # carries the new input rather than the prior cycle's.
                # ``__credentials`` is stripped by ``emit_variables_seeded``.
                self._engine.emit_variables_seeded(
                    test_run, dict(test_run.context.variables),
                )

                # We already hold the lock — call the locked variant to
                # avoid re-acquiring (asyncio.Lock is not reentrant).
                await self._engine.orchestrate_start_locked(test_run.id)

        return TestFullWorkflowResult(
            run_id=test_run.id,
            workflow_id=workflow.id,
            status=test_run.status.value,
            output_data=test_run.output_data,
            error=test_run.error or "",
        )
