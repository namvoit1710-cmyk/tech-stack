from typing import Any

from app.layer1_domain.entities.file_state import ConversationTurn, FileState
from app.layer2_application.use_cases.plan_usecase import PlanInputDto


def _tool_schema(tool) -> dict:
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is None:
        return {}
    model_json_schema = getattr(args_schema, "model_json_schema", None)
    if callable(model_json_schema):
        schema = model_json_schema()
        return schema if isinstance(schema, dict) else {}
    legacy_schema = getattr(args_schema, "schema", None)
    if callable(legacy_schema):
        schema = legacy_schema()
        return schema if isinstance(schema, dict) else {}
    return {}


async def plan_node(state: FileState, deps: dict) -> dict:
    usecase = deps.get("plan_usecase")
    if usecase is None:
        raise RuntimeError("plan_usecase dependency is not configured")

    message = state.get("message", "")
    parameters = state.get("parameters", {})
    file_id = parameters.get("file_id", "")
    raw_conversation_history: list[Any] = list(
        state.get("conversation_history", []) or []
    )
    conversation_history: list[ConversationTurn]
    if raw_conversation_history and isinstance(
        raw_conversation_history[0], ConversationTurn
    ):
        conversation_history = [
            turn
            for turn in raw_conversation_history
            if isinstance(turn, ConversationTurn)
        ]
    else:
        conversation_history = []
        for turn in raw_conversation_history:
            if not isinstance(turn, dict):
                continue
            role = turn.get("role")
            message_value = turn.get("message")
            file_id_value = turn.get("file_id")
            conversation_history.append(
                ConversationTurn(
                    role=str(role or "user"),
                    message=str(message_value or ""),
                    file_id=str(file_id_value) if file_id_value is not None else None,
                )
            )

    tool_catalog = state.get("tool_catalog")
    if not tool_catalog:
        tool_catalog = [
            {
                "name": getattr(tool, "name", "unknown"),
                "description": getattr(tool, "description", ""),
                "parameters": _tool_schema(tool),
            }
            for tool in deps.get("worker_tools", [])
        ]
    output_dto = await usecase.execute(
        PlanInputDto(
            message=message,
            file_id=file_id,
            conversation_history=conversation_history,
            tool_catalog=tool_catalog,
            parameters=parameters,
        )
    )
    return {
        "execution_plan": output_dto.execution_plan.to_dict(),
        "is_reviewed": output_dto.is_reviewed,
    }
