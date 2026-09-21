from app.layer1_domain.entities.state import MainState
from app.layer2_application.features.troubleshoot.use_cases import get_usecase
from app.layer2_application.features.troubleshoot.use_cases.report_usecase import (
    ReportInputDto,
)


async def report_node(state: MainState) -> dict:
    usecase = get_usecase("report")
    out = await usecase.execute(ReportInputDto(report=state["diagnosis"]))
    # `formatted_response` is the SDK output channel: normalize_final_state reads it
    # (priority 1) to build the /execute response — message=content, agent_data=the report.
    return {
        "report": out.report,
        "formatted_response": {
            "content": out.summary,
            "status": "success",
            "agent_data": out.report,
        },
    }
