class _Settings:
    DEFAULT_QUEUE_NAME = "workflow.default.queue"


def test_dependency_resolver_exposes_mapping_values_as_attributes():
    from agent_sdk.layer2_application.utils.dependency_resolver import (
        ensure_dependency_resolver,
    )

    deps = ensure_dependency_resolver({"settings": _Settings(), "sample": 1})

    assert deps.settings.DEFAULT_QUEUE_NAME == "workflow.default.queue"
    assert deps.sample == 1
    assert deps.get("sample") == 1
    assert deps.get("missing", "fallback") == "fallback"


def test_ensure_dependency_resolver_returns_existing_resolver():
    from agent_sdk.layer2_application.utils.dependency_resolver import (
        DependencyResolver,
        ensure_dependency_resolver,
    )

    resolver = DependencyResolver({"sample": 1})

    assert ensure_dependency_resolver(resolver) is resolver


def test_dependency_resolver_exports_are_available_from_agent_sdk():
    import agent_sdk
    from agent_sdk import DependencyResolver, ensure_dependency_resolver

    assert DependencyResolver is not None
    assert callable(ensure_dependency_resolver)
    assert "DependencyResolver" in agent_sdk.__all__
    assert "ensure_dependency_resolver" in agent_sdk.__all__
