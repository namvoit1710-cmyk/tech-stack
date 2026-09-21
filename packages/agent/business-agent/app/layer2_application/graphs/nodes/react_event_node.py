from app.layer1_domain.entities.state import MainState
from app.layer2_application.features.main_agent.use_cases import get_usecase
from app.layer2_application.features.main_agent.dto.event import EventDTO
from app.layer1_domain.entities.queue_event import PLAN_EVENTS, REVIEW_EVENTS, EventSource
from app.layer1_domain.entities.workflow_event import WorkflowEvent
from app.layer1_domain.entities.ui_event import UIActionEvent

async def react_event_node(state: MainState) -> dict:
    event_data = state.get("parameters", {}).get("event", {})

    if event_data is None:
        raise ValueError("Event data is missing")

    source = event_data.get("source", "")

    if source == EventSource.WORKFLOW_CONTROL_PLANE:
        workflow_event = WorkflowEvent.from_dict(event_data)
        event = EventDTO.workflow_event_to_dto(workflow_event)

    # elif source == EventSource.UI:
    #     ui_event = UIActionEvent.from_dict(event_data)
    #     event = EventDTO.ui_action_event_to_dto(ui_event)
    else:
        event = EventDTO.from_dict(event_data)
    
    if event.type in PLAN_EVENTS:
        usecase = get_usecase("create_plan")

    elif event.type in REVIEW_EVENTS:
        usecase = get_usecase("review_plan")

    else:
        raise ValueError(
            f"Unsupported event type: {event.type}"
        )

    return await usecase.execute(event)
