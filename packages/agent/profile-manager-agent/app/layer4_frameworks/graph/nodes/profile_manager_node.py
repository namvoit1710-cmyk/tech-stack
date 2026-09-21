from app.layer2_application.features.execute_agent.use_cases.process_profile_query_use_case import (
    ProcessProfileQueryCommand,
)
from app.layer4_frameworks.config.settings_adapter import AppSettingsAdapter


async def profile_manager_node(state: dict, deps: dict) -> dict:
    """Thin layer4 adapter: translate graph state -> use case -> graph state.

    Holds no business logic (P1/P6). It builds the command from the graph state
    and the request-scoped token, delegates to the layer2 use case, and writes
    the resulting ``formatted_response`` back into the state.
    """
    use_case = deps.get("process_profile_query_use_case")
    settings = AppSettingsAdapter()

    command = ProcessProfileQueryCommand(
        message=state.get("message", ""),
        token=deps.get("token"),
        agent_type=settings.AGENT_TYPE,
    )
    result = await use_case.execute(command)
    return {"formatted_response": result.as_dict()}
