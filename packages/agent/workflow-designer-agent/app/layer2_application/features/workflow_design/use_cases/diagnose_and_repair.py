from __future__ import annotations

from app.layer1_domain.entities.workflow_design_result import WorkflowDesignResult
from app.layer1_domain.interfaces.workflow_control_plane_interface import (
    IWorkflowControlPlane,
)
from app.layer2_application.features.workflow_design.use_cases.build_repair_seed import (
    build_repair_seed,
)
from app.layer2_application.features.workflow_design.use_cases.compose_repair_report import (
    build_repair_report,
)
from app.layer2_application.features.workflow_design.use_cases.resolve_failed_run import (
    ResolveFailedRun,
    run_reference_clarification,
)
from app.layer2_application.features.workflow_design.use_cases.validate_repair_gate import (
    ValidateRepairGate,
)
from app.layer2_application.interfaces.progress_sink_interface import IProgressSink
from app.layer2_application.interfaces.workflow_diagnoser_interface import IWorkflowDiagnoser
from app.layer2_application.interfaces.workflow_run_reader_interface import (
    IWorkflowRunReader,
)


class DiagnoseAndRepair:
    """Part A-D bridge: a user reports a broken workflow -> resolve its failed run -> diagnose
    it (troubleshooting-agent contract) -> seed the ValidateRepairGate in UPDATE mode to
    auto-repair -> report root cause, solution and fixed points.

    Every mutation goes through the existing gate (persist -> dry-run -> Class-A auto-repair ->
    test-run); Class-B failures surface the gate's clarifying question unchanged.
    """

    def __init__(
        self,
        reader: IWorkflowRunReader,
        diagnoser: IWorkflowDiagnoser,
        control_plane: IWorkflowControlPlane,
        gate: ValidateRepairGate,
    ) -> None:
        self._reader = reader
        self._diagnoser = diagnoser
        self._control_plane = control_plane
        self._gate = gate

    async def run(
        self,
        workflow_id: str = "",
        run_id: str = "",
        workflow_name: str = "",
        progress: IProgressSink | None = None,
    ) -> WorkflowDesignResult:
        clarify = run_reference_clarification(
            run_id=run_id, workflow_id=workflow_id, workflow_name=workflow_name
        )
        if clarify:
            return WorkflowDesignResult(
                status=WorkflowDesignResult.NEEDS_CLARIFICATION, content=clarify
            )

        resolved = await ResolveFailedRun(self._reader).resolve(
            run_id=run_id, workflow_id=workflow_id, workflow_name=workflow_name
        )
        if not resolved.found():
            return WorkflowDesignResult(
                status=WorkflowDesignResult.ERROR,
                content="I couldn't find a run to diagnose for that workflow.",
            )

        verdict = await self._diagnoser.diagnose(resolved.execution_log, run_id=resolved.run_id)
        if not verdict.has_failure():
            return WorkflowDesignResult(
                status=WorkflowDesignResult.SUCCESS,
                content="I checked the latest run and found no failure to repair.",
                run_id=resolved.run_id,
            )

        # Need the workflow's id to update it; if the user gave only a run_id, take it from the run.
        wid = workflow_id or str(resolved.execution_log.get("workflow_id") or "")
        if not wid:
            return WorkflowDesignResult(
                status=WorkflowDesignResult.NEEDS_CLARIFICATION,
                content=("I diagnosed the run but need to know which workflow to repair — "
                         "tell me its name or id."),
            )

        current = await self._control_plane.get(wid)
        seed = build_repair_seed(verdict, current, wid)
        result = await self._gate.finalize(
            {
                "name": seed["name"], "description": seed["description"],
                "nodes": seed["nodes"], "edges": seed["edges"],
            },
            workflow_id=wid,
            progress=progress,
            remove_node_ids=seed["remove_node_ids"],
        )

        if result.status == WorkflowDesignResult.SUCCESS:
            result.content = build_repair_report(verdict, result)
        return result
