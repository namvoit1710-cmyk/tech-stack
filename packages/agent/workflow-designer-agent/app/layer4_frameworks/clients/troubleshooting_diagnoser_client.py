from __future__ import annotations

import logging
from typing import Any

import httpx

from app.layer1_domain.entities.diagnosis_verdict import DiagnosisVerdict
from app.layer2_application.interfaces.workflow_diagnoser_interface import (
    IWorkflowDiagnoser,
)

logger = logging.getLogger(__name__)


class RemoteWorkflowDiagnoser(IWorkflowDiagnoser):
    """Diagnose a failed run by calling the troubleshooting-agent (SA-1438) over HTTP.

    This is the concrete adapter behind ``IWorkflowDiagnoser`` (Solution A: call the service
    across the network). It POSTs to the SDK's ``/api/v1/execute`` endpoint, which drives the
    troubleshooting-agent's deterministic collect->diagnose->report graph; the graph's
    ``DiagnosticReport`` comes back in the response ``agent_data`` and maps 1:1 onto
    ``DiagnosisVerdict`` (both mirror ``DiagnosticReport.to_dict()``).

    The transport is deliberately isolated here so the seam is easy to refactor: swapping to a
    different endpoint, or to an in-process classifier, only touches this class. Read-only and
    fail-soft — any error (no URL, network, bad shape) degrades to an empty verdict
    (``has_failure() is False``) so it never raises into the design flow.
    """

    #: The troubleshooting-agent collects + diagnoses from a run reference in ``parameters``;
    #: the message is only narrative context for the graph's report step.
    _MESSAGE = "Diagnose why this workflow run failed."

    def __init__(
        self,
        base_url: str = "",
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._base = (base_url or "").rstrip("/")
        self._client = client
        self._timeout = timeout

    def _acquire(self) -> tuple[Any, bool]:
        if self._client is not None:
            return self._client, False
        return httpx.AsyncClient(timeout=self._timeout), True

    def _parameters(self, execution_log: dict[str, Any], run_id: str) -> dict[str, Any]:
        """The troubleshooting-agent's collect step reads run_id / workflow_id / workflow_name
        from ``parameters`` and re-fetches the log itself, so pass whatever reference we have."""
        log = execution_log or {}
        params: dict[str, Any] = {}
        rid = run_id or str(log.get("run_id") or "")
        if rid:
            params["run_id"] = rid
        wid = str(log.get("workflow_id") or "")
        if wid:
            params["workflow_id"] = wid
        wname = str(log.get("workflow_name") or "")
        if wname:
            params["workflow_name"] = wname
        return params

    async def diagnose(
        self, execution_log: dict[str, Any], run_id: str = ""
    ) -> DiagnosisVerdict:
        params = self._parameters(execution_log, run_id)
        if not self._base or not params:
            return DiagnosisVerdict(run_id=run_id)

        client, owns = self._acquire()
        try:
            resp = await client.post(
                f"{self._base}/api/v1/execute",
                json={"message": self._MESSAGE, "parameters": params},
            )
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:  # fail-soft: never raise into the design flow
            logger.warning("troubleshooting diagnose failed: %s", exc)
            return DiagnosisVerdict(run_id=run_id)
        finally:
            if owns:
                await client.aclose()

        if not isinstance(body, dict):
            return DiagnosisVerdict(run_id=run_id)
        # report_node routes the DiagnosticReport into formatted_response.agent_data, which the
        # SDK's normalize_final_state surfaces as the response's "agent_data".
        report = body.get("agent_data")
        if not isinstance(report, dict) or not report:
            logger.warning(
                "troubleshooting diagnose returned no agent_data (status=%r error=%r)",
                body.get("status"), body.get("error"),
            )
            return DiagnosisVerdict(run_id=run_id)
        verdict = DiagnosisVerdict.from_dict(report)
        if not verdict.run_id:
            verdict.run_id = run_id or str((execution_log or {}).get("run_id") or "")
        return verdict
