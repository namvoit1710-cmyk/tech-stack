import json

import pytest


def test_graph_builder_injects_resolver_objects_by_parameter_name():
    from agent_sdk.layer2_application.utils.dependency_resolver import (
        DependencyResolver,
    )
    from agent_sdk.layer2_application.utils.state_resolver import StateResolver
    from agent_sdk.layer4_frameworks.graph.agent_graph_builder import AgentGraphBuilder

    captured = {}

    def resolver_node(state, state_resolver, dependency_resolver):
        captured["state"] = state
        captured["state_resolver"] = state_resolver
        captured["dependency_resolver"] = dependency_resolver
        return {"ok": True}

    builder = AgentGraphBuilder(deps={"service": "available"})
    builder.add_node("resolver_node", resolver_node)
    builder.set_entry_point("resolver_node")

    result = builder.compile().invoke(
        {
            "message": "hello",
            "metadata": {"main_conv_id": "main-1"},
        }
    )

    assert result == {"ok": True}
    assert captured["state"] == {
        "message": "hello",
        "metadata": {"main_conv_id": "main-1"},
    }
    assert isinstance(captured["state_resolver"], StateResolver)
    assert captured["state_resolver"].metadata.main_conv_id == "main-1"
    assert isinstance(captured["dependency_resolver"], DependencyResolver)
    assert captured["dependency_resolver"].service == "available"


@pytest.mark.asyncio
async def test_graph_builder_supports_mixed_raw_and_resolver_injection_for_async_nodes():
    from agent_sdk.layer4_frameworks.graph.agent_graph_builder import AgentGraphBuilder

    captured = {}

    async def resolver_node(state, deps, state_resolver, dependency_resolver):
        captured["state"] = state
        captured["deps"] = deps
        captured["state_resolver"] = state_resolver
        captured["dependency_resolver"] = dependency_resolver
        return {"transport_state": "COMPLETED"}

    builder = AgentGraphBuilder(deps={"service": "available"})
    builder.add_node("resolver_node", resolver_node)
    builder.set_entry_point("resolver_node")

    result = await builder.compile().ainvoke(
        {
            "tenant_context": {
                "tenant_id": "tenant-1",
                "user_id": "user-1",
                "conv_id": "conv-1",
            }
        }
    )

    assert result == {"transport_state": "COMPLETED"}
    assert captured["deps"] == {"service": "available"}
    assert captured["state_resolver"].tenant_context.tenant_id == "tenant-1"
    assert captured["dependency_resolver"].service == "available"


def test_graph_builder_wraps_node_failures_with_serializable_error_context():
    from agent_sdk import NodeExecutionError
    from agent_sdk.layer4_frameworks.graph.agent_graph_builder import AgentGraphBuilder

    class ExplodingNodeError(RuntimeError):
        def __init__(self):
            super().__init__("boom")
            self.error_code = "STEP_FAILED"
            self.related_step_id = "step-42"
            self.critical = True
            self.metadata = {"source": "resolver-test"}

    def exploding_node(state, state_resolver):
        raise ExplodingNodeError()

    builder = AgentGraphBuilder()
    builder.add_node("explode", exploding_node)
    builder.set_entry_point("explode")

    with pytest.raises(NodeExecutionError) as exc_info:
        builder.compile().invoke({"message": "hello"})

    error_context = exc_info.value.error_context

    assert error_context == {
        "exception_type": "ExplodingNodeError",
        "message": "boom",
        "error_code": "STEP_FAILED",
        "related_step_id": "step-42",
        "critical": True,
        "metadata": {"source": "resolver-test"},
    }
    assert json.dumps(error_context)


def test_graph_builder_reads_is_critical_from_agent_sdk_error():
    from agent_sdk.layer1_domain.exceptions import AgentSDKError, NodeExecutionError
    from agent_sdk.layer4_frameworks.graph.agent_graph_builder import AgentGraphBuilder

    def exploding_node(state, state_resolver):
        raise AgentSDKError(
            "boom",
            error_code="STEP_FAILED",
            related_step_id="step-99",
            is_critical=True,
            metadata={"source": "agent-sdk-error"},
        )

    builder = AgentGraphBuilder()
    builder.add_node("explode", exploding_node)
    builder.set_entry_point("explode")

    with pytest.raises(NodeExecutionError) as exc_info:
        builder.compile().invoke({"message": "hello"})

    assert exc_info.value.error_context["critical"] is True
