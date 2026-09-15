import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.layer2_application.features.report_task_result.use_cases.report_task_result_usecase import (
    ReportTaskResultUseCase,
    ReportTaskResultInput,
)


class DummyLogger:
    def info(self, message: str, *args, **kwargs): pass
    def error(self, message: str, *args, **kwargs): pass
    def warning(self, message: str, *args, **kwargs): pass
    def debug(self, message: str, *args, **kwargs): pass


class DummyResultPublisher:
    def __init__(self):
        self.published = []

    def publish_result(self, result):
        self.published.append(result)


def test_report_result():
    logger = DummyLogger()
    publisher = DummyResultPublisher()
    uc = ReportTaskResultUseCase(logger=logger, result_publisher=publisher)
    result = uc.execute(ReportTaskResultInput(data={"task_id": "t3", "status": "COMPLETED"}))
    assert result["reported"] is True
    assert len(publisher.published) == 1
