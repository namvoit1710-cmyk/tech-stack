import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.layer2_application.features.start_heartbeat_loop.use_cases.start_heartbeat_loop_usecase import (
    StartHeartbeatLoopUseCase,
    StartHeartbeatLoopInput,
)


class DummyLogger:
    def info(self, message: str, *args, **kwargs): pass
    def error(self, message: str, *args, **kwargs): pass
    def warning(self, message: str, *args, **kwargs): pass
    def debug(self, message: str, *args, **kwargs): pass


def test_start_heartbeat_loop():
    logger = DummyLogger()
    uc = StartHeartbeatLoopUseCase(logger=logger)
    result = uc.execute(StartHeartbeatLoopInput(worker_id="w2"))
    assert result["started"] is True
    assert result["worker_id"] == "w2"
