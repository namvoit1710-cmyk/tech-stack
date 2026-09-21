from agent_sdk.layer1_domain.value_objects.agent_status import AgentStatus
from agent_sdk.layer1_domain.value_objects.node_type import NodeType
from agent_sdk.layer1_domain.value_objects.transport_state import TransportState


def test_transport_state_lifecycle():
    assert TransportState.IDLE.value == "IDLE"
    assert TransportState.COMPLETED.value == "COMPLETED"
    assert TransportState.ERROR.value == "ERROR"


def test_agent_status():
    assert AgentStatus.HEALTHY.value == "HEALTHY"


def test_node_type_custom():
    assert NodeType.CUSTOM.value == "custom"
