from app.layer1_domain.entities.state import MainState
from app.layer2_application.features.troubleshoot.use_cases import get_usecase
from app.layer2_application.features.troubleshoot.use_cases.collect_usecase import (
    CollectInputDto,
)


async def collect_node(state: MainState) -> dict:
    usecase = get_usecase("collect")
    params = state.get("parameters", {}) or {}
    run_id = state.get("run_id") or params.get("run_id", "")
    workflow_id = state.get("workflow_id") or params.get("workflow_id", "")
    workflow_name = state.get("workflow_name") or params.get("workflow_name", "")
    out = await usecase.execute(
        CollectInputDto(run_id=run_id, workflow_id=workflow_id, workflow_name=workflow_name)
    )
    return {"run_id": out.run_id, "execution_log": out.execution_log}
