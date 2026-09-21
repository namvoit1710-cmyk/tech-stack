"""End-to-end (self-contained): drive the full COLLECT -> DIAGNOSE -> REPORT vertical with a
stubbed Workflow Control Plane serving a failed run's execution log, and assert the final
DiagnosticReport. Deterministic, offline, no live credentials — this is the /loop target."""
import asyncio

from app.layer1_domain.value_objects.failure_category import FailureCategory
from app.layer2_application.features.troubleshoot.use_cases.collect_usecase import (
    CollectInputDto,
    CollectUseCase,
)
from app.layer2_application.features.troubleshoot.use_cases.diagnose_usecase import (
    DiagnoseInputDto,
    DiagnoseUseCase,
)
from app.layer2_application.features.troubleshoot.use_cases.report_usecase import (
    ReportInputDto,
    ReportUseCase,
)

# Shape verified against the control-plane e2e (test_failure_surfaces_e2e.py):
# a worker HTTP 500 → an agent/service communication failure.
FAILED_RUN_LOG = {
    "summary": {"node_counts": {"failed": 1, "succeeded": 0}},
    "events": [
        {"event_type": "RunStatusChanged",
         "payload": {"run_id": "c5700061", "status": "started"}},
        {"event_type": "NodeFailed",
         "payload": {"node_id": "task-1", "task_id": "t-1",
                     "error_code": "HTTP_500", "error_message": "HTTP 500"}},
        {"event_type": "NodeOutputProduced",
         "payload": {"state_updates": {"task-1": {"status_code": 500}}}},
    ],
}


class _StubWorkflowClient:
    def __init__(self, log):
        self._log = log

    async def get_execution_log(self, run_id, offset=0, include_children=True):
        return {**self._log, "run_id": run_id}


class _Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def debug(self, *a, **k): pass


def test_troubleshoot_e2e_diagnoses_comms_failure():
    logger = _Log()
    client = _StubWorkflowClient(FAILED_RUN_LOG)

    async def run():
        collected = await CollectUseCase(logger, client).execute(
            CollectInputDto(run_id="c5700061")
        )
        diagnosed = await DiagnoseUseCase(logger).execute(
            DiagnoseInputDto(run_id=collected.run_id, execution_log=collected.execution_log)
        )
        return await ReportUseCase(logger).execute(ReportInputDto(report=diagnosed.report))

    out = asyncio.run(run())

    assert out.report["category"] == FailureCategory.AGENT_SERVICE_COMMUNICATION.value
    assert "HTTP 500" in out.report["root_cause"]
    assert out.report["recommended_fix"]
    assert out.report["failed_node_ids"] == ["task-1"]
    assert out.report["severity"] == "high"
    # No LLM here → the user-facing message is the deterministic plain-language baseline
    # (never the raw category token or a stack of HTTP codes).
    assert out.report["explanation"]
    assert out.summary == out.report["explanation"]
    assert "HTTP" not in out.summary and "agent_service_communication" not in out.summary


def test_troubleshoot_e2e_healthy_run_reports_no_failure():
    logger = _Log()
    healthy = {"summary": {"node_counts": {"failed": 0}},
               "events": [{"event_type": "RunStatusChanged", "payload": {"status": "started"}}]}
    client = _StubWorkflowClient(healthy)

    async def run():
        collected = await CollectUseCase(logger, client).execute(CollectInputDto(run_id="ok-1"))
        diagnosed = await DiagnoseUseCase(logger).execute(
            DiagnoseInputDto(run_id=collected.run_id, execution_log=collected.execution_log)
        )
        return await ReportUseCase(logger).execute(ReportInputDto(report=diagnosed.report))

    out = asyncio.run(run())
    assert out.report["category"] == FailureCategory.NO_FAILURE.value
