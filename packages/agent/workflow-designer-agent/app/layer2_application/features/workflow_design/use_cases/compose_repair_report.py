from __future__ import annotations

from app.layer1_domain.entities.diagnosis_verdict import DiagnosisVerdict
from app.layer1_domain.entities.workflow_design_result import WorkflowDesignResult


def build_repair_report(verdict: DiagnosisVerdict, result: WorkflowDesignResult) -> str:
    """Part D: the user-facing message after a successful auto-repair — root cause, the
    solution applied, and the fixed points (which nodes changed)."""
    fixed = ", ".join(verdict.failed_node_ids) or "the affected node(s)"
    repair_log = (result.raw or {}).get("repair_log") or []  # RepairLog.to_document() -> list
    attempts = len(repair_log) if isinstance(repair_log, list) else 0
    verified = f" Re-ran the workflow to verify (run {result.run_id})." if result.run_id else ""
    repaired = f" (auto-repaired in {attempts} attempt(s))" if attempts else ""
    return (
        f"Root cause: {verdict.root_cause or verdict.category}. "
        f"Solution: {verdict.recommended_fix or 'applied the fix and re-validated the workflow'}. "
        f"Fixed point(s): updated node(s) {fixed}{repaired}.{verified}"
    )
