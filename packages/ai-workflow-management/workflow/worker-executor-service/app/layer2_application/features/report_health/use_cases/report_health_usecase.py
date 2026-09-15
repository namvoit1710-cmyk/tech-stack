"""Report health use case: record worker health report."""

from dataclasses import dataclass

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer1_domain.entities.heartbeat_record import HeartbeatRecord
from typing import Any


@dataclass
class ReportHealthInput:
    worker_id: str = ""
    healthy: bool = True


@dataclass
class ReportHealthOutput:
    worker_id: str = ""
    healthy: bool = True
    timestamp: str = ""


class ReportHealthUseCase:
    def __init__(self, logger: ILogger, **kwargs: Any) -> None:
        self.logger = logger
        self._records: list[HeartbeatRecord] = []

    def execute(self, input: ReportHealthInput) -> dict:
        self.logger.info("Health report", worker_id=input.worker_id)
        record = HeartbeatRecord(
            worker_id=input.worker_id,
            healthy=input.healthy,
        )
        self._records.append(record)
        return ReportHealthOutput(
            worker_id=record.worker_id,
            healthy=record.healthy,
            timestamp=record.timestamp,
        ).__dict__
