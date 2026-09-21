from __future__ import annotations

from dataclasses import dataclass, field

from app.layer1_domain.entities.failure_classifier import FailureClassifier, RepairClass
from app.layer1_domain.entities.repair_log import RepairAttempt, RepairLog
from app.layer1_domain.entities.repair_policy import RepairPolicy
from app.layer1_domain.entities.workflow_definition import (
    WorkflowDefinition,
    WorkflowValidationError,
)
from app.layer1_domain.entities.workflow_design_result import WorkflowDesignResult
from app.layer1_domain.entities.workflow_diff import merge_definitions
from app.layer1_domain.interfaces.workflow_control_plane_interface import (
    IWorkflowControlPlane,
    WorkflowControlPlaneError,
)
from app.layer2_application.interfaces.progress_sink_interface import IProgressSink
from app.layer2_application.interfaces.workflow_repairer_interface import (
    IWorkflowRepairer,
    WorkflowRepairError,
)


@dataclass
class _Failure:
    """A single validate/persist/dry-run/test-run failure, ready to classify."""

    error_code: str = ""
    error_message: str = ""
    error_details: dict = field(default_factory=dict)
    is_workflow_validation_error: bool = False


class ValidateRepairGate:
    """Deterministic outer loop: upsert -> dry-run -> classify -> repair -> final
    test-run -> finalize. Owns the ENTIRE post-generation gate the designer used to skip:
    a generated workflow is only surfaced as "created" after structural + semantic (via
    create/update) + dry-run all pass AND the single final test/full (skip_validation:
    False) succeeds. Class A failures are auto-repaired (bounded by ``RepairPolicy``);
    Class B failures stop with one focused clarifying question. Every iteration is logged
    to a ``RepairLog`` (AC3/AC4 evidence) and emitted as a ``repair_iteration`` progress
    event.
    """

    def __init__(
        self,
        control_plane: IWorkflowControlPlane,
        repairer: IWorkflowRepairer,
        policy: RepairPolicy | None = None,
    ) -> None:
        self._control_plane = control_plane
        self._repairer = repairer
        self._policy = policy or RepairPolicy()

    async def finalize(
        self,
        definition: dict,
        workflow_id: str = "",
        progress: IProgressSink | None = None,
        remove_node_ids: list | None = None,
        source_workflow_id: str = "",
    ) -> WorkflowDesignResult:
        log = RepairLog()
        current_id: str = workflow_id or ""
        # Three modes, all sharing the load+merge machinery:
        #  - CLONE (SA-2131): ``source_workflow_id`` set — load the SOURCE as a template, merge the
        #    change onto it, then persist a NEW workflow via create() (``current_id`` stays empty).
        #    The source is only read (get), never written, so it is left unchanged.
        #  - UPDATE (SA-2157): ``workflow_id`` set — load the current graph, merge the change onto
        #    it so unrelated nodes/edges survive, then persist via PATCH (id stable, no version fork).
        #  - CREATE: neither — a brand-new workflow, created and (if it needs repair) re-persisted
        #    with update() — the pre-SA-2158 flow, unchanged.
        is_update = bool(current_id)
        if source_workflow_id:
            base = await self._control_plane.get(source_workflow_id)
            current_def = merge_definitions(base, definition, remove_node_ids)
        elif is_update:
            base = await self._control_plane.get(current_id)
            current_def = merge_definitions(base, definition, remove_node_ids)
        else:
            current_def = dict(definition)
        attempts_used: int = 0

        while True:
            current_id, failure = await self._validate_persist_dry_run(
                current_def, current_id, use_patch=is_update
            )
            if failure is None:
                run_outcome = await self._control_plane.test_run(current_id, {}, skip_validation=False)
                if not run_outcome.failed():
                    return self._success_result(
                        current_def, current_id, run_outcome, attempts_used, log, is_update
                    )
                failure = _Failure(
                    error_code=run_outcome.error_code,
                    error_message=run_outcome.error,
                    error_details=run_outcome.details,
                )

            step = await self._repair_or_stop(failure, current_def, current_id, attempts_used, log, progress)
            if isinstance(step, WorkflowDesignResult):
                return step
            current_def, attempts_used = step

    # --- one validate/persist/dry-run cycle ---

    async def _validate_persist_dry_run(
        self, definition: dict, workflow_id: str, use_patch: bool = False
    ) -> tuple[str, _Failure | None]:
        try:
            WorkflowDefinition.from_raw(
                definition.get("name"), definition.get("description", ""),
                definition.get("nodes") or [], definition.get("edges") or [],
            )
        except WorkflowValidationError as exc:
            return workflow_id, _Failure(error_message=str(exc), is_workflow_validation_error=True)

        try:
            if workflow_id and use_patch:
                ref = await self._control_plane.patch(workflow_id, definition)
            elif workflow_id:
                ref = await self._control_plane.update(workflow_id, definition)
            else:
                ref = await self._control_plane.create(definition)
        except WorkflowControlPlaneError as exc:
            return workflow_id, _Failure(
                error_code=exc.code, error_message=exc.message, error_details=exc.details,
            )
        workflow_id = ref.workflow_id

        dry = await self._control_plane.dry_run(workflow_id)
        if not dry.ok:
            return workflow_id, _Failure(
                error_code=dry.error_code, error_message=dry.error_message,
                error_details=dry.error_details,
            )
        return workflow_id, None

    # --- classify -> repair (or stop) ---

    async def _repair_or_stop(
        self,
        failure: _Failure,
        current_def: dict,
        current_id: str,
        attempts_used: int,
        log: RepairLog,
        progress: IProgressSink | None,
    ) -> WorkflowDesignResult | tuple[dict, int]:
        repair_class = FailureClassifier.classify(
            error_code=failure.error_code,
            message=failure.error_message,
            error_details=failure.error_details,
            is_workflow_validation_error=failure.is_workflow_validation_error,
        )

        if repair_class is RepairClass.B:
            log.record(RepairAttempt(
                attempt=attempts_used + 1, error_code=failure.error_code,
                error_message=failure.error_message, repair_class=repair_class.value, fixed=False,
            ))
            await self._emit_repair_iteration(progress, attempts_used + 1, failure, blocked=True)
            return WorkflowDesignResult(
                status=WorkflowDesignResult.NEEDS_CLARIFICATION,
                content=self._clarify_question(failure),
                workflow=current_def, workflow_id=current_id,
                raw={"repair_log": log.to_document()},
            )

        decision = self._policy.evaluate(attempts_used)
        if not decision.should_repair:
            return self._exhausted_result(current_def, current_id, failure, log)

        try:
            corrected = await self._repairer.repair(
                current_def, failure.error_message, failure.error_details,
            )
        except WorkflowRepairError as exc:
            log.record(RepairAttempt(
                attempt=decision.next_attempt, error_code=failure.error_code,
                error_message=f"repair failed: {exc}", repair_class=repair_class.value, fixed=False,
            ))
            return self._exhausted_result(current_def, current_id, failure, log)

        log.record(RepairAttempt(
            attempt=decision.next_attempt, error_code=failure.error_code,
            error_message=failure.error_message, repair_class=repair_class.value, fixed=True,
        ))
        await self._emit_repair_iteration(progress, decision.next_attempt, failure, blocked=False)
        return corrected, decision.next_attempt

    # --- terminal results ---

    def _success_result(
        self, definition, workflow_id, run_outcome, attempts_used, log, is_update=False
    ) -> WorkflowDesignResult:
        verb = "updated" if is_update else "created"
        content = (
            f"Repaired and {verb} your workflow." if attempts_used else f"{verb.capitalize()} your workflow."
        )
        return WorkflowDesignResult(
            status=WorkflowDesignResult.SUCCESS,
            content=content,
            workflow=definition, workflow_id=workflow_id,
            run_id=run_outcome.run_id, link=run_outcome.replay_link,
            raw={"repair_log": log.to_document()},
        )

    def _exhausted_result(
        self, definition: dict, workflow_id: str, failure: _Failure, log: RepairLog
    ) -> WorkflowDesignResult:
        return WorkflowDesignResult(
            status=WorkflowDesignResult.ERROR,
            content=(
                f"Could not auto-repair the workflow after {log.attempt_count} attempt(s): "
                f"{failure.error_message}"
            ),
            workflow=definition, workflow_id=workflow_id,
            raw={"repair_log": log.to_document(), "last_error": failure.error_message},
        )

    @staticmethod
    def _clarify_question(failure: _Failure) -> str:
        # SA-2132: a nested-workflow failure (cycle / too-deep / no target) gets a specific,
        # actionable message that names the cause instead of the generic "provide the value".
        reason = FailureClassifier.nested_workflow_reason(
            failure.error_code, failure.error_message, failure.error_details,
        )
        if reason:
            return reason
        return (
            f"I couldn't finish setting up the workflow: {failure.error_message}. "
            "Could you provide the correct value?"
        )

    @staticmethod
    async def _emit_repair_iteration(
        progress: IProgressSink | None, attempt: int, failure: _Failure, blocked: bool,
    ) -> None:
        if progress is None:
            return
        await progress.emit("repair_iteration", {
            "attempt": attempt,
            "error_code": failure.error_code,
            "error": failure.error_message,
            "blocked": blocked,
        })
