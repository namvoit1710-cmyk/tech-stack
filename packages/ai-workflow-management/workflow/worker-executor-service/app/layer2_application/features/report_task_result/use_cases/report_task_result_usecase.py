"""Report task result use case: publish result to message broker."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.result_publisher_interface import IResultPublisher


@dataclass
class ReportTaskResultInput:
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReportTaskResultOutput:
    reported: bool = False


class ReportTaskResultUseCase:
    def __init__(
        self,
        logger: ILogger,
        result_publisher: IResultPublisher,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.result_publisher = result_publisher

    def execute(self, input: ReportTaskResultInput) -> dict:
        self.logger.info("Reporting result", task_id=input.data.get("task_id", ""))
        if self.result_publisher:
            self.result_publisher.publish_result(input.data)
        return ReportTaskResultOutput(reported=True).__dict__
