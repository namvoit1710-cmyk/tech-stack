from worker_sdk.layer2_application.features.get_worker_info.use_cases.get_worker_info_usecase import (
    GetWorkerInfoUseCase as UseCase,
    GetWorkerInfoCommand,
)


class DummyLogger:
    def __init__(self):
        self.events = []

    def info(self, message: str, **kwargs):
        self.events.append(("info", message, kwargs))

    def error(self, message: str, **kwargs):
        self.events.append(("error", message, kwargs))

    def warning(self, message: str, **kwargs):
        self.events.append(("warning", message, kwargs))

    def debug(self, message: str, **kwargs):
        self.events.append(("debug", message, kwargs))


def test_get_worker_info_returns_output():
    logger = DummyLogger()
    use_case = UseCase(logger=logger)
    result = use_case.execute(GetWorkerInfoCommand())
    assert result.worker_type is not None
    assert result.version is not None
    assert result.sdk_version is not None


def test_get_worker_info_logs_event():
    logger = DummyLogger()
    use_case = UseCase(logger=logger)
    use_case.execute(GetWorkerInfoCommand())
    assert any(msg == "Returning worker info" for _, msg, _ in logger.events)
