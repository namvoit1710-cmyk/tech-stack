from app.layer2_application.features import get_usecase
from app.layer1_domain.entities.orchestrator.orchestration_state import OrchestrationState
from app.layer2_application.features.orchestrate_graph.use_cases.orchestrate_graph_use_case import (
    OrchestrateGraphUseCase,
    OrchestrationGraphCommand,
)


async def main_node(state: OrchestrationState) -> OrchestrationState:
    event_data: dict = state.get("parameters", {}).get("event", {})
    usecase: OrchestrateGraphUseCase = get_usecase("orchestrate_graph")
    command: OrchestrationGraphCommand = OrchestrationGraphCommand.from_event(event_data)
    return await usecase.execute(command)
