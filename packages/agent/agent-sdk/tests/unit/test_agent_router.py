from agent_sdk.layer1_domain.entities.agent_capability import AgentCapability
from agent_sdk.layer2_application.services.agent_router import AgentRouter


def test_router_generates_tool_definitions():
    caps = [
        AgentCapability(
            agent_type="workflow-agent",
            name="Workflow",
            description="Use when running workflows",
            input_schema={"type": "object"},
            output_schema={},
        )
    ]
    router = AgentRouter(caps)
    tools = router.get_tool_definitions()
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "call_workflow_agent"


def test_router_includes_negative_examples():
    caps = [
        AgentCapability(
            agent_type="test",
            name="Test",
            description="Use for tests",
            input_schema={},
            output_schema={},
            negative_examples=["Don't call for production"],
        )
    ]
    router = AgentRouter(caps)
    tools = router.get_tool_definitions()
    assert "Do NOT call this agent when" in tools[0]["function"]["description"]


def test_router_filters_disabled_agents():
    caps = [
        AgentCapability(
            agent_type="enabled",
            name="E",
            description="d",
            input_schema={},
            output_schema={},
            enabled=True,
        ),
        AgentCapability(
            agent_type="disabled",
            name="D",
            description="d",
            input_schema={},
            output_schema={},
            enabled=False,
        ),
    ]
    router = AgentRouter(caps)
    tools = router.get_tool_definitions()
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "call_enabled"


def test_router_validates_required_parameters():
    caps = [
        AgentCapability(
            agent_type="file-agent",
            name="F",
            description="d",
            input_schema={},
            output_schema={},
            required_parameters=["file_id", "node_id"],
        )
    ]
    router = AgentRouter(caps)
    errors = router.validate_call("file-agent", {"file_id": "abc"})
    assert "node_id" in str(errors)


def test_router_dynamic_update():
    caps1 = [
        AgentCapability(
            agent_type="a1",
            name="A1",
            description="d",
            input_schema={},
            output_schema={},
        )
    ]
    router = AgentRouter(caps1)
    assert len(router.get_tool_definitions()) == 1
    caps2 = caps1 + [
        AgentCapability(
            agent_type="a2",
            name="A2",
            description="d",
            input_schema={},
            output_schema={},
        )
    ]
    router.update_capabilities(caps2)
    assert len(router.get_tool_definitions()) == 2
