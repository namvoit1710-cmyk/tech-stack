import asyncio

from app.layer1_domain.value_objects.failure_category import FailureCategory
from app.layer2_application.features.troubleshoot.use_cases.diagnose_usecase import (
    DiagnoseInputDto,
    DiagnoseUseCase,
)


class _Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def debug(self, *a, **k): pass


def test_diagnose_use_case_classifies_comms_failure():
    uc = DiagnoseUseCase(_Log())
    log = {
        "summary": {"node_counts": {"failed": 1}},
        "events": [{"event_type": "NodeFailed",
                    "payload": {"node_id": "n1", "error_code": "HTTP_503",
                                "error_message": "Service unavailable"}}],
    }
    out = asyncio.run(uc.execute(DiagnoseInputDto(run_id="r1", execution_log=log)))
    assert out.report.category is FailureCategory.AGENT_SERVICE_COMMUNICATION
    assert out.report.run_id == "r1"
