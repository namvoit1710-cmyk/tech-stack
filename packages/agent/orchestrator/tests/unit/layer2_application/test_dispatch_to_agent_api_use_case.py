# from unittest.mock import AsyncMock, MagicMock

# import pytest

# from app.layer2_application.interfaces.conversation_summary_repository import ConversationTitleSource
# from app.layer2_application.features.orchestrate.use_cases import dispatch_to_agent_use_case as module


# class _FakeUuid:
#     def __init__(self, value: str):
#         self.value = value

#     def __str__(self) -> str:
#         return self.value


# class _FixedDatetime:
#     @staticmethod
#     def now(_tz):
#         class _FixedNow:
#             @staticmethod
#             def isoformat():
#                 return "2026-06-01T00:00:00+00:00"

#         return _FixedNow()


# # @pytest.mark.asyncio
# # async def test_dispatch_to_agent_use_case_publishes_agent_request(monkeypatch):
# #     uuid_builder = MagicMock()
# #     uuid_builder.create_uuid7.side_effect = ["corr-123", "request-123", "task-123"]
# #     monkeypatch.setattr(module, "datetime", _FixedDatetime)

# #     scheduled_coroutines = []

# #     def run_immediately(coroutine):
# #         scheduled_coroutines.append(coroutine)
# #         return coroutine

# #     monkeypatch.setattr(module.asyncio, "create_task", run_immediately)

# #     message_publisher = MagicMock()
# #     message_publisher.publish = AsyncMock(return_value=None)
# #     use_case = module.DispatchToAgentUseCase(
# #         logger=MagicMock(),
# #         monitor=MagicMock(),
# #         publisher=message_publisher,
# #         settings=MagicMock(),
# #         orchestration_state_repo=MagicMock(),
# #         uuid_builder=uuid_builder,
# #     )

# #     result = await use_case.execute(
# #         module.DispatchToAgentInput(
# #             message="process this request",
# #             conv_id="conv-123",
# #             user_id="user-1",
# #             user_email="user@example.com",
# #             tenant_id="tenant-1",
# #             parameters={
# #                 "agent_type": "workflow",
# #                 "agent_name": "Business Agent",
# #                 "agent_id": "agent-123",
# #                 "file_id": "file-1",
# #             },
# #             context={"auth": {"authorization": "Bearer token-abc"}},
# #         )
# #     )

# #     await scheduled_coroutines[0]

# #     await_args = message_publisher.publish.await_args
# #     assert await_args is not None
# #     published_topic = await_args.args[0]
# #     published_message = await_args.kwargs["message"]
# #     assert published_topic == module.AGENT_REQUEST_TOPIC
# #     assert published_message == {
# #         "agent_type": "workflow",
# #         "agent_name": "Business Agent",
# #         "timestamp": "2026-06-01T00:00:00+00:00",
# #         "message": "process this request",
# #         "parameters": {
# #             "agent_type": "workflow",
# #             "agent_name": "Business Agent",
# #             "agent_id": "agent-123",
# #             "file_id": "file-1",
# #         },
# #     }
# #     assert result.status == "queued"
# #     assert result.conv_id == "conv-123"
# #     assert result.correlation_id == "corr-123"
# #     assert result.request_id == "request-123"


# @pytest.mark.asyncio
# async def test_dispatch_to_agent_use_case_persists_initial_shared_state(monkeypatch):
#     uuid_builder = MagicMock()
#     uuid_builder.create_uuid7.side_effect = ["corr-456", "request-456", "task-456"]
#     monkeypatch.setattr(module, "datetime", _FixedDatetime)

#     message_publisher = MagicMock()
#     message_publisher.publish = AsyncMock(return_value=None)
#     orchestration_state_repo = MagicMock()
#     conversation_summary_repo = MagicMock()
#     use_case = module.DispatchToAgentUseCase(
#         logger=MagicMock(),
#         monitor=MagicMock(),
#         publisher=message_publisher,
#         settings=MagicMock(),
#         orchestration_state_repo=orchestration_state_repo,
#         uuid_builder=uuid_builder,
#         conversation_summary_repo=conversation_summary_repo,
#     )

#     result = await use_case.execute(
#         module.DispatchToAgentInput(
#             message="process this request",
#             conv_id="conv-789",
#             user_id="user-2",
#             user_email="user2@example.com",
#             tenant_id="tenant-1",
#             parameters={
#                 "agent_type": "workflow",
#                 "agent_name": "Business Agent",
#                 "agent_id": "agent-789",
#                 "workflow_id": "wf-123",
#             },
#         )
#     )

#     saved_state = orchestration_state_repo.save_conversation.call_args.args[0]
#     assert saved_state.conv_id == "conv-789"
#     assert saved_state.shared_state.metadata.correlation_id == "corr-456"
#     assert saved_state.shared_state.metadata.current_agent == "Business Agent"
#     assert saved_state.shared_state.metadata.current_task_id == "task-456"
#     assert saved_state.shared_state.context.conversation_context == "process this request"
#     assert saved_state.shared_state.executions[0].id == "task-456"
#     assert saved_state.shared_state.executions[0].goal == "Dispatch message to Business Agent"
#     assert saved_state.shared_state.executions[0].execute_by == "Business Agent"
#     assert saved_state.shared_state.executions[0].input_data["agent_name"] == "Business Agent"
#     assert saved_state.shared_state.executions[0].workflow_id == "wf-123"
#     assert saved_state.shared_state.executions[0].agent_id == "agent-789"
#     saved_title = conversation_summary_repo.save_title.call_args.args[0]
#     assert saved_title.conv_id == "conv-789"
#     assert saved_title.title == "process this request"
#     assert saved_title.source == ConversationTitleSource.USER_MESSAGE
#     assert result.conv_id == "conv-789"
#     assert result.correlation_id == "corr-456"
#     assert result.request_id == "request-456"
