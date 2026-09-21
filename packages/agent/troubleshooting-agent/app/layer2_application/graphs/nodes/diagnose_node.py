from app.layer1_domain.entities.state import MainState
from app.layer2_application.features.troubleshoot.use_cases import get_usecase
from app.layer2_application.features.troubleshoot.use_cases.diagnose_usecase import (
    DiagnoseInputDto,
)


async def diagnose_node(state: MainState) -> dict:
    usecase = get_usecase("diagnose")
    out = await usecase.execute(
        DiagnoseInputDto(
            run_id=state.get("run_id", ""),
            execution_log=state.get("execution_log", {}),
        )
    )
    return {"diagnosis": out.report}
