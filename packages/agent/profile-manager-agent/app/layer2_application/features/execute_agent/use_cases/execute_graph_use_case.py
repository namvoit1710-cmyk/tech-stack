import uuid
from dataclasses import dataclass
from typing import Any


@dataclass
class ExecuteGraphCommand:
    message: str
    user_id: str
    tenant_id: str
    source: str
    parameters: dict[str, Any]
    token: str | None
    conv_id: str = ""


class ExecuteGraphUseCase:
    """Thin graph invoker for the HTTP path (no business logic).

    The business flow lives in ``ProcessProfileQueryUseCase``; this use case only
    assembles the initial state and invokes the graph, keeping the transport
    credential out of persisted state (via the request-scoped configurable dict).
    """

    def __init__(self, graph) -> None:
        self._graph = graph

    async def execute(self, command: ExecuteGraphCommand) -> dict[str, Any]:
        conv_id = command.conv_id or str(uuid.uuid4())
        state = {
            "message": command.message,
            "conv_id": conv_id,
            "user_id": command.user_id,
            "tenant_id": command.tenant_id,
            "source": command.source,
            "transport_state": "IDLE",
            "parameters": command.parameters,
        }
        # Token is a transport credential — keep it out of persisted graph state
        # by passing it through LangGraph's request-scoped configurable dict.
        config = {"configurable": {"thread_id": conv_id, "token": command.token}}
        final_state: dict[str, Any] = await self._graph.ainvoke(state, config=config)
        return final_state.get("formatted_response", {})
