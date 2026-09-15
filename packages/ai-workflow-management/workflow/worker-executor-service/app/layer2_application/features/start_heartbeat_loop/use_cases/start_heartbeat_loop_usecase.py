"""Start heartbeat loop use case: start heartbeat loop for a worker."""

from dataclasses import dataclass

from app.layer2_application.interfaces.logger_interface import ILogger
from typing import Any


@dataclass
class StartHeartbeatLoopInput:
    worker_id: str = ""


@dataclass
class StartHeartbeatLoopOutput:
    started: bool = False
    worker_id: str = ""


class StartHeartbeatLoopUseCase:
    def __init__(self, logger: ILogger, **kwargs: Any) -> None:
        self.logger = logger

    def execute(self, input: StartHeartbeatLoopInput) -> dict:
        self.logger.info("Starting heartbeat loop", worker_id=input.worker_id)
        return StartHeartbeatLoopOutput(
            started=True,
            worker_id=input.worker_id,
        ).__dict__
