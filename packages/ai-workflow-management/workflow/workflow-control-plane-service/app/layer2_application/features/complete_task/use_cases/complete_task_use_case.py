"""Complete a running task."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer2_application.interfaces.orchestration_engine_interface import IOrchestrationEngine
from app.layer1_domain.value_objects.task_status import TaskStatus
# Module-level on purpose: ``_record_node_output`` needs it, and the other
# use of ``is_file_ref`` in this file is a function-local import that is not in
# scope there. Relying on that one would raise NameError the first time a
# node_output_repo is actually wired — a path no test reaches while the repo
# defaults to None, because the ``or`` short-circuits.
from app.layer1_domain.entities.data_reference import collect_file_ids, is_file_ref
from app.layer2_application.data import task_metadata
from app.layer2_application.resolve.variable_resolver import VariableResolver
from app.layer2_application.orchestration.successor_processor import BUILTIN_NODE_TYPES, PENDING_NODE_TYPES
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.interfaces.orchestration_queue_interface import (
    IOrchestrationQueue,
    ContinueRunAction,
)
from app.layer2_application.run_save_retry import save_run_with_cas_retry
from app.layer2_application.errors import ErrorCode
from app.layer2_application.generation_guard import should_reject_callback
from app.layer2_application.interfaces.app_config_interface import IAppConfig


class _PhaseTimer:
    """Per-invocation stopwatch for the ``complete_task`` hot path.

    Deliberately allocation-light and never raises: this runs on the worker
    result path for every completed node, so it must cost nothing measurable
    and must never be the reason a callback fails.
    """

    __slots__ = ("_t0", "_last", "_marks")

    def __init__(self) -> None:
        self._t0 = time.perf_counter()
        self._last = self._t0
        self._marks: list[tuple[str, float]] = []

    def mark(self, name: str) -> None:
        now = time.perf_counter()
        self._marks.append((name, (now - self._last) * 1000.0))
        self._last = now

    @property
    def total_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000.0

    def render(self) -> str:
        return " ".join(f"{n}={ms:.0f}" for n, ms in self._marks) or "-"


# Phase 3 of the arrow-size work. Default TRUE, so this ships byte-for-byte
# inert and moves by env alone — in both directions.
#
# Read per import like the rest of this module's settings; flipping it needs a
# restart, which is fine because it is a deliberate, gated step (see
# ``solace/specs/wcp-arrow-size-reduction-plan.md`` §3.4) and not a knob anyone
# should be turning under load.
TASK_OUTPUT_MIRROR_METADATA: bool = os.getenv(
    "TASK_OUTPUT_MIRROR_METADATA", "true",
).strip().lower() in ("1", "true", "yes")


def build_task_output(
    outputs: Any,
    *,
    output_reference: str = "",
    mirror_metadata: bool = True,
) -> dict[str, Any]:
    """The node's output value, from what the worker returned.

    Envelope keys first, then the raw worker outputs spread ON TOP, so keys like
    ``response_body`` and ``status_code`` are directly reachable by
    ``output_mapping`` and by downstream expressions.

    ``mirror_metadata`` controls the historical duplicate. With it on, the whole
    worker output is ALSO stored under ``metadata`` — measured on a real live
    value, exactly 50% of the stored bytes, paid twice in the CSV tree, twice in
    the Arrow sidecar and twice in HANA.

    Turning it off is safe only because ``_rewrite_legacy_path`` already rewrites
    ``metadata.X -> X`` for the 15 tenant-1 workflows (69 expressions) that read
    the ``metadata`` spelling; 0 read it as a whole object. Ship that alias,
    watch it, then flip this — never the other order
    (``DELETE_WRITE_BEFORE_READ_MIGRATION``).

    A worker whose OWN output has a key named ``metadata`` wins either way: with
    the mirror on, the spread overwrites it; with it off, it is the only one.
    """
    task_output: dict[str, Any] = {}
    # Only surface the file-pointer envelope when the worker actually produced a
    # stored output reference. Every current node worker returns inline outputs
    # (no ``uri``), so ``output_reference`` is empty and an unconditional
    # ``{"uri": ""}`` was pure noise in every node's output.
    if output_reference:
        task_output["output"] = {"uri": output_reference}
    if mirror_metadata:
        task_output["metadata"] = outputs if outputs else {}
    if isinstance(outputs, dict):
        task_output.update(outputs)
    return task_output



@dataclass
class CompleteTaskCommand:
    run_id: str = ""
    task_id: str = ""
    status: str = "COMPLETED"
    output_reference: str = ""
    output_metadata: dict[str, Any] = field(default_factory=dict)
    # How long the WORKER itself ran, as the worker reported it. All three
    # executor completion publishers already put this on the wire
    # (``execute_task_usecase`` for push, ``handle_worker_callback_usecase``
    # for the async-callback and pull paths) and the control plane dropped it
    # on the floor — ``Task.execution_time_ms`` and
    # ``NodeCompleted.execution_time_ms`` have both existed, unset, the whole
    # time. Carrying it is what splits the biggest cost in a run:
    # ``NodeDispatched -> NodeOutputProduced`` is 58.6% of wall clock
    # (measured 2026-08-11) and nothing said how much of it was the worker
    # versus the path to it.
    execution_time_ms: int = 0
    idempotency_key: str = ""



@dataclass
class CompleteTaskResult:
    completed: bool = False
    status: str = ""
    idempotent: bool = False
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)



class CompleteTaskUseCase:
    def __init__(self, logger: ILogger, workflow_repo: IWorkflowRepository, run_repo: IRunRepository, task_repo: ITaskRepository,
                 orchestration_engine: IOrchestrationEngine,
                 stage_recorder: StageRecorder,
                 variable_resolver: Optional[VariableResolver] = None,
                 data_store_service: Any = None,
                 orchestration_queue: Optional[IOrchestrationQueue] = None,
                 app_config: IAppConfig | None = None,
                 node_output_repo: Any = None,
                 **kwargs: Any) -> None:
        self.logger = logger
        self.workflow_repo = workflow_repo
        self.run_repo = run_repo
        self.task_repo = task_repo
        self._engine = orchestration_engine
        self._stage_recorder = stage_recorder
        self._variable_resolver = variable_resolver
        self._data_store_service = data_store_service
        self._app_config = app_config
        # AIW_RUN_NODE_OUTPUT (migration 055). Optional: absent in every test
        # that predates it and on any deployment that has not wired it, and the
        # write is a no-op then.
        self._node_output_repo = node_output_repo
        self._queue = orchestration_queue if orchestration_queue is not None else getattr(getattr(self, "_engine", None) or getattr(self, "_orchestration_engine", None), "_queue", None)

    def _narrow_task_metadata(self, metadata: Any, task: Any) -> Any:
        """Apply the ``output_metadata`` contract — see ``data/task_metadata.py``.

        Three modes, defaulting to ``off`` so this ships inert
        (``TASK_METADATA_MODE``):

        * ``off``    — return the value unchanged. Byte-for-byte today.
        * ``shadow`` — return it unchanged, but LOG what would be dropped. This
          is the mode that earns the allowlist its trust: the sample behind it
          is 279 post-purge tasks holding **zero PENDING tasks**, so it sizes
          the payload half well and cannot confirm the dispatch half. A key the
          control plane actually needs shows up here as a dropped key, in
          production, before anything is deleted.
        * ``on``     — narrow.

        Never raises. A completion must not fail because metadata was odd —
        the authoritative output was already stored by this point.
        """
        try:
            if not (task_metadata.enforcing() or task_metadata.observing()):
                return metadata
            report = task_metadata.narrow(metadata)
            if report.changed:
                self.logger.info(
                    "task-metadata %s node=%s %s",
                    "narrowed" if task_metadata.enforcing() else "shadow",
                    getattr(task, "node_id", "?"),
                    report.summary(),
                )
            return report.kept if task_metadata.enforcing() else metadata
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"task-metadata narrowing skipped: {exc}")
            return metadata

    async def execute(self, command: CompleteTaskCommand) -> CompleteTaskResult:
        phases = _PhaseTimer()
        try:
            return await self._complete_task(command, phases)
        finally:
            # Measured 2026-08-05 on tenant-1: the span from the worker's
            # callback reaching the executor to the CP logging "Processing
            # continue" is 1.75-13.88s, against ~0.21s for ALL CP<->executor
            # <->worker transport and ~0.55s for the worker's own HTTP GET.
            # That span is two broker hops with this use case in the middle,
            # and nothing separated the hops from the work. This line does.
            self.logger.info(
                "complete_task timing",
                task_id=command.task_id,
                run_id=command.run_id,
                total_ms=round(phases.total_ms),
                phases=phases.render(),
            )

    async def _complete_task(
        self, req: CompleteTaskCommand, phases: "_PhaseTimer | None" = None,
    ) -> CompleteTaskResult:
        phases = phases if phases is not None else _PhaseTimer()
        self.logger.info("Completing task", task_id=req.task_id, run_id=req.run_id, status=req.status)

        run = await self.run_repo.aget_by_id(req.run_id)
        if not run:
            return CompleteTaskResult(
                error=ErrorCode.RUN_NOT_FOUND.default_message,
                error_code=ErrorCode.RUN_NOT_FOUND.code,
                error_details={"run_id": req.run_id},
            )

        task = await self.task_repo.aget_by_id(req.task_id)
        phases.mark("load")
        if not task:
            return CompleteTaskResult(
                error=ErrorCode.TASK_NOT_FOUND.default_message,
                error_code=ErrorCode.TASK_NOT_FOUND.code,
                error_details={"task_id": req.task_id},
            )

        if should_reject_callback(task, run, self.logger, handler="complete", app_config=self._app_config):
            return CompleteTaskResult(
                completed=False,
                error=ErrorCode.TASK_SUPERSEDED.default_message,
                error_code=ErrorCode.TASK_SUPERSEDED.code,
                error_details={
                    "task_id": req.task_id,
                    "task_generation": getattr(task, "run_generation", 0),
                    "run_generation": getattr(run, "generation", 0),
                },
            )

        # No terminal-status short-circuit (option 1 — re-drive on replay): an
        # at-least-once redelivery of ``task.completed`` re-runs the body. This
        # is idempotent — the re-emitted ``NodeOutputProduced`` is dropped by the
        # event log's deterministic ``(run_id, event_id)`` dedupe (append returns
        # False, never raises), ``NodeCompleted`` folds into the projector's
        # ``completed_nodes`` frozenset, ``store_data`` is content-hash keyed, and
        # successor dispatch is edge-deduped. The terminal status is persisted
        # FIRST (below), so it stays correct for both EXECUTOR_MODE=in_process and
        # =executor (the emit-driven consumers read the persisted status).
        workflow = self._engine.load_workflow_for_run(run)
        if not workflow:
            return CompleteTaskResult(
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
                error_details={"workflow_id": run.workflow_id},
            )

        # Persist the terminal status FIRST (before the emit/enqueue side
        # effects). This keeps it correct under EXECUTOR_MODE=executor, where the
        # emits drive consumers that read the persisted ``task.status``. Replay
        # safety comes from the idempotent sinks (see the note above), not from
        # deferring this save.
        task.status = TaskStatus.COMPLETED
        task.output_ref = req.output_reference
        # This assignment is why AIW_TASKS.OUTPUT_METADATA is 99.997% worker
        # payload (spec sections 17 and 18). ``narrow`` keeps the control-plane
        # contract and drops the echo — the payload's real homes are
        # AIW_RUN_NODE_OUTPUT + the file service, or AIW_EVENT_LOG for the
        # deliberately-inline ``code``/``approval_flow`` types, both verified
        # per task in 17.8. ``task_output`` below still gets the FULL payload:
        # this narrows what the TASK stores, never what the node outputs.
        task.output_metadata = self._narrow_task_metadata(req.output_metadata, task)
        # Worker-reported duration. Only overwrite with a real value: a
        # replayed or partial result carrying 0 must not erase a figure an
        # earlier delivery already established.
        if req.execution_time_ms:
            task.execution_time_ms = int(req.execution_time_ms)
        task.completed_at = datetime.now(timezone.utc).isoformat()
        await self.task_repo.asave(task)
        phases.mark("task_save")

        self._stage_recorder.record(
            run_id=req.run_id, node_id=task.node_id, task_id=req.task_id,
            event_type="node.completed",
            from_status=TaskStatus.RUNNING.value,
            to_status=TaskStatus.COMPLETED.value,
            metadata={"output_ref": req.output_reference},
        )

        task_output: dict[str, Any] = build_task_output(
            req.output_metadata,
            output_reference=req.output_reference,
            mirror_metadata=TASK_OUTPUT_MIRROR_METADATA,
        )

        node = workflow.get_node(task.node_id)
        if not node:
            return CompleteTaskResult(
                error=ErrorCode.NODE_NOT_FOUND.default_message,
                error_code=ErrorCode.NODE_NOT_FOUND.code,
                error_details={"node_id": task.node_id, "workflow_id": run.workflow_id},
            )
        warnings: list[str] = []

        # Apply node definition output_mapping (before output_binding)
        if node and self._variable_resolver:
            snapshot = node.definition_snapshot
            if snapshot:
                output_mapping = snapshot.get("output_mapping") or {}
                if output_mapping:
                    try:
                        task_output = await self._variable_resolver.resolve_mapping(
                            output_mapping, {"_output": task_output}
                        )
                    except Exception as exc:
                        msg = f"output_mapping failed for node {task.node_id}: {exc}"
                        self.logger.warning(msg)
                        warnings.append(msg)

        # Apply output_binding transformation if defined
        if node and node.output_binding and self._variable_resolver:
            try:
                task_output = await self._variable_resolver.resolve_mapping(
                    node.output_binding, {"_output": task_output}
                )
            except Exception as exc:
                msg = f"output_binding failed for node {task.node_id}: {exc}"
                self.logger.warning(msg)
                warnings.append(msg)

        # Extract _artifacts before CSV conversion (workers embed them in output_metadata)
        worker_artifacts = []
        if isinstance(task_output, dict) and "_artifacts" in task_output:
            worker_artifacts = task_output.pop("_artifacts", [])

        # SA-1910 — file_ids the worker created while streaming this output.
        # Popped HERE, before store_data, or the list becomes a column in the
        # node's own CSV and then a field of its value.
        #
        # The worker cannot decide which of them survive: `keep_inline`,
        # `output_binding` and the mapping below can all drop a ref it just
        # built, and it can see none of that. So it reports what it created and
        # the control plane deletes what nothing points at.
        worker_uploaded: list[str] = []
        if isinstance(task_output, dict) and "_uploaded_file_ids" in task_output:
            worker_uploaded = [
                f for f in (task_output.pop("_uploaded_file_ids", []) or [])
                if isinstance(f, str) and f
            ]

        # Store the output as a CSV file reference — EXCEPT for inline-output
        # nodes (code). A code node returns small, structured JSON consumed
        # downstream as real values; file-backing would stringify scalars,
        # split arrays into artifact CSVs, and surface opaque file_id pointers
        # instead of the original data. Keep the raw dict inline so it round-
        # trips verbatim. See ``keeps_inline_output``.
        from app.layer1_domain.entities.data_reference import (
            INLINE_OUTPUT_MAX_BYTES,
            inline_output_size_bytes,
            is_file_ref,
            keeps_inline_output,
        )
        snapshot = node.definition_snapshot or {}
        # Probe every place the worker identity can live: ``task.worker_type``
        # is the value that actually routed this task to the code worker (most
        # authoritative); ``node.worker_type`` / the definition snapshot's
        # ``base_worker_id`` are the static fallbacks set at workflow save time.
        inline_type = (
            keeps_inline_output(getattr(task, "worker_type", None))
            or keeps_inline_output(getattr(node, "worker_type", None))
            or keeps_inline_output(snapshot.get("base_worker_id"))
        )
        # SA-1951 — the type is only half the question; the value must also be
        # small enough to ride an event. An oversized "inline" output used to be
        # published raw and get 413'd by Event Mesh, losing the result silently.
        # Measure only when the type already qualifies (the size probe is a full
        # serialize — don't pay for it on the file-backed path).
        output_size = inline_output_size_bytes(task_output) if inline_type else 0
        keep_inline = inline_type and output_size <= INLINE_OUTPUT_MAX_BYTES
        self.logger.info(
            "complete_task output storage decision",
            task_id=req.task_id,
            node_id=task.node_id,
            task_worker_type=getattr(task, "worker_type", None),
            node_worker_type=getattr(node, "worker_type", None),
            base_worker_id=snapshot.get("base_worker_id"),
            inline_type=inline_type,
            output_size_bytes=output_size,
            inline_max_bytes=INLINE_OUTPUT_MAX_BYTES,
            keep_inline=keep_inline,
        )
        if inline_type and not keep_inline:
            self.logger.warning(
                "inline-output node exceeded the inline ceiling — file-backing it "
                "instead (an oversized inline output cannot ride an event)",
                task_id=req.task_id,
                node_id=task.node_id,
                output_size_bytes=output_size,
                inline_max_bytes=INLINE_OUTPUT_MAX_BYTES,
            )
        if self._data_store_service and isinstance(task_output, dict) and not keep_inline:
            if not is_file_ref(task_output):
                task_output = await self._data_store_service.store_data(task.node_id, task_output)
                phases.mark("store_data")

        # Attach worker-produced artifacts to the file reference
        if worker_artifacts and isinstance(task_output, dict):
            from app.layer1_domain.entities.data_reference import add_artifact
            for art in worker_artifacts:
                if isinstance(art, dict) and art.get("file_id"):
                    add_artifact(
                        task_output,
                        file_id=art["file_id"],
                        name=art.get("name", ""),
                        content_type=art.get("content_type", "text/csv"),
                        size_bytes=art.get("size_bytes", 0),
                        artifact_type=art.get("artifact_type", "data"),
                    )

        # SA-1910 — whatever the worker uploaded that the stored output does
        # NOT point at is an orphan by construction, not by guess: the walk is
        # over the value that is about to be persisted. Runs after the artifact
        # attachment above, because that is the last thing that can make a
        # file_id reachable.
        #
        # Bounded honestly: this is the "callback arrived, the ref did not
        # survive" case. A callback that never arrives takes the list with it,
        # and only a sweeper can find those files.
        if worker_uploaded and self._data_store_service:
            referenced = collect_file_ids(task_output)
            orphans = [f for f in worker_uploaded if f not in referenced]
            if orphans:
                await self._data_store_service.gc_files(
                    orphans,
                    reason=f"worker-uploaded but unreferenced by node {task.node_id}",
                )

        # Phase 1a engine-split: route the variable write-set through
        # the engine's ``apply_state_updates_as_files`` so a projector
        # replaying the event log sees the full write before any
        # downstream reader could observe a mutation. Alias propagation
        # (node.name) lives inside that helper — no need to double-write
        # here.
        await self._engine.apply_state_updates_as_files(
            {task.node_id: task_output},
            run,
            workflow,
            source_node_id=task.node_id,
            causation_id=task.id,
            output_port="success",
            attempt_id=task.idempotency_key or "",
            # The AIW_RUN_NODE_OUTPUT mirror moved INTO this helper so the
            # builtin paths get it too; the task carries the (generation,
            # iteration, attempt) stamps that form the row's primary key.
            task=task,
        )
        phases.mark("apply_state")
        from app.layer1_domain.entities.data_reference import register_artifacts
        register_artifacts(
            run.context.variables,
            task_output,
            node_id=task.node_id,
            task_id=task.id,
        )

        self._engine.emit_node_completed(task, run, node)

        # Phase M.2.2: wrap the aggregate save in CAS retry. The only
        # mutation persisted here is ``register_artifacts``'s write to
        # ``run.context.variables["artifacts"]`` (idempotent set of
        # provenance-keyed entries). On conflict, reload and re-apply
        # the registry write against the fresh run.
        def _apply_artifacts(fresh_run: Any) -> None:
            register_artifacts(
                fresh_run.context.variables,
                task_output,
                node_id=task.node_id,
                task_id=task.id,
            )
            # SA-846 (TASK path): write the just-produced output into the
            # persisted aggregate so a downstream CONDITION reads it
            # read-consistently. The projector view is the authoritative read
            # path but folds ASYNCHRONOUSLY on the broker — merely popping
            # (refill-from-view) raced that fold in production, so a loop
            # revisit read the PREVIOUS iteration's value (off-by-one). The
            # aggregate is persisted here, synchronously, before the
            # ContinueRunAction that triggers the CONDITION — so this value is
            # already in place when the CONDITION resolves it.
            fresh_run.context.variables[node.id] = task_output
            if node.name and node.name != node.id:
                fresh_run.context.variables[node.name] = task_output
        await save_run_with_cas_retry(self.run_repo, run.id, _apply_artifacts)
        phases.mark("cas_save")

        # The AIW_RUN_NODE_OUTPUT mirror used to run here. It now lives in
        # ``OrchestrationEngine.apply_state_updates_as_files`` (called above at
        # the ``apply_state`` phase) with ``task=task``, so the row is written
        # from the ONE place every node output passes through instead of only
        # the worker-completion path. Same node, same ``task_output``, same
        # (generation, iteration, attempt) key — this is a move, not a change.
        phases.mark("node_output_row")

        # Enqueue orchestration for async processing
        await self._queue.enqueue(ContinueRunAction(
            run_id=req.run_id,
            task_id=req.task_id,
            test_mode=getattr(run, "test_mode", None),
        ))
        phases.mark("enqueue")

        return CompleteTaskResult(completed=True, status="COMPLETED", warnings=warnings)
