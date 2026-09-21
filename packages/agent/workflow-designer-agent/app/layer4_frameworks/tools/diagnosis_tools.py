from __future__ import annotations

from typing import Any

from agent_sdk import tool

from app.layer2_application.interfaces.workflow_diagnoser_interface import (
    IWorkflowDiagnoser,
)
from app.layer2_application.interfaces.workflow_run_reader_interface import (
    IWorkflowRunReader,
)


def build_diagnosis_tools(
    reader: IWorkflowRunReader, diagnoser: IWorkflowDiagnoser
) -> list[Any]:
    """The diagnose half of self-heal, wrapped as a tool the design loop can call.

    ``reader`` resolves the failed run + its execution log from the Workflow Control Plane;
    ``diagnoser`` (the troubleshooting-agent, over HTTP) classifies WHY it failed. The tool
    only READS + explains — it never edits a workflow. The model repairs afterwards through the
    normal get_workflow -> update_workflow -> gate path, so the network diagnosis stays behind a
    single swappable seam (this tool + the diagnoser adapter)."""

    @tool("diagnose_failure", internal=True)
    async def diagnose_failure(
        run_id: str = "", workflow_name: str = "", workflow_id: str = ""
    ) -> dict:
        """Diagnose WHY a workflow run failed, so you can then repair it.

        Call this when the user reports a broken/failed workflow (e.g. "a node failed, please
        fix it"). You need a run reference: a ``run_id`` OR a ``workflow_name`` (or the real
        ``workflow_id``). If the user gave none, ask for one with ask_for_clarify FIRST — do not
        call this with all-empty arguments. The failed run is resolved and its execution log is
        read from the control plane, then the troubleshooting service classifies the failure.

        This is READ-ONLY — it explains the failure but does not change anything. After you get
        the verdict, repair the workflow yourself: get_workflow(workflow_id) to load the current
        graph, then update_workflow(...) touching the reported ``failed_node_ids`` per
        ``recommended_fix``; the gate persists, dry-runs, auto-repairs and test-runs it.

        Args:
            run_id: The failed run's id, if the user gave it.
            workflow_name: The workflow's name, to resolve its latest failed run (if no run_id).
            workflow_id: The workflow's real id, if known (also used to resolve the run).
        Returns:
            {"status": "diagnosed"|"no_failure"|"not_found"|"needs_reference",
             "run_id", "workflow_id", "category", "severity", "owner", "root_cause",
             "recommended_fix", "explanation", "failed_node_ids", "evidence", "confidence",
             "message"}
        """
        rid = (run_id or "").strip()
        wid = (workflow_id or "").strip()
        wname = (workflow_name or "").strip()
        if not (rid or wid or wname):
            return {
                "status": "needs_reference",
                "message": ("I need a run_id or the workflow's name to diagnose the failure. "
                            "Ask the user for one before calling diagnose_failure."),
            }

        if not rid:
            rid = await reader.resolve_run_id(workflow_id=wid, workflow_name=wname)
        if not rid:
            return {
                "status": "not_found",
                "workflow_id": wid,
                "message": ("I couldn't find a run to diagnose for that workflow. Ask the user "
                            "for a specific run_id."),
            }

        execution_log = await reader.get_execution_log(rid)
        resolved_wid = wid or str((execution_log or {}).get("workflow_id") or "")
        verdict = await diagnoser.diagnose(execution_log, run_id=rid)

        if not verdict.has_failure():
            return {
                "status": "no_failure",
                "run_id": rid,
                "workflow_id": resolved_wid,
                "message": "That run did not fail — there's nothing to repair.",
            }

        return {
            "status": "diagnosed",
            "run_id": verdict.run_id or rid,
            "workflow_id": resolved_wid,
            "category": verdict.category,
            "severity": verdict.severity,
            "owner": verdict.owner,
            "root_cause": verdict.root_cause,
            "recommended_fix": verdict.recommended_fix,
            "explanation": verdict.explanation,
            "failed_node_ids": list(verdict.failed_node_ids),
            "evidence": list(verdict.evidence),
            "confidence": verdict.confidence,
        }

    return [diagnose_failure]
