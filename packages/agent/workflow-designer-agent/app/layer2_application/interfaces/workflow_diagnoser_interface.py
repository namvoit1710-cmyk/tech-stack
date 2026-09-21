from __future__ import annotations

from typing import Any, Protocol

from app.layer1_domain.entities.diagnosis_verdict import DiagnosisVerdict


class IWorkflowDiagnoser(Protocol):
    """Port for diagnosing WHY a workflow run failed, from its execution log.

    The concrete adapter reuses the troubleshooting-agent's diagnosis (same deterministic
    classifier / service). Read-only: it explains the failure, it never edits a workflow.
    """

    async def diagnose(
        self, execution_log: dict[str, Any], run_id: str = ""
    ) -> DiagnosisVerdict:
        ...
