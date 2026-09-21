from app.layer1_domain.entities.state import MainState
from app.layer2_application.features.main_agent.use_cases import get_usecase
from app.layer2_application.features.main_agent.use_cases.draft_business_agent_usecase import (
    DraftInputDto,
)


def _resolve_prompt(state: MainState) -> str:
    prompt = state.get("prompt")
    if prompt:
        return prompt
    prompt = state.get("parameters", {}).get("prompt")
    if prompt:
        return prompt
    # Fall back to the chat message so a plain {"message": "..."} call also works.
    return state.get("message", "")


async def draft_business_agent_node(state: MainState) -> dict:
    usecase = get_usecase("draft_business_agent")
    params = state.get("parameters", {})
    result = await usecase.execute(
        DraftInputDto(
            prompt=_resolve_prompt(state),
            available_tools=params.get("available_tools"),
            available_agents=params.get("available_agents"),
        )
    )

    draft = result.draft
    envelope = {"payload": draft.to_register_payload(), "source": result.source}

    # `formatted_response` is the SDK's surfaced-output channel (a declared state key that
    # normalize_final_state reads first); non-schema keys are dropped by LangGraph.
    return {
        "draft": draft,
        "formatted_response": {
            "content": f"Drafted business agent '{draft.name}'.",
            "status": "success",
            "agent_data": envelope,
            "data": envelope,
        },
    }
