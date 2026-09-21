from agent_sdk.layer1_domain.entities.agent_info import AgentInfo
from agent_sdk.layer1_domain.entities.agent_registration import AgentRegistration
from agent_sdk.layer1_domain.entities.agent_request import AgentRequest
from agent_sdk.layer1_domain.entities.agent_response import AgentResponse
from agent_sdk.layer1_domain.entities.agent_state import AgentBaseState


def test_agent_base_state_is_total_false():
    state: AgentBaseState = {"message": "hello"}
    assert state["message"] == "hello"
    assert "user_id" not in state


def test_agent_request_defaults():
    req = AgentRequest(message="hello")
    assert req.user_id == "anonymous"
    assert req.tenant_id == "default"


def test_agent_response_success():
    resp = AgentResponse(message="done", agent_data={"key": "value"})
    assert resp.status == "success"
    assert resp.error is None


def test_agent_response_error():
    resp = AgentResponse(
        message="failed", status="error", error="timeout", error_code="AGENT_001"
    )
    assert resp.status == "error"


def test_agent_info():
    info = AgentInfo(
        agent_type="test", version="1.0", sdk_version="1.0.0", domain="test"
    )
    assert info.capabilities == []


def test_agent_registration():
    reg = AgentRegistration(
        agent_type="test",
        version="1.0",
        sdk_version="1.0.0",
        domain="test",
        endpoint_url="http://localhost:36000",
    )
    assert reg.queue_name is None
    assert reg.capabilities == []


def test_capabilities_structure():
    reg = AgentRegistration(
        agent_type="x",
        version="1",
        sdk_version="1",
        domain="dom",
        endpoint_url="u",
        capabilities=[{"domain": "dom", "action": "act"}],
    )
    assert isinstance(reg.capabilities, list)
    assert reg.capabilities[0]["domain"] == "dom"
    assert reg.capabilities[0]["action"] == "act"


def test_agent_info_capabilities():
    info = AgentInfo(
        agent_type="test",
        version="1.0",
        sdk_version="1.0.0",
        domain="test",
        capabilities=[{"domain": "test", "action": "run"}],
    )
    assert isinstance(info.capabilities, list)
    assert info.capabilities[0]["action"] == "run"
