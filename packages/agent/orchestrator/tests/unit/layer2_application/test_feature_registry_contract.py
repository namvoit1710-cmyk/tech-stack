"""Feature registry contract tests for the migration refactor."""

from app.layer2_application import features as feature_module


class _FeatureUseCase:
    def __init__(self, logger=None, workflow_client=None):
        self.logger = logger
        self.workflow_client = workflow_client


def test_load_all_usecases_instantiates_from_registry(monkeypatch):
    feature_module.FEATURE_REGISTRY.clear()
    feature_module.USECASE_CONTAINER.clear()
    feature_module.FEATURE_REGISTRY.update(
        {
            "sample.action": _FeatureUseCase,
        }
    )

    container = feature_module.load_all_usecases(
        {
            "logger": "log",
            "workflow_client": "client",
            "unused": "ignored",
        },
        reload=True,
    )

    usecase = container["sample.action"]
    assert isinstance(usecase, _FeatureUseCase)
    assert usecase.logger == "log"
    assert usecase.workflow_client == "client"


def test_get_usecase_returns_registered_instance():
    feature_module.FEATURE_REGISTRY.clear()
    feature_module.USECASE_CONTAINER.clear()
    instance = object()
    feature_module.USECASE_CONTAINER["sample.action"] = instance

    assert feature_module.get_usecase("sample.action") is instance


def test_load_features_keeps_canonical_conversation_keys():
    feature_module.FEATURE_REGISTRY.clear()
    feature_module.USECASE_CONTAINER.clear()

    registry = feature_module.load_features()

    assert "conversation.get_conversation" in registry
    assert registry["conversation.get_conversation"].__name__ == "GetConversationUseCase"
    assert "conversation.get_conversation_messages" in registry
    assert "conversation.list_conversations" in registry


def test_load_features_registers_classify_and_build_plan_key():
    feature_module.FEATURE_REGISTRY.clear()
    feature_module.USECASE_CONTAINER.clear()

    registry = feature_module.load_features()

    assert "orchestrate_graph" in registry
    assert registry["orchestrate_graph"].__name__ == "OrchestrateGraphUseCase"
    assert "orchestrate_graph.classify_and_build_plan" not in registry
