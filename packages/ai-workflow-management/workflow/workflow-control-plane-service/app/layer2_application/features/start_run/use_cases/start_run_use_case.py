"""Start a new workflow run."""

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
from app.layer1_domain.entities.workflow import Workflow
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer1_domain.entities.data_reference import is_file_ref
from app.layer1_domain.value_objects.instance_selection import (
    normalize_selection,
    to_run_selection,
    SELECTION_METADATA_KEY,
)
from app.layer2_application.features.start_run.use_cases.env_assignment_guard import (
    check_environment_assignment,
)
from app.layer2_application.features.start_run.use_cases.section_run_guard import (
    check_section_run,
)
from app.layer2_application.read.schema_validator import SchemaValidator
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.interfaces.orchestration_queue_interface import (
    IOrchestrationQueue,
    StartRunAction,
)
from app.layer2_application.errors import ErrorCode
from app.layer2_application.credentials import RunCredentialSeeder


@dataclass
class StartRunCommand:
    workflow_id: str = ""
    input_data: dict[str, Any] = field(default_factory=dict)
    tenant_id: str = "system"
    correlation_id: str = ""
    callback_topic: str = ""
    predefined_run_id: Optional[str] = None
    parent_run_id: str | None = None
    parent_node_id: str | None = None
    depth_level: int = 0
    skip_validation: bool = True
    execution_mode: str = "auto"  # "auto" | "manual"
    # Per-invocation custom variable overrides exposed to expressions
    # as ``{{$variable.X}}``. Merged on top of ``Workflow.variables``
    # defaults by ``ResolutionScopeBuilder`` at scope-build time.
    # Persisted on the (root) run so sub-workflows inherit through
    # the engine's parent-scope chain.
    user_variables: dict[str, Any] = field(default_factory=dict)
    # Free-form caller metadata, stored verbatim on the run and echoed back
    # onto every outbound external message. Exposed to expressions as
    # ``{{$run.metadata.X}}``.
    metadata: dict[str, Any] = field(default_factory=dict)
    # Runtime credential map: name -> {type, config}. Stored ENCRYPTED under
    # the reserved key "__credentials"; never echoed or exposed in plaintext.
    runtime_credentials: dict[str, dict] = field(default_factory=dict)
    # SA-2365: the environment selected for this run. Its non-secret variables
    # (text/base_url/list) are injected as the ``$env`` source so node fields
    # can reference ``{{$env.KEY}}``. Empty = no environment bound.
    environment_id: str = ""
    # SA-2424: optional per-node worker-instance pin (debug routing).
    # {mode, by_worker_type, by_node}. Structurally validated here and stashed
    # on the run metadata (reserved key); the engine resolves it per node at
    # dispatch and the executor fails fast if a pinned instance is ineligible.
    instance_selection: dict[str, Any] = field(default_factory=dict)
    # SA-2558: display label of the server-verified caller who triggered the run
    # (resolved in the controller from the BFF session / Bearer JWT — never from
    # the request body). Persisted verbatim on the run as its attribution.
    triggered_by: str = ""



@dataclass
class StartRunResult:
    id: str = ""
    workflow_id: str = ""
    status: str = ""
    started_at: str = ""
    completed_at: str | None = None
    error: str | None = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)
    output_data: dict[str, Any] | None = None
    execution_mode: str = "auto"  # "auto" | "manual"



class StartRunUseCase:
    def __init__(self, logger: ILogger, workflow_repo: IWorkflowRepository,
                 workflow_snapshot_repo: Any,
                 run_repo: IRunRepository, task_repo: ITaskRepository,
                 orchestration_engine: IOrchestrationEngine,
                 stage_recorder: StageRecorder,
                 data_store_service: Any = None,
                 orchestration_queue: Optional[IOrchestrationQueue] = None,
                 credential_cipher: Any = None,
                 vault_credential_client: Any = None,
                 environment_repo: Any = None,
                 workflow_environment_repo: Any = None,
                 workflow_section_repo: Any = None,
                 section_membership_repo: Any = None,
                 worker_config_repo: Any = None,
                 **kwargs: Any) -> None:
        self.logger = logger
        self._environment_repo = environment_repo
        self._workflow_environment_repo = workflow_environment_repo  # SA-2450 run guard
        self._workflow_section_repo = workflow_section_repo  # SA-2605 run guard
        self._section_membership_repo = section_membership_repo  # SA-2605 run guard
        self.workflow_repo = workflow_repo
        self.workflow_snapshot_repo = workflow_snapshot_repo
        self.run_repo = run_repo
        self.task_repo = task_repo
        self._engine = orchestration_engine
        self._stage_recorder = stage_recorder
        self._data_store_service = data_store_service
        self._queue = orchestration_queue if orchestration_queue is not None else getattr(getattr(self, "_engine", None) or getattr(self, "_orchestration_engine", None), "_queue", None)
        self._credential_cipher = credential_cipher
        self._vault_credential_client = vault_credential_client
        self._worker_config_repo = worker_config_repo  # SA-2437
        # Seals the CALLER's runtime_credentials only. Node credential_refs
        # are no longer resolved here — they are fetched at dispatch by
        # RunCredentialProvider (SA-2287 / B3). ``vault_credential_client`` is
        # still accepted above so the DI wiring is unchanged, and is now unused
        # by this use case.
        self._credential_seeder = RunCredentialSeeder(cipher=credential_cipher)
        # SA-2372: per-process round-robin cursors for list-type env variables.
        from app.layer2_application.features.start_run.use_cases.env_pool import (
            PoolCursorRegistry,
        )
        self._pool_cursors = PoolCursorRegistry()

    async def execute(self, command: StartRunCommand) -> StartRunResult:
        return await self._start_run(command)

    def _bind_environment(self, run: Any, req: StartRunCommand) -> None:
        """Inject the selected environment's non-secret variables as ``$env``.

        Delegates to the shared ``load_and_bind`` (used by TestFullWorkflow too).
        No-op when no environment is bound, the repo is absent, the environment
        is missing/inactive, or it belongs to another tenant.
        """
        from app.layer2_application.features.start_run.use_cases.env_pool import (
            load_and_bind,
        )
        load_and_bind(
            run,
            tenant_id=req.tenant_id,
            environment_id=req.environment_id,
            repo=self._environment_repo,
            cursors=self._pool_cursors,
            logger=self.logger,
        )

    async def _start_run(self, req: StartRunCommand) -> StartRunResult:
        self.logger.info("Starting run", workflow_id=req.workflow_id)

        workflow: Workflow | None = await self.workflow_repo.aget_by_latest_publish_version(req.workflow_id)
        if not workflow:
            return StartRunResult(
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
                error_details={"workflow_id": req.workflow_id},
            )

        # Environment run guard. SA-2605 (section-only binding) takes PRECEDENCE:
        # if the workflow is bound to a section (new model), the selected
        # environment must be a member of that section, and the legacy SA-2450
        # subset guard is skipped. Otherwise fall back to SA-2450 (a workflow
        # bound via the old per-flow-subset model, or unbound → any concrete
        # environment_id is rejected). Empty/inherit are always allowed. Fires on
        # every start path (manual, /test/full, triggers + schedules funnel here).
        section_bound = False
        if self._workflow_section_repo is not None:
            try:
                _binding = self._workflow_section_repo.get_by_workflow(
                    tenant_id=req.tenant_id, workflow_id=req.workflow_id
                )
                section_bound = _binding is not None and bool((_binding.section or "").strip())
            except Exception:
                section_bound = False

        if section_bound:
            section_reject = check_section_run(
                workflow_section_repo=self._workflow_section_repo,
                section_membership_repo=self._section_membership_repo,
                tenant_id=req.tenant_id,
                workflow_id=req.workflow_id,
                environment_id=req.environment_id,
            )
            if section_reject:
                return StartRunResult(
                    error=section_reject,
                    error_code=ErrorCode.ENVIRONMENT_NOT_IN_SECTION.code,
                    error_details={"workflow_id": req.workflow_id, "environment_id": req.environment_id},
                )
        else:
            env_reject = check_environment_assignment(
                repo=self._workflow_environment_repo,
                tenant_id=req.tenant_id,
                workflow_id=req.workflow_id,
                environment_id=req.environment_id,
            )
            if env_reject:
                return StartRunResult(
                    error=env_reject,
                    error_code=ErrorCode.ENVIRONMENT_NOT_ASSIGNED.code,
                    error_details={"workflow_id": req.workflow_id, "environment_id": req.environment_id},
                )

        # SA-2424: structurally validate the optional inline instance pin up front
        # so a bad ``mode`` fails at POST /runs (400) rather than mid-orchestration.
        try:
            instance_selection = normalize_selection(req.instance_selection)
        except ValueError as exc:
            return StartRunResult(
                error=str(exc),
                error_code=ErrorCode.INPUT_VALIDATION_FAILED.code,
                error_details={"workflow_id": req.workflow_id, "field": "instance_selection"},
            )
        # SA-2437: no inline pin → fall back to the persisted per-workflow config
        # (applies on EVERY run path, including triggers/schedules). Per-run inline
        # override wins over the saved row.
        if not instance_selection and self._worker_config_repo is not None:
            try:
                saved = self._worker_config_repo.get(req.workflow_id)
                if saved:
                    instance_selection = to_run_selection(saved.mode, saved.selection)
            except Exception as exc:  # never fail a run because config load hiccuped
                self.logger.warning("worker-config load failed", workflow_id=req.workflow_id, error=str(exc))

        # Structural DAG validation removed — execution is fan-out driven.
        # Input-schema validation still runs unless explicitly skipped.
        if not req.skip_validation and workflow.input_schema:
            schema_errors = SchemaValidator.validate_input(req.input_data, workflow.input_schema)
            if schema_errors:
                return StartRunResult(
                    error=ErrorCode.INPUT_VALIDATION_FAILED.default_message,
                    error_code=ErrorCode.INPUT_VALIDATION_FAILED.code,
                    error_details={"workflow_id": req.workflow_id, "errors": schema_errors},
                )

        topology = GraphTopology.from_workflow(workflow)

        # Capture an immutable snapshot. The engine reads through this
        # for every callback (``_load_workflow_for_run``); mid-run edits
        # to the workflow cannot leak into this run.
        from app.layer2_application.definition.workflow_snapshot_loader import capture_snapshot
        snapshot_id = capture_snapshot(workflow, self.workflow_snapshot_repo)

        # Store workflow input as CSV file (always, even if empty, for consistent file_ref format)
        input_var: Any = req.input_data or {}
        if self._data_store_service:
            input_var = await self._data_store_service.store_data("input", input_var)

        run = WorkflowRun(
            workflow_id=req.workflow_id,
            workflow_version=workflow.version,
            workflow_snapshot_id=snapshot_id,
            status=RunStatus.PENDING,
            input_data=req.input_data,
            context=RunContext(
                variables={
                    "input": input_var,
                    "workflow": {
                        "id": workflow.id,
                        "name": workflow.name,
                        "version": workflow.version,
                        "description": workflow.description,
                    },
                    "artifacts": {},
                },
                topology=topology,
            ),
            parent_run_id=req.parent_run_id,
            parent_node_id=req.parent_node_id,
            depth_level=req.depth_level,
            tenant_id=req.tenant_id,
            correlation_id=req.correlation_id,
            callback_topic=req.callback_topic,
            execution_mode=req.execution_mode,
            user_variables=dict(req.user_variables or {}),
            metadata=dict(req.metadata or {}),
            # SA-2558: attribute the run to the server-verified caller (falls
            # back to the "system" sentinel for automated / M2M / schedule runs).
            triggered_by=(req.triggered_by or "system"),
        )
        # SA-2424: stash the (already-validated) instance pin on the run metadata
        # under a reserved key. metadata is persisted, inherited by child runs via
        # root_run_id, and already flows to task dispatch — so the engine can
        # resolve the pin per node at every dispatch with no schema migration.
        if instance_selection:
            # SA-2437: tag with the flow id so the engine applies it ONLY to this
            # flow's nodes, never to a sub-flow it calls (see _pin_selection_for).
            instance_selection["workflow_id"] = req.workflow_id
            run.metadata[SELECTION_METADATA_KEY] = instance_selection

        if req.predefined_run_id:
            run.id = req.predefined_run_id
            # __post_init__ set root_run_id to the auto-generated id; re-sync for
            # top-level runs so root_run_id tracks the new id.
            if run.parent_run_id is None:
                run.root_run_id = run.id

        # Seed run metadata into context (run.id is now final)
        run.context.variables["run"] = {
            "id": run.id,
            "tenant_id": req.tenant_id,
            "correlation_id": req.correlation_id,
            "started_at": run.started_at,
            # Expose caller metadata to expressions as ``{{$run.metadata.X}}``.
            "metadata": run.metadata,
        }

        # SA-2365: bind the selected environment's NON-secret variables as the
        # ``$env`` source, so node fields resolve ``{{$env.KEY}}`` for this run.
        # Secret (credential-type) variables are intentionally NOT injected here
        # — the platform never persists secrets in the run context (SA-2287);
        # those resolve at dispatch through the credential seam.
        self._bind_environment(run, req)

        # NOTHING is sealed onto the run any more — phase 1 of retiring
        # ``__credentials`` (spec §13.6). SA-2287/B3 had already moved the vault
        # half to the egress boundary: each node fetches its own
        # ``credential_ref`` when it dispatches. What was left here was the
        # caller's inline ``runtime_credentials``, and that was the ONE key
        # keeping ``CONTEXT`` alive — it is not event-sourced (LAI-19 forbids
        # the sealed map entering a replayable store) and no new table may hold
        # it, so ``DROP CONTEXT`` would have destroyed it.
        #
        # The field is still ACCEPTED on the request and ignored, so an older
        # caller — ``executor-runtime-service`` still sends a bearer token as
        # ``"default"`` — does not start failing on a version skew. Its own
        # cleanup follows separately.
        #
        # Consequence, accepted by the operator: a node that relied on borrowing
        # the run's ``"default"`` entry now authenticates only if it declares a
        # ``credential_ref``. One that declares a real ``auth_type`` and finds
        # nothing fails CLOSED and loudly (``resolve_effective_auth`` raises);
        # one with ``auth_type`` none/"" goes out unauthenticated.

        await self.run_repo.asave(run)

        self._stage_recorder.record(
            run_id=run.id,
            event_type="run.created",
            from_status="",
            to_status=RunStatus.PENDING.value,
            metadata={"workflow_id": req.workflow_id},
        )

        # Manual mode: suspend instead of auto-executing
        if req.execution_mode == "manual":
            run.status = RunStatus.SUSPENDED
            await self.run_repo.asave(run)

            self._stage_recorder.record(
                run_id=run.id,
                event_type="run.suspended",
                from_status=RunStatus.PENDING.value,
                to_status=RunStatus.SUSPENDED.value,
                metadata={"execution_mode": "manual"},
            )

            self._engine.emit_run_status(run, "suspended")

            return StartRunResult(
                id=run.id,
                workflow_id=run.workflow_id,
                status=run.status.value,
                started_at=run.started_at,
                completed_at=run.completed_at,
                error=run.error,
                output_data=run.output_data,
                execution_mode=run.execution_mode,
            )

        # Auto mode: Transition to RUNNING and publish run.started
        run.status = RunStatus.RUNNING
        await self.run_repo.asave(run)

        self._stage_recorder.record(
            run_id=run.id,
            event_type="run.started",
            from_status=RunStatus.PENDING.value,
            to_status=RunStatus.RUNNING.value,
        )

        # run.status=started carries input_data so the projector's
        # ``input_data`` field is rebuildable from the log — but it carried the
        # PAYLOAD to do it, and that is 86.30 MB of AIW_EVENT_LOG on tenant-1
        # (5,939 events, p50 125 KB, max 341 KB, 100% raw values on every one of
        # the 400 largest sampled). A reference rebuilds the field just as well
        # at roughly 200 bytes.
        #
        # No new I/O buys this: ``input_var`` above is the file_ref that
        # ``store_data("input", ...)`` already produced for
        # ``variables["input"]``. The event was re-serialising, from
        # ``req.input_data``, bytes that had just been written to the file
        # service three statements earlier.
        #
        # ``run.input_data`` — the AGGREGATE field, column
        # AIW_WORKFLOW_RUNS.INPUT_DATA — deliberately stays raw. It is the
        # fallback ``$input`` reads when a file_ref's ``extracted`` cache is
        # empty (scope_providers.py:372), and converting it too would empty the
        # variable-suggestion dropdown with no error.
        #
        # Falls back to the raw dict when no store is wired (tests), which is
        # byte-identical to the previous behaviour.
        self._engine.emit_run_status(
            run, "started",
            input_data=input_var if is_file_ref(input_var) else (req.input_data or {}),
        )
        # Emit NodeOutputProduced with the bootstrap-seeded variables
        # (``input``, ``workflow``, ``artifacts``, ``run``) so the
        # projector's ``variables`` dict matches the live aggregate.
        # Without this a shadow-mode read sees drift on every fresh run.
        seeded_vars = {
            k: v for k, v in run.context.variables.items()
            if k != "__credentials"
        }
        self._engine.emit_variables_seeded(run, seeded_vars)
        # Flush before branching: publish_event buffers via EventCollector,
        # and the queue path returns without further draining. The collector
        # lives in a contextvar local to this async task — the orchestration
        # worker pulled from the queue runs in a different context and won't
        # see our buffer. Without this flush, run.started never reaches the
        # broker in the queue path.
        await self._engine.drain_events()

        # Enqueue orchestration for async processing
        await self._queue.enqueue(StartRunAction(run_id=run.id))

        return StartRunResult(
            id=run.id,
            workflow_id=run.workflow_id,
            status=run.status.value,
            started_at=run.started_at,
            completed_at=run.completed_at,
            error=run.error,
            output_data=run.output_data,
            execution_mode=run.execution_mode,
        )


