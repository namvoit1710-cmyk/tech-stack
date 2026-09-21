import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer1_domain.entities.queue_event import NodeInfo, QueueEvent, QueuePayload
from app.layer1_domain.entities.ui_event import (
    UIActionReference,
    UIEvent,
    UIEventRole,
    UIEventType,
    UIPayload,
    UIPayloadType,
    UIMetadata,
    UIWFInfo,
)
from app.layer1_domain.entities.turn_meter import TurnMeter, reset_turn, start_turn
from app.layer4_frameworks.messaging import push_gateway_notifier as notifier_module
from app.layer4_frameworks.messaging.push_gateway_notifier import PushGatewayNotifier


Task = notifier_module.Task
TaskStatus = notifier_module.TaskStatus


class RecordingLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, message: str, **kwargs) -> None:
        self.messages.append(message)

    def error(self, message: str, **kwargs) -> None:
        self.messages.append(message)

    def warning(self, message: str, **kwargs) -> None:
        self.messages.append(message)

    def debug(self, message: str, **kwargs) -> None:
        self.messages.append(message)


class StubUuidBuilder:
    def __init__(self) -> None:
        self.values = [
            "uuid-1",
            "uuid-2",
            "uuid-3",
            "uuid-4",
            "uuid-5",
            "uuid-6",
            "uuid-7",
            "uuid-8",
            "uuid-9",
            "uuid-10",
        ]
        self.index = 0

    def create_uuid7(self) -> str:
        value = self.values[self.index]
        self.index += 1
        return value


def _task(**updates) -> Task:
    payload = {
        "task_id": "task-1",
        "agent": "agent.agent-1",
        "name": "Plan task",
        "status": TaskStatus.PROCESSING,
        "parent_task_id": "parent-1",
    }
    payload.update(updates)
    return Task(**payload)


def _queue_event(**updates) -> QueueEvent:
    payload = {
        "message_id": "message-1",
        "type": "workflow:node:status",
        "conv_id": "conv-1",
        "correlation_id": "corr-1",
        "metadata": {"step_id": "step-1", "parent_id": "parent-1"},
        "payload": QueuePayload(
            from_="business-agent",
            to="executor.runtime",
            steps=[],
            node_info=NodeInfo(
                run_id="run-1",
                node_id="node-1",
                node_name="Node 1",
                task_id="task-1",
            ),
        ),
    }
    payload.update(updates)
    return QueueEvent(**payload)


def _ui_event() -> UIEvent:
    return UIEvent(
        event_id="event-1",
        event_type=UIEventType.CHAT_RESPONSE,
        correlation_id="corr-1",
        conv_id="conv-1",
        metadata=UIMetadata(main_conv_id="conv-1"),
        payload=UIPayload(id="payload-1", content="hello"),
    )


def _ui_payload(
    *,
    payload_type: UIPayloadType = UIPayloadType.TEXT,
    content: str = "summary",
    wf_info: UIWFInfo | None = None,
    text: list[str] | None = None,
) -> UIPayload:
    return UIPayload(
        id="step-1",
        parent_id="parent-1",
        type=payload_type,
        content=content,
        wf_info=wf_info,
        text=text,
    )


def _extract_payload(await_args) -> dict:
    return json.loads(await_args.args[1])


def _compat_ui_event(*args, **kwargs) -> UIEvent:
    kwargs.setdefault("event_id", "event-1")
    kwargs.setdefault("correlation_id", "corr-1")
    kwargs.setdefault("role", UIEventRole.ASSISTANT)
    return UIEvent(*args, **kwargs)


@pytest.fixture(autouse=True)
def _patch_ui_event(monkeypatch):
    monkeypatch.setattr(notifier_module, "UIEvent", _compat_ui_event)


@pytest.mark.asyncio
async def test_send_notification_persists_and_projects_ui_events():
    inner = SimpleNamespace(send_notification=AsyncMock(), close=AsyncMock())
    logger = RecordingLogger()
    uuid_builder = StubUuidBuilder()
    event_repo = SimpleNamespace(save_event=MagicMock())
    timeline_repo = SimpleNamespace(upsert_from_event=MagicMock())
    subject = PushGatewayNotifier(inner, logger, uuid_builder, event_repo, timeline_repo)

    await subject.send_notification("conv-1", _ui_event())

    event_repo.save_event.assert_called_once()
    timeline_repo.upsert_from_event.assert_called_once()
    inner.send_notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_tool_form_event_includes_action_context():
    inner = SimpleNamespace(send_notification=AsyncMock(), close=AsyncMock())
    logger = RecordingLogger()
    uuid_builder = StubUuidBuilder()
    event_repo = SimpleNamespace(save_event=MagicMock())
    timeline_repo = SimpleNamespace(upsert_from_event=MagicMock())
    subject = PushGatewayNotifier(inner, logger, uuid_builder, event_repo, timeline_repo)

    await subject.send_tool_form_event(
        "conv-1",
        _ui_payload(
            payload_type=UIPayloadType.FORM_TOOL,
            content="Please fill the form",
            wf_info=UIWFInfo(node_id="node-1", task_id="task-1", run_id="run-1"),
        ),
    )

    saved_event = event_repo.save_event.call_args.args[0]
    assert saved_event.metadata is not None
    assert saved_event.metadata.action_context is not None
    assert isinstance(saved_event.metadata.action_context, UIActionReference)
    assert saved_event.metadata.action_context.source_event_id == saved_event.event_id
    assert saved_event.metadata.action_context.source_conv_id == "conv-1"

    serialized_payload = inner.send_notification.await_args.args[1]
    assert serialized_payload["metadata"]["action_context"] == {
        "source_event_id": saved_event.event_id,
        "source_conv_id": "conv-1",
    }


@pytest.mark.asyncio
async def test_send_thinking_event_is_saved_but_not_projected():
    inner = SimpleNamespace(send_notification=AsyncMock(), close=AsyncMock())
    logger = RecordingLogger()
    uuid_builder = StubUuidBuilder()
    event_repo = SimpleNamespace(save_event=MagicMock())
    timeline_repo = SimpleNamespace(upsert_from_event=MagicMock(side_effect=lambda event: None))
    subject = PushGatewayNotifier(inner, logger, uuid_builder, event_repo, timeline_repo)

    await subject.send_thinking_event("conv-1", "thinking")

    event_repo.save_event.assert_called_once()
    timeline_repo.upsert_from_event.assert_called_once()
    inner.send_notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_task_summary_event_emits_chat_enable_after_response():
    inner = SimpleNamespace(send_notification=AsyncMock(), close=AsyncMock())
    logger = RecordingLogger()
    uuid_builder = StubUuidBuilder()
    event_repo = SimpleNamespace(save_event=MagicMock())
    timeline_repo = SimpleNamespace(upsert_from_event=MagicMock())
    subject = PushGatewayNotifier(inner, logger, uuid_builder, event_repo, timeline_repo)

    await subject.send_task_summary_event("conv-1", "summary")

    assert event_repo.save_event.call_count == 2
    response_event = event_repo.save_event.call_args_list[0].args[0]
    enable_event = event_repo.save_event.call_args_list[1].args[0]
    assert response_event.event_type == UIEventType.CHAT_RESPONSE
    assert enable_event.event_type == UIEventType.CHAT_ENABLE
    assert inner.send_notification.await_count == 2


@pytest.mark.asyncio
async def test_send_task_summary_event_serializes_next_step_suggestions():
    from app.layer1_domain.entities.next_step_suggestion import (
        NextStepKind,
        NextStepSuggestion,
    )

    inner = SimpleNamespace(send_notification=AsyncMock(), close=AsyncMock())
    logger = RecordingLogger()
    uuid_builder = StubUuidBuilder()
    event_repo = SimpleNamespace(save_event=MagicMock())
    timeline_repo = SimpleNamespace(upsert_from_event=MagicMock())
    subject = PushGatewayNotifier(inner, logger, uuid_builder, event_repo, timeline_repo)

    await subject.send_task_summary_event(
        "conv-1",
        "summary",
        suggestions=[
            NextStepSuggestion(title="Review", prompt="Open the report", kind=NextStepKind.INFO),
        ],
    )

    response_event = event_repo.save_event.call_args_list[0].args[0]
    assert response_event.payload.next_step_suggestions is not None
    assert response_event.payload.next_step_suggestions[0].title == "Review"
    # Suggestion titles are appended to content for immediate FE visibility
    assert response_event.payload.content == "summary\n\nSuggested next steps:\n• Review"

    serialized_payload = inner.send_notification.await_args_list[0].args[1]
    assert serialized_payload["payload"]["content"] == "summary\n\nSuggested next steps:\n• Review"
    suggestions = serialized_payload["payload"]["next_step_suggestions"]
    assert suggestions == [
        {"title": "Review", "prompt": "Open the report", "kind": "info", "suggestion_id": ""}
    ]


@pytest.mark.asyncio
async def test_send_task_summary_event_content_unchanged_when_no_suggestions():
    inner = SimpleNamespace(send_notification=AsyncMock(), close=AsyncMock())
    logger = RecordingLogger()
    uuid_builder = StubUuidBuilder()
    event_repo = SimpleNamespace(save_event=MagicMock())
    timeline_repo = SimpleNamespace(upsert_from_event=MagicMock())
    subject = PushGatewayNotifier(inner, logger, uuid_builder, event_repo, timeline_repo)

    await subject.send_task_summary_event("conv-1", "summary")

    response_event = event_repo.save_event.call_args_list[0].args[0]
    assert response_event.payload.next_step_suggestions is None
    assert response_event.payload.content == "summary"


@pytest.mark.asyncio
async def test_send_token_info_event_processing_pushes_as_chat_thinking_without_persisting():
    inner = SimpleNamespace(send_notification=AsyncMock(), close=AsyncMock())
    logger = RecordingLogger()
    uuid_builder = StubUuidBuilder()
    event_repo = SimpleNamespace(save_event=MagicMock())
    timeline_repo = SimpleNamespace(upsert_from_event=MagicMock())
    subject = PushGatewayNotifier(inner, logger, uuid_builder, event_repo, timeline_repo)

    await subject.send_token_info_event(
        conv_id="conv-1",
        message_id="msg-1",
        content="Cooking… 1.1s · 820 tokens",
        status=TaskStatus.PROCESSING,
    )

    event_repo.save_event.assert_not_called()
    timeline_repo.upsert_from_event.assert_not_called()
    inner.send_notification.assert_awaited_once()
    pushed_event = inner.send_notification.await_args.args[1]
    assert pushed_event["event_type"] == UIEventType.CHAT_THINKING.value
    assert pushed_event["payload"]["type"] is None
    assert pushed_event["payload"]["message_id"] == "msg-1"
    assert pushed_event["payload"]["status"] == TaskStatus.PROCESSING.value


@pytest.mark.asyncio
async def test_send_token_info_event_success_persists_and_pushes_as_chat_response_with_type():
    inner = SimpleNamespace(send_notification=AsyncMock(), close=AsyncMock())
    logger = RecordingLogger()
    uuid_builder = StubUuidBuilder()
    event_repo = SimpleNamespace(save_event=MagicMock())
    timeline_repo = SimpleNamespace(upsert_from_event=MagicMock())
    subject = PushGatewayNotifier(inner, logger, uuid_builder, event_repo, timeline_repo)

    await subject.send_token_info_event(
        conv_id="conv-1",
        message_id="msg-1",
        content="✻ Cooked for 1.1s, 820 tokens",
        status=TaskStatus.SUCCESS,
    )

    saved_event = event_repo.save_event.call_args.args[0]
    assert saved_event.event_type == UIEventType.CHAT_RESPONSE
    assert saved_event.payload.type == UIPayloadType.TOKEN_INFO
    assert saved_event.payload.message_id == "msg-1"
    assert saved_event.payload.status == TaskStatus.SUCCESS
    assert saved_event.payload.content == "✻ Cooked for 1.1s, 820 tokens"
    # each token_info gets its own fresh event id — never the message_id itself
    assert saved_event.event_id != "msg-1"
    inner.send_notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_task_summary_event_with_active_turn_emits_content_then_success_token_info_last():
    inner = SimpleNamespace(send_notification=AsyncMock(), close=AsyncMock())
    logger = RecordingLogger()
    uuid_builder = StubUuidBuilder()
    event_repo = SimpleNamespace(save_event=MagicMock())
    timeline_repo = SimpleNamespace(upsert_from_event=MagicMock())
    subject = PushGatewayNotifier(inner, logger, uuid_builder, event_repo, timeline_repo)

    meter = TurnMeter(message_id="msg-1", conv_id="conv-1", start_perf=0.0, total_tokens=1682)
    token = start_turn(meter)
    try:
        await subject.send_task_summary_event("conv-1", "summary")
    finally:
        reset_turn(token)

    # 3 saves: the final content, then the success token_info, then chat_enable
    assert event_repo.save_event.call_count == 3
    content_event = event_repo.save_event.call_args_list[0].args[0]
    token_info_event = event_repo.save_event.call_args_list[1].args[0]

    # the final content message's payload.id == the turn's message_id (FE grouping key)
    assert content_event.payload.id == "msg-1"
    assert content_event.event_id == "msg-1"

    assert token_info_event.event_type == UIEventType.CHAT_RESPONSE
    assert token_info_event.payload.type == UIPayloadType.TOKEN_INFO
    assert token_info_event.payload.status == TaskStatus.SUCCESS
    assert token_info_event.payload.message_id == "msg-1"
    assert token_info_event.payload.content.startswith("✻ Cooked for ")


@pytest.mark.asyncio
async def test_send_task_summary_event_without_active_turn_skips_token_info():
    inner = SimpleNamespace(send_notification=AsyncMock(), close=AsyncMock())
    logger = RecordingLogger()
    uuid_builder = StubUuidBuilder()
    event_repo = SimpleNamespace(save_event=MagicMock())
    timeline_repo = SimpleNamespace(upsert_from_event=MagicMock())
    subject = PushGatewayNotifier(inner, logger, uuid_builder, event_repo, timeline_repo)

    await subject.send_task_summary_event("conv-1", "summary")

    # no active TurnMeter -> just the content event + chat_enable (2), no token_info
    assert event_repo.save_event.call_count == 2
    content_event = event_repo.save_event.call_args_list[0].args[0]
    assert content_event.payload.type is None
    assert content_event.payload.id != "msg-1"


@pytest.mark.asyncio
async def test_send_chat_disable_event_persists_and_sends_toggle_event():
    inner = SimpleNamespace(send_notification=AsyncMock(), close=AsyncMock())
    logger = RecordingLogger()
    uuid_builder = StubUuidBuilder()
    event_repo = SimpleNamespace(save_event=MagicMock())
    timeline_repo = SimpleNamespace(upsert_from_event=MagicMock())
    subject = PushGatewayNotifier(inner, logger, uuid_builder, event_repo, timeline_repo)

    await subject.send_chat_disable_event("conv-1")

    saved_event = event_repo.save_event.call_args.args[0]
    assert saved_event.event_type == UIEventType.CHAT_DISABLE
    assert saved_event.payload is None
    inner.send_notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_notification_marks_text_payload_success():
    inner = SimpleNamespace(send_notification=AsyncMock(), close=AsyncMock())
    logger = RecordingLogger()
    uuid_builder = StubUuidBuilder()
    event_repo = SimpleNamespace(save_event=MagicMock())
    timeline_repo = SimpleNamespace(upsert_from_event=MagicMock())
    subject = PushGatewayNotifier(inner, logger, uuid_builder, event_repo, timeline_repo)
    event = UIEvent(
        event_id="event-2",
        event_type=UIEventType.PROCESS_RESPONSE,
        correlation_id="corr-2",
        conv_id="conv-1",
        metadata=UIMetadata(main_conv_id="conv-1"),
        payload=_ui_payload(payload_type=UIPayloadType.TEXT, content="summary"),
    )

    await subject.send_notification("conv-1", event)

    saved_event = event_repo.save_event.call_args.args[0]
    assert saved_event.payload is not None
    assert saved_event.payload.status == TaskStatus.SUCCESS


# @pytest.mark.asyncio
# async def test_send_notification_serializes_event_and_marks_non_final():
#     inner = SimpleNamespace(send_notification=AsyncMock(), close=AsyncMock())
#     logger = RecordingLogger()
#     subject = PushGatewayNotifier(inner, logger)

#     await subject.send_notification("conv-1", _ui_event())

#     call = inner.send_notification.await_args
#     assert call.args[0] == "conv-1"
#     assert call.kwargs == {"is_final": False}
#     assert _extract_payload(call)["event_type"] == UIEventType.CHAT_RESPONSE
#     assert any("Sent notification to Push Gateway" in message for message in logger.messages)


# @pytest.mark.asyncio
# async def test_send_task_status_event_includes_parent_task_id():
#     inner = SimpleNamespace(send_notification=AsyncMock(), close=AsyncMock())
#     subject = PushGatewayNotifier(inner, RecordingLogger())

#     await subject.send_task_status_event("conv-1", _task())

#     payload = _extract_payload(inner.send_notification.await_args)
#     assert payload["event_type"] == UIEventType.PROCESS_RESPONSE
#     assert payload["payload"]["type"] == "progressing_collapse"
#     assert payload["payload"]["parent_id"] == "parent-1"


# @pytest.mark.asyncio
# async def test_send_task_summary_event_builds_chat_response():
#     inner = SimpleNamespace(send_notification=AsyncMock(), close=AsyncMock())
#     subject = PushGatewayNotifier(inner, RecordingLogger())

#     await subject.send_task_summary_event("conv-1", "summary")

#     payload = _extract_payload(inner.send_notification.await_args)
#     assert payload["event_type"] == UIEventType.CHAT_RESPONSE
#     assert payload["payload"]["content"] == "summary"
#     assert payload["payload"]["id"]


# @pytest.mark.asyncio
# async def test_send_process_response_with_text_event_builds_text_payload():
#     inner = SimpleNamespace(send_notification=AsyncMock(), close=AsyncMock())
#     subject = PushGatewayNotifier(inner, RecordingLogger())

#     await subject.send_process_response_with_text_event(
#         "conv-1",
#         _ui_payload(payload_type=UIPayloadType.TEXT, content="summary"),
#     )

#     payload = _extract_payload(inner.send_notification.await_args)
#     assert payload["event_type"] == UIEventType.PROCESS_RESPONSE
#     assert payload["metadata"]["main_conv_id"] == "conv-1"
#     assert payload["payload"]["type"] == "text"
#     assert payload["payload"]["content"] == "summary"
#     assert payload["payload"]["id"] == "step-1"
#     assert payload["payload"]["parent_id"] == "parent-1"


# @pytest.mark.asyncio
# async def test_send_tool_form_event_builds_tool_form_payload():
#     inner = SimpleNamespace(send_notification=AsyncMock(), close=AsyncMock())
#     subject = PushGatewayNotifier(inner, RecordingLogger())

#     await subject.send_tool_form_event(
#         "conv-1",
#         _ui_payload(
#             payload_type=UIPayloadType.FORM_TOOL,
#             content="Please fill the form",
#             wf_info=UIWFInfo(node_id="node-1", task_id="task-1", run_id="run-1"),
#         ),
#     )

#     payload = _extract_payload(inner.send_notification.await_args)
#     assert payload["event_type"] == UIEventType.PROCESS_RESPONSE
#     assert payload["metadata"]["main_conv_id"] == "conv-1"
#     assert payload["payload"]["type"] == "form_tool"
#     assert payload["payload"]["content"] == "Please fill the form"
#     assert payload["payload"]["wf_info"] == {
#         "node_id": "node-1",
#         "task_id": "task-1",
#         "run_id": "run-1",
#     }


# @pytest.mark.asyncio
# async def test_send_tool_form_event_supports_button_group_payload():
#     inner = SimpleNamespace(send_notification=AsyncMock(), close=AsyncMock())
#     subject = PushGatewayNotifier(inner, RecordingLogger())

#     await subject.send_tool_form_event(
#         "conv-1",
#         _ui_payload(
#             payload_type=UIPayloadType.BUTTON_GROUP,
#             content="Please confirm",
#             wf_info=UIWFInfo(node_id="node-1", task_id="task-1", run_id="run-1"),
#             text=["Approve", "Reject"],
#         ),
#     )

#     payload = _extract_payload(inner.send_notification.await_args)
#     assert payload["payload"]["type"] == "button_group"
#     assert payload["payload"]["text"] == ["Approve", "Reject"]


# @pytest.mark.asyncio
# async def test_send_thinking_event_builds_chat_thinking_payload():
#     inner = SimpleNamespace(send_notification=AsyncMock(), close=AsyncMock())
#     subject = PushGatewayNotifier(inner, RecordingLogger())

#     await subject.send_thinking_event("conv-1", "thinking")

#     payload = _extract_payload(inner.send_notification.await_args)
#     assert payload["event_type"] == UIEventType.CHAT_THINKING
#     assert payload["payload"]["status"] == TaskStatus.SUCCESS
#     assert payload["payload"]["content"] == "thinking"

