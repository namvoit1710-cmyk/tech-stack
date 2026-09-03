from agent_sdk.layer1_domain.value_objects.transport_state import TransportState


def format_response_node(state: dict, deps: dict) -> dict:
    if state.get("formatted_response"):
        return {"transport_state": TransportState.COMPLETED.value}
    agent_result = state.get("agent_result", {})
    agent_data = state.get("agent_data", {})
    conv_id = state.get("conv_id", "")
    if isinstance(agent_result, dict):
        content = (
            agent_result.get("content")
            or agent_result.get("message")
            or agent_result.get("result", "")
        )
    else:
        content = str(agent_result)

    formatted_response = {
        "type": "response",
        "content": content,
        "agent_result": agent_result,
        "agent_data": agent_data,
        "conv_id": conv_id,
    }
    return {
        "formatted_response": formatted_response,
        "transport_state": TransportState.COMPLETED.value,
    }
