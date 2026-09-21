# """Unit tests for current orchestrator use cases."""

# from dataclasses import asdict
# from datetime import datetime, timezone
# from unittest.mock import AsyncMock, MagicMock

# import pytest
# from app.layer2_application.interfaces.conversation_summary_repository import ConversationTitleSource, ConversationTitleSummary
# from app.layer1_domain.entities.orchestrator.agent_event import AgentEvent
# from app.layer1_domain.entities.orchestrator.ui_event import UIEvent, UIEventRole, UIEventType, UIPayloadType
# from app.layer2_application.interfaces.agent_event_repository import ReopenConversationRow

# from tests.conftest import (
#     DummyConversationContextRepo,
#     DummyLogger,
#     DummyMonitor,
#     DummyPermissionProvider,
#     build_shared_conversation,
# )


# class TestGetConversationUseCase:
#     def setup_method(self):
#         from app.layer2_application.features.conversation.use_cases.get_conversation_use_case import (
#             GetConversationInput,
#             GetConversationUseCase,
#         )

#         self.agent_event_repo = MagicMock()
#         self.uc = GetConversationUseCase(
#             logger=DummyLogger(),
#             monitor=DummyMonitor(),
#             agent_event_repo=self.agent_event_repo,
#         )
#         self.Input = GetConversationInput

#     def test_returns_event_stream(self):
#         self.agent_event_repo.get_events_by_conversation_id.return_value = [
#             AgentEvent(
#                 event_id="evt-1",
#                 event_type="main:user:chat",
#                 correlation_id="corr-1",
#                 conv_id="c1",
#                 payload={"id": "payload-1", "content": "hi"},
#             )
#         ]

#         result = self.uc.execute(self.Input(conversation_id="c1"))

#         assert result.conversation_id == "c1"
#         assert len(result.events) == 1
#         assert result.events[0].payload["content"] == "hi"

#     def test_returns_error_when_not_found(self):
#         self.agent_event_repo.get_events_by_conversation_id.return_value = []

#         result = self.uc.execute(self.Input(conversation_id="missing"))

#         assert result.error == "Conversation not found"
#         assert result.conversation_id == "missing"


# class TestListConversationsUseCase:
#     def setup_method(self):
#         from app.layer2_application.features.conversation.use_cases.list_conversations_use_case import (
#             ListConversationsInput,
#             ListConversationsUseCase,
#         )

#         self.agent_event_repo = MagicMock()
#         self.conversation_summary_repo = MagicMock()
#         self.orchestration_state_repo = MagicMock()
#         self.uc = ListConversationsUseCase(
#             logger=DummyLogger(),
#             monitor=DummyMonitor(),
#             agent_event_repo=self.agent_event_repo,
#             conversation_summary_repo=self.conversation_summary_repo,
#             orchestration_state_repo=self.orchestration_state_repo,
#         )
#         self.Input = ListConversationsInput

#     def test_lists_conversations_with_requested_paging(self):
#         first = AgentEvent(event_id="evt-1", event_type="main:user:chat", conv_id="c1", timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc))
#         second = AgentEvent(event_id="evt-2", event_type="chat:response", conv_id="c2", timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc))
#         self.agent_event_repo.list_latest_conversation_events.return_value = [first, second]
#         self.conversation_summary_repo.get_titles.return_value = {
#             "c1": ConversationTitleSummary(conv_id="c1", title="Plan invoice approval", source=ConversationTitleSource.STRATEGY),
#             "c2": ConversationTitleSummary(conv_id="c2", title="Investigate exception", source=ConversationTitleSource.STRATEGY),
#         }

#         result = self.uc.execute(self.Input(user_id="u1", tenant_id="t1", page_index=2, page_size=5))

#         self.agent_event_repo.list_latest_conversation_events.assert_called_once_with(skip=5, top=5)
#         assert [conversation.conversation_id for conversation in result.conversations] == ["c1", "c2"]
#         assert [conversation.title for conversation in result.conversations] == ["Plan invoice approval", "Investigate exception"]
#         assert [conversation.timestamp for conversation in result.conversations] == [
#             "2024-01-01T00:00:00+00:00",
#             "2024-01-02T00:00:00+00:00",
#         ]
#         assert result.page_index == 2
#         assert result.page_size == 5

#     def test_normalizes_invalid_paging_values(self):
#         self.agent_event_repo.list_latest_conversation_events.return_value = []
#         self.conversation_summary_repo.get_titles.return_value = {}

#         result = self.uc.execute(self.Input(user_id="u1", tenant_id="t1", page_index=0, page_size=0))

#         self.agent_event_repo.list_latest_conversation_events.assert_called_once_with(skip=0, top=1)
#         assert result.conversations == []
#         assert result.page_index == 1
#         assert result.page_size == 1

#     def test_falls_back_to_shared_state_user_intent_when_title_missing(self):
#         event = AgentEvent(
#             event_id="evt-1",
#             event_type="chat:response",
#             conv_id="c1",
#             timestamp=datetime(2024, 1, 3, tzinfo=timezone.utc),
#             payload={"content": "Latest response"},
#         )
#         self.agent_event_repo.list_latest_conversation_events.return_value = [event]
#         self.conversation_summary_repo.get_titles.return_value = {}
#         self.orchestration_state_repo.get_conversation_by.return_value = build_shared_conversation(
#             conversation_id="c1",
#             user_id="u1",
#             conversation_context="Fallback title from shared state",
#             run_id="run-1",
#             workflow_id="wf-1",
#             current_agent="planner",
#         )

#         result = self.uc.execute(self.Input(user_id="u1", tenant_id="t1", page_index=1, page_size=10))

#         assert result.conversations[0].title == "Fallback title from shared state"

#     def test_falls_back_to_latest_event_content_when_no_other_title_exists(self):
#         event = AgentEvent(
#             event_id="evt-1",
#             event_type="chat:response",
#             conv_id="c1",
#             timestamp=datetime(2024, 1, 3, tzinfo=timezone.utc),
#             payload={"content": "Latest response content"},
#         )
#         self.agent_event_repo.list_latest_conversation_events.return_value = [event]
#         self.conversation_summary_repo.get_titles.return_value = {}
#         self.orchestration_state_repo.get_conversation_by.return_value = None

#         result = self.uc.execute(self.Input(user_id="u1", tenant_id="t1", page_index=1, page_size=10))

#         assert result.conversations[0].title == "Latest response content"


# class TestGetConversationMessagesUseCase:
#     def setup_method(self):
#         from app.layer2_application.features.conversation.use_cases.get_conversation_messages_use_case import (
#             GetConversationMessagesInput,
#             GetConversationMessagesUseCase,
#         )

#         self.agent_event_repo = MagicMock()
#         self.uuid_builder = MagicMock()
#         self.uuid_builder.create_uuid7.side_effect = [f"uuid-{i}" for i in range(1, 11)]
#         self.uc = GetConversationMessagesUseCase(
#             logger=DummyLogger(),
#             monitor=DummyMonitor(),
#             agent_event_repo=self.agent_event_repo,
#             uuid_builder=self.uuid_builder,
#         )
#         self.Input = GetConversationMessagesInput

#     def test_maps_user_and_assistant_reopen_messages(self):
#         self.agent_event_repo.get_reopen_conversation_rows.return_value = [
#             ReopenConversationRow(
#                 conv_id="c1",
#                 payload_id="payload-1",
#                 event_id="",
#                 event_type=UIEventType.MAIN_USER_CHAT,
#                 correlation_id="corr-1",
#                 role=UIEventRole.USER,
#                 payload={"id": "payload-1", "content": "hello"},
#                 node_created_at="2024-01-01T00:00:00+00:00",
#             ),
#             ReopenConversationRow(
#                 conv_id="c1",
#                 payload_id="payload-2",
#                 role=UIEventRole.ASSISTANT,
#                 event_id="evt-2",
#                 event_type=UIEventType.PROCESS_RESPONSE,
#                 correlation_id="corr-2",
#                 timestamp="2024-01-01T00:00:01+00:00",
#                 metadata={"main_conv_id": "c1"},
#                 payload={"id": "payload-2", "content": "working"},
#                 node_created_at="2024-01-01T00:00:01+00:00",
#             ),
#         ]

#         result = self.uc.execute(self.Input(conversation_id="c1"))

#         assert result.total == 3
#         assert result.messages[0].event_type == UIEventType.CHAT_DISABLE
#         assert result.messages[0].id == "uuid-3"
#         assert result.messages[1].role == UIEventRole.USER
#         assert result.messages[1].content == "hello"
#         assert result.messages[1].id == "uuid-1"
#         assert result.messages[1].created_at == "2024-01-01T00:00:00+00:00"
#         assert result.messages[1].timestamp == ""
#         assert result.messages[1].event_id == ""
#         assert result.messages[2].role == UIEventRole.ASSISTANT
#         assert result.messages[2].id == "uuid-2"
#         assert result.messages[2].event_id == "evt-2"
#         assert result.messages[2].created_at == "2024-01-01T00:00:01+00:00"
#         assert result.messages[2].timestamp == "2024-01-01T00:00:01+00:00"
#         assert result.messages[2].payload["content"] == "working"
#         assert self.uuid_builder.create_uuid7.call_count == 3

#     def test_falls_back_to_conversation_events_when_timeline_rows_missing(self):
#         self.agent_event_repo.get_reopen_conversation_rows.return_value = []
#         self.agent_event_repo.get_events_by_conversation_id.return_value = [
#             AgentEvent(
#                 event_id="evt-1",
#                 role=UIEventRole.USER,
#                 event_type=UIEventType.MAIN_USER_CHAT.value,
#                 correlation_id="corr-1",
#                 conv_id="c1",
#                 payload={"id": "payload-1", "content": "hello"},
#             )
#         ]

#         result = self.uc.execute(self.Input(conversation_id="c1"))

#         assert result.total == 2
#         assert result.messages[0].event_type == UIEventType.CHAT_DISABLE
#         assert result.messages[0].id == "uuid-2"
#         assert result.messages[1].id == "uuid-1"
#         assert result.messages[1].role == UIEventRole.USER
#         assert result.messages[1].content == "hello"
#         assert isinstance(result.messages[1].created_at, str)
#         assert isinstance(result.messages[1].timestamp, str)
#         self.agent_event_repo.get_events_by_conversation_id.assert_called_once_with("c1")

#     def test_appends_enable_when_chat_response_exists_in_event_fallback(self):
#         self.agent_event_repo.get_reopen_conversation_rows.return_value = []
#         self.agent_event_repo.get_events_by_conversation_id.return_value = [
#             AgentEvent(
#                 event_id="evt-2",
#                 role=UIEventRole.ASSISTANT,
#                 event_type=UIEventType.CHAT_RESPONSE.value,
#                 correlation_id="corr-2",
#                 conv_id="c1",
#                 payload={"id": "payload-2", "content": "done"},
#             )
#         ]

#         result = self.uc.execute(self.Input(conversation_id="c1"))

#         assert result.total == 3
#         assert result.messages[0].event_type == UIEventType.CHAT_DISABLE
#         assert result.messages[0].id == "uuid-2"
#         assert result.messages[1].event_type == UIEventType.CHAT_RESPONSE.value
#         assert result.messages[1].id == "uuid-1"
#         assert result.messages[2].event_type == UIEventType.CHAT_ENABLE
#         assert result.messages[2].id == "uuid-3"

#     def test_appends_enable_when_chat_response_exists_in_timeline_rows(self):
#         self.agent_event_repo.get_reopen_conversation_rows.return_value = [
#             ReopenConversationRow(
#                 conv_id="c1",
#                 payload_id="payload-1",
#                 event_id="evt-1",
#                 event_type=UIEventType.CHAT_RESPONSE,
#                 correlation_id="corr-1",
#                 role=UIEventRole.ASSISTANT,
#                 payload={"id": "payload-1", "content": "done"},
#                 node_created_at="2024-01-01T00:00:00+00:00",
#                 timestamp="2024-01-01T00:00:00+00:00",
#             ),
#         ]

#         result = self.uc.execute(self.Input(conversation_id="c1"))

#         assert result.total == 3
#         assert result.messages[0].event_type == UIEventType.CHAT_DISABLE
#         assert result.messages[1].event_type == UIEventType.CHAT_RESPONSE
#         assert result.messages[2].event_type == UIEventType.CHAT_ENABLE

#     def test_does_not_append_enable_when_chat_response_is_not_last_message(self):
#         self.agent_event_repo.get_reopen_conversation_rows.return_value = [
#             ReopenConversationRow(
#                 conv_id="c1",
#                 payload_id="payload-1",
#                 event_id="evt-1",
#                 event_type=UIEventType.CHAT_RESPONSE,
#                 correlation_id="corr-1",
#                 role=UIEventRole.ASSISTANT,
#                 payload={"id": "payload-1", "content": "done"},
#                 node_created_at="2024-01-01T00:00:00+00:00",
#                 timestamp="2024-01-01T00:00:00+00:00",
#             ),
#             ReopenConversationRow(
#                 conv_id="c1",
#                 payload_id="payload-2",
#                 event_id="evt-2",
#                 event_type=UIEventType.PROCESS_RESPONSE,
#                 correlation_id="corr-2",
#                 role=UIEventRole.ASSISTANT,
#                 payload={"id": "payload-2", "content": "working"},
#                 node_created_at="2024-01-01T00:00:01+00:00",
#                 timestamp="2024-01-01T00:00:01+00:00",
#             ),
#         ]

#         result = self.uc.execute(self.Input(conversation_id="c1"))

#         assert result.total == 3
#         assert result.messages[0].event_type == UIEventType.CHAT_DISABLE
#         assert result.messages[1].event_type == UIEventType.CHAT_RESPONSE
#         assert result.messages[2].event_type == UIEventType.PROCESS_RESPONSE


# class TestGetPermissionsUseCase:
#     def setup_method(self):
#         from app.layer2_application.features.conversation.use_cases.get_permissions_use_case import (
#             GetPermissionsInput,
#             GetPermissionsUseCase,
#         )

#         self.uc = GetPermissionsUseCase(
#             logger=DummyLogger(),
#             monitor=DummyMonitor(),
#             permission_provider=DummyPermissionProvider(),
#         )
#         self.Input = GetPermissionsInput

#     def test_returns_permissions(self):
#         result = self.uc.execute(self.Input(user_id="u1"))

#         assert result.user_id == "u1"
#         assert isinstance(result.roles, list)
#         assert isinstance(result.allowed_agents, list)
#         assert result.subscription_tier != ""

#     def test_output_is_serializable(self):
#         result = self.uc.execute(self.Input(user_id="u1"))

#         payload = asdict(result)

#         assert "roles" in payload
#         assert "resource_quotas" in payload


# class TestOrchestrateUseCaseDataclasses:
#     def test_input_fields(self):
#         from app.layer2_application.features.orchestrate.use_cases.orchestrate_use_case import OrchestrateInput

#         request = OrchestrateInput(
#             message="test",
#             conv_id="c1",
#             user_id="u1",
#             tenant_id="t1",
#             source="ws",
#         )

#         assert request.message == "test"
#         assert request.tenant_id == "t1"
#         assert request.source == "ws"

#     def test_output_is_serializable(self):
#         from app.layer2_application.features.orchestrate.use_cases.orchestrate_use_case import OrchestrateAsyncOutput

#         payload = asdict(
#             OrchestrateAsyncOutput(
#                 message="hello",
#                 conv_id="c1",
#                 uploaded_file_ids=["file-1"],
#             )
#         )

#         assert payload == {
#             "message": "hello",
#             "conv_id": "c1",
#             "uploaded_file_ids": ["file-1"],
#         }


# class TestAgentActionUseCase:
#     @pytest.mark.asyncio
#     async def test_publishs_without_context_and_returns_workflow_identifiers(self):
#         from app.layer2_application.features.orchestrate.use_cases.agent_action_use_case import (
#             AgentActionInput,
#             AgentActionUseCase,
#             WfInfo,
#         )

#         publisher = MagicMock()
#         agent_event_repo = MagicMock()
#         uuid_builder = MagicMock()
#         uuid_builder.create_uuid7.return_value = "generated-message-id"
#         workflow_client = AsyncMock()
#         uuid_builder.get_data.return_value = ""
#         uuid_builder.patch_data.return_value = ""
#         uuid_builder.submit.return_value = ""
#         uuid_builder.submit_user_action.return_value = ""

#         use_case = AgentActionUseCase(
#             logger=DummyLogger(),
#             publisher=publisher,
#             uuid_builder=uuid_builder,
#             workflow_client=workflow_client,
#             agent_event_repo=agent_event_repo,
#         )

#         result = await use_case.execute_fire_and_forget(
#             AgentActionInput(
#                 wf_info=WfInfo(run_id="run-1", node_id="node-2", task_id="task-3"),
#                 action="save",
#             )
#         )

#         assert result.run_id == "run-1"
#         assert result.node_id == "node-2"
#         assert result.task_id == "task-3"
#         agent_event_repo.save_event.assert_not_called()

#     @pytest.mark.asyncio
#     async def test_emits_success_event_when_action_context_is_present(self):
#         from app.layer2_application.features.orchestrate.use_cases.agent_action_use_case import (
#             AgentActionInput,
#             AgentActionUseCase,
#             WfInfo,
#         )

#         publisher = MagicMock()
#         agent_event_repo = MagicMock()
#         uuid_builder = MagicMock()
#         uuid_builder.create_uuid7.return_value = "generated-message-id"
#         workflow_client = AsyncMock()
#         workflow_client.submit = AsyncMock(return_value=None)
#         agent_event_repo.get_event_by_id.return_value = AgentEvent(
#             event_id="evt-1",
#             role=UIEventRole.ASSISTANT,
#             event_type="process:response",
#             correlation_id="corr-1",
#             conv_id="conv-1",
#             metadata={"main_conv_id": "conv-1"},
#             payload={
#                 "id": "payload-1",
#                 "parent_id": "parent-1",
#                 "content": "Please fill the form",
#                 "sub_conv_id": "sub-1",
#                 "type": "button_group",
#                 "text": ["Approve", "Reject"],
#                 "wf_info": {
#                     "run_id": "run-1",
#                     "node_id": "node-2",
#                     "task_id": "task-3",
#                 },
#             },
#         )

#         use_case = AgentActionUseCase(
#             logger=DummyLogger(),
#             publisher=publisher,
#             uuid_builder=uuid_builder,
#             workflow_client=workflow_client,
#             agent_event_repo=agent_event_repo,
#         )

#         await use_case.execute_fire_and_forget(
#             AgentActionInput(
#                 wf_info=WfInfo(run_id="run-1", node_id="node-2", task_id="task-3"),
#                 action="save",
#                 button_label="Approve",
#                 metadata={
#                     "main_conv_id": "conv-1",
#                     "action_context": {
#                         "source_event_id": "evt-1",
#                         "source_conv_id": "conv-1",
#                     },
#                 },
#             )
#         )

#         agent_event_repo.save_event.assert_called_once()
#         agent_event_repo.upsert_timeline_event.assert_called_once()
#         saved_event = agent_event_repo.save_event.call_args.args[0]
#         assert saved_event.payload is not None
#         assert saved_event.payload.status == "success"
#         assert saved_event.payload.id == "payload-1"
#         assert saved_event.payload.type == UIPayloadType.BUTTON_GROUP
#         assert saved_event.payload.text == ["Approve"]
#         assert saved_event.role == UIEventRole.ASSISTANT
#         agent_event_repo.get_event_by_id.assert_called_once_with("evt-1")

#     @pytest.mark.asyncio
#     async def test_emits_error_event_when_workflow_submit_fails(self):
#         from app.layer2_application.features.orchestrate.use_cases.agent_action_use_case import (
#             AgentActionInput,
#             AgentActionUseCase,
#             WfInfo,
#         )

#         publisher = MagicMock()
#         agent_event_repo = MagicMock()
#         uuid_builder = MagicMock()
#         uuid_builder.create_uuid7.return_value = "generated-message-id"
#         workflow_client = AsyncMock()
#         workflow_client.submit = AsyncMock(side_effect=RuntimeError("boom"))
#         agent_event_repo.get_event_by_id.return_value = AgentEvent(
#             event_id="evt-1",
#             role=UIEventRole.ASSISTANT,
#             event_type="process:response",
#             correlation_id="corr-1",
#             conv_id="conv-1",
#             metadata={"main_conv_id": "conv-1"},
#             payload={
#                 "id": "payload-1",
#                 "parent_id": "parent-1",
#                 "content": "Please fill the form",
#                 "sub_conv_id": "sub-1",
#                 "type": "button_group",
#                 "text": ["Approve", "Reject"],
#                 "wf_info": {
#                     "run_id": "run-1",
#                     "node_id": "node-2",
#                     "task_id": "task-3",
#                 },
#             },
#         )

#         use_case = AgentActionUseCase(
#             logger=DummyLogger(),
#             publisher=publisher,
#             uuid_builder=uuid_builder,
#             workflow_client=workflow_client,
#             agent_event_repo=agent_event_repo,
#         )

#         with pytest.raises(RuntimeError, match="boom"):
#             await use_case.execute_fire_and_forget(
#             AgentActionInput(
#                 wf_info=WfInfo(run_id="run-1", node_id="node-2", task_id="task-3"),
#                 action="save",
#                 button_label="Approve",
#                 metadata={
#                     "main_conv_id": "conv-1",
#                     "action_context": {
#                             "source_event_id": "evt-1",
#                             "source_conv_id": "conv-1",
#                         },
#                     },
#                 )
#         )

#         saved_event = agent_event_repo.save_event.call_args.args[0]
#         assert saved_event.payload is not None
#         assert saved_event.payload.status == "error"
#         assert saved_event.payload.type == UIPayloadType.BUTTON_GROUP
#         assert saved_event.payload.text == ["Approve"]

#     @pytest.mark.asyncio
#     async def test_preserves_existing_text_when_payload_type_is_not_button_group(self):
#         from app.layer2_application.features.orchestrate.use_cases.agent_action_use_case import (
#             AgentActionInput,
#             AgentActionUseCase,
#             WfInfo,
#         )

#         publisher = MagicMock()
#         agent_event_repo = MagicMock()
#         uuid_builder = MagicMock()
#         uuid_builder.create_uuid7.return_value = "generated-message-id"
#         workflow_client = AsyncMock()
#         workflow_client.submit = AsyncMock(return_value=None)
#         agent_event_repo.get_event_by_id.return_value = AgentEvent(
#             event_id="evt-1",
#             role=UIEventRole.ASSISTANT,
#             event_type="process:response",
#             correlation_id="corr-1",
#             conv_id="conv-1",
#             metadata={"main_conv_id": "conv-1"},
#             payload={
#                 "id": "payload-1",
#                 "content": "Please fill the form",
#                 "type": "form_tool",
#                 "text": ["Original label"],
#                 "wf_info": {
#                     "run_id": "run-1",
#                     "node_id": "node-2",
#                     "task_id": "task-3",
#                 },
#             },
#         )

#         use_case = AgentActionUseCase(
#             logger=DummyLogger(),
#             publisher=publisher,
#             uuid_builder=uuid_builder,
#             workflow_client=workflow_client,
#             agent_event_repo=agent_event_repo,
#         )

#         await use_case.execute_fire_and_forget(
#             AgentActionInput(
#                 wf_info=WfInfo(run_id="run-1", node_id="node-2", task_id="task-3"),
#                 action="save",
#                 button_label="Approve",
#                 metadata={
#                     "main_conv_id": "conv-1",
#                     "action_context": {
#                         "source_event_id": "evt-1",
#                         "source_conv_id": "conv-1",
#                     },
#                 },
#             )
#         )

#         saved_event = agent_event_repo.save_event.call_args.args[0]
#         assert saved_event.payload is not None
#         assert saved_event.payload.type == UIPayloadType.FORM_TOOL
#         assert saved_event.payload.text == ["Original label"]

#     @pytest.mark.asyncio
#     async def test_raises_when_source_event_is_missing(self):
#         from app.layer2_application.features.orchestrate.use_cases.agent_action_use_case import (
#             AgentActionInput,
#             AgentActionUseCase,
#             WfInfo,
#         )

#         use_case = AgentActionUseCase(
#             logger=DummyLogger(),
#             publisher=MagicMock(),
#             uuid_builder=MagicMock(),
#             workflow_client=AsyncMock(),
#             agent_event_repo=MagicMock(get_event_by_id=MagicMock(return_value=None)),
#         )

#         with pytest.raises(ValueError, match="source event not found"):
#             await use_case.execute_fire_and_forget(
#                 AgentActionInput(
#                     wf_info=WfInfo(run_id="run-1", node_id="node-2", task_id="task-3"),
#                     metadata={
#                         "action_context": {
#                             "source_event_id": "missing",
#                             "source_conv_id": "conv-1",
#                         }
#                     },
#                 )
#             )
