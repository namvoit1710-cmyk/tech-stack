from worker_sdk.layer2_application.features.get_worker_info.use_cases.get_worker_info_usecase import (
    GetWorkerInfoUseCase,
    GetWorkerInfoCommand,
    GetWorkerInfoResult,
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
    uc = GetWorkerInfoUseCase(logger=logger)
    result = uc.execute(GetWorkerInfoCommand())
    assert isinstance(result, GetWorkerInfoResult)
    assert result.worker_type is not None
    assert result.version is not None
    assert result.sdk_version is not None


def test_get_worker_info_logs_event():
    logger = DummyLogger()
    uc = GetWorkerInfoUseCase(logger=logger)
    uc.execute(GetWorkerInfoCommand())
    assert any(msg == "Returning worker info" for _, msg, _ in logger.events)


def test_get_worker_info_accepts_extra_kwargs():
    logger = DummyLogger()
    uc = GetWorkerInfoUseCase(logger=logger, unknown="ignored")
    result = uc.execute(GetWorkerInfoCommand())
    assert result.worker_type is not None


def test_get_worker_info_output_defaults():
    out = GetWorkerInfoResult(worker_type="x", version="1")
    assert out.sdk_version == ""
    assert out.metadata == {}
    assert out.input_schema is None
    assert out.output_schema is None


def test_get_worker_info_input_is_empty_dataclass():
    inp = GetWorkerInfoCommand()
    assert inp is not None


def test_get_worker_info_with_schemas():
    input_schema = {"fields": [{"key": "name", "outputType": "string"}]}
    output_schema = {"fields": [{"key": "result", "outputType": "string"}]}
    logger = DummyLogger()
    uc = GetWorkerInfoUseCase(
        logger=logger,
        input_schema=input_schema,
        output_schema=output_schema,
    )
    result = uc.execute(GetWorkerInfoCommand())
    assert result.input_schema == input_schema
    assert result.output_schema == output_schema


def test_get_worker_info_without_schemas():
    logger = DummyLogger()
    uc = GetWorkerInfoUseCase(logger=logger)
    result = uc.execute(GetWorkerInfoCommand())
    assert result.input_schema is None
    assert result.output_schema is None
