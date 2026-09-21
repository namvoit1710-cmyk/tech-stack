import importlib
from types import SimpleNamespace

import pytest


class RecordingLogger:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def info(self, message: str, **kwargs) -> None:
        self.infos.append(message)

    def warning(self, message: str, **kwargs) -> None:
        self.warnings.append(message)

    def error(self, message: str, **kwargs) -> None:
        self.errors.append(message)

    def debug(self, message: str, **kwargs) -> None:
        pass


@pytest.fixture
def use_cases_module_fixture():
    module = importlib.reload(
        importlib.import_module("app.layer2_application.features.main_agent.use_cases")
    )
    module.FEATURE_REGISTRY.clear()
    module.USECASE_CONTAINER.clear()
    yield module
    module.FEATURE_REGISTRY.clear()
    module.USECASE_CONTAINER.clear()


@pytest.mark.parametrize(
    ("snake", "expected"),
    [
        ("create_plan", "CreatePlan"),
        ("review_plan", "ReviewPlan"),
        ("x", "X"),
        ("multi_word_name", "MultiWordName"),
    ],
)
def test_to_pascal_case(use_cases_module_fixture, snake, expected):
    assert use_cases_module_fixture._to_pascal_case(snake) == expected


def test_load_features_registers_expected_named_usecase(
    use_cases_module_fixture, monkeypatch
):
    class CreatePlanUseCase:
        pass

    monkeypatch.setattr(
        use_cases_module_fixture.pkgutil,
        "iter_modules",
        lambda path: [(None, "create_plan_usecase", False)],
    )
    monkeypatch.setattr(
        use_cases_module_fixture.importlib,
        "import_module",
        lambda name: SimpleNamespace(CreatePlanUseCase=CreatePlanUseCase),
    )

    registry = use_cases_module_fixture.load_features()

    assert registry == {"create_plan": CreatePlanUseCase}


def test_load_features_registers_fallback_usecase_class(
    use_cases_module_fixture, monkeypatch
):
    class UseCase:
        pass

    monkeypatch.setattr(
        use_cases_module_fixture.pkgutil,
        "iter_modules",
        lambda path: [(None, "review_plan_usecase", False)],
    )
    monkeypatch.setattr(
        use_cases_module_fixture.importlib,
        "import_module",
        lambda name: SimpleNamespace(UseCase=UseCase),
    )

    registry = use_cases_module_fixture.load_features()

    assert registry == {"review_plan": UseCase}


def test_load_features_logs_warning_when_module_has_no_matching_class(
    use_cases_module_fixture, monkeypatch
):
    logger = RecordingLogger()
    monkeypatch.setattr(
        use_cases_module_fixture.pkgutil,
        "iter_modules",
        lambda path: [(None, "noop_usecase", False)],
    )
    monkeypatch.setattr(
        use_cases_module_fixture.importlib,
        "import_module",
        lambda name: SimpleNamespace(),
    )

    registry = use_cases_module_fixture.load_features(logger)

    assert registry == {}
    assert any(
        "No matching use case class found" in message for message in logger.warnings
    )


def test_load_features_logs_import_errors(use_cases_module_fixture, monkeypatch):
    logger = RecordingLogger()
    monkeypatch.setattr(
        use_cases_module_fixture.pkgutil,
        "iter_modules",
        lambda path: [(None, "broken_usecase", False)],
    )

    def _raise(_name):
        raise RuntimeError("boom")

    monkeypatch.setattr(use_cases_module_fixture.importlib, "import_module", _raise)

    registry = use_cases_module_fixture.load_features(logger)

    assert registry == {}
    assert any(
        "Failed to load use case module 'broken_usecase'" in message
        for message in logger.errors
    )


def test_load_all_usecases_injects_only_matching_dependencies(use_cases_module_fixture):
    class SampleUseCase:
        def __init__(self, logger, settings):
            self.logger = logger
            self.settings = settings

    use_cases_module_fixture.FEATURE_REGISTRY["sample"] = SampleUseCase
    logger = RecordingLogger()

    container = use_cases_module_fixture.load_all_usecases(
        {"logger": "logger", "settings": "settings", "ignored": "ignored"},
        logger,
    )

    assert container["sample"].logger == "logger"
    assert container["sample"].settings == "settings"
    assert any("Loaded use cases" in message for message in logger.infos)


def test_load_all_usecases_reuses_cached_instances(use_cases_module_fixture):
    class SampleUseCase:
        def __init__(self, logger):
            self.logger = logger

    use_cases_module_fixture.FEATURE_REGISTRY["sample"] = SampleUseCase

    first = use_cases_module_fixture.load_all_usecases({"logger": "first"})
    second = use_cases_module_fixture.load_all_usecases({"logger": "second"})

    assert first["sample"] is second["sample"]
    assert second["sample"].logger == "first"


def test_get_usecase_returns_registered_instance(use_cases_module_fixture):
    use_cases_module_fixture.USECASE_CONTAINER["sample"] = "instance"

    assert use_cases_module_fixture.get_usecase("sample") == "instance"


def test_get_usecase_raises_for_unknown_name(use_cases_module_fixture):
    with pytest.raises(KeyError, match="Use case 'missing' is not registered"):
        use_cases_module_fixture.get_usecase("missing")


@pytest.mark.parametrize("raw_payload", [{"x": 1}, "value"])
def test_wrap_inbound_payload_preserves_raw_event_only(bootstrap_module, raw_payload):
    assert bootstrap_module._wrap_inbound_payload(raw_payload) == {
        "parameters": {"event": raw_payload}
    }


def test_wrap_consumer_start_is_noop_for_none(bootstrap_module):
    assert bootstrap_module._wrap_consumer_start(None) is None


def test_wrap_consumer_start_is_noop_without_start_method(bootstrap_module):
    consumer = SimpleNamespace()

    assert bootstrap_module._wrap_consumer_start(consumer) is None


@pytest.mark.asyncio
async def test_wrap_consumer_start_wraps_delivery_payload(bootstrap_module):
    captured = {}

    class Delivery:
        def __init__(self):
            self.payload = {"message": "hello"}
            self.marker = "ok"

    class Consumer:
        async def start(self, handler):
            await handler(Delivery())
            return "done"

    consumer = Consumer()

    async def handler(delivery):
        captured["payload"] = delivery.payload
        captured["marker"] = delivery.marker

    bootstrap_module._wrap_consumer_start(consumer)
    result = await consumer.start(handler)

    assert result == "done"
    assert captured["payload"] == {"parameters": {"event": {"message": "hello"}}}
    assert captured["marker"] == "ok"


@pytest.mark.asyncio
async def test_wrap_consumer_start_passes_control_through_unwrapped(bootstrap_module):
    # SA-2042: an agent.control (Stop) message must reach the consumer with its
    # top-level `type` intact (NOT wrapped in {parameters:{event:...}}), so the
    # inline control path can detect it and cancel the in-flight turn.
    captured = {}

    class Delivery:
        def __init__(self):
            self.payload = {"type": "agent.control", "action": "cancel", "conv_id": "c1"}

    class Consumer:
        async def start(self, handler):
            await handler(Delivery())
            return "done"

    consumer = Consumer()

    async def handler(delivery):
        captured["payload"] = delivery.payload

    bootstrap_module._wrap_consumer_start(consumer)
    await consumer.start(handler)

    # unwrapped: top-level type preserved, no parameters/event envelope
    assert captured["payload"] == {
        "type": "agent.control",
        "action": "cancel",
        "conv_id": "c1",
    }
    assert "parameters" not in captured["payload"]


@pytest.mark.asyncio
async def test_wrap_consumer_start_passes_agent_response_through_unwrapped(bootstrap_module):
    # SA-2042 sibling: agent.response must reach the consumer intake UNWRAPPED so
    # it hits the resume path (resume_agent). Wrapping hides the top-level `type`
    # and the message would be misrouted into the graph ->
    # "'agent.response' is not a valid QueueRequestType".
    captured = {}

    class Delivery:
        def __init__(self):
            self.payload = {"type": "agent.response", "thread_id": "t1", "conv_id": "c1"}

    class Consumer:
        async def start(self, handler):
            await handler(Delivery())
            return "done"

    consumer = Consumer()

    async def handler(delivery):
        captured["payload"] = delivery.payload

    bootstrap_module._wrap_consumer_start(consumer)
    await consumer.start(handler)

    assert captured["payload"].get("type") == "agent.response"
    assert "parameters" not in captured["payload"]


def test_bootstrap_module_is_importable(bootstrap_module):
    assert bootstrap_module.__name__ == "bootstrap"
    assert hasattr(bootstrap_module, "build_app_container")