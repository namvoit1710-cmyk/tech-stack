import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.layer2_application.features.report_health.use_cases.report_health_usecase import ReportHealthUseCase, ReportHealthInput


class DummyLogger:
    def info(self, message: str, *args, **kwargs): pass
    def error(self, message: str, *args, **kwargs): pass
    def warning(self, message: str, *args, **kwargs): pass
    def debug(self, message: str, *args, **kwargs): pass


def test_report_health():
    logger = DummyLogger()
    uc = ReportHealthUseCase(logger=logger)
    result = uc.execute(ReportHealthInput(worker_id="w1", healthy=True))
    assert result["worker_id"] == "w1"
    assert result["healthy"] is True
    assert "timestamp" in result
