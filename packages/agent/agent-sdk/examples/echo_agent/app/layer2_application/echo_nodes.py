from datetime import datetime

from app.layer1_domain.echo_state import EchoState

from agent_sdk import InterruptType, interrupt, tool
from agent_sdk.layer2_application.interfaces.chat_completion_service import (
    IChatCompletionService,
)


@tool
def get_current_time() -> str:
    """Get the current system time."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def openai_node(state: EchoState, deps: dict) -> dict:
    openai_service: IChatCompletionService | None = deps.get("openai_service")
    if not openai_service:
        return {"agent_result": {"content": f"[echo] {state.get('message')}"}}

    response = await openai_service.get_chat_completion(
        system_prompt=(
            "You are an echo assistant. Just repeat what the user says but make it sound professional. "
            "If the user asks for the time, you can mention that you have a tool for that."
        ),
        user_prompt=state.get("message", ""),
        json_mode=False,
    )
    return {"agent_result": {"content": str(response)}}


def hitl_node(state: EchoState, deps: dict) -> dict:
    if state.get("confirmed"):
        return {"transport_state": "PROCESSING"}

    interrupt_data = state.get("formatted_response") or state.get("agent_result")

    confirmation = interrupt(
        {
            "type": InterruptType.CONFIRMATION.value,
            "message": "Do you want to proceed with this echo?",
            "data": interrupt_data,
        }
    )

    if isinstance(confirmation, str):
        confirmed = confirmation == "approved"
    else:
        confirmed = confirmation.get("confirmed", False)

    return {
        "confirmed": confirmed,
        "transport_state": "PROCESSING",
    }
