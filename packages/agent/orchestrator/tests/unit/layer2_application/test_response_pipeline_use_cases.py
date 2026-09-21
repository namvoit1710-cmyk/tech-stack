from types import SimpleNamespace

import pytest

from app.layer1_domain.entities.orchestrator.orchestration_state import OrchestrationState
from app.layer1_domain.entities.orchestrator.response_formatter import ResponseFormatter
from app.layer1_domain.entities.orchestrator.ui_event import UIEventType
from app.layer2_application.features.orchestrate_graph.use_cases.orchestrate_graph_use_case import OrchestrateGraphUseCase
from app.layer1_domain.entities.exceptions import ErrorMessages
# from tests.conftest import DummyLogger, DummyMonitor
from agent_sdk import settings


class _SpyNotifier:
    def __init__(self):
        self.sent = []

    async def send_notification(self, *, key, data, is_final=True):
        self.sent.append(
            {
                "key": key,
                "data": data,
                "is_final": is_final,
            }
        )


# def _build_use_case(conversation_repo, notifier):
#     uuid_builder = SimpleNamespace(create_uuid7=lambda *_args, **_kwargs: "generated-message-id")

#     return OrchestrateGraphUseCase(
#         logger=DummyLogger(),
#         monitor=DummyMonitor(),
#         permission_provider=SimpleNamespace(
#             get_user_permissions=lambda _user_id: [],
#             get_user_features=lambda *_args, **_kwargs: {},
#         ),
#         input_guard=SimpleNamespace(
#             check=lambda message, user_id="": SimpleNamespace(
#                 passed=True,
#                 sanitized_message=message,
#                 warnings=[],
#             )
#         ),
#         push_gateway_notifier=notifier,
#         agent_registry_client=SimpleNamespace(get_available_agents=None),
#         workflow_client=SimpleNamespace(),
#         llm_provider=SimpleNamespace(),
#         orchestration_state_repo=SimpleNamespace(),
#         agent_event_repo=SimpleNamespace(save_event=lambda _event: None),
#         publisher=SimpleNamespace(),
#         response_formatter=ResponseFormatter(),
#         uuid_builder=uuid_builder,
#         settings=settings,
#     )


# def _build_state(*, context=None, planning=None, result=None, workflow=None, runtime=None):
#     workflow_payload = dict(workflow or {})
#     runtime_payload = dict(runtime or {})

#     workflow_extras = {
#         key: workflow_payload.pop(key)
#         for key in list(workflow_payload.keys())
#         if key not in OrchestrationWorkflow.__dataclass_fields__
#     }
#     runtime_extras = {
#         key: runtime_payload.pop(key)
#         for key in list(runtime_payload.keys())
#         if key not in OrchestrationRuntime.__dataclass_fields__
#     }

#     state = OrchestrationState(
#         context=OrchestrationContext(**(context or {})),
#         planning=OrchestrationPlanning(**(planning or {})),
#         result=OrchestrationResult(**(result or {})),
#         permission=OrchestrationPermission(),
#         category=OrchestrationCategory(),
#         workflow=OrchestrationWorkflow(**workflow_payload),
#         runtime=OrchestrationRuntime(**runtime_payload),
#     )

#     for key, value in workflow_extras.items():
#         setattr(state.workflow, key, value)
#     for key, value in runtime_extras.items():
#         setattr(state.runtime, key, value)

#     return state


# @pytest.mark.asyncio
# async def test_format_unsupported_response_marks_not_found_and_persists_message():
#     notifier = _SpyNotifier()
#     use_case = _build_use_case(SimpleNamespace(), notifier)

#     await use_case._emit_unsupported_response("conv-1")

#     assert len(notifier.sent) == 1
#     event = notifier.sent[0]["data"]
#     assert notifier.sent[0]["key"] == "conv-1"
#     assert notifier.sent[0]["is_final"] is True
#     assert event["event_type"] == UIEventType.CHAT_RESPONSE
#     assert event["conv_id"] == "conv-1"
#     assert event["payload"]["content"] == ErrorMessages.ORCH_011.value
#     assert event["event_id"] == "generated-message-id"


# @pytest.mark.asyncio
# async def test_emit_error_response_sends_final_notification():
#     notifier = _SpyNotifier()
#     use_case = _build_use_case(SimpleNamespace(), notifier)

#     await use_case._emit_error_response("conv-1", "final")

#     assert len(notifier.sent) == 1
#     event = notifier.sent[0]["data"]
#     assert notifier.sent[0]["key"] == "conv-1"
#     assert notifier.sent[0]["is_final"] is True
#     assert event["event_type"] == UIEventType.CHAT_RESPONSE
#     assert event["conv_id"] == "conv-1"
#     assert event["payload"]["content"] == ErrorMessages.ORCH_001.value
#     assert event["event_id"] == "generated-message-id"
